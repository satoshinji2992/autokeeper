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
# A speaking NPC's mood (mood.py) on their portrait, the same six numbers as LIGHTS: cold and
# dark for fear, a red cast for anger, drained and dim for sorrow, warm for a friendly word,
# washed out for a fluster, closing in for a wary look. Calm is the portrait as it is.
TONES: dict[str, tuple[float, float, float, tuple[int, int, int] | None, float, float]] = {
    "警惕": (0.86, 0.75, 1.25, (150, 200, 170), 0.25, 0.55),
    "害怕": (0.72, 0.45, 1.15, (70, 110, 230), 0.45, 0.60),
    "发怒": (0.98, 1.30, 1.25, (255, 50, 30), 0.45, 0.30),
    "难过": (0.76, 0.20, 0.90, (150, 165, 200), 0.25, 0.35),
    "友善": (1.08, 1.12, 1.00, (255, 190, 120), 0.35, 0.10),
    "慌乱": (1.12, 0.60, 0.85, (255, 245, 170), 0.30, 0.00),
}
_HEX = re.compile(r"#([0-9a-fA-F]{6})")
DEFAULT_INK = "#c8c8c8"  # unmasked strokes, so they take the light too
GLOW = set("oyYW")  # lamplight, candles, lit windows: they stay bright after dark
DARK = {"傍晚", "夜里", "深夜"}

# Graded copies of a scene for the weather layer (atmosphere.py): exposure multipliers for
# torchlight and flicker, a fog veil, a lightning flash. Shared by pictures and ASCII.
GRADES: dict[str, float] = {"dim": 0.24, "half": 0.55, "glow": 1.18, "fade": 0.8}
HAZE: dict[str, float] = {"haze": 0.24, "haze2": 0.44}  # thin and thick fog
FLASH = (228, 234, 255)


def fog_colour(when: str) -> tuple[int, int, int]:
    return (92, 104, 128) if when in DARK else (198, 204, 210)


def _toned(rgb: list[float], bright: float, sat: float, tint: tuple[int, int, int] | None, amount: float) -> list[float]:
    """Saturation, then a tint, then exposure: one colour under LIGHTS or TONES."""
    grey = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    rgb = [grey + (c - grey) * sat for c in rgb]
    if tint:
        rgb = [c * (1 - amount) + t * amount for c, t in zip(rgb, tint, strict=True)]
    return [c * bright for c in rgb]


def regrade(color: str, grade: str, when: str = "") -> str:
    """One colour in a graded copy of the scene (see GRADES; also "haze" and "flash"), or of
    a hand-drawn portrait in a mood (TONES; lines on a dark terminal, so the exposure goes
    only half as far, as in shade)."""
    if not grade:
        return color

    def one(m: re.Match) -> str:
        rgb = [int(m.group(1)[i : i + 2], 16) for i in (0, 2, 4)]
        if grade in TONES:
            bright, sat, _, tint, amount, _ = TONES[grade]
            rgb = _toned(rgb, 1 - (1 - bright) * 0.5, sat, tint, amount)
        elif grade in GRADES:
            rgb = [c * GRADES[grade] for c in rgb]
        elif grade in HAZE:
            a = HAZE[grade]
            rgb = [c * (1 - a) + f * a for c, f in zip(rgb, fog_colour(when), strict=True)]
        elif grade == "flash":
            rgb = [min(255, c * 1.6) * 0.45 + f * 0.55 for c, f in zip(rgb, FLASH, strict=True)]
        elif grade == "red":  # everything under a darkroom safelight
            lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
            rgb = [48 + lum, lum * 0.2, lum * 0.12]
        return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)

    return _HEX.sub(one, color)


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
        rgb = _toned([int(m.group(1)[i : i + 2], 16) for i in (0, 2, 4)], bright, sat, tint, amount)
        return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)

    return _HEX.sub(one, color)


def colorize(text: str, mask: str | None = None, default: str = "", *, when: str = "", grade: str = "") -> Text:
    """Coloured ASCII; `when` (清晨 … 深夜) lights it for the time of day. After dark the
    light sources (GLOW keys) keep their colour, so lit windows and lamps stand out.
    `grade` makes one of the weather layer's copies (GRADES, "haze", "flash")."""
    mask = mask if mask is not None else auto_mask(text)
    palette = PALETTE
    if when:
        palette = {k: v if when in DARK and k in GLOW else shade(v, when, strokes=True) for k, v in PALETTE.items()}
    if when and not default:
        default = shade(DEFAULT_INK, when, strokes=True)
    if grade:
        palette = {k: regrade(v, grade, when) for k, v in palette.items()}
        default = regrade(default or DEFAULT_INK, grade, when)
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
