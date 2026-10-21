"""The map (F3): the places the investigators know, laid out in two dimensions.

engine/fog.py says which places those are, the ways between them and what lies in what (the
chart). A place with places in it is drawn folded (金博尔家 ▸ 1) or open, as a frame with
those places and their ways inside it; the area the investigators are in starts open. Each
level is laid out as one map (networkx lays the places out, they snap to a grid of rows and
columns as wide as their names, an open place as wide and tall as its frame), then every
connection is routed around the names as a box-drawing path, through a frame's wall where it
leads in, and the plan becomes text. A map too big for the window is drawn whole on a bigger
canvas that the window scrolls over. All of that is pure and cached; MapModal shows it, lets
the player pick a place and open or fold it, and says what they know of the place picked.
"""

from __future__ import annotations

import heapq
import itertools
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

import networkx as nx
from rich.cells import cell_len
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer, Vertical, VerticalScroll
from textual.content import Content
from textual.geometry import Region
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static

from ... import weather
from ...engine import fog
from ...engine.fog import Chart, chart
from ...engine.state import GameState
from ...module.schema import Module

MARK = {"here": "◉", "been": "●", "heard": "○"}
STYLE = {  # theme colours, resolved when drawn ($text-muted would lose its transparency inline)
    "here": "bold $accent",
    "you": "bold reverse $accent",  # the current place's name
    "been": "bold $foreground",
    "heard": "italic $foreground 60%",
    "road": "$primary",
    "trail": "$secondary",
    "wall": "$foreground 40%",  # the frame round a place that is open
    "tag": "bold $accent",
    "note": "$foreground 60%",
    "pick": "underline",  # added to the name of the place picked
}
TAG = " ◂ 当前位置"
FOLDED, OPENED = "▸", "▾"  # after a place with places in it: folded (and how many), or open
SKY_MARK = {"rain": "☂ 雨", "storm": "⚡ 雷雨", "snow": "❄ 雪", "fog": "≋ 雾"}
GAP = 4  # cells between a column's longest name and the next column's markers
MARGIN = 1  # a free column on either side, so a path can go round the outside

N, E, S, W = 1, 2, 4, 8
STEP = {N: (0, -1), E: (1, 0), S: (0, 1), W: (-1, 0)}
BACK = {N: S, E: W, S: N, W: E}
TURNS = {N: (E, W), S: (E, W), E: (N, S), W: (N, S)}
GLYPH = {
    N | S: "│", E | W: "─", N | E: "╰", E | S: "╭", S | W: "╮", N | W: "╯",
    N | E | S: "├", E | S | W: "┬", N | S | W: "┤", N | E | W: "┴", N | E | S | W: "┼",
    N: "╵", E: "╶", S: "╷", W: "╴",
}  # fmt: skip
DOTTED = {N | S: "┆", E | W: "┄"}
WALL = {E | S: "┌", S | W: "┐", N | E: "└", N | W: "┘", E | W: "─", N | S: "│"}  # square corners: not a road's bend
BEND, CROSS, CLASH = 3, 8, 30  # what a turn, a crossing and running along another path cost a route
DOOR = 6  # what going through a frame's wall costs a route: a way in stays a way in
KEPT = 48  # plans kept: opening the map again, or folding back, needs no new layout


# -- what the map shows -------------------------------------------------------------------
def short_names(m: Module, c: Chart) -> dict[str, str]:
    """Names for a tight map: without the town every name there starts with, and without whose
    place it is (道格拉斯的书房 -> 书房). A shortened name that two places would share stays long."""
    full = {loc.id: fog.plain(loc.name) for loc in m.locations}
    shared = {_common(a, b) for a, b in itertools.combinations(full.values(), 2)}
    towns = sorted((s for s in shared if len(s) >= 2), key=len, reverse=True)
    out = {}
    for place, name in c.names.items():
        if place in full and fog.plain(name) == full[place]:
            name = next((name[len(t) :] for t in towns if name.startswith(t) and len(name) - len(t) >= 2), name)
            tail = name.rsplit("的", 1)[-1]
            name = tail if len(tail) >= 2 else name
        out[place] = name
    taken = [*out.values()]
    return {p: n if taken.count(n) == 1 else c.names[p] for p, n in out.items()}


def _common(a: str, b: str) -> str:
    """What two names start with."""
    n = next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    return a[:n]


@dataclass
class Tree:
    """How the places on the map nest as drawn: which are open, which folded, which shown."""

    within: dict[str, str]  # a place -> the place it lies in (the chart's)
    kids: dict[str, list[str]]  # a place -> the places directly in it
    top: list[str]  # the places in nothing
    opened: set[str] = field(default_factory=set)  # drawn as a frame with its places inside
    folded: set[str] = field(default_factory=set)  # drawn as its name and how many places are in it
    shown: list[str] = field(default_factory=list)  # every place drawn, outer before inner

    def up(self, place: str) -> list[str]:
        """The places it lies in, the outermost first."""
        out, at = [], self.within.get(place)
        while at is not None:
            out.append(at)
            at = self.within.get(at)
        return out[::-1]

    def drawn(self, place: str) -> str:
        """The place drawn for it: itself, or the outermost folded place it lies in."""
        return next((a for a in self.up(place) if a in self.folded), place)

    def count(self, place: str) -> int:
        """How many places on the map lie in it, at any depth."""
        return sum(1 + self.count(q) for q in self.kids.get(place, []))


def tree(c: Chart, opened: set[str]) -> Tree:
    kids: dict[str, list[str]] = {}
    for p in c.names:
        if p in c.within:
            kids.setdefault(c.within[p], []).append(p)
    t = Tree(within=dict(c.within), kids=kids, top=[p for p in c.names if p not in c.within], opened={g for g in kids if g in opened})

    def walk(p: str) -> None:
        t.shown.append(p)
        if p in t.opened:
            for q in kids[p]:
                walk(q)

    for p in t.top:
        walk(p)
    t.folded = {g for g in kids if g in t.shown and g not in t.opened}
    return t


def default_open(c: Chart) -> set[str]:
    """What is open at first: the place the investigators are in, and every place it lies in."""
    out, at = set(), c.here
    while at in c.names and at not in out:
        out.add(at)
        at = c.within.get(at, "")
    return out


# -- where everything goes -----------------------------------------------------------------
@dataclass
class Plan:
    """The chart on a grid of cells."""

    width: int
    height: int
    spots: dict[str, tuple[int, int]]  # place -> its marker (an open place's sits on its frame's top)
    labels: dict[str, str]  # place -> the name written beside it
    tagged: str  # the place "当前位置" is written beside, if any
    kinds: dict[str, str] = field(default_factory=dict)  # as marked: a folded place the investigators are in is "here"
    frames: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)  # open place -> left, top, right, bottom
    walls: dict[tuple[int, int], int] = field(default_factory=dict)  # a frame's cells -> their directions
    lines: dict[tuple[int, int], tuple[int, str]] = field(default_factory=dict)  # cell -> (directions, "road" or "trail")
    owners: dict[tuple[int, int], set[str]] = field(default_factory=dict)  # cell -> the places its paths join
    missing: list[tuple[str, str]] = field(default_factory=list)  # ways that found no route (never, so far)

    def box(self, place: str) -> tuple[int, int, int]:
        """A place's marker and name as (first x, last x, y)."""
        x, y = self.spots[place]
        return x, x + 1 + cell_len(self.labels[place]) + (cell_len(TAG) if place == self.tagged else 0), y

    def area(self, place: str) -> Region:
        """What to keep in view when the place is picked: its name, or its whole frame if open."""
        if place in self.frames:
            left, top, right, bottom = self.frames[place]
            return Region(left, top, right - left + 1, bottom - top + 1)
        x0, x1, y = self.box(place)
        return Region(x0 - 2, y - 1, x1 - x0 + 5, 3)

    def at(self, x: int, y: int) -> str | None:
        """The place whose name, or the frame's edge, is at this cell."""
        for place in self.spots:
            x0, x1, row = self.box(place)
            if row == y and x0 <= x <= x1:
                return place
        return next((place for place, (left, top, right, bottom) in self.frames.items()
                     if (x in (left, right) and top <= y <= bottom) or (y in (top, bottom) and left <= x <= right)), None)


_PLANS: dict[tuple, Plan] = {}


def plan(c: Chart, width: int, height: int, short: dict[str, str] | None = None, opened: set[str] | None = None) -> Plan:
    """Lay the chart out in at most width x height cells if it can be done: full names and the
    "you are here" tag, else without the tag, else short names, else closer rows. If none of
    that fits, the whole map on a bigger canvas, as wide as the window if the names allow,
    for the window to scroll over. `opened`: the places drawn open (default_open)."""
    if not c.names:
        return Plan(width=0, height=0, spots={}, labels={}, tagged="")
    opened = default_open(c) if opened is None else opened
    key = (c.here, tuple(c.names.items()), tuple(c.kinds.items()), tuple(c.roads), tuple(c.trails), tuple(c.within.items()),
           tuple((short or {}).items()), width, height, frozenset(opened))  # fmt: skip
    if (kept := _PLANS.get(key)) is not None:
        return kept
    t = tree(c, opened)
    # With room to spare, rows further apart first: the painted ground between them shows what
    # each place is (drawing/mapart.py); a tight layout left it no room (2026-10-21).
    tries = [(c.names, True, 4), (c.names, True, 3), (c.names, True, 2), (c.names, False, 2)]
    if short and short != c.names:
        tries.append((short, False, 2))
    tries.append((short or c.names, False, 1))
    laid = None
    for labels, tag, gap in tries:
        laid = _attempt(c, t, labels, tag, gap, width, (height - 1) // (gap + 1) + 1, fit=(width, height))
        if laid is not None:
            break
    else:
        for gap in (2, 3, 4):  # further apart until every way finds a route
            laid = _attempt(c, t, short or c.names, True, gap, width, len(t.shown), fit=None)
            if laid is not None and not laid.missing:
                break
    assert laid is not None  # without a size to keep to, a layout is always found
    _PLANS[key] = laid
    while len(_PLANS) > KEPT:
        del _PLANS[next(iter(_PLANS))]
    return laid


def _attempt(c: Chart, t: Tree, names: dict[str, str], tag: bool, gap: int, width: int, rows: int,
             fit: tuple[int, int] | None) -> Plan | None:
    """One layout: the open places' insides first, each its own small map in a frame, then
    the places in nothing around them. With `fit`, None unless it fits and every way routes;
    without, as wide as `width` unless a name is wider, as tall as it needs."""
    here = t.drawn(c.here) if c.here in c.names else ""
    labels = {}
    for p in t.shown:
        name = names.get(p, c.names[p])
        labels[p] = f"{name} {FOLDED} {t.count(p)}" if p in t.folded else f"{name} {OPENED}" if p in t.opened else name
    tagged = here if tag else ""
    widths = {p: 2 + cell_len(labels[p]) + (cell_len(TAG) if p == tagged else 0) for p in t.shown}
    ways = _ways(c, t)
    sizes = {p: (widths[p], 1) for p in t.shown}
    inner: dict[str, tuple[dict[str, tuple[int, int]], int, int]] = {}
    for g in reversed(t.shown):  # inner before outer: a frame is as big as what is in it
        if g in t.opened:
            kids = t.kids[g]
            laid = _level(kids, _level_ways(ways, kids, t), sizes, width - 2, len(kids), gap, grow=fit is None)
            if laid is None:
                return None
            inner[g] = laid
            sizes[g] = (max(laid[1] + 2, widths[g] + 6) - 1, laid[2] + 4)  # less the wall, which sits left of the column
    laid = _level(t.top, _level_ways(ways, t.top, t), sizes, width, rows, gap, grow=fit is None)
    if laid is None:
        return None
    top, w, h = laid
    if fit is not None and (w > fit[0] or h > fit[1]):
        return None
    kinds = {p: c.kinds[p] for p in t.shown}
    if here:
        kinds[here] = "here"
    p = Plan(width=w, height=h, spots={}, labels=labels, tagged=tagged, kinds=kinds)

    def put(members: list[str], at: dict[str, tuple[int, int]], ox: int, oy: int) -> None:
        for q in members:
            x, y = at[q][0] + ox, at[q][1] + oy
            if q not in t.opened:
                p.spots[q] = (x, y)
                continue
            fw, fh = sizes[q]
            # The left wall one cell before the column: a road from a place above or below
            # the frame meets the wall straight, not its corner.
            p.frames[q] = (x - 1, y, x + fw - 1, y + fh - 1)
            p.spots[q] = (x + 2, y)  # on the top edge: "┌─ ● 公墓 ▾ ──┐"
            put(t.kids[q], inner[q][0], x, y + 2)  # inside the wall, below a free row

    put(t.top, top, 0, 0)
    for g, (left, top_, right, bottom) in p.frames.items():
        mx = p.spots[g][0]
        named = range(mx - 1, mx + widths[g] + 1)  # the name on the top edge, a space either side
        for x in range(left + 1, right):
            if x not in named:
                p.walls[(x, top_)] = E | W
            p.walls[(x, bottom)] = E | W
        for y in range(top_ + 1, bottom):
            p.walls[(left, y)] = p.walls[(right, y)] = N | S
        p.walls.update({(left, top_): E | S, (right, top_): S | W, (left, bottom): N | E, (right, bottom): N | W})
    if _route(p, ways, widths, strict=fit is not None) and not p.missing:
        return p
    # A column of places, their doors above and below them, walls the map off from top to
    # bottom: a free row above and below lets a path go round it.
    if fit is not None and h + 2 > fit[1]:
        return None
    p.spots = {q: (x, y + 1) for q, (x, y) in p.spots.items()}
    p.frames = {q: (left, top_ + 1, right, bottom + 1) for q, (left, top_, right, bottom) in p.frames.items()}
    p.walls = {(x, y + 1): mask for (x, y), mask in p.walls.items()}
    p.height, p.lines, p.owners, p.missing = h + 2, {}, {}, []
    if not _route(p, ways, widths, strict=fit is not None):
        return None
    return p


def _ways(c: Chart, t: Tree) -> list[tuple[str, tuple[str, str]]]:
    """The connections drawn, each between the places drawn for its ends (a folded place for
    what is in it): none inside a folded place, none from a place to what lies in it (being
    inside says that), one between two places however many ways there are. A road wins."""
    out: dict[frozenset[str], tuple[str, tuple[str, str]]] = {}
    for kind, edges in (("road", c.roads), ("trail", c.trails)):
        for a, b in edges:
            a, b = t.drawn(a), t.drawn(b)
            if a != b and a not in t.up(b) and b not in t.up(a):
                out.setdefault(frozenset((a, b)), (kind, (a, b)))
    return list(out.values())


def _level_ways(ways: list[tuple[str, tuple[str, str]]], members: list[str], t: Tree) -> list[tuple[str, str]]:
    """The ways between the places of one level: each end taken to the one of them it is in."""
    inside = set(members)

    def lift(place: str) -> str | None:
        return next((a for a in [place, *reversed(t.up(place))] if a in inside), None)

    out = []
    for _, (a, b) in ways:
        x, y = lift(a), lift(b)
        if x is not None and y is not None and x != y:
            out.append((x, y))
    return out


def _level(members: list[str], edges: list[tuple[str, str]], sizes: dict[str, tuple[int, int]], width: int, rows: int,
           gap: int, grow: bool = False) -> tuple[dict[str, tuple[int, int]], int, int] | None:
    """One level laid out: each place's top left corner, and the level's width and height. A row
    is as tall as its tallest place, and the others sit at its middle. `grow`: wider than
    `width` if a single place is."""
    widths = {p: sizes[p][0] for p in members}
    if grow:
        width = max(width, max(widths.values()) + 2 * MARGIN)
    slots = arrange(members, edges, widths, width - 2 * MARGIN, rows)
    if slots is None:
        return None
    cols, lines = max(x for x, _ in slots.values()) + 1, max(y for _, y in slots.values()) + 1
    colw = [max(widths[p] for p, (x, _) in slots.items() if x == k) for k in range(cols)]
    rowh = [max(sizes[p][1] for p, (_, y) in slots.items() if y == k) for k in range(lines)]
    xs = [MARGIN + sum(colw[:k]) + k * GAP for k in range(cols)]
    ys = [sum(rowh[:k]) + k * gap for k in range(lines)]
    spots = {p: (xs[x], ys[y] + (rowh[y] - sizes[p][1]) // 2) for p, (x, y) in slots.items()}
    return spots, xs[-1] + colw[-1] + MARGIN, ys[-1] + rowh[-1]


def arrange(nodes: list[str], edges: list[tuple[str, str]], widths: dict[str, int], width: int, rows: int) -> dict[str, tuple[int, int]] | None:
    """A (column, row) slot for every place: networkx's spring layout snapped to a grid, then
    tidied place by place while that makes the connections shorter, straighter and less
    tangled. None if the names cannot fit side by side in `width` cells."""
    g = nx.Graph()
    g.add_nodes_from(nodes)
    g.add_edges_from(edges)
    n = len(g)
    narrow = sorted(widths.values())
    fits = [k for k in range(1, n + 1) if sum(narrow[:k]) + (k - 1) * GAP <= width]
    if not fits or n > max(fits) * rows:
        return None
    snapped = []
    low = max(1, math.ceil(n / rows))
    for cols in range(low, min(max(fits), low + 3) + 1):
        grid_rows = min(rows, math.ceil(n / cols) + 1)
        if cols * grid_rows < n:
            continue
        for seed in range(1, 9):  # snapping is cheap; a few starts find a much tidier map
            pos = _spring(tuple(g.nodes), tuple(g.edges), seed) if n > 1 else {p: (0.0, 0.0) for p in g}
            slots = _snap(g, pos, cols, grid_rows)
            snapped.append((_cost(g, slots, widths, width), cols, grid_rows, slots))
            if n <= 1 or not g.number_of_edges():
                break  # nothing for another start to untangle
    if not snapped:
        return None
    snapped.sort(key=lambda t: (t[0], t[2]))  # only the most promising are worth tidying; of two as good, the flatter
    tidied = [_tidy(g, slots, widths, width, cols, grid_rows) for _, cols, grid_rows, slots in snapped[:2]]
    best = min(tidied, key=lambda slots: _cost(g, slots, widths, width))
    if _overflow(best, widths, width):  # the tidiest is too wide: the best start that is not
        narrow_enough = [slots for _, _, _, slots in snapped if not _overflow(slots, widths, width)]
        if not narrow_enough:
            return None
        best = min(narrow_enough, key=lambda slots: _cost(g, slots, widths, width))
    used_cols = sorted({x for x, _ in best.values()})
    used_rows = sorted({y for _, y in best.values()})
    return {p: (used_cols.index(x), used_rows.index(y)) for p, (x, y) in best.items()}


@lru_cache(maxsize=256)
def _spring(nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...], seed: int) -> dict[str, tuple[float, float]]:
    """networkx's spring layout of a level, kept: every try at fitting the map starts from it."""
    g = nx.Graph()
    g.add_nodes_from(nodes)
    g.add_edges_from(edges)
    return {p: (float(x), float(y)) for p, (x, y) in nx.spring_layout(g, seed=seed).items()}


def _snap(g: nx.Graph, pos, cols: int, rows: int) -> dict[str, tuple[int, int]]:
    """Every place to the free slot nearest its spot in the layout; the best connected choose first."""
    xs, ys = [pos[p][0] for p in g], [pos[p][1] for p in g]

    def scale(v: float, lo: float, hi: float, k: int) -> float:
        return (v - lo) / (hi - lo) * (k - 1) if hi > lo else (k - 1) / 2

    free = {(x, y) for x in range(cols) for y in range(rows)}
    slots = {}
    for p in sorted(g, key=lambda p: -g.degree(p)):
        wx, wy = scale(pos[p][0], min(xs), max(xs), cols), scale(-pos[p][1], -max(ys), -min(ys), rows)
        slot = min(free, key=lambda s: ((s[0] - wx) ** 2 + (s[1] - wy) ** 2, s[1], s[0]))
        slots[p] = slot
        free.remove(slot)
    return slots


def _tidy(g: nx.Graph, slots: dict, widths: dict[str, int], width: int, cols: int, rows: int) -> dict:
    """Move or swap places one at a time while the map gets better. A move is judged by what it
    changes: the connections of the places moved, what crosses them, and the straight
    connections of others those places now sit on or leave."""
    edges = list(g.edges)
    touching = {p: {i for i, e in enumerate(edges) if p in e} for p in g}
    slots = dict(slots)
    taken = {s: p for p, s in slots.items()}

    def local(group: list[str]) -> float:
        mine = set().union(*(touching[p] for p in group))
        cost = 0.0
        for i in mine:
            a, b = edges[i]
            cost += _length(slots[a], slots[b]) + 6 * _between(slots[a], slots[b], taken, (a, b))
            for j, (c, d) in enumerate(edges):
                if (j not in mine or j > i) and c not in (a, b) and d not in (a, b) and _crosses(slots[a], slots[b], slots[c], slots[d]):
                    cost += 4
        for p in group:
            cost += 6 * sum(1 for j, (c, d) in enumerate(edges) if j not in mine and _inside(slots[c], slots[d], slots[p]))
        return cost + _spread(slots, widths, width)

    def move(p: str, to: tuple[int, int]) -> None:
        q, was = taken.get(to), slots[p]
        if q is not None:
            slots[q], taken[was] = was, q
        else:
            del taken[was]
        slots[p], taken[to] = to, p

    grid = [(x, y) for y in range(rows) for x in range(cols)]
    for _ in range(8):
        better = False
        for p in list(slots):
            for to in grid:
                was, q = slots[p], taken.get(to)
                if to == was:
                    continue
                group = [p] if q is None else [p, q]
                before = local(group)
                move(p, to)
                if local(group) < before - 1e-9:
                    better = True
                else:
                    move(p, was)
        if not better:
            break
    return slots


def _length(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Neighbouring slots cost nothing; every slot further, and a bend, costs more."""
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    span = dx + dy - 1
    return 2 * span + span * span + (1.5 if dx and dy else 0)


def _between(a: tuple[int, int], b: tuple[int, int], taken: dict, ends: tuple[str, str]) -> int:
    """Places sitting on a straight connection between two others."""
    if a[0] != b[0] and a[1] != b[1]:
        return 0
    steps = max(abs(a[0] - b[0]), abs(a[1] - b[1]))
    cells = ((a[0] + (b[0] - a[0]) * i // steps, a[1] + (b[1] - a[1]) * i // steps) for i in range(1, steps))
    return sum(1 for cell in cells if taken.get(cell) not in (None, *ends))


def _inside(a: tuple[int, int], b: tuple[int, int], p: tuple[int, int]) -> bool:
    if a[0] == b[0] == p[0]:
        return min(a[1], b[1]) < p[1] < max(a[1], b[1])
    return a[1] == b[1] == p[1] and min(a[0], b[0]) < p[0] < max(a[0], b[0])


def _spread(slots: dict, widths: dict[str, int], width: int) -> float:
    """Names that would not fit side by side, and every row and column the map takes up."""
    return 100 * _overflow(slots, widths, width) + 0.5 * len({x for x, _ in slots.values()}) + 0.5 * len({y for _, y in slots.values()})


def _overflow(slots: dict, widths: dict[str, int], width: int) -> int:
    cols: dict[int, int] = {}
    for p, (x, _) in slots.items():
        cols[x] = max(cols.get(x, 0), widths[p])
    return max(0, sum(cols.values()) + (len(cols) - 1) * GAP - width)


def _cost(g: nx.Graph, slots: dict, widths: dict[str, int], width: int) -> float:
    """How bad a grid layout looks: long and bent connections, crossings, places sitting on
    someone else's connection, and names that would not fit side by side."""
    taken = {s: p for p, s in slots.items()}
    edges = list(g.edges)
    total = sum(_length(slots[a], slots[b]) + 6 * _between(slots[a], slots[b], taken, (a, b)) for a, b in edges)
    for (a, b), (c, d) in itertools.combinations(edges, 2):
        if c not in (a, b) and d not in (a, b) and _crosses(slots[a], slots[b], slots[c], slots[d]):
            total += 4
    return total + _spread(slots, widths, width)


def _crosses(p1: tuple[int, int], p2: tuple[int, int], q1: tuple[int, int], q2: tuple[int, int]) -> bool:
    """Whether two straight connections between slots cross or run along each other."""
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = p1, p2, q1, q2
    d1 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)
    d2 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)
    if d1 * d2 > 0:  # both ends on one side of the other
        return False
    d3 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    d4 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)
    if d3 * d4 > 0:
        return False
    if d1 * d2 < 0 and d3 * d4 < 0:
        return True
    # an end lies on the other's line: they meet if it lies within the other
    return (
        (d1 == 0 and _within(q1, q2, p1)) or (d2 == 0 and _within(q1, q2, p2))
        or (d3 == 0 and _within(p1, p2, q1)) or (d4 == 0 and _within(p1, p2, q2))
    )  # fmt: skip


def _within(a: tuple[int, int], b: tuple[int, int], p: tuple[int, int]) -> bool:
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


# -- routing -------------------------------------------------------------------------------
def _route(p: Plan, ways: list[tuple[str, tuple[str, str]]], widths: dict[str, int], strict: bool = True) -> bool:
    """Route every road, then every trail, as an orthogonal path between the doors of the two
    places: above or below a marker, left of it, or past the end of the name; anywhere round
    the outside of an open place's frame, the path joining its wall. Straight runs between
    neighbours go first, so the rest bend around them. A path may go straight through a
    frame's wall to a place inside. `strict`: False as soon as one cannot be routed; else
    the way is left out and noted in p.missing."""
    blocked: set[tuple[int, int]] = {cell for cell, mask in p.walls.items() if mask not in (N | S, E | W)}  # corners
    doors: dict[str, dict[tuple[int, int], int]] = {}
    sides: set[tuple[int, int]] = set()  # the doors left and right of a name
    above: set[tuple[int, int]] = set()  # and those above and below a marker
    taken: set[tuple[int, int]] = set()  # doors a path went past: their place goes out another way
    for place, (x, y) in p.spots.items():
        end = x + widths[place] - 1  # the name's last cell
        if place in p.frames:
            left, top, right, bottom = p.frames[place]
            blocked |= {(i, top) for i in range(x - 1, end + 2)}  # the name on the frame's top edge
            ring = {(i, top - 1): N for i in range(left + 1, right) if (i, top) in p.walls}
            ring |= {(i, bottom + 1): S for i in range(left + 1, right)}
            ring |= {(left - 1, j): W for j in range(top + 1, bottom)} | {(right + 1, j): E for j in range(top + 1, bottom)}
            doors[place] = ring
        else:
            blocked |= {(i, y) for i in range(x, end + 2)}  # marker, name, and a space after it
            doors[place] = {(x, y - 1): N, (x, y + 1): S, (x - 1, y): W, (end + 2, y): E}  # door -> the way out
            sides |= {(x - 1, y), (end + 2, y)}
            above |= {(x, y - 1), (x, y + 1)}
    ways = sorted(ways, key=lambda w: (w[0] != "road", not _clear(p, *w[1]), _apart(p, *w[1])))
    for kind, (a, b) in ways:
        # Out of a door straight ahead or to either side, into one the same way; turning in the
        # doorway costs a bend. Other places' doors are kept clear, or a path passing would seem to
        # go in; one above or below a marker that nothing uses yet may be passed straight across,
        # or a column of places would wall the map off.
        starts = {(*cell, h): 0 if h == out else BEND for cell, out in doors[a].items() if cell not in taken for h in (out, *TURNS[out])}
        goals = {(*cell, h): 0 if h == BACK[out] else BEND for cell, out in doors[b].items() if cell not in taken for h in (BACK[out], *TURNS[out])}
        own = set(doors[a]) | set(doors[b])
        passes = {cell: N | S for cell in above - own if cell not in p.lines}
        way = _astar(p, blocked | ((sides | above) - own - set(passes)), starts, goals, own, {a, b}, kind, passes)
        if way is None:
            if strict:
                return False
            p.missing.append((a, b))
            continue
        _mark(p, way, kind, BACK[doors[a][way[0]]], BACK[doors[b][way[-1]]], {a, b})
        taken |= set(way) & set(passes)
        for place, cell in ((a, way[0]), (b, way[-1])):
            if place in p.frames:  # where it meets the wall, the wall branches off to it
                out = doors[place][cell]
                wall = (cell[0] - STEP[out][0], cell[1] - STEP[out][1])
                mask, was = p.lines.get(wall, (0, kind))
                p.lines[wall] = (mask | out, "road" if "road" in (was, kind) else kind)
                p.owners.setdefault(wall, set()).update((a, b))
    return True


def _clear(p: Plan, a: str, b: str) -> bool:
    """Two places side by side or one above the other, with nothing between them."""
    (ax, ay), (bx, by) = p.spots[a], p.spots[b]
    others = [s for q, s in p.spots.items() if q not in (a, b)]
    if ay == by:
        return not any(y == ay and min(ax, bx) < x < max(ax, bx) for x, y in others)
    return ax == bx and not any(x == ax and min(ay, by) < y < max(ay, by) for x, y in others)


def _apart(p: Plan, a: str, b: str) -> int:
    (ax, ay), (bx, by) = p.spots[a], p.spots[b]
    return abs(ax - bx) + abs(ay - by)


def _free(cell: tuple[int, int], p: Plan, blocked: set) -> bool:
    x, y = cell
    return 0 <= x < p.width and 0 <= y < p.height and cell not in blocked and cell not in p.walls


def _astar(p: Plan, blocked: set, starts: dict, goals: dict, forks: set, ends: set[str], kind: str,
           passes: dict | None = None) -> list[tuple[int, int]] | None:
    """The cheapest way through free cells from a start to a goal, both (x, y, heading) -> the
    cost of turning in that doorway: few turns, crossing other paths rather than running along
    them. At the doors of the two places it joins (`forks`) it may meet their other paths, and
    it may run along a path of its kind from one of them (`ends`): a fork in the road. A
    frame's wall, and a door in `passes` (cell -> the wall it is like), is crossed straight,
    never followed."""
    passes = passes or {}
    starts = {s: v for s, v in starts.items() if _free(s[:2], p, blocked)}
    goals = {g: v for g, v in goals.items() if _free(g[:2], p, blocked)}
    if not starts or not goals:
        return None
    gx0, gx1 = min(g[0] for g in goals), max(g[0] for g in goals)
    gy0, gy1 = min(g[1] for g in goals), max(g[1] for g in goals)

    def guess(x: int, y: int) -> int:  # to the box round the goals: never more than it takes
        return max(gx0 - x, 0, x - gx1) + max(gy0 - y, 0, y - gy1)

    frontier: list = []
    tie = itertools.count()
    for (x, y, d), turn in starts.items():
        cost = turn + _toll(p, (x, y), d, forks, ends, kind) + goals.get((x, y, d), 0)
        heapq.heappush(frontier, (cost + guess(x, y), cost, next(tie), x, y, d, None))
    came: dict[tuple[int, int, int], tuple | None] = {}
    while frontier:
        _, cost, _, x, y, d, prev = heapq.heappop(frontier)
        if (x, y, d) in came:
            continue
        came[(x, y, d)] = prev
        if (x, y, d) in goals:
            way, state = [], (x, y, d)
            while state is not None:
                way.append(state[:2])
                state = came[state]
            return way[::-1]
        for nd in (d,) if (x, y) in p.walls or (x, y) in passes else (d, *TURNS[d]):
            nx_, ny = x + STEP[nd][0], y + STEP[nd][1]
            if not (0 <= nx_ < p.width and 0 <= ny < p.height) or (nx_, ny) in blocked or (nx_, ny, nd) in came:
                continue
            wall = p.walls.get((nx_, ny)) or passes.get((nx_, ny))
            if wall is not None and wall != (E | W if nd in (N, S) else N | S):
                continue  # only straight across a wall
            step = 1 + (BEND if nd != d else 0) + _toll(p, (nx_, ny), nd, forks, ends, kind) + goals.get((nx_, ny, nd), 0)
            if wall is not None:
                step += DOOR if (nx_, ny) in p.walls else CROSS
            if nd != d and (x, y) in p.lines and (x, y) not in forks and not _ours(p, (x, y), ends, kind):
                step += CLASH  # turning on another path joins it
            heapq.heappush(frontier, (cost + step + guess(nx_, ny), cost + step, next(tie), nx_, ny, nd, (x, y, d)))
    return None


def _ours(p: Plan, cell: tuple[int, int], ends: set[str], kind: str) -> bool:
    """A cell on a path of the same kind from one of the places being joined."""
    return p.lines[cell][1] == kind and bool(p.owners.get(cell, set()) & ends)


def _toll(p: Plan, cell: tuple[int, int], heading: int, forks: set, ends: set[str], kind: str) -> float:
    """What stepping onto a cell another path already uses costs."""
    if cell not in p.lines:
        return 0
    if _ours(p, cell, ends, kind):
        return -0.5  # one road out of a place, branching, reads better than several side by side
    if cell in forks:
        return 0.5
    across = W | E if heading in (N, S) else N | S
    return CROSS if p.lines[cell][0] == across else CLASH


def _mark(p: Plan, way: list[tuple[int, int]], kind: str, start: int, end: int, ends: set[str]) -> None:
    """Draw a routed way into the plan's cells; `start` and `end` point from its first and last
    cells into the places it joins."""
    bits = {cell: 0 for cell in way}
    bits[way[0]] |= start
    bits[way[-1]] |= end
    for (x1, y1), (x2, y2) in itertools.pairwise(way):
        d = next(d for d, step in STEP.items() if step == (x2 - x1, y2 - y1))
        bits[(x1, y1)] |= d
        bits[(x2, y2)] |= BACK[d]
    for cell, b in bits.items():
        mask, was = p.lines.get(cell, (0, kind))
        p.lines[cell] = (mask | b, "road" if "road" in (was, kind) else kind)
        p.owners.setdefault(cell, set()).update(ends)


# -- text ---------------------------------------------------------------------------------
def draw(c: Chart, p: Plan, picked: str | None = None) -> Content:
    """The plan as text, in the theme's colours; `picked`: the place picked, underlined."""
    grid: list[list[tuple[str, str] | None]] = [[(" ", "")] * p.width for _ in range(p.height)]
    for (x, y), mask in p.walls.items():
        if (x, y) in p.lines:  # a path through the wall, or one joining it
            path, kind = p.lines[(x, y)]
            grid[y][x] = (GLYPH[mask | path], STYLE[kind])
        else:
            grid[y][x] = (WALL[mask], STYLE["wall"])
    for (x, y), (mask, kind) in p.lines.items():
        if (x, y) not in p.walls:
            glyph = DOTTED.get(mask, GLYPH[mask]) if kind == "trail" else GLYPH[mask]
            grid[y][x] = (glyph, STYLE[kind])

    def write(x: int, y: int, text: str, style: str) -> int:
        for ch in text:
            grid[y][x] = (ch, style)
            for k in range(1, cell_len(ch)):
                grid[y][x + k] = None  # covered by a wide character
            x += cell_len(ch)
        return x

    for place, (x, y) in p.spots.items():
        kind = p.kinds.get(place, c.kinds[place])
        name = STYLE["you"] if kind == "here" else STYLE[kind]
        write(x, y, MARK[kind], STYLE[kind] + (f" {STYLE['pick']}" if place == picked else ""))
        end = write(x + 2, y, p.labels[place], name + (f" {STYLE['pick']}" if place == picked else ""))
        if place == p.tagged:
            write(end, y, TAG, STYLE["tag"])
    rows = []
    for row in grid:
        cells = [cell for cell in row if cell is not None]
        runs = itertools.groupby(cells, key=lambda cell: cell[1])
        rows.append(Content.assemble(*(("".join(ch for ch, _ in run), style) for style, run in runs)))
    return Content("\n").join(rows)


def legend(c: Chart, t: Tree | None = None, *, painted: bool = False) -> Content:
    """What the marks mean, and the places heard of with no known way there yet. `painted`: the
    ways as the painted map shows them (drawing/mapart.py), a swatch of track."""
    keys = [(MARK["here"], "here", "当前位置"), (MARK["been"], "been", "已到访")]
    if "heard" in c.kinds.values() or c.rumours:
        keys.append((MARK["heard"], "heard", "已听闻"))
    if c.roads:
        keys.append(("██" if painted else "──", "#805e3a" if painted else "road", "已行经道路"))
    if c.trails:
        keys.append(("█ █" if painted else "┄┄", "#967a54" if painted else "trail", "未行经道路"))
    if t is not None and t.folded:
        keys.append((FOLDED, "been", "内部区域"))
    rows = [Content("   ").join(Content.assemble((mark, STYLE.get(kind, kind)), f" {label}") for mark, kind, label in keys)]
    if c.rumours:
        rows.append(Content.assemble(("已听闻，路线未知：", STYLE["note"]), *_names(list(c.rumours.values()))))
    return Content("\n").join(rows)


def _names(names: list[str]) -> list:
    parts: list = []
    for i, name in enumerate(names):
        parts += [("、", STYLE["note"])] if i else []
        parts.append((f"{MARK['heard']} {name}", STYLE["heard"]))
    return parts


NO_START = set("，。、；：！？）」』”’》〉…—,.;:!?)")  # never the first character of a line
NO_END = set("（「『“‘《〈(")  # never the last


def fold(text: str, width: int, indent: int = 0) -> str:
    """Chinese wrapped at `width` cells the way it is typeset: no line starts with a comma or a
    full stop, none ends on an opening quote, and a number or a Latin word stays whole. Later
    lines start `indent` cells in, under the text after a label."""
    tokens = re.findall(r"[A-Za-z0-9.:/%]+|.", text)  # `width` excludes the indent
    lines: list[list[str]] = [[]]
    used = 0
    for tok in tokens:
        w = cell_len(tok)
        if used + w > width and lines[-1]:
            carry: list[str] = []
            if tok in NO_START and len(lines[-1]) > 1:  # the character before it goes down too
                carry.append(lines[-1].pop())
            while len(lines[-1]) > 1 and lines[-1][-1] in NO_END:
                carry.insert(0, lines[-1].pop())
            if carry and carry[0] == " ":
                carry.pop(0)
            lines.append(carry)
            used = sum(cell_len(t) for t in carry)
            if tok == " " and not carry:
                continue
        lines[-1].append(tok)
        used += w
    return ("\n" + " " * indent).join("".join(line).rstrip() for line in lines)


def about(m: Module, st: GameState, c: Chart, place: str, width: int = 0) -> Content:
    """What the investigators know of a place on the map: what they saw on arriving (only
    where they stopped), the places in it, the people met there, the things they know lie
    there, the clues found there, how far the neighbours are by the roads walked, and the sky
    if it is open to it. Nothing else: no keeper's notes, no list of what could be searched,
    no clue not found, no way or place they do not know. All of it is in a shared table's
    copy of the module too (net/hub.py table), so every screen says the same. With a `width`,
    the text is folded to it (fold) and each way out gets its own line."""
    kind = c.kinds[place]
    stopped = place in st.visited or place == st.location
    status = "当前位置" if kind == "here" else "已到访" if stopped else "已途经" if kind == "been" else "已听闻，未到访"
    at = c.within.get(c.here)
    while at is not None and at != place:
        at = c.within.get(at)
    if at == place and kind != "here":
        status += " · 位于此区域内"
    if place in c.within:
        status += f" · 所属区域：{c.names[c.within[place]]}"
    rows = [Content.assemble((f"{MARK[kind]} {c.names[place]}", STYLE[kind]), (f"  {status}", STYLE["note"]))]
    seen = _unwrap(m.location(place).read_aloud) if stopped else ""
    if seen:
        rows.append(Content(fold(seen, width) if width else seen))
    lying = fog.things(m, st)
    facts = [("内部区域", [c.names[q] for q in c.names if c.within.get(q) == place]),
             ("已遇见人物", fog.met(m, st).get(place, [])),
             ("物件", [i.name for i in m.items if lying.get(i.id) == place]),
             ("线索", fog.found(m, st).get(place, []))]  # fmt: skip
    ways = [f"{c.names[b if a == place else a]} {fog.minutes(m, place, b if a == place else a)} 分钟" for a, b in c.roads if place in (a, b)]
    ways += [f"{c.names[b if a == place else a]}（未行经）" for a, b in c.trails if place in (a, b)]
    facts.append(("通往", ways))
    if st.sky in SKY_MARK and kind != "heard" and weather.outdoors(c.names[place]):
        facts.append(("天气", [SKY_MARK[st.sky]]))
    for label, items in facts:
        if not items:
            continue
        lead = cell_len(label) + 1
        if width and label == "通往":
            body = ("\n" + " " * lead).join(fold(i, width - lead, lead) for i in items)
        else:
            body = fold("、".join(items), width - lead, lead) if width else "、".join(items)
        rows.append(Content.assemble((f"{label} ", STYLE["note"]), body))
    return Content("\n").join(rows)


def _unwrap(text: str) -> str:
    """A module's text without the line breaks it was typed with: the window wraps it. Chinese
    lines join as they are, a word of Latin letters at the break gets its space back."""
    out = ""
    for line in (line.strip() for line in text.strip().splitlines()):
        if line and out and out[-1].isascii() and out[-1].isalnum() and line[0].isascii() and line[0].isalnum():
            out += " "
        out += line
    return out


def view(m: Module, st: GameState, width: int, height: int) -> Content:
    """The map as the window first shows it, and its legend, for a window of width x height cells."""
    c = chart(m, st)
    opened = default_open(c)
    return Content("\n\n").join([draw(c, plan(c, width, height, short_names(m, c), opened)), legend(c, tree(c, opened))])


def _toward(boxes: dict[str, tuple[int, int, int]], at: str, dx: int, dy: int) -> str | None:
    """The nearest place that way from the one picked; straight ahead counts most."""
    x0, x1, y = boxes[at]
    best: tuple[float, str] | None = None
    for place, (a0, a1, b) in boxes.items():
        ax, ay = (a0 + a1) / 2 - (x0 + x1) / 2, (b - y) * 3  # a line down weighs three cells across: names run wide
        ahead, aside = (ax, abs(ay)) if dx else (ay, abs(ax))
        ahead *= dx or dy
        if place != at and ahead > 0 and (best is None or ahead + 2 * aside < best[0]):
            best = (ahead + 2 * aside, place)
    return best[1] if best else None


# -- on screen ----------------------------------------------------------------------------
def window(width: int, height: int, notes: Content) -> tuple[tuple[int, int], bool, int, int]:
    """How the map modal shares a screen of width x height: the most room the map's window may
    take, whether the place's notes go beside it (and how wide), and how many lines they get
    under it. The modal keeps a margin round it (a short screen, a line above and below) and
    has a border, padding, the title, the legend `notes` (as many lines as it takes) and the keys."""
    body = max(30, min(width - 10, 150))
    lines = sum(max(1, math.ceil(cell_len(row) / body)) for row in notes.plain.split("\n"))
    tall = max(6, (height - 2 if height < 30 else height * 9 // 10) - 8 - lines)
    wide = body >= 124  # beside it, the map still as wide as most need
    side = max(34, min(44, body - 92)) if wide else 0
    under = max(3, min(7, tall // 3))
    return ((body - side - 2, tall) if wide else (body, max(3, tall - under - 1))), wide, side, under


class MapCanvas(Static):
    """The drawn map. A click picks a place; dragging moves the map."""

    ALLOW_SELECT = False

    class Clicked(Message):
        def __init__(self, x: int, y: int) -> None:
            super().__init__()
            self.x, self.y = x, y

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.grip: tuple[float, float, float, float] | None = None  # where a drag began, and the scroll then
        self.dragged = False

    def on_mouse_down(self, event: events.MouseDown) -> None:
        view = self.parent
        if isinstance(view, ScrollableContainer):
            self.grip, self.dragged = (event.screen_x, event.screen_y, view.scroll_x, view.scroll_y), False
            self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        view = self.parent
        if self.grip is not None and isinstance(view, ScrollableContainer):
            sx, sy, ox, oy = self.grip
            if event.screen_x != sx or event.screen_y != sy:
                self.dragged = True
                view.scroll_to(ox - (event.screen_x - sx), oy - (event.screen_y - sy), animate=False)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self.grip = None
        self.release_mouse()

    def on_click(self, event: events.Click) -> None:
        if not self.dragged:
            self.post_message(self.Clicked(event.x, event.y))
        self.dragged = False


class MapView(ScrollableContainer, can_focus=False):
    """The window onto the map: scrolls both ways, by wheel, drag or the modal's keys."""


class PlaceView(VerticalScroll, can_focus=False):
    """What they know of the place picked; the wheel scrolls it."""


class MapModal(ModalScreen[str | None]):
    """F3. The map, and beside or under it what the investigators know of the place picked.
    Arrows and Tab pick a place, Enter or a click opens or folds one with places in it, G
    hands back 「我去…」 for the command box (`go`), Shift+arrows and a drag move the map.
    Laid out for the screen it is on, and again when that changes size."""

    AUTO_FOCUS = None
    BINDINGS = [
        Binding("escape", "dismiss", "关闭"),
        Binding("up", "step(0, -1)", show=False),
        Binding("down", "step(0, 1)", show=False),
        Binding("left", "step(-1, 0)", show=False),
        Binding("right", "step(1, 0)", show=False),
        Binding("tab", "cycle(1)", show=False),
        Binding("shift+tab", "cycle(-1)", show=False),
        Binding("enter,space", "fold", show=False),
        Binding("g", "go", show=False),
        Binding("shift+up", "pan(0, -3)", show=False),
        Binding("shift+down", "pan(0, 3)", show=False),
        Binding("shift+left", "pan(-8, 0)", show=False),
        Binding("shift+right", "pan(8, 0)", show=False),
        Binding("home", "home", show=False),
    ]

    def __init__(self, module: Module, state: GameState, go: bool = False):
        super().__init__()
        self.m, self.st, self.can_go = module, state, go
        self.c = chart(module, state)
        self.short = short_names(module, self.c)
        self.opened = default_open(self.c)
        self.nest = tree(self.c, self.opened)
        self.picked = self.nest.drawn(self.c.here) if self.c.here in self.c.names else next(iter(self.c.names), "")
        self.laid: Plan | None = None
        self.room = (40, 10)  # the most the map's window may take, in cells
        self.wide = False  # the place's notes beside the map, not under it
        self.side = 0  # their width there
        self.notes = 4  # lines for them under it
        self.drawn_for: tuple[int, int] | None = None  # the screen size the map was laid out for
        self._painted: tuple | None = None  # (the plan, its painted ground): made again when the plan is

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="map-modal"):
            yield Static("地图", classes="modal-title")
            with Container(id="map-body"):
                with MapView(id="map-view"):
                    yield MapCanvas(id="map")
                with PlaceView(id="map-place"):
                    yield Static(id="place")
            yield Static(legend(self.c, self.nest), id="map-legend")
            yield Static(classes="system", id="map-keys")

    def on_mount(self) -> None:
        self._paint()

    def on_resize(self) -> None:
        self._paint()

    def _paint(self) -> None:
        width, height = self.app.size
        if self.drawn_for == (width, height):
            return
        self.drawn_for = (width, height)
        self.room, self.wide, self.side, self.notes = window(width, height, legend(self.c, self.nest))
        body_box = self.query_one("#map-body")
        body_box.styles.layout = "horizontal" if self.wide else "vertical"
        body_box.set_class(not self.wide, "under")
        self._show(replan=True, center=True)

    def _show(self, replan: bool = False, center: bool = False) -> None:
        if replan or self.laid is None:
            room_w, room_h = self.room
            self.laid = plan(self.c, room_w - 1, room_h - 1, self.short, self.opened)  # a cell each way for scrollbars
            wider, taller = self.laid.width > room_w - 1, self.laid.height > room_h - 1
            w = room_w if wider else self.laid.width + taller
            h = room_h if taller else self.laid.height + wider
            view, place = self.query_one(MapView), self.query_one(PlaceView)
            view.styles.width, view.styles.height = w, h
            if self.wide:  # as tall as the map, or a good part of the screen: picking another does not resize it
                place.styles.width, place.styles.height = self.side, max(h, min(room_h, 16))
                inside = w + 2 + self.side
            else:
                inside = max(w, min(room_w, max(60, cell_len(self._keys()[0]))))
                place.styles.width, place.styles.height = inside, self.notes
            self.query_one("#map-modal").styles.width = inside + 6
            long, short = self._keys()
            self.query_one("#map-keys", Static).update(long if cell_len(long) <= inside else short)
        self.query_one(MapCanvas).update(self._drawn())
        place_width = (self.side if self.wide else self.query_one(PlaceView).styles.width.value) - 2  # its scrollbar
        self.query_one("#place", Static).update(about(self.m, self.st, self.c, self.picked, int(place_width)) if self.picked else "")
        self.call_after_refresh(self._follow, center)

    def _drawn(self) -> Content:
        """The map painted (drawing/mapart.py): ground, ways and mist made by program, the names in
        ink on top; in the pure-ASCII art style, the plan as text as before."""
        assert self.laid is not None
        if getattr(self.app, "art_style", "photo") == "ascii":
            return draw(self.c, self.laid, self.picked)
        from ..drawing import mapart

        kinds = {p: self.laid.kinds.get(p, self.c.kinds[p]) for p in self.laid.spots}
        if self._painted is None or self._painted[0] is not self.laid:
            self._painted = (self.laid, mapart.paint(self.laid, kinds, self.c.names, self.m.id))
            self.query_one("#map-legend", Static).update(legend(self.c, self.nest, painted=True))
        return mapart.compose(self.laid, self._painted[1], kinds, TAG, self.picked, MARK)

    def _keys(self) -> tuple[str, str]:
        """The few keys that matter (Tab, Shift+arrows and dragging are in the guide), in full
        and for a narrow window."""
        go = self.can_go and any(p != self.c.here for p in self.c.names)
        long = ["方向键 选地点", "回车 展开", "G 去这里" if go else "", "Esc 关闭"]
        short = ["方向键 选", "回车 展开", "G 去" if go else "", "Esc 关闭"]
        return " · ".join(k for k in long if k), " · ".join(k for k in short if k)

    def _follow(self, center: bool = False) -> None:
        """Keep the place picked in view (the place they are in, when the map opens)."""
        if self.picked and self.laid is not None and self.picked in self.laid.spots:
            area = self.laid.area(self.picked)
            view = self.query_one(MapView)
            if area.width > view.size.width or area.height > view.size.height:  # a frame bigger than the window: its name
                x0, x1, y = self.laid.box(self.picked)
                area = Region(x0 - 2, y - 1, x1 - x0 + 5, 3)
            view.scroll_to_region(area, animate=False, center=center, immediate=True)

    def action_step(self, dx: int, dy: int) -> None:
        if self.laid is None or not self.picked:
            return
        found = _toward({p: self.laid.box(p) for p in self.laid.spots}, self.picked, dx, dy)
        if found:
            self.picked = found
            self._show()
        else:  # nothing further that way: look further that way
            self.action_pan(dx * 8, dy * 3)

    def action_cycle(self, step: int) -> None:
        if self.laid is None or not self.picked:
            return
        spots = self.laid.spots
        order = sorted(spots, key=lambda p: (spots[p][1], spots[p][0]))  # reading order
        self.picked = order[(order.index(self.picked) + step) % len(order)]
        self._show()

    def action_fold(self) -> None:
        if self.picked in self.nest.kids:
            self.opened ^= {self.picked}
            self.nest = tree(self.c, self.opened)
            self.query_one("#map-legend", Static).update(legend(self.c, self.nest))
            self._show(replan=True)

    def action_go(self) -> None:
        if self.can_go and self.picked and self.picked != self.c.here:
            self.dismiss(self.c.names[self.picked])
        else:
            self.app.bell()

    def action_pan(self, dx: int, dy: int) -> None:
        self.query_one(MapView).scroll_relative(dx, dy, animate=False)

    def action_home(self) -> None:
        if self.c.here in self.c.names:
            self.picked = self.nest.drawn(self.c.here)
            self._show(center=True)

    def on_map_canvas_clicked(self, event: MapCanvas.Clicked) -> None:
        hit = self.laid.at(event.x, event.y) if self.laid is not None else None
        if hit is None:
            return
        self.picked = hit
        if hit in self.nest.kids:
            self.action_fold()
        else:
            self._show()
