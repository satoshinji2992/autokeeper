"""Game time drives the world: when time passes, scheduled things happen and important NPCs go on.

advance(world, brain, start, end) simulates the minutes between in chunks. In each chunk:

    1. scheduled events that came due land (dropped, and their author woken, if what they read
       has changed since);
    2. promises whose time came are noted, and the important NPCs in them wake;
    3. each offscreen important NPC: a due plan step whose condition holds lands its prepared
       ops with no model call while the plan is fresh; one without ops, or a stale plan, wakes
       the NPC; a step whose condition stays unmet past its grace is blocked, and wakes it;
       events it took part in or saw since it last decided wake it; so does its own timer;
    4. the woken are ranked (promise, blocked, due, affected, timer) and grouped by place; each
       group is one decision call (an offscreen scene) while the budget lasts, the rest wait.

NPCs where the party is are onscreen: they act through the scene, not here. A tick id (the
span simulated) is kept in the record, so a retried turn never simulates the same span twice;
every op gets a deterministic id for the same reason.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import agents, context
from .ops import AdvancePlan, Occur, SetPlan, parse
from .record import Agent

if TYPE_CHECKING:
    from .store import World

RANK = {"promise": 0, "blocked": 1, "stale": 1, "due": 2, "affected": 3, "timer": 4}


@dataclass
class Budget:
    turn: int = 2  # decisions in a player turn
    skip: int = 4  # decisions in a time skip (travel, a long rest, leaving for the night)
    long: int = 120  # minutes from which an advance counts as a skip
    chunk: int = 240  # minutes simulated at a time
    chunks: int = 12  # at most; a longer skip gets longer chunks
    grace: int = 120  # minutes a due step may wait for its condition before it is blocked


@dataclass
class Report:
    tick: str
    repeat: bool = False
    calls: int = 0
    chunks: int = 0
    woke: list[str] = field(default_factory=list)  # npc: reason, of those who decided
    deferred: list[str] = field(default_factory=list)  # woken, but the budget ran out
    landed: list[str] = field(default_factory=list)  # op ids
    refused: list[str] = field(default_factory=list)  # what an NPC tried and could not do
    blocked: list[str] = field(default_factory=list)  # npc:step
    fired: list[str] = field(default_factory=list)  # scheduled ids
    dropped: list[str] = field(default_factory=list)  # scheduled ids gone stale


def fresh(w: World, reads: dict[str, int]) -> list[str]:
    """The refs whose version moved since they were read; empty when all still hold."""
    return [ref for ref, v in reads.items() if w.version(ref) != v]


def _land(w: World, ops: list[dict[str, Any]], *, prefix: str, actor: str, report: Report) -> None:
    for i, raw in enumerate(ops):
        try:
            op = parse({"actor": actor, **raw, "id": f"{prefix}:{i}"})
        except ValueError as exc:
            report.refused.append(f"{prefix}:{i} 格式不对：{str(exc)[:80]}")
            continue
        if actor in w.rec.agents and (problems := agents.vet(w, actor, op)):
            report.refused += [f"{prefix}:{i} {p}" for p in problems]
            continue
        r = w.apply(op)
        (report.landed.append(r.op) if r.ok else report.refused.extend(f"{r.op} {p}" for p in r.problems))


async def advance(w: World, brain: agents.Brain | None, start: int, end: int, *, budget: Budget | None = None, tick: str = "") -> Report:
    budget = budget or Budget()
    tick = tick or f"tick:{start}:{end}"
    report = Report(tick)
    if tick in w.rec.ticks:
        report.repeat = True
        return report
    calls = budget.skip if end - start >= budget.long else budget.turn
    n = max(1, min(budget.chunks, math.ceil(max(0, end - start) / budget.chunk)))
    size = max(0, end - start) / n
    try:
        for i in range(n):
            c = end if i == n - 1 else start + round(size * (i + 1))
            w.clock = c
            report.chunks += 1
            wakes: dict[str, tuple[str, str]] = {}  # npc -> (rank kind, reason)

            def wake(npc: str, kind: str, reason: str, wakes=wakes) -> None:
                if npc in w.rec.agents and (npc not in wakes or RANK[kind] < RANK[wakes[npc][0]]):
                    wakes[npc] = (kind, reason)

            for sch in sorted((s for s in w.rec.scheduled.values() if s.status == "pending" and s.at <= c), key=lambda s: s.at):
                if moved := fresh(w, sch.reads):
                    sch.status = "dropped"
                    report.dropped.append(sch.id)
                    wake(sch.actor, "stale", f"原先安排的事落空了：{sch.text}（{'、'.join(moved)} 变了）")
                    continue
                _land(w, sch.ops, prefix=sch.id, actor=sch.actor, report=report)
                w.apply(Occur(id=f"{sch.id}:occur", actor=sch.actor or "engine", text=sch.text, place=sch.place,
                              participants=[p for p in sch.participants if p in w.rec.entities]))  # fmt: skip
                sch.status = "done"
                report.fired.append(sch.id)
            for p in w.rec.promises.values():
                if p.status == "active" and p.due is not None and p.due <= c and not p.noted:
                    p.noted = True
                    for who in (p.by, p.to):
                        wake(who, "promise", f"约定的时间到了：{w.name(p.by)}答应{w.name(p.to)}：{p.text}")
            for npc, a in w.rec.agents.items():
                here = w.place(npc)
                if here == w.st.location or w.rec.entities[npc].status == "死亡":
                    continue  # onscreen, or past acting
                _steps(w, npc, a, c, budget, report, wake)
                for e in w.rec.events:
                    if e.seq > a.seen and e.actor != npc and (npc in e.participants or npc in e.witnesses):
                        wake(npc, "affected", f"发生了：{e.text}")
                if a.wake is not None and a.wake <= c:
                    wake(npc, "timer", "该想想下一步了")
            groups: dict[str, list[str]] = {}
            for npc in sorted(wakes, key=lambda x: RANK[wakes[x][0]]):
                groups.setdefault(w.place(npc), []).append(npc)
            for place, group in sorted(groups.items(), key=lambda g: min(RANK[wakes[x][0]] for x in g[1])):
                if brain is None or calls <= 0:
                    report.deferred += [f"{x}：{wakes[x][1]}" for x in group]
                    continue
                calls -= 1
                report.calls += 1
                await _decide(w, brain, group, place, {x: wakes[x][1] for x in group}, f"{tick}:{c}", report)
    finally:
        w.clock = None
    w.rec.ticks.append(tick)
    del w.rec.ticks[:-200]
    return report


def _steps(w: World, npc: str, a: Agent, c: int, budget: Budget, report: Report, wake) -> None:
    for s in a.plan:
        if s.status != "pending" or (s.due is not None and s.due > c):
            continue
        if agents.holds(w, s.when):
            if s.ops and not fresh(w, a.reads):
                _land(w, s.ops, prefix=f"{npc}:{s.id}", actor=npc, report=report)
                w.apply(AdvancePlan(id=f"{npc}:{s.id}:done", npc=npc, step=s.id, status="done", actor=npc))
            elif s.ops:
                wake(npc, "stale", f"计划依据的事变了：{s.text}（{'、'.join(fresh(w, a.reads))}）")
            else:
                wake(npc, "due", f"该做了：{s.text}")
        elif s.due is not None and c - s.due >= budget.grace:
            w.apply(AdvancePlan(id=f"{npc}:{s.id}:blocked", npc=npc, step=s.id, status="blocked", note=f"条件没满足：{s.when}", actor=npc))
            report.blocked.append(f"{npc}:{s.id}")
            wake(npc, "blocked", f"计划受阻：{s.text}")


async def _decide(w: World, brain: agents.Brain, group: list[str], place: str, reasons: dict[str, str], prefix: str, report: Report) -> None:
    views = {npc: context.npc_view(w, npc) for npc in group}
    ask = agents.Ask(npcs=group, views={k: v.text for k, v in views.items()}, known={k: v.facts for k, v in views.items()},
                     reasons=reasons, t=w.now, place=w.name(place))  # fmt: skip
    scene = await brain.decide(ask)
    report.woke += [f"{x}：{reasons[x]}" for x in group]
    for d in scene.decisions if scene else []:
        if d.npc not in group:
            report.refused.append(f"{d.npc} 不在这场戏里")
            continue
        for i, op in enumerate(d.ops):
            op = op.model_copy(update={"actor": d.npc, "id": f"{prefix}:{d.npc}:{i}"})
            if isinstance(op, SetPlan) and not op.reads:
                op = op.model_copy(update={"reads": views[d.npc].reads})
            if problems := agents.vet(w, d.npc, op):
                report.refused += [f"{d.npc}：{p}" for p in problems]
                continue
            r = w.apply(op)
            (report.landed.append(r.op) if r.ok else report.refused.extend(f"{d.npc}：{p}" for p in r.problems))
        if a := w.rec.agents.get(d.npc):
            a.wake = w.now + d.wait
    if scene and scene.summary and len(group) > 1:
        r = w.apply(Occur(id=f"{prefix}:scene", actor=group[0], text=scene.summary, place=place, participants=group))
        (report.landed.append(r.op) if r.ok else report.refused.extend(r.problems))
    for npc in group:
        if a := w.rec.agents.get(npc):
            a.seen, a.acted = w.rec.seq, w.now
            if not any(isinstance(op, SetPlan) for d in (scene.decisions if scene else []) if d.npc == npc for op in d.ops):
                a.reads = views[npc].reads  # it looked again with what it knows now and kept its plan
            if a.wake is not None and a.wake <= w.now:
                a.wake = None
