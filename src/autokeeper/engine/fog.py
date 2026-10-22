"""What the investigators know of the map: where they have been, and what they have only heard of.

A place is heard of once its name comes up in something the players were shown: the
module's title, hook and setting, the opening actually told, what they saw on arriving somewhere, the
keeper's narration and the NPCs' lines, who the people they met turned out to be, the
clues and handouts they found, the new places the director announced. Their own words do
not count, or typing a guess would put a secret on the map.

A name counts in full or by its head (阿诺兹堡公共图书馆 as 图书馆, 地底隧道网 as 隧道网),
never by a word so plain it could mean anything (大街, 中心), nor by one that is part of
another place's name. jieba finds where the words of a name begin.

The log is folded into a summary as the game goes on, so the engine keeps what was heard
in GameState.heard (place id -> the name it went by): nothing heard of is forgotten, and
the players' screens at a shared table, which never get the log, know it too.

What they know is also where they can go. A place they have been to or heard of can be
set out for from anywhere, and so can one in plain sight (landmarks: the police station
the town centre shows); any other is found only by going that way from next door, as the
module's exits allow. Naming a secret from across town takes no one there (Game.resolve),
and the action parser is only ever given the places they could head for (bearings), so a
name it never saw cannot end up on a player's screen.

The map (chart) is drawn from the same knowledge, and a shared table's screens get no more
of the module than it needs (net/hub.py table): drawn from that copy, it comes out the same.
"""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache

import networkx as nx

from ..module.schema import Module
from . import world
from .state import GameState

with warnings.catch_warnings():  # jieba 0.42 predates Python 3.12's stricter string escapes
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

jieba.setLogLevel(logging.WARNING)  # it reports loading its dictionary on stderr, under the TUI

SHOWN = ("opening", "keeper", "npc", "event", "clue", "handout", "ending")  # log kinds the players read
# Heads too plain to stand for one place: the keeper says them about anywhere.
GENERIC = frozenset(
    "大街 街道 街上 小路 道路 马路 路口 路上 中心 地方 房间 屋子 房子 小屋 屋里 门口 门外 外面 里面 附近 周围 一带 "
    "楼上 楼下 一楼 二楼 三楼 楼梯 地下 地上 深处 角落 旁边 对面 后面 前面 入口 出口 大门 号房 镇上 城里 家里 村里 "
    "远处 这里 那里".split()
)
_MARKS = str.maketrans("", "", "《》「」『』“”‘’\"' \u3000\t\r\n")
_JOINS = frozenset("的之·・")  # a head starts after these, never with them


def plain(text: str) -> str:
    """Text as names are matched: no book-title marks, quotes or spaces."""
    return text.translate(_MARKS)


@lru_cache(maxsize=16)
def _aliases(names: tuple[tuple[str, str], ...]) -> dict[str, tuple[str, ...]]:
    flat = {pid: plain(name) for pid, name in names}
    out: dict[str, tuple[str, ...]] = {}
    for pid, name in names:
        words = [w for w in jieba.lcut(name) if plain(w)]
        heads = {plain("".join(words[i:])) for i in range(1, len(words)) if words[i] not in _JOINS}
        heads = {
            h for h in heads
            if len(h) >= 2 and h not in GENERIC and not any(h in other for oid, other in flat.items() if oid != pid)
        }  # fmt: skip
        out[pid] = tuple(sorted({flat[pid], *heads}, key=len, reverse=True))
    return out


def aliases(m: Module) -> dict[str, tuple[str, ...]]:
    """Every place's names as they can come up in text, longest first: the full name, then its
    heads that name nothing else in the module, and the other names the module gives it
    (an address, whose house it is)."""
    out = dict(_aliases(tuple((loc.id, loc.name) for loc in m.locations)))
    for loc in m.locations:
        if loc.aliases:
            out[loc.id] = tuple(sorted({*out[loc.id], *(plain(a) for a in loc.aliases if plain(a))}, key=len, reverse=True))
    return out


@lru_cache(maxsize=16)
def _pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    # Longest first: where 圣玛丽教堂的钟楼 is named, 钟楼 is not named separately.
    return re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))


def mentions(m: Module, texts: Iterable[str]) -> dict[str, str]:
    """The places these texts name: id -> the name used (the longest, if it came up several ways)."""
    owners: dict[str, list[str]] = {}
    for pid, names in aliases(m).items():
        for n in names:
            owners.setdefault(n, []).append(pid)
    if not owners:
        return {}
    full = {plain(a): a for loc in m.locations for a in loc.aliases} | {plain(loc.name): loc.name for loc in m.locations}
    pattern = _pattern(tuple(owners))
    found: dict[str, str] = {}
    for text in texts:
        for hit in pattern.finditer(plain(text)):
            said = full.get(hit.group(), hit.group())
            for pid in owners[hit.group()]:
                if len(plain(said)) > len(plain(found.get(pid, ""))):
                    found[pid] = said
    return found


def shown(m: Module, st: GameState) -> list[str]:
    """Everything the players have been shown that can name a place."""
    places = {loc.id: loc for loc in m.locations}
    clues, handouts = {c.id: c for c in m.clues}, {h.id: h for h in m.handouts}
    texts = [m.title, m.subtitle, m.hook, m.setting]
    texts += [places[v].read_aloud for v in st.visited if v in places]  # what they saw on arriving
    texts += [e.text for e in st.log if e.kind in SHOWN]
    texts += [f"{clues[c].title}\n{clues[c].text}" for c in st.clues if c in clues]
    texts += [f"{handouts[h].title}\n{handouts[h].text}" for h in st.handouts if h in handouts]
    texts += [n.look for n in m.npcs if n.id in st.npcs and st.npcs[n.id].met]
    # The director's new places are announced by name when they open a new line (tui: "improv").
    texts += [loc.get("name", "") for p in st.improv if p.get("kind") == "展开" for loc in p.get("plan", {}).get("locations", [])]
    return texts


def kept(m: Module, st: GameState) -> list[str]:
    """What the players' screens hold that can name a place, a shared table's too (they never
    get the log): the module's title, hook and setting, what they saw on arriving somewhere,
    the clues and handouts found, who the people they met turned out to be."""
    places = {loc.id: loc for loc in m.locations}
    clues, handouts = {c.id: c for c in m.clues}, {h.id: h for h in m.handouts}
    return [m.title, m.subtitle, m.hook, m.setting, *(places[v].read_aloud for v in st.visited if v in places),
            *(f"{clues[c].title}\n{clues[c].text}" for c in st.clues if c in clues),
            *(f"{handouts[h].title}\n{handouts[h].text}" for h in st.handouts if h in handouts),
            *(n.look for n in m.npcs if n.id in st.npcs and st.npcs[n.id].met)]


def heard(m: Module, st: GameState) -> dict[str, str]:
    """Places the investigators have heard of, visited or not (id -> the name they heard it by):
    what the engine kept in st.heard, and anything the players were shown since."""
    ids = {loc.id for loc in m.locations}
    out = {pid: name for pid, name in st.heard.items() if pid in ids}
    for pid, name in mentions(m, shown(m, st)).items():
        if len(plain(name)) > len(plain(out.get(pid, ""))):
            out[pid] = name
    return out


# -- where they have been, and where they can go --------------------------------------------
def network(m: Module) -> nx.Graph:
    g = nx.Graph()
    g.add_nodes_from(loc.id for loc in m.locations)
    g.add_edges_from((loc.id, e) for loc in m.locations for e in loc.exits if e in g and e != loc.id)
    return g


def walked(g: nx.Graph, visited: list[str], known: set[str]) -> list[str]:
    """The places visited, and the ones walked through on the way: the engine takes the party
    straight to where it is going and marks only that, but the way there leads somewhere.
    Each new place is reached by the shortest way from where they have been; of two equally
    short ways, the one through places they know of (not through an unknown one, which may
    be a secret)."""
    been = visited[:1]
    for place in visited[1:]:
        if place in been:
            continue
        try:
            _, way = nx.multi_source_dijkstra(g, set(been), target=place, weight=lambda u, v, _: 1 if v in known else 1.01)
        except nx.NetworkXNoPath:  # carried off by an event to a place cut off from the rest
            way = [place]
        been += [p for p in way if p not in been]
    return been


def been(m: Module, st: GameState, told: dict[str, str] | None = None) -> list[str]:
    """Places the investigators have stopped at or walked through. `told`: heard(m, st), if
    the caller has it already."""
    ids = {loc.id for loc in m.locations}
    visited = [v for v in dict.fromkeys([*st.visited, st.location]) if v in ids]
    told = heard(m, st) if told is None else told
    return walked(network(m), visited, set(told) | set(visited))


def known(m: Module, st: GameState) -> dict[str, str]:
    """Every place the investigators know of, in module order, by the name they know it by:
    the full name where they have been, the name they heard where they have not."""
    told = heard(m, st)
    there = set(been(m, st, told))
    return {loc.id: loc.name if loc.id in there else told[loc.id] for loc in m.locations if loc.id in there or loc.id in told}


def landmarks(m: Module, st: GameState | None = None) -> dict[str, str]:
    """Places in plain sight: named in what anyone sees on arriving somewhere else (the town
    centre's read-aloud names its police station), by the name used there. Anyone in town
    could point the way, so they need not have come up in play yet: the opening's 警局 does
    not match 阿诺兹堡警察局, and the way there must not hang on that.

    A place the investigation reveals (named in a clue or handout, not in the opening texts,
    like the mausoleum and the tunnels) is no landmark, and neither is anything only its own
    read-aloud names: what is seen down a secret way is a secret too."""
    if st is not None:
        out = {loc.id: loc.name for loc in m.locations if loc.public}
        for loc in m.locations:
            if loc.id == st.location or loc.id in st.visited:
                out.update(mentions(m, [loc.read_aloud]))
        return out
    opening = set(mentions(m, [m.title, m.subtitle, m.hook, m.setting, m.opening]))
    found = mentions(m, [f"{c.title}\n{c.text}" for c in m.clues] + [f"{h.title}\n{h.text}" for h in m.handouts])
    hidden = set(found) - opening
    out: dict[str, str] = {}
    for loc in m.locations:
        if loc.id in hidden:
            continue
        for pid, name in mentions(m, [loc.read_aloud]).items():
            if pid != loc.id and pid not in hidden and len(plain(name)) > len(plain(out.get(pid, ""))):
                out[pid] = name
    return out


def findable(m: Module, st: GameState, place: str) -> bool:
    """Whether the investigators can set out for a place: one they know of, one in plain
    sight, or one next door, which going that way shows them. A place nobody has mentioned
    is not reached by guessing its name from across town."""
    if place == st.location:
        return True
    if not any(loc.id == place for loc in m.locations):
        return False
    return place in m.location(st.location).exits or place in landmarks(m, st) or place in known(m, st)


def bearings(m: Module, st: GameState) -> dict[str, str]:
    """The places an action here can head for (findable), as the action parser is given them:
    by the name the investigators know, else as plain sight names them, else (next door) by
    the plainest form of the name (隧道网, not 地底隧道网), which getting there would show
    them anyway. Here is left out."""
    out = known(m, st)
    for pid, name in landmarks(m, st).items():
        out.setdefault(pid, name)
    names = aliases(m)
    for e in m.location(st.location).exits:
        if e not in out and names.get(e):
            out[e] = names[e][-1]  # shortest: the head of the name, without whose it is or what lives there
    return {loc.id: out[loc.id] for loc in m.locations if loc.id in out and loc.id != st.location}


# -- the map ----------------------------------------------------------------------------------
@dataclass
class Chart:
    """The places the investigators know and the ways between them (tui/modals/mapview.py draws it)."""

    here: str
    names: dict[str, str]  # every place on the map -> the name it has there
    kinds: dict[str, str]  # -> "here", "been" (stopped there or walked through) or "heard"
    roads: list[tuple[str, str]] = field(default_factory=list)  # between places they have been
    trails: list[tuple[str, str]] = field(default_factory=list)  # to a place they saw, or were shown, from where they stood
    rumours: dict[str, str] = field(default_factory=dict)  # heard of, the way there unknown: listed below the map
    within: dict[str, str] = field(default_factory=dict)  # a place on the map -> the one on the map it lies in


def chart(m: Module, st: GameState) -> Chart:
    """Only what the investigators know: places they have been to or heard of, never one they
    have not, not even as a blank (a door to nowhere gives a secret away). A road joins two
    places they have been to; a trail leads from where they stood to a place they saw or were
    shown from there (its read-aloud, the clues found there). A map they hold (a handout whose kind
    says 地图) puts every place written on it on theirs, with the ways between them. The rest of
    what they heard of has no known way there yet, and stays off the map with whatever it lies in."""
    places = {loc.id: loc for loc in m.locations}
    g = network(m)
    visited = [v for v in dict.fromkeys([*st.visited, st.location]) if v in places]
    told = heard(m, st)
    there = been(m, st, told)
    trails: list[tuple[str, str]] = []
    for v in visited:  # they looked around where they stopped, not on the way through
        named = mentions(m, [places[v].read_aloud, *(f"{c.title}\n{c.text}" for c in m.clues if c.where == v and c.id in st.clues)])
        trails += [(v, p) for p in places if p in named and p in told and p not in there and g.has_edge(v, p)]
    # A map in hand (the user, 2026-10-22: the Ratgers' drawing of Esnel left the map as it was)
    mapped = set(mentions(m, [f"{h.title}\n{h.text}" for h in m.handouts if h.id in st.handouts and "地图" in h.kind])) & set(places)
    if mapped:
        # the streets it draws between them: the public places a way from here passes (Esnel's 主街), never a secret one
        open_ = g.subgraph(mapped | set(there) | {p for p, loc in places.items() if loc.public})
        for p in sorted(mapped):
            if st.location in open_ and p in open_ and nx.has_path(open_, st.location, p):
                mapped |= set(nx.shortest_path(open_, st.location, p))
        told = {p: told.get(p) or places[p].name for p in places if p in told or p in mapped}
        shown = mapped | set(there)
        trails += [(a, b) for a, b in g.edges if a in shown and b in shown and (a not in there or b not in there)
                   and (a, b) not in trails and (b, a) not in trails]
    pointed = {p for edge in trails for p in edge if p not in there}
    names = {p: places[p].name for p in places if p in there} | {p: told[p] for p in places if p in pointed}
    kinds = {p: "heard" if p in pointed else "been" for p in names}
    if st.location in kinds:
        kinds[st.location] = "here"
    return Chart(
        here=st.location,
        names={p: names[p] for p in places if p in names},  # module order: the same map every time
        kinds=kinds,
        roads=[(a, b) for a, b in g.edges if a in there and b in there],
        trails=trails,
        rumours={p: told[p] for p in places if p in told and p not in names},
        within=_within(m, set(names)),
    )


def _within(m: Module, drawn: set[str]) -> dict[str, str]:
    """What lies in what, among the places on the map; a loop in a broken module is cut."""
    out = {loc.id: loc.within for loc in m.locations if loc.id in drawn and loc.within in drawn and loc.within != loc.id}
    for place in list(out):
        seen, at = {place}, out.get(place)
        while at in out and at not in seen:
            seen.add(at)
            at = out[at]
        if at == place:
            del out[place]
    return out


def minutes(m: Module, a: str, b: str) -> int:
    """The walk from a place to its neighbour, as the game clock counts it (Game._edge_time)."""
    there, back = m.location(a).travel_minutes, m.location(b).travel_minutes
    return max(0, min(720, there.get(b, back.get(a, 10))))


def met(m: Module, st: GameState) -> dict[str, list[str]]:
    """The people the investigators have met, by the place they keep to (only where they have stopped)."""
    stopped = set(st.visited) | {st.location}
    out: dict[str, list[str]] = {}
    for n in m.npcs:
        if n.location in stopped and st.npcs.get(n.id) and st.npcs[n.id].met:
            out.setdefault(n.location, []).append(n.name)
    return out


def things(m: Module, st: GameState) -> dict[str, str]:
    """The things the investigators know are lying at a place: item id -> place. They saw it
    there (named in what they saw on arriving, or in a clue found there) or left it there. What
    an investigator holds is on their sheet; what someone was handed goes with them."""
    from ..world import knowledge
    from ..world.record import PLAYERS
    from ..world.store import World

    view = World(m, st, initialize=False)
    places = {loc.id for loc in m.locations}
    seen: dict[str, list[str]] = {}
    for v in dict.fromkeys([*st.visited, st.location]):
        if v in places:
            seen.setdefault(v, []).append(plain(m.location(v).read_aloud))
    for c in m.clues:
        if c.id in st.clues and c.where in places:
            seen.setdefault(c.where, []).append(plain(f"{c.title}{c.text}"))
    out: dict[str, str] = {}
    for item in m.items:
        recorded = st.record.entities.get(item.id)
        if recorded and not knowledge.item_known(view, recorded, PLAYERS):
            continue
        if not recorded and (item.hidden or item.equipment == "concealed"):
            continue
        holder = world.owner(m, st, item.id)
        place = holder if holder in places else item.where if item.owner and holder == item.owner and item.id not in st.item_owners else ""
        if place and (st.item_owners.get(item.id) == place or any(h in text for text in seen.get(place, []) for h in _heads(item.name))):
            out[item.id] = place
    return out


@lru_cache(maxsize=256)
def _heads(name: str) -> tuple[str, ...]:
    """A thing's name as it comes up in text: in full or by its head (典籍 for 卡德威尔家的典籍).
    Unlike a place's, not weighed against the other things' names: those a shared table's
    screens never get must not change what they read."""
    words = [w for w in jieba.lcut(name) if plain(w)]
    heads = {plain("".join(words[i:])) for i in range(1, len(words)) if words[i] not in _JOINS}
    return tuple(h for h in {plain(name), *heads} if len(h) >= 2 and h not in GENERIC)
