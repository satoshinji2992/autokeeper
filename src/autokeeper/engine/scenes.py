"""Continuing scene contexts over one authoritative world.

SceneView routes execution cursors to SceneState and world data to the root save.
It never swaps the root's active character, pending journal or encounter across an await.
Only temporary read/validation copies materialize a GameState.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, MutableMapping
from copy import deepcopy
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from ..agents.cast import Action, Ruling
from ..module import improv
from ..module.schema import Module
from ..rules.coc import Rules
from . import graph as turn_graph
from .game import Game
from .state import GameState, LogEntry, SceneState, format_time, parse_time
from .timing import SceneResourceConflict, SceneTimeBoundary

LOCAL_FIELDS = set(SceneState.model_fields) - {"id", "members", "place", "at", "busy", "closed", "reservation", "notices", "passed_until"}
LOCAL_FIELDS |= {"location", "minutes"}


class ScenePositions(MutableMapping[str, str]):
    """The old relative `apart` interface, backed by explicit canonical positions."""

    def __init__(self, view: SceneView):
        self.view = view

    def __getitem__(self, key: str) -> str:
        place = self.view.root.positions[key]
        if place == self.view.scene.place:
            raise KeyError(key)
        return place

    def __setitem__(self, key: str, value: str) -> None:
        if key not in self.view.root.positions:
            raise KeyError(key)
        self.view.root.positions[key] = value

    def __delitem__(self, key: str) -> None:
        if key not in self or key not in self.view.scene.members:
            raise KeyError(key)  # removing a relative "apart" entry cannot teleport another scene's player
        self.view.root.positions[key] = self.view.scene.place

    def __iter__(self) -> Iterator[str]:
        return (key for key, place in self.view.root.positions.items() if place != self.view.scene.place)

    def __len__(self) -> int:
        return sum(place != self.view.scene.place for place in self.view.root.positions.values())

    def copy(self) -> dict[str, str]:
        return dict(self)


class SceneView:
    """A live scoped facade; root investigators/record/items remain the same objects."""

    def __init__(self, root: GameState, scene: SceneState):
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "scene", scene)
        object.__setattr__(self, "_apart", ScenePositions(self))

    def __getattr__(self, key: str) -> Any:
        if key == "location":
            return self.scene.place
        if key == "minutes":
            return self.scene.at
        if key == "apart":
            return self._apart
        if key in LOCAL_FIELDS:
            return getattr(self.scene, key)
        return getattr(self.root, key)

    def __setattr__(self, key: str, value: Any) -> None:
        if key == "location":
            self.scene.place = value
        elif key == "minutes":
            self.scene.at = value
            self.root.minutes = max(self.root.minutes, value)
        elif key == "apart":
            for who in self.scene.members:
                self.root.positions[who] = value.get(who, self.scene.place)
        elif key in LOCAL_FIELDS:
            setattr(self.scene, key, value)
        else:
            setattr(self.root, key, value)

    @property
    def investigator(self):
        return self.root.investigators[self.scene.active]

    @property
    def clock(self) -> str:
        return format_time(self.scene.at)

    def find(self, who: str):
        return self.root.find(who)

    def add(self, kind, text: str, who: str = "", **kwargs) -> LogEntry:
        # Assign the actual source scene and keep the same entry in the aggregate history.
        if kwargs.get("audience") is None:
            kwargs["audience"] = list(self.scene.members)
        entry = GameState.add(self, kind, text, who=who, **kwargs)
        entry.scene = self.scene.id
        self.root.log.append(entry)
        self.root.logged += 1
        return entry

    def rollback_parse(self, turn: int, count: int, logged: int) -> None:
        removed = {id(entry) for entry in self.scene.log[count:]}
        failed_turn = self.scene.turn
        self.root.log[:] = [entry for entry in self.root.log if id(entry) not in removed]
        self.root.logged = max(0, self.root.logged - len(removed))
        self.root.deeds[:] = [deed for deed in self.root.deeds if deed.turn != failed_turn]
        del self.scene.log[count:]
        self.scene.turn, self.scene.logged = turn, logged

    def _deed(self, entry: LogEntry, acting: str) -> None:
        GameState._deed(self, entry, acting)

    def recent_checks(self):
        return GameState.recent_checks(self)

    def npc(self, who: str):
        return GameState.npc(self, who)

    def record_usage(self, role: str, reply) -> None:
        GameState.record_usage(self, role, reply)
        self.root.record_usage(role, reply)

    def tally(self) -> None:
        previous = len(self.scene.turn_usage)
        GameState.tally(self)
        if len(self.scene.turn_usage) > previous:
            self.root.turn_usage.append(self.scene.turn_usage[-1])

    def materialize(self) -> GameState:
        """Short-lived model/validation snapshot; it is never another writable world."""
        data = self.root.model_dump(mode="python", exclude={"scenes"})
        for key in LOCAL_FIELDS - {"location", "minutes"}:
            value = getattr(self.scene, key)
            data[key] = value
        data.update(location=self.scene.place, minutes=self.scene.at, apart=dict(self._apart), scenes={})
        return GameState.model_validate(data)

    def model_dump(self, **kwargs):
        return self.materialize().model_dump(**kwargs)

    def model_dump_json(self, **kwargs):
        return self.materialize().model_dump_json(**kwargs)

    def model_copy(self, *, update=None, deep=False):
        return self.materialize().model_copy(update=update, deep=deep)


class SceneRuntime:
    """Own continuing contexts and causal time tickets over one authoritative save.

    A ticket is an authorized attempt, never its future outcome. A scene can settle up to
    another scene's prepared endpoint; it cannot jump past an idle, unconsulted player.
    """

    def __init__(self, state: GameState, module: Module, cast_factory: Callable[[str], Any],
                 rules_factory: Callable[[str], Rules], emit: Callable[[str, dict], Any]):
        self.state, self.module = state, module
        self.cast_factory, self.rules_factory, self.emit = cast_factory, rules_factory, emit
        self.changed = asyncio.Event()
        self._pulse = asyncio.Event()
        self._views: dict[str, SceneView] = {}
        self._games: dict[str, Game] = {}
        self._graphs: dict[str, Any] = {}
        self.on_checkpoint: Callable[[], None] | None = None
        for saved_scene in state.scenes.values():
            saved_scene.busy = False  # process-owned tasks are rebuilt from durable journals
        if not state.positions:
            state.positions = {inv.id: state.apart.get(inv.id, state.location) for inv in state.investigators}
        # Restore the module projection once, before several scoped Games bind it.
        if state.improv:
            improv.restore(module, state.improv)
        self.reconcile()

    @property
    def scenes(self) -> dict[str, SceneState]:
        return self.state.scenes

    def _notify(self) -> None:
        pulse, self._pulse = self._pulse, asyncio.Event()
        pulse.set()
        self.changed.set()

    def for_member(self, who: str) -> str:
        return next((scene.id for scene in self.scenes.values() if not scene.closed and who in scene.members), "")

    def _new(self, members: list[str], place: str, at: int,
             sources: list[SceneState] | None = None, *, opening: bool = False) -> SceneState:
        root = self.state
        root.next_scene += 1
        scene = SceneState(id=f"scene-{root.next_scene}", members=list(members), place=place, at=at,
                           active=next(n for n, inv in enumerate(root.investigators) if inv.id == members[0]), day=at // 1440)
        histories = sources or []
        # Aggregate history retains lines folded out of individual scene model windows.
        logs = root.log
        private = [*root.private_history, *(entry for source in histories for entry in source.private_history)]
        seen: set[tuple] = set()
        for entry in logs:
            # The immutable audience is retained, including former private lines on a merge.
            key = (entry.turn, entry.time, entry.kind, entry.who, entry.text, entry.delivery, tuple(entry.audience or []))
            if key in seen or (entry.audience is not None and not set(entry.audience).intersection(members)):
                continue
            seen.add(key)
            scene.log.append(entry)
        scene.log.sort(key=lambda entry: (parse_time(entry.time), entry.turn))
        scene.private_history = [entry for entry in private
                                 if entry.audience is None or set(entry.audience).intersection(members)]
        scene.logged = len(scene.log)
        # A retained group keeps its model sessions; changing members starts a separate
        # session from attributed history rather than cloning another player's private chat.
        if opening and set(members) == {inv.id for inv in root.investigators}:
            for key in LOCAL_FIELDS - {"location", "minutes", "active", "log", "private_history", "logged"}:
                if hasattr(root, key):
                    setattr(scene, key, deepcopy(getattr(root, key)))
            scene.turn, scene.active = root.turn, root.active
        elif opening and root.investigator.id in members:
            # The root opening may have paused in a real encounter or a declaration.
            for key in ("pending_turn", "pending_push", "encounter", "encounter_log", "round_spent", "round_elapsed",
                        "round_plan", "held", "queued_actions", "queued_receipts"):
                setattr(scene, key, deepcopy(getattr(root, key)))
            scene.turn, scene.active = root.turn, root.active
        if histories:
            scene.ongoing = "\n".join(dict.fromkeys(source.ongoing for source in histories if source.ongoing))
            scene.journey = next((source.journey for source in histories if source.journey), "")
        root.scenes[scene.id] = scene
        return scene

    def reconcile(self) -> list[str]:
        """Replace stable membership once. Busy journals and encounters retain their owner.

        A new arrival cannot merge with an earlier scene until both have reached the same
        actual minute. A genuine split inherits the old scene's time and attributed history.
        """
        root = self.state
        current = [scene for scene in self.scenes.values() if not scene.closed]
        if not current:
            groups: dict[str, list[str]] = {}
            for inv in root.investigators:
                groups.setdefault(root.positions[inv.id], []).append(inv.id)
            return [self._new(members, place, root.minutes, opening=True).id for place, members in groups.items()]
        fixed = [scene for scene in current if scene.busy or scene.pending_turn or scene.encounter or scene.reservation]
        owned = {who for scene in fixed for who in scene.members}
        source_for = {who: scene for scene in current for who in scene.members}
        groups: dict[tuple[str, int], list[str]] = {}
        for inv in root.investigators:
            if inv.id in owned:
                continue
            source = source_for.get(inv.id)
            at = source.at if source else root.minutes
            groups.setdefault((root.positions[inv.id], at), []).append(inv.id)
        active = [scene.id for scene in fixed]
        retained: set[str] = set(active)
        for (place, at), members in groups.items():
            exact = next((scene for scene in current if scene.id not in retained
                          and scene.place == place and set(scene.members) == set(members)), None)
            if exact is not None:
                active.append(exact.id)
                retained.add(exact.id)
                continue
            sources = list({source_for[who].id: source_for[who] for who in members if who in source_for}.values())
            created = self._new(members, place, at, sources)
            active.append(created.id)
            retained.add(created.id)
        # Every replaced group closes together, including a source split into two smaller groups.
        for previous in current:
            if previous.id not in retained:
                previous.closed = True
        self._notify()
        return active

    def state_view(self, ident: str) -> SceneView:
        if ident not in self._views:
            self._views[ident] = SceneView(self.state, self.scenes[ident])
        return self._views[ident]

    def game(self, ident: str) -> Game:
        if ident not in self._games:
            cast = self.cast_factory(ident)
            if any(game.cast is cast for game in self._games.values()):
                raise ValueError("不同现场必须有独立的模型会话")
            game = Game(self.module, self.state_view(ident), cast, self.rules_factory(ident), restore=False)
            game.checkpoint = lambda: self.checkpoint(ident)
            game.allocate_turn = self.allocate_turn
            game.time_reserve = lambda action, ruling: self.reserve(ident, action, ruling)
            game.time_wait = lambda end=None: self.wait(ident, end)
            game.time_commit = lambda end: self.commit_time(ident, end)
            game.time_release = lambda: self.release(ident)
            game.listener = game.emit = lambda event: self.emit(ident, event)
            self._games[ident] = game
        return self._games[ident]

    def graph(self, ident: str):
        if ident not in self._graphs:
            self._graphs[ident] = turn_graph.build(self.game(ident), InMemorySaver())
        return self._graphs[ident]

    def allocate_turn(self) -> int:
        self.state.turn += 1
        return self.state.turn

    def set_busy(self, ident: str, busy: bool) -> None:
        self.scenes[ident].busy = busy
        self._notify()

    def revoke_pass(self, ident: str) -> None:
        self.scenes[ident].passed_until = None
        self._notify()

    def pass_time(self, ident: str, until: int | None = None) -> bool:
        scene = self.scenes[ident]
        if scene.pending_turn or scene.encounter or scene.reservation or scene.busy:
            return False  # a reaction/active attempt cannot silently turn into passive waiting
        scene.passed_until = -1 if until is None else max(scene.at, until)
        self.checkpoint(ident)
        return True

    def reserve(self, ident: str, action: Action, ruling: Ruling | None) -> None:
        scene, game = self.scenes[ident], self.game(ident)
        saved = scene.reservation
        if saved and saved.get("turn") == scene.turn and saved.get("attempt"):
            return  # retries restore the original start/end instead of charging time again
        start = game._actor_cursor()
        heading = (ruling.go if ruling else "") or action.destination
        travel = (game._travel(scene.place, heading) or 0) if heading and heading != scene.place else 0
        if action.arrival_after_success:
            travel = game._travel(scene.place, action.destination) or 0  # conservative until the roll determines entry
        minutes = max(0, min(ruling.minutes, 720 if action.kind == "wait" or ruling.rest else 180)) if ruling else 0
        if action.kind == "wait":
            request = scene.pending_turn.get("request", scene.pending_turn)
            window = request.get("wait_window", {})
            ends = ([window["end"]] if window else [])
            ends += [start + action.wait_limit] if action.wait_limit is not None else []
            ends += [parse_time(action.wait_until)] if action.wait_until else []
            if ends:
                minutes = min(minutes, max(0, min(ends) - start - travel))
        resources = {self.state.investigators[scene.active].id, *action.companions}
        if action.npc:
            resources.add(action.npc)
        resources.update(row.npc for row in action.before)
        # An actual important object is shared even if the words use its display name.
        target = action.target.strip()
        resources.update(ent.id for ent in game.world.rec.entities.values()
                         if ent.kind == "item" and target and target in (ent.id, ent.name))
        scene.reservation = {"turn": scene.turn, "start": start, "end": start + travel + minutes,
                             "actor": self.state.investigators[scene.active].id,
                             "original": scene.pending_turn.get("input", action.summary),
                             "resources": sorted(resources), "phase": "prepared",
                             "attempt": action.model_dump(mode="json"),
                             "versions": self._resource_versions(resources)}
        scene.passed_until = None
        self.checkpoint(ident)

    def _resource_versions(self, resources: set[str]) -> dict[str, dict]:
        return {who: {"version": ent.version, "at": ent.at, "status": ent.status}
                for who in resources if (ent := self.state.record.entities.get(who)) is not None}

    def blockers(self, ident: str, end: int | None = None) -> list[dict]:
        own = self.scenes[ident]
        ticket = own.reservation
        end = ticket.get("end", own.at) if end is None else end
        start = ticket.get("start", own.at)
        blocks = []
        for other in self.scenes.values():
            if other.closed or other.id == ident:
                continue
            peer = other.reservation
            shared = set(ticket.get("resources", [])).intersection(peer.get("resources", []))
            overlap = peer and start <= peer["end"] and peer["start"] <= end
            if shared and overlap:
                order = (ticket.get("start", own.at), ticket.get("turn", own.turn), ident)
                peer_order = (peer["start"], peer.get("turn", other.turn), other.id)
                if order < peer_order and ticket.get("joint_reviewed"):
                    continue
                blocks.append({"scene": other.id, "kind": "resource", "resources": sorted(shared), "at": other.at,
                               "owner": order < peer_order})
                continue
            if end <= other.at or other.passed_until == -1 or (other.passed_until is not None and end <= other.passed_until):
                continue
            # Equal endpoints are safe for independent resources. Neither final result exists
            # until its own commit; shorter attempts may continue before a longer result lands.
            if peer and end <= peer["end"]:
                continue
            blocks.append({"scene": other.id, "kind": "time", "at": other.at,
                           "until": peer.get("end", other.at)})
        return blocks

    async def wait(self, ident: str, end: int | None = None) -> None:
        scene = self.scenes[ident]
        if end is not None:
            if not scene.reservation:
                scene.reservation = {"turn": scene.turn, "start": scene.at, "end": end,
                                     "resources": [self.state.investigators[scene.active].id], "phase": "prepared"}
            else:
                scene.reservation["end"] = max(scene.reservation["end"], end)
            self.checkpoint(ident)
        while True:
            if scene.closed:
                raise ValueError("该现场已经交接，不能继续结算原行动")
            resources = set(scene.reservation.get("resources", []))
            current = self._resource_versions(resources)
            expected = scene.reservation.get("versions", current)
            changed = [who for who in expected if expected[who] != current.get(who)]
            if changed and scene.reservation.get("phase") != "running":
                conflict = {"resources": changed, "changed": True,
                            "reason": "共同人物或物品已实际变化，旧裁定尚未执行；须按最新位置、归属和实际经过重新安排",
                            "attempts": [{"scene": ident, "actor": scene.reservation.get("actor"),
                                          "original": scene.reservation.get("original", ""),
                                          "action": scene.reservation.get("attempt", {})}]}
                scene.pending_turn["scene_conflict"] = conflict
                self.checkpoint(ident)
                raise SceneResourceConflict(conflict)
            blocks = self.blockers(ident)
            scene.reservation["blocked"] = blocks
            if any(block.get("owner") for block in blocks):
                conflict = {"resources": sorted({resource for block in blocks for resource in block.get("resources", [])}),
                            "attempts": [{"scene": ident, "actor": scene.reservation.get("actor"),
                                          "original": scene.reservation.get("original", ""),
                                          "action": scene.reservation.get("attempt", {})},
                                         *({"scene": block["scene"], **self.scenes[block["scene"]].reservation}
                                           for block in blocks if block.get("kind") == "resource")]}
                scene.pending_turn["scene_conflict"] = conflict
                self.checkpoint(ident)
                raise SceneResourceConflict(conflict)
            if not blocks:
                scene.reservation["phase"] = "running"
                shown = scene.reservation.pop("waiting_shown", None)
                self.checkpoint(ident)
                if shown is not None:
                    await self.emit(ident, {"type": "status", "text": "", "audience": list(scene.members)})
                return
            message = (f"行动预计于{format_time(scene.reservation.get('end', scene.at))}结束；"
                       + ("正在协调共同人物或物品。" if any(block["kind"] == "resource" for block in blocks)
                          else "等待其他现场推进。"))
            if scene.reservation.get("waiting_shown") != message:
                scene.reservation["waiting_shown"] = message
                self.checkpoint(ident)
                await self.emit(ident, {"type": "status", "text": message, "audience": list(scene.members)})
                continue  # the transport await may have admitted another declaration
            self.checkpoint(ident)
            pulse = self._pulse
            # No await occurred between the predicate and capturing its next change signal.
            await pulse.wait()

    def commit_time(self, ident: str, end: int) -> None:
        scene = self.scenes[ident]
        if end <= scene.at:
            return
        if not scene.reservation or end > scene.reservation.get("end", scene.at) or self.blockers(ident, end):
            raise SceneTimeBoundary(end)
        # Permission is consumed at the actual synchronous clock mutation. A new declaration
        # accepted before this point revokes permission; one accepted afterwards sees this time.
        for other in self.scenes.values():
            if other.closed or other.id == ident or other.at >= end or other.passed_until is None:
                continue
            if other.passed_until == -1 or end <= other.passed_until:
                other.at = end
                other.day = end // 1440
        scene.reservation["committed"] = max(scene.reservation.get("committed", scene.at), end)

    def resolve_resource_conflict(self, ident: str) -> None:
        """A caller's same-KP joint ruling grants execution, never item ownership itself.

        The other ticket remains intact and must be refreshed from the changed record before
        it can commit. This acknowledgement is intentionally explicit, not network priority.
        """
        scene = self.scenes[ident]
        if "scene_conflict" not in scene.pending_turn:
            raise ValueError("没有需要共同裁定的行动")
        scene.reservation["joint_reviewed"] = True
        scene.reservation["versions"] = self._resource_versions(set(scene.reservation.get("resources", [])))
        scene.pending_turn["scene_conflict"]["reviewed"] = True
        self.checkpoint(ident)

    def release(self, ident: str) -> None:
        self.scenes[ident].reservation.clear()
        self.checkpoint(ident)

    def checkpoint(self, ident: str) -> None:
        self.state.minutes = max(self.state.minutes, self.scenes[ident].at)
        self._notify()
        if self.on_checkpoint is not None:
            self.on_checkpoint()

    async def close(self) -> None:
        for game in self._games.values():
            await game.close()
            if close := getattr(game.cast, "close", None):
                await close()
        self._games.clear()
        self._graphs.clear()
