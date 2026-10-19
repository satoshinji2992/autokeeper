"""Module knowledge graph.

The structured part of GraphRAG: locations, NPCs, clues, conclusions, handouts and
events become a NetworkX graph. The keeper asks it what can be found nearby, the
validator checks reachability and clue redundancy, and each NPC only ever sees its
own neighbourhood of the graph.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

import networkx as nx

from .schema import Module


def build(m: Module) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph(module=m.id)
    for loc in m.locations:
        g.add_node(loc.id, kind="location", name=loc.name)
    for h in m.handouts:
        g.add_node(h.id, kind="handout", name=h.title)
    for item in m.items:
        g.add_node(item.id, kind="item", name=item.name)
        g.add_edge(item.id, item.where, rel="at")
    for k in m.conclusions:
        g.add_node(k.id, kind="conclusion", name=k.text)
    for loc in m.locations:
        for ex in loc.exits:
            g.add_edge(loc.id, ex, rel="connects")
    for n in m.npcs:
        g.add_node(n.id, kind="npc", name=n.name)
        if n.location:
            g.add_edge(n.id, n.location, rel="at")
        for c in n.clues:
            g.add_edge(n.id, c, rel="can_tell")
    for c in m.clues:
        g.add_node(c.id, kind="clue", name=c.title)
        g.add_edge(c.id, c.where, rel="found_at")
        if c.npc:
            g.add_edge(c.npc, c.id, rel="can_tell")
        if c.handout:
            g.add_edge(c.id, c.handout, rel="handout")
        for k in c.points_to:
            g.add_edge(c.id, k, rel="points_to")
    for e in m.events:
        g.add_node(e.id, kind="event", name=e.title)
        if e.move_to:
            g.add_edge(e.id, e.move_to, rel="happens_at")
        if e.performer:
            g.add_edge(e.performer, e.id, rel="performs")
    return g


def reachable_locations(m: Module) -> set[str]:
    seen = {m.start}
    queue = deque([m.start])
    ids = {loc.id for loc in m.locations}
    while queue:
        loc = m.location(queue.popleft())
        for ex in loc.exits:
            if ex in ids and ex not in seen:
                seen.add(ex)
                queue.append(ex)
    return seen


def supporting_clues(m: Module) -> dict[str, list[str]]:
    support: dict[str, list[str]] = {k.id: [] for k in m.conclusions}
    for c in m.clues:
        for k in c.points_to:
            support.setdefault(k, []).append(c.id)
    return support


def neighbourhood(m: Module, location: str) -> dict:
    """What the keeper needs at a location: exits, NPCs present, clues and their checks."""
    loc = m.location(location)
    return {
        "location": loc,
        "exits": [m.location(e) for e in loc.exits if any(x.id == e for x in m.locations)],
        "npcs": m.npcs_at(location),
        "clues": m.clues_at(location),
    }


def search(m: Module, query: str, *, hops: int = 1, limit: int = 12) -> list[dict]:
    """Graph retrieval: nodes whose name or id appears in the query, plus their neighbours.

    Returns one fact per node with the relation that led there, so the keeper can
    answer "who knows about X" or "what does this clue point to" without reading
    every reference file.
    """
    g = build(m)
    seeds = [n for n, d in g.nodes(data=True) if n in query or (d.get("name") and str(d["name"])[:12] in query)]
    seen: dict[str, str] = {n: "命中" for n in seeds}
    frontier = list(seeds)
    for _ in range(hops):
        nxt = []
        for node in frontier:
            for _, other, data in g.out_edges(node, data=True):
                if other not in seen:
                    seen[other] = f"{node} -{data['rel']}-> {other}"
                    nxt.append(other)
            for other, _, data in g.in_edges(node, data=True):
                if other not in seen:
                    seen[other] = f"{other} -{data['rel']}-> {node}"
                    nxt.append(other)
        frontier = nxt
    return [
        {"id": n, "kind": g.nodes[n].get("kind", ""), "name": g.nodes[n].get("name", n), "via": why, "text": _describe(m, n, g.nodes[n].get("kind", ""))}
        for n, why in list(seen.items())[:limit]
    ]


def _describe(m: Module, node: str, kind: str) -> str:
    if kind == "clue":
        return m.clue(node).text.strip()
    if kind == "npc":
        return f"{m.npc(node).role}；知道：{'；'.join(m.npc(node).knows)}"
    if kind == "location":
        return m.location(node).read_aloud.strip()[:160]
    if kind == "item":
        return next(i.description for i in m.items if i.id == node)
    if kind == "conclusion":
        return next(k.text for k in m.conclusions if k.id == node)
    if kind == "handout":
        return m.handout(node).text.strip()[:160]
    if kind == "event":
        return next(e.text.strip() for e in m.events if e.id == node)
    return ""


# -- validation ----------------------------------------------------------------
@dataclass
class Issue:
    level: str  # error / warning
    text: str

    def __str__(self) -> str:
        return f"[{'错误' if self.level == 'error' else '提醒'}] {self.text}"


def audit(m: Module) -> list[Issue]:
    issues: list[Issue] = []
    def err(text: str) -> None:
        issues.append(Issue("error", text))

    def warn(text: str) -> None:
        issues.append(Issue("warning", text))

    locs = {x.id for x in m.locations}
    npcs = {x.id for x in m.npcs}
    clues = {x.id for x in m.clues}
    handouts = {x.id for x in m.handouts}
    concl = {x.id for x in m.conclusions}
    endings = {x.id for x in m.endings}

    if m.start not in locs:
        err(f"起始地点 {m.start} 不存在")
    for item in m.items:
        if item.where not in locs:
            err(f"物件 {item.id} 所在地点 {item.where} 不存在")
        if item.owner and item.owner not in npcs | {inv.id for inv in m.pregens}:
            err(f"物件 {item.id} 的持有者 {item.owner} 不存在")
        if item.art and item.art not in m.art:
            warn(f"物件 {item.id} 的插图 {item.art} 还没画")
    for loc in m.locations:
        for target, minutes in loc.travel_minutes.items():
            if target not in loc.exits or not 0 <= minutes <= 720:
                err(f"地点 {loc.id} 的路程 {target} 必须指向出口且耗时在 0–720 分钟内")
            elif target in locs and loc.id in m.location(target).travel_minutes and m.location(target).travel_minutes[loc.id] != minutes:
                err(f"地点 {loc.id} 与 {target} 的双向路程耗时不一致")
        for ex in loc.exits:
            if ex not in locs:
                err(f"地点 {loc.id} 的出口 {ex} 不存在")
        if loc.within is not None:
            outer, seen = loc.within, {loc.id}
            while outer in locs and outer not in seen:
                seen.add(outer)
                outer = m.location(outer).within
            if loc.within not in locs:
                err(f"地点 {loc.id} 所在的 {loc.within} 不存在")
            elif outer == loc.id:
                err(f"地点 {loc.id} 的 within 绕回了它自己")
        if loc.art and loc.art not in m.art:
            warn(f"地点 {loc.id} 的插图 {loc.art} 还没画")
        if len({v.key for v in loc.views}) != len(loc.views):
            err(f"地点 {loc.id} 的几张图用了同一个图片键")
        for v in loc.views:
            if not re.fullmatch(r"[a-z0-9_]+", v.key):
                err(f"地点 {loc.id} 的图 {v.key} 的键只能用小写字母、数字和下划线")
            for f in v.features:
                if f not in loc.features:
                    warn(f"地点 {loc.id} 的图 {v.key} 对着的“{f}”不在它可调查的东西里")
            if v.key not in m.pictures and v.key not in m.art:
                warn(f"地点 {loc.id} 的图 {v.key} 还没有")
    for n in m.npcs:
        if n.location and n.location not in locs:
            err(f"NPC {n.id} 所在地点 {n.location} 不存在")
        for c in n.clues:
            if c not in clues:
                err(f"NPC {n.id} 能给的线索 {c} 不存在")
        for c in n.gates:
            if c not in n.clues:
                err(f"NPC {n.id} 的门槛线索 {c} 不在它能给的线索里")
        if n.portrait and n.portrait not in m.art:
            warn(f"NPC {n.id} 的头像 {n.portrait} 还没画")
    for c in m.clues:
        for requirement in c.requires:
            if requirement.startswith("flag:"):
                if requirement[5:] not in m.flags:
                    err(f"线索 {c.id} 依赖未声明的标记 {requirement[5:]}")
            elif requirement not in clues or requirement == c.id:
                err(f"线索 {c.id} 的前置线索 {requirement} 不存在或指向自己")
        if c.where not in locs:
            err(f"线索 {c.id} 所在地点 {c.where} 不存在")
        if c.npc and c.npc not in npcs:
            err(f"线索 {c.id} 的提供者 {c.npc} 不存在")
        if c.handout and c.handout not in handouts:
            err(f"线索 {c.id} 的手稿 {c.handout} 不存在")
        for k in c.points_to:
            if k not in concl:
                err(f"线索 {c.id} 指向的结论 {k} 不存在")
    for e in m.events:
        if e.move_to and e.move_to not in locs:
            err(f"事件 {e.id} 的地点 {e.move_to} 不存在")
        if e.performer and e.performer not in npcs:
            err(f"事件 {e.id} 的表演者 {e.performer} 不存在")
        if e.ending and e.ending not in endings:
            err(f"事件 {e.id} 的结局 {e.ending} 不存在")
        for f in e.sets:
            if f not in m.flags:
                err(f"事件 {e.id} 设置了未声明的标记 {f}")
        if e.art and e.art not in m.pictures:
            warn(f"事件 {e.id} 的图 {e.art} 还没有")
        if e.when.startswith("visit:") and e.when[6:] not in locs:
            err(f"事件 {e.id} 的触发地点 {e.when[6:]} 不存在")
        if e.when.startswith("flag:") and e.when[5:] not in m.flags:
            err(f"事件 {e.id} 的触发标记 {e.when[5:]} 未声明")
    for end in m.endings:
        if end.condition.startswith("flag:") and end.condition[5:] not in m.flags:
            err(f"结局 {end.id} 依赖未声明的标记 {end.condition[5:]}")
    changing = [(f"地点 {x.id}", x.art, x.looks) for x in m.locations] + [(f"NPC {n.id}", n.portrait, n.looks) for n in m.npcs]
    for who, base, looks in changing:
        for look in looks:
            if not look.when.startswith("flag:") or look.when[5:] not in m.flags:
                err(f"{who} 的变样图条件要写成 flag:已声明的标记，现在是 {look.when}")
            if not base:
                err(f"{who} 有变样图，但自己没有图片键（art 或 portrait）")
            elif not re.fullmatch(r"[a-z0-9_]+", look.key(base)):
                err(f"{who} 的变样图键 {look.key(base)} 只能用小写字母、数字和下划线")
        if len(looks) > 2:
            warn(f"{who} 写了 {len(looks)} 种变样，一般最多两种")
    moments = {"visit": locs, "event": {e.id for e in m.events}, "creature": {c.id for c in m.creatures},
               "flag": set(m.flags), "ending": endings}
    if len(m.showcases) > 15:
        err(f"大场景最多 15 个，现在有 {len(m.showcases)} 个")
    if len({sc.id for sc in m.showcases}) != len(m.showcases):
        err("大场景的 id 有重复")
    for sc in m.showcases:
        side, _, ident = sc.when.partition(":")
        if sc.when != "opening" and ident not in moments.get(side, set()):
            err(f"大场景 {sc.id} 的 when 要写成 opening、visit:地点、event:事件、creature:怪物、flag:标记或 ending:结局，现在是 {sc.when}")
        if sc.art and sc.art not in m.pictures:
            warn(f"大场景 {sc.id} 的图 {sc.art} 不在 art/ 里，会改用生成的图")

    people = npcs | {"players"}
    for bond in m.relations:
        if bond.a not in npcs or bond.b not in people or bond.a == bond.b:
            err(f"人物关系 {bond.a}→{bond.b} 要写两个不同的 NPC（b 也可以是 players）")
    if len({a.npc for a in m.agendas}) != len(m.agendas):
        err("同一个 NPC 写了两份打算")
    for a in m.agendas:
        if a.npc not in npcs:
            err(f"打算里的 NPC {a.npc} 不存在")
        for step in a.steps:
            if step.when.startswith("flag:") and step.when[5:] not in m.flags:
                err(f"{a.npc} 的打算 {step.id} 依赖未声明的标记 {step.when[5:]}")
    if len(m.agendas) > 6:
        warn(f"写了 {len(m.agendas)} 份打算；每局最多 6 个重要 NPC，多出的不会自己行动")
    targets = concl | {e.id for e in m.events} | endings
    for f in m.foreshadow:
        if f.where not in locs:
            err(f"伏笔 {f.id} 的地点 {f.where} 不存在")
        if f.pays_off not in targets:
            err(f"伏笔 {f.id} 指向的 {f.pays_off} 不是结论、事件或结局")
    for t in m.ties:
        if t.npc and t.npc not in npcs:
            err(f"牵连 {t.id} 的 NPC {t.npc} 不存在")
        if t.place and t.place not in locs:
            err(f"牵连 {t.id} 的地点 {t.place} 不存在")
    for mech in m.mechanics:
        if mech.plugin and mech.plugin not in m.plugins:
            err(f"规则「{mech.name}」的插件 {mech.plugin} 不在 plugins 里")

    reach = reachable_locations(m) if m.start in locs else set()
    for loc in m.locations:
        if loc.id not in reach and not any(e.move_to == loc.id for e in m.events):
            warn(f"地点 {loc.id} 从起点走不到")
    for k, support in supporting_clues(m).items():
        if not support:
            err(f"结论 {k} 没有任何线索支持")
        elif len(support) == 1:
            warn(f"结论 {k} 只有一条线索（{support[0]}），建议至少两条")
    for c in m.clues:
        if c.core and c.where not in reach:
            err(f"关键线索 {c.id} 在走不到的地点")
    if not m.pregens:
        warn("没有预设调查员")
    return issues
