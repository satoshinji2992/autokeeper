"""The map painted: the plan's names and ways (tui/modals/mapview.py) over ground made by program.

No drawing to lean on and no line art: every cell is two pixels of a generated picture (the top
one in the glyph ▀, the bottom one behind it), the names and markers written in ink on top.

    paper    a parchment tone, grain in three octaves of value noise, fibres, a few stains,
             darker towards the edges
    ground   round each place, the land its name says it is: water, woods, a graveyard (small
             crosses), streets (cobbles), a house or a hall (a walled plot, boards inside), fields
    ways     the routed paths as dirt tracks; a way not yet walked fainter and broken
    mist     over what the investigators have only heard of, thicker the further from where
             they have been

All of it is seeded by the module and the place, so a map looks the same every time it is drawn.
The user, 2026-10-21: the map's art should be made by program, not stick drawings."""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass

import numpy as np
from rich.cells import cell_len
from textual.content import Content

Rgb = tuple[int, int, int]


@dataclass(frozen=True)
class Ground:
    tint: Rgb
    pattern: str  # speckle | trees | crosses | cobbles | plot | ripples | plain


PAPER: Rgb = (214, 197, 158)
INK: Rgb = (46, 30, 14)
INK_HERE: Rgb = (140, 28, 22)
INK_HEARD: Rgb = (110, 92, 66)
ROAD: Rgb = (110, 76, 44)
TRAIL: Rgb = (150, 122, 84)
MIST: Rgb = (226, 220, 206)
WALL: Rgb = (96, 70, 44)

GROUNDS: list[tuple[tuple[str, ...], Ground]] = [
    (("墓", "坟", "陵", "灵堂"), Ground((112, 128, 96), "crosses")),
    (("河", "湖", "江", "港", "码头", "海", "溪", "池", "湾", "渡"), Ground((104, 140, 156), "ripples")),
    (("林", "树", "森", "丛", "山", "岭", "坡", "谷", "野"), Ground((92, 122, 74), "trees")),
    (("田", "农场", "牧场", "庄", "园", "坪"), Ground((156, 160, 98), "speckle")),
    (("街", "路", "道", "镇", "广场", "车站", "站", "市", "桥", "巷"), Ground((168, 158, 140), "cobbles")),
    (("家", "屋", "房", "宅", "店", "铺", "馆", "堂", "教堂", "所", "院", "局", "厅", "室", "楼", "书房", "卧室",
      "厨房", "地窖", "阁楼", "诊所", "学校", "邮局", "银行", "酒吧", "旅店", "工棚", "棚"), Ground((176, 140, 98), "plot")),
]
FIELD = Ground((170, 168, 120), "speckle")


def ground(name: str) -> Ground:
    """What the land round a place is, read from its name."""
    for words, g in GROUNDS:
        if any(w in name for w in words):
            return g
    return FIELD


def _seed(*parts: str) -> int:
    return int.from_bytes(hashlib.sha1("|".join(parts).encode("utf-8")).digest()[:4], "little")


def noise(h: int, w: int, seed: int, octaves: int = 3) -> np.ndarray:
    """Value noise in [-1, 1]: random grids, coarse to fine, each smoothly enlarged and summed."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cell = max(2, 8 >> o)
        gh, gw = h // cell + 2, w // cell + 2
        grid = rng.uniform(-1, 1, (gh, gw))
        ys, xs = np.linspace(0, gh - 1.001, h), np.linspace(0, gw - 1.001, w)
        y0, x0 = ys.astype(int), xs.astype(int)
        fy, fx = ys - y0, xs - x0
        fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)  # smoothstep
        a = grid[np.ix_(y0, x0)] * (1 - fx) + grid[np.ix_(y0, x0 + 1)] * fx
        b = grid[np.ix_(y0 + 1, x0)] * (1 - fx) + grid[np.ix_(y0 + 1, x0 + 1)] * fx
        out += amp * (a * (1 - fy[:, None]) + b * fy[:, None])
        total += amp
        amp *= 0.5
    return out / total


def _blob(h: int, w: int, cx: float, cy: float, rx: float, ry: float) -> np.ndarray:
    """A soft round weight, 1 in the middle, gone by its edge."""
    yy, xx = np.mgrid[0:h, 0:w]
    d = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
    return np.clip(1 - d, 0, 1) ** 1.5


def _pattern(g: Ground, h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    """The ground's own texture, a darkening in [0, 1] per pixel."""
    out = np.zeros((h, w))
    if g.pattern == "trees":  # dark canopy dots, clustered
        mask = rng.random((h, w)) < 0.16
        out[mask] = 0.45
        out[1:][mask[:-1]] += 0.2  # a shadow under each
    elif g.pattern == "crosses":  # small headstones in rows
        for y in range(1, h - 2, 4):
            for x in range(1 + (y // 4) % 2, w - 1, 3):
                if rng.random() < 0.7:
                    out[y, x] = 0.55
                    out[y + 1, x] = 0.55
    elif g.pattern == "cobbles":
        out[::2, ::2] = 0.12
        out[1::2, 1::2] = 0.08
    elif g.pattern == "ripples":
        out[::3, :] = 0.18
    elif g.pattern == "speckle":
        out[rng.random((h, w)) < 0.08] = 0.2
    return out


@dataclass
class Painted:
    pixels: np.ndarray  # (2 * height, width, 3) floats in [0, 1]


def paint(plan, kinds: dict[str, str], names: dict[str, str], key: str) -> Painted:
    """The picture behind a plan: `kinds` place -> here / been / heard; `names` place -> its full name."""
    w, h = plan.width, plan.height * 2
    seed = _seed(key)
    rng = np.random.default_rng(seed)
    paper = np.array(PAPER, float) / 255
    grain = noise(h, w, seed) * 0.07 + noise(h, w, seed + 1, 2) * 0.05
    fibres = noise(h, w // 6 + 2, seed + 2, 1).repeat(6, axis=1)[:, :w] * 0.025
    img = np.clip(paper[None, None, :] * (1 + grain + fibres)[..., None], 0, 1)

    # stains: a few soft brown rings
    for _ in range(max(1, w * h // 4000)):
        cy, cx = rng.uniform(0, h), rng.uniform(0, w)
        r = rng.uniform(4, 10)
        ring = _blob(h, w, cx, cy, r, r) - _blob(h, w, cx, cy, r * 0.8, r * 0.8)
        img -= ring[..., None] * np.array([0.05, 0.06, 0.08])

    # ground round each place; an open place is its frame's whole floor
    for place, (x, y) in plan.spots.items():
        name = names.get(place) or plan.labels.get(place) or ""
        g = ground(name)
        tint = np.array(g.tint, float) / 255
        local = np.random.default_rng(_seed(key, place))
        if place in plan.frames:
            left, top, right, bottom = plan.frames[place]
            weight = np.zeros((h, w))
            weight[top * 2:(bottom + 1) * 2, left:right + 1] = 0.55
            boards = np.zeros((h, w))
            boards[top * 2:(bottom + 1) * 2:3, left:right + 1] = 0.08  # floorboards
            img = img * (1 - weight[..., None]) + tint * weight[..., None]
            img -= boards[..., None] * 0.6
            continue
        span = cell_len(plan.labels.get(place, "")) + 2
        weight = _blob(h, w, x + span / 2, y * 2 + 1, span / 2 + 7, 8) * 0.8
        texture = _pattern(g, h, w, local) * weight
        img = img * (1 - weight[..., None]) + tint * weight[..., None]
        img -= texture[..., None] * np.array([0.6, 0.55, 0.5])
        if g.pattern == "plot":  # a few houses round the name: tiled roofs over pale walls
            _houses(img, local, x, y * 2, span, taken={(cx, cy) for cx in range(x, x + span) for cy in (y * 2, y * 2 + 1)})

    # frames' walls, a pixel thick whichever way they run (a cell is twice as tall as wide)
    wall = np.array(WALL, float) / 255
    for (x, y), mask in plan.walls.items():
        for py in _rows(y, mask):
            img[py, x] = wall

    # ways: dirt tracks along the routed cells; a way not yet walked fainter and broken
    road, trail = np.array(ROAD, float) / 255, np.array(TRAIL, float) / 255
    dirt = noise(h, w, seed + 7, 2)
    for (x, y), (mask, kind) in plan.lines.items():
        if kind == "trail" and (x + y) % 2:
            continue
        colour = road if kind == "road" else trail
        for py in _rows(y, mask):
            img[py, x] = colour * (1 + dirt[py, x] * 0.12)
            if py - 1 >= 0 and (py - 1) // 2 == y and py - 1 not in _rows(y, mask):  # a worn edge beside it
                img[py - 1, x] = img[py - 1, x] * 0.75 + colour * 0.25

    # mist over what has only been heard of, thicker away from where they have been
    known = [(x, y) for p, (x, y) in plan.spots.items() if kinds.get(p) in ("here", "been")]
    if known:
        yy, xx = np.mgrid[0:h, 0:w]
        near = np.full((h, w), np.inf)
        for x, y in known:
            near = np.minimum(near, np.hypot((xx - x - 3) / 1.6, yy - y * 2))
        fog = np.clip((near - 10) / 30, 0, 0.55) * (0.8 + 0.2 * noise(h, w, seed + 9, 2))
        mist = np.array(MIST, float) / 255
        img = img * (1 - fog[..., None]) + mist * fog[..., None]

    # darker towards the edges
    yy, xx = np.mgrid[0:h, 0:w]
    edge = np.minimum.reduce([xx, w - 1 - xx, (yy) / 2, (h - 1 - yy) / 2]).astype(float)
    img *= (0.82 + 0.18 * np.clip(edge / 4, 0, 1))[..., None]
    return Painted(np.clip(img, 0, 1))


def _rows(y: int, mask: int) -> tuple[int, ...]:
    """The pixel rows a way or a wall takes in cell row y: the upper one, and the lower one too
    where it goes on downwards (mask: N 1, E 2, S 4, W 8). Across, a pixel is as thick as a
    cell is wide; down, the whole cell."""
    return (y * 2, y * 2 + 1) if mask & 4 else (y * 2,)


ROOF: Rgb = (138, 62, 44)
HOUSE: Rgb = (226, 212, 184)


def _houses(img: np.ndarray, rng: np.random.Generator, x: int, y: int, span: int, taken: set[tuple[int, int]]) -> None:
    """Small houses scattered round a name: a roof two or three pixels wide, its wall under it,
    a shadow to the right. Not on the name's own cells."""
    h, w = img.shape[:2]
    roof, wall = np.array(ROOF, float) / 255, np.array(HOUSE, float) / 255
    for _ in range(int(rng.integers(3, 6))):
        hx = int(rng.integers(x - 3, x + span + 3))
        hy = int(rng.choice([y - 3, y - 2, y + 3, y + 4]))
        width = int(rng.integers(2, 4))
        cells = [(hx + i, hy + j) for i in range(width + 1) for j in (0, 1)]
        if any(not (0 <= cx < w and 0 <= cy < h) or (cx, cy) in taken for cx, cy in cells):
            continue
        tone = 0.9 + rng.random() * 0.2
        for i in range(width):
            img[hy, hx + i] = roof * tone
            img[hy + 1, hx + i] = wall
        img[hy + 1, hx + width] = img[hy + 1, hx + width] * 0.7  # its shadow
        taken.update(cells)


def _hex(c: np.ndarray) -> str:
    r, g, b = (np.clip(c, 0, 1) * 255).astype(int)
    return f"#{r:02x}{g:02x}{b:02x}"


def compose(plan, painted: Painted, kinds: dict[str, str], tag: str, picked: str | None, marks: dict[str, str]) -> Content:
    """The picture as text, half a cell a pixel, the names and markers in ink on top."""
    px = painted.pixels
    text: dict[tuple[int, int], tuple[str, str]] = {}

    def write(x: int, y: int, words: str, style: str) -> int:
        for ch in words:
            text[(x, y)] = (ch, f"{style} on {wash}")
            for k in range(1, cell_len(ch)):
                text[(x + k, y)] = ("", "")  # covered by a wide character
            x += cell_len(ch)
        return x

    for place, (x, y) in plan.spots.items():
        kind = kinds.get(place, "heard")
        ink = INK_HERE if kind == "here" else INK_HEARD if kind == "heard" else INK
        style = f"bold #{ink[0]:02x}{ink[1]:02x}{ink[2]:02x}" if kind != "heard" else f"italic #{ink[0]:02x}{ink[1]:02x}{ink[2]:02x}"
        if place == picked:
            style += " underline"
        end_x = x + 2 + cell_len(plan.labels[place]) + (cell_len(tag) if place == plan.tagged else 0)
        cells = px[y * 2:y * 2 + 2, x:min(end_x, plan.width)].reshape(-1, 3)
        wash = _hex(np.clip(cells.mean(axis=0) * 1.08 + 0.04, 0, 1))  # one paler wash under the whole name, to read
        write(x, y, marks[kind], style)
        end = write(x + 2, y, plan.labels[place], style)
        if place == plan.tagged:
            write(end, y, tag, f"bold #{INK_HERE[0]:02x}{INK_HERE[1]:02x}{INK_HERE[2]:02x}")
    rows = []
    for y in range(plan.height):
        parts: list[tuple[str, str]] = []
        for x in range(plan.width):
            top, bottom = px[y * 2, x], px[y * 2 + 1, x]
            if (x, y) in text:
                ch, style = text[(x, y)]
                if ch:
                    parts.append((ch, style))
            else:
                parts.append(("▀", f"{_hex(top)} on {_hex(bottom)}"))
        runs = itertools.groupby(parts, key=lambda part: part[1])
        rows.append(Content.assemble(*(("".join(ch for ch, _ in run), style) for style, run in runs)))
    return Content("\n").join(rows)
