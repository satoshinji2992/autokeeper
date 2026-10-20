"""Turning points wait for the director (the user's call, 2026-10-20: important NPCs act on their own
so the story can surprise, but the director and the keeper keep it in hand).

Backstage most of what important NPCs do lands as it happens (world/scheduler.py), and what
would change the scene in front of the investigators waits for the keeper (world/stage.py). What
would change where the story can go waits for the director instead, while a screen has it
looking (World.reviewing): someone dies or vanishes, an important thing changes hands, a story
flag that starts an event or an ending comes about, a secret reaches someone new, a promise to
the investigators is broken. After the turn is told the director looks at them together
(engine/game.Game._review): lets most go ahead, puts one off a while, and rarely turns one down,
saying why to the one who meant it. The same look is its chance to act on its own: a rewrite, a
plan, an arrangement, checked as its plans are.

Without a director looking (autoplay, replays) nothing is held: it all lands as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .ops import Break, Condition, Learn, SetFlag, Transfer
from .record import Proposal

if TYPE_CHECKING:
    from .store import World

KEPT = 30  # decided proposals kept for the developer record
SECRET = ("secret", "truth", "conclusion", "clue", "lie")


def significant(w: World, op: Any) -> list[str]:
    """What makes this op a turning point, judged before it lands; empty for the rest."""
    if isinstance(op, Condition) and op.status in ("死亡", "失踪") and op.entity in w.rec.entities:
        return [f"{w.name(op.entity)}{'死了' if op.status == '死亡' else '不见了'}"]
    if isinstance(op, Transfer) and (item := w.rec.entities.get(op.item)) is not None and item.important:
        return [f"{item.name}到了{w.name(op.to)}手里"]
    if isinstance(op, SetFlag) and op.on and op.flag not in w.st.flags:
        hangs = [e.title for e in w.m.events if e.when == f"flag:{op.flag}"]
        hangs += [f"结局「{e.title}」" for e in w.m.endings if e.condition == f"flag:{op.flag}"]
        return [f"立起标记 {op.flag}，引出{'、'.join(hangs)}"] if hangs else []
    if isinstance(op, Learn) and (f := w.rec.facts.get(op.fact)) is not None and f.kind in SECRET:
        new = [w.name(x) for x in op.who if x in w.rec.entities and x not in f.known_by]
        return [f"{'、'.join(new)}得知了「{f.text[:30]}」"] if new else []
    if isinstance(op, Break) and (p := w.rec.promises.get(op.promise)) is not None:
        party = {i.id for i in w.st.investigators}
        return [f"{w.name(p.by)}违背了对{w.name(p.to)}的约定"] if party & {p.by, p.to} else []
    return []


def hold(w: World, ident: str, actor: str, ops: list[dict[str, Any]], why: list[str], text: str = "") -> Proposal:
    """Keep a turning point for the director. The same work asked again is the same proposal."""
    if (known := next((p for p in w.rec.proposals if p.id == ident), None)) is not None:
        return known
    proposal = Proposal(id=ident, actor=actor, ops=ops, why=list(dict.fromkeys(why)), text=text, t=w.now, turn=w.st.turn)
    finished = [p for p in w.rec.proposals if p.status in ("landed", "dropped")]
    open_ = [p for p in w.rec.proposals if p.status not in ("landed", "dropped")]
    w.rec.proposals = [*finished[-KEPT:], *open_, proposal]
    return proposal


def waiting(w: World, actor: str | None = None) -> list[Proposal]:
    """Those the director has not looked at yet, or put off: their author does not act meanwhile."""
    return [p for p in w.rec.proposals if p.status in ("waiting", "deferred") and (actor is None or p.actor == actor)]


def let_through(w: World, proposal: Proposal) -> list[str]:
    """It goes ahead: on stage if it now touches the investigators' scene (the keeper plays it),
    else it lands. What stops it when it no longer fits."""
    from . import stage
    from .ops import parse

    try:
        ops = [parse(raw) for raw in proposal.ops]
    except ValueError as exc:
        problems = [f"格式不对：{str(exc)[:80]}"]
    else:
        involved = [x for op in ops for x in stage.touches(w, op)]
        if involved:
            leaving = [x for op in ops for x in stage.leaves(w, op)]
            stage.queue(w, proposal.id, proposal.actor, proposal.ops, involved, leaving)
            problems = []
        else:
            problems = stage.commit(w, proposal.actor, proposal.ops)
    proposal.status = "dropped" if problems else "landed"
    proposal.note = proposal.note or "；".join(problems)[:200]
    if problems and (a := w.rec.agents.get(proposal.actor)) is not None:
        a.wake = w.now
    return problems


def put_off(w: World, proposal: Proposal, minutes: int, why: str = "") -> None:
    proposal.status, proposal.until, proposal.note = "deferred", w.now + max(10, min(minutes, 1440)), why


def turn_down(w: World, proposal: Proposal, why: str) -> None:
    """Dropped; the one who meant it remembers why, and thinks again in an hour."""
    from .ops import Remember

    proposal.status, proposal.note = "dropped", why
    if (a := w.rec.agents.get(proposal.actor)) is not None:
        if why.strip():
            w.apply(Remember(id=f"review:{proposal.id}", npc=proposal.actor, text=why.strip()[:200], actor="director"))
        a.wake = w.now + 60


def due(w: World) -> list[Proposal]:
    """Put off until now: they go ahead without asking again."""
    return [p for p in w.rec.proposals if p.status == "deferred" and p.until is not None and p.until <= w.now]
