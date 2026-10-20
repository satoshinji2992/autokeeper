"""Scenes painted by program, for places with no picture: a module's that came without one, or
one the director made up during play.

What the place is comes from its name and its description (an office, a study, a cellar, a
shop, a bedroom; a street, a graveyard, a church, a station, water, woods, hills, fields), and
the scene is built from shapes: a room in perspective with its window and its furniture, a
street of facades running to the horizon, headstones under bare trees, a spire against the sky.
Big shapes and strong values, since chafa brings it down to a few dozen characters; grain and a
soft blur so it reads as paint, not diagrams. Seeded by the place, so it is the same every time.
The picture then goes the way a photo goes (imagery.py): lit for the hour, weather over it, the
faces of who is there set into it. It is cached under data/cache (paths.scenery), never in the
module. The user, 2026-10-21: art made by program, not stick drawings."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

if TYPE_CHECKING:
    from .module.schema import Module

W, H = 960, 540

Rgb = tuple[int, int, int]

KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("cellar", ("地窖", "地下室", "墓室", "隧道", "地道", "下水道", "洞")),
    ("study", ("书房", "图书馆", "藏书", "档案", "阅览")),
    ("office", ("警察局", "局", "报社", "社", "邮局", "银行", "办公", "事务所", "诊所", "医院", "法庭", "市政")),
    ("shop", ("店", "铺", "酒吧", "餐馆", "饭馆", "牛排馆", "咖啡", "旅馆", "旅店", "发廊", "车行", "杂货")),
    ("bedroom", ("卧室", "客房", "出租屋", "房间", "阁楼", "病房")),
    ("glasshouse", ("温室", "天文台", "观测")),
    ("graveyard", ("墓", "坟", "陵", "公墓")),
    ("church", ("教堂", "会堂", "礼拜", "修道")),
    ("station", ("车站", "站台", "火车", "铁路")),
    ("water", ("河", "湖", "江", "港", "码头", "海", "溪", "湾", "渡口")),
    ("woods", ("林", "森", "树")),
    ("hills", ("山", "岭", "坡", "谷", "崖")),
    ("fields", ("农场", "田", "牧场", "庄园", "麦")),
    ("street", ("街", "镇", "广场", "路", "巷", "桥", "市场", "中心")),
    ("house", ("家", "宅", "屋", "房", "门廊", "院")),
]
INDOOR = {"cellar", "study", "office", "shop", "bedroom"}


def kind_of(name: str, description: str = "") -> str:
    """What sort of place this is: by its name first, then by what is said of it."""
    for text in (name, description):
        for kind, words in KINDS:
            if any(w in text for w in words):
                return kind
    return "street"


def _seed(*parts: str) -> int:
    return int.from_bytes(hashlib.sha1("|".join(parts).encode("utf-8")).digest()[:4], "little")


@dataclass
class Brush:
    """The canvas, its drawer and the place's own random numbers."""

    img: Image.Image
    d: ImageDraw.ImageDraw
    rng: np.random.Generator

    def tone(self, rgb: Rgb, spread: float = 0.08) -> Rgb:
        k = 1 + self.rng.uniform(-spread, spread)
        return tuple(int(max(0, min(255, c * k))) for c in rgb)  # type: ignore[return-value]


def _gradient(top: Rgb, bottom: Rgb, h: int = H) -> np.ndarray:
    t = np.linspace(0, 1, h)[:, None]
    return (np.array(top) * (1 - t) + np.array(bottom) * t)[:, None, :].repeat(W, axis=1)


def _noise(rng: np.random.Generator, scale: int = 24) -> np.ndarray:
    """Soft value noise over the canvas, in [-1, 1]."""
    small = rng.uniform(-1, 1, (H // scale + 2, W // scale + 2))
    return np.asarray(Image.fromarray(((small + 1) * 127.5).astype(np.uint8)).resize((W, H), Image.Resampling.BICUBIC),
                      float) / 127.5 - 1


def _canvas(top: Rgb, bottom: Rgb, rng: np.random.Generator) -> Brush:
    base = _gradient(top, bottom) * (1 + _noise(rng)[..., None] * 0.04)
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    return Brush(img, ImageDraw.Draw(img), rng)


# -- outdoors ---------------------------------------------------------------------------------
SKY = ((122, 146, 170), (206, 200, 182))


def _sky(rng: np.random.Generator) -> Brush:
    b = _canvas(*SKY, rng)
    for _ in range(int(rng.integers(3, 7))):  # long soft clouds
        x, y = rng.uniform(-100, W), rng.uniform(20, H * 0.35)
        w, h = rng.uniform(160, 380), rng.uniform(18, 42)
        b.d.ellipse([x, y, x + w, y + h], fill=b.tone((222, 220, 212), 0.04))
    return b


def _ridge(b: Brush, y: float, rough: float, colour: Rgb, *, step: int = 24) -> None:
    """A line of land across the picture, and everything under it."""
    pts, h = [], y
    for x in range(-step, W + step * 2, step):
        h += b.rng.uniform(-rough, rough)
        h = min(max(h, y - rough * 3), y + rough * 3)
        pts.append((x, h))
    b.d.polygon([(-step, H), *pts, (W + step * 2, H)], fill=colour)


def _tree(b: Brush, x: float, base: float, size: float, colour: Rgb, *, bare: bool = False) -> None:
    trunk = b.tone((58, 46, 36))
    b.d.rectangle([x - size * 0.06, base - size * 0.9, x + size * 0.06, base], fill=trunk)
    if bare:  # branches, forking twice
        def branch(x0, y0, angle, length, depth):
            x1, y1 = x0 + math.cos(angle) * length, y0 - math.sin(angle) * length
            b.d.line([x0, y0, x1, y1], fill=trunk, width=max(1, int(depth * 2)))
            if depth > 0:
                for turn in (-0.45, 0.4):
                    branch(x1, y1, angle + turn + b.rng.uniform(-0.15, 0.15), length * 0.68, depth - 1)
        branch(x, base - size * 0.8, math.pi / 2, size * 0.35, 3)
        return
    for _ in range(5):  # a crown of overlapping blobs
        r = size * b.rng.uniform(0.25, 0.4)
        cx, cy = x + b.rng.uniform(-0.3, 0.3) * size, base - size * b.rng.uniform(0.8, 1.15)
        b.d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=b.tone(colour, 0.12))


def _conifer(b: Brush, x: float, base: float, size: float, colour: Rgb) -> None:
    for i in range(3):
        top, width = base - size * (0.45 + i * 0.28), size * (0.42 - i * 0.1)
        y0 = base - size * i * 0.28
        b.d.polygon([(x, top - size * 0.2), (x - width, y0), (x + width, y0)], fill=b.tone(colour, 0.06))


def _facade(b: Brush, x: float, base: float, w: float, h: float, colour: Rgb, *, roof: bool = True, lit: float = 0.2) -> None:
    """A building's front: wall, roof, rows of windows (some lit)."""
    b.d.rectangle([x, base - h, x + w, base], fill=b.tone(colour))
    if roof:
        b.d.polygon([(x - w * 0.06, base - h), (x + w / 2, base - h - w * 0.35), (x + w * 1.06, base - h)], fill=b.tone((86, 58, 48)))
    rows, cols = max(1, int(h // 70)), max(1, int(w // 46))
    for r in range(rows):
        for c in range(cols):
            wx = x + w * (c + 0.5) / cols - 9
            wy = base - h + 22 + r * (h - 30) / rows
            glow = b.rng.random() < lit
            b.d.rectangle([wx, wy, wx + 18, wy + 28], fill=(236, 202, 128) if glow else b.tone((52, 58, 66)))


def _street(b: Brush) -> None:
    horizon = H * 0.52
    _ridge(b, horizon, 4, b.tone((140, 136, 124)))
    vx = W / 2 + b.rng.uniform(-60, 60)
    b.d.polygon([(vx - 8, horizon), (vx + 8, horizon), (W * 0.8, H), (W * 0.2, H)], fill=b.tone((98, 92, 84)))
    for side in (-1, 1):  # facades on both sides, smaller towards the horizon
        x = W / 2 + side * 40
        size = 1.0
        while 0 < x < W and size < 9:
            w, h = 40 * size, 80 * size * b.rng.uniform(0.9, 1.4)
            left = x if side > 0 else x - w
            _facade(b, left, horizon + 18 * size, w, h, b.tone((150, 128, 104), 0.15), lit=0.15)
            x += side * w * 1.02
            size *= 1.35


def _graveyard(b: Brush) -> None:
    horizon = H * 0.58
    _ridge(b, horizon - 30, 10, b.tone((96, 104, 98)))
    for x in b.rng.uniform(0, W, 4):
        _tree(b, x, horizon - 10, b.rng.uniform(120, 200), (60, 62, 58), bare=True)
    _ridge(b, horizon, 6, b.tone((88, 100, 78)))
    for row in range(4):
        y = horizon + 30 + row * 55
        s = 0.6 + row * 0.35
        for x in np.arange(b.rng.uniform(0, 60), W, 110 * s):
            x += b.rng.uniform(-20, 20)
            stone = b.tone((150, 148, 140), 0.1)
            if b.rng.random() < 0.3:  # a cross
                b.d.rectangle([x - 4 * s, y - 50 * s, x + 4 * s, y], fill=stone)
                b.d.rectangle([x - 16 * s, y - 38 * s, x + 16 * s, y - 30 * s], fill=stone)
            else:
                b.d.rounded_rectangle([x - 15 * s, y - 40 * s, x + 15 * s, y], radius=int(12 * s), fill=stone)
                b.d.rectangle([x - 15 * s, y - 4 * s, x + 18 * s, y + 3 * s], fill=b.tone((70, 76, 62)))  # its shadow


def _church(b: Brush) -> None:
    horizon = H * 0.9
    _ridge(b, horizon, 4, b.tone((104, 112, 90)))
    cx, w = W / 2 + b.rng.uniform(-80, 80), 230
    wall = b.tone((168, 156, 138))
    b.d.rectangle([cx - w / 2, horizon - 190, cx + w / 2, horizon], fill=wall)
    b.d.polygon([(cx - w / 2 - 10, horizon - 190), (cx, horizon - 280), (cx + w / 2 + 10, horizon - 190)], fill=b.tone((80, 64, 58)))
    b.d.rectangle([cx - 34, horizon - 300, cx + 34, horizon - 190], fill=wall)  # the tower
    b.d.polygon([(cx - 44, horizon - 300), (cx, horizon - 410), (cx + 44, horizon - 300)], fill=b.tone((72, 60, 56)))
    b.d.rectangle([cx - 3, horizon - 440, cx + 3, horizon - 406], fill=(60, 52, 46))
    b.d.rectangle([cx - 13, horizon - 432, cx + 13, horizon - 426], fill=(60, 52, 46))
    b.d.ellipse([cx - 26, horizon - 160, cx + 26, horizon - 108], fill=b.tone((150, 70, 60)))  # a rose window
    b.d.rounded_rectangle([cx - 28, horizon - 80, cx + 28, horizon], radius=26, fill=b.tone((70, 52, 40)))
    for x in (cx - 160, cx + 150):
        _tree(b, x, horizon, 150, (70, 92, 62))


def _station(b: Brush) -> None:
    horizon = H * 0.55
    _ridge(b, horizon, 3, b.tone((120, 118, 104)))
    b.d.polygon([(0, H * 0.7), (W, H * 0.62), (W, H * 0.7), (0, H * 0.82)], fill=b.tone((118, 110, 98)))  # the platform
    for rail in (0.88, 0.97):
        b.d.line([(0, H * rail), (W, H * (rail - 0.12))], fill=(70, 66, 62), width=6)
    for x in range(0, W, 46):
        b.d.line([(x, H * 0.86 - x * 0.12 / W * H), (x + 14, H * 1.0 - x * 0.12 / W * H)], fill=(84, 70, 58), width=8)
    _facade(b, W * 0.55, H * 0.64, 360, 150, b.tone((140, 112, 92)), lit=0.3)
    b.d.rectangle([W * 0.55 - 20, H * 0.64 - 170, W * 0.55 + 380, H * 0.64 - 150], fill=b.tone((82, 66, 56)))  # the canopy


def _water(b: Brush) -> None:
    horizon = H * 0.5
    _ridge(b, horizon - 6, 6, b.tone((96, 110, 104)))
    sea = _gradient((96, 124, 140), (60, 84, 98), int(H - horizon))
    b.img.paste(Image.fromarray(sea.astype(np.uint8)), (0, int(horizon)))
    for _ in range(140):  # glints
        x, y = b.rng.uniform(0, W), b.rng.uniform(horizon + 6, H)
        length = (y - horizon) * 0.4 + 6
        b.d.line([(x, y), (x + length, y)], fill=b.tone((150, 170, 180), 0.1), width=2)
    for x in (W * 0.12, W * 0.78):  # a pier's posts
        for i in range(6):
            px = x + i * 26
            b.d.rectangle([px, horizon + 30 + i * 8, px + 8, horizon + 120 + i * 20], fill=(58, 48, 40))


def _woods(b: Brush) -> None:
    horizon = H * 0.62
    for layer, colour in enumerate(((112, 128, 116), (84, 104, 86), (58, 78, 60))):
        y = horizon - 60 + layer * 50
        for x in np.arange(-40, W + 40, 70 - layer * 12):
            _conifer(b, x + b.rng.uniform(-20, 20), y + 120 + layer * 30, 160 + layer * 70, colour)
    _ridge(b, H * 0.92, 4, b.tone((60, 70, 52)))


def _hills(b: Brush) -> None:
    for layer, colour in enumerate(((140, 150, 150), (108, 124, 108), (84, 104, 76))):
        _ridge(b, H * (0.45 + layer * 0.14), 22 - layer * 4, b.tone(colour))
    for x in b.rng.uniform(0, W, 6):
        _tree(b, x, H * 0.86, b.rng.uniform(60, 110), (70, 96, 60))


def _fields(b: Brush) -> None:
    horizon = H * 0.55
    _ridge(b, horizon, 4, b.tone((150, 150, 104)))
    vx = W * 0.5
    for i in range(-14, 15):  # furrows to the horizon
        b.d.line([(vx + i * 6, horizon), (vx + i * 90, H)], fill=b.tone((118, 116, 80)), width=3)
    _facade(b, W * 0.12, horizon + 20, 170, 120, b.tone((138, 58, 48)), lit=0.0)  # a barn
    for x in (W * 0.7, W * 0.84):
        _tree(b, x, horizon + 30, 120, (86, 106, 64))


def _house(b: Brush) -> None:
    horizon = H * 0.66
    _ridge(b, horizon, 4, b.tone((104, 116, 88)))
    _facade(b, W * 0.3, horizon + 10, 380, 200, b.tone((158, 120, 96)), lit=0.3)
    b.d.rectangle([W * 0.3 - 20, horizon - 40, W * 0.3 + 400, horizon - 30], fill=b.tone((96, 74, 60)))  # a porch roof
    for x in (W * 0.3, W * 0.3 + 120, W * 0.3 + 240, W * 0.3 + 370):
        b.d.rectangle([x, horizon - 30, x + 8, horizon + 10], fill=b.tone((120, 96, 76)))
    _tree(b, W * 0.15, horizon + 10, 180, (70, 94, 60))
    _tree(b, W * 0.86, horizon + 10, 150, (78, 100, 62), bare=b.rng.random() < 0.4)


def _glasshouse(b: Brush) -> None:
    horizon = H * 0.7
    _ridge(b, horizon, 4, b.tone((96, 110, 86)))
    cx, r = W / 2, 200
    b.d.pieslice([cx - r, horizon - r, cx + r, horizon + r], 180, 360, fill=b.tone((150, 176, 176)))
    for a in range(0, 181, 20):  # the iron ribs of the dome
        x = cx + math.cos(math.radians(180 + a)) * r
        y = horizon + math.sin(math.radians(180 + a)) * r
        b.d.line([(cx, horizon - r * 0.15), (x, y)], fill=(64, 72, 72), width=3)
    b.d.arc([cx - r, horizon - r, cx + r, horizon + r], 180, 360, fill=(64, 72, 72), width=4)
    b.d.line([(cx - 20, horizon - 60), (cx + 90, horizon - 170)], fill=(54, 50, 46), width=14)  # a telescope


# -- indoors ----------------------------------------------------------------------------------
def _dim(rgb: Rgb, k: float) -> Rgb:
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k))


def _room(rng: np.random.Generator, wall: Rgb, floor: Rgb) -> tuple[Brush, tuple[float, float, float, float]]:
    """A room seen from its door: the back wall, side walls, ceiling and a floor of boards."""
    b = _canvas((60, 50, 42), (40, 32, 26), rng)
    x0, y0, x1, y1 = W * 0.24, H * 0.18, W * 0.76, H * 0.7
    b.d.polygon([(0, 0), (x0, y0), (x0, y1), (0, H)], fill=b.tone(_dim(wall, 0.8)))  # left wall
    b.d.polygon([(W, 0), (x1, y0), (x1, y1), (W, H)], fill=b.tone(_dim(wall, 0.72)))  # right wall
    b.d.polygon([(0, 0), (W, 0), (x1, y0), (x0, y0)], fill=b.tone(_dim(wall, 0.6)))  # ceiling
    b.d.rectangle([x0, y0, x1, y1], fill=b.tone(wall))
    b.d.polygon([(x0, y1), (x1, y1), (W, H), (0, H)], fill=b.tone(floor))
    for i in range(1, 12):  # boards to the back wall
        t = i / 12
        b.d.line([(x0 + (x1 - x0) * t, y1), (W * t, H)], fill=b.tone(_dim(floor, 0.82)), width=2)
    return b, (x0, y0, x1, y1)


def _window(b: Brush, x: float, y: float, w: float, h: float) -> None:
    b.d.rectangle([x - 8, y - 8, x + w + 8, y + h + 8], fill=b.tone((70, 56, 44)))
    sky = _gradient((150, 170, 186), (204, 200, 184), int(h))[:, : int(w)]
    b.img.paste(Image.fromarray(sky.astype(np.uint8)), (int(x), int(y)))
    b.d.line([(x + w / 2, y), (x + w / 2, y + h)], fill=(70, 56, 44), width=6)
    b.d.line([(x, y + h / 2), (x + w, y + h / 2)], fill=(70, 56, 44), width=6)


def _shelves(b: Brush, x: float, y: float, w: float, h: float) -> None:
    b.d.rectangle([x, y, x + w, y + h], fill=b.tone((74, 52, 36)))
    for row in range(5):
        top = y + 10 + row * (h - 20) / 5
        bx = x + 8
        while bx < x + w - 12:
            bw = b.rng.uniform(8, 18)
            if b.rng.random() > 0.08:  # a gap where a book is missing
                colour = b.tone(((120, 40, 34), (46, 70, 52), (60, 52, 92), (140, 112, 60))[int(b.rng.integers(4))], 0.2)
                b.d.rectangle([bx, top + b.rng.uniform(0, 8), bx + bw, top + (h - 20) / 5 - 4], fill=colour)
            bx += bw + 2


def _desk(b: Brush, x: float, y: float, w: float, *, lamp: bool = True) -> None:
    b.d.rectangle([x, y, x + w, y + 18], fill=b.tone((96, 66, 44)))
    for lx in (x + 10, x + w - 22):
        b.d.rectangle([lx, y + 18, lx + 12, y + 110], fill=b.tone((76, 52, 34)))
    if lamp:
        glow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(glow).ellipse([x + w * 0.7 - 120, y - 140, x + w * 0.7 + 120, y + 60], fill=90)
        warm = Image.new("RGB", (W, H), (255, 214, 140))
        b.img.paste(warm, (0, 0), glow.filter(ImageFilter.GaussianBlur(50)))
        b.d.polygon([(x + w * 0.7 - 22, y - 40), (x + w * 0.7 + 22, y - 40), (x + w * 0.7 + 10, y - 62), (x + w * 0.7 - 10, y - 62)],
                    fill=(196, 160, 90))
        b.d.line([(x + w * 0.7, y - 40), (x + w * 0.7, y)], fill=(60, 52, 40), width=4)
    for _ in range(3):  # papers
        px = x + b.rng.uniform(10, w * 0.5)
        b.d.rectangle([px, y - 4, px + 40, y + 2], fill=(222, 214, 190))


def _study(rng: np.random.Generator) -> Image.Image:
    b, (x0, y0, x1, y1) = _room(rng, (120, 92, 70), (110, 78, 52))
    _shelves(b, x0 + 10, y0 + 20, (x1 - x0) * 0.42, y1 - y0 - 30)
    _window(b, x1 - (x1 - x0) * 0.38, y0 + 40, (x1 - x0) * 0.28, (y1 - y0) * 0.45)
    _desk(b, W * 0.3, H * 0.74, W * 0.4)
    return b.img


def _office(rng: np.random.Generator, name: str = "") -> Image.Image:
    """An office; a police station's has a cell, a newspaper's its desks and typewriters, a bank's
    and a post office's their counters with grilles or pigeonholes."""
    if "报" in name:
        return _newsroom(rng)
    b, (x0, y0, x1, y1) = _room(rng, (130, 126, 108), (96, 84, 70))
    if "警" in name:  # a holding cell along the left wall
        for x in np.arange(x0 + 10, x0 + (x1 - x0) * 0.35, 18):
            b.d.line([(x, y0 + 30), (x, y1)], fill=(52, 54, 56), width=6)
        b.d.line([(x0 + 10, y0 + 30), (x0 + (x1 - x0) * 0.35, y0 + 30)], fill=(52, 54, 56), width=8)
    elif "邮" in name:  # pigeonholes
        for r in range(5):
            for c in range(8):
                hx, hy = x0 + 20 + c * 34, y0 + 30 + r * 30
                b.d.rectangle([hx, hy, hx + 30, hy + 26], fill=b.tone((86, 62, 42)))
                if b.rng.random() < 0.6:
                    b.d.rectangle([hx + 4, hy + 8, hx + 26, hy + 22], fill=(222, 212, 186))
    _window(b, x0 + 30, y0 + 40, (x1 - x0) * 0.26, (y1 - y0) * 0.45)
    for i in range(3):  # filing cabinets
        cx = x1 - 70 - i * 64
        b.d.rectangle([cx, y1 - 150, cx + 56, y1], fill=b.tone((90, 96, 92)))
        for d in range(3):
            b.d.rectangle([cx + 20, y1 - 136 + d * 48, cx + 36, y1 - 130 + d * 48], fill=(180, 170, 150))
    b.d.rectangle([W * 0.18, H * 0.66, W * 0.82, H * 0.72], fill=b.tone((104, 76, 52)))  # a counter
    b.d.rectangle([W * 0.18, H * 0.72, W * 0.82, H * 0.95], fill=b.tone((84, 60, 42)))
    for x in np.arange(W * 0.2, W * 0.8, 26):  # its rails
        b.d.line([(x, H * 0.5), (x, H * 0.66)], fill=(70, 60, 48), width=3)
    _desk(b, W * 0.04, H * 0.8, W * 0.22, lamp=True)
    return b.img


def _newsroom(rng: np.random.Generator) -> Image.Image:
    b, (x0, y0, x1, y1) = _room(rng, (136, 128, 110), (98, 86, 72))
    _window(b, x1 - (x1 - x0) * 0.34, y0 + 36, (x1 - x0) * 0.26, (y1 - y0) * 0.42)
    for row, (y, scale) in enumerate(((H * 0.72, 0.8), (H * 0.86, 1.1))):
        for i in range(3):
            x = W * (0.08 + i * 0.32) + row * 40
            _desk(b, x, y, W * 0.22 * scale, lamp=False)
            b.d.rectangle([x + 30, y - 34 * scale, x + 90 * scale, y], fill=b.tone((40, 40, 42)))  # a typewriter
            b.d.rectangle([x + 40, y - 52 * scale, x + 80 * scale, y - 34 * scale], fill=(226, 220, 200))  # its sheet
            for k in range(int(b.rng.integers(2, 5))):  # paper stacks
                b.d.rectangle([x + 120 * scale, y - 6 - k * 6, x + 170 * scale, y - k * 6], fill=b.tone((214, 206, 182)))
    return b.img


def _cellar(rng: np.random.Generator) -> Image.Image:
    b, (x0, y0, x1, y1) = _room(rng, (84, 80, 74), (60, 56, 50))
    for y in np.arange(y0, y1, 26):  # stone courses on the back wall
        for x in np.arange(x0 + (13 if int(y) % 52 else 0), x1, 52):
            b.d.rectangle([x, y, x + 48, y + 22], outline=b.tone((60, 56, 52)), width=2)
    for i in range(4):  # barrels
        cx, cy = x0 + 40 + i * 90, y1 + 40 + (i % 2) * 30
        b.d.ellipse([cx - 40, cy - 70, cx + 40, cy + 40], fill=b.tone((92, 64, 40)))
        b.d.line([(cx - 40, cy - 30), (cx + 40, cy - 30)], fill=(52, 44, 38), width=4)
        b.d.line([(cx - 40, cy + 10), (cx + 40, cy + 10)], fill=(52, 44, 38), width=4)
    glow = Image.new("L", (W, H), 0)  # a single lantern's light
    ImageDraw.Draw(glow).ellipse([x1 - 260, y0 - 40, x1 + 40, y1 - 100], fill=110)
    b.img.paste(Image.new("RGB", (W, H), (240, 190, 110)), (0, 0), glow.filter(ImageFilter.GaussianBlur(60)))
    return b.img


def _shop(rng: np.random.Generator) -> Image.Image:
    b, (x0, y0, x1, y1) = _room(rng, (132, 104, 78), (100, 72, 50))
    _shelves(b, x0 + 10, y0 + 20, x1 - x0 - 20, (y1 - y0) * 0.55)
    b.d.rectangle([W * 0.1, H * 0.68, W * 0.9, H * 0.76], fill=b.tone((110, 76, 48)))  # the counter
    b.d.rectangle([W * 0.1, H * 0.76, W * 0.9, H], fill=b.tone((86, 58, 38)))
    for x in np.arange(W * 0.16, W * 0.86, 70):  # jars and bottles
        b.d.rounded_rectangle([x, H * 0.6, x + 26, H * 0.68], radius=6, fill=b.tone((120, 140, 120), 0.2))
    return b.img


def _bedroom(rng: np.random.Generator) -> Image.Image:
    b, (x0, y0, x1, y1) = _room(rng, (140, 118, 98), (104, 76, 54))
    _window(b, (x0 + x1) / 2 - 70, y0 + 40, 140, (y1 - y0) * 0.45)
    b.d.rectangle([W * 0.12, H * 0.64, W * 0.5, H * 0.82], fill=b.tone((196, 186, 168)))  # a bed
    b.d.rectangle([W * 0.12, H * 0.56, W * 0.16, H * 0.9], fill=b.tone((90, 62, 42)))
    b.d.rectangle([W * 0.14, H * 0.6, W * 0.24, H * 0.66], fill=(222, 216, 204))
    b.d.rectangle([W * 0.66, H * 0.5, W * 0.84, H * 0.9], fill=b.tone((96, 66, 44)))  # a wardrobe
    return b.img


OUTDOORS = {"street": _street, "graveyard": _graveyard, "church": _church, "station": _station, "water": _water,
            "woods": _woods, "hills": _hills, "fields": _fields, "house": _house, "glasshouse": _glasshouse}
INDOORS = {"study": _study, "office": _office, "cellar": _cellar, "shop": _shop, "bedroom": _bedroom}


def paint(name: str, description: str = "", *, key: str = "") -> Image.Image:
    """The place's scene, 960x540: by its name and description, seeded by `key` (or the name)."""
    kind = kind_of(name, description)
    rng = np.random.default_rng(_seed(key or name, kind))
    if kind == "office":
        img = _office(rng, name)
    elif kind in INDOORS:
        img = INDOORS[kind](rng)
    else:
        b = _sky(rng)
        OUTDOORS[kind](b)
        img = b.img
    img = img.filter(ImageFilter.GaussianBlur(1.6))  # paint, not diagram
    grain = np.asarray(img, float) * (1 + rng.normal(0, 0.035, (H, W, 1)))
    yy, xx = np.mgrid[0:H, 0:W]
    vignette = 1 - 0.28 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 2
    return Image.fromarray(np.clip(grain * vignette[..., None], 0, 255).astype(np.uint8))


def picture(folder: Path, key: str, name: str, description: str = "") -> Path:
    """The scene saved in `folder` as <key>.png (made once; the same place gives the same picture)."""
    out = folder / f"{key}.png"
    if not out.is_file():
        _save(paint(name, description, key=key), out)
    return out


def _save(img: Image.Image, out: Path) -> None:
    """Whole or not at all: a renderer in another thread may look for it while it is written
    (macOS CI read half a file: "image file is truncated")."""
    import os
    import threading

    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f"{out.stem}.{os.getpid()}-{threading.get_ident()}.part.png")
    img.save(part)
    os.replace(part, out)


def fill(m: Module) -> list[str]:
    """Every place of the module that has no picture gets a painted one (in its art key, or a key
    of its own), so no place is left to the hand-drawn art. The module on disk never changes.
    Returns the keys painted."""
    from . import paths

    folder, made = paths.scenery() / m.id, []
    for loc in m.locations:
        key = loc.art or f"scene-{loc.id}"
        if key in m.pictures:
            continue
        out = folder / f"{key}.png"
        _pending[str(out)] = (key, loc.name, loc.read_aloud or getattr(loc, "description", ""))  # painted when first shown
        m.pictures[key] = str(out)
        loc.art = key
        made.append(key)
    return made


_pending: dict[str, tuple[str, str, str]] = {}  # a picture fill() named, not painted yet: path -> key, name, words


def ensure(path: str | Path) -> None:
    """Paint a picture fill() or fill_people() named if it is not there yet (imagery reads
    pictures through here)."""
    if Path(path).is_file():
        return
    try:
        if (found := _pending.get(str(path))) is not None:
            picture(Path(path).parent, *found)
        elif (person := _faces.get(str(path))) is not None:
            face(Path(path).parent, *person)
    except OSError:
        pass


def painted(path: str | Path) -> bool:
    """Whether a picture is one of ours (painted by program, not a photo or a drawing): a redraw
    (engine/visuals.py) starts from the words, not from it."""
    p = Path(path)
    from . import paths

    return (str(p) in _pending or str(p) in _faces or p.is_relative_to(paths.scenery())
            or (p.suffix == ".png" and p.stem.startswith(("improv_scene_", "improv_portrait_"))))


# -- people ---------------------------------------------------------------------------------------
PW, PH = 600, 800  # a portrait: head and shoulders, cropped round the face where it is shown

SKINS: tuple[Rgb, ...] = ((226, 188, 160), (214, 172, 140), (196, 150, 118), (160, 112, 84), (122, 84, 62))
HAIRS: tuple[Rgb, ...] = ((44, 32, 26), (82, 56, 36), (126, 92, 58), (170, 140, 96), (60, 44, 34))
GREY: Rgb = (176, 172, 166)

DRESS: list[tuple[tuple[str, ...], str]] = [
    (("警", "探长", "巡警", "警官", "军", "士兵", "兵"), "uniform"),
    (("医生", "大夫", "护士", "医"), "coat"),
    (("牧师", "神父", "传教", "布道", "修女"), "clergy"),
    (("农", "工人", "矿", "铁匠", "掘墓", "守墓", "车夫", "水手", "渔"), "work"),
]
FEMALE = ("女", "夫人", "小姐", "太太", "妇", "她", "母亲", "姐", "妹", "妻", "姑娘", "修女")
OLD = ("老", "年迈", "白发", "花白", "六十", "七十", "八十", "祖")


def _person(text: str) -> tuple[str, bool, bool]:
    """Dress, woman, old: what a portrait needs to know, from the name, the role and the look."""
    dress = next((d for words, d in DRESS if any(w in text for w in words)), "suit")
    return dress, any(w in text for w in FEMALE), any(w in text for w in OLD)


def portrait(name: str, look: str = "", role: str = "", *, key: str = "") -> Image.Image:
    """A head-and-shoulders portrait painted by program, 600x800: dressed for the role, hair, beard,
    glasses and hat as the look says, lit from one side. Seeded by `key` (or the name)."""
    text = f"{name} {role} {look}"
    rng = np.random.default_rng(_seed(key or name, "portrait"))
    dress, woman, old = _person(text)
    t = np.linspace(0, 1, PH)[:, None, None]
    back = (np.array((92, 78, 64)) * (1 - t) + np.array((40, 34, 30)) * t).repeat(PW, axis=1)
    img = Image.fromarray(np.clip(back, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    skin = SKINS[int(rng.integers(len(SKINS)))]
    hair = GREY if old else HAIRS[int(rng.integers(len(HAIRS)))]
    if "白发" in look or "银发" in look:
        hair = (214, 212, 206)
    cx, top = PW / 2, 170

    # shoulders and clothes
    cloth = {"uniform": (44, 54, 78), "coat": (226, 224, 216), "clergy": (30, 28, 30), "work": (96, 74, 52),
             "suit": ((52, 50, 54), (70, 56, 46), (48, 56, 66))[int(rng.integers(3))]}[dress]
    if woman and dress == "suit":
        cloth = ((116, 40, 48), (40, 88, 92), (88, 70, 110), (62, 74, 58))[int(rng.integers(4))]
    d.rectangle([cx - 46, 470, cx + 46, 600], fill=tuple(int(c * 0.86) for c in skin))  # the neck, in shade
    d.rounded_rectangle([cx - 250, 560, cx + 250, PH + 80], radius=140, fill=cloth)
    if dress in ("suit", "uniform") and not woman:
        d.polygon([(cx - 70, 560), (cx, 660), (cx + 70, 560)], fill=(232, 228, 218))  # shirt
        d.polygon([(cx - 14, 576), (cx + 14, 576), (cx + 20, 720), (cx, 750), (cx - 20, 720)],
                  fill=(120, 36, 36) if dress == "suit" else (34, 40, 58))  # tie
        d.polygon([(cx - 70, 560), (cx - 10, 680), (cx - 120, 720)], fill=tuple(int(c * 0.8) for c in cloth))  # lapels
        d.polygon([(cx + 70, 560), (cx + 10, 680), (cx + 120, 720)], fill=tuple(int(c * 0.8) for c in cloth))
    if dress == "uniform":
        for y in (650, 700):
            d.ellipse([cx + 60, y, cx + 76, y + 16], fill=(200, 170, 90))  # buttons
    if dress == "clergy":
        d.rectangle([cx - 40, 560, cx + 40, 582], fill=(236, 236, 230))  # the white collar
    if dress == "coat":
        d.line([(cx, 570), (cx, PH)], fill=(190, 188, 180), width=4)
    if woman:
        d.ellipse([cx - 60, 550, cx + 60, 600], fill=tuple(int(c * 1.1) if c < 220 else c for c in cloth))  # a round collar

    # head
    fw, fh = (150, 196) if woman else (160, 206)
    d.ellipse([cx - fw / 2 - 10, top + 120, cx - fw / 2 + 14, top + 190], fill=skin)  # ears
    d.ellipse([cx + fw / 2 - 14, top + 120, cx + fw / 2 + 10, top + 190], fill=skin)
    d.ellipse([cx - fw / 2, top, cx + fw / 2, top + fh * 1.7], fill=skin)
    if woman:  # a bob, framing the face
        d.pieslice([cx - fw / 2 - 26, top - 20, cx + fw / 2 + 26, top + fh * 1.4], 180, 360, fill=hair)
        d.rectangle([cx - fw / 2 - 26, top + fh * 0.7 - 20, cx - fw / 2 + 10, top + fh * 1.2], fill=hair)
        d.rectangle([cx + fw / 2 - 10, top + fh * 0.7 - 20, cx + fw / 2 + 26, top + fh * 1.2], fill=hair)
    elif "秃" not in look:
        d.pieslice([cx - fw / 2 - 6, top - 14, cx + fw / 2 + 6, top + fh * 0.9], 180, 360, fill=hair)
    hat = "帽" in look or (not woman and rng.random() < 0.45 and dress in ("suit", "work"))
    if hat or (woman and "帽" in look):
        brim = (36, 32, 30) if not woman else tuple(int(c * 0.7) for c in cloth)
        if woman:  # a cloche
            d.pieslice([cx - fw / 2 - 20, top - 40, cx + fw / 2 + 20, top + fh * 0.9], 180, 360, fill=brim)
        else:  # a fedora
            d.ellipse([cx - fw / 2 - 60, top + 36, cx + fw / 2 + 60, top + 70], fill=brim)
            d.rounded_rectangle([cx - fw / 2 + 4, top - 50, cx + fw / 2 - 4, top + 54], radius=30, fill=brim)
            d.rectangle([cx - fw / 2 + 4, top + 26, cx + fw / 2 - 4, top + 42], fill=(90, 70, 50))

    # the face
    ey = top + fh * 0.78
    for side in (-1, 1):
        ex = cx + side * fw * 0.2
        d.ellipse([ex - 13, ey - 8, ex + 13, ey + 8], fill=(240, 234, 226))
        d.ellipse([ex - 7, ey - 7, ex + 7, ey + 7], fill=(50, 40, 34))
        d.line([(ex - 20, ey - 22 - (4 if old else 0)), (ex + 20, ey - 24)], fill=tuple(int(c * 0.6) for c in hair), width=7)  # brows
    d.polygon([(cx, ey + 8), (cx - 14, ey + 62), (cx + 6, ey + 66)], fill=tuple(int(c * 0.86) for c in skin))  # the nose's shade
    mouth = ey + 104
    d.line([(cx - 28, mouth), (cx + 28, mouth + rng.uniform(-4, 4))], fill=(140, 74, 66), width=5)
    if not woman and ("胡" in look or "须" in look or (old and rng.random() < 0.5)):
        d.pieslice([cx - fw / 2 + 10, ey + 40, cx + fw / 2 - 10, top + fh * 1.75], 0, 180, fill=hair)  # a beard
        d.ellipse([cx - 40, mouth - 16, cx + 40, mouth + 8], fill=hair)
    if "眼镜" in look or (old and rng.random() < 0.4):
        for side in (-1, 1):
            ex = cx + side * fw * 0.2
            d.ellipse([ex - 24, ey - 20, ex + 24, ey + 20], outline=(60, 50, 40), width=4)
        d.line([(cx - fw * 0.2 + 24, ey), (cx + fw * 0.2 - 24, ey)], fill=(60, 50, 40), width=4)
    if old:  # lines at the eyes and the mouth
        for side in (-1, 1):
            d.line([(cx + side * (fw * 0.2 + 26), ey + 4), (cx + side * (fw * 0.2 + 40), ey + 14)], fill=tuple(int(c * 0.8) for c in skin), width=3)
            d.line([(cx + side * 40, mouth - 20), (cx + side * 46, mouth + 14)], fill=tuple(int(c * 0.82) for c in skin), width=3)

    # light from the left, shade to the right; then paint, not diagram
    arr = np.asarray(img, float)
    xx = np.linspace(1.08, 0.78, PW)[None, :, None]
    arr = arr * xx * (1 + rng.normal(0, 0.03, (PH, PW, 1)))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.4))


def face(folder: Path, key: str, name: str, look: str = "", role: str = "") -> Path:
    """The portrait saved in `folder` as <key>.png (made once)."""
    out = folder / f"{key}.png"
    if not out.is_file():
        _save(portrait(name, look, role, key=key), out)
    return out


def fill_people(m: Module) -> list[str]:
    """Every person of the module with no picture (an NPC, a pregen) gets a portrait painted by
    program, in place of the hand-drawn face; painted when first shown."""
    from . import paths

    folder, made = paths.scenery() / m.id / "people", []  # "people": imagery prepares it as a portrait
    for who, look, role in [*((n, n.look, n.role) for n in m.npcs), *((p, "", p.occupation) for p in m.pregens)]:
        key = who.portrait or f"face-{who.id}"
        if key in m.pictures:
            continue
        out = folder / f"{key}.png"
        _faces[str(out)] = (key, who.name, look or "", role or "")
        m.pictures[key] = str(out)
        who.portrait = key
        made.append(key)
    return made


_faces: dict[str, tuple[str, str, str, str]] = {}  # a portrait fill_people() named: path -> key, name, look, role


# -- things ---------------------------------------------------------------------------------------
COLOURS: list[tuple[tuple[str, ...], Rgb]] = [
    (("红", "朱", "血"), (150, 46, 40)), (("蓝", "靛"), (54, 82, 140)), (("绿", "翠"), (60, 110, 70)),
    (("黄", "金"), (196, 160, 70)), (("银", "白", "瓷"), (214, 212, 204)), (("黑", "乌"), (40, 38, 40)),
    (("铜", "黄铜"), (176, 124, 64)), (("皮", "棕", "褐"), (110, 74, 46)), (("铁", "锈"), (110, 96, 88)),
]
THINGS: list[tuple[str, tuple[str, ...]]] = [
    ("skull", ("头骨", "骷髅", "骨")), ("key", ("钥匙",)), ("gun", ("枪",)), ("blade", ("刀", "匕首", "斧", "剑")),
    ("watch", ("怀表", "手表", "表", "钟")), ("light", ("灯", "手电", "蜡烛", "烛")), ("photo", ("照片", "相片", "合影", "相框", "肖像", "画")),
    ("map", ("地图", "图纸")), ("book", ("书", "日记", "笔记", "账本", "信", "手稿", "典籍", "本")),
    ("cup", ("杯", "碗", "茶")), ("vessel", ("瓶", "罐", "壶", "酒")), ("jewel", ("项链", "戒指", "珍珠", "首饰", "胸针", "坠")),
    ("shovel", ("铲", "锹")),
]


def thing_of(name: str, description: str = "") -> str:
    for text in (name, description):
        for kind, words in THINGS:
            if any(w in text for w in words):
                return kind
    return "box"


def still(name: str, description: str = "", *, key: str = "") -> Image.Image:
    """A thing on a table under a lamp, painted by program, 960x540: shaped by what it is, coloured
    as its description says."""
    rng = np.random.default_rng(_seed(key or name, "still"))
    kind = thing_of(name, description)
    colour = next((c for words, c in COLOURS if any(w in f"{name}{description}" for w in words)), (120, 88, 60))
    b = _canvas((54, 44, 36), (26, 22, 18), rng)
    d = b.d
    table = H * 0.62
    d.rectangle([0, table, W, H], fill=b.tone((96, 66, 42)))
    for y in np.arange(table + 18, H, 26):  # the grain of the table
        d.line([(0, y), (W, y + rng.uniform(-6, 6))], fill=b.tone((84, 58, 38)), width=2)
    cx, cy = W / 2, table + 40
    d.ellipse([cx - 230, cy - 20, cx + 230, cy + 50], fill=(60, 42, 28))  # its shadow
    dark = tuple(int(c * 0.6) for c in colour)
    if kind == "book":
        d.polygon([(cx - 200, cy), (cx + 170, cy - 30), (cx + 210, cy + 10), (cx - 160, cy + 40)], fill=colour)
        d.polygon([(cx - 200, cy), (cx + 170, cy - 30), (cx + 170, cy - 46), (cx - 200, cy - 16)], fill=(226, 216, 190))  # pages
        d.line([(cx - 190, cy - 8), (cx + 165, cy - 38)], fill=(190, 180, 156), width=2)
    elif kind == "key":
        d.ellipse([cx - 190, cy - 50, cx - 100, cy + 40], outline=colour, width=18)
        d.rectangle([cx - 104, cy - 10, cx + 170, cy + 8], fill=colour)
        for x in (cx + 110, cx + 150):
            d.rectangle([x, cy + 8, x + 18, cy + 40], fill=colour)
    elif kind == "gun":
        steel = (126, 128, 136)
        d.rectangle([cx - 150, cy - 30, cx + 170, cy - 2], fill=steel)  # the barrel
        d.rectangle([cx - 150, cy - 44, cx - 40, cy + 10], fill=steel)  # the frame and cylinder
        d.polygon([(cx - 150, cy + 4), (cx - 80, cy + 4), (cx - 110, cy + 70), (cx - 180, cy + 70)], fill=(120, 80, 48))  # the grip
        d.ellipse([cx - 80, cy - 2, cx - 40, cy + 34], outline=steel, width=7)  # the trigger guard
    elif kind == "blade":
        d.polygon([(cx - 60, cy - 6), (cx + 230, cy - 20), (cx - 60, cy + 14)], fill=(196, 198, 200))
        d.rectangle([cx - 200, cy - 10, cx - 60, cy + 18], fill=dark if kind else (70, 50, 34))
        d.rectangle([cx - 70, cy - 30, cx - 54, cy + 36], fill=(150, 120, 70))
    elif kind == "watch":
        d.ellipse([cx - 110, cy - 130, cx + 110, cy + 90], fill=colour if colour != (120, 88, 60) else (186, 150, 80))
        d.ellipse([cx - 90, cy - 110, cx + 90, cy + 70], fill=(232, 226, 210))
        d.line([(cx, cy - 20), (cx, cy - 90)], fill=(40, 34, 30), width=6)
        d.line([(cx, cy - 20), (cx + 50, cy + 10)], fill=(40, 34, 30), width=6)
        d.rectangle([cx - 12, cy - 160, cx + 12, cy - 130], fill=(186, 150, 80))
    elif kind == "light":
        glow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(glow).ellipse([cx - 260, cy - 360, cx + 260, cy + 60], fill=140)
        b.img.paste(Image.new("RGB", (W, H), (255, 214, 140)), (0, 0), glow.filter(ImageFilter.GaussianBlur(60)))
        d.rectangle([cx - 40, cy - 150, cx + 40, cy], fill=(226, 214, 186))  # a candle, or the lamp's glass
        d.ellipse([cx - 14, cy - 200, cx + 14, cy - 150], fill=(255, 200, 90))
        d.rectangle([cx - 70, cy, cx + 70, cy + 16], fill=colour)
    elif kind == "photo":
        d.polygon([(cx - 170, cy - 200), (cx + 150, cy - 190), (cx + 160, cy + 30), (cx - 180, cy + 20)], fill=(70, 50, 36))
        d.polygon([(cx - 145, cy - 175), (cx + 125, cy - 166), (cx + 133, cy + 6), (cx - 153, cy - 2)], fill=(176, 166, 146))
        d.ellipse([cx - 40, cy - 150, cx + 30, cy - 70], fill=(120, 108, 96))  # a figure, faded
        d.rectangle([cx - 70, cy - 70, cx + 60, cy], fill=(96, 86, 78))
    elif kind == "map":
        d.polygon([(cx - 230, cy - 10), (cx + 220, cy - 40), (cx + 240, cy + 40), (cx - 210, cy + 60)], fill=(214, 196, 158))
        for _ in range(6):
            x0, y0 = cx + rng.uniform(-200, 160), cy + rng.uniform(-20, 40)
            d.line([(x0, y0), (x0 + rng.uniform(30, 90), y0 + rng.uniform(-10, 10))], fill=(120, 80, 50), width=3)
        d.line([(cx - 10, cy - 30), (cx + 10, cy + 55)], fill=(180, 160, 120), width=2)  # the fold
    elif kind == "cup":
        d.polygon([(cx - 90, cy - 110), (cx + 90, cy - 110), (cx + 66, cy + 10), (cx - 66, cy + 10)], fill=(226, 224, 216))
        d.ellipse([cx - 90, cy - 126, cx + 90, cy - 94], fill=(204, 200, 190))
        d.arc([cx + 60, cy - 90, cx + 140, cy - 20], 270, 90, fill=(226, 224, 216), width=14)  # the handle
        d.line([(cx - 88, cy - 104), (cx + 88, cy - 104)], fill=colour if colour != (120, 88, 60) else (54, 82, 140), width=6)  # its rim
    elif kind == "vessel":
        d.rounded_rectangle([cx - 70, cy - 170, cx + 70, cy + 10], radius=30, fill=colour)
        d.rectangle([cx - 30, cy - 220, cx + 30, cy - 160], fill=colour)
        d.ellipse([cx - 40, cy - 230, cx + 40, cy - 210], fill=tuple(int(c * 1.15) if c < 220 else c for c in colour))
        d.line([(cx - 40, cy - 130), (cx - 40, cy - 30)], fill=(236, 232, 220), width=6)  # a highlight
    elif kind == "jewel":
        for i, a in enumerate(np.linspace(math.pi * 1.05, math.pi * 1.95, 24)):  # a string of beads
            x, y = cx + math.cos(a) * 180, cy + 30 + math.sin(a) * 110
            d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=(234, 230, 220) if i % 2 else (210, 204, 192))
        d.ellipse([cx - 34, cy + 10, cx + 34, cy + 70], fill=colour if colour != (120, 88, 60) else (236, 234, 226))
    elif kind == "skull":
        d.ellipse([cx - 110, cy - 200, cx + 110, cy - 10], fill=(222, 214, 192))
        d.rectangle([cx - 60, cy - 30, cx + 60, cy + 20], fill=(222, 214, 192))
        for side in (-1, 1):
            d.ellipse([cx + side * 50 - 30, cy - 120, cx + side * 50 + 30, cy - 70], fill=(40, 34, 30))
        d.polygon([(cx, cy - 60), (cx - 14, cy - 30), (cx + 14, cy - 30)], fill=(60, 50, 44))
    elif kind == "shovel":
        d.rectangle([cx - 250, cy - 14, cx + 80, cy + 4], fill=(110, 80, 52))
        d.polygon([(cx + 80, cy - 40), (cx + 220, cy - 30), (cx + 220, cy + 20), (cx + 80, cy + 30)], fill=(120, 116, 112))
    else:  # a box, or a parcel
        d.polygon([(cx - 150, cy - 120), (cx + 150, cy - 120), (cx + 150, cy + 20), (cx - 150, cy + 20)], fill=colour)
        d.polygon([(cx - 150, cy - 120), (cx - 100, cy - 170), (cx + 200, cy - 170), (cx + 150, cy - 120)], fill=tuple(int(c * 1.15) if c < 220 else c for c in colour))
        d.polygon([(cx + 150, cy - 120), (cx + 200, cy - 170), (cx + 200, cy - 30), (cx + 150, cy + 20)], fill=dark)
    light = np.linspace(1.12, 0.8, W)[None, :, None]  # the lamp to the left
    arr = np.asarray(b.img, float) * light * (1 + rng.normal(0, 0.03, (H, W, 1)))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.4))


def thing(folder: Path, key: str, name: str, description: str = "") -> Path:
    """The thing's picture saved in `folder` as <key>.png (made once)."""
    out = folder / f"{key}.png"
    if not out.is_file():
        _save(still(name, description, key=key), out)
    return out
