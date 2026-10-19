"""The percentile dice on the side column, in the skin the player picked.

A check's dice are thrown for real (rules/physdice.py) and the tray replays that very throw:
each frame painted (diceart.py) and rendered by chafa in the player's art style like the
scenes, in a worker thread as fast as the machine allows and shown on the throw's own clock,
frames that cannot be ready in time skipped. The numbers are the dice's own, cut into every
face; once the dice lie still, those on the top faces turn yellow.

Without chafa, or for a roll that was not thrown (the plain dice it falls back to), the tray
draws the pixel-font dice instead: two solid d10s with a shadow, tumbling a moment, then the
result. A digit is 3 pixels wide and 5 tall, drawn with half blocks (▀ ▄ █) so a terminal cell
holds two pixel rows: 3 columns by 3 rows a digit. The tens die shows two digits (30), as a
real tens die does; the units die one.
"""

from __future__ import annotations

import random
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from rich.cells import get_character_cell_size
from rich.style import Style
from rich.text import Text

FONT = {  # 3 x 5 pixels, row by row
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"),
    "3": ("###", "..#", ".##", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", ".#.", ".#.", ".#."),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"),
}
HALF = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}
FRAMES = {  # corners and edges: top-left, top, top-right, side, bottom-left, bottom, bottom-right
    "rounded": "╭─╮│╰─╯",
    "heavy": "┏━┓┃┗━┛",
    "double": "╔═╗║╚═╝",
    "square": "┌─┐│└─┘",
    "eldritch": "╭≈╮┆╰≈╯",
}
LEVEL_EDGE = {"大成功": "#ffd700", "极难成功": "#7cfc00", "困难成功": "#3cb371", "常规成功": "#3cb371",
              "失败": "#ff6347", "大失败": "#b00020"}  # fmt: skip
GOLD = "#ffd700"  # the top faces' numbers once the dice lie still


@dataclass(frozen=True)
class Skin:
    name: str
    body: str | None  # the die's colour; None: see-through, only its outline
    ink: str  # the numbers
    edge: str  # the outline while it tumbles
    shadow: str
    frame: str  # FRAMES
    note: str = ""  # what it looks like, for the settings


SKINS: dict[str, Skin] = {
    "bone": Skin("骨白", "#e6d8b8", "#3b2614", "#a8977a", "#3a3026", "rounded", "骨雕，刻着墨染的触手"),
    "obsidian": Skin("黑曜石", "#1a1622", "#e9c46a", "#6d5a96", "#000000", "heavy", "黑玻璃，每面睁着一只绿眼"),
    "blood": Skin("血珀", "#5e0b0b", "#ffd9cc", "#c1121f", "#1c0303", "rounded", "琥珀里封着一截触手"),
    "brass": Skin("黄铜", "#b58f52", "#2a1a08", "#e3c17d", "#3d2c12", "double", "刻着拉莱耶文字，生了铜绿"),
    "eldritch": Skin("旧日", "#0e3a38", "#b8f5da", "#7b5ea7", "#041413", "eldritch", "绿石刻着旧印，触手缠绕"),
    "phosphor": Skin("磷光", None, "#33ff66", "#1fbf4c", "#062b0f", "square", "示波屏上的线框"),
}
DEFAULT = "bone"
WIDTH, HEIGHT = 11, 5  # a die: its border, three rows of digits between blank margins


def digits(number: str) -> list[str]:
    """`number` in the pixel font, as three rows of half blocks, a column between digits."""
    rows = ["", "", ""]
    for i, ch in enumerate(number):
        glyph = (*FONT.get(ch, FONT["0"]), "...")  # six pixel rows: three cell rows
        for r in range(3):
            top, bottom = glyph[2 * r], glyph[2 * r + 1]
            rows[r] += ("" if i == 0 else " ") + "".join(HALF[(a == "#", b == "#")] for a, b in zip(top, bottom, strict=True))
    return rows


def die(number: str, skin: Skin, edge: str | None = None) -> list[Text]:
    """One die, HEIGHT rows of WIDTH + 1 cells (the last column and a row under it: shadow)."""
    tl, t, tr, side, bl, b, br = FRAMES[skin.frame]
    edge_style = Style(color=edge or skin.edge, bold=True)
    body = Style(bgcolor=skin.body) if skin.body else Style()
    ink = Style(color=skin.ink, bgcolor=skin.body, bold=True) if skin.body else Style(color=skin.ink, bold=True)
    shade = Style(color=skin.shadow)
    inner = WIDTH - 2
    face = digits(number)
    pad = (inner - len(face[0])) // 2
    lines = [Text.assemble((tl + t * inner + tr, edge_style), (" ", ""))]
    for row in face:
        text = Text(side, style=edge_style)
        text.append(" " * pad, style=body)
        text.append(row, style=ink)
        text.append(" " * (inner - pad - len(row)), style=body)
        text.append(side, style=edge_style)
        text.append("▌", style=shade)
        lines.append(text)
    lines.append(Text.assemble((bl + b * inner + br, edge_style), ("▌", shade)))
    lines.append(Text.assemble((" ", ""), ("▀" * WIDTH, shade)))
    return lines


def pair(tens: str, units: str, skin: Skin, *, rolling: bool = False, level: str = "") -> Text:
    """Both dice side by side. Tumbling, each bounces a row on its own; settled, their
    outline takes the result's colour."""
    edge = None if rolling else LEVEL_EDGE.get(level)
    left, right = die(tens, skin, edge), die(units, skin, edge)
    blank = Text(" " * (WIDTH + 1))
    if rolling:  # one die up, the other down, at random: they tumble
        lift = random.choice((0, 1))
        left = ([blank] * lift) + left + ([blank] * (1 - lift))
        right = ([blank] * (1 - lift)) + right + ([blank] * lift)
    else:
        left, right = [*left, blank], [*right, blank]
    out = Text()
    for i, (a, b) in enumerate(zip(left, right, strict=True)):
        out.append("  ")
        out.append_text(a)
        out.append("  ")
        out.append_text(b)
        if i < len(left) - 1:
            out.append("\n")
    return out


def skin(name: str | None) -> Skin:
    return SKINS.get(name or DEFAULT, SKINS[DEFAULT])


# -- chafa's cells -------------------------------------------------------------------------------
Cells = list[list[tuple[str, Style]]]


def cells(ansi: str, cols: int, rows: int) -> Cells:
    """chafa's picture as rows × cols (character, style) cells, padded or cut to the size (chafa
    may come a row short). A wide character takes its cell and an empty one after it."""
    grid: Cells = []
    for line in Text.from_ansi(ansi).split("\n", allow_blank=True):
        styles = [Style()] * len(line.plain)
        for span in line.spans:
            st = Style.parse(span.style) if isinstance(span.style, str) else span.style
            for i in range(span.start, min(span.end, len(styles))):
                styles[i] = styles[i] + st
        row: list[tuple[str, Style]] = []
        for ch, st in zip(line.plain, styles, strict=True):
            row.append((ch, st))
            if get_character_cell_size(ch) == 2:
                row.append(("", st))
        grid.append((row + [(" ", Style())] * cols)[:cols])
    return (grid + [[(" ", Style())] * cols for _ in range(rows)])[:rows]


def compose(grid: Cells) -> Text:
    out = Text(no_wrap=True, overflow="crop", end="")
    for i, row in enumerate(grid):
        run, style = [], None
        for ch, st in row:
            if st != style and run:
                out.append("".join(run), style)
                run = []
            run.append(ch)
            style = st
        if run:
            out.append("".join(run), style)
        if i < len(grid) - 1:
            out.append("\n")
    return out


# -- a throw on the tray -----------------------------------------------------------------------
BUDGET = 1.6  # seconds: a longer throw is played faster, so it never drags


@dataclass
class Replay:
    """A thrown check as the tray shows it: the recording, and which dice's numbers turn gold
    (the units die and the tens die that counted)."""

    kinds: list[str]
    labels: list[str]
    tray: tuple[float, float]
    fps: float
    centres: np.ndarray  # (frames, dice, 3)
    turns: np.ndarray  # (frames, dice, 3, 3)
    gold: list[bool]

    @property
    def frames(self) -> int:
        return len(self.centres)

    @property
    def speed(self) -> float:
        return max(1.0, (self.frames - 1) / self.fps / BUDGET)

    def due(self, k: int) -> float:
        """When frame k is shown, in seconds from the start."""
        return k / self.fps / self.speed


def replay(view: dict) -> Replay | None:
    """The throw in a dice event, or None (plain dice; an old or broken recording)."""
    from ..rules.physdice import decode

    t = view.get("throw")
    try:
        centres, turns = decode(t)  # type: ignore[arg-type]
        kinds, labels = list(t["kinds"]), list(t["labels"])  # type: ignore[index]
        if not len(centres) or len(kinds) != centres.shape[1] or len(labels) != len(kinds):
            return None
        kept = int(view.get("kept") or 0)
        tens = [i for i, k in enumerate(kinds) if k == "tens"]
        gold = [k == "units" or (len(tens) > 1 and tens.index(i) == kept) or len(tens) == 1 for i, k in enumerate(kinds)]
        return Replay(kinds, labels, (float(t["tray"][0]), float(t["tray"][1])), float(t["fps"]), centres, turns, gold)  # type: ignore[index]
    except (TypeError, KeyError, ValueError, IndexError):
        return None


@dataclass
class Timing:
    """What a throw's frames took, per frame in ms: painting, chafa, and turning chafa's
    output into cells."""

    paint: list[float] = field(default_factory=list)
    chafa: list[float] = field(default_factory=list)
    lay: list[float] = field(default_factory=list)


def frame(r: Replay, k: int, skin_key: str, cols: int, rows: int, style: str, timing: Timing | None = None) -> Text:
    """Frame k of the throw: painted, then through chafa in `style`. In the last the dice lie
    still and the numbers on the top faces of those that count are painted gold."""
    from .. import imagery
    from . import diceart

    t0 = time.perf_counter()
    last = k == r.frames - 1
    dice = [(kind, r.centres[k, i], r.turns[k, i]) for i, kind in enumerate(r.kinds)]
    im, _ = diceart.picture(skin_key, cols, rows, r.tray, dice, gold=r.gold if last else None)
    t1 = time.perf_counter()
    out = compose(cells(imagery._chafa(im, cols, rows, style, work=9 if last else 5), cols, rows))
    t2 = time.perf_counter()
    if timing is not None:
        timing.paint.append((t1 - t0) * 1000)
        timing.chafa.append((t2 - t1) * 1000)
        timing.lay.append((time.perf_counter() - t2) * 1000)
    return out


class Showing:
    """One throw on the tray: its frames rendered in a worker thread and handed to `show` on the
    throw's own clock. A frame that could not be ready before the next one is due is skipped,
    so a slow machine sees stop motion instead of a slow throw; the last frame, the dice at rest
    with their numbers, is always shown. Without animation only the last is rendered."""

    def __init__(self, r: Replay, render: Callable[[int], Text], *, animate: bool = True,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.replay, self.render, self.animate = r, render, animate
        self.clock, self.sleep = clock, sleep
        self.stop = threading.Event()
        self.shown: list[int] = []  # frame indices, in the order shown
        self.ms = 0.0  # from the start to the dice at rest on the screen

    def run(self, show: Callable[[Text, bool], None]) -> None:
        r = self.replay
        last = r.frames - 1
        start = self.clock()
        guess = 0.0  # how long a frame takes here, learnt as they come
        k = 0 if self.animate else last
        while not self.stop.is_set():
            now = self.clock() - start
            if k < last and now + guess > r.due(k + 1):  # it would come too late: on to a later one
                k += 1
                continue
            began = self.clock()
            try:
                text = self.render(k)
            except (OSError, subprocess.TimeoutExpired):
                if k == last:
                    return
                k += 1
                continue
            took = self.clock() - began
            guess = took if not self.shown else 0.7 * guess + 0.3 * took
            wait = r.due(k) - (self.clock() - start)
            if wait > 0 and self.animate:
                self.sleep(wait)
            if self.stop.is_set():
                return
            show(text, k == last)
            self.shown.append(k)
            if k == last:
                self.ms = (self.clock() - start) * 1000
                return
            k += 1
