"""NPC identity, prepared intentions, and permitted actions.

Persistent goals and memory are data for the keeper and the character voice, not independent
model workers. Fixed plans use scheduler.py; open decisions belong to the keeper.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from ..engine import world as legacy_world
from ..engine.state import hours
from . import conditions, knowledge
from .ops import (
    AdvancePlan,
    Break,
    Bring,
    Condition,
    Enlist,
    Equip,
    Follow,
    Fulfil,
    Learn,
    MakePromise,
    Move,
    Occur,
    Relate,
    Remember,
    Schedule,
    SetFlag,
    SetPlan,
    Transfer,
)
from .record import PLAYERS, Step

if TYPE_CHECKING:
    from .store import World

NpcOp = Annotated[
    Move | Follow | Relate | Transfer | Equip | Learn | MakePromise | Fulfil | Break | Occur | Remember | Condition | AdvancePlan | SetPlan | Schedule,
    Field(discriminator="kind"),
]
# Character voice operations through its own gateway, including taking out an everyday item.
ReplyOp = Annotated[
    Move | Follow | Relate | Transfer | Bring | Equip | Learn | MakePromise | Fulfil | Break | Occur | Remember | Condition | AdvancePlan
    | SetPlan | Schedule | Enlist,
    Field(discriminator="kind"),
]


HOUR = re.compile(r"D\d+ \d{1,2}:\d{2}")


def split_when(when: str) -> tuple[str, str]:
    """A module step's when as its two halves: the hour it waits for and what must hold as well,
    joined by & (「D1 22:00 & flag:bell_stopped」: at ten, and only once the bell has stopped).
    A plan step keeps them apart (Step.due, Step.when). An hour inside an |, or two hours, is no
    hour to wait for: it stays in the condition, where graph.audit reports it."""
    when = when.strip()
    parts = [x.strip() for x in when.split("&") if x.strip()]
    hours = [x for x in parts if HOUR.fullmatch(x)]
    if "|" in when or len(hours) != 1:
        return ("", when) if not HOUR.fullmatch(when) else (when, "")
    return hours[0], " & ".join(x for x in parts if x != hours[0])


def marked(w: World) -> list[Any]:
    """What the module sets going: the NPCs it marks important (Enlist), what they mean to do
    (its agendas: Enlist with their goals, then SetPlan; one it does not mark important supplies
    fixed steps without open-goal attention, Agent.passive), and its foreshadowing (threads).
    Fixed ids, so a loaded game repeats none of it."""
    from ..engine.state import parse_time
    from .ops import OpenThread

    ops: list[Any] = [Enlist(id=f"enlist:{n.id}", npc=n.id, actor="director") for n in w.m.npcs if n.important and n.id not in w.rec.agents]
    npcs = {n.id for n in w.m.npcs}
    for a in w.m.agendas:
        if a.npc not in npcs:
            continue
        ops.append(Enlist(id=f"agenda:{a.npc}", npc=a.npc, goals=list(a.goals), actor="director"))
        if not w.m.npc(a.npc).important:  # fixed material, without open-goal attention
            ops.append(Enlist(id=f"agenda:{a.npc}:passive", npc=a.npc, passive=True, actor="director"))
        steps = []
        for move in a.steps:
            if move.ruling:  # what comes of the keeper's ruling is the keeper's (context.keeper_brief), not the NPC's to do
                continue
            hour, wait = split_when(move.when)  # 「D1 22:00 & flag:x」: from ten on, once x holds
            # What the module says it brings about, done with no model call when its time or
            # condition comes; a step for "whenever" (no when), or with grounds the condition cannot
            # say (only_if), is the NPC's own to judge, the outcome then what must hold once done.
            prepared, achieved = effects(a.npc, move.does) if move.when.strip() and not move.meet else ([], "")
            if move.only_if.strip():
                prepared = []
            steps.append(Step(id=move.id, text=step_text(move), due=parse_time(hour) if hour else None,
                              when=wait, ops=prepared, achieved=achieved, meet=move.meet,
                              once=move.once if move.meet else None))
        if steps:
            ops.append(SetPlan(id=f"agenda:{a.npc}:plan", npc=a.npc, steps=steps, actor="director"))
    places = {loc.id for loc in w.m.locations}
    ops += [OpenThread(id=f"foreshadow:{f.id}", thread=f"foreshadow:{f.id}", thread_kind="foreshadow", title=f.hint[:40],
                       entities=[f.where] if f.where in places else [], actor="director") for f in w.m.foreshadow]  # fmt: skip
    # The past (Module.history): private events from the start, remembered by those in them and
    # those who know; the director may rewrite them like any private event. They wake no one.
    for i, past in enumerate(w.m.history):
        who = [x for x in past.who if x in npcs]
        ops.append(Occur(id=f"past:{i}", text=f"（{past.when}）{past.text}", participants=who,
                         place=past.where if past.where in places else "", visibility="private", reactive=False,
                         actor="engine", witnesses=[x for x in past.known_by if x in npcs and x not in who]))
    return ops


def step_text(move: Any) -> str:
    """An agenda step as its NPC reads it: what it does, and what must really have happened first
    when the condition language cannot say it (Move.only_if): the drink in his hand, not in the
    investigators' bag; told by someone, not known to the players."""
    only = move.only_if.strip().rstrip("。")
    return f"{move.text.strip()}（只在{only}时才这样）" if only else move.text


def effects(npc: str, does: str) -> tuple[list[dict[str, Any]], str]:
    """An agenda step's outcome (Move.does) as the ops that bring it about (it goes somewhere,
    hands something over, sets a story flag, someone is gone), and the condition that then holds
    (Step.achieved). Only "|"-free outcomes count; the world still checks each op."""
    ops: list[dict[str, Any]] = []
    done: list[str] = []
    if "|" in does:
        return ops, ""
    for kind, rest, negate in condition_atoms(does):
        a, _, b = rest.partition(":")
        if negate:
            continue
        if kind == "at" and a == npc and b:
            ops.append({"kind": "move", "entity": npc, "to": b})
        elif kind == "owner" and a and b:
            ops.append({"kind": "transfer", "item": a, "to": b})
        elif kind == "flag" and rest:
            ops.append({"kind": "flag", "flag": rest})
        elif kind == "gone" and rest:
            ops.append({"kind": "condition", "entity": rest, "status": "失踪" if rest == npc else "死亡"})
        else:
            continue
        done.append(f"{kind}:{rest}")
    return ops, " & ".join(done)


# What a step without an outcome or a condition is about, when nothing says: meeting the
# investigators (coming to the door, being asked, taking them to see something).
# Kept narrow: a backstage step taken for a meeting would never be acted on.
MEETING = re.compile(r"(等|见到|遇到|碰到|见着).{0,8}(调查员|玩家|客人|来人|访客)|来访|上门|来问|问起|问到|被问|有人来|"
                     r"招呼|寒暄|应门|请.{0,4}(吃|喝|坐|进)|(领|带).{0,2}(调查员|玩家|客人|访客)")


def meeting(w: World, npc: str, step: Step) -> bool:
    """Whether a plan step is what the NPC does on meeting the investigators: character material
    for its voice, never a backstage decision (its time or condition only says from when on).
    Its own label first, then the module's agenda step of the same id, then, for a step with no time, condition or outcome, what its words are about. One
    with an outcome is always backstage work."""
    if step.completion == "report" or step.ops or step.achieved.strip():
        return False
    if step.meet is not None:
        return step.meet
    move = next((s for a in w.m.agendas if a.npc == npc for s in a.steps if s.id == step.id), None)
    if move is not None and move.meet is not None:
        return move.meet
    return step.due is None and not step.when.strip() and bool(MEETING.search(step.text))


def meets(w: World, npc: str, step: Step) -> bool:
    """A meeting step whose time has come and whose condition holds: its voice plays it now."""
    return meeting(w, npc, step) and (step.due is None or step.due <= w.now) and holds(w, step.when)


def sanctioned(w: World, npc: str) -> set[str]:
    """Story flags the module's own agenda for this NPC brings about (Move.does): it may set those."""
    return {rest for a in w.m.agendas if a.npc == npc for move in a.steps
            for kind, rest, negate in condition_atoms(move.does) if kind == "flag" and not negate}


# -- what an NPC can reach ---------------------------------------------------------------------
def places(w: World, npc: str) -> set[str]:
    """Places it knows: the public ones, where it is and next door, its home in the module, the
    ones its facts or the director gave it."""
    out = {loc.id for loc in w.m.locations if loc.public}
    here = w.place(npc)
    if here:
        out.add(here)
        out |= set(next((loc.exits for loc in w.m.locations if loc.id == here), []))
    if (n := next((x for x in w.m.npcs if x.id == npc), None)) and n.location:
        out.add(n.location)
    if a := w.agent(npc):
        out |= set(a.places)
        out |= {rest.partition(":")[2] for s in a.plan for kind, rest, _ in condition_atoms(f"{s.when} & {s.achieved}".strip(" &"))
                if kind == "at"}  # where its plan waits for someone, or takes it
    locations = {e.id for e in w.rec.entities.values() if e.kind == "location"}
    out |= {x for f in knowledge.known(w.rec, npc) for x in f.about if x in locations}
    return out


def _beside(w: World, npc: str, other: str) -> bool:
    return other == npc or (other == PLAYERS and w.place(npc) == w.st.location) or (w.place(other) == w.place(npc) != "")


def vet(w: World, npc: str, op: Any) -> list[str]:
    """Check identity, reach and explicit fact references, never infer secrets from prose.

    An NPC decides whether to share its own knowledge from its character context. Learn.check
    and Occur's checks below still reject facts it does not know; text similarity cannot decide
    whether a confession, a lie or an everyday action is appropriate.
    """
    me, name = npc, w.name(npc)
    if isinstance(op, Move):
        if op.entity != me:
            return [f"{name}只能决定自己去哪"]
        return [] if op.to in places(w, me) else [f"{name}不知道 {op.to} 这个地方"]
    if isinstance(op, Follow):
        if op.npc != me:
            return [f"{name}只能决定自己是否同行"]
        return [] if not op.destination or op.destination in places(w, me) else [f"{name}不知道同行目的地"]
    if isinstance(op, Relate):
        return [] if op.a == me else [f"{name}只能改变自己对别人的看法"]
    if isinstance(op, Equip):
        return [] if legacy_world.owner(w.m, w.st, op.item) == me else [f"{name}只能整理自己的装备"]
    if isinstance(op, Transfer):
        holder = legacy_world.owner(w.m, w.st, op.item)
        if holder == me:
            return [] if _beside(w, me, op.to) else [f"{op.to} 不在{name}身边"]  # a place: where it is, to put it down
        if op.to == me and holder == w.place(me):
            return []
        return [f"{op.item} 不在{name}手里，也不在他身边"]
    if isinstance(op, Bring):  # what it has on it, or puts down where it stands; handing it over is a transfer
        if op.holder and op.holder != me:
            return [f"{name}只能拿出自己身上的东西"]
        return [] if not op.place or op.place == w.place(me) else [f"{name}不在 {op.place}"]
    if isinstance(op, Learn):
        return [f"{name}身边没有 {x}" for x in op.who if not _beside(w, me, x)]
    if isinstance(op, MakePromise):
        if op.by != me:
            return [f"{name}只能替自己答应"]
        return [] if _beside(w, me, op.to) else [f"{op.to} 不在{name}身边"]
    if isinstance(op, Occur):
        problems = [] if not op.place or op.place == w.place(me) else [f"{name}不在 {op.place}"]
        if not op.reactive:
            problems.append("不能替其他人物关闭对这件事的反应")
        problems += [f"{x} 不在{name}身边" for x in op.participants if not _beside(w, me, x)]
        sees = {f.id for f in knowledge.known(w.rec, me)}
        return problems + [f"{name}不知道事实 {f}" for f in op.facts if f not in sees]
    if isinstance(op, Condition):
        return [] if _beside(w, me, op.entity) else [f"{op.entity} 不在{name}身边"]
    if isinstance(op, Remember | AdvancePlan | SetPlan):
        return [] if op.npc == me else [f"{name}只能管自己的记忆和计划"]
    if isinstance(op, Enlist):
        return [] if op.npc == me and not op.resources and not op.places else [f"{name}只能登记自己的打算，不能借此获得资源或地点知识"]
    if isinstance(op, Schedule):
        return [] if op.participants == [] or me in op.participants else [f"{name}只能安排自己要做的事"]
    if isinstance(op, Fulfil | Break):
        return []  # the op itself checks that it is a party
    if isinstance(op, SetFlag):
        return [] if op.flag in sanctioned(w, me) else [f"{name}不能改剧情标记 {op.flag}"]
    return [f"{name}不能做 {getattr(op, 'kind', '?')}"]


# -- conditions on plan steps -------------------------------------------------------------------
def condition_atoms(when: str) -> list[tuple[str, str, bool]]:
    """The small condition language has AND precedence over OR, and no parentheses."""
    return [(part.lstrip("!").partition(":")[0], part.lstrip("!").partition(":")[2], part.startswith("!"))
            for group in when.split("|") for raw in group.split("&") if (part := raw.strip())]


def condition_problems(w: World, when: str) -> list[str]:
    if not when.strip():
        return []
    problems = []
    who = set(w.rec.entities) | {i.id for i in w.st.investigators} | {PLAYERS}
    if any(not p.strip() for group in when.split("|") for p in group.split("&")):
        problems.append("条件含有空的分支")
    for kind, rest, _ in condition_atoms(when):
        a, sep, b = rest.partition(":")
        if kind == "flag":
            valid = rest in w.m.flags or rest in w.st.flags
        elif kind == "at":
            valid = a in who and bool(sep) and (e := w.rec.entities.get(b)) is not None and e.kind == "location"
        elif kind == "promise":
            valid = a in w.rec.promises and b in {"active", "fulfilled", "broken"}
        elif kind == "owner":
            valid = (e := w.rec.entities.get(a)) is not None and e.kind == "item" and b in who
        elif kind == "knows":
            valid = a in who and b in w.rec.facts
        elif kind == "after":
            valid = rest.isdecimal()
        elif kind == "gone":
            valid = (e := w.rec.entities.get(rest)) is not None and e.kind == "npc"
        elif kind == "hour":
            valid = hours(rest) is not None
        else:
            valid = False
        if not valid or any(c in rest for c in "()!"):
            problems.append(f"条件不存在或格式不正确：{kind}:{rest}")
    return problems


def holds(w: World, when: str) -> bool:
    """Three-valued conditions at the scene time: an unknown atom never becomes true by negation."""
    if condition_problems(w, when):
        return False

    def atom(kind: str, rest: str, negate: bool) -> bool | None:
        result = conditions.test(w, kind, rest)
        return None if result is None else result != negate

    # Only a fully true AND branch establishes the condition. False and unknown both
    # fail that branch; another explicitly true OR branch can still establish it.
    return any(all(atom(*part) is True for part in condition_atoms(group)) for group in when.split("|"))
