"""The deterministic check of what the keeper plan writes about an encounter.

`turn` checks an EncounterTurnPlan (the player's declared action, the arrangements of the keeper's
people, who joins, filled numbers, stakes, an end, the pace); `start` an EncounterStart; `pursuit`
a pursuit decision; `react` a reaction in a player's own words. Each gives problems in Chinese,
naming ids, which go back to the model once (agents/cast.Cast._checked). What is still wrong after
that does not land: `land` keeps the entries that hold and lists the rest for the keeper's report
as 【未落实】 (the user's call, 2026-10-21: nothing is put in their place). An NPC whose arrangement
did not land takes no action that round; its defence uses its sheet.

Perception (the keeper-craft review, guide #25): the keeper knows everything, an NPC only what it
sees, hears or was told; an arrangement aimed at someone it cannot perceive, or a pursuit of
someone it lost, is refused with the reason.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...module import graph as module_graph
from . import actions, profiles
from .actions import Ctx
from .choices import needs_arrangement
from .plan import DeclaredAction, EncounterStart, EncounterTurnPlan, NpcArrangement

if TYPE_CHECKING:
    pass


@dataclass
class Landed:
    """What of a plan holds: the parts to land, and why the rest does not."""

    action: DeclaredAction | None = None
    action_problems: list[str] = field(default_factory=list)
    npcs: list[NpcArrangement] = field(default_factory=list)
    join: list[str] = field(default_factory=list)
    fills: list[Any] = field(default_factory=list)
    stakes: list[Any] = field(default_factory=list)
    end: Any = None
    pace: int | None = None
    cover: list[Any] = field(default_factory=list)
    sight: list[Any] = field(default_factory=list)
    distances: list[Any] = field(default_factory=list)
    unsettled: list[str] = field(default_factory=list)  # 【未落实】 lines for the keeper


def _arrangement(ctx: Ctx, a: NpcArrangement, joining: set[str]) -> tuple[NpcArrangement | None, list[str]]:
    p = ctx.p(a.who)
    if p is None and a.who not in joining:
        return None, [f"{a.who} 不在这场遭遇里"]
    if p is not None and p.controller != "keeper":
        return None, [f"{a.who} 不是守秘人控制的人，不用安排"]
    if p is not None and p.presence != "in":
        return None, [f"{a.who} 已经不在现场，不用安排"]
    problems: list[str] = []
    kept = []
    for i, d in enumerate(a.actions, 1):
        found = actions.preconditions(ctx, a.who, d) if p is not None else []
        if d.do == "pursue" and ctx.enc.chase is None:
            found += _pursue(ctx, a.who, [d.target])
        if found:
            problems += [f"{a.who} 的第 {i} 个行动（{d.do}）：{x}" for x in found]
        else:
            kept.append(d)
    problems += _pursue(ctx, a.who, a.pursue, ahead=True)
    for x in a.let_go:
        q = ctx.p(x)
        if q is None or q.presence not in ("fled", "in"):
            problems.append(f"{a.who} 的 let_go 里 {x} 不在这场遭遇里")
    release = []
    for x in a.release:
        if ctx.held_by(x) != a.who:
            problems.append(f"{a.who} 的 release 里 {x} 不是他正在压制的人")
        else:
            release.append(x)
    if not kept:
        return None, problems or [f"{a.who} 没有一个做得到的行动"]
    return a.model_copy(update={"actions": kept, "release": release,
                                "pursue": [x for x in a.pursue if not _pursue(ctx, a.who, [x], ahead=True)]}), problems


def _pursue(ctx: Ctx, who: str, ids: list[str], *, ahead: bool = False) -> list[str]:
    """Why `who` cannot go after `ids`. `ahead`: said in advance in a fight, for whoever may run (a
    pursuit of someone still fighting holds, should they flee)."""
    out = []
    ch = ctx.enc.chase
    for x in ids:
        q = ctx.p(x)
        if ch is not None:  # a chase: those still running, as far as he can tell where they are
            if q is None or x not in ch.quarry or q.presence != "in":
                out.append(f"{who} 要追的 {x} 不是还在跑的人（在逃的：{'、'.join(ch.quarry) or '无'}）")
            elif why := ctx.hidden_from(who, x):
                out.append(f"{who} 追不了 {x}：{why}")
        elif q is None or q.presence not in (("fled", "in") if ahead else ("fled",)):
            out.append(f"{who} 要追的 {x} 没有在逃")
        elif not ctx.opposed(who, x):
            out.append(f"{who} 和 {x} 是一边的，不用追")
        elif why := ctx.hidden_from(who, x):
            out.append(f"{who} 追不了 {x}：{why}")
    return out


def undecided(ctx: Ctx, plan_npcs: list[NpcArrangement]) -> list[str]:
    """Keeper opponents who must say whether they chase someone who fled, and have not (of those they
    can perceive: someone gone from their sight is no one to decide about)."""
    fled = [p.id for p in ctx.enc.participants if p.presence == "fled"]
    out = []
    for p in ctx.enc.participants:
        if p.controller != "keeper" or not ctx.acting(p.id):
            continue
        mine = next((a for a in plan_npcs if a.who == p.id), None) or p.arrangement
        said = set(mine.pursue) | set(mine.let_go) if mine is not None else set()
        missing = [x for x in fled if ctx.opposed(p.id, x) and x not in said and not ctx.hidden_from(p.id, x)]
        if missing:
            out.append(p.id)
    return out


def land(game: Any, plan: EncounterTurnPlan, actor: str = "", *, act: bool = True) -> Landed:
    """What of `plan` holds now, and what does not (in words)."""
    ctx = Ctx.of(game)
    out = Landed()
    m = game.m
    joining = set()
    for x in plan.join:
        npc = next((n for n in m.npcs if n.id == x), None)
        if npc is None:
            out.unsettled.append(f"join：{x} 不是模组里的 NPC")
        elif ctx.p(x) is not None:
            out.unsettled.append(f"join：{x} 已经在这场遭遇里")
        elif game.world.place(x) != ctx.enc.place or npc.status in ("死亡", "失踪"):
            out.unsettled.append(f"join：{x} 不在现场")
        else:
            joining.add(x)
            out.join.append(x)
    if act:
        if plan.action is None:
            out.action_problems.append("action 必填：玩家这一轮要做的事")
        else:
            takes = [t.name for t in plan.action.takes]
            out.action_problems += actions.preconditions(ctx, actor, plan.action, takes=takes)
            if not plan.action.summary.strip():
                out.action_problems.append("action.summary 要写一句中立的话")
            if not out.action_problems:
                out.action = plan.action
    seen = set()
    for a in plan.npcs:
        if a.who in seen:
            out.unsettled.append(f"{a.who} 安排了不止一次")
            continue
        seen.add(a.who)
        kept, problems = _arrangement(ctx, a, joining)
        out.unsettled += problems
        if kept is not None:
            out.npcs.append(kept)
    arranged = {a.who for a in plan.npcs}
    missing = [p.id for p in ctx.enc.participants if needs_arrangement(ctx.enc, p) and ctx.acting(p.id) and p.id not in arranged]
    missing += [x for x in joining if x not in arranged]
    if missing:
        out.unsettled.append("缺少这些人的安排：" + "、".join(missing))
    if who := undecided(ctx, out.npcs):
        out.unsettled.append("有人逃跑了，这些人必须在 pursue 或 let_go 里写明追不追：" + "、".join(who))
    for f in plan.fills:
        if ctx.p(f.who) is None and f.who not in joining:
            out.unsettled.append(f"fills 里 {f.who} 不在这场遭遇里")
        elif f.key in ("生命", "HP") and f.value <= 0:
            out.unsettled.append(f"fills 里 {f.who} 的『生命』必须是正整数")
        else:
            out.fills.append(f)
    for s in plan.stakes:
        problems = module_graph.stake_problems(m, s.when) + [f"标记 {x} 没有声明" for x in s.flags if x not in m.flags]
        if problems:
            out.unsettled += [f"stakes：{x}" for x in problems]
        else:
            out.stakes.append(s)
    if plan.end is not None:
        wrong = [x for x in plan.end.who if ctx.p(x) is None]
        if wrong:
            out.unsettled.append(f"end 里 {'、'.join(wrong)} 不在这场遭遇里")
        elif ctx.enc.pending is not None:
            out.unsettled.append("还在等玩家的选择，现在不能收场")
        else:
            out.end = plan.end
    for c in plan.cover:
        if ctx.p(c.who) is None:
            out.unsettled.append(f"cover 里 {c.who} 不在这场遭遇里")
        else:
            out.cover.append(c)
    for x in plan.sight:
        wrong = [y for y in (x.watcher, x.who) if ctx.p(y) is None]
        if wrong:
            out.unsettled.append(f"sight 里 {'、'.join(wrong)} 不在这场遭遇里")
        else:
            out.sight.append(x)
    for d in plan.distances:
        wrong = [x for x in (d.a, d.b) if ctx.p(x) is None]
        if wrong:
            out.unsettled.append(f"distances 里 {'、'.join(wrong)} 不在这场遭遇里")
        else:
            out.distances.append(d)
    if plan.pace is None and ctx.enc.clock.pace is None:
        out.unsettled.append("pace 必填：一轮在故事里过几秒")
    out.pace = plan.pace
    return out


def turn(game: Any, actor: str = "", *, act: bool = True) -> Callable[[EncounterTurnPlan], list[str]]:
    """The check Cast.plan sends a turn plan back with."""

    def check(plan: EncounterTurnPlan) -> list[str]:
        found = land(game, plan, actor, act=act)
        return [*found.action_problems, *found.unsettled]

    return check


def opening(game: Any, s: EncounterStart, actor: str = "") -> list[str]:
    """What the investigator's first action as a start writes it (EncounterStart.opening) lacks: the
    preconditions of a declaration in the fight it begins (actions.preconditions), against those it
    names; a weapon the investigator does not hold among them (the review of 2026-10-22: an opening
    with `colt_revolver`, an id nobody had, went through and stopped the first round)."""
    from .model import EncounterState, Participant

    d = combatants(game, s.opening)
    if d is None:
        return []
    st = game.state
    actor = actor or st.investigator.id
    parts = [Participant(id=i.id, kind="investigator", side="party", controller="human", node=st.location)
             for i in st.investigators if i.id not in st.apart or i.id == actor]
    for x, side in [*((f, "foe") for f in s.foes), *((a, "party") for a in s.allies)]:
        prof = profiles.profile(game.world, x)
        if prof is not None and all(q.id != prof.id for q in parts):
            parts.append(Participant(id=prof.id, kind="creature" if prof.kind == "creature" else "npc", side=side,  # type: ignore[arg-type]
                                     controller="keeper", node=st.location))
    ctx = Ctx.of(game, EncounterState(id="opening", mode=s.mode, place=st.location, started_turn=st.turn, participants=parts))
    return [f"opening：{x}" for x in actions.preconditions(ctx, actor, d, takes=[t.name for t in d.takes])]


def combatants(game: Any, d: DeclaredAction | None) -> DeclaredAction | None:
    """A declaration with whoever it names by a creature form's id named as who fights in it
    (profiles.combatant: erie_entity is erie)."""
    if d is None:
        return None
    w = game.world
    picks = [p.model_copy(update={"target": profiles.combatant(w, p.target)}) for p in d.attacks]
    return d.model_copy(update={"attacks": picks, "target": profiles.combatant(w, d.target) if d.target else ""})


def start(game: Any, candidates: set[str]) -> Callable[[EncounterStart], list[str]]:
    """The check of how an encounter begins: who is in it, its stakes, its pace, the investigator's
    first action."""

    def check(s: EncounterStart) -> list[str]:
        out = opening(game, s)
        for x in [*s.foes, *s.allies]:
            if x not in candidates:
                out.append(f"{x} 不在【可能卷入的人】里")
        if set(s.foes) & set(s.allies):
            out.append("同一个人不能既是对手又是同伴")
        for a in s.npcs:
            if a.who not in [*s.foes, *s.allies]:
                out.append(f"npcs 里 {a.who} 不在 foes 或 allies 里")
        for st in s.stakes:
            out += [f"stakes：{x}" for x in module_graph.stake_problems(game.m, st.when)]
            out += [f"stakes：标记 {x} 没有声明" for x in st.flags if x not in game.m.flags]
        if s.pace is None:
            out.append("pace 必填：一轮在故事里过几秒")
        if s.mode == "chase" and not s.quarry:
            out.append("追逐要写 quarry：谁在逃")
        if s.site is not None:
            from .site import site_problems

            out += site_problems(s.site)
        return out

    return check


def pursuit(game: Any, deciders: list[str]) -> Callable[[Any], list[str]]:
    ctx = Ctx.of(game)
    fled = [p.id for p in ctx.enc.participants if p.presence == "fled"]

    def check(answer) -> list[str]:
        out = []
        given = {d.who: d for d in answer.decisions}
        for who in deciders:
            d = given.get(who)
            if d is None:
                out.append(f"缺少 {who} 的决定")
                continue
            for x in fled:
                if ctx.opposed(who, x) and x not in d.pursue and x not in d.let_go and not ctx.hidden_from(who, x):
                    out.append(f"{who} 没写追不追 {x}")
            out += _pursue(ctx, who, d.pursue)
        out += [f"{w} 不用决定" for w in given if w not in deciders]
        return out

    return check


def react(options: list[str]) -> Callable[[Any], list[str]]:
    def check(answer) -> list[str]:
        return [] if answer.reaction in options else [f"{answer.reaction} 不能用来应对这一下；只能选：{'、'.join(options)}"]

    return check


__all__ = ["Landed", "land", "pursuit", "react", "start", "turn", "undecided"]
