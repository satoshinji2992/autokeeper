"""Independent table loops over scene cursors and one authoritative world save."""

from __future__ import annotations

import asyncio
from typing import Any

from ..engine.game import PLAY_WAIT
from ..engine.scenes import SceneRuntime
from .hub import Hub

ROUTED = {"act", "done", "undo", "respond", "react", "speak", "kp_private", "retell", "suggest", "recount",
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
        self.halted = False
        for key, receipt in root.state.private_messages.items():
            if receipt["who"] in members and not receipt.get("delivered"):
                self.private_requests.put_nowait((receipt["who"], receipt["text"], key.split(":", 1)[1]))
                self.private_pending.add(key)

    @property
    def scene(self):
        return self.tables.runtime.scenes[self.ident]

    def _participants(self):
        return [person for person in self.state.investigators if person.id in self.scene.members]

    def _acts(self, seat: str) -> bool:
        return seat in self.scene.members and super()._acts(seat)

    def _accept_declaration(self, seat: str, text: str) -> None:
        self.tables.runtime.revoke_pass(self.ident)
        super()._accept_declaration(seat, text)

    async def _on(self, cid: str, msg: dict[str, Any]) -> None:
        await super()._on(cid, msg)
        if (msg.get("t") == "done" and not self.pool and not self.early
                and all(person.id in self.done for person in self._participants() if person.can_act)):
            self.tables.runtime.pass_time(self.ident)
        self.tables.runtime.changed.set()

    async def _send(self, cid: str, msg: dict[str, Any]) -> None:
        if self._seat_of(cid) in self.scene.members:
            await super()._send(cid, msg)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        if msg.get("t") == "event":
            event = dict(msg["ev"])
            event.setdefault("audience", list(self.scene.members))
            msg = {**msg, "ev": event}
        else:
            msg = {**msg, "audience": msg.get("audience", list(self.scene.members))}
        await super()._broadcast(msg)

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

    async def run(self) -> None:
        self._start_privates()
        try:
            while not self.halted and not self.root.state.ending:
                if not any(person.can_act for person in self._participants()):
                    return
                humans = [who for who in self.scene.members if who in self.state.human]
                if ((humans and not any(self.sits.get(who) for who in humans)) or not self.clients):
                    self.wake.clear()
                    await self.wake.wait()
                    continue
                if self.game.pending_party() is None:
                    if self.state.held:
                        await self._safely(self._tell_held(), "上一轮的叙述")
                    await self._collect()
                if self.halted or self.scene.closed:
                    return
                await self.tables.host_ready.wait()
                self.tables.runtime.set_busy(self.ident, True)
                try:
                    await self._settle()
                finally:
                    self.tables.runtime.set_busy(self.ident, False)
                await self.tables.regroup()
        finally:
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
        self.workers: dict[str, SceneTable] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.closing = False
        self.host_ready = asyncio.Event()
        self.host_ready.set()
        root.game.scene_runtime = self.runtime

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
            # Only unstarted input migrates. A pending journal/choice pins its controller.
            queued = []
            for ident, worker in self.workers.items():
                if ident not in active and not worker.halted:
                    queued.extend(worker.early)
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
                    worker._save_queued()
                    self.tasks[ident] = asyncio.create_task(worker.run(), name=f"table:{ident}")
            self.runtime.changed.set()

    async def run(self) -> None:
        await self.regroup()
        while not self.root.state.ending:
            for task in self.tasks.values():
                if task.done() and not task.cancelled() and task.exception():
                    raise task.exception()
            if not any(person.can_act for person in self.root.state.investigators):
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
            self.runtime.changed.clear()
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
