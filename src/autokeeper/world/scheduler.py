"""Game time drives the world: when time passes, scheduled things happen and important NPCs go on.

advance(world, brain, start, end) simulates the minutes between in chunks. In each chunk:

    1. scheduled events that came due land (dropped, and their author woken, if what they read
       has changed since);
    2. promises whose time came are noted, and the important NPCs in them wake;
    3. each important NPC: a due plan step whose condition holds lands its prepared
       ops with no model call while the plan is fresh; one without ops, or a stale plan, wakes
       the NPC; a step whose condition stays unmet past its grace is blocked, and wakes it;
       events it took part in or saw since it last decided wake it; so does its own timer;
    4. the woken are ranked (promise, blocked, due, affected, timer) and grouped by place; each
       group is one decision call (an offscreen scene) while the budget lasts, the rest wait.

NPCs where the party is also react to witnessed changes and due plans; their observable
actions reach the current scene. A tick id (the
span simulated) is kept in the record, so a retried turn never simulates the same span twice;
every op gets a deterministic id for the same reason.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
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


def _land(w: World, ops: list[dict[str, Any]], *, prefix: str, actor: str, report: Report) -> bool:
    """A decision is one unit: a refused transfer must not leave a successful handover story."""
    from .store import World
    from .tools import Gateway

    trial = World(w.m.model_copy(deep=True), w.st.model_copy(deep=True))
    trial.clock = w.clock
    prepared = []
    for i, raw in enumerate(ops):
        try:
            raw = Gateway(trial, actor).normalize_ops([raw])[0]
            op = parse({**raw, "actor": actor, "id": f"{prefix}:{i}"})
        except ValueError as exc:
            report.refused.append(f"{prefix}:{i} 格式不对：{str(exc)[:80]}")
            return False
        if actor in trial.rec.agents and (problems := agents.vet(trial, actor, op)):
            report.refused += [f"{prefix}:{i} {p}" for p in problems]
            return False
        if isinstance(op, SetPlan):
            # Prior actions in this same decision are already part of the proposed
            # future, not an external change that makes the new plan immediately stale.
            op.reads = {ref: trial.version(ref) for ref in op.reads}
        r = trial.apply(op)
        if not r.ok:
            report.refused.extend(f"{r.op} {p}" for p in r.problems)
            return False
        prepared.append(op)
    # No await or model call between validation and commit. The NPC operations are
    # deterministic, so they see exactly the state against which they just checked.
    for op in prepared:
        r = w.apply(op)
        if not r.ok:
            report.refused.extend(f"{r.op} {p}" for p in r.problems)
            return False
        report.landed.append(r.op)
    return True


def reactions_ready(w: World) -> bool:
    """New experienced information or a prepared action; elapsed minutes are not required."""
    for npc, a in w.rec.agents.items():
        if w.rec.entities[npc].status == "死亡":
            continue
        if any(_affects(e, npc, a.seen) for e in w.rec.events):
            return True
        if any(s.status == "pending" and s.ops and (s.due is None or s.due <= w.now)
               and agents.holds(w, s.when) for s in a.plan):
            return True
    return (any(s.status == "pending" and s.at <= w.now for s in w.rec.scheduled.values())
            or any(p.status == "active" and not p.noted and p.due is not None and p.due <= w.now
                   for p in w.rec.promises.values()))


async def advance(w: World, brain: agents.Brain | None, start: int, end: int, *, budget: Budget | None = None,
                  tick: str = "", shared: bool = False, events_only: bool = False,
                  checkpoint: Callable[[], None] | None = None) -> Report:
    budget = budget or Budget()
    tick = tick or f"tick:{start}:{end}"
    report = Report(tick)
    if tick in w.rec.ticks:
        report.repeat = True
        return report
    limit = budget.skip if end - start >= budget.long else budget.turn
    if shared:
        if w.rec.npc_budget_turn != w.st.turn:
            w.rec.npc_budget_turn = w.st.turn
            w.rec.npc_budget_calls = w.rec.npc_budget_limit = 0
            for old in [key for key, item in w.rec.npc_work.items() if item["turn"] != w.st.turn]:
                w.rec.npc_work.pop(old)
        w.rec.npc_budget_limit = max(w.rec.npc_budget_limit, limit)
    work = w.rec.npc_work.setdefault(tick, {"turn": w.st.turn, "start": start, "end": end, "since": w.rec.seq,
                                           "events_only": events_only, "report": asdict(report), "chunks": {}, "batches": {}})
    report = Report(**work["report"])

    def save() -> None:
        work["report"] = asdict(report)
        if checkpoint:
            checkpoint()

    def reserve() -> bool:
        if shared:
            if limit <= 0 or w.rec.npc_budget_calls >= w.rec.npc_budget_limit:
                return False
            w.rec.npc_budget_calls += 1
        elif report.calls >= limit:
            return False
        report.calls += 1
        return True

    n = max(1, min(budget.chunks, math.ceil(max(0, end - start) / budget.chunk)))
    size = max(0, end - start) / n
    try:
        for i in range(n):
            c = end if i == n - 1 else start + round(size * (i + 1))
            w.clock = c
            chunk = work["chunks"].get(str(i))
            if chunk and chunk.get("done"):
                continue
            wakes: dict[str, tuple[str, str]] = {}  # npc -> (rank kind, reason)

            def wake(npc: str, kind: str, reason: str, wakes=wakes) -> None:
                if npc in w.rec.agents and (npc not in wakes or RANK[kind] < RANK[wakes[npc][0]]):
                    wakes[npc] = (kind, reason)

            if chunk is None:
                report.chunks += 1
                _prepare(w, c, budget, report, wake, events_only=events_only)
                chunk = work["chunks"][str(i)] = {"wakes": wakes, "before": w.rec.seq}
                save()
            await _serve(w, brain, chunk["wakes"], f"{tick}:{c}", report, work["batches"], reserve, save)
            # Those the decisions just touched may answer in the same time slice.
            if "ripple" not in chunk:
                ripple: dict[str, tuple[str, str]] = {}
                for npc, a in w.rec.agents.items():
                    if w.rec.entities[npc].status != "死亡":
                        _affected(w, npc, a, lambda x, kind, why, ripple=ripple: ripple.setdefault(x, (kind, why)), since=chunk["before"])
                chunk["ripple"] = ripple
                save()
            await _serve(w, brain, chunk["ripple"], f"{tick}:{c}:r", report, work["batches"], reserve, save)
            chunk["done"] = True
            save()
    finally:
        w.clock = None
    w.rec.ticks.append(tick)
    del w.rec.ticks[:-200]
    w.rec.npc_work.pop(tick, None)  # completed ticks need no retained private prompt copies
    if checkpoint:
        checkpoint()
    return report


def _prepare(w: World, c: int, budget: Budget, report: Report, wake, *, events_only: bool) -> None:
    """Synchronous work and wake reasons are checkpointed before any model is awaited."""
    for sch in sorted((s for s in w.rec.scheduled.values() if s.status == "pending" and s.at <= c), key=lambda s: s.at):
        if moved := fresh(w, sch.reads):
            sch.status = "dropped"
            report.dropped.append(sch.id)
            wake(sch.actor, "stale", f"原先安排的事落空了：{sch.text}（{'、'.join(moved)} 变了）")
            continue
        # Someone walking with the investigators stays with them: an arrangement does not pull
        # a companion away; it goes on without them (agents.py lets the NPC end that itself).
        people = [p for p in sch.participants if p in w.rec.entities
                  and not (w.rec.entities[p].following and w.place(p) != sch.place)]
        ops = list(sch.ops)
        if (spot := w.rec.entities.get(sch.place)) is not None and spot.kind == "location":
            for person in people:
                if (w.rec.entities[person].kind == "npc" and w.place(person) != sch.place
                        and not any(raw.get("kind") == "move" and raw.get("entity") == person for raw in ops)):
                    ops.append({"kind": "move", "entity": person, "to": sch.place})
        ops.append({"kind": "occur", "text": sch.text, "place": sch.place, "participants": people})
        if not _land(w, ops, prefix=sch.id, actor=sch.actor or "engine", report=report):
            sch.status = "dropped"
            report.dropped.append(sch.id)
            wake(sch.actor, "blocked", f"原先安排无法执行：{sch.text}")
            continue
        sch.status = "done"
        report.fired.append(sch.id)
    for promise in w.rec.promises.values():
        if promise.status == "active" and promise.due is not None and promise.due <= c and not promise.noted:
            promise.noted = True
            for who in (promise.by, promise.to):
                wake(who, "promise", f"约定的时间到了：{w.name(promise.by)}答应{w.name(promise.to)}：{promise.text}")
    for npc, agent in w.rec.agents.items():
        if w.rec.entities[npc].status == "死亡":
            continue
        _steps(w, npc, agent, c, budget, report, wake)
        _affected(w, npc, agent, wake)
        if events_only:
            continue  # a zero-minute reaction does not create an extra periodic planning cycle
        if agent.wake is not None and agent.wake <= c:
            wake(npc, "timer", "该想想下一步了")
        elif agent.wake is None and agent.goals and not any(s.status == "pending" for s in agent.plan) and c - agent.acted >= 10:
            wake(npc, "timer", "有自己的目标，但还没有接下来的安排")


def _affects(event, npc: str, since: int) -> bool:
    return (event.reactive and event.seq > since and event.actor != npc
            and (npc in event.participants or npc in event.witnesses))


def _affected(w: World, npc: str, a: Agent, wake, *, since: int = 0) -> None:
    for event in w.rec.events:
        if _affects(event, npc, max(a.seen, since)):
            wake(npc, "affected", f"发生了：{event.text}")


async def _serve(w: World, brain: agents.Brain | None, wakes: dict[str, tuple[str, str]], prefix: str,
                 report: Report, batches: dict, reserve: Callable[[], bool], save: Callable[[], None]) -> None:
    """Freeze permissions, persist each returned result, then settle in priority order.

    An interrupted provider call still consumes its reserved allowance. Completed
    sibling results survive cancellation and are revalidated rather than requested twice.
    """
    if prefix not in batches:
        groups: dict[str, list[str]] = {}
        for npc in sorted(wakes, key=lambda x: RANK[wakes[x][0]]):
            groups.setdefault(w.place(npc), []).append(npc)
        batches[prefix] = [
            {"place": place, "group": group, "views": {npc: asdict(context.npc_view(w, npc)) for npc in group},
             "reasons": {npc: wakes[npc][1] for npc in group}, "t": w.now, "status": "pending"}
            for place, group in sorted(groups.items(), key=lambda g: min(RANK[wakes[x][0]] for x in g[1]))
        ]
    jobs = batches[prefix]

    async def think(job: dict) -> None:
        views = {npc: context.View(**view) for npc, view in job["views"].items()}
        ask = agents.Ask(npcs=job["group"], views={npc: view.text for npc, view in views.items()},
                         known={npc: view.facts for npc, view in views.items()}, reasons=job["reasons"],
                         t=job["t"], place=w.name(job["place"]))
        try:
            assert brain is not None
            scene = await brain.decide(ask)
            job["scene"] = scene.model_dump(mode="json") if scene is not None else None
        except asyncio.CancelledError:
            # Keep the old permission snapshot; a resumed result must still pass freshness.
            for npc, reads in ask.consulted_reads.items():
                if npc in job["views"]:
                    job["views"][npc]["reads"].update(reads)
            save()
            raise
        except Exception as exc:
            job["scene"], job["error"] = None, type(exc).__name__
        for npc, reads in ask.consulted_reads.items():
            if npc in job["views"]:
                job["views"][npc]["reads"].update(reads)
        job["status"] = "ready"
        save()

    selected = []
    for job in jobs:
        if job["status"] != "pending":
            continue
        if job.get("attempts") and any(fresh(w, view["reads"]) for view in job["views"].values()):
            report.deferred += [f"{npc}：恢复构思时依据已变化，需重新决策" for npc in job["group"]]
            job["status"] = "done"
            continue
        if brain is not None and reserve():
            job["attempts"] = job.get("attempts", 0) + 1
            selected.append(job)
        else:
            report.deferred += [f"{npc}：{job['reasons'][npc]}" for npc in job["group"]]
            job["status"] = "done"
    save()  # record every call reservation before any provider is allowed to run
    if selected:
        tasks = [asyncio.create_task(think(job)) for job in selected]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    for job in jobs:
        if job["status"] != "ready":
            continue
        if job.get("error") == "CassetteError":
            from ..harness.replay import CassetteError

            raise CassetteError("NPC 决策的离线回放不匹配")
        if job.get("error"):
            report.refused.append(f"{'、'.join(job['group'])} 决策失败：{job['error']}")
        scene = agents.Scene.model_validate(job["scene"]) if job.get("scene") is not None else None
        views = {npc: context.View(**view) for npc, view in job["views"].items()}
        _settle(w, scene, job["group"], job["place"], views, job["reasons"], prefix, report)
        job["status"] = "done"
        for key in ("scene", "views"):
            job.pop(key, None)
        save()  # landed operations and the completion receipt share one checkpoint


def _steps(w: World, npc: str, a: Agent, c: int, budget: Budget, report: Report, wake) -> None:
    for s in a.plan:
        if s.status != "pending" or (s.due is not None and s.due > c):
            continue
        problems = agents.condition_problems(w, s.when) + agents.condition_problems(w, s.achieved)
        if not s.started and not s.achieved and not s.ops:
            problems.append("旧计划缺少完成依据，需重新规划")
        if problems:
            # Older saves may contain invented IDs or an unsupported condition.
            # Make the fault observable and ask the NPC to reconsider, not wait forever.
            note = "；".join(problems)
            w.apply(AdvancePlan(id=f"{npc}:{s.id}:{s.started}:invalid", npc=npc, step=s.id, status="blocked", note=note, actor=npc))
            report.blocked.append(f"{npc}:{s.id}")
            wake(npc, "blocked", f"计划条件无效，需重新安排：{s.text}（{note}）")
            continue
        if agents.holds(w, s.when):
            if s.ops and not fresh(w, a.reads):
                ops = [*s.ops, {"kind": "step", "npc": npc, "step": s.id, "status": "done"}]
                if not _land(w, ops, prefix=f"{npc}:{s.id}:{s.started}", actor=npc, report=report):
                    wake(npc, "blocked", f"计划未能执行，需处理实际阻碍：{s.text}")
                    report.blocked.append(f"{npc}:{s.id}")
            elif s.ops:
                wake(npc, "stale", f"计划依据的事变了：{s.text}（{'、'.join(fresh(w, a.reads))}）")
            elif s.woke is None:
                wake(npc, "due", f"该做了：{s.text}")
        elif s.due is not None and c - s.due >= budget.grace:
            w.apply(AdvancePlan(id=f"{npc}:{s.id}:{s.started}:blocked", npc=npc, step=s.id, status="blocked", note=f"条件没满足：{s.when}", actor=npc))
            report.blocked.append(f"{npc}:{s.id}")
            wake(npc, "blocked", f"计划受阻：{s.text}")


def _settle(w: World, scene: agents.Scene | None, group: list[str], place: str, views: dict[str, context.View],
            reasons: dict[str, str], prefix: str, report: Report) -> None:
    if scene is None:  # no decision came: they stay woken and are asked again later
        report.deferred += [f"{x}：{reasons[x]}" for x in group]
        return
    report.woke += [f"{x}：{reasons[x]}" for x in group]
    accepted = set()
    for d in scene.decisions:
        if d.npc not in group:
            report.refused.append(f"{d.npc} 不在这场戏里")
            continue
        if moved := fresh(w, views[d.npc].reads):
            report.deferred.append(f"{d.npc}：构思期间依据发生变化，需重新决策（{'、'.join(moved)}）")
            if a := w.rec.agents.get(d.npc):
                a.wake = w.now
            continue
        ops = []
        for op in d.ops:
            if isinstance(op, SetPlan) and not op.reads:
                op = op.model_copy(update={"reads": views[d.npc].reads})
            ops.append(op.model_dump(mode="json"))
        if not _land(w, ops, prefix=f"{prefix}:{d.npc}", actor=d.npc, report=report):
            # A failed decision must remain eligible on the next turn, with the real
            # refusal in the diagnostic report, instead of sleeping for four hours.
            if a := w.rec.agents.get(d.npc):
                a.wake = w.now
            report.deferred.append(f"{d.npc}：行动未能执行，需重新决策")
            continue
        accepted.add(d.npc)
        if a := w.rec.agents.get(d.npc):
            a.wake = w.now + d.wait
            if d.mood:
                a.mood = d.mood
                w.touch(a)
    if scene.summary and len(group) > 1 and accepted == set(group):
        op = Occur(id=f"{prefix}:scene", actor=group[0], text=scene.summary, place=place, participants=group)
        if problems := agents.vet(w, group[0], op):
            report.refused.extend(problems)
        else:
            r = w.apply(op)
            (report.landed.append(r.op) if r.ok else report.refused.extend(r.problems))
    for npc in accepted:
        if a := w.rec.agents.get(npc):
            # Other groups may already have committed after this view was captured.
            # Their new events must remain unread, so they can wake this NPC later.
            a.seen, a.acted = views[npc].seen, w.now
            for s in a.plan:  # it has thought about these now; its timer brings it back to them
                if s.status == "pending" and (s.due is None or s.due <= w.now) and s.woke is None:
                    s.woke = w.now
            if not any(isinstance(op, SetPlan) for d in scene.decisions if d.npc == npc for op in d.ops):
                a.reads = {ref: version for ref, version in views[npc].reads.items() if ref != f"agent:{npc}"}
            if a.wake is not None and a.wake <= w.now:
                a.wake = None
