"""Module knowledge graph.

The structured part of GraphRAG: locations, NPCs, clues, conclusions, handouts and
events become a NetworkX graph. The keeper asks it what can be found nearby, the
validator checks reachability and clue redundancy, and each NPC only ever sees its
own neighbourhood of the graph.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from ..world.record import WorldRecord

from ..engine.encounter import profiles
from ..engine.state import hours
from ..rules import dice_expr
from ..rules.catalog import CHARACTERISTICS, SKILL_BASE, canonical
from ..rules.combat import stats_of
from ..rules.weapons import carried, parse_attack, unread
from .schema import Check, Clue, Event, Leg, Module, Obstacle


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
        if e.where or e.move_to:
            g.add_edge(e.id, e.where or e.move_to, rel="happens_at")
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


def tellers(m: Module, c: Clue, *, record: WorldRecord | None = None) -> list[str]:
    """Current knowers supply testimony in a running game. Without a record, the static module
    graph uses Clue.npc and NPC.clues for compilation and reachability checks."""
    if record is not None:
        from ..world.knowledge import sees

        fact = record.facts.get(f"clue:{c.id}")
        return [n.id for n in m.npcs if fact is not None and sees(fact, n.id)]
    return list(dict.fromkeys([*([c.npc] if c.npc else []), *(n.id for n in m.npcs if c.id in n.clues)]))


def disclosure_gate(m: Module, c: Clue, npc: str) -> Check | None:
    """This speaker's explicit disclosure gate. Clue.check belongs to the original discovery
    method; neither it nor another character's personal conditions follow a learned result."""
    return m.npc(npc).gates.get(c.id)


def original_source_at_hand(m: Module, c: Clue, place: str, present: Iterable[str], *,
                            record: WorldRecord | None = None) -> bool:
    """The original discovery or material route, distinct from a new witness's learned summary."""
    here = set(present)
    if not c.npc and c.where == place:
        return True
    if c.npc in here and c.id not in m.npc(c.npc).clues:
        return True  # something observed of that person, not their testimony
    providers = set(tellers(m, c, record=record))
    return any(n.id in here and n.id in providers and c.id in n.clues for n in m.npcs)


def at_hand(m: Module, place: str, present: Iterable[str], *, record: WorldRecord | None = None) -> list[Clue]:
    """The clues to be had at a place with these people there. One that comes from someone (Clue.npc:
    what they say, or what is seen of them) needs them there, or another who can tell it, wherever
    that is: its `where` is where they are met. One that comes from no one lies where it is found,
    and whoever is there and can tell it may (Game._valid_clues, the arbiter, the keeper's brief)."""
    here = set(present)

    def available(c: Clue) -> bool:
        if record is not None and ((f := record.facts.get(f"clue:{c.id}")) is None or f.retracted):
            return False
        # Clue.npc can name the person being observed, not a willing/knowing teller. Keep that
        # physical source distinct from somebody whose new knowledge supplies testimony.
        observed = c.npc in here and (record is None or c.id not in m.npc(c.npc).clues)
        return (not c.npc and c.where == place) or observed or bool(here.intersection(tellers(m, c, record=record)))

    return [c for c in m.clues if available(c)]


def neighbourhood(m: Module, location: str, present: Iterable[str] | None = None, *, record: WorldRecord | None = None) -> dict:
    """What the keeper needs at a location: exits, who is there (`present`, as the world record has
    them; without it, the module's people of that place who are alive and not gone) and the clues
    to be had with them (at_hand)."""
    loc = m.location(location)
    if present is None:
        people = [n for n in m.npcs_at(location) if n.status not in ("死亡", "失踪")]
    else:
        ids = set(present)
        people = [n for n in m.npcs if n.id in ids]
    return {
        "location": loc,
        "exits": [m.location(e) for e in loc.exits if any(x.id == e for x in m.locations)],
        "npcs": people,
        "clues": at_hand(m, location, [n.id for n in people], record=record),
    }


# Someone on the scene in a read-aloud, with no name: a figure, the one who steps down from the
# truck or sits on the tombstone, a man or a girl, a he or she who speaks.
FIGURE = re.compile(r"人影|身影|黑影|人形|[上下里外旁边]的(?:男|女|年轻|老)?人(?![家们群])|(?:坐|站|蹲|躺)着(?:一|个)[^，。]{0,6}?人"
                    r"|一个[^，。“”]{0,12}?(?:男人|女人|年轻人|老人|姑娘|女孩|男孩)|[他她](?:开口|说[话道：:“])")
SPEECH = re.compile(r"“[^”]*”|「[^」]*」")


def shown(m: Module, e: Event) -> list[str]:
    """Whom an event's read-aloud shows on the scene, by its words alone: the NPCs it names outside
    what is said aloud (full name, the given name before the ·, what unmet players call them), else
    有人 for a figure in it. A cheap guess for check and upgrade (Event.present says who it is)."""
    text = SPEECH.sub("", e.read_aloud)
    named = [n.name for n in m.npcs if any(len(k) >= 2 and k in text for k in (n.name, n.name.split("·")[0], n.called))]
    return named or (["有人"] if FIGURE.search(text) else [])


def brought(m: Module) -> dict[str, set[str]]:
    """Where an NPC is met besides their own place: where an event puts them before the investigators
    at a place it names (Event.present, with its move_to or the place a visit: waits for), where an
    agenda step takes them (does at:him:place)."""
    out: dict[str, set[str]] = {}
    for e in m.events:
        place = e.where or e.move_to or (e.when[6:] if e.when.startswith("visit:") else "")
        for who in e.present if place else ():
            out.setdefault(who, set()).add(place)
    for a in m.agendas:
        for s in a.steps:
            for part in s.does.split("&"):
                kind, _, rest = part.strip().partition(":")
                who, _, place = rest.partition(":")
                if kind == "at" and who == a.npc and place:
                    out.setdefault(who, set()).add(place)
    return out


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


def _outcome(m: Module, npc: str, does: str) -> list[str]:
    """What is wrong with an agenda step's outcome (Move.does): at:him:place, owner:item:holder,
    flag:declared, gone:someone, joined by &."""
    if not does.strip():
        return []
    if "|" in does or "!" in does:
        return ["只用 & 连接，不写“或”和“不”"]
    places, people, items = {x.id for x in m.locations}, {x.id for x in m.npcs}, {x.id for x in m.items}
    out = []
    for part in does.split("&"):
        kind, _, rest = part.strip().partition(":")
        a, _, b = rest.partition(":")
        ok = {"at": a == npc and b in places, "owner": a in items and b in places | people | {"players"},
              "flag": rest in m.flags, "gone": rest in people}.get(kind, False)
        if not ok:
            out.append(f"{part.strip()} 不对（at 只写他自己去哪；地点、物件、人物和标记要已经有）")
    return out


def _waits(m: Module, atoms: list[str], when: str = "") -> list[str]:
    """What is wrong with the people, places, things and facts an agenda step's condition names:
    at:谁:地点, owner:物件:持有者, gone:谁, knows:谁:clue:线索 (a clue someone has: the players once
    they found it)."""
    people = {x.id for x in m.npcs} | {"players"} | {p.id for p in m.pregens}
    places, items, clues = {x.id for x in m.locations}, {x.id for x in m.items}, {x.id for x in m.clues}
    out = []
    timed = re.compile(r"D\d+ \d{1,2}:\d{2}")
    for atom in atoms:
        kind, _, rest = atom.partition(":")
        a, _, b = rest.partition(":")
        if timed.fullmatch(atom) and len(atoms) > 1 and ("|" in when or sum(bool(timed.fullmatch(x)) for x in atoms) > 1):
            out.append(f"{atom} 不对：时间只能单独写，或者用 & 和条件连写（D1 22:00 & flag:x，到点而且条件成立），不能放进 |，也不能写两个")
        elif not timed.fullmatch(atom) and kind not in ("flag", "at", "owner", "gone", "knows", "hour", "after", "promise"):
            out.append(f"{atom} 不对：条件只能是 flag、at、owner、gone、knows、hour、after，或单独一个时间")
        elif kind == "at" and not (a in people and b in places):
            out.append(f"{atom} 不对：at 写已有的人物（或 players）和地点")
        elif kind == "owner" and not (a in items and b in people | places):
            out.append(f"{atom} 不对：owner 写【物件】里的 id 和已有的人物、地点")
        elif kind == "gone" and rest not in people:
            out.append(f"{atom} 不对：gone 写已有的人物")
        elif kind == "knows":
            fact_kind, _, ident = b.partition(":")
            known = (fact_kind == "clue" and ident in clues) or (fact_kind == "npc" and ident.partition(":")[0] in people) \
                or (fact_kind == "location" and ident.partition(":")[0] in places)
            if a not in people or not known:
                out.append(f"{atom} 不对：knows 写谁（或 players）和一条已有的事实，如 knows:players:clue:线索id")
    return out


def event_when_problems(m: Module, when: str) -> list[str]:
    """The event's clock or arrival trigger, or the existing world condition language."""
    if when.startswith("visit:"):
        return [] if when[6:] in {loc.id for loc in m.locations} else [f"触发地点 {when[6:]} 不存在"]
    atoms = [atom.strip().lstrip("!") for group in when.split("|") for atom in group.split("&")]
    out = _waits(m, atoms, when)
    out += [f"触发标记 {atom[5:]} 未声明" for atom in atoms if atom.startswith("flag:") and atom[5:] not in m.flags]
    return out


def event_outcome_problems(m: Module, e: Event, *, actors: set[str] | None = None) -> list[str]:
    """Validate explicit consequences and their source encounter, without interpreting prose."""
    out = []
    people = {n.id for n in m.npcs} | {c.id for c in m.creatures} | {i.id for i in m.pregens} | (actors or set())
    for effect in e.effects:
        if effect.target not in people:
            out.append(f"死亡后果的目标 {effect.target} 不存在")
        if not effect.why.strip():
            out.append(f"死亡后果 {effect.target} 缺少明确依据")
    if gate := e.after_rounds:
        origin = next((event for event in m.events if event.id == gate.event), None)
        if gate.event == e.id:
            out.append("轮次条件不能等待本事件自己开启的遭遇")
        elif origin is None:
            out.append(f"轮次条件的起源事件 {gate.event} 不存在")
        elif not (origin.fight or origin.chase):
            out.append(f"轮次条件的起源事件 {gate.event} 没有开启战斗或追逐")
    return out


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
    met = brought(m)  # where an event or an agenda brings each NPC, besides their own place
    judged = any(e.present for e in m.events)  # someone has said who the events bring: no guessing from the words

    if m.start not in locs:
        err(f"起始地点 {m.start} 不存在")
    for inv in m.pregens:
        for place in inv.known_locations:
            if place not in locs:
                err(f"调查员 {inv.id} 的已知地点 {place} 不存在")
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
        elif c.npc and c.where in locs and c.where != m.npc(c.npc).location and c.where not in met.get(c.npc, set()):
            warn(f"线索 {c.id} 出自 {c.npc}，写在 {c.where}；{c.npc} 平时不在那里，也没有事件的 present 或打算的 does 带去，"
                 "只有本人在场才拿得到。若东西就在那里、本人只是指路或转述，清空 npc")
        if c.handout and c.handout not in handouts:
            err(f"线索 {c.id} 的手稿 {c.handout} 不存在")
        for k in c.points_to:
            if k not in concl:
                err(f"线索 {c.id} 指向的结论 {k} 不存在")
    for e in m.events:
        if e.where and e.where not in locs:
            err(f"事件 {e.id} 的发生地点 {e.where} 不存在")
        if e.move_to and e.move_to not in locs:
            err(f"事件 {e.id} 的地点 {e.move_to} 不存在")
        if e.performer and e.performer not in npcs:
            err(f"事件 {e.id} 的表演者 {e.performer} 不存在")
        for who in e.present:
            if who not in npcs:
                err(f"事件 {e.id} 里出现的 {who} 不是 NPC")
        if not judged and (who := shown(m, e)):
            warn(f"事件 {e.id} 的朗读写到{'、'.join(who)}在调查员眼前，present 是空的：触发时记录里没人到场，"
                 "裁定和叙述都会当作不在。写上在场的 NPC id（upgrade --only present）")
        if e.ending and e.ending not in endings:
            err(f"事件 {e.id} 的结局 {e.ending} 不存在")
        for f in e.sets:
            if f not in m.flags:
                err(f"事件 {e.id} 设置了未声明的标记 {f}")
        if e.art and e.art not in m.pictures:
            warn(f"事件 {e.id} 的图 {e.art} 还没有")
        for problem in ([] if not e.when.strip() and e.after_rounds else event_when_problems(m, e.when)):
            err(f"事件 {e.id} 的条件 {problem}")
        for problem in event_outcome_problems(m, e):
            err(f"事件 {e.id} 的后果 {problem}")
    for end in m.endings:
        if end.condition.startswith("flag:") and end.condition[5:] not in m.flags:
            err(f"结局 {end.id} 依赖未声明的标记 {end.condition[5:]}")
        for required in end.requires:
            if (required[5:] not in m.flags) if required.startswith("flag:") else (required not in clues):
                err(f"结局 {end.id} 的完成前置 {required} 不存在")
        if end.sanity_gain and (why := dice_expr.problem(end.sanity_gain)):
            err(f"结局 {end.id} 的理智恢复式不合法：{why}")
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
    staged = m.stage_flags()  # a step of foreshadowing changes a place's picture, drawn as the story gets near (engine/visuals.py)
    for n in m.npcs:
        for look in n.looks:
            if look.when.removeprefix("flag:") in staged:
                err(f"NPC {n.id} 的变样图挂在伏笔步骤的标记 {look.when[5:]} 上：伏笔的变化换的是地点的场景图，写在那个地点的 looks 里")
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
            atoms = [x.strip().lstrip("!") for group in step.when.split("|") for x in group.split("&") if x.strip()]
            for flag in (x[5:] for x in atoms if x.startswith("flag:")):
                if flag not in m.flags:
                    err(f"{a.npc} 的打算 {step.id} 依赖未声明的标记 {flag}")
            for span in (x[5:] for x in atoms if x.startswith("hour:")):
                if hours(span) is None:
                    err(f"{a.npc} 的打算 {step.id} 的时段 hour:{span} 要写成 hour:22-6 这样")
            for problem in _waits(m, atoms, step.when):
                err(f"{a.npc} 的打算 {step.id} 的条件 {problem}")
            if step.once is not None and not step.meet:
                warn(f"{a.npc} 的打算 {step.id} 不是见面反应，once 不起作用")
            for problem in _outcome(m, a.npc, step.does):
                err(f"{a.npc} 的打算 {step.id} 的结果 {problem}")
    for i, past in enumerate(m.history, 1):
        if missing := [x for x in [*past.who, *past.known_by] if x not in npcs]:
            err(f"前史第 {i} 件里的人物 {'、'.join(missing)} 不存在")
        if past.where and past.where not in locs:
            err(f"前史第 {i} 件的地点 {past.where} 不存在")
    targets = concl | {e.id for e in m.events} | endings
    for f in m.foreshadow:
        if f.where not in locs:
            err(f"伏笔 {f.id} 的地点 {f.where} 不存在")
        if f.pays_off not in targets:
            err(f"伏笔 {f.id} 指向的 {f.pays_off} 不是结论、事件或结局")
        for n, s in enumerate(f.stages, 1):  # engine/signs.py
            if s.where and s.where not in locs:
                err(f"伏笔 {f.id} 第 {n} 步的地点 {s.where} 不存在")
            if s.flag and s.flag not in m.flags:
                err(f"伏笔 {f.id} 第 {n} 步设的标记 {s.flag} 未声明")
            when = s.when.strip()
            if when and not (re.fullmatch(r"D\d+ \d{1,2}:\d{2}", when) or (when.startswith("flag:") and when[5:] in m.flags)):
                err(f"伏笔 {f.id} 第 {n} 步的 when 要写成 'D2 08:00' 这样的时间或 flag:已声明的标记，现在是 {s.when}")
            if not s.hint.strip():
                err(f"伏笔 {f.id} 第 {n} 步没有写 hint")
    for t in m.ties:
        if t.npc and t.npc not in npcs:
            err(f"牵连 {t.id} 的 NPC {t.npc} 不存在")
        if t.place and t.place not in locs:
            err(f"牵连 {t.id} 的地点 {t.place} 不存在")
    for mech in m.mechanics:
        if mech.plugin and mech.plugin not in m.plugins:
            err(f"规则「{mech.name}」的插件 {mech.plugin} 不在 plugins 里")
    issues += fighting(m)
    issues += annotated(m)

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
    from ..engine import fog  # how the map reads names (it imports this package)

    told = fog.mentions(m, [m.opening]) | fog.landmarks(m)
    for pid, name in fog.mentions(m, [m.title, m.subtitle, m.hook, m.setting]).items():
        if pid != m.start and pid not in told and not m.location(pid).public:
            warn(f"标题、副标题、缘起或地点说明提到了「{name}」，地点 {pid} 开局就会出现在地图上；要调查才找得到的地方，这几处别点名")
    return issues


# -- what a fight and a chase read of it ---------------------------------------------------------
HARM = re.compile(r"\d*[dD]\d+(?:[+\-]\d+)?|\d+")  # kept for old callers; dice_expr is what reads a harm now
SLUG = re.compile(r"[a-z0-9_]+")
# A barrier the rulebook's table gives hit points for (p. 116): a door, a fence, a wall, a tree.
BREAKABLE = re.compile(r"门|围栏|栅栏|篱|墙|木板|树|路障|街垒")
# What an encounter's stake may say beyond the world's conditions (engine/encounter's stakes):
# who went down, died, got away, was caught or gave up, and how the whole of it ended.
OUTCOMES = ("down", "dead", "escaped", "caught", "surrendered")
ENDS = ("won", "lost", "fled", "escaped", "caught", "parley", "surrender", "dispersed")


def known_skill(name: str) -> bool:
    """A characteristic or a skill the rules know (rules/catalog), a specialty such as 外语(拉丁文) too."""
    key = canonical(name)
    return key in CHARACTERISTICS or key in SKILL_BASE or key.split("(")[0] in SKILL_BASE or key in ("幸运", "理智")


def leg_key(leg: Leg) -> str:
    """What a leg is known by: its id, or its name when it has none (Leg.links names either)."""
    return leg.id or leg.name


def route_edges(legs: list[Leg], names: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """A route as the pairs of legs it joins, by leg_key: each leg to the one listed after it, unless it
    is an exit (an exit ends its branch, the next leg starts another), and to each of its links. A link
    names a leg of this route or of another place's (by id or name): the site merges them."""
    out: list[tuple[str, str]] = []
    names = {**(names or {}), **{leg.name: leg_key(leg) for leg in legs}}
    for leg, after in pairwise(legs):
        if not leg.exit:
            out.append((leg_key(leg), leg_key(after)))
    for leg in legs:
        out += [(leg_key(leg), names.get(x, x)) for x in leg.links]
    return out


def ways(m: Module) -> dict[str, Leg]:
    """Every leg of every route by its key and by its name: what a link may name."""
    out: dict[str, Leg] = {}
    for loc in m.locations:
        for leg in loc.chase:
            out.setdefault(leg_key(leg), leg)
            out.setdefault(leg.name, leg)
    return out


def stake_problems(m: Module, when: str, *, actors: set[str] | None = None) -> list[str]:
    """What is wrong with a stake's condition (Event.stakes): the world's conditions (flag, at, gone,
    owner, knows) and the encounter's own: down/dead/escaped/caught/surrendered:谁 (an NPC, a
    creature, a pregen, or players for the investigators' side) and ended:结局."""
    if not when.strip():
        return ["条件是空的"]
    people = {x.id for x in m.npcs} | {c.id for c in m.creatures} | {p.id for p in m.pregens} | {"players"} | (actors or set())
    atoms = [x.strip().lstrip("!") for group in when.split("|") for x in group.split("&") if x.strip()]
    out: list[str] = []
    for atom in atoms:
        kind, _, rest = atom.partition(":")
        if kind in OUTCOMES:
            if rest not in people:
                out.append(f"{atom} 不对：{kind} 写这场遭遇里的 NPC、怪物、预设调查员的 id，或 players（调查员一方）")
        elif kind == "ended":
            if rest not in ENDS:
                out.append(f"{atom} 不对：ended 只能是 {'、'.join(ENDS)}")
        elif kind == "flag" and rest not in m.flags:
            out.append(f"{atom} 不对：标记 {rest} 没有声明")
        elif re.fullmatch(r"D\d+ \d{1,2}:\d{2}", atom):
            out.append(f"{atom} 不对：胜负后果看遭遇怎么收场，不写时间")
        else:
            out += _waits(m, [atom])
    return out


def annotated(m: Module) -> list[Issue]:
    """Names of people and creatures with a note in brackets, as “埃里”（异界存在）: the note is the
    keeper's, never shown to the players (engine/encounter/profiles.shown_name leaves it out), and a
    name the keeper reads with it is easily told aloud; it belongs in the description or the role."""
    out = []
    for c in m.creatures:
        if profiles.ANNOTATION.search(c.name):
            out.append(Issue("warning", f"怪物 {c.id} 的名字「{c.name}」带括号注释：注释不会显示给玩家，写进 description"))
    for n in m.npcs:
        if profiles.ANNOTATION.search(n.name):
            out.append(Issue("warning", f"NPC {n.id} 的名字「{n.name}」带括号注释：注释不会显示给玩家，写进 role"))
    return out


def fighting(m: Module) -> list[Issue]:
    """What a fight or a chase reads of the module and cannot use as written (engine/encounter): a creature with no word for the players (Creature.called) or one that names it,
    a sheet without hit points or DEX, an attack line with no chance to hit, a gun skill with no gun,
    a chase route the rules cannot run, an event charging the first sight of a horror twice. What
    names someone, somewhere or a flag the module does not have is an error (a creature's NPC, a leg's
    place or link, an event's people, stakes and sights); the rest still plays, on the rules' defaults."""
    out: list[Issue] = []

    def warn(text: str) -> None:
        out.append(Issue("warning", text))

    def err(text: str) -> None:
        out.append(Issue("error", text))

    npcs, locs = {n.id: n for n in m.npcs}, {x.id for x in m.locations}
    forms: dict[str, str] = {}  # NPC -> the creature sheet that is its fighting form
    for c in m.creatures:
        if not c.called.strip():
            warn(f"怪物 {c.id} 没写 called：调查员还不知道它是什么时，屏幕上就是它的名字「{c.name}」")
        elif c.name in c.called:
            warn(f"怪物 {c.id} 的 called「{c.called}」带着它的名字：只写看得见的样子，不写学名、真名和真相")
        swarm = bool(profiles.SWARM.search(c.notes + c.description)) and not c.stats
        s = stats_of(c.stats)
        if not swarm and (lacking := [k for k in ("生命", "敏捷") if k not in s]):
            warn(f"怪物 {c.id} 的 stats 缺{'、'.join(lacking)}：打起来生命按体质、体型算（没写的各 60），敏捷按 50")
        for line in c.attacks:
            if problem := unread(line):
                warn(f"怪物 {c.id} 的攻击「{line.strip()[:40]}」{problem}")
        natural = any(w in k for k in s for w in profiles.NATURAL)
        if not swarm and not natural and not any(parse_attack(x) for x in c.attacks):
            warn(f"怪物 {c.id} 没有读得出的攻击：打起来按 45% 的“攻击”、伤害 1D6+DB 算")
        if c.npc:
            if c.npc not in npcs:
                err(f"怪物 {c.id} 的 npc {c.npc} 不是模组里的 NPC")
            elif c.npc in forms:
                err(f"NPC {c.npc} 有两张怪物卡（{forms[c.npc]}、{c.id}）：一个人只有一种打起来的样子")
            else:
                forms[c.npc] = c.id
                own, its = stats_of(npcs[c.npc].stats).get("生命"), s.get("生命")
                if own and its and own != its:
                    warn(f"怪物 {c.id} 是 NPC {c.npc} 打起来的样子，两张卡的生命不一样（{its}、{own}）：同一个人只有一条命，打起来按怪物卡")
        if c.pursues and re.search(r"不追", c.notes):
            warn(f"怪物 {c.id} 写了 pursues: true，notes 里却写着不追")
    for n in m.npcs:
        guns = [k for k in stats_of(n.stats) if k.startswith("射击")]
        armed = any(w.firearm for w in carried([n.look, *(i.name for i in m.items if i.owner == n.id)]))
        if guns and not armed and not any((w := parse_attack(x)) and w.firearm for x in n.attacks):
            warn(f"NPC {n.id} 会{'、'.join(guns)}，look、items 和 attacks 里却没有枪：打起来只用拳脚")
        for line in n.attacks:
            if problem := unread(line):
                warn(f"NPC {n.id} 的攻击「{line.strip()[:40]}」{problem}")
        names = [n.name, *(x for x in re.split(r"[·・\s]", n.name) if len(x) >= 2)]
        if n.called and (hit := next((x for x in names if x in n.called), None)):
            warn(f"NPC {n.id} 的 called「{n.called}」带着他的名字「{hit}」：只写调查员看得见的样子")
    out += _routes(m, locs)
    people = {*npcs, *(c.id for c in m.creatures)}
    creatures = {c.id: c for c in m.creatures}
    for e in m.events:
        for key, ids in (("fight", e.fight), ("chase", e.chase)):
            for x in ids:
                if x not in people:
                    err(f"事件 {e.id} 的 {key} 里的 {x} 不是 NPC 或怪物")
            for x in ids:
                if (c := creatures.get(x)) is not None and c.npc in ids:
                    warn(f"事件 {e.id} 的 {key} 同时写了 {c.npc} 和他打起来的样子 {c.id}：是同一个人，写一个就够")
        if e.fight and e.chase:
            warn(f"事件 {e.id} 同时写了 fight 和 chase：只按 fight 打起来，调查员逃跑时会追的对手自己会追")
        if (e.fight or e.chase) and e.ending:
            warn(f"事件 {e.id} 直接进入结局，fight 和 chase 不会开始")
        if e.surprise and not e.fight:
            warn(f"事件 {e.id} 的 surprise 只在写了 fight 时起作用")
        if e.chase_party and not e.chase:
            warn(f"事件 {e.id} 的 chase_party 只在写了 chase 时起作用")
        if e.stakes and not (e.fight or e.chase):
            warn(f"事件 {e.id} 写了 stakes，却没有 fight 或 chase：没有遭遇，胜负后果不会算")
        for i, stake in enumerate(e.stakes, 1):
            for problem in stake_problems(m, stake.when):
                err(f"事件 {e.id} 的第 {i} 条胜负后果 {problem}")
            for flag in stake.flags:
                if flag not in m.flags:
                    err(f"事件 {e.id} 的第 {i} 条胜负后果设了未声明的标记 {flag}")
            if not stake.flags:
                warn(f"事件 {e.id} 的第 {i} 条胜负后果没有 flags：成立了也什么都不设")
        for x in e.sights:
            if x not in creatures:
                err(f"事件 {e.id} 的 sights 里的 {x} 不是怪物")
        if e.sights and not e.sanity:
            warn(f"事件 {e.id} 写了 sights，自己却没有 sanity：照面的理智就谁也不扣了")
        twice = [x for x in e.fight + e.chase if (c := creatures.get(x)) is not None and c.sanity and x not in e.sights]
        if e.sanity and twice:
            warn(f"事件 {e.id} 有自己的理智检定（{e.sanity}），打起来的 {'、'.join(twice)} 照面还要再扣一次："
                 "这次检定就是照面的，把它们写进 sights")
    return out


def _routes(m: Module, locs: set[str]) -> list[Issue]:
    """What is wrong with the chase routes (Location.chase): the rules' (4–12 legs, an obstacle's skill,
    methods and harm, a barrier's hit points), and the graph's (a leg's place and links, an id written two
    ways, a leg nothing reaches)."""
    out: list[Issue] = []
    known = ways(m)
    seen: dict[str, tuple[str, Leg]] = {}  # leg id -> the first place's leg that wrote it
    edges: list[tuple[str, str]] = []
    for loc in m.locations:
        if loc.chase and not 4 <= len(loc.chase) <= 12:
            out.append(Issue("warning", f"地点 {loc.id} 的追逐路线有 {len(loc.chase)} 段，要 4–12 段：引擎不用它，临场另画"))
        ids = [leg.id for leg in loc.chase if leg.id]
        for x in sorted({x for x in ids if ids.count(x) > 1}):
            out.append(Issue("error", f"地点 {loc.id} 的追逐路线里有两段都叫 {x}"))
        edges += route_edges(loc.chase, {name: leg_key(leg) for name, leg in known.items()})
        for i, leg in enumerate(loc.chase, 1):
            where = f"地点 {loc.id} 追逐路线第 {i} 段「{leg.name}」"
            if leg.id and not SLUG.fullmatch(leg.id):
                out.append(Issue("warning", f"{where}的 id「{leg.id}」只用小写字母、数字和下划线"))
            elif leg.id in locs:
                out.append(Issue("warning", f"{where}的 id「{leg.id}」和一个地点的 id 一样：换一个，它在哪个地点写进 place"))
            if leg.place and leg.place not in locs:
                out.append(Issue("error", f"{where}的 place {leg.place} 不是模组里的地点"))
            for x in leg.links:
                if x not in known:
                    out.append(Issue("error", f"{where}的 links 里的「{x}」不是哪条路线里的段（写段的 id 或名字）"))
                elif x in (leg.id, leg.name):
                    out.append(Issue("warning", f"{where}的 links 连到了它自己"))
            if leg.id and (first := seen.get(leg.id)) is not None and first[1].model_dump(exclude={"links"}) != leg.model_dump(exclude={"links"}):
                out.append(Issue("warning", f"{where}和地点 {first[0]} 的同一段 {leg.id} 写得不一样（名字、样子、地点、障碍或出口）："
                                            "几条路线合在一起时它是同一处，写法要一致"))
            elif leg.id:
                seen.setdefault(leg.id, (loc.id, leg))
            if leg.obstacle is not None:
                out += _obstacle(where, leg.obstacle)
    # A leg cut off from its route's start, when the routes are put together: an exit's next leg that no link names.
    graph: nx.Graph = nx.Graph()
    graph.add_edges_from(edges)
    for loc in m.locations:
        if not 4 <= len(loc.chase) <= 12:
            continue
        start = leg_key(loc.chase[0])
        graph.add_node(start)
        reach = nx.node_connected_component(graph, start)
        for leg in loc.chase:
            if leg_key(leg) not in reach:
                out.append(Issue("warning", f"地点 {loc.id} 追逐路线的「{leg.name}」从起点跑不到：它前一段是出口，"
                                            "出口后面的段要由别的段的 links 连过来"))
    return out


def _obstacle(where: str, ob: Obstacle) -> list[Issue]:
    out = []

    def warn(text: str) -> None:
        out.append(Issue("warning", f"{where}的{ob.what}{text}"))

    if not known_skill(ob.skill):
        warn(f"用「{ob.skill}」，不是技能或属性名（跳跃、攀爬、敏捷、力量、闪避、锁匠、游泳……）")
    if ob.harm and (why := dice_expr.problem(ob.harm)):
        warn(f"的伤害「{ob.harm}」用不了：{why}")
    ids = [x.id for x in ob.methods]
    for x in ob.methods:
        if not known_skill(x.skill):
            warn(f"的办法「{x.label}」用「{x.skill}」，不是技能或属性名")
        if not SLUG.fullmatch(x.id) or ids.count(x.id) > 1:
            warn(f"的办法 id「{x.id}」要用小写字母、数字和下划线，一处障碍里不重复")
        if x.opens and ob.kind == "hazard":
            warn(f"是险阻，办法「{x.label}」的 opens 不起作用：险阻过了就过了，只有路障会被打开")
    if ob.kind == "hazard" and ob.hp:
        warn("是险阻，hp 不起作用：只有路障能被打破")
    if ob.kind == "barrier" and ob.hp is None and BREAKABLE.search(ob.what) and not any(x.opens for x in ob.methods):
        warn("是路障，没写 hp，也没有能把它打开的办法：照规则书写 hp（室内门或薄围栏 5，普通后门 10，坚固的外门 15，"
             "9 寸砖墙 25，大树 50），或写一个 opens: true 的办法")
    return out
