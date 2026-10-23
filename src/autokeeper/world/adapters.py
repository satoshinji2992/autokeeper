"""The game's existing changes, expressed as world ops.

    settle_changes   the arbiter's and NPCs' WorldChange list (engine/world.apply): the same
                     changes are taken (engine/world.admissible, in order), and each lands as
                     ops that also keep GameState.world, item owners and inventories exactly
                     as engine/world.apply leaves them
    improvise        the director's plan (module/improv.py) as an Improvise op
    plugin_ops       a rule plugin's effects that touch the world (flags, conditions, notes)
    bring_ops        everyday things where the investigators are, as Bring (or, picked up, Transfer)
                     ops: the keeper's scene, an NPC's reply and the arbiter's ruling share it
    reply_ops        an NPC's on-the-spot acts in its reply (taking out, handing over, going off),
                     as groups of ops for its own gateway (world/tools.Gateway)

Phase 2 swaps engine/world.apply for settle_changes in game.py and cast.py.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from ..engine import fog
from ..engine import world as legacy_world
from ..engine.state import WorldChange
from .ops import (
    MAX_BRING,
    AddFact,
    Bring,
    Fulfil,
    Improvise,
    MakePromise,
    Occur,
    Relate,
    SetFlag,
    Transfer,
    bring_count,
    holding,
    lacking,
    named_item,
    overlap,
)
from .ops import Condition as ConditionOp
from .record import PLAYERS
from .store import Result, digest

if TYPE_CHECKING:
    from ..plugins.schema import Effect
    from .store import World


def change_ops(w: World, c: WorldChange, *, source: str, spoken: str = "", who: str = "") -> list[Any]:
    """One admissible change as ops (ids from its key and the turn, so a retried turn repeats them)."""
    st = w.st
    me = who or st.investigator.id
    oid = f"wc:{st.turn}:{c.kind}:{c.key}"
    common = {"id": oid, "actor": me if who and not spoken else source, "key": c.key, "tag": c.npc, "visibility": "public"}
    if c.kind == "transfer":
        ident = legacy_world.item_id(w.m, st, c.item, holder=source if spoken else me)
        return [Transfer(item=ident, to=c.owner, text=c.text, **common)] if ident is not None else []
    if c.kind == "fulfill":
        if c.key in w.rec.promises and w.rec.promises[c.key].status == "active":
            return [Fulfil(promise=c.key, text=c.text, id=oid, actor=common["actor"], visibility="public")]
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


def settle_changes(w: World, changes: list[WorldChange], *, source: str, spoken: str = "", private: bool = False,
                   who: str = "") -> list[Result]:
    """engine/world.apply through the world API."""
    state = w.st
    if who:
        index = next(i for i, investigator in enumerate(state.investigators) if investigator.id == who)
        state = state.model_copy(update={"active": index, "location": w.place(who)})
    present = {i.id for i in state.investigators} | set(w.present(state.location)) | {state.location}
    out: list[Result] = []
    for c in changes[:8]:
        if not legacy_world.admissible(w.m, state, c, source=source, spoken=spoken, present=present):
            continue
        ops = change_ops(w, c, source=source, spoken=spoken, who=who)
        if private:
            ops = [op.model_copy(update={"visibility": "private"}) for op in ops]
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


class Brought(NamedTuple):
    """What bring_ops makes of a list of things: the ops to land, in order; what cannot be registered,
    one line a thing, with why; and for each thing asked about, the id of what stands for it (new,
    picked up or already there), empty when it was refused."""

    ops: list[Bring | Transfer]
    problems: list[str]
    ids: list[str]


def _field(thing: Any, key: str) -> str:
    if isinstance(thing, str):
        return thing if key == "name" else ""
    value = thing.get(key, "") if isinstance(thing, Mapping) else getattr(thing, key, "")
    return str(value or "").strip()


def _person(w: World, who: str) -> str:
    """A person's id from an id or a name: the full name, else part of the name of just one person in
    the scene (托马斯 for 托马斯·金博尔); as given when nobody answers to it."""
    if any(i.id == who for i in w.st.investigators) or ((e := w.rec.entities.get(who)) is not None and e.kind == "npc"):
        return who
    people = [(i.id, i.name) for i in w.st.investigators] + [(e.id, e.name) for e in w.rec.entities.values() if e.kind == "npc"]
    named = [pid for pid, name in people if name == who]
    if not named and len(who) >= 2:
        here = {i.id for i in w.st.investigators} | set(w.present(w.st.location))
        named = [pid for pid, name in people if pid in here and who in name]
    return named[0] if len(named) == 1 else who


def _location(w: World, where: str) -> str:
    """A place's id from an id or one of its names (engine/fog.aliases); empty when none or several fit."""
    if (e := w.rec.entities.get(where)) is not None and e.kind == "location":
        return where
    named = [pid for pid, names in fog.aliases(w.m).items() if fog.plain(where) in names]
    return named[0] if len(named) == 1 else ""


def _lying(w: World, name: str, taken: list[str], *, here: str = "") -> str:
    """An everyday thing of this name lying where the investigators are, not spoken for in this batch:
    one registered as it came up (Bring), or one of the module's items of no importance."""
    here = here or w.st.location
    candidates = {e.id: e.name for e in w.rec.entities.values() if e.kind == "item" and not e.important
                  and e.at == here and e.id not in taken}
    candidates.update({i.id: i.name for i in w.m.items if not i.important and i.id not in taken
                       and i.id not in w.rec.entities and legacy_world.owner(w.m, w.st, i.id) == here})
    return named_item(name, candidates.items())


def bring_ops(w: World, items: Iterable[Any], *, actor: str, holder: str | None = None, place: str | None = None,
              missing: Iterable[str] = (), visibility: str = "auto") -> Brought:
    """Everyday things where the investigators are, as world ops (ops.Bring): what someone has on
    them or what lies about, for the keeper's scene, an NPC's reply or the arbiter's ruling alike.
    Pure: nothing lands. Each op is checked against the record as it is now (scene, names, this
    turn's Ruling.missing and limit) and against the batch itself (the same thing twice, the limit,
    `missing` besides the recorded one).

    A thing is a name, or a mapping or object with `name` and optionally `look`, `holder` or `place`
    (ids, or the names the table knows), which take the place of the keyword arguments. Something
    of its name already there is left as it is; an everyday thing of its name lying where the
    investigators are goes to a holder who takes it up (Transfer); anything else is brought."""
    missing = [x for x in (str(m).strip() for m in missing) if x]
    ops: list[Bring | Transfer] = []
    problems: list[str] = []
    ids: list[str] = []
    batch: list[tuple[str, str]] = []  # (where, name) brought in this batch
    count = bring_count(w)
    for thing in items:
        name = _field(thing, "name")
        own_holder, own_place = _field(thing, "holder"), _field(thing, "place")
        who, at = (own_holder, own_place) if own_holder or own_place else (holder or "", place or "")
        who = _person(w, who) if who else ""
        at = (_location(w, at) or at) if at else ""
        where = who or at
        if not name:
            problems.append("有一件东西没写名称")
            ids.append("")
            continue
        here = w.place(actor) or w.place(who) or w.st.location
        try:
            there = holding(w, where, name) if where else ""
            lying = _lying(w, name, ids, here=here) if not there and who and not at else ""
        except ValueError as exc:
            problems.append(str(exc))
            ids.append("")
            continue
        if there:
            ids.append(there)  # it is already where it is said to be
            continue
        if lying:  # taken up from where it lies
            take = Transfer(id=f"pick:{w.st.turn}:{lying}:{who}", item=lying, to=who, actor=actor,
                            visibility=visibility)  # type: ignore[arg-type]
            if bad := take.check(w):
                problems.append(f"{name}：{'；'.join(bad)}")
                ids.append("")
            else:
                ops.append(take)
                ids.append(lying)
            continue
        ident = f"thing:{digest([w.st.turn, actor, where, name])[:10]}"
        op = Bring(id=f"bring:{ident}", item=ident, name=name, look=_field(thing, "look")[:80], holder=who, place=at, actor=actor,
                   visibility=visibility)  # type: ignore[arg-type]
        bad = op.check(w)  # the record as it is now: then what this batch adds to it
        if any(w0 == where and overlap(name, n0) for w0, n0 in batch):
            bad.append("同一件东西写了两次")
        if not lacking(w, name) and (gap := next((x for x in missing if overlap(name, x, least=1)), "")):
            bad.append(f"这一轮的行动正缺{gap}，不能当场拿出来")
        if count < MAX_BRING <= count + len(batch):
            bad.append(f"这一轮已经登记了 {MAX_BRING} 件东西")
        if bad:
            problems.append(f"{name}：{'；'.join(bad)}")
            ids.append("")
            continue
        ops.append(op)
        ids.append(ident)
        batch.append((where, name))
    return Brought(ops, problems, ids)


def reply_ops(w: World, npc: str, *, leave: str = "", items: Iterable[Any] = (), accompany: str = "",
              visibility: str = "auto", played: Iterable[str] = ()) -> tuple[list[list[dict[str, Any]]], list[str]]:
    """An NPC's on-the-spot acts in its reply (agents/cast.Dialogue.items, .leave), as groups of
    world ops for its own gateway (world/tools.Gateway), each group landing whole: a thing it takes
    out (bring_ops) and the handing of it over (Transfer), then its going off (ending the company it
    keeps, then Move). What cannot be put as ops at all, in words. What it does on meeting them
    (Dialogue.played) is done once played, and no longer put to it each time they talk; its way
    with them every time (Step.once False: clams up when asked about the graves) stays its way."""
    from .agents import meets

    groups: list[list[dict[str, Any]]] = []
    if (a := w.rec.agents.get(npc)) is not None:
        due = {s.id for s in a.plan if s.status == "pending" and s.once is not False and meets(w, npc, s)}
        groups += [[{"kind": "step", "npc": npc, "step": sid, "status": "done"}] for sid in dict.fromkeys(played) if sid in due]
    things = list(items)
    found = bring_ops(w, things, actor=npc, holder=npc, visibility=visibility)
    problems = list(found.problems)
    for thing, ident in zip(things, found.ids, strict=True):
        if not ident:
            continue
        group = [op.model_dump(mode="json", exclude={"id", "actor"}) for op in found.ops if op.item == ident]
        to = _person(w, _field(thing, "to")) if _field(thing, "to") else ""
        if to and to != npc:  # handed over, in the same breath
            group.append({"kind": "transfer", "item": ident, "to": to, "giver": npc, "visibility": visibility})
        if group:
            groups.append(group)
    if leave.strip():
        dest = _location(w, leave.strip())
        if not dest:
            problems.append(f"不知道“{leave}”是哪里：说完走开时，leave 填给出的地点 id")
        elif accompany:
            problems.append("同意了同行，就不能同时走开：两样只能选一样")
        elif dest != w.place(npc):
            end = [{"kind": "follow", "npc": npc, "visibility": visibility}] if w.rec.entities[npc].following else []
            groups.append([*end, {"kind": "move", "entity": npc, "to": dest, "visibility": visibility}])
    return groups, problems


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
