"""Independent table loops over scene cursors and one authoritative world save."""

from __future__ import annotations

import asyncio
from typing import Any

from ..agents.cast import TimeHandoff
from ..engine.game import PLAY_WAIT
from ..engine.scenes import SceneRuntime
from ..engine.state import time_of_day
from ..engine.timing import SceneResourceConflict
from .hub import Hub

ROUTED = {"act", "join", "done", "undo", "respond", "react", "speak", "kp_private", "retell", "suggest", "recount",
          "answer", "command"}


class SceneTable(Hub):
    """Reuse the table protocol, with only this scene's execution queues and sessions."""

    def __init__(self, tables: SceneTables, ident: str):
        root, runtime = tables.root, tables.runtime
        self.__dict__.update(root.__dict__)
        self._scene_tables = None
        self.tables, self.ident, self.root = tables, ident, root
        self.state = runtime.state_view(ident)
        self.game, self.graph = runtime.game(ident), runtime.graph(ident)
        self.cast = self.game.cast
        self.thread = {"configurable": {"thread_id": f"{root.state.slot}:{ident}"}, "recursion_limit": 100}
        self.game.listener = self.game.emit = self._event
        self.game.checkpoint = self._save
        self.game.director_wait = PLAY_WAIT
        self.game.write_progress = self.game.world_later = self.game.world.reviewing = True
        self.game.table_turn_based = True
        self.game.wait_horizon = self._wait_horizon
        self.game.plugins.away = lambda who: self.sits.get(who) is None
        members = set(self.scene.members)
        self.inbox = {who: queue for who, queue in root.inbox.items() if who in members}
        self.choices = {who: queue for who, queue in root.choices.items() if who in members}
        self.pool, self.early = [], list(self.state.queued_actions)
        self.done, self.automatic, self.confirming = set(), set(), set()
        self.asking, self.offers, self.settled = {}, {}, []
        self.wake, self.instant, self.asks = asyncio.Event(), asyncio.Queue(), asyncio.Queue()
        self.swaps, self.rules, self.plays = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
        self.private_requests, self.private_pending = asyncio.Queue(), set()
        self.private_lock, self.private_task = asyncio.Lock(), None
        self.open, self.closes_at, self.retrying = False, None, None
        self._init_planning()
        self.halted = False
        self.time_task: asyncio.Task | None = None
        self.time_asked: tuple | None = None
        for key, receipt in root.state.private_messages.items():
            if receipt["who"] in members and not receipt.get("delivered"):
                self.private_requests.put_nowait((receipt["who"], receipt["text"], key.split(":", 1)[1]))
                self.private_pending.add(key)

    @property
    def scene(self):
        return self.tables.runtime.scenes[self.ident]

    @property
    def over(self) -> bool:
        """The room's lifecycle is shared; a scene must never retain its own old end flag."""
        return self.root.over

    def _participants(self):
        return [person for person in self.state.investigators if person.id in self.scene.members]

    def _context(self, viewer: str | None):
        return self if viewer in self.scene.members else self.root._context(viewer)

    def _acts(self, seat: str) -> bool:
        return seat in self.scene.members and super()._acts(seat)

    def _accept_declaration(self, seat: str, text: str) -> None:
        if self.time_task is not None:
            self.time_asked = None
            self.time_task.cancel()
        self.tables.runtime.revoke_pass(self.ident)
        super()._accept_declaration(seat, text)

    def _time_available(self) -> bool:
        """An unstarted clock proposal must yield to actual input and actual choices."""
        return (self.open and not self.halted and not self.tables.closing and not self.root.state.ending
                and not self.scene.closed and not self.scene.busy and not self.state.held
                and self.phase not in ("thinking", "ready") and not self.joining
                and (self.preparing is None or self.preparing.done())
                and not self.state.pending_turn and not self.scene.reservation and not self.scene.encounter
                and not self.state.pending_push and not self.pool and not self.early and not self.asking
                and not self.state.queued_actions and not self.state.queued_receipts and not self.confirming
                and not self.private_pending and (self.private_task is None or self.private_task.done())
                and not any(self.state.unfinished.get(who, {}).get("execution") for who in self.scene.members)
                and any(self.sits.get(who) is not None for who in self.scene.members))

    def request_time(self, until: int) -> None:
        if not self._time_available() or not callable(getattr(self.cast, "scene_time", None)):
            return
        if self.time_task is not None and not self.time_task.done():
            return
        key = (self.scene.turn, self.scene.at, self.scene.logged, until)
        if key == self.time_asked:
            return
        self.time_asked = key
        stamp = (*key, self.state.record.seq)
        self.time_task = asyncio.create_task(self._handoff_time(until, stamp), name=f"scene-time:{self.ident}")

    async def _handoff_time(self, until: int, stamp: tuple) -> None:
        try:
            decision = TimeHandoff.model_validate(await self.cast.scene_time(self.state, until))
            current = (self.scene.turn, self.scene.at, self.scene.logged, until, self.state.record.seq)
            allowed = False
            if (decision.advance and current == stamp and self.time_asked == stamp[:4] and self._time_available()
                    and self.tables.runtime.time_requested(self.ident) == until):
                allowed = self.tables.runtime.pass_time(self.ident, until)
            elif decision.advance:
                self.time_asked = None  # expired permission may be reconsidered after a reconnect or other interruption
            self.game._debug("scene_time", {"until": until, "decision": decision.model_dump(),
                                            "status": "allowed" if allowed else "expired" if decision.advance else "declined"})
        except asyncio.CancelledError:
            if self.time_asked == stamp[:4]:
                self.time_asked = None
            raise
        except Exception as exc:
            self._failed("场景时间衔接", exc)
        finally:
            self.tables.runtime._notify()

    async def _stop_time(self) -> None:
        if self.time_task is not None:
            task, self.time_task = self.time_task, None
            if not task.done():
                self.time_asked = None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _on(self, cid: str, msg: dict[str, Any]) -> None:
        await super()._on(cid, msg)
        if (msg.get("t") == "done" and not self.pool and not self.early
                and all(person.id in self.done for person in self._participants() if person.can_act)):
            self.tables.runtime.pass_time(self.ident)
        self.tables.runtime.changed.set()

    async def _send(self, cid: str, msg: dict[str, Any]) -> None:
        await self.root._send(cid, msg)

    async def _pool(self) -> None:
        for cid in list(self.clients):
            if self._seat_of(cid) in self.scene.members:
                await self._send(cid, self._pool_view(cid))

    async def _share_book(self) -> None:
        await self.root._share_book()

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        if msg.get("t") == "event":
            event = dict(msg["ev"])
            event.setdefault("audience", list(self.scene.members))
            msg = {**msg, "ev": event}
        else:
            msg = {**msg, "audience": msg.get("audience", list(self.scene.members))}
        await self.root._broadcast(msg)

    def _start_privates(self) -> None:
        # Each consumer owns only its members; the retained mailbox itself is shared.
        if not self.asking and not self.state.pending_turn:
            for ident, receipt in self.state.private_messages.items():
                if (receipt["who"] in self.scene.members and not receipt.get("delivered")
                        and "reply" in receipt and ident not in self.private_pending):
                    self.private_pending.add(ident)
                    self.private_requests.put_nowait((receipt["who"], receipt["text"], ident.split(":", 1)[1]))
        if not self.private_requests.empty() and (self.private_task is None or self.private_task.done()):
            self.private_task = asyncio.create_task(self._privates())

    def _save(self) -> None:
        self.tables.runtime.checkpoint(self.ident)

    def _abandon_turn(self) -> None:
        self.tables.runtime.abandon(self.ident)

    async def _run(self, text: str, inv_id: str, *, ai: bool, push: str = "") -> None:
        while True:
            try:
                await super()._run(text, inv_id, ai=ai, push=push)
                return
            except SceneResourceConflict:
                await self.tables.runtime.prepare_conflict(self.ident)
                text = "/retry"

    async def run(self) -> None:
        self._start_privates()
        try:
            while not self.halted and not self.root.state.ending:
                if (not any(person.can_act for person in self._participants())
                        and self.game.world_encounter() is None and not self.state.pending_turn):
                    return
                humans = [who for who in self.scene.members if who in self.state.human]
                offline_wait = humans and not any(
                    self.sits.get(who) or who in self.takeovers.active for who in humans)
                if (offline_wait or not self.clients
                        or (self.takeovers.enabled and not self.takeovers.online())):
                    self.wake.clear()
                    await self.wake.wait()
                    continue
                if humans and all(who in self.takeovers.active for who in humans):
                    await self.takeovers.pace(self.ident, humans)
                if self.game.pending_party() is None:
                    if self.state.held:
                        await self._safely(self._tell_held(), "上一轮的叙述")
                    await self._collect()
                if self.halted or self.scene.closed:
                    return
                await self._stop_time()
                await self.tables.host_ready.wait()
                self.tables.runtime.set_busy(self.ident, True)
                try:
                    await self._settle()
                finally:
                    self.tables.runtime.set_busy(self.ident, False)
                await self.tables.regroup()
        finally:
            if self.preparing is not None:
                self.planning_revision += 1
                await self._stop_preparing()
            await self._stop_time()
            if self.private_task is not None:
                self.private_task.cancel()
                await asyncio.gather(self.private_task, return_exceptions=True)
            self._save()


class SceneTables:
    """Supervise independent hosts; all short world commits still use the shared record."""

    def __init__(self, root: Hub, cast_factory, rules_factory):
        self.root = root
        self.runtime = SceneRuntime(root.state, root.m, cast_factory, rules_factory, self._event)
        self.runtime.on_checkpoint = root._save
        self.runtime.on_time_block = self._request_time
        self.workers: dict[str, SceneTable] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.closing = False
        self.host_ready = asyncio.Event()
        self.host_ready.set()
        root.game.scene_runtime = self.runtime

    def _request_time(self, ident: str, until: int) -> None:
        if not self.closing and (worker := self.workers.get(ident)) is not None:
            worker.request_time(until)

    def for_member(self, who: str | None) -> SceneTable | None:
        return self.workers.get(self.runtime.for_member(who)) if who else None

    async def _event(self, ident: str, event: dict) -> None:
        if worker := self.workers.get(ident):
            await worker._event(event)

    def wake(self, who: str) -> None:
        if worker := self.for_member(who):
            worker.wake.set()

    async def regroup(self) -> None:
        async with self.lock:
            if self.closing:
                return
            active = set(self.runtime.reconcile())
            # Only unstarted input migrates. A sealed batch/journal/choice pins its controller.
            queued, receipts, retired = [], [], []
            for ident, worker in self.workers.items():
                if ident not in active and not worker.halted:
                    retired.append(worker)
                    queued.extend(worker.early)
                    receipts.extend(worker.state.queued_receipts)
                    if worker.open:
                        queued.extend(pair for pair in worker.pool if pair[0] not in worker.automatic)
                    worker.halted = True
                    worker.wake.set()
                    if self.tasks.get(ident) is not asyncio.current_task():
                        self.tasks[ident].cancel()
            for ident in active:
                if ident not in self.workers:
                    worker = self.workers[ident] = SceneTable(self, ident)
                    for who, text in queued:
                        if who in worker.scene.members and (who, text) not in worker.early:
                            worker.early.append((who, text))
                    worker.state.queued_receipts.extend(row for row in receipts
                                                        if row["seat"] in worker.scene.members)
                    worker._save_queued()
                    self.tasks[ident] = asyncio.create_task(worker.run(), name=f"table:{ident}")
            # A closed scene must not keep a director/image task that can still write facts.
            # Install the new routing before awaits so incoming declarations cannot fall
            # back into the no-longer-running root table.
            for worker in retired:
                if worker.private_task is not None:
                    worker.private_task.cancel()
                    await asyncio.gather(worker.private_task, return_exceptions=True)
                if task := self.tasks.get(worker.ident):
                    if task is not asyncio.current_task():
                        await asyncio.gather(task, return_exceptions=True)
                await self.runtime.retire(worker.ident)
            self.runtime.changed.set()

    async def run(self) -> None:
        await self.regroup()
        while not self.root.state.ending:
            self.runtime.changed.clear()
            for task in self.tasks.values():
                if task.done() and not task.cancelled() and task.exception():
                    raise task.exception()
            for ident, worker in list(self.workers.items()):
                if not worker.halted and (until := self.runtime.time_requested(ident)) is not None:
                    worker.request_time(until)
                for notice in list(worker.scene.notices):
                    if notice.get("type") == "time_cut":
                        if worker.halted or worker.scene.closed:
                            await self.root._broadcast({"t": "sync", "state": {}, "audience": list(worker.scene.members)})
                        else:
                            await worker._event({"type": "time", "clock": worker.state.clock,
                                                 "phase": time_of_day(worker.state.minutes)})
                        worker.scene.notices.remove(notice)
                        self.root._save()
            if (not any(person.can_act for person in self.root.state.investigators)
                    and not any(scene.encounter or scene.pending_turn for scene in self.runtime.scenes.values()
                                if not scene.closed)):
                break
            if ((not self.root.swaps.empty() or not self.root.rules.empty())
                    and not any(scene.busy or scene.pending_turn for scene in self.runtime.scenes.values()
                                if not scene.closed)):
                self.host_ready.clear()
                try:
                    swapping = not self.root.swaps.empty()
                    await self.root._swap()
                    if swapping:
                        for worker in self.workers.values():
                            if worker.halted:
                                continue
                            old = worker.cast
                            worker.cast = self.runtime.cast_factory(worker.ident)
                            await worker.game.replace_cast(worker.cast)
                            await old.close()
                    await self.root._house_rules()
                finally:
                    self.host_ready.set()
            wake = asyncio.create_task(self.runtime.changed.wait())
            running = [task for task in self.tasks.values() if not task.done()]
            try:
                await asyncio.wait([wake, *running], return_when=asyncio.FIRST_COMPLETED)
            finally:
                wake.cancel()
                await asyncio.gather(wake, return_exceptions=True)

    async def close(self) -> None:
        self.closing = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.runtime.close()
        self.root._save()
