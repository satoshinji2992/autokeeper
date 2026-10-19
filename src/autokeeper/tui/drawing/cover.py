"""The title screen: Cthulhu rising off R'lyeh behind everything, dim, the sea and his
tentacles swaying, lightning now and then, the name torn over the sky, a see-through menu.

The painting is Dominique Signoret's "Cthulhu and R'lyeh" (CC BY-SA 3.0), from Wikimedia
Commons: https://commons.wikimedia.org/wiki/File:Cthulhu_and_R%27lyeh.jpg

A few frames are drawn once per screen size (chafa, in a thread) and looped; the words go
over them cell by cell, keeping the painting's colour behind the title's letters. Without
chafa the hand-drawn eye stands in for the painting.

The screen shows them through Cover, a widget of ready-made rows (Textual's line API): a
frame is turned into rows once, and moving the menu's bar or glitching the name redoes the
rows it touches, not the six thousand cells of the screen."""

from __future__ import annotations

import math
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from rich.cells import cell_len, get_character_cell_size
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.strip import Strip
from textual.widget import Widget

from ... import artkit, imagery
from . import art

PICTURE = Path(__file__).with_name("cover.jpg")
FRAMES = 16  # one swell of the sea; the screen shows a frame every other tick
HEAD = 175 / 550  # how far down the painting his eyes are
SPAN = 1.0  # how much of the screen's width the painting takes (all: the screen is filled); any rest is its blurred copy
DIM = 0.5  # how much of its light the painting keeps
VEIL = 0.62  # how much of the menu's own colour lies over the painting behind it
CELL = (6, 12)  # pixels per cell handed to chafa; terminal cells are twice as tall as wide
GROUND = Style(bgcolor="#060403")
PLAQUE = Style(bgcolor="#0b0806")  # behind the tagline and the hint, so they read over the sea

Cells = list[list[tuple[str, Style]]]


def cells(text: Text, width: int | None = None) -> Cells:
    """Each line of `text` as (character, style) cells; a wide character takes its own cell
    and leaves an empty one after it."""
    rows: Cells = []
    for line in text.split("\n", allow_blank=True):
        styles = [Style.parse(line.style) if isinstance(line.style, str) else line.style] * len(line.plain)
        for span in line.spans:
            st = Style.parse(span.style) if isinstance(span.style, str) else span.style
            for i in range(span.start, min(span.end, len(styles))):
                styles[i] = styles[i] + st
        row: list[tuple[str, Style]] = []
        for ch, st in zip(line.plain, styles, strict=True):
            row.append((ch, st))
            if get_character_cell_size(ch) == 2:  # CJK and fullwidth forms; not sextants (六分块), past them in Unicode
                row.append(("", st))
        rows.append(row[:width] if width else row)
    return rows


def _scale(cols: int) -> float:
    """Screen pixels per pixel of the painting: it spans SPAN of the screen's width."""
    return cols * CELL[0] * SPAN / Image.open(PICTURE).width


def focus(cols: int, rows: int, row: float) -> float:
    """Where the crop sits on the tall painting (0 top, 1 bottom) so his eyes come to `row`
    on the screen."""
    height = Image.open(PICTURE).height
    band = rows * CELL[1] / _scale(cols)  # the screen's height, in the painting's pixels
    if band >= height:
        return 0.5
    top = HEAD * height - row / rows * band
    return round(min(1.0, max(0.0, top / (height - band))), 3)


def canvas(cols: int, rows: int, middle: float) -> Image.Image:
    """The painting across most of the screen's width, cropped top and bottom (`middle`:
    where the crop sits, 0 top to 1 bottom), its sides melting into a blurred, darkened copy
    that fills the rest; then all of it dimmed and shaded at the edges."""
    size = (cols * CELL[0], rows * CELL[1])
    photo = Image.open(PICTURE).convert("RGB")
    photo = ImageEnhance.Color(ImageEnhance.Contrast(photo).enhance(1.25)).enhance(1.2)
    back = ImageOps.fit(photo, size, Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(max(size) / 40))
    back = ImageEnhance.Brightness(back).enhance(0.35)
    scale = _scale(cols)
    front = photo.resize((round(photo.width * scale), round(photo.height * scale)), Image.Resampling.LANCZOS)
    top = round(max(0, front.height - size[1]) * middle)
    front = front.crop((0, top, front.width, top + min(size[1], front.height)))
    edge = Image.linear_gradient("L").rotate(90).resize((max(1, front.width // 5), front.height))  # soft sides
    mask = Image.new("L", front.size, 255)
    if front.width < size[0]:
        mask.paste(edge.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (0, 0))
        mask.paste(edge, (front.width - edge.width, 0))
    back.paste(front, ((size[0] - front.width) // 2, (size[1] - front.height) // 2), mask)
    # well back: the name and the menu are what is read, he only looms behind them
    back = ImageEnhance.Brightness(ImageEnhance.Color(back).enhance(0.8)).enhance(DIM)
    shade = Image.radial_gradient("L").resize(size).point(lambda v: int(v * 0.6))
    return Image.composite(Image.new("RGB", size, (0, 0, 0)), back, shade)


def moment(im: Image.Image, k: int, flash: bool = False) -> Image.Image:
    """Frame k of the loop: the lower half swaying sideways (sea, tentacles), more the deeper
    it is; a slow breath of light; a lightning frame lifts the sky."""
    phase = 2 * math.pi * k / FRAMES
    a = np.asarray(im)
    h, w = a.shape[:2]
    out = a.copy()
    for y in range(h):
        t = y / h
        reach = max(0.0, (t - 0.38) / 0.62) ** 1.5 * w * 0.012
        if reach >= 0.5:
            out[y] = np.roll(a[y], round(reach * math.sin(phase + t * 9)), axis=0)
    frame = ImageEnhance.Brightness(Image.fromarray(out)).enhance(0.9 + 0.08 * math.sin(phase))
    if flash:
        lit = Image.blend(ImageEnhance.Brightness(frame).enhance(2.4), Image.new("RGB", frame.size, (210, 225, 255)), 0.3)
        sky = Image.linear_gradient("L").rotate(180).resize(frame.size).point(lambda v: 70 + int(v * 0.72))
        frame = Image.composite(lit, frame, sky)
    return frame


@lru_cache(maxsize=2)
def _canvas(cols: int, rows: int, middle: float) -> Image.Image:
    return canvas(cols, rows, middle)


@lru_cache(maxsize=48)
def picture(cols: int, rows: int, middle: float, style: str, k: int) -> str:
    """Frame k as chafa drew it (k == FRAMES: the lightning frame), cached per screen size:
    text, which the garbage collector never walks, where the cells made from it are a hundred
    thousand tuples."""
    im = moment(_canvas(cols, rows, middle), k % FRAMES, flash=k == FRAMES)
    try:
        return imagery._chafa(im, cols, rows, style, work=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""


@lru_cache(maxsize=FRAMES + 1)
def frame(cols: int, rows: int, middle: float, style: str, k: int) -> Cells:
    """Frame k as cells, for the title on screen; it lets them go while a game is on (clear)."""
    ansi = picture(cols, rows, middle, style, k)
    return cells(Text.from_ansi(ansi), cols) if ansi else []


def clear() -> None:
    """Let the cells go; the pictures stay, so the title comes back without chafa."""
    frame.cache_clear()


def eye(cols: int, rows: int, _focus: float = 0.0) -> Cells:
    """No chafa: the hand-drawn eye where the painting would be."""
    mask = []
    for i, line in enumerate(art.EYE.split("\n")):
        key = "P" if i < 2 else "p" if i < 6 else "R"
        mask.append("".join("y" if ch in "()" and 2 <= i <= 4 else key for ch in line))
    grid = blank(cols, rows)
    drawn = cells(artkit.colorize(art.EYE, "\n".join(mask)))
    x = (cols - max(map(len, drawn))) // 2
    put(grid, drawn, x, max(0, rows - len(drawn) - 4))
    return grid


def blank(cols: int, rows: int) -> Cells:
    return [[(" ", GROUND)] * cols for _ in range(rows)]


def put(grid: Cells, drawn: Cells, x: int, y: int, *, under: Style | None = None, keep_ground: bool = False) -> None:
    """Lay `drawn` over the grid at (x, y), spaces left see-through. `keep_ground`: letters
    keep the colour behind them (the title on the sky); `under`: a plaque behind them."""
    for dy, row in enumerate(drawn):
        line = grid[y + dy] if 0 <= y + dy < len(grid) else None
        if line is None:
            continue
        for dx, (ch, st) in enumerate(row):
            at = x + dx
            if not 0 <= at < len(line) or (ch == " " and under is None):
                continue
            if at + 1 < len(line) and line[at + 1][0] == "" and ch != "" and (dx + 1 >= len(row) or row[dx + 1][0] != ""):
                line[at + 1] = (" ", line[at + 1][1])  # do not leave half of a wide character
            if keep_ground:
                st = Style(bgcolor=line[at][1].bgcolor) + st
            elif under is not None:
                st = under + st
            line[at] = (ch, st)


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


def strips(grid: Cells) -> list[Strip]:
    """The grid as Textual rows, a run of one style a segment, as compose() makes its lines."""
    rows = []
    for row in grid:
        segments, run, style = [], [], None
        for ch, st in row:
            if st != style and run:
                segments.append(Segment("".join(run), style))
                run = []
            run.append(ch)
            style = st
        if run:
            segments.append(Segment("".join(run), style))
        rows.append(Strip(segments))
    return rows


class Cover(Widget):
    """The title's picture, a frame at a time: whole rows, made once each and shown in turn."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.rows: list[Strip] = []

    def show(self, rows: list[Strip]) -> None:
        self.rows = rows
        self.refresh()

    def render_line(self, y: int) -> Strip:
        return self.rows[y] if y < len(self.rows) else Strip.blank(self.size.width)

    @property
    def plain(self) -> str:
        """What it shows, as text."""
        return "\n".join(row.text for row in self.rows)


def _mix(a, b: str, amount: float):
    """Colour a moved `amount` of the way towards b (a hex string)."""
    from rich.color import Color

    if a is None:
        return Color.parse(b)
    one, two = a.get_truecolor(), Color.parse(b).get_truecolor()
    return Color.from_rgb(*(round(p + (q - p) * amount) for p, q in zip(one, two, strict=True)))


def menu(grid: Cells, box: tuple[int, int, int, int], labels: list[str], at: int | None, accent: str, ink: str, panel: str) -> None:
    """A heavy-bordered menu laid over the grid, the picture showing through it (VEIL)."""
    x, y, w, h = box
    for row in range(y, min(y + h, len(grid))):
        line = grid[row]
        for col in range(max(0, x), min(x + w, len(line))):
            ch, st = line[col]
            line[col] = (ch, Style(color=_mix(st.color, panel, VEIL), bgcolor=_mix(st.bgcolor, panel, VEIL)))
    edge = Style(color=accent, bold=True)
    border = ["┏" + "━" * (w - 2) + "┓", *["┃" + " " * (w - 2) + "┃"] * (h - 2), "┗" + "━" * (w - 2) + "┛"]
    put(grid, [[(ch, edge) for ch in line] for line in border], x, y, keep_ground=True)
    for i, label in enumerate(labels[: h - 2]):
        if i == at:
            bar = cells(Text(f"  {label}".ljust(w - 2 - (cell_len(label) - len(label))), style=Style(color=panel, bgcolor=accent, bold=True)))
            put(grid, bar, x + 1, y + 1 + i, under=Style())
        else:
            put(grid, cells(Text(f"  {label}", style=ink)), x + 1, y + 1 + i, keep_ground=True)
