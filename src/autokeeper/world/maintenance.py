"""Read-only checks of recorded state, projections and dependencies; never a truth police."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from ..engine import world as legacy_world
from . import agents
from .record import PLAYERS
from .scheduler import fresh
from .store import World


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Literal["error", "warning", "info"]
    ref: str
    title: str
    evidence: tuple[str, ...]


def inspect(w: World) -> dict:
    """Observe cross-layer disagreement without syncing, applying ops or judging testimony."""
    issues: list[Issue] = []
    rec, st, m = w.rec, w.st, w.m

    def add(code: str, severity: str, ref: str, title: str, *evidence: str) -> None:
        issues.append(Issue(code, severity, ref, title, evidence))

    for npc in m.npcs:
        entity = rec.entities.get(npc.id)
        if entity is not None and entity.at != npc.location:
            add("npc_projection", "error", f"entity:{npc.id}", "人物位置与场景投影不一致",
                f"世界位置={entity.at}", f"场景投影={npc.location}")
        if entity is not None and entity.status != npc.status:
            add("npc_status_projection", "warning", f"entity:{npc.id}", "人物状态与场景投影不一致",
                f"世界状态={entity.status}", f"场景投影={npc.status}")

    actor_ids = set(rec.entities) | {i.id for i in st.investigators} | {PLAYERS}
    for item in (e for e in rec.entities.values() if e.kind == "item"):
        module_item = next((i for i in m.items if i.id == item.id), None)
        recorded = legacy_world.owner(m, st, item.id)
        if recorded and recorded != item.at:
            add("item_owner", "error", f"entity:{item.id}", "物品持有记录不一致",
                f"世界持有者={item.at}", f"物品台账持有者={recorded}")
        if item.at and item.at not in actor_ids:
            add("item_holder_missing", "error", f"entity:{item.id}", "物品持有者不存在", f"持有者={item.at}")
        name = module_item.name if module_item else item.name
        holders = [i.id for i in st.investigators if name in i.inventory or item.id in i.inventory]
        if len(holders) > 1:
            add("item_duplicate_inventory", "error", f"entity:{item.id}", "同一物品出现在多个调查员的背包",
                f"背包持有者={'、'.join(holders)}")
        if item.at in {i.id for i in st.investigators} and item.at not in holders and getattr(item, "quantity", 1) > 0:
            add("item_inventory_missing", "warning", f"entity:{item.id}", "物品台账与调查员背包不一致",
                f"世界持有者={item.at}", f"背包缺少={name}")

    for npc, agent in rec.agents.items():
        for step in agent.plan:
            if step.status in {"dropped", "done"}:
                continue
            invalid = agents.condition_problems(w, step.when) + agents.condition_problems(w, step.achieved)
            if invalid:
                add("plan_condition", "warning", f"agent:{npc}/step:{step.id}", "计划条件无法执行", *invalid)
            if step.status == "blocked":
                add("plan_blocked", "info", f"agent:{npc}/step:{step.id}", "计划步骤受阻",
                    step.note or "记录已标记为 blocked；受阻本身不说明存在程序错误")
        if any(s.status == "pending" for s in agent.plan) and (moved := fresh(w, agent.reads)):
            add("plan_stale", "info", f"agent:{npc}", "计划的读取依据已经变化", *moved)

    for fact in rec.facts.values():
        if fact.retracted:
            continue
        if fact.revealed is not None and not fact.reveal_source:
            add("reveal_source_missing", "warning", f"fact:{fact.id}", "公开事实缺少可追溯来源",
                f"标记回合={fact.revealed}", "不能仅凭此标记向玩家补述内容")
        if fact.legacy_revealed is not None:
            add("legacy_reveal_unverified", "warning", f"fact:{fact.id}", "旧公开标记尚待核验",
                f"旧标记回合={fact.legacy_revealed}", fact.reveal_note or "缺少可靠公开来源，保留原记录供核对")
        missing = [actor for actor in fact.known_by if actor not in actor_ids]
        if missing:
            add("knowledge_actor_missing", "warning", f"fact:{fact.id}", "知识记录引用了不存在的人物", *missing)

    for request in rec.requests.values():
        if request.status != "pending":
            continue
        if moved := fresh(w, request.reads):
            add("request_stale", "info", f"request:{request.id}", "后台方案的读取依据已经变化", *moved)
        if st.turn - request.turn > request.ttl:
            add("request_expired", "info", f"request:{request.id}", "后台方案已超过提交期限",
                f"发起回合={request.turn}", f"当前回合={st.turn}", f"有效轮数={request.ttl}")

    for scheduled in rec.scheduled.values():
        if scheduled.status == "pending" and (moved := fresh(w, scheduled.reads)):
            add("schedule_stale", "info", f"scheduled:{scheduled.id}", "定时安排的读取依据已经变化", *moved)

    counts = {severity: sum(issue.severity == severity for issue in issues) for severity in ("error", "warning", "info")}
    return {"read_only": True, "counts": counts, "issues": [asdict(issue) for issue in issues],
            "scope": "核对程序记录及其来源；人物撒谎、误解和传闻不等于程序错误，不自动改写历史或纠正台词。"}
