"""The map (F5): the places the investigators know, laid out in two dimensions.

engine/fog.py knows which places those are. Here they become a chart (the places on the
map, the roads walked, the trails seen but not taken, and the rumours whose way is still
unknown), then a plan (networkx lays the places out, they snap to a grid of rows and
columns as wide as their names, and every connection is routed around the names as a
box-drawing path), then text. All of that is pure; MapModal only shows it.
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field

import networkx as nx
from rich.cells import cell_len
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static

from .. import weather
from ..engine import fog
from ..engine.state import GameState
from ..module.schema import Module

MARK = {"here": "◉", "been": "●", "heard": "○"}
STYLE = {  # theme colours, resolved when drawn ($text-muted would lose its transparency inline)
    "here": "bold $accent",
    "you": "bold reverse $accent",  # the current place's name
    "been": "bold $foreground",
    "heard": "italic $foreground 60%",
    "road": "$primary",
    "trail": "$secondary",
    "tag": "bold $accent",
    "note": "$foreground 60%",
}
TAG = " ◂ 你在这里"
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
BEND, CROSS, CLASH = 3, 8, 30  # what a turn, a crossing and running along another path cost a route


# -- what the map shows -------------------------------------------------------------------
@dataclass
class Chart:
    """The places the investigators know and the ways between them."""

    here: str
    names: dict[str, str]  # every place on the map -> the name it has there
    kinds: dict[str, str]  # -> "here", "been" (stopped there or walked through) or "heard"
    roads: list[tuple[str, str]] = field(default_factory=list)  # between places they have been
    trails: list[tuple[str, str]] = field(default_factory=list)  # to a place they saw, or were shown, from where they stood
    rumours: dict[str, str] = field(default_factory=dict)  # heard of, the way there unknown: listed below the map


def chart(m: Module, st: GameState) -> Chart:
    """Only what the investigators know: places they have been to or heard of, never one they
    have not, not even as a blank (a door to nowhere gives a secret away). A road joins two
    places they have been to; a trail leads from where they stood to a place they saw or were
    shown from there. The rest of what they heard of has no known way there yet."""
    places = {loc.id: loc for loc in m.locations}
    g = fog.network(m)
    visited = [v for v in dict.fromkeys([*st.visited, st.location]) if v in places]
    heard = fog.heard(m, st)
    been = fog.been(m, st, heard)
    trails: list[tuple[str, str]] = []
    for v in visited:  # they looked around where they stopped, not on the way through
        seen = [places[v].read_aloud, *(f"{c.title}\n{c.text}" for c in m.clues if c.where == v and c.id in st.clues)]
        if v == m.start:
            seen.append(m.opening)
        named = fog.mentions(m, seen)
        trails += [(v, p) for p in places if p in named and p in heard and p not in been and g.has_edge(v, p)]
    pointed = {p for _, p in trails}
    names = {p: places[p].name for p in places if p in been} | {p: heard[p] for p in places if p in pointed}
    kinds = {p: "heard" if p in pointed else "been" for p in names}
    if st.location in kinds:
        kinds[st.location] = "here"
    return Chart(
        here=st.location,
        names={p: names[p] for p in places if p in names},  # module order: the same map every time
        kinds=kinds,
        roads=[(a, b) for a, b in g.edges if a in been and b in been],
        trails=trails,
        rumours={p: heard[p] for p in places if p in heard and p not in names},
    )


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


# -- where everything goes -----------------------------------------------------------------
@dataclass
class Plan:
    """The chart on a grid of cells."""

    width: int
    height: int
    spots: dict[str, tuple[int, int]]  # place -> its marker
    labels: dict[str, str]  # place -> the name written beside it
    tagged: str  # the place "你在这里" is written beside, if any
    lines: dict[tuple[int, int], tuple[int, str]] = field(default_factory=dict)  # cell -> (directions, "road" or "trail")

    def box(self, place: str) -> tuple[int, int, int]:
        """A place's marker and name as (first x, last x, y)."""
        x, y = self.spots[place]
        return x, x + 1 + cell_len(self.labels[place]) + (cell_len(TAG) if place == self.tagged else 0), y


def plan(c: Chart, width: int, height: int, short: dict[str, str] | None = None) -> Plan | None:
    """Lay the chart out in at most width x height cells: full names and the "you are here" tag
    if they fit, else without the tag, else short names, else closer rows; None if nothing fits."""
    if not c.names:
        return None
    tries = [(c.names, True, 2), (c.names, False, 2)]
    if short and short != c.names:
        tries.append((short, False, 2))
    tries.append((short or c.names, False, 1))
    for labels, tag, gap in tries:
        tag = tag and c.here in c.names
        widths = {p: 2 + cell_len(labels[p]) + (cell_len(TAG) if tag and p == c.here else 0) for p in c.names}
        rows = (height - 1) // (gap + 1) + 1
        slots = arrange(c, widths, width - 2 * MARGIN, rows)
        if slots is None:
            continue
        p = _spots(c, slots, widths, labels, tag, gap)
        if p.width <= width and p.height <= height and _route(c, p, widths):
            return p
    return None


def arrange(c: Chart, widths: dict[str, int], width: int, rows: int) -> dict[str, tuple[int, int]] | None:
    """A (column, row) slot for every place: networkx's spring layout snapped to a grid, then
    tidied place by place while that makes the connections shorter, straighter and less
    tangled. None if the names cannot fit side by side in `width` cells."""
    g = nx.Graph()
    g.add_nodes_from(c.names)
    g.add_edges_from(c.roads + c.trails)
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
            pos = nx.spring_layout(g, seed=seed) if n > 1 else {p: (0.0, 0.0) for p in g}
            slots = _snap(g, pos, cols, grid_rows)
            snapped.append((_cost(g, slots, widths, width), cols, grid_rows, slots))
    if not snapped:
        return None
    snapped.sort(key=lambda t: t[0])  # only the most promising are worth tidying
    tidied = [_tidy(g, slots, widths, width, cols, grid_rows) for _, cols, grid_rows, slots in snapped[:2]]
    best = min(tidied, key=lambda slots: _cost(g, slots, widths, width))
    if _overflow(best, widths, width):
        return None
    used_cols = sorted({x for x, _ in best.values()})
    used_rows = sorted({y for _, y in best.values()})
    return {p: (used_cols.index(x), used_rows.index(y)) for p, (x, y) in best.items()}


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


def _spots(c: Chart, slots: dict, widths: dict[str, int], labels: dict[str, str], tag: bool, gap: int) -> Plan:
    cols = max(x for x, _ in slots.values()) + 1
    colw = [max(widths[p] for p, (x, _) in slots.items() if x == k) for k in range(cols)]
    xs = [MARGIN + sum(colw[:k]) + k * GAP for k in range(cols)]
    spots = {p: (xs[x], y * (gap + 1)) for p, (x, y) in slots.items()}
    return Plan(
        width=xs[-1] + colw[-1] + MARGIN,
        height=max(y for _, y in spots.values()) + 1,
        spots=spots,
        labels={q: labels[q] for q in c.names},
        tagged=c.here if tag else "",
    )


# -- routing -------------------------------------------------------------------------------
def _route(c: Chart, p: Plan, widths: dict[str, int]) -> bool:
    """Route every road, then every trail, as an orthogonal path between the doors of the two
    places: above or below a marker, left of it, or past the end of the name. Straight runs
    between neighbours go first, so the rest bend around them. False if one cannot be routed."""
    blocked: set[tuple[int, int]] = set()
    doors: dict[str, dict[tuple[int, int], int]] = {}
    for place, (x, y) in p.spots.items():
        end = x + widths[place] - 1  # the name's last cell
        blocked |= {(i, y) for i in range(x, end + 2)}  # marker, name, and a space after it
        doors[place] = {(x, y - 1): N, (x, y + 1): S, (x - 1, y): W, (end + 2, y): E}  # door -> the way out
    every = {cell for place in doors for cell in doors[place]}
    ways = [("road", e) for e in c.roads] + [("trail", e) for e in c.trails]
    ways.sort(key=lambda w: (w[0] != "road", not _clear(p, *w[1]), _apart(p, *w[1])))
    for kind, (a, b) in ways:
        # Out of a door straight ahead or to either side, into one the same way; turning in the
        # doorway costs a bend. Other places' doors are kept clear, or a path passing would seem to go in.
        starts = {(*cell, h): 0 if h == out else BEND for cell, out in doors[a].items() for h in (out, *TURNS[out])}
        goals = {(*cell, h): 0 if h == BACK[out] else BEND for cell, out in doors[b].items() for h in (BACK[out], *TURNS[out])}
        own = set(doors[a]) | set(doors[b])
        way = _astar(p, blocked | (every - own), starts, goals, own)
        if way is None:
            return False
        _mark(p, way, kind, BACK[doors[a][way[0]]], BACK[doors[b][way[-1]]])
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
    return 0 <= x < p.width and 0 <= y < p.height and cell not in blocked


def _astar(p: Plan, blocked: set, starts: dict, goals: dict, forks: set) -> list[tuple[int, int]] | None:
    """The cheapest way through free cells from a start to a goal, both (x, y, heading) -> the
    cost of turning in that doorway: few turns, crossing other paths rather than running along
    them. At the doors of the two places it joins (`forks`) it may meet their other paths: a
    fork in the road."""
    starts = {s: v for s, v in starts.items() if _free(s[:2], p, blocked)}
    goals = {g: v for g, v in goals.items() if _free(g[:2], p, blocked)}
    if not starts or not goals:
        return None
    cells = {g[:2] for g in goals}

    def guess(x: int, y: int) -> int:
        return min(abs(x - gx) + abs(y - gy) for gx, gy in cells)

    frontier: list = []
    tie = itertools.count()
    for (x, y, d), turn in starts.items():
        cost = turn + _toll(p, (x, y), d, forks) + goals.get((x, y, d), 0)
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
        for nd in (d, *TURNS[d]):
            nx_, ny = x + STEP[nd][0], y + STEP[nd][1]
            if not (0 <= nx_ < p.width and 0 <= ny < p.height) or (nx_, ny) in blocked or (nx_, ny, nd) in came:
                continue
            step = 1 + (BEND if nd != d else 0) + _toll(p, (nx_, ny), nd, forks) + goals.get((nx_, ny, nd), 0)
            if nd != d and (x, y) in p.lines and (x, y) not in forks:
                step += CLASH  # turning on another path joins it
            heapq.heappush(frontier, (cost + step + guess(nx_, ny), cost + step, next(tie), nx_, ny, nd, (x, y, d)))
    return None


def _toll(p: Plan, cell: tuple[int, int], heading: int, forks: set) -> float:
    """What stepping onto a cell another path already uses costs."""
    if cell not in p.lines:
        return 0
    if cell in forks:
        return 0.5
    across = W | E if heading in (N, S) else N | S
    return CROSS if p.lines[cell][0] == across else CLASH


def _mark(p: Plan, way: list[tuple[int, int]], kind: str, start: int, end: int) -> None:
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


# -- text ---------------------------------------------------------------------------------
def draw(c: Chart, p: Plan) -> Content:
    """The plan as text, in the theme's colours."""
    grid: list[list[tuple[str, str] | None]] = [[(" ", "")] * p.width for _ in range(p.height)]
    for (x, y), (mask, kind) in p.lines.items():
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
        kind = c.kinds[place]
        write(x, y, MARK[kind], STYLE[kind])
        end = write(x + 2, y, p.labels[place], STYLE["you"] if kind == "here" else STYLE[kind])
        if place == p.tagged:
            write(end, y, TAG, STYLE["tag"])
    rows = []
    for row in grid:
        cells = [cell for cell in row if cell is not None]
        runs = itertools.groupby(cells, key=lambda cell: cell[1])
        rows.append(Content.assemble(*(("".join(ch for ch, _ in run), style) for style, run in runs)))
    return Content("\n").join(rows)


def listing(c: Chart) -> Content:
    """The map as a list, for a terminal too small to draw it: every place and where it leads."""
    ways: dict[str, list[str]] = {p: [] for p in c.names}
    for a, b in c.roads + c.trails:
        ways[a].append(c.names[b])
        ways[b].append(c.names[a])
    rows = []
    for place in sorted(c.names, key=lambda p: ("here", "been", "heard").index(c.kinds[p])):
        kind = c.kinds[place]
        parts: list = [(f"{MARK[kind]} {c.names[place]}", STYLE[kind])]
        if kind == "here":
            parts.append(("（你在这里）", STYLE["tag"]))
        if ways[place]:
            parts.append((f"  通往 {'、'.join(ways[place])}", STYLE["note"]))
        rows.append(Content.assemble(*parts))
    return Content("\n").join(rows)


def legend(m: Module, st: GameState, c: Chart) -> Content:
    """What the marks mean, the places with no known way there, and what happened where:
    the clues found, the people met, the weather over the open-air places."""
    keys = [(MARK["here"], "here", "你在这里"), (MARK["been"], "been", "去过")]
    if "heard" in c.kinds.values() or c.rumours:
        keys.append((MARK["heard"], "heard", "听说过"))
    if c.roads:
        keys.append(("──", "road", "走过的路"))
    if c.trails:
        keys.append(("┄┄", "trail", "还没走过的路"))
    rows = [Content("   ").join(Content.assemble((mark, STYLE[kind]), f" {label}") for mark, kind, label in keys)]
    if c.rumours:
        rows.append(Content.assemble(("听说过，还不知道怎么去：", STYLE["note"]), *_names(list(c.rumours.values()))))
    found: dict[str, int] = {}
    for cid in st.clues:
        where = next((x.where for x in m.clues if x.id == cid), None)
        if where:
            found[where] = found.get(where, 0) + 1
    met: dict[str | None, list[str]] = {}
    for n in m.npcs:
        if st.npcs.get(n.id) and st.npcs[n.id].met:
            met.setdefault(n.location, []).append(n.name)
    order = sorted((p for p, k in c.kinds.items() if k != "heard"), key=lambda p: p != c.here)
    for place in order:
        notes = [SKY_MARK.get(st.sky, "")] if st.sky and weather.outdoors(c.names[place]) else []
        notes += ([f"线索 {found[place]}"] if found.get(place) else []) + met.get(place, [])
        if notes:
            kind = c.kinds[place]
            rows.append(Content.assemble((f"{MARK[kind]} {c.names[place]}", STYLE[kind]), ("  " + " · ".join(notes), STYLE["note"])))
    return Content("\n").join(rows)


def _names(names: list[str]) -> list:
    parts: list = []
    for i, name in enumerate(names):
        parts += [("、", STYLE["note"])] if i else []
        parts.append((f"{MARK['heard']} {name}", STYLE["heard"]))
    return parts


def view(m: Module, st: GameState, width: int, height: int) -> Content:
    """Everything the map modal shows, for a body of width x height cells (the legend may scroll)."""
    c = chart(m, st)
    p = plan(c, width, height, short_names(m, c))
    body = draw(c, p) if p is not None else listing(c)
    return Content("\n\n").join([body, legend(m, st, c)])


# -- on screen ----------------------------------------------------------------------------
class MapModal(ModalScreen[None]):
    """F5. Drawn for the screen it is on, and again when that changes size."""

    BINDINGS = [("escape", "dismiss", "关闭"), ("enter", "dismiss", "关闭")]

    def __init__(self, module: Module, state: GameState):
        super().__init__()
        self.m, self.st = module, state
        self.drawn_for: tuple[int, int] | None = None  # the screen size the map was laid out for

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("地图", classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(id="map")
            yield Static("Esc 关闭", classes="system")

    def on_mount(self) -> None:
        self._paint()

    def on_resize(self) -> None:
        self._paint()

    def _paint(self) -> None:
        width, height = self.app.size
        if self.drawn_for == (width, height):
            return
        self.drawn_for = (width, height)
        # .modal is at most 96 wide and 90% of the screen tall, less its border, padding, title
        # and hint; the body keeps room for a scrollbar, and the legend under the map scrolls.
        body = min(30, height * 9 // 10 - 7)
        self.query_one(VerticalScroll).styles.max_height = max(3, body)
        self.query_one("#map", Static).update(view(self.m, self.st, max(20, min(88, width - 10)), max(5, min(22, body - 4))))
