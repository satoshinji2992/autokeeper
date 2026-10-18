"""The percentile dice on the side column: a tens die and a units die, drawn as solid d10s
with a shadow, their numbers in a small pixel font, in the skin the player picked.

A digit is 3 pixels wide and 5 tall, drawn with half blocks (▀ ▄ █) so a terminal cell holds
two pixel rows: 3 columns by 3 rows a digit. The tens die shows two digits (30), as a real
tens die does; the units die one. While they tumble they bounce a row and show any number;
once settled the edge takes the colour of the result.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

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


@dataclass(frozen=True)
class Skin:
    name: str
    body: str | None  # the die's colour; None: see-through, only its outline
    ink: str  # the numbers
    edge: str  # the outline while it tumbles
    shadow: str
    frame: str  # FRAMES


SKINS: dict[str, Skin] = {
    "bone": Skin("骨白", "#e6d8b8", "#3b2614", "#a8977a", "#3a3026", "rounded"),
    "obsidian": Skin("黑曜石", "#1a1622", "#e9c46a", "#6d5a96", "#000000", "heavy"),
    "blood": Skin("血珀", "#5e0b0b", "#ffd9cc", "#c1121f", "#1c0303", "rounded"),
    "brass": Skin("黄铜", "#b58f52", "#2a1a08", "#e3c17d", "#3d2c12", "double"),
    "eldritch": Skin("旧日", "#0e3a38", "#b8f5da", "#7b5ea7", "#041413", "eldritch"),
    "phosphor": Skin("磷光", None, "#33ff66", "#1fbf4c", "#062b0f", "square"),
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
