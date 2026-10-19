"""The dice tray as a picture: the physical dice (rules/physdice.py) in their wooden tray, seen
from a little above, lit by a candle off to the upper left; tentacles creep over the rim.

Everything is painted straight, with no pass over the finished picture: flat-shaded kites (a
warm key light, a cold fill, a rim light from the far side, a weak broad highlight), surfaces
rough with grit fixed to the die so it tumbles with it, the numbers as decals on every kite,
hard shadows on the floor. dice.py hands each frame to chafa like any scene and lays the top
faces' numbers over it in cells.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from ..rules.physdice import d10, label, lying

CELL = (8, 16)  # picture pixels per terminal cell handed to chafa (cells are twice as tall as wide)
TILT = 14.0  # the camera's lean from straight down, degrees: the far wall shows a little
DISTANCE = 14.0  # from the camera to the middle of the floor, die radii: perspective, gently
RIM = 0.12  # the wall's thickness, die radii
GROOVE = 0.3  # how far a number's cut reaches past its paint, in its pixels (light skins: Material.cut)
RGB = tuple[float, float, float]


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


# -- the camera --------------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)  # one per picture size and tray (camera() is cached): hashed by identity
class Camera:
    w: int
    h: int
    eye: np.ndarray
    m: np.ndarray  # world to camera: rows right, up, back
    f: float
    cx: float
    cy: float

    def project(self, p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """World points (n, 3) on the picture (n, 2), and how far from the camera."""
        q = (p - self.eye) @ self.m.T
        d = -q[:, 2]
        return np.stack([self.cx + self.f * q[:, 0] / d, self.cy - self.f * q[:, 1] / d], axis=1), d

    def point(self, p) -> tuple[float, float, float]:
        """One world point on the picture, and its distance."""
        xy, d = self.project(np.asarray(p, dtype=float)[None])
        return float(xy[0, 0]), float(xy[0, 1]), float(d[0])


@lru_cache(maxsize=8)
def camera(cols: int, rows: int, tray: tuple[float, float]) -> Camera:
    """A camera leaning TILT from straight down, framing the tray and its rim."""
    w, h = cols * CELL[0], rows * CELL[1]
    t = math.radians(TILT)
    back, up = np.array([0.0, -math.sin(t), math.cos(t)]), np.array([0.0, math.cos(t), math.sin(t)])
    m = np.array([[1.0, 0.0, 0.0], up, back])
    eye = back * DISTANCE
    a, b = tray[0] + RIM, tray[1] + RIM
    corners = np.array([(x, y, z) for x in (-a, a) for y in (-b, b) for z in (0.0, 0.5)])
    q = (corners - eye) @ m.T
    sx, sy = q[:, 0] / -q[:, 2], -q[:, 1] / -q[:, 2]
    f = min(w / (sx.max() - sx.min()), h / (sy.max() - sy.min()))
    return Camera(w, h, eye, m, f, w / 2 - f * (sx.max() + sx.min()) / 2, h / 2 - f * (sy.max() + sy.min()) / 2)


@lru_cache(maxsize=8)
def rays(cam: Camera) -> np.ndarray:
    """Each pixel's ray into the world (h, w, 3)."""
    yy, xx = np.mgrid[0 : cam.h, 0 : cam.w].astype(float) + 0.5
    local = np.stack([(xx - cam.cx) / cam.f, -(yy - cam.cy) / cam.f, -np.ones_like(xx)], axis=-1)
    return local @ cam.m


@lru_cache(maxsize=8)
def floor_at(cam: Camera) -> np.ndarray:
    """Where each pixel's ray meets the floor's plane: (h, w, 2) x and y."""
    r = rays(cam)
    t = -cam.eye[2] / np.where(np.abs(r[..., 2]) > 1e-9, r[..., 2], -1e-9)
    return (cam.eye[:2] + t[..., None] * r[..., :2]).astype(np.float32)


# -- grit --------------------------------------------------------------------------------------
def grit(p: np.ndarray, size: float, seed: int) -> np.ndarray:
    """Blocky noise in 0-1 over points (..., 3): one value per cube `size` across. No smoothing:
    the surface stays rough at any scale."""
    k = np.floor(p / size).astype(np.int64)
    h = (k[..., 0] * 73856093) ^ (k[..., 1] * 19349663) ^ (k[..., 2] * 83492791) ^ (seed * 2654435761)
    h = (h ^ (h >> 13)) * 1274126177
    return ((h ^ (h >> 16)) & 0xFFFF).astype(np.float32) / 0xFFFF


# -- skins -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Material:
    base: RGB
    stain: RGB  # the blotches' colour
    blotch: float  # how much of the surface they cover
    grain: float  # the fine grit's strength
    pits: float  # share of dark pits
    spec: float = 0.08  # a weak, broad highlight
    shine: float = 6.0
    ink: RGB = (0.2, 0.12, 0.06)  # the numbers' paint
    groove: RGB = (0.06, 0.04, 0.03)  # the cut round it
    cut: float = GROOVE  # how wide round the yellow: on a light skin it needs a dark rim to read
    rim: RGB = (0.25, 0.32, 0.4)  # the light from the far side
    glow: float = 0.0  # light of its own (phosphor)


MATERIALS = {
    "bone": Material((0.9, 0.84, 0.7), (0.62, 0.5, 0.32), 0.38, 0.2, 0.05, ink=(0.36, 0.22, 0.11), groove=(0.2, 0.12, 0.06), cut=0.6),
    "obsidian": Material((0.06, 0.055, 0.07), (0.2, 0.19, 0.24), 0.3, 0.12, 0.0, spec=0.3, shine=9, ink=(0.92, 0.76, 0.36),
                         rim=(0.18, 0.4, 0.26)),
    "blood": Material((0.55, 0.09, 0.03), (0.85, 0.32, 0.06), 0.35, 0.12, 0.02, spec=0.22, shine=8, ink=(1.0, 0.86, 0.72),
                      rim=(0.45, 0.15, 0.1)),
    "brass": Material((0.7, 0.5, 0.19), (0.16, 0.36, 0.28), 0.34, 0.28, 0.04, spec=0.3, shine=7, ink=(0.24, 0.15, 0.05), groove=(0.12, 0.07, 0.02), cut=0.6),
    "eldritch": Material((0.15, 0.33, 0.25), (0.05, 0.13, 0.09), 0.45, 0.22, 0.06, ink=(0.74, 1.0, 0.86), rim=(0.3, 0.2, 0.42)),
    "phosphor": Material((0.01, 0.06, 0.025), (0.0, 0.12, 0.04), 0.2, 0.0, 0.0, spec=0.0, ink=(0.45, 1.0, 0.6),
                         rim=(0.0, 0.25, 0.08), glow=1.0),
}
PHOSPHOR = np.array([0.25, 1.0, 0.45])
GOLD = (1.0, 0.843, 0.0)  # #ffd700: the top face's own numbers once the dice lie still
CANDLE = np.array([-4.0, 3.2, 6.0])  # off the tray's upper left corner
WARM, COLD = np.array([1.0, 0.9, 0.74]), np.array([0.35, 0.45, 0.58])
FILL = unit([0.85, -0.3, 0.45])
RIMLIGHT = unit([0.15, 0.95, 0.35])


def candlelight(p: np.ndarray) -> np.ndarray:
    """How strongly the candle lights points (..., 3): falling off with distance."""
    d2 = ((p - CANDLE) ** 2).sum(axis=-1)
    return 85.0 / (20.0 + d2)


# -- the tray ----------------------------------------------------------------------------------
def creeping(tray: tuple[float, float]) -> list[list[tuple[float, float, float]]]:
    """Tentacles reaching over the rim into the tray, as paths on the floor (x, y, radius)."""
    a, b = tray
    out = []
    for (x0, y0), (dx, dy), length, thick, curl in (
        ((a + 0.5, b * 0.35), (-1.0, -0.15), 1.5, 0.2, 1.0),
        ((-a * 0.2, b + 0.5), (0.3, -1.0), 1.25, 0.17, -1.0),
        ((-a - 0.5, -b * 0.55), (1.0, 0.25), 1.35, 0.18, 1.0),
    ):
        path = []
        for i in range(40):
            s = i / 39
            bend = curl * (1.2 * s**2)
            ang = math.atan2(dy, dx) + bend
            r = thick * (1 - s) ** 0.8 + 0.025
            if i:
                x, y = path[-1][0] + math.cos(ang) * length / 39, path[-1][1] + math.sin(ang) * length / 39
            else:
                x, y = x0, y0
            path.append((x, y, r))
        out.append(path)
    return out


@lru_cache(maxsize=12)
def room(skin: str, cols: int, rows: int, tray: tuple[float, float]) -> np.ndarray:
    """The tray without dice: worn planks lit by the candle, the rim, tentacles over it."""
    cam = camera(cols, rows, tray)
    fx, fy = floor_at(cam)[..., 0], floor_at(cam)[..., 1]
    a, b = tray
    inside = (np.abs(fx) <= a) & (np.abs(fy) <= b)
    on_rim = ~inside & (np.abs(fx) <= a + RIM) & (np.abs(fy) <= b + RIM)
    p = np.stack([fx, fy, np.zeros_like(fx)], axis=-1)
    if skin == "phosphor":
        img = np.zeros((cam.h, cam.w, 3), np.float32) + np.array([0.0, 0.025, 0.01])
        grid = inside & ((np.abs((fx / 0.5) - np.round(fx / 0.5)) < 0.06) | (np.abs((fy / 0.5) - np.round(fy / 0.5)) < 0.08))
        img[grid] = PHOSPHOR * 0.18
        img[on_rim] = PHOSPHOR * 0.5
        out = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
        draw = ImageDraw.Draw(out)
        for path in creeping(tray):
            for side in (1, -1):  # the outline only: two lines along its sides
                pts = []
                for i, (x, y, r) in enumerate(path):
                    (x1, y1, _), (x0, y0, _) = path[min(len(path) - 1, i + 1)], path[max(0, i - 1)]
                    nx, ny = unit([-(y1 - y0), x1 - x0])
                    pts.append(tuple(cam.project(np.array([[x + side * nx * r, y + side * ny * r, 0.05]]))[0][0]))
                draw.line(pts, fill=tuple(int(c * 255 * 0.7) for c in PHOSPHOR), width=1)
        return np.asarray(out).astype(np.float32) / 255
    light = candlelight(p)[..., None]
    plank = (np.floor((fy + 7) / 0.62) % 7).astype(np.int64)  # boards running across, each its own shade
    streak = grit(np.stack([fx * 0.35, fy * 6.0, plank.astype(float)], axis=-1), 1.0, 3)
    wood = np.array([0.17, 0.095, 0.05]) * (0.7 + 0.25 * (plank % 3) / 2 + 0.35 * streak)[..., None]
    wood *= (0.85 + 0.3 * grit(p, 0.11, 4))[..., None]  # grime
    seam = (np.abs(((fy + 7) / 0.62) % 1.0) < 0.06)[..., None]
    wood = np.where(seam, wood * 0.35, wood)
    img = wood * (0.25 + 1.1 * light)
    rim = np.array([0.26, 0.15, 0.07]) * (0.6 + 0.4 * grit(p, 0.07, 5))[..., None] * (0.35 + 1.3 * light)
    img = np.where(on_rim[..., None], rim, img)
    img = np.where((~inside & ~on_rim)[..., None], np.array([0.015, 0.012, 0.012]), img)
    for path in creeping(tray):
        img = tentacle(img, cam, path, MATERIALS[skin].rim)
    return img.astype(np.float32)


def tentacle(img: np.ndarray, cam: Camera, path, tint: RGB) -> np.ndarray:
    """A tentacle lying across the floor: dark, its upper left lit by the candle in two hard
    steps, pale suckers along its underside."""
    h, w = img.shape[:2]
    body, lit, cups = (Image.new("L", (w, h), 0) for _ in range(3))
    db, dl, dc = ImageDraw.Draw(body), ImageDraw.Draw(lit), ImageDraw.Draw(cups)
    for i, (x, y, r) in enumerate(path):
        sx, sy, _ = cam.point((x, y, r))
        pr = r * cam.f / DISTANCE
        db.ellipse([sx - pr, sy - pr, sx + pr, sy + pr], fill=255)
        dl.ellipse([sx - pr * 0.95, sy - pr * 0.95, sx + pr * 0.3, sy + pr * 0.3], fill=255)
        if i % 4 == 2 and i < len(path) * 0.8:
            cr = max(0.8, pr * 0.28)
            dc.ellipse([sx + pr * 0.35 - cr, sy + pr * 0.45 - cr, sx + pr * 0.35 + cr, sy + pr * 0.45 + cr], fill=255)
    body_a, lit_a, cup_a = (np.asarray(m, dtype=np.float32)[..., None] / 255 for m in (body, lit, cups))
    flesh = np.array([0.1, 0.06, 0.1]) + np.array(tint) * 0.35
    colour = flesh * (0.55 + 0.75 * lit_a) + cup_a * np.array([0.32, 0.24, 0.28])
    return img * (1 - body_a) + colour * body_a


# -- the dice ----------------------------------------------------------------------------------
FONT = {  # 3 x 5 pixels, row by row: the same digits the cells use
    "0": ("###", "#.#", "#.#", "#.#", "###"), "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"), "3": ("###", "..#", ".##", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"), "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"), "7": ("###", "..#", ".#.", ".#.", ".#."),
    "8": ("###", "#.#", "###", "#.#", "###"), "9": ("###", "#.#", "###", "..#", "###"),
}
# A number's pixel (die radii) and where its middle sits on the kite (v: 0 the side corners, 1
# the apex): as big as a kite holds, two digits only just fitting its width.
DECAL = {"tens": (0.105, 0.145), "units": (0.16, 0.22)}


@lru_cache(maxsize=10)
def kite(k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Kite k's own frame in the die's space: the middle of its side corners, a half-width to
    the right, the way up to its apex. (u, v) = (0, 1) is the apex, (±1, 0) the sides."""
    verts, faces, normals = d10()
    f = faces[k]
    i = next(j for j, v in enumerate(f) if v in (0, 1))
    apex, side, _, other = (f[(i + j) % 4] for j in range(4))
    mid = (verts[side] + verts[other]) / 2
    up = verts[apex] - mid
    right = unit(np.cross(up, normals[k]))
    return mid, right * np.linalg.norm(verts[side] - mid), up


def on_kite(k: int, uv: np.ndarray) -> np.ndarray:
    """Points (u, v) on kite k (n, 2) in the die's space (n, 3)."""
    mid, right, up = kite(k)
    return mid + uv[:, :1] * right + uv[:, 1:] * up


@lru_cache(maxsize=64)
def digits(text: str, size: float, at: float, grow: float = 0.0) -> tuple[np.ndarray, ...]:
    """The number as squares on a kite: each lit pixel of the font, its four corners as (u, v),
    grown by `grow` of a pixel on every side (the cut round the paint)."""
    _, right, up = kite(0)
    du, dv = size / np.linalg.norm(right), size / np.linalg.norm(up)
    width = len(text) * 4 - 1
    out = []
    for n, ch in enumerate(text):
        for row, line in enumerate(FONT.get(ch, FONT["0"])):
            for col, px in enumerate(line):
                if px == "#":
                    u0, u1 = (n * 4 + col - width / 2 - grow) * du, (n * 4 + col + 1 - width / 2 + grow) * du
                    v0, v1 = at + (2.5 - row + grow) * dv, at + (1.5 - row - grow) * dv
                    out.append(np.array([(u0, v0), (u1, v0), (u1, v1), (u0, v1)]))
    return tuple(out)


def poly(draw: ImageDraw.ImageDraw, pts: np.ndarray, at: tuple[int, int], fill: int = 255) -> None:
    draw.polygon([(float(x - at[0]), float(y - at[1])) for x, y in pts], fill=fill)


@dataclass
class Placed:
    """A die as one frame shows it."""

    top: int  # the kite turned most nearly up
    face: np.ndarray  # that kite's corners on the picture
    centre: tuple[float, float]


def die(img: np.ndarray, cam: Camera, skin: str, kind: str, x: np.ndarray, r: np.ndarray, *, seed: int,
        gold: bool = False, motif=None) -> Placed:
    """Paint one die into the picture where its pose puts it, its numbers cut into every kite.
    `gold`: the dice lie still and this one counts: its top face's number is painted yellow."""
    verts, faces, normals = d10()
    m = MATERIALS[skin]
    world = x + verts @ r.T
    scr, _ = cam.project(world)
    nw = normals @ r.T
    centres = np.array([world[list(f)].mean(axis=0) for f in faces])
    visible = [k for k in range(10) if nw[k] @ (cam.eye - centres[k]) > 0]
    top = int(np.argmax(nw[:, 2]))
    x0, y0 = np.floor(scr.min(axis=0)).astype(int) - 1
    x1, y1 = np.ceil(scr.max(axis=0)).astype(int) + 2
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(cam.w, x1), min(cam.h, y1)
    placed = Placed(top, scr[list(faces[top])], cam.point(x)[:2])
    if x1 <= x0 or y1 <= y0:
        return placed
    size = (x1 - x0, y1 - y0)
    ids = Image.new("L", size, 0)
    draw = ImageDraw.Draw(ids)
    for k in visible:
        poly(draw, scr[list(faces[k])], (x0, y0), k + 1)
    ids_a = np.asarray(ids).astype(np.int16)
    sel = ids_a > 0
    if not sel.any():
        return placed
    k_of = ids_a[sel] - 1
    # where each pixel's ray meets its kite, in the die's own space: the grit stays on the die
    ray = rays(cam)[y0:y1, x0:x1][sel]
    n_px = nw[k_of]
    t = ((centres[k_of] - cam.eye) * n_px).sum(axis=1) / (ray * n_px).sum(axis=1)
    body = (cam.eye + t[:, None] * ray - x) @ r
    rough = grit(body, 0.24, seed) * 0.6 + grit(body, 0.09, seed + 1) * 0.4
    albedo = np.array(m.base) * (1 - m.blotch * rough[:, None]) + np.array(m.stain) * m.blotch * rough[:, None]
    albedo = albedo * (1 + m.grain * (grit(body, 0.045, seed + 2)[:, None] - 0.5))
    if m.pits:
        albedo = albedo * np.where(grit(body, 0.05, seed + 3)[:, None] < m.pits, 0.45, 1.0)
    # each kite lit once: the candle, the cold fill, the far side's rim light, a weak gloss
    to_candle = np.array([unit(CANDLE - c) for c in centres])
    to_eye = np.array([unit(cam.eye - c) for c in centres])
    strength = float(candlelight(x[None])[0])
    key = np.clip((nw * to_candle).sum(axis=1), 0, 1)[:, None] * WARM * strength
    fill = np.clip(nw @ FILL, 0, 1)[:, None] * COLD * 0.35
    rim = (np.clip(nw @ RIMLIGHT, 0, 1) * (1 - np.clip((nw * to_eye).sum(axis=1), 0, 1)) ** 2)[:, None] * np.array(m.rim) * 1.6
    halfway = np.array([unit(a + b) for a, b in zip(to_candle, to_eye, strict=True)])
    gloss = (np.clip((nw * halfway).sum(axis=1), 0, 1) ** m.shine)[:, None] * m.spec * strength
    light = 0.2 + key + fill
    colour = albedo * light[k_of] + rim[k_of] + gloss[k_of]
    if m.glow:  # a screen's phosphor lights itself; the kites are only a faint fill
        colour = albedo * (0.6 + 0.6 * np.clip(nw[k_of] @ unit([0.0, 0.3, 1.0]), 0, 1))[:, None]
    tile = img[y0:y1, x0:x1]
    tile[sel] = colour
    under, over = motif or (None, None)
    if under is not None:
        under(tile, cam, skin, x, r, visible, (x0, y0), sel, ids_a, gold, top)
    # the numbers, every kite's: a dark cut, the paint in it; each kept to its own kite
    size_px, at_v = DECAL[kind]
    for paint in (False, True):
        if paint is False and m.glow:
            continue  # a screen's lines have no cut
        im = Image.new("L", size, 0)
        dr = ImageDraw.Draw(im)
        for k in visible:
            grow = 0.0 if paint else m.cut if gold and k == top else GROOVE  # the yellow gets a wide dark rim
            for square in digits(label(kind, k), size_px, at_v, grow):
                pts, _ = cam.project(x + on_kite(k, square) @ r.T)
                poly(dr, pts, (x0, y0), k + 1)
        mark = np.asarray(im).astype(np.int16)
        on = (mark > 0) & (mark == ids_a)
        if not on.any():
            continue
        faces_on = ids_a[on] - 1
        lit = np.clip(light[faces_on] @ np.array([0.3, 0.59, 0.11]), 0.55, 1.15)[:, None]  # the paint keeps its colour
        if not paint:
            tile[on] = np.array(m.groove) * lit
            continue
        paint_colour = np.where((gold & (faces_on == top))[:, None], np.array(GOLD), np.array(m.ink))
        tile[on] = paint_colour if m.glow else paint_colour * lit
    if over is not None:
        over(tile, cam, skin, x, r, visible, (x0, y0), sel, ids_a, gold, top)
    if skin == "phosphor":
        wire(tile, cam, x, r, visible, (x0, y0))
    img[y0:y1, x0:x1] = tile
    return placed


def wire(tile: np.ndarray, cam: Camera, x: np.ndarray, r: np.ndarray, visible: list[int], at: tuple[int, int]) -> None:
    """A vector screen's die: every edge a phosphor line, the hidden ones dim."""
    verts, faces, _ = d10()
    scr, _ = cam.project(x + verts @ r.T)
    h, w = tile.shape[:2]
    near, far = Image.new("L", (w, h), 0), Image.new("L", (w, h), 0)
    dn, df = ImageDraw.Draw(near), ImageDraw.Draw(far)
    for k, f in enumerate(faces):
        pts = [(float(scr[i][0] - at[0]), float(scr[i][1] - at[1])) for i in (*f, f[0])]
        (dn if k in visible else df).line(pts, fill=255, width=1)
    lines = np.maximum(np.asarray(near, np.float32), np.asarray(far, np.float32) * 0.3)[..., None] / 255
    tile[...] = tile * (1 - lines) + PHOSPHOR * lines


# -- the skins' marks --------------------------------------------------------------------------
def marks(skin: str):
    """The skin's marks: what goes under the numbers, and what over them."""
    return {"bone": (scrimshaw, None), "obsidian": (eyes, None), "blood": (drowned, None), "brass": (glyphs, None),
            "eldritch": (signs, coil)}.get(skin)


def _strokes(tile, cam, x, r, visible, at, ids, paths_for, colour, width=1, skip=None):
    """Lines drawn on the kites, each kite's from paths_for(k) in (u, v), kept to that kite."""
    h, w = tile.shape[:2]
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    for k in visible:
        if skip is not None and k == skip:
            continue
        for path in paths_for(k):
            pts, _ = cam.project(x + on_kite(k, np.array(path)) @ r.T)
            d.line([(float(px - at[0]), float(py - at[1])) for px, py in pts], fill=k + 1, width=width)
    a = np.asarray(im).astype(np.int16)
    hit = (a > 0) & (a == ids)
    tile[hit] = tile[hit] * 0.25 + np.array(colour) * 0.75


def curl(u0: float, v0: float, size: float, turn: float = 1.0) -> list[tuple[float, float]]:
    """A tentacle's curling tip, in (u, v)."""
    return [(u0 + turn * size * (1 - a / 9) * math.cos(a), v0 + size * 1.6 * (1 - a / 9) * math.sin(a)) for a in np.linspace(0, 7, 18)]


def scrimshaw(tile, cam, skin, x, r, visible, at, sel, ids, gold, top) -> None:
    """Bone: a tentacle scratched into each kite above its number and rubbed with ink."""
    def paths(k):
        turn = 1.0 if k % 2 else -1.0
        return [[(-0.3 * turn, 0.42), (-0.05 * turn, 0.5), *curl(0.08 * turn, 0.62, 0.11, turn)]]

    _strokes(tile, cam, x, r, visible, at, ids, paths, (0.2, 0.11, 0.05), width=max(1, round(cam.f / DISTANCE * 0.035)))


def eyes(tile, cam, skin, x, r, visible, at, sel, ids, gold, top) -> None:
    """Obsidian: an eye open on every kite above its number, green, its pupil a slit."""
    h, w = tile.shape[:2]
    iris, slit = Image.new("L", (w, h), 0), Image.new("L", (w, h), 0)
    di, ds = ImageDraw.Draw(iris), ImageDraw.Draw(slit)
    for k in visible:
        for wide, tall, draw in ((0.22, 0.09, di), (0.045, 0.1, ds)):
            t = np.linspace(-1, 1, 12)
            uv = np.concatenate([np.stack([wide * t, 0.64 + tall * (1 - t * t)], 1), np.stack([wide * t[::-1], 0.64 - tall * (1 - t * t)], 1)])
            pts, _ = cam.project(x + on_kite(k, uv) @ r.T)
            poly(draw, pts, at, k + 1)
    a, b = np.asarray(iris).astype(np.int16), np.asarray(slit).astype(np.int16)
    green = (a > 0) & (a == ids) & ~((b > 0) & (b == ids))
    tile[green] = np.array([0.25, 0.85, 0.35])
    tile[(b > 0) & (b == ids)] = 0.0


def drowned(tile, cam, skin, x, r, visible, at, sel, ids, gold, top) -> None:
    """Blood amber: a tentacle caught inside, curled round the middle, dark through the resin."""
    path = [np.array([0.55 * math.cos(a) * (1 - a / 14), 0.55 * math.sin(a) * (1 - a / 14), 0.25 * math.sin(a * 0.7)])
            for a in np.linspace(0, 9, 40)]
    h, w = tile.shape[:2]
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    for i, p in enumerate(path):
        sx, sy, _ = cam.point(x + r @ p)
        sx, sy = sx - at[0], sy - at[1]
        pr = (0.13 * (1 - i / len(path)) + 0.03) * cam.f / DISTANCE
        d.ellipse([sx - pr, sy - pr, sx + pr, sy + pr], fill=255)
    a = (np.asarray(im) > 0) & sel
    tile[a] = tile[a] * np.array([0.35, 0.3, 0.3])


GLYPHS = (  # strokes of made-up R'lyehian letters, in a unit box
    (((0, 0), (0.5, 1), (1, 0)), ((0.2, 0.4), (0.8, 0.4))),
    (((0.5, 0), (0.5, 1)), ((0, 0.7), (0.5, 0.45), (1, 0.7))),
    (((0, 1), (1, 1), (0.3, 0)), ((0.7, 0.5), (1, 0))),
    (((0, 0.5), (0.5, 1), (1, 0.5), (0.5, 0), (0, 0.5)), ((0.5, 0.25), (0.5, 0.75))),
    (((0, 0), (0, 1), (1, 0.6)), ((0.4, 0.3), (1, 0))),
)


def glyphs(tile, cam, skin, x, r, visible, at, sel, ids, gold, top) -> None:
    """Brass: a R'lyeh glyph cut into each kite above its number, green with verdigris."""
    def paths(k):
        g = GLYPHS[k % len(GLYPHS)]
        return [[(-0.15 + 0.3 * px, 0.45 + 0.28 * py) for px, py in stroke] for stroke in g]

    _strokes(tile, cam, x, r, visible, at, ids, paths, (0.08, 0.2, 0.15), width=max(1, round(cam.f / DISTANCE * 0.04)))


def elder_sign(k: int) -> list[list[tuple[float, float]]]:
    """The Elder Sign: a branch with five twigs, above the number."""
    stem = [(0.0, 0.42), (0.0, 0.86)]
    twigs = [[(0.0, 0.5 + 0.08 * i), (0.14 * s, 0.56 + 0.08 * i)] for i, s in ((0, -1), (1, 1), (2, -1), (3, 1))]
    return [stem, *twigs, [(0.0, 0.86), (-0.08, 0.92)], [(0.0, 0.86), (0.08, 0.92)]]


def signs(tile, cam, skin, x, r, visible, at, sel, ids, gold, top) -> None:
    """Old stone: the Elder Sign on each kite, above its number."""
    _strokes(tile, cam, x, r, visible, at, ids, elder_sign, (0.55, 0.95, 0.72), width=1)


def coil(tile, cam, skin, x, r, visible, at, sel, ids, gold, top) -> None:
    """Old stone: a tentacle coiled round the die, its stretches behind the stone hidden."""
    centre_depth = cam.point(x)[2]
    h, w = tile.shape[:2]
    body, lit = Image.new("L", (w, h), 0), Image.new("L", (w, h), 0)
    db, dl = ImageDraw.Draw(body), ImageDraw.Draw(lit)
    n = 46
    for i in range(n):
        s = i / (n - 1)
        a = 2 * math.pi * 1.25 * s + 0.6
        p = np.array([1.02 * math.cos(a) * (1 - 0.35 * abs(s - 0.5)), 1.02 * math.sin(a) * (1 - 0.35 * abs(s - 0.5)), -0.75 + 1.4 * s])
        sx, sy, dd = cam.point(x + r @ p)
        if dd > centre_depth:  # behind the stone
            continue
        sx, sy = sx - at[0], sy - at[1]
        pr = (0.16 * (1 - s) + 0.04) * cam.f / DISTANCE
        db.ellipse([sx - pr, sy - pr, sx + pr, sy + pr], fill=255)
        dl.ellipse([sx - pr, sy - pr, sx + pr * 0.25, sy + pr * 0.25], fill=255)
    b, l_ = np.asarray(body) > 0, np.asarray(lit) > 0
    tile[b] = np.array([0.13, 0.07, 0.15])
    tile[b & l_] = np.array([0.32, 0.2, 0.36])


# -- shadows and frames ------------------------------------------------------------------------
def hull(pts: np.ndarray) -> list[tuple[float, float]]:
    """The convex hull of points (n, 2), as a polygon."""
    p = sorted(map(tuple, pts.tolist()))

    def half(seq):
        out: list[tuple[float, float]] = []
        for q in seq:
            while len(out) >= 2 and (out[-1][0] - out[-2][0]) * (q[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (q[0] - out[-2][0]) <= 0:
                out.pop()
            out.append(q)
        return out

    lower, upper = half(p), half(reversed(p))
    return lower[:-1] + upper[:-1]


def shadows(img: np.ndarray, cam: Camera, poses, skin: str) -> None:
    """Each die's shadow on the floor: thrown away from the candle, hard-edged, paler and wider
    the higher it is, and a dark patch right under it where it touches."""
    if skin == "phosphor":
        return
    verts = d10()[0]
    fl = floor_at(cam)
    im = Image.new("L", (cam.w, cam.h), 0)
    d = ImageDraw.Draw(im)
    for x, r in poses:
        world = x + verts @ r.T
        away = unit(world.mean(axis=0) - CANDLE)
        cast = world + away * (world[:, 2] / -away[2])[:, None]  # along the candle's rays down to the floor
        pts, _ = cam.project(cast)
        height = max(0.0, float(x[2]) - lying())
        d.polygon(hull(pts), fill=int(150 / (1 + 0.6 * height)))
    dark = np.asarray(im, np.float32)[..., None] / 255
    for x, _ in poses:
        reach = 0.95 + 0.25 * max(0.0, float(x[2]) - lying())
        d2 = (fl[..., 0] - x[0]) ** 2 + (fl[..., 1] - x[1]) ** 2
        touch = np.clip(1 - d2 / reach**2, 0, 1) / (1 + 0.8 * max(0.0, float(x[2]) - lying()))
        dark = np.maximum(dark, (touch * 0.75)[..., None])
    img *= 1 - dark


def picture(skin: str, cols: int, rows: int, tray: tuple[float, float], dice: list[tuple[str, np.ndarray, np.ndarray]],
            *, gold: list[bool] | None = None) -> tuple[Image.Image, list[Placed]]:
    """One frame of the tray: dice (kind, centre, turn) where the throw has them, the nearest
    painted last. Returns the picture (CELL pixels a cell) and where each die's top face is."""
    cam = camera(cols, rows, tuple(tray))
    img = room(skin, cols, rows, tuple(tray)).copy()
    shadows(img, cam, [(x, r) for _, x, r in dice], skin)
    order = sorted(range(len(dice)), key=lambda i: -cam.point(dice[i][1])[2])
    placed: list[Placed | None] = [None] * len(dice)
    motif = marks(skin)
    for i in order:
        kind, x, r = dice[i]
        placed[i] = die(img, cam, skin, kind, x, r, seed=31 + i, gold=bool(gold and gold[i]), motif=motif)
    if skin == "phosphor":  # the screen's scanlines
        img[1::4] *= 0.5
        img[2::4] *= 0.8
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)), [p for p in placed if p is not None]
