"""The game's existing changes, expressed as world ops.

    settle_changes   the arbiter's and NPCs' WorldChange list (engine/world.apply): the same
                     changes are taken (engine/world.admissible, in order), and each lands as
                     ops that also keep GameState.world, item owners and inventories exactly
                     as engine/world.apply leaves them
    improvise        the director's plan (module/improv.py) as an Improvise op
    plugin_ops       a rule plugin's effects that touch the world (flags, conditions, notes)

Phase 2 swaps engine/world.apply for settle_changes in game.py and cast.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..engine import world as legacy_world
from ..engine.state import WorldChange
from .ops import AddFact, Fulfil, Improvise, MakePromise, Occur, Relate, SetFlag, Transfer
from .ops import Condition as ConditionOp
from .record import PLAYERS
from .store import Result

if TYPE_CHECKING:
    from ..plugins.schema import Effect
    from .store import World


def change_ops(w: World, c: WorldChange, *, source: str, spoken: str = "") -> list[Any]:
    """One admissible change as ops (ids from its key and the turn, so a retried turn repeats them)."""
    st = w.st
    me = st.investigator.id
    oid = f"wc:{st.turn}:{c.kind}:{c.key}"
    common = {"id": oid, "actor": source, "key": c.key, "tag": c.npc, "visibility": "public"}
    if c.kind == "transfer":
        return [Transfer(item=c.item, to=c.owner, text=c.text, **common)]
    if c.kind == "fulfill":
        if c.key in w.rec.promises and w.rec.promises[c.key].status == "active":
            return [Fulfil(promise=c.key, text=c.text, id=oid, actor=source, visibility="public")]
        return []
    if c.kind == "promise" and c.key not in w.rec.promises:
        by, to = (c.npc, me) if spoken and c.npc == source else (me, c.npc or PLAYERS)
        return [MakePromise(promise=c.key, by=by, to=to, text=c.text, **common)]
    if c.kind == "relationship" and c.npc:
        return [Relate(a=c.npc, b=me, rel="交情", note=c.text, **common)]
    fid = f"wc:{c.key}" if f"wc:{c.key}" not in w.rec.facts else f"wc:{c.key}@{st.turn}"
    kind = "relation" if c.kind == "relationship" else "state"
    return [AddFact(fact=fid, text=c.text, fact_kind=kind, about=[x for x in (me, c.npc) if x], known_by=[c.npc] if c.npc else [],
                    public=True, **common)]  # fmt: skip


def settle_changes(w: World, changes: list[WorldChange], *, source: str, spoken: str = "") -> list[Result]:
    """engine/world.apply through the world API."""
    present = legacy_world.actors(w.m, w.st)
    out: list[Result] = []
    for c in changes[:8]:
        if not legacy_world.admissible(w.m, w.st, c, source=source, spoken=spoken, present=present):
            continue
        ops = change_ops(w, c, source=source, spoken=spoken)
        if not ops:  # a promise restated: as engine/world.apply leaves it
            if c.kind == "promise":
                w.legacy(c.key, "promise", c.text, c.npc, source)
            continue
        out += w.apply_all(ops)
    return out


def improvise(w: World, plan: Any, *, at: str = "") -> Result:
    """A director's plan (agents/director.Improv) through the world API: checked, merged, seeded, logged."""
    data = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan)
    return w.apply(Improvise(plan=data, at=at, actor="director"))


def plugin_ops(w: World, plugin: str, effects: list[Effect]) -> list[Any]:
    """The world side of a plugin's effects. Sheet rules (attribute, damage, sanity) and clues stay
    with the engine; what they did is noted as an event."""
    out: list[Any] = []
    base = f"plugin:{plugin}:{w.st.turn}:{w.st.minutes}"
    for i, e in enumerate(effects):
        common = {"id": f"{base}:{i}", "actor": "engine"}
        if e.kind == "flag":
            out.append(SetFlag(flag=e.key, **common))
        elif e.kind == "condition":
            out.append(ConditionOp(entity=e.who, add=e.key if e.amount == 1 else "", remove=e.key if e.amount != 1 else "", **common))
        elif e.kind == "note" and e.text.strip():
            out.append(Occur(text=e.text[:300], place=w.st.location, visibility="public", **common))
    return out
