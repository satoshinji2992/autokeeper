"""Coloured ASCII art.

An art piece is two files of the same shape:

    art/shuchang.txt     the characters (printable ASCII only)
    art/shuchang.color   one palette key per character; '.' or ' ' keeps the default colour

Palette keys are single letters, so the mask lines up with the art in any editor.
Pieces without a mask are coloured by character class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rich.text import Text

PALETTE: dict[str, str] = {
    "r": "#ff5f5f",  # red
    "R": "#a4161a",  # blood
    "o": "#ff9f43",  # orange, lamplight
    "y": "#ffd75f",  # yellow
    "Y": "#ffb000",  # amber / gold
    "g": "#87d75f",  # green
    "G": "#3a7d44",  # dark green
    "c": "#5fd7ff",  # cyan
    "b": "#5f87ff",  # blue
    "B": "#27408b",  # night blue
    "m": "#d75fd7",  # magenta
    "p": "#9a7fc4",  # lavender
    "P": "#5b3f8c",  # deep purple
    "w": "#eeeeee",  # white
    "W": "bold #ffffff",
    "k": "#8a8a8a",  # grey
    "K": "#4e4e4e",  # dark grey
    "n": "#b07a4f",  # wood brown
    "N": "#6b3e26",  # dark brown
    "s": "#e8b98a",  # skin
    "t": "#2aa198",  # teal
}

PALETTE_DOC = ", ".join(f"{k}={v}" for k, v in PALETTE.items())

_AUTO = {"~": "c", "*": "y", "@": "p", "#": "K", "$": "Y", "+": "r", ".": "k", "'": "k", ":": "k", "o": "o", "O": "o"}

MAX_SCENE = (56, 14)
MAX_PORTRAIT = (18, 7)


def auto_mask(text: str) -> str:
    return "\n".join("".join(_AUTO.get(ch, ".") for ch in line) for line in text.split("\n"))


# Light by time of day, shared by pictures (imagery.light) and hand-drawn ASCII (shade):
# brightness, saturation, contrast, tint colour, tint strength, vignette strength.
LIGHTS: dict[str, tuple[float, float, float, tuple[int, int, int] | None, float, float]] = {
    "清晨": (0.92, 0.80, 0.95, (150, 175, 230), 0.14, 0.30),
    "上午": (1.00, 1.00, 1.00, None, 0.00, 0.18),
    "晌午": (1.06, 1.05, 1.05, (255, 244, 214), 0.06, 0.12),
    "下午": (1.00, 1.05, 1.02, (255, 205, 140), 0.12, 0.22),
    "傍晚": (0.82, 1.10, 1.06, (255, 130, 50), 0.24, 0.42),
    "夜里": (0.55, 0.55, 1.12, (35, 55, 135), 0.36, 0.62),
    "深夜": (0.42, 0.42, 1.18, (18, 26, 88), 0.46, 0.78),
}
_HEX = re.compile(r"#([0-9a-fA-F]{6})")
DEFAULT_INK = "#c8c8c8"  # unmasked strokes, so they take the light too
GLOW = set("oyYW")  # lamplight, candles, lit windows: they stay bright after dark
DARK = {"傍晚", "夜里", "深夜"}


def shade(color: str, when: str, *, strokes: bool = False) -> str:
    """One colour seen under the light of `when`: exposure, saturation, tint.

    `strokes` is for ASCII art on a dark terminal: lines must stay readable, so exposure
    and tint go only half as far as they do on a picture."""
    if when not in LIGHTS:
        return color
    bright, sat, _, tint, amount, _ = LIGHTS[when]
    if strokes:
        bright, amount = 1 - (1 - bright) * 0.5, amount * 0.6

    def one(m: re.Match) -> str:
        r, g, b = (int(m.group(1)[i : i + 2], 16) for i in (0, 2, 4))
        grey = 0.299 * r + 0.587 * g + 0.114 * b
        rgb = [grey + (c - grey) * sat for c in (r, g, b)]
        if tint:
            rgb = [c * (1 - amount) + t * amount for c, t in zip(rgb, tint, strict=True)]
        return "#" + "".join(f"{max(0, min(255, round(c * bright))):02x}" for c in rgb)

    return _HEX.sub(one, color)


def colorize(text: str, mask: str | None = None, default: str = "", *, when: str = "") -> Text:
    """Coloured ASCII; `when` (清晨 … 深夜) lights it for the time of day. After dark the
    light sources (GLOW keys) keep their colour, so lit windows and lamps stand out."""
    mask = mask if mask is not None else auto_mask(text)
    palette = PALETTE
    if when:
        palette = {k: v if when in DARK and k in GLOW else shade(v, when, strokes=True) for k, v in PALETTE.items()}
    if when and not default:
        default = shade(DEFAULT_INK, when, strokes=True)
    out = Text()
    mlines = mask.split("\n")
    for i, line in enumerate(text.split("\n")):
        m = mlines[i] if i < len(mlines) else ""
        run_style, run = None, ""
        for j, ch in enumerate(line):
            key = m[j] if j < len(m) else "."
            style = palette.get(key, default) if ch != " " else default
            if style != run_style and run:
                out.append(run, style=run_style or default)
                run = ""
            run_style = style
            run += ch
        if run:
            out.append(run, style=run_style or default)
        if i < len(text.split("\n")) - 1:
            out.append("\n")
    return out


@dataclass
class ArtIssue:
    name: str
    text: str

    def __str__(self) -> str:
        return f"{self.name}: {self.text}"


def check(name: str, text: str, mask: str | None = None, *, portrait: bool = False) -> list[ArtIssue]:
    issues = []
    lines = text.split("\n")
    max_w, max_h = MAX_PORTRAIT if portrait else MAX_SCENE
    bad = sorted({ch for ch in text if not (32 <= ord(ch) <= 126 or ch == "\n")})
    if bad:
        issues.append(ArtIssue(name, f"含非 ASCII 字符：{''.join(bad)[:10]}"))
    if max(map(len, lines)) > max_w:
        issues.append(ArtIssue(name, f"宽 {max(map(len, lines))} 列，超过 {max_w}"))
    if len(lines) > max_h:
        issues.append(ArtIssue(name, f"高 {len(lines)} 行，超过 {max_h}"))
    if mask is not None:
        mlines = mask.split("\n")
        if len(mlines) != len(lines):
            issues.append(ArtIssue(name, f"配色蒙版 {len(mlines)} 行，图是 {len(lines)} 行"))
        unknown = sorted({ch for ch in mask if ch not in PALETTE and ch not in ". \n"})
        if unknown:
            issues.append(ArtIssue(name, f"配色蒙版里有未知颜色键：{''.join(unknown)}"))
    return issues


def fit_mask(text: str, mask: str) -> str:
    """Pad or trim each mask line to the art's shape."""
    lines, mlines = text.split("\n"), mask.split("\n")
    out = []
    for i, line in enumerate(lines):
        m = mlines[i] if i < len(mlines) else ""
        m = (m + "." * len(line))[: len(line)]
        out.append("".join(c if c in PALETTE else "." for c in m))
    return "\n".join(out)
