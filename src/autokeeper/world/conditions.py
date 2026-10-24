"""Small, structured histories for the condition language, independent of undo retention.

Submission order is not game time. Values are selected by effective minute and then sequence;
writers may not replace a field that already has a committed value in a later scene. Missing
objects/values are unknown, including under negation. This is not a historical world snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..engine import world as legacy_world
from ..engine.state import within
from .record import PLAYERS, ConditionValue

if TYPE_CHECKING:
    from .record import Event
    from .store import World

Value = bool | str | None


@dataclass
class Snapshot:
    values: dict[str, Value] = field(default_factory=dict)
    knowledge: dict[str, set[str]] = field(default_factory=dict)


def capture(w: World) -> Snapshot:
    """Capture only fields used by conditions; knowledge stays sparse rather than NPC × facts."""
    out = Snapshot()
    out.values.update({f"flag:{flag}": flag in w.st.flags for flag in set(w.m.flags) | set(w.st.flags)})
    for ident, entity in w.rec.entities.items():
        out.values[f"exists:{ident}"] = True
        if entity.kind in {"npc", "creature", "investigator"}:
            out.values[f"position:{ident}"] = w.place(ident)
        if entity.kind in {"npc", "creature"}:
            out.values[f"gone:{ident}"] = entity.status in {"死亡", "失踪"}
        if entity.kind == "item":
            out.values[f"owner:{ident}"] = legacy_world.owner(w.m, w.st, ident)
    for ident, fact in w.rec.facts.items():
        out.values[f"fact:{ident}"] = True
        who = set() if fact.retracted else set(fact.known_by)
        who.discard(PLAYERS)
        if not fact.retracted and fact.revealed is not None:
            who.add(PLAYERS)
        out.knowledge[ident] = who
    out.values.update({f"promise:{ident}": promise.status for ident, promise in w.rec.promises.items()})
    return out


def value(w: World, ref: str, *, at: int | None = None) -> Value:
    """A committed value effective at this scene's time; None before its first entry."""
    when = w.now if at is None else at
    row = max((row for row in reversed(w.rec.conditions.get(ref, ())) if row.t <= when),
              key=lambda row: (row.t, row.seq), default=None)
    return row.value if row is not None else None


def _latest(w: World, ref: str) -> Value:
    row = max(reversed(w.rec.conditions.get(ref, ())), key=lambda row: (row.t, row.seq), default=None)
    return row.value if row is not None else None


def _push(w: World, ref: str, current: Value) -> None:
    rows = w.rec.conditions.setdefault(ref, [])
    if not rows or _latest(w, ref) != current:
        rows.append(ConditionValue(t=w.now, seq=w.rec.seq, value=current, seed=not rows))


def changes(before: Snapshot, after: Snapshot) -> dict[str, Value]:
    out = {ref: after.values.get(ref) for ref in before.values.keys() | after.values.keys()
           if ref not in before.values or before.values.get(ref) != after.values.get(ref)}
    for fact in before.knowledge.keys() | after.knowledge.keys():
        old, new = before.knowledge.get(fact, set()), after.knowledge.get(fact, set())
        out.update({f"knows:{who}:{fact}": who in new for who in old ^ new})
    return out


def _parents(ref: str) -> list[str]:
    kind, _, rest = ref.partition(":")
    if kind == "knows":
        who, _, fact = rest.partition(":")
        return [f"fact:{fact}", *([] if who == PLAYERS else [f"exists:{who}"])]
    return [f"exists:{rest}"] if kind in {"owner", "gone", "position"} else []


def conflicts(w: World, refs: set[str]) -> list[str]:
    """A late submitted old action cannot overwrite an already committed future value."""
    checked = refs | {parent for ref in refs for parent in _parents(ref)}
    return [ref for ref in sorted(checked) if any(row.t > w.now for row in w.rec.conditions.get(ref, ()))]


def targets(w: World, op: Any, event: Event | None = None) -> set[str]:
    """Include explicit no-op assignments, so reasserting a future value cannot backdate it."""
    if op.kind == "flag":
        return {f"flag:{op.flag}"}
    if op.kind == "move":
        return {f"position:{op.entity}"}
    if op.kind == "effects":
        out = set()
        for effect in op.effects:
            if effect.kind == "relocate":
                out.add(f"position:{effect.who}")
            elif effect.kind == "party":
                movers = (set(effect.participants) if effect.participants is not None else
                          {inv.id for inv in w.st.investigators if inv.id not in w.st.apart})
                out.update(f"position:{who}" for who in movers | effect.apart.keys())
        return out
    if op.kind in {"transfer", "bring"}:
        return {f"owner:{op.item}"}
    if op.kind in {"promise", "fulfil", "break"}:
        return {f"promise:{op.promise}"}
    if op.kind == "condition" and op.status is not None:
        return {f"gone:{op.entity}"}
    if op.kind == "learn":
        from .knowledge import resolve_ref

        fact = resolve_ref(w, op.actor, op.fact) or op.fact
        return {f"knows:{who}:{fact}" for who in op.who}
    if op.kind == "rewrite" and op.target.startswith("fact:") and op.known_by is not None:
        fact = op.target.removeprefix("fact:")
        old = w.rec.facts.get(fact)
        return {f"knows:{who}:{fact}" for who in {*op.known_by, *(old.known_by if old else [])}}
    if event is not None and event.facts:
        return {f"knows:{who}:{fact}" for fact in event.facts
                for who in {*event.participants, *event.witnesses} if who in w.rec.entities or who == PLAYERS}
    return set()


def record(w: World, before: Snapshot, after: Snapshot, *, explicit: set[str] | None = None) -> list[str]:
    """Validate and append one operation's condition deltas, as part of its ordinary transaction."""
    delta = changes(before, after)
    if moved := conflicts(w, set(delta) | (explicit or set())):
        return moved
    for ref, current in delta.items():
        _push(w, ref, current)
    return []


def sync(w: World) -> None:
    """Seed new objects and record an explicitly synchronized current-state change.

    Existing histories survive reload; merely visiting an older scene does not copy its shared
    latest values back into the past. Direct fixture/data edits can be declared with World.sync.
    """
    current = capture(w)
    desired = dict(current.values)
    for fact, members in current.knowledge.items():
        desired.update({f"knows:{who}:{fact}": True for who in members})
    for ref in w.rec.conditions:
        if ref.startswith("knows:"):
            _kind, _, rest = ref.partition(":")
            who, _, fact = rest.partition(":")
            desired.setdefault(ref, who in current.knowledge.get(fact, set()))
        else:
            desired.setdefault(ref, None)
    delta = {ref: current for ref, current in desired.items()
             if ref not in w.rec.conditions or _latest(w, ref) != current}
    if moved := conflicts(w, set(delta)):
        raise ValueError("不能在较早时点覆盖已有未来条件：" + "、".join(moved))
    for ref, current in delta.items():
        _push(w, ref, current)


def test(w: World, kind: str, rest: str) -> bool | None:
    """Evaluate one atom without turning an uncreated object into a known negative fact."""
    a, _, b = rest.partition(":")
    if kind == "hour":
        return within(rest, w.now)
    if kind == "after":
        return w.now >= int(rest)
    if kind == "flag":
        result = value(w, f"flag:{rest}")
        return result if isinstance(result, bool) else None
    if kind == "promise":
        actual = value(w, f"promise:{a}")
        return actual == b if isinstance(actual, str) else None
    if kind == "knows":
        if value(w, f"fact:{b}") is not True or (a != PLAYERS and value(w, f"exists:{a}") is not True):
            return None
        result = value(w, f"knows:{a}:{b}")
        return result if isinstance(result, bool) else False
    if kind == "at":
        who = w.st.investigator.id if a == PLAYERS else a
        if value(w, f"exists:{who}") is not True or value(w, f"exists:{b}") is not True:
            return None
        where = w.place_at(who, w.now)
        return where == b if where else None
    if kind == "owner":
        if value(w, f"exists:{a}") is not True or (b != PLAYERS and value(w, f"exists:{b}") is not True):
            return None
        owner = value(w, f"owner:{a}")
        if not isinstance(owner, str):
            return None
        return owner == b or (b == PLAYERS and any(inv.id == owner for inv in w.st.investigators))
    if kind == "gone":
        if value(w, f"exists:{rest}") is not True:
            return None
        result = value(w, f"gone:{rest}")
        return result if isinstance(result, bool) else None
    return None
