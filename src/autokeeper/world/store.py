"""Settling operations into the record: validated, idempotent, logged, undoable.

World binds one game's module copy and GameState. Every change goes through apply(op):

    repeat    an op id already settled is a no-op (a retried turn never settles twice);
              the same id with a different payload is refused
    check     the op says what is wrong, in words the director can fix
    settle    the op changes the record (and the GameState fields the game still reads:
              flags, item owners, inventories, GameState.world), emits its event
    journal   the entry keeps a digest, a summary and an inverse JSON patch (jsonpatch)
              over that domain, so the latest ops can be undone in order
    project   the record's truth is written back into this game's module copy

A failing settle restores the domain as it was: an op lands whole or not at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import jsonpatch

from ..engine.state import GameState, WorldFact
from ..module.schema import Module
from ..rules.coc import Investigator
from . import knowledge
from .record import ENGINE, PLAYERS, Agent, Entity, Entry, Event, Memory, Relation, WorldRecord, relation_key

if TYPE_CHECKING:
    from .ops import Op

UNDO_KEEP = 64  # entries that keep their undo patch
JOURNAL_KEEP = 4000  # entries kept for repeats and the trace
EVENTS_KEEP = 6000
MEMORY_KEEP = 40  # memories an important NPC keeps
# The journal's, the desk's, the scheduler's and the game's own clocks: undo leaves them.
BOOKKEEPING = {"journal", "requests", "ticks", "simulated", "left", "npc_budget_turn", "npc_budget_calls", "npc_budget_limit", "npc_work",
               "beats", "missing", "missing_turn"}


@dataclass
class Result:
    ok: bool
    op: str = ""
    problems: list[str] = field(default_factory=list)
    event: str = ""
    repeat: bool = False
    data: Any = None


def _pointer(path: str, key: str | int) -> str:
    return f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"


def undo_patch(after: Any, before: Any, path: str = "") -> list[dict]:
    """The JSON patch that turns `after` back into `before`. Unchanged parts are skipped with
    a plain ==, which runs in C; jsonpatch.make_patch compared the whole record item by item,
    and the record only grows (thousands of events late in a game). Lists that only grew at
    the end become removals from the end; any other changed list goes to jsonpatch."""
    if after == before:
        return []
    if isinstance(after, dict) and isinstance(before, dict):
        ops: list[dict] = []
        for k in after.keys() - before.keys():
            ops.append({"op": "remove", "path": _pointer(path, k)})
        for k, v in before.items():
            if k not in after:
                ops.append({"op": "add", "path": _pointer(path, k), "value": v})
            elif after[k] != v:
                ops += undo_patch(after[k], v, _pointer(path, k))
        return ops
    if isinstance(after, list) and isinstance(before, list):
        if len(after) > len(before) and after[: len(before)] == before:  # appended to
            return [{"op": "remove", "path": _pointer(path, i)} for i in range(len(after) - 1, len(before) - 1, -1)]
        return [{**op, "path": path + op["path"], **({"from": path + op["from"]} if "from" in op else {})}
                for op in jsonpatch.make_patch(after, before).patch]
    return [{"op": "replace", "path": path, "value": before}]


def digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class World:
    def __init__(self, m: Module, st: GameState, *, checker=None, initialize: bool = True):
        self.m, self.st = m, st
        self.checker = checker  # rewrite consistency hook (world/rewrite.py), an LLM check in phase 2
        self.reviewing = False  # a screen has the director look backstage after a long stretch of time (Game._look)
        self.clock: int | None = None  # game minutes ops happen at, while the scheduler runs a time skip
        if initialize:
            if self.rec.simulated is None:  # a new game: the world starts with the story
                self.rec.simulated = st.minutes
            self.sync()

    # -- basics ------------------------------------------------------------------------------
    @property
    def rec(self) -> WorldRecord:
        return self.st.record

    @property
    def now(self) -> int:
        return self.st.minutes if self.clock is None else self.clock

    def sync(self) -> list[str]:
        """Seed module objects new to the record and mark what reached the table."""
        knowledge.seed(self.m, self.st, self.rec)
        revealed = knowledge.sync(self.m, self.st, self.rec)
        knowledge.project(self.m, self.rec)
        return revealed

    def entity(self, ident: str) -> Entity | None:
        return self.rec.entities.get(ident)

    def name(self, ident: str) -> str:
        if ident == PLAYERS:
            return "调查员们"
        e = self.rec.entities.get(ident)
        return e.name if e else ident

    def place(self, ident: str) -> str:
        """Where an entity is: an investigator with the party, an item with its holder."""
        if any(i.id == ident for i in self.st.investigators):
            return self.st.location
        e = self.rec.entities.get(ident)
        if e is None:
            return ""
        if e.kind == "item" and e.at in self.rec.entities and self.rec.entities[e.at].kind != "location":
            return self.place(e.at)
        return e.id if e.kind == "location" else e.at

    def present(self, place: str) -> list[str]:
        """NPCs at a place, alive and not gone missing."""
        return [e.id for e in self.rec.entities.values() if e.kind == "npc" and e.at == place and e.status not in ("死亡", "失踪")]

    def agent(self, npc: str) -> Agent | None:
        return self.rec.agents.get(npc)

    def relation(self, a: str, b: str, kind: str) -> Relation | None:
        return self.rec.relations.get(relation_key(a, b, kind))

    def touch(self, obj) -> None:
        obj.version += 1
        obj.changed = self.rec.seq

    def version(self, ref: str) -> int:
        """The version of fact:<id>, entity:<id>, agent:<id>, thread:<id>, promise:<id>,
        relation:<a|b|kind>, scheduled:<id>; 0 when it does not exist."""
        kind, _, ident = ref.partition(":")
        table = {"fact": self.rec.facts, "entity": self.rec.entities, "agent": self.rec.agents, "thread": self.rec.threads,
                 "promise": self.rec.promises, "relation": self.rec.relations}.get(kind)  # fmt: skip
        if table is not None:
            obj = table.get(ident)
            return obj.version if obj else 0
        if kind == "event":
            e = self.rec.event(ident)
            return e.version if e else 0
        if kind == "scheduled":
            scheduled = self.rec.scheduled.get(ident)
            # Schedules are plain records rather than Versioned objects; their status
            # changes during simulation, so use a stable, JSON-safe content revision.
            return int(digest(scheduled.model_dump(mode="json"))[:12], 16) if scheduled else 0
        if kind == "flag":
            return int(ident in self.st.flags)
        return 0

    # -- what ops call ---------------------------------------------------------------------
    def emit(self, kind: str, text: str, *, actor: str = ENGINE, participants: list[str] | None = None, place: str = "",
             visibility: str = "auto", witnesses: list[str] | None = None, facts: list[str] | None = None,
             trace: str = "", op: str = "", reactive: bool = True, verbatim: bool = False) -> Event:  # fmt: skip
        """Log an event. auto: public where the party is, private elsewhere. NPCs at the place see
        it; important NPCs keep it as a memory, and every witness learns the facts it carries."""
        rec = self.rec
        place = place or self.place(actor)
        people = list(dict.fromkeys(x for x in [actor, *(participants or [])] if x and x != ENGINE))
        # A public event elsewhere is visible to people there, not an omniscient cutaway
        # for the players. Only events in their scene reach the table.
        public = visibility != "private" and place == self.st.location and place != ""
        npcs = [p for p in people if (x := rec.entities.get(p)) is not None and x.kind == "npc"]
        around = [] if visibility == "private" else self.present(place)  # covert: only who took part or was named
        seeing = list(dict.fromkeys([*npcs, *around, *(witnesses or [])]))
        base = f"ev:{op or rec.seq}"
        n = sum(1 for x in rec.events[-8:] if x.id.split("#")[0] == base)
        e = Event(id=base if not n else f"{base}#{n + 1}", seq=rec.seq, t=self.now, turn=self.st.turn, kind=kind, actor=actor,
                  participants=people, place=place, text=text if verbatim else text[:300], visibility="public" if public else "private",
                  witnesses=seeing, facts=list(facts or []), trace=trace[:120], op=op, reactive=reactive)  # fmt: skip
        rec.events.append(e)
        del rec.events[:-EVENTS_KEEP]
        for npc in dict.fromkeys([*seeing, *(p for p in people if p in rec.agents)]):
            for fid in e.facts:
                if (f := rec.facts.get(fid)) and npc not in f.known_by and npc in rec.entities:
                    f.known_by.append(npc)
            if a := rec.agents.get(npc):
                a.memory.append(Memory(t=e.t, turn=e.turn, text=e.text, event=e.id))
                del a.memory[:-MEMORY_KEEP]
        if public:
            for fid in e.facts:
                if f := rec.facts.get(fid):
                    knowledge.reveal(rec, f, self.st.turn, source=f"event:{e.id}", excerpt=e.text)
        return e

    def legacy(self, key: str, kind: str, text: str, npc: str, source: str) -> None:
        """Mirror into GameState.world, which the keeper and NPC prompts still read (engine/world.py)."""
        if key:
            self.st.world[key] = WorldFact(kind=kind, text=text, who=self.st.investigator.id, npc=npc, turn=self.st.turn, source=source)

    # -- settling ------------------------------------------------------------------------------
    def apply(self, op: Op | dict) -> Result:
        from .ops import parse

        try:
            op = parse(op) if isinstance(op, dict) else op
        except ValueError as exc:
            return Result(False, problems=[f"操作格式不对：{str(exc)[:200]}"])
        payload = op.model_dump(mode="json", exclude={"id"})
        dig = digest(payload)
        oid = op.id or f"{op.kind}:{dig[:12]}:{self.st.turn}"
        if prior := next((x for x in reversed(self.rec.journal) if x.id == oid), None):
            if prior.digest != dig:
                return Result(False, oid, [f"op id {oid} 已经用于另一个操作"])
            return Result(True, oid, event=prior.event, repeat=True)
        problems = op.check(self)
        if problems:
            return Result(False, oid, problems)
        before = self.domain()
        self.rec.seq += 1
        try:
            event, data = op.settle(self, oid)
        except Exception as exc:  # an op lands whole or not at all
            self.load(before)
            return Result(False, oid, [f"结算失败：{type(exc).__name__}: {str(exc)[:200]}"])
        after = self.domain()
        entry = Entry(id=oid, seq=self.rec.seq, kind=op.kind, digest=dig, turn=self.st.turn, t=self.now, actor=op.actor,
                      summary=(event.text if event else op.kind)[:200], public=bool(event and event.visibility == "public"),
                      event=event.id if event else "", after=digest(after),
                      undo=undo_patch(after, before) if op.undoable else None)  # fmt: skip
        self.rec.journal.append(entry)
        for old in self.rec.journal[:-UNDO_KEEP]:
            old.undo = None
        del self.rec.journal[:-JOURNAL_KEEP]
        knowledge.project(self.m, self.rec)
        return Result(True, oid, event=entry.event, data=data)

    def apply_all(self, ops: list[Op | dict]) -> list[Result]:
        return [self.apply(op) for op in ops]

    # -- undo ------------------------------------------------------------------------------------
    def undo(self, count: int = 1) -> list[str]:
        """Undo the latest settled ops, newest first. Refused for an op the players have seen in an
        earlier turn, an op that cannot be undone (an improvisation in the module) and when the
        state moved on outside the world API since (the inverse patch would not fit)."""
        problems: list[str] = []
        for _ in range(count):
            live = [x for x in self.rec.journal if not x.undone]
            if not live:
                return [*problems, "没有可以撤销的操作"]
            entry = live[-1]
            if entry.undo is None:
                return [*problems, f"{entry.id} 不能撤销"]
            if entry.public and entry.turn < self.st.turn:
                return [*problems, f"{entry.id} 玩家已经看到了，不能撤销"]
            now = self.domain()
            if digest(now) != entry.after:
                return [*problems, f"{entry.id} 之后状态又变了，不能直接撤销"]
            self.load(jsonpatch.apply_patch(now, entry.undo))
            entry.undone, entry.undo = True, None
            entry.digest = f"undone:{entry.digest}"  # the same op may be settled again
            entry.id = f"{entry.id}~undone{self.rec.seq}"
            knowledge.project(self.m, self.rec)
        return problems

    def undo_to(self, op_id: str) -> list[str]:
        """Undo every op back to and including op_id."""
        live = [x.id for x in self.rec.journal if not x.undone]
        if op_id not in live:
            return [f"没有找到操作 {op_id}"]
        return self.undo(len(live) - live.index(op_id))

    # -- the domain an op may change -----------------------------------------------------------
    def domain(self) -> dict[str, Any]:
        st = self.st
        return {
            "record": st.record.model_dump(mode="json", exclude=BOOKKEEPING),
            "flags": list(st.flags),
            "item_owners": dict(st.item_owners),
            "world": {k: v.model_dump(mode="json") for k, v in st.world.items()},
            "investigators": [i.model_dump(mode="json") for i in st.investigators],
        }

    def load(self, d: dict[str, Any]) -> None:
        st = self.st
        kept = {k: getattr(st.record, k) for k in BOOKKEEPING}
        st.record = WorldRecord.model_validate(d["record"])
        for k, v in kept.items():  # not part of the domain: the same objects go on
            setattr(st.record, k, v)
        st.flags = list(d["flags"])
        st.item_owners = dict(d["item_owners"])
        st.world = {k: WorldFact.model_validate(v) for k, v in d["world"].items()}
        st.investigators = [Investigator.model_validate(x) for x in d["investigators"]]
