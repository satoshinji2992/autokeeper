"""Write the director's improvisations into this game's copy of the module.

A plan (agents/director.Improv) becomes module objects, is checked against the module
(references, limits, what may be handed over right now), and is merged in place, so
everything that reads the module (arbiter, keeper, NPCs, map) sees it at once. The
plans live in GameState.improv and are applied again whenever the game is loaded; the
module on disk never changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import NPC, Check, Clue, Ending, Event, Item, Location, Module

LIMITS = {  # npcs, locations, clues, events, endings
    "开局改编": (2, 1, 3, 3, 1),
    "default": (1, 1, 2, 1, 1),
}


@dataclass
class Applied:
    now: list[str] = field(default_factory=list)  # flags that fire this turn's events
    go: str = ""  # where the investigators end up this turn
    give: list[str] = field(default_factory=list)  # clues handed over this turn
    note: str = ""
    summary: str = ""  # player-safe: names of new people and places only


def _now_flag(event_id: str) -> str:
    return f"now_{event_id}"


def check(m: Module, plan: Any, *, at: str) -> list[str]:
    """What is wrong with a plan, in words the director can fix; empty when it can go in."""
    problems: list[str] = []
    from ..plugins.schema import check as check_plugin

    for plugin in plan.plugins:
        problems += [f"插件 {plugin.id}：{problem}" for problem in check_plugin(plugin)]
        if existing := m.extensions.get(plugin.id):
            if tuple(map(int, plugin.version.split("."))) <= tuple(map(int, existing.version.split("."))):
                problems.append(f"更新插件 {plugin.id} 必须增加 version，并兼容已有 state")
        elif len(m.plugins) >= 8:
            problems.append("每局最多 8 个插件")
    limit = LIMITS.get(plan.kind, LIMITS["default"])
    for name, items, cap in zip(("NPC", "地点", "线索", "事件", "结局"), (plan.npcs, plan.locations, plan.clues, plan.events, plan.endings), limit, strict=True):
        if len(items) > cap:
            problems.append(f"新{name}最多 {cap} 个，这次有 {len(items)} 个")
    if len(plan.items) > 3:
        problems.append("新物件最多 3 件")
    taken = {x.id for group in (m.locations, m.npcs, m.clues, m.events, m.endings, m.items) for x in group}
    new_ids = [x.id for group in (plan.locations, plan.npcs, plan.clues, plan.events, plan.endings, plan.items) for x in group]
    for i in new_ids:
        if i in taken:
            problems.append(f"id {i} 已经存在")
    if len(set(new_ids)) != len(new_ids):
        problems.append("这次方案里有重复的 id")
    locs = {x.id for x in m.locations} | {x.id for x in plan.locations}
    npcs = {x.id for x in m.npcs} | {x.id for x in plan.npcs}
    flags = set(m.flags) | {f.id for f in plan.flags}
    flags |= {key for plugin in plan.plugins for key in plugin.flags}
    endings = {x.id for x in m.endings} | {x.id for x in plan.endings}
    conclusions = {x.id for x in m.conclusions}
    for loc in plan.locations:
        if not loc.exits:
            problems.append(f"新地点 {loc.id} 没有和任何地点相连")
        problems += [f"新地点 {loc.id} 的出口 {e} 不存在" for e in loc.exits if e not in locs]
    for n in plan.npcs:
        if n.location not in locs:
            problems.append(f"新 NPC {n.id} 所在地点 {n.location} 不存在")
    for item in plan.items:
        if item.where not in locs:
            problems.append(f"新物件 {item.id} 所在地点 {item.where} 不存在")
        if item.owner and item.owner not in npcs:
            problems.append(f"新物件 {item.id} 的持有者 {item.owner} 不存在")
    for c in plan.clues:
        if c.where not in locs:
            problems.append(f"新线索 {c.id} 所在地点 {c.where} 不存在")
        if c.npc and c.npc not in npcs:
            problems.append(f"新线索 {c.id} 的提供者 {c.npc} 不存在")
        problems += [f"新线索 {c.id} 指向的结论 {k} 不存在" for k in c.points_to if k not in conclusions]
    for e in plan.events:
        when = e.when.strip()
        if when.startswith("flag:") and when[5:] not in flags:
            problems.append(f"事件 {e.id} 的触发标记 {when[5:]} 没有声明")
        elif when.startswith("visit:") and when[6:] not in locs:
            problems.append(f"事件 {e.id} 的触发地点 {when[6:]} 不存在")
        elif when != "now" and not when.startswith(("flag:", "visit:")) and not when.startswith("D"):
            problems.append(f"事件 {e.id} 的 when 写法不对：{when}")
        problems += [f"事件 {e.id} 设置的标记 {f} 没有声明" for f in e.sets if f not in flags]
        if e.move_to and e.move_to not in locs:
            problems.append(f"事件 {e.id} 的地点 {e.move_to} 不存在")
        if e.performer and e.performer not in npcs:
            problems.append(f"事件 {e.id} 的表演者 {e.performer} 不存在")
        if e.ending and e.ending not in endings:
            problems.append(f"事件 {e.id} 的结局 {e.ending} 不存在")
    problems += [f"角色弧光里的 NPC {a.npc} 不存在" for a in plan.arcs if a.npc not in npcs]
    for end in plan.endings:
        if not end.condition.startswith("flag:") or end.condition[5:] not in flags:
            problems.append(f"结局 {end.id} 的条件要写成 flag:已声明的标记，现在是 {end.condition}")
    if plan.go and plan.go not in locs:
        problems.append(f"go 的地点 {plan.go} 不存在")
    there = plan.go or at
    new_clues = {c.id: c for c in plan.clues}
    for cid in plan.give:
        c = new_clues.get(cid)
        if c is None:
            problems.append(f"give 里的 {cid} 不是这次新建的线索")
        elif c.where != there:
            problems.append(f"give 里的 {cid} 不在调查员这一轮所在的地点 {there}")
    return problems


def apply(m: Module, plan: Any) -> Applied:
    """Merge a checked plan into the module in place. Ids already present are skipped, so
    applying the same plans again after a load is harmless."""
    have = {x.id for group in (m.locations, m.npcs, m.clues, m.events, m.endings, m.items) for x in group}
    out = Applied(note=plan.note)
    for plugin in plan.plugins:
        if plugin.id not in m.plugins:
            m.plugins.append(plugin.id)
        m.extensions[plugin.id] = plugin
        for key, meaning in plugin.flags.items():
            m.flags.setdefault(key, meaning)
    for f in plan.flags:
        m.flags.setdefault(f.id, f.meaning)
    for loc in plan.locations:
        if loc.id in have:
            continue
        m.locations.append(
            Location(id=loc.id, name=loc.name, read_aloud=loc.read_aloud, keeper=loc.keeper, features=loc.features,
                     exits=list(loc.exits), art=visual_key("scene", loc.id))
        )  # fmt: skip
        for e in loc.exits:
            other = next((x for x in m.locations if x.id == e), None)
            if other is not None and loc.id not in other.exits:
                other.exits.append(loc.id)
    for n in plan.npcs:
        if n.id in have:
            continue
        m.npcs.append(
            NPC(id=n.id, name=n.name, role=n.role, look=n.look, personality=n.personality, voice=n.voice, goal=n.goal,
                knows=n.knows, secrets=n.secrets, lies=n.lies, location=n.location, portrait=visual_key("portrait", n.id))
        )  # fmt: skip
    for item in plan.items:
        if item.id not in have:
            m.items.append(Item(**item.model_dump(), art=visual_key("item", item.id)))
            loc = m.location(item.where)
            if item.name not in loc.features:
                loc.features.append(item.name)
    for c in plan.clues:
        if c.id in have:
            continue
        gate = Check(skill=c.skill, difficulty=c.difficulty) if c.skill else None
        m.clues.append(
            Clue(id=c.id, title=c.title, text=c.text, where=c.where, feature=c.feature, check=None if c.npc else gate,
                 npc=c.npc or None, points_to=c.points_to)
        )  # fmt: skip
        if c.npc:
            giver = m.npc(c.npc)
            if c.id not in giver.clues:
                giver.clues.append(c.id)
            if gate is not None:
                giver.gates[c.id] = gate
    for end in plan.endings:
        if end.id not in have:
            m.endings.append(Ending(id=end.id, title=end.title, condition=end.condition, text=end.text))
    for e in plan.events:
        when = e.when.strip()
        if when == "now":  # fired this turn through a flag of its own
            when = f"flag:{_now_flag(e.id)}"
            m.flags.setdefault(_now_flag(e.id), f"即兴事件“{e.title}”已经发生")
            out.now.append(_now_flag(e.id))
        if e.id in have:
            continue
        m.events.append(
            Event(id=e.id, title=e.title, when=when, text=e.text or e.read_aloud, read_aloud=e.read_aloud, sets=e.sets,
                  sanity=e.sanity or None, damage=e.damage or None, move_to=e.move_to or None, performer=e.performer or None,
                  performance=e.performance, ending=e.ending or None)
        )  # fmt: skip
    for a in plan.arcs:
        if any(n.id == a.npc for n in m.npcs):
            m.npc(a.npc).arc = a.arc
    out.give, out.go = list(plan.give), plan.go
    shown = [f"新人物 {n.name}" for n in plan.npcs] + [f"新地点 {x.name}" for x in plan.locations]
    out.summary = "、".join(shown)
    return out


def visual_key(kind: str, identity: str) -> str:
    import hashlib

    return f"improv_{kind}_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"


def restore(m: Module, plans: list[dict[str, Any]]) -> None:
    """Apply a saved game's improvisations to a freshly loaded module."""
    from ..agents.director import Improv

    for saved in plans:
        apply(m, Improv.model_validate(saved["plan"]))
