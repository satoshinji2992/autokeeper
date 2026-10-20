"""Game time drives the world: when time passes, scheduled things happen and important NPCs go on.

advance(world, brain, start, end) simulates the minutes between in chunks. In each chunk:

    1. scheduled events that came due land (dropped, and their author woken, if what they read
       has changed since);
    2. promises whose time came are noted, and the important NPCs in them wake;
    3. each important NPC: a due plan step whose condition holds lands its prepared
       ops with no model call while the plan is fresh; one without ops, or a stale plan, wakes
       the NPC; a step whose condition stays unmet past its grace is blocked, and wakes it;
       events it took part in or saw since it last decided wake it; a conversation with the
       investigators only once time jumps or they move on (its voice answered it at the time); so
       does its own timer. One with goals and nothing left to do thinks of its next step only when
       time jumps or the party moves on;
    4. those woken are decided together in one call while the budget lasts (the rest wait),
       each by its own view, each op held to what that NPC can do (agents.vet), and each decision
       resting only on lines of its own view (agents.foreign).

A step marked for meeting the investigators (agents.meeting: coming to the door, being asked)
never wakes anyone: it is character material for the NPC's voice. NPCs where the party is also
react to witnessed changes and due plans; what would change the scene in front of the party is
left for the keeper to play (world/stage.py). A tick id (the span simulated) is kept in the
record, so a retried turn never simulates the same span twice; every op gets a deterministic id
for the same reason.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from . import agents, context, stage
from .ops import AdvancePlan, Occur, SetPlan, parse
from .record import Agent

if TYPE_CHECKING:
    from .store import World

RANK = {"promise": 0, "blocked": 1, "stale": 1, "due": 2, "affected": 3, "timer": 4}
SCENE = ":scene"  # the id suffix of a decided scene's summary (_settle)
TALK = "talk:"  # the op id prefix of a conversation with the investigators (engine/game.Game._exchanged)


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
    staged: list[str] = field(default_factory=list)  # beats left for the keeper: they would change the scene (stage.py)

    @classmethod
    def restored(cls, saved: dict[str, Any]) -> Report:
        """A report kept in an interrupted tick, without the fields of older versions."""
        return cls(**{k: v for k, v in saved.items() if k in cls.__dataclass_fields__})


def fresh(w: World, reads: dict[str, int]) -> list[str]:
    """The refs whose version moved since they were read; empty when all still hold."""
    return [ref for ref, v in reads.items() if w.version(ref) != v]


def _land(w: World, ops: list[dict[str, Any]], *, prefix: str, actor: str, report: Report, text: str = "",
          reads: dict[str, int] | None = None, when: str = "") -> bool:
    """A decision is one unit: a refused transfer must not leave a successful handover story.
    Work that would change the scene in front of the investigators waits for the keeper as a
    beat instead of landing behind their backs (stage.py), keeping what it rested on (reads,
    the plan step's when): if that moved by the time it plays, its author decides again. The
    rest lands now, turning points included: whoever decides has decided."""
    from .store import World
    from .tools import Gateway

    trial = World(w.m.model_copy(deep=True), w.st.model_copy(deep=True))
    trial.clock = w.clock
    prepared = []
    involved: list[str] = []
    leaving: list[str] = []
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
        involved += stage.touches(trial, op)
        leaving += stage.leaves(trial, op)
        r = trial.apply(op)
        if not r.ok:
            report.refused.extend(f"{r.op} {p}" for p in r.problems)
            return False
        prepared.append(op)
    if involved:
        beat = stage.queue(w, prefix, actor, [op.model_dump(mode="json") for op in prepared], involved, leaving,
                           reads=reads, when=when, text=text)
        if beat.status == "dropped":
            report.refused.append(f"{beat.id} 上次没能上演：{beat.note}")
            return False
        report.staged.append(beat.id)
        return True
    # No await or model call between validation and commit. The NPC operations are
    # deterministic, so they see exactly the state against which they just checked.
    for op in prepared:
        r = w.apply(op)
        if not r.ok:
            report.refused.extend(f"{r.op} {p}" for p in r.problems)
            return False
        report.landed.append(r.op)
    return True


def reactions_ready(w: World, *, moved_on: bool = False) -> bool:
    """New experienced information or a prepared action; elapsed minutes are not required. Once
    the party has moved on, the conversations they left behind count too."""
    for npc, a in w.rec.agents.items():
        if w.rec.entities[npc].status == "死亡" or stage.waiting(w, npc):
            continue
        if any(_affects(e, npc, a.seen, talks=moved_on) for e in w.rec.events):
            return True
        if any(s.status == "pending" and s.ops and (s.due is None or s.due <= w.now)
               and not agents.meeting(w, npc, s) and agents.holds(w, s.when) for s in a.plan):
            return True
    return (any(s.status == "pending" and s.at <= w.now for s in w.rec.scheduled.values())
            or any(p.status == "active" and not p.noted and p.due is not None and p.due <= w.now
                   for p in w.rec.promises.values()))


async def advance(w: World, brain: agents.Brain | None, start: int, end: int, *, budget: Budget | None = None,
                  tick: str = "", shared: bool = False, events_only: bool = False, moved_on: bool = False,
                  checkpoint: Callable[[], None] | None = None) -> Report:
    """`moved_on`: the party went somewhere else this turn; like a jump in time, a moment for
    those with nothing left to do to think of their next step."""
    budget = budget or Budget()
    tick = tick or f"tick:{start}:{end}"
    report = Report(tick)
    if tick in w.rec.ticks:
        report.repeat = True
        return report
    skip = end - start >= budget.long
    limit = budget.skip if skip else budget.turn
    if shared:
        if w.rec.npc_budget_turn != w.st.turn:
            w.rec.npc_budget_turn = w.st.turn
            w.rec.npc_budget_calls = w.rec.npc_budget_limit = 0
            for old in [key for key, item in w.rec.npc_work.items() if item["turn"] != w.st.turn]:
                w.rec.npc_work.pop(old)
        w.rec.npc_budget_limit = max(w.rec.npc_budget_limit, limit)
    work = w.rec.npc_work.setdefault(tick, {"turn": w.st.turn, "start": start, "end": end, "since": w.rec.seq,
                                           "events_only": events_only, "report": asdict(report), "chunks": {}, "batches": {}})
    report = Report.restored(work["report"])

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
                _prepare(w, c, budget, report, wake, events_only=events_only, planning=skip or moved_on)
                chunk = work["chunks"][str(i)] = {"wakes": wakes, "before": w.rec.seq}
                save()
            await _serve(w, brain, chunk["wakes"], f"{tick}:{c}", report, work["batches"], reserve, save)
            # Those the decisions just touched may answer in the same time slice.
            if "ripple" not in chunk:
                ripple: dict[str, tuple[str, str]] = {}
                for npc, a in w.rec.agents.items():
                    if w.rec.entities[npc].status != "死亡" and not stage.waiting(w, npc):
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


PLANNING_GAP = 60  # game minutes since its last decision before one with nothing to do plans again


def _prepare(w: World, c: int, budget: Budget, report: Report, wake, *, events_only: bool, planning: bool = False) -> None:
    """Synchronous work and wake reasons are checkpointed before any model is awaited."""
    for sch in sorted((s for s in w.rec.scheduled.values() if s.status == "pending" and s.at <= c), key=lambda s: s.at):
        if moved := fresh(w, sch.reads):
            sch.status = "dropped"
            report.dropped.append(sch.id)
            wake(sch.actor, "stale", f"原先安排的事落空了：{sch.text}（{'、'.join(moved)} 变了）")
            continue
        # A commitment to the investigators outranks an arrangement made elsewhere: it goes on
        # without someone walking with them (stage.py), and whoever made it hears why.
        busy = {p: why for p in sch.participants if p in w.rec.entities and w.place(p) != sch.place
                and (why := stage.committed(w, p))}
        people = [p for p in sch.participants if p in w.rec.entities and p not in busy]
        for why in busy.values():
            wake(sch.actor, "blocked", f"安排「{sch.text}」少了一个人：{why}")
        ops = list(sch.ops)
        if (spot := w.rec.entities.get(sch.place)) is not None and spot.kind == "location":
            for person in people:
                if (w.rec.entities[person].kind == "npc" and w.place(person) != sch.place
                        and not any(raw.get("kind") == "move" and raw.get("entity") == person for raw in ops)):
                    ops.append({"kind": "move", "entity": person, "to": sch.place})
        ops.append({"kind": "occur", "text": sch.text, "place": sch.place, "participants": people})
        if not _land(w, ops, prefix=sch.id, actor=sch.actor or "engine", report=report, text=sch.text, reads=sch.reads):
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
        if w.rec.entities[npc].status == "死亡" or stage.waiting(w, npc):  # its move is waiting
            continue
        _steps(w, npc, agent, c, budget, report, wake)
        _affected(w, npc, agent, wake, talks=planning)
        if agent.refused:  # what it last meant to do could not be done: it hears what stopped it
            wake(npc, "blocked", f"上次没做成：{agent.refused}")
        if events_only:
            continue  # a zero-minute reaction does not create an extra periodic planning cycle
        if agent.wake is not None and agent.wake <= c:
            wake(npc, "timer", "该想想下一步了")
        elif (planning and agent.wake is None and agent.goals and c - agent.acted >= PLANNING_GAP
              and not any(s.status == "pending" and not agents.meeting(w, npc, s) for s in agent.plan)):
            wake(npc, "timer", "有自己的目标，但还没有接下来的安排")


def _affects(event, npc: str, since: int, *, talks: bool = False) -> bool:
    """Something it went through or saw, other than its own doing. A conversation with the
    investigators counts only with `talks` (time has jumped, or they have moved on): its voice
    answered it in the same breath, and what it makes of it backstage can wait until they go."""
    if event.id.endswith(SCENE) and npc in event.participants:
        return False  # the summary of a scene it was decided in
    return (event.reactive and event.seq > since and event.actor != npc and (talks or not event.op.startswith(TALK))
            and (npc in event.participants or npc in event.witnesses))


def _affected(w: World, npc: str, a: Agent, wake, *, since: int = 0, talks: bool = False) -> None:
    for event in w.rec.events:
        if _affects(event, npc, max(a.seen, since), talks=talks):
            wake(npc, "affected", f"发生了：{event.text}")


async def _serve(w: World, brain: agents.Brain | None, wakes: dict[str, tuple[str, str]], prefix: str,
                 report: Report, batches: dict, reserve: Callable[[], bool], save: Callable[[], None]) -> None:
    """Freeze permissions, persist each returned result, then settle in priority order.

    An interrupted provider call still consumes its reserved allowance. Completed
    sibling results survive cancellation and are revalidated rather than requested twice.
    """
    if prefix not in batches:
        # Everyone woken in this slice is decided in one call, wherever they are: those in the same
        # place can act together, and the world gets one look per slice instead of one per room.
        group = sorted(wakes, key=lambda x: RANK[wakes[x][0]])
        places = {w.place(npc) for npc in group}
        batches[prefix] = [
            {"place": next(iter(places)) if len(places) == 1 else "", "group": group,
             "views": {npc: asdict(context.npc_view(w, npc)) for npc in group},
             "reasons": {npc: wakes[npc][1] for npc in group}, "t": w.now, "status": "pending"}
        ] if group else []
    jobs = batches[prefix]

    async def think(job: dict) -> None:
        views = {npc: context.View(**view) for npc, view in job["views"].items()}
        ask = agents.Ask(npcs=job["group"], views={npc: view.text for npc, view in views.items()},
                         known={npc: view.facts for npc, view in views.items()}, reasons=job["reasons"],
                         t=job["t"], place=w.name(job["place"]) if job["place"] else "")
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
        _settle(w, scene, job["group"], views, job["reasons"], prefix, report)
        job["status"] = "done"
        for key in ("scene", "views"):
            job.pop(key, None)
        save()  # landed operations and the completion receipt share one checkpoint


def _steps(w: World, npc: str, a: Agent, c: int, budget: Budget, report: Report, wake) -> None:
    for s in a.plan:
        if s.status != "pending" or (s.due is not None and s.due > c) or agents.meeting(w, npc, s):
            continue  # not yet; or what it does on meeting the investigators, for its voice
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
                mark = len(report.refused)
                if not _land(w, ops, prefix=f"{npc}:{s.id}:{s.started}", actor=npc, report=report, text=s.text,
                             reads=a.reads, when=s.when):
                    why = "；".join(p.partition(" ")[2] or p for p in report.refused[mark:])[:160]
                    wake(npc, "blocked", f"计划未能执行，需处理实际阻碍：{s.text}" + (f"（{why}）" if why else ""))
                    report.blocked.append(f"{npc}:{s.id}")
            elif s.ops:
                wake(npc, "stale", f"计划依据的事变了：{s.text}（{'、'.join(fresh(w, a.reads))}）")
            elif s.woke is None:
                wake(npc, "due", f"该做了：{s.text}")
        elif s.due is not None and c - s.due >= budget.grace:
            w.apply(AdvancePlan(id=f"{npc}:{s.id}:{s.started}:blocked", npc=npc, step=s.id, status="blocked", note=f"条件没满足：{s.when}", actor=npc))
            report.blocked.append(f"{npc}:{s.id}")
            wake(npc, "blocked", f"计划受阻：{s.text}")


def _together(where: dict[str, str], accepted: set[str]) -> tuple[str, list[str]] | None:
    """The one place where several of a call's people were together, every one of them decided:
    a scene's summary is told there. Two such places, or one of them refused, and it is not."""
    by_place: dict[str, list[str]] = {}
    for npc, place in where.items():
        by_place.setdefault(place, []).append(npc)
    shared = [(place, people) for place, people in by_place.items() if place and len(people) > 1]
    if len(shared) != 1 or not set(shared[0][1]) <= accepted:
        return None
    return shared[0]


def _settle(w: World, scene: agents.Scene | None, group: list[str], views: dict[str, context.View],
            reasons: dict[str, str], prefix: str, report: Report) -> None:
    if scene is None:  # no decision came: they stay woken and are asked again later
        report.deferred += [f"{x}：{reasons[x]}" for x in group]
        return
    report.woke += [f"{x}：{reasons[x]}" for x in group]
    where = {npc: w.place(npc) for npc in group}  # as they stood when the call was answered
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
        if bad := agents.foreign(w, d.npc, d.basis, group):
            # One call decides for several people: a decision resting on what only another of them
            # knows is not this NPC's to make.
            report.refused.append(f"{d.npc} 的依据出自别人那一段资料：{'、'.join(bad)}")
            if a := w.rec.agents.get(d.npc):
                a.wake, a.refused = w.now, "那个决定依据了他并不知道的事"
            continue
        ops = []
        for op in d.ops:
            if isinstance(op, SetPlan) and not op.reads:
                op = op.model_copy(update={"reads": views[d.npc].reads})
            ops.append(op.model_dump(mode="json"))
        mark = len(report.refused)
        if not _land(w, ops, prefix=f"{prefix}:{d.npc}", actor=d.npc, report=report, text=d.judge or d.notice,
                     reads=views[d.npc].reads):
            # A failed decision must remain eligible on the next turn, told what stopped it,
            # instead of sleeping for four hours.
            if a := w.rec.agents.get(d.npc):
                a.wake, a.refused = w.now, "；".join(p.partition(" ")[2] or p for p in report.refused[mark:])[:200]
            report.deferred.append(f"{d.npc}：行动未能执行，需重新决策")
            continue
        accepted.add(d.npc)
        if a := w.rec.agents.get(d.npc):
            a.wake, a.refused = w.now + d.wait, ""
            if d.mood:
                a.mood = d.mood
                w.touch(a)
    if scene.summary and (together := _together(where, accepted)):
        place, people = together
        op = Occur(id=f"{prefix}{SCENE}", actor=people[0], text=scene.summary, place=place, participants=people)
        if problems := agents.vet(w, people[0], op):
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
