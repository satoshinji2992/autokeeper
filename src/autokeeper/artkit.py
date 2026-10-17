"""Coloured ASCII art.

An art piece is two files of the same shape:

    art/shuchang.txt     the characters (printable ASCII only)
    art/shuchang.color   one palette key per character; '.' or ' ' keeps the default colour

Palette keys are single letters, so the mask lines up with the art in any editor.
Pieces without a mask are coloured by character class.
"""

from __future__ import annotations

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


def colorize(text: str, mask: str | None = None, default: str = "") -> Text:
    mask = mask if mask is not None else auto_mask(text)
    out = Text()
    mlines = mask.split("\n")
    for i, line in enumerate(text.split("\n")):
        m = mlines[i] if i < len(mlines) else ""
        run_style, run = None, ""
        for j, ch in enumerate(line):
            key = m[j] if j < len(m) else "."
            style = PALETTE.get(key, default) if ch != " " else default
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
