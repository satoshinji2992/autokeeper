"""Weather over a scene: rain, storms, snow, fog, wind-blown leaves, dust, embers,
flickering lamps and a torch beam, animated on top of the picture or the ASCII art.

Each frame is put together cell by cell. Light effects pick every cell from a graded copy
of the scene (artkit.GRADES: the dark outside a torch beam, a fog veil, a lightning
flash...), made once when the scene loads; particles are then drawn over it, keeping the
background colour of the cell they cross. Which weather a place has is weather.py's call.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np
from rich.style import Style
from rich.text import Text

from . import artkit

# Graded copies each kind needs ("beam": the scene in plain white light, for a torch).
GRADES_FOR = {
    "storm": ("flash",),
    "fog": ("haze", "haze2"),
    "embers": ("glow", "fade"),
    "flicker": ("glow", "fade"),
    "torch": ("beam", "dim"),
    "redlight": ("red",),
}
LAYERS = ("", "glow", "fade", "haze", "haze2", "beam", "dim", "flash", "red")  # index order of the layer map


def grades(kinds: list[str]) -> list[str]:
    """The graded copies these kinds need, besides the plain scene."""
    return sorted({g for k in kinds for g in GRADES_FOR.get(k, ())})


Cell = tuple[str, Style | None]


def grid(text: Text, width: int, height: int) -> list[list[Cell]]:
    """A scene as rows of (character, style), padded to the panel."""
    rows: list[list[Cell]] = []
    for line in text.split("\n", allow_blank=True)[:height]:
        plain = line.plain
        styles: list[Style | None] = [None] * len(plain)
        for span in line.spans:
            style = span.style if isinstance(span.style, Style) else Style.parse(span.style)
            for i in range(span.start, min(span.end, len(plain))):
                styles[i] = style
        row = list(zip(plain, styles, strict=True))[:width]
        rows.append(row + [(" ", None)] * (width - len(row)))
    while len(rows) < height:
        rows.append([(" ", None)] * width)
    return rows


@dataclass
class Mote:
    kind: str
    x: float
    y: float
    vx: float
    vy: float
    glyph: str
    color: str


class Atmosphere:
    """Composes frames for a scene; call frame() once per tick (about seven a second)."""

    def __init__(self, kinds: list[str], layers: dict[str, Text], width: int, height: int, *, when: str = "", seed: int | None = None):
        self.kinds, self.when = kinds, when
        self.w, self.h = width, height
        self.layers = {name: grid(t, width, height) for name, t in layers.items()}
        self.rng = random.Random(seed)
        self.t = 0
        self.flash = 0  # frames of lightning left
        self.level = ""  # flicker: "", "glow" or "fade" for the whole frame
        self.motes = [m for k in kinds for m in self._spawn(k)]
        self._styles: dict[tuple[str, object], Style] = {}
        night = when in artkit.DARK
        self.fog_edge = Style(color="#23282f" if night else "#3a3f46")  # thin fog over empty cells
        self.fog_core = Style(color="#30363f" if night else "#525860")
        self._lattice = np.random.default_rng(seed).random((24, 48))
        self._ys, self._xs = np.mgrid[0:height, 0:width].astype(float)

    # -- particles ---------------------------------------------------------------------------
    def _spawn(self, kind: str) -> list[Mote]:
        w, h, r = self.w, self.h, self.rng
        night = self.when in artkit.DARK
        if kind in ("rain", "storm"):
            heavy = kind == "storm"
            colour = "#4f6a8a" if night else "#9fb4c8"
            return [self._drop(colour, heavy, top=False) for _ in range(max(8, w * h // (22 if heavy else 32)))]
        if kind == "snow":
            colour = "#a9b3c2" if night else "#eef3f8"
            return [Mote(kind, r.uniform(0, w), r.uniform(0, h), 0, r.uniform(0.2, 0.45), r.choice("··*•"), colour) for _ in range(max(6, w * h // 40))]
        if kind == "leaves":
            colours = ("#c8752a", "#a0522d", "#d4a017", "#8b5a2b")
            return [Mote(kind, r.uniform(0, w), r.uniform(0, h), r.uniform(0.6, 1.3), r.uniform(0.1, 0.35), r.choice("*~'`,"), r.choice(colours)) for _ in range(8 + w // 12)]
        if kind == "dust":
            colour = "#8c8068" if night else "#d8c9a3"
            return [Mote(kind, r.uniform(0, w), r.uniform(0, h), 0, 0, r.choice("·.·'"), colour) for _ in range(8 + w // 8)]
        if kind == "embers":
            return [self._ember(top=False) for _ in range(6 + w // 14)]
        return []

    def _drop(self, colour: str, heavy: bool, *, top: bool = True) -> Mote:
        r = self.rng
        vy = r.uniform(1.6, 2.3) if heavy else r.uniform(1.0, 1.6)
        return Mote("rain", r.uniform(0, self.w + self.h * 0.6), 0 if top else r.uniform(0, self.h), -vy * 0.45, vy, "/", colour)

    def _ember(self, *, top: bool = True) -> Mote:
        r = self.rng
        return Mote("embers", r.uniform(self.w * 0.15, self.w * 0.85), self.h - 1 if top else r.uniform(0, self.h), 0, -r.uniform(0.35, 0.8), r.choice("·'*."), "#ffb347")

    def _move(self) -> None:
        r, w, h = self.rng, self.w, self.h
        for i, m in enumerate(self.motes):
            if m.kind == "rain":
                m.x, m.y = m.x + m.vx, m.y + m.vy
                if m.y >= h or m.x < 0:
                    self.motes[i] = self._drop(m.color, m.vy > 1.6)
            elif m.kind == "embers":  # rise, waver, cool from amber to red
                m.x += r.uniform(-0.35, 0.35)
                m.y += m.vy
                m.color = "#ffb347" if m.y > h * 0.6 else "#e07a2e" if m.y > h * 0.3 else "#c0501a"
                if m.y < 0:
                    self.motes[i] = self._ember()
            elif m.kind == "leaves":  # carried along by the wind, bobbing
                m.x += m.vx
                m.y += m.vy + 0.35 * math.sin(self.t * 0.4 + i)
                if m.x >= w or m.y >= h or m.y < 0:
                    m.x, m.y = r.uniform(-4, 0), r.uniform(0, h * 0.7)
            elif m.kind == "snow":
                m.x += r.uniform(-0.3, 0.3)
                m.y += m.vy
                if m.y >= h:
                    m.x, m.y = r.uniform(0, w), 0
            else:  # dust: a slow random walk
                m.x = (m.x + r.uniform(-0.25, 0.25)) % w
                m.y = (m.y + r.uniform(-0.12, 0.12)) % h

    # -- light -------------------------------------------------------------------------------
    def _noise(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Smooth value noise in 0..1 over a fixed random lattice."""
        lat = self._lattice
        n, m = lat.shape
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
        fx, fy = x - x0, y - y0
        sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        i0, i1, j0, j1 = y0 % n, (y0 + 1) % n, x0 % m, (x0 + 1) % m
        top = lat[i0, j0] + (lat[i0, j1] - lat[i0, j0]) * sx
        bottom = lat[i1, j0] + (lat[i1, j1] - lat[i1, j0]) * sx
        return top + (bottom - top) * sy

    def fog(self) -> np.ndarray:
        """Fog density per cell: wide, flat banks drifting sideways, thicker near the ground."""
        t, xs, ys = self.t, self._xs, self._ys
        wide = self._noise(xs * 0.06 - t * 0.022, ys * 0.3)
        fine = self._noise(xs * 0.15 - t * 0.045, ys * 0.6 + 7.3)
        return 0.68 * wide + 0.32 * fine + 0.22 * (ys / max(1, self.h - 1) - 0.5)

    def beam(self) -> tuple[float, float, float]:
        """Torch: centre and radius of the light, sweeping slowly."""
        t = self.t
        return self.w * (0.5 + 0.12 * math.sin(t * 0.07)), self.h * (0.5 + 0.18 * math.sin(t * 0.11 + 1)), self.h * 0.5

    def layer_map(self) -> tuple[np.ndarray, np.ndarray]:
        """Which graded copy each cell shows (index into LAYERS), and how thick the fog over
        an empty cell is: 0 none, 1 thin, 2 thick."""
        shape = (self.h, self.w)
        index = np.full(shape, LAYERS.index("red" if "redlight" in self.kinds else self.level), dtype=np.int8)
        veil = np.zeros(shape, dtype=np.int8)
        if self.flash:
            return np.full(shape, LAYERS.index("flash"), dtype=np.int8), veil
        if "fog" in self.kinds:
            density = self.fog()
            index[density > 0.5] = LAYERS.index("haze")
            index[density > 0.62] = LAYERS.index("haze2")
            veil[density > 0.68] = 1
            veil[density > 0.78] = 2
        if "torch" in self.kinds:
            cx, cy, radius = self.beam()
            d = np.hypot((self._xs - cx) * 0.5, self._ys - cy)
            index[d <= radius * 0.7] = LAYERS.index("beam")
            index[d > radius * 1.3] = LAYERS.index("dim")
            veil[d > radius * 1.3] = 0
        return index, veil

    def warp(self) -> np.ndarray:
        """Funhouse mirror: each cell shows a neighbour a column or two away, and the bulges
        drift, so the scene swells and pinches like a reflection in a bent glass."""
        t, xs, ys = self.t, self._xs, self._ys
        shift = 2.2 * np.sin(xs * 0.11 + t * 0.15) + 1.2 * np.sin(ys * 0.7 - t * 0.22)
        return np.clip(np.rint(xs + shift), 0, self.w - 1).astype(int)

    # -- one frame -------------------------------------------------------------------------
    def step(self) -> None:
        self.t += 1
        self._move()
        if "storm" in self.kinds:
            if self.flash:
                self.flash -= 1
            elif self.rng.random() < 0.025:
                self.flash = self.rng.choice((1, 2, 3))
        if ("flicker" in self.kinds or "embers" in self.kinds) and self.rng.random() < 0.4:
            self.level = self.rng.choice(("", "", "", "glow", "fade"))

    def frame(self) -> Text:
        self.step()
        return self.compose()

    def compose(self) -> Text:
        index, veil = self.layer_map()
        layers = [self.layers.get(name, self.layers[""]) for name in LAYERS]
        dark = LAYERS.index("dim")
        dots: dict[tuple[int, int], Mote] = {}
        for m in self.motes:
            x, y = int(m.x), int(m.y)
            if 0 <= x < self.w and 0 <= y < self.h and (m.kind != "dust" or self.rng.random() < 0.75):  # dust twinkles
                dots[x, y] = m
        rows, fog_rows = index.tolist(), veil.tolist()
        src = self.warp().tolist() if "warp" in self.kinds else None
        out = Text()
        for y in range(self.h):
            run: list[str] = []
            run_style: Style | None = None
            row, fog_row, cols = rows[y], fog_rows[y], src[y] if src else range(self.w)
            for x in range(self.w):
                which = row[x]
                ch, style = layers[which][y][cols[x]]
                if fog_row[x] and ch == " " and (style is None or style.bgcolor is None):
                    ch, style = "░", self.fog_core if fog_row[x] == 2 else self.fog_edge
                mote = dots.get((x, y))
                if mote is not None and (which != dark or mote.kind == "embers"):
                    ch, style = mote.glyph, self._over(mote.color, style)
                if style is not run_style and run:
                    out.append("".join(run), run_style)
                    run = []
                run_style = style
                run.append(ch)
            if run:
                out.append("".join(run), run_style)
            if y < self.h - 1:
                out.append("\n")
        return out

    def _over(self, colour: str, under: Style | None) -> Style:
        """A particle's colour over whatever background the cell already has."""
        bg = under.bgcolor if under is not None else None
        key = (colour, bg)
        if key not in self._styles:
            self._styles[key] = Style(color=colour, bgcolor=bg)
        return self._styles[key]
