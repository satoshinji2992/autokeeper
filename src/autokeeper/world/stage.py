"""What is on stage: the scene in front of the investigators belongs to the keeper.

Offscreen the world lives on: important NPCs decide, arrangements come due, prepared plan steps
go ahead, and all of it lands in the record as it happens (world/scheduler.py). What would change
the scene in front of the investigators does not land behind their backs: someone walks in or
leaves, a companion turns away, someone acts on them or on what they carry, something happens
where they stand. It waits as a beat, an arrangement left unfinished for the keeper, until the
keeper plays it in the next telling (engine/game.py), and lands whole then, told in the same
breath: a reaction in place at once, but someone the investigators are dealing with does not walk
out mid-exchange unless the keeper explicitly cues it. A beat whose basis moved meanwhile (what its
decision rested on, its plan step's condition), or that the world no longer allows, is dropped,
and its author woken to think again.

Commitments to the investigators outrank what was arranged elsewhere: someone walking with them
leaves only by ending that in front of them (ops.Move refuses it otherwise), and an arrangement
coming due goes on without them (committed).

One rule for every backstage writer, instead of a case for each collision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..engine import world as legacy_world
from .ops import Break, Bring, Condition, Equip, Follow, Fulfil, Learn, MakePromise, Move, Occur, Transfer, parse
from .record import PLAYERS, Beat

if TYPE_CHECKING:
    from .store import World

KEPT = 30  # played and dropped beats kept for the developer record


def party(w: World) -> set[str]:
    return {i.id for i in w.st.investigators}


def on_stage(w: World, ident: str) -> bool:
    """In front of the investigators: one of them, someone or something where they are, or
    something one of those carries."""
    if not ident:
        return False
    if ident == PLAYERS or ident in party(w):
        return True
    e = w.rec.entities.get(ident)
    if e is not None:
        return ident == w.st.location if e.kind == "location" else w.place(ident) == w.st.location
    holder = legacy_world.owner(w.m, w.st, ident)  # an item outside the record
    return bool(holder) and holder != ident and on_stage(w, holder)


def committed(w: World, npc: str) -> str:
    """The NPC's standing commitment to the investigators, in words; empty when it is free."""
    e = w.rec.entities.get(npc)
    if e is None or not e.following:
        return ""
    return f"{e.name}正与{w.name(e.following)}同行" + (f"去{w.name(e.follow_to)}" if e.follow_to else "")


def touches(w: World, op: Any) -> list[str]:
    """Who and what on stage this op would change, judged before it lands; empty for backstage
    work (plans, memories, feelings, arrangements, rewrites)."""
    here, us = w.st.location, party(w)
    if isinstance(op, Move):
        return [op.entity] if w.place(op.entity) == here or op.to == here else []
    if isinstance(op, Follow):
        return [op.npc] if on_stage(w, op.npc) else []
    if isinstance(op, Transfer):
        before = legacy_world.owner(w.m, w.st, op.item)
        return [x for x in dict.fromkeys((before, op.to, op.item)) if x and on_stage(w, x)]
    if isinstance(op, Bring):
        return [x for x in (op.holder, op.place) if x and on_stage(w, x)]
    if isinstance(op, Occur):
        if (op.place or w.place(op.actor)) == here:
            return list(dict.fromkeys([op.actor, *op.participants]))
        return [x for x in op.participants if x in us]
    if isinstance(op, Learn):
        told = [x for x in op.who if x == PLAYERS or x in us]
        return [op.actor, *told] if told else []
    if isinstance(op, MakePromise):  # said in front of them, to them or to someone there
        return [op.by, op.to] if on_stage(w, op.by) or on_stage(w, op.to) else []
    if isinstance(op, Fulfil | Break):  # judged where the one settling it stands, not where the other party is
        return [op.actor] if on_stage(w, op.actor) else []
    if isinstance(op, Condition):
        return [op.entity] if on_stage(w, op.entity) else []
    if isinstance(op, Equip):
        return [op.item] if op.reveal and on_stage(w, op.item) else []
    return []


def leaves(w: World, op: Any) -> list[str]:
    """Who this op takes out of the scene: walking off, ending their company, dead or gone. Only
    such a beat waits for an exchange with that person to end; a reaction in place plays at once."""
    here = w.st.location
    if isinstance(op, Move):
        return [op.entity] if w.place(op.entity) == here and op.to != here else []
    if isinstance(op, Follow):
        return [op.npc] if not op.leader and on_stage(w, op.npc) else []
    if isinstance(op, Condition):
        return [op.entity] if op.status in ("死亡", "失踪") and on_stage(w, op.entity) else []
    return []


def queue(w: World, ident: str, actor: str, ops: list[dict[str, Any]], involves: list[str],
          leaving: list[str] | None = None, *, reads: dict[str, int] | None = None, when: str = "", text: str = "") -> Beat:
    """Keep work that touches the stage for the keeper, with what it rested on. The same work
    asked again is the same beat."""
    if (known := next((b for b in w.rec.beats if b.id == ident), None)) is not None:
        return known
    beat = Beat(id=ident, actor=actor, ops=ops, involves=list(dict.fromkeys(involves)),
                leaving=list(dict.fromkeys(leaving or [])), reads=basis(actor, reads), when=when, text=text, t=w.now,
                turn=w.st.turn)
    finished = [b for b in w.rec.beats if b.status != "waiting"]
    w.rec.beats = [*finished[-KEPT:], *(b for b in w.rec.beats if b.status == "waiting"), beat]
    return beat


def basis(actor: str, reads: dict[str, int] | None) -> dict[str, int]:
    """What a held decision rests on, without its author's own bookkeeping (mood, timer), which
    changes the moment the decision is taken."""
    return {ref: v for ref, v in (reads or {}).items() if ref != f"agent:{actor}"}


def gone_by(w: World, reads: dict[str, int], when: str) -> list[str]:
    """Why held work no longer fits, in words its author can act on: what it rested on moved, or
    its condition no longer holds."""
    from .agents import holds

    moved = [_moved(w, ref) for ref, v in reads.items() if w.version(ref) != v]
    return list(dict.fromkeys(moved)) + ([f"条件不再成立：{when}"] if when.strip() and not holds(w, when) else [])


def _moved(w: World, ref: str) -> str:
    kind, _, ident = ref.partition(":")
    if kind == "entity":
        return f"{w.name(ident)}的情况变了"
    return {"fact": "他知道的一件事变了", "relation": "和别人的关系变了", "promise": "一个约定变了"}.get(kind, "情况变了")


def waiting(w: World, actor: str | None = None) -> list[Beat]:
    return [b for b in w.rec.beats if b.status == "waiting" and (actor is None or b.actor == actor)]


def cue(w: World, ident: str, action: str, reason: str) -> None:
    """The keeper decides the timing in its ordinary plan; no new call or approval queue.
    A played/dropped receipt stays final when a saved plan is replayed."""
    beat = next((b for b in waiting(w) if b.id == ident), None)
    if beat is None:
        return
    beat.note = reason
    if action == "drop":
        beat.status = "dropped"
        if (agent := w.rec.agents.get(beat.actor)) is not None:
            agent.wake, agent.refused = w.now, f"守秘人撤回安排：{reason}"
    elif action in ("play", "hold"):
        beat.cue = action


def commit(w: World, actor: str, raw_ops: list[dict[str, Any]]) -> list[str]:
    """Land work whole, now: tried on a copy first (the actor's own permissions, the world's
    checks); what stops it when it no longer fits."""
    from . import agents
    from .store import World

    trial = World(w.m.model_copy(deep=True), w.st.model_copy(deep=True))
    trial.clock = w.clock
    problems: list[str] = []
    ops = []
    for raw in raw_ops:
        try:
            op = parse(raw)
        except ValueError as exc:
            problems.append(f"格式不对：{str(exc)[:80]}")
            break
        if actor in trial.rec.agents and (refused := agents.vet(trial, actor, op)):
            problems += refused
            break
        result = trial.apply(op)
        if not result.ok:
            problems += result.problems
            break
        ops.append(op)
    if not problems:  # no await between the trial and the commit: the same state both times
        for op in ops:
            result = w.apply(op)
            if not result.ok:
                problems += result.problems
                break
    return problems


def play(w: World, beat: Beat) -> list[str]:
    """Land a beat whole, now; what stops it when it no longer fits (it is dropped then, and
    its author woken to decide again): what it rested on moved, its condition no longer holds,
    or the world refuses it."""
    problems = gone_by(w, beat.reads, beat.when) or commit(w, beat.actor, beat.ops)
    beat.status = "dropped" if problems else "played"
    if problems:
        beat.note = "；".join(problems)[:200]
    if problems and (a := w.rec.agents.get(beat.actor)) is not None:
        # What it meant to do no longer fits: it thinks again, knowing why.
        a.wake, a.refused = w.now, f"{beat.text or '原先的安排'}没做成：{'；'.join(problems)}"[:200]
    return problems
