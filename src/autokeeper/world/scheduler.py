"""Run prepared world changes and leave judgment to the keeper.

No model, periodic NPC wake-up, or call budget lives here. Known outcomes execute on game
time; blocked arrangements, due work without an outcome, and newly experienced events become
persistent attention notes. Reading or retrying a note does not consume it: the keeper must
finish a valid response before the caller acknowledges its captured version.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from . import agents, stage
from .ops import AdvancePlan, SetPlan, execution_order, parse
from .record import Agent, BackstageNeed

if TYPE_CHECKING:
    from .store import World

GRACE = 120  # game minutes a scheduled condition may wait before it is reported blocked
TALK = "talk:"


@dataclass
class Report:
    tick: str
    repeat: bool = False
    chunks: int = 0  # deterministic calendar boundaries processed
    landed: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    fired: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    staged: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)

    @classmethod
    def restored(cls, saved: dict[str, Any]) -> Report:
        return cls(**saved)


def pending(w: World, npcs: Collection[str] | None = None) -> list[BackstageNeed]:
    """Unconsidered work, optionally selected by related NPCs; global notes remain available."""
    chosen = None if npcs is None else set(npcs)
    return [need for need in w.rec.backstage.values() if need.status == "pending" and need.t <= w.now
            and (chosen is None or not need.people or bool(chosen.intersection(need.people)))]


def acknowledge(w: World, tickets: dict[str, int]) -> list[str]:
    """Mark only the exact changes the keeper considered, never a plan or goal as completed."""
    handled = []
    for ident, version in tickets.items():
        need = w.rec.backstage.get(ident)
        if need is None or need.status != "pending" or need.version != version:
            continue
        need.status = "considered"
        handled.append(ident)
    return handled


def _notice(w: World, source: str, npc: str, kind: str, text: str, *, at: int | None = None,
            people: Collection[str] = (), evidence: dict[str, int] | None = None) -> BackstageNeed:
    """A stable source is not a repeated alarm; only changed evidence opens a new version."""
    from .store import digest

    ident = "backstage:" + digest(source)[:20]
    related = list(dict.fromkeys(x for x in [npc, *people] if x in w.rec.agents))
    evidence = dict(evidence or {})
    when = w.now if at is None else at
    known = w.rec.backstage.get(ident)
    if known is not None:
        if (known.kind, known.text, known.evidence) != (kind, text, evidence):
            known.kind, known.text, known.evidence = kind, text, evidence
            known.people, known.version, known.status = related, known.version + 1, "pending"
            known.t, known.turn = when, w.st.turn
        return known
    need = BackstageNeed(id=ident, npc=npc, people=related, kind=kind, source=source,
                         text=text, evidence=evidence, t=when, turn=w.st.turn)
    w.rec.backstage[ident] = need
    return need


def fresh(w: World, reads: dict[str, int]) -> list[str]:
    """The refs whose version moved since they were read; empty when all still hold."""
    return [ref for ref, v in reads.items() if w.version(ref) != v]


def _land(w: World, ops: list[dict[str, Any]], *, prefix: str, actor: str, report: Report, text: str = "",
          reads: dict[str, int] | None = None, when: str = "") -> bool:
    """A decision is one unit: a refused transfer must not leave a successful handover story.
    Work that would change the scene in front of the investigators waits for the keeper as a
    beat instead of landing behind their backs (stage.py), keeping what it rested on (reads,
    the plan step's when): if that moved by the time it plays, the keeper reconsiders it. The
    rest lands now, turning points included: whoever decides has decided."""
    from .store import World
    from .tools import Gateway

    trial = World(w.m.model_copy(deep=True), w.st.model_copy(deep=True))
    trial.clock = w.clock
    prepared = []
    involved: list[str] = []
    leaving: list[str] = []
    for i, raw in execution_order(ops):
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
    # No await or model call between validation and commit. The prepared operations are
    # deterministic, so they see exactly the state against which they just checked.
    for op in prepared:
        r = w.apply(op)
        if not r.ok:
            report.refused.extend(f"{r.op} {p}" for p in r.problems)
            return False
        report.landed.append(r.op)
    return True


def _affects(event, npc: str, *, talks: bool = False) -> bool:
    return (event.reactive and event.actor != npc and (talks or not event.op.startswith(TALK))
            and (npc in event.participants or npc in event.witnesses))


def _stamp(w: World, seq: int) -> int | None:
    return next((entry.t for entry in reversed(w.rec.journal) if entry.seq == seq),
                next((event.t for event in reversed(w.rec.events) if event.seq == seq), None))


def _current(w: World, obj) -> bool:
    """Current mutable plans/intentions may belong to a leading scene's future."""
    stamp = _stamp(w, obj.changed)
    return stamp is None or stamp <= w.now


def _source(w: World, source: str) -> BackstageNeed | None:
    from .store import digest

    return w.rec.backstage.get("backstage:" + digest(source)[:20])


def collect(w: World, *, moved_on: bool = False, held: Collection[str] = ()) -> list[BackstageNeed]:
    """Collect attention at this scene's time, without running any part of the past calendar.

    Each experienced event owns a versioned ticket. Record sequence is submission order, not
    game time: acknowledging a late-written old event cannot consume an earlier-written future one.
    """
    for promise in w.rec.promises.values():
        if (promise.status == "active" and promise.made <= w.now and promise.due is not None
                and promise.due <= w.now and _current(w, promise)):
            _notice(w, f"promise:{promise.id}", promise.by, "promise",
                    f"约定的时间到了：{w.name(promise.by)}答应{w.name(promise.to)}：{promise.text}",
                    people=[promise.by, promise.to], at=max(promise.made, promise.due),
                    evidence={f"promise:{promise.id}": promise.version})
    for npc, agent in w.rec.agents.items():
        if npc in held:
            continue
        for event in w.rec.events:
            if event.t > w.now or not _affects(event, npc, talks=moved_on):
                continue
            changes = [revision for revision in w.rec.revisions if revision.target == f"event:{event.id}"
                       or f"event:{event.id}" in revision.touched]
            if any(revision.t > w.now for revision in changes):
                continue  # do not pass a future rewrite off as this past event's original words
            _notice(w, f"event:{npc}:{event.id}", npc, "affected", "亲历或获知：" + event.text,
                    at=max([event.t, *(revision.t for revision in changes)]),
                    evidence={f"event:{event.id}": event.version})
        for revision in w.rec.revisions:
            if revision.t > w.now:
                continue
            kind, _, ident = revision.target.partition(":")
            fact = w.rec.facts.get(ident) if kind == "fact" else None
            event = w.rec.event(ident) if kind == "event" else None
            if (f"agent:{npc}" in revision.touched or (fact is not None and npc in fact.known_by)
                    or (event is not None and (npc in event.participants or npc in event.witnesses))):
                _notice(w, f"revision:{revision.id}:{npc}", npc, "revised",
                        f"人物相关的幕后设定已修改：{revision.new}（{revision.reason}）", at=revision.t,
                        evidence={revision.target: revision.new_version})
        if not _current(w, agent):
            continue
        entity = w.rec.entities[npc]
        if (_current(w, entity) and entity.status == "死亡") or stage.waiting(w, npc):
            continue
        _judgments(w, npc, agent)
        if agent.refused:
            _notice(w, f"refused:{npc}", npc, "blocked", f"未落实：{agent.refused}")
        if (moved_on and not agent.passive and agent.goals
                and not any(step.status in {"pending", "blocked"} and not agents.meeting(w, npc, step)
                            for step in agent.plan)):
            _notice(w, f"goals:{npc}:{agent.goal_source}", npc, "goals",
                    "尚无后续安排的目标：" + "；".join(agent.goals), at=_stamp(w, agent.changed))
    return pending(w)


def _judgments(w: World, npc: str, agent: Agent) -> None:
    for step in agent.plan:
        installed = _stamp(w, step.started)
        if (step.status not in {"pending", "blocked"} or (installed is not None and installed > w.now)
                or (step.due is not None and step.due > w.now) or agents.meeting(w, npc, step)):
            continue
        source = f"step:{npc}:{step.id}:{step.started}"
        evidence = {ref: w.version(ref) for ref in agent.reads}
        if step.status == "blocked":
            if _source(w, source) is None:
                _notice(w, source, npc, "blocked", f"计划受阻：{step.text}（{step.note}）", evidence=evidence)
            continue
        problems = agents.condition_problems(w, step.when) + agents.condition_problems(w, step.achieved)
        if problems:
            _notice(w, source, npc, "blocked", f"计划条件无效：{step.text}（{'；'.join(problems)}）", evidence=evidence)
        elif agents.holds(w, step.when):
            moved = fresh(w, agent.reads)
            previous = _source(w, source)
            if step.ops and not moved and previous is not None and previous.kind == "blocked" and previous.evidence == evidence:
                continue  # keep the actual execution failure rather than replacing it with a generic due note
            kind = "stale" if moved else "due"
            text = f"计划依据已变化：{step.text}（{'、'.join(moved)}）" if moved else f"待判断的安排已到时机：{step.text}"
            _notice(w, source, npc, kind, text, evidence=evidence)
        elif step.due is not None and w.now - step.due >= GRACE:
            _notice(w, source, npc, "blocked", f"计划受阻：{step.text}（条件未满足：{step.when}）", evidence=evidence)


def reactions_ready(w: World, *, moved_on: bool = False) -> bool:
    """Collect attention and report prepared work that can execute at the current boundary."""
    if collect(w, moved_on=moved_on):
        return True
    return (any(s.status == "pending" and s.at <= w.now for s in w.rec.scheduled.values())
            or any(_current(w, agent) and step.status == "pending" and step.ops
                   and (step.due is None or step.due <= w.now) and not agents.meeting(w, npc, step)
                   and agents.holds(w, step.when)
                   for npc, agent in w.rec.agents.items() for step in agent.plan))


def _next_boundary(w: World, cursor: int, end: int) -> int:
    from ..engine.state import hours

    due = [end]
    due += [row.t for history in w.rec.conditions.values() for row in history if cursor < row.t <= end]
    due += [int(motion[edge]) for event in w.rec.events for motion in event.motions.values()
            for edge in ("start", "end") if cursor < int(motion[edge]) <= end]
    due += [s.at for s in w.rec.scheduled.values() if s.status == "pending" and cursor < s.at <= end]
    due += [p.due for p in w.rec.promises.values()
            if p.status == "active" and p.due is not None and cursor < p.due <= end]
    for npc, agent in w.rec.agents.items():
        if not _current(w, agent):
            continue
        for step in agent.plan:
            if step.status != "pending" or agents.meeting(w, npc, step):
                continue
            if step.due is not None:
                due += [t for t in (step.due, step.due + GRACE) if cursor < t <= end]
            for group in step.when.split("|"):
                for kind, value, _negated in agents.condition_atoms(group):
                    if kind == "after" and value.isdecimal() and cursor < int(value) <= end:
                        due.append(int(value))
                    if kind == "hour" and (span := hours(value)) is not None:
                        for hour in span:
                            boundary = cursor // 1440 * 1440 + hour % 24 * 60
                            if boundary <= cursor:
                                boundary += 1440
                            if boundary <= end:
                                due.append(boundary)
    return min(due)


async def advance(w: World, start: int, end: int, *, tick: str = "", events_only: bool = False,
                  moved_on: bool = False, checkpoint: Callable[[], None] | None = None,
                  held: Collection[str] = ()) -> Report:
    """Execute fixed work and collect durable keeper notes, with no independent NPC model."""
    if end < start:
        raise ValueError("世界时间不能倒退")
    tick = tick or f"tick:{start}:{end}"
    if tick in w.rec.ticks:
        return Report(tick=tick, repeat=True, pending=[need.id for need in pending(w)])
    work = w.rec.schedule_work.setdefault(tick, {"turn": w.st.turn, "start": start, "end": end,
                                                "since": w.rec.seq, "cursor": None, "report": asdict(Report(tick))})
    report = Report.restored(work["report"])
    cursor = work["cursor"]
    try:
        while cursor is None or cursor < end:
            c = start if cursor is None else _next_boundary(w, cursor, end)
            w.clock = c
            # A fixed operation can make another condition true or schedule work for right now.
            while True:
                before = w.rec.seq
                _prepare(w, c, report, planning=moved_on or not events_only, held=held)
                if before == w.rec.seq:
                    break
            report.chunks += 1
            report.pending = [need.id for need in pending(w)]
            work["cursor"], work["report"] = c, asdict(report)
            cursor = c
            if checkpoint:
                checkpoint()
    finally:
        w.clock = None
    w.rec.ticks.append(tick)
    del w.rec.ticks[:-200]
    w.rec.schedule_work.pop(tick, None)
    if checkpoint:
        checkpoint()
    return report


def _prepare(w: World, c: int, report: Report, *, planning: bool, held: Collection[str]) -> None:
    for sch in sorted((s for s in w.rec.scheduled.values() if s.status == "pending" and s.at <= c), key=lambda s: s.at):
        who = sch.actor if sch.actor in w.rec.agents else ""
        source = f"scheduled:{sch.id}"
        if moved := fresh(w, sch.reads):
            sch.status = "dropped"
            report.dropped.append(sch.id)
            _notice(w, source, who, "stale", f"原先安排的事落空了：{sch.text}（{'、'.join(moved)} 变了）",
                    people=sch.participants, evidence={ref: w.version(ref) for ref in sch.reads})
            continue
        busy = {p: why for p in sch.participants if p in w.rec.entities and w.place(p) != sch.place
                and (why := stage.committed(w, p))}
        people = [p for p in sch.participants if p in w.rec.entities and p not in busy]
        for person, why in busy.items():
            _notice(w, f"{source}:busy:{person}", who, "blocked", f"安排「{sch.text}」缺席：{why}", people=[person])
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
            _notice(w, source, who, "blocked", f"原先安排无法执行：{sch.text}", people=sch.participants)
            continue
        sch.status = "done"
        report.fired.append(sch.id)
    for npc, agent in w.rec.agents.items():
        if not _current(w, agent) or w.rec.entities[npc].status == "死亡" or stage.waiting(w, npc):
            continue
        _steps(w, npc, agent, c, report)
    collect(w, moved_on=planning, held=held)


def _steps(w: World, npc: str, agent: Agent, c: int, report: Report) -> None:
    for step in agent.plan:
        if step.status != "pending" or (step.due is not None and step.due > c) or agents.meeting(w, npc, step):
            continue
        source = f"step:{npc}:{step.id}:{step.started}"
        problems = agents.condition_problems(w, step.when) + agents.condition_problems(w, step.achieved)
        evidence = {ref: w.version(ref) for ref in agent.reads}
        if problems:
            note = "；".join(problems)
            w.apply(AdvancePlan(id=f"{source}:invalid", npc=npc, step=step.id, status="blocked", note=note, actor=npc))
            report.blocked.append(f"{npc}:{step.id}")
            _notice(w, source, npc, "blocked", f"计划条件无效：{step.text}（{note}）", evidence=evidence)
        elif agents.holds(w, step.when):
            moved = fresh(w, agent.reads)
            if step.ops and not moved:
                ops = [*_outstanding(w, step.ops), {"kind": "step", "npc": npc, "step": step.id, "status": "done"}]
                mark = len(report.refused)
                if not _land(w, ops, prefix=f"{npc}:{step.id}:{step.started}", actor=npc, report=report,
                             text=step.text, reads=agent.reads, when=step.when):
                    why = "；".join(report.refused[mark:])
                    _notice(w, source, npc, "blocked", f"计划无法执行：{step.text}（{why}）", evidence=evidence)
                    if f"{npc}:{step.id}" not in report.blocked:
                        report.blocked.append(f"{npc}:{step.id}")
        elif step.due is not None and c - step.due >= GRACE:
            w.apply(AdvancePlan(id=f"{source}:blocked", npc=npc, step=step.id, status="blocked",
                                note=f"条件未满足：{step.when}", actor=npc))
            report.blocked.append(f"{npc}:{step.id}")
            _notice(w, source, npc, "blocked", f"计划受阻：{step.text}（条件未满足：{step.when}）", evidence=evidence)


def _outstanding(w: World, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [op for op in ops if not (op.get("kind") == "move" and op.get("entity") in w.rec.entities
                                     and w.place(op["entity"]) == op.get("to"))]
