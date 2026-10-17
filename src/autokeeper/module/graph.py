"""Module knowledge graph.

The structured part of GraphRAG: locations, NPCs, clues, conclusions, handouts and
events become a NetworkX graph. The keeper asks it what can be found nearby, the
validator checks reachability and clue redundancy, and each NPC only ever sees its
own neighbourhood of the graph.
"""

from __future__ import annotations

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
    for loc in m.locations:
        for ex in loc.exits:
            if ex not in locs:
                err(f"地点 {loc.id} 的出口 {ex} 不存在")
        if loc.art and loc.art not in m.art:
            warn(f"地点 {loc.id} 的插图 {loc.art} 还没画")
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
        if e.when.startswith("visit:") and e.when[6:] not in locs:
            err(f"事件 {e.id} 的触发地点 {e.when[6:]} 不存在")
        if e.when.startswith("flag:") and e.when[5:] not in m.flags:
            err(f"事件 {e.id} 的触发标记 {e.when[5:]} 未声明")
    for end in m.endings:
        if end.condition.startswith("flag:") and end.condition[5:] not in m.flags:
            err(f"结局 {end.id} 依赖未声明的标记 {end.condition[5:]}")

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
