"""Built-in ASCII art: title, dice faces, frames and glitch noise."""

from __future__ import annotations

import random
from functools import lru_cache

import pyfiglet
from rich.text import Text

EYE = r"""
            .-~~~~~~~~-.
        .-~   .-~~~~-.   ~-.
      .'    .'  .--.  '.    '.
     /     /   ( () )   \     \
     \     \    '--'    /     /
      '.    '-.______.-'    .'
        '-.              .-'
           '~~--....--~~'
     ~\  ~\  ~\  ||  /~  /~  /~
       \   \   \ || /   /   /
""".strip("\n")

TAGLINE = "自 动 守 秘 人  ·  克苏鲁的呼唤 7 版中文跑团"

NOISE = "▓▒░█▚▞▙▟◢◣◤◥#%&@$*+=~:;!?<>/\\|"
DIGITS = "0123456789"


@lru_cache(maxsize=8)
def title_lines(width: int) -> tuple[str, ...]:
    font = "bloody" if width >= 90 else "small"
    text = pyfiglet.figlet_format("AUTOKEEPER", font=font, width=400).rstrip("\n")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    return tuple(lines)


def title(width: int, flicker: float = 0.0, palette: tuple[str, ...] = ("#ff2a2a", "#d11a1a", "#a10f0f", "#6e0909")) -> Text:
    """The big title with a vertical colour gradient; flicker > 0 corrupts a few cells."""
    out = Text()
    lines = title_lines(width)
    for i, line in enumerate(lines):
        color = palette[min(len(palette) - 1, i * len(palette) // max(1, len(lines)))]
        chars = []
        for ch in line:
            if flicker and ch != " " and random.random() < flicker:
                chars.append(random.choice(NOISE))
            else:
                chars.append(ch)
        out.append("".join(chars) + "\n", style=f"bold {color}")
    return out


@lru_cache(maxsize=16)
def big_digit(d: str) -> tuple[str, ...]:
    return tuple(pyfiglet.figlet_format(d, font="small").rstrip("\n").splitlines()[:4])


def die_face(value: str) -> list[str]:
    """A die drawn as an 8-column box around a big digit."""
    glyph = [line[:6].ljust(6) for line in big_digit(value)]
    while len(glyph) < 4:
        glyph.append(" " * 6)
    return [".------.", *[f"|{g}|" for g in glyph], "'------'"]


def corrupt(text: str, rate: float) -> str:
    """Replace a fraction of visible characters with noise (used for SAN-loss glitches)."""
    if rate <= 0:
        return text
    return "".join(random.choice(NOISE) if ch not in " \n" and random.random() < rate else ch for ch in text)


def bar(value: int, maximum: int, width: int = 12) -> str:
    maximum = max(1, maximum)
    filled = round(width * max(0, min(value, maximum)) / maximum)
    return "█" * filled + "░" * (width - filled)


def frame(text: str, title: str = "", width: int = 56) -> str:
    """A torn-paper frame for handouts."""
    inner = width - 4
    lines: list[str] = []
    for para in text.rstrip().split("\n"):
        if not para.strip():
            lines.append("")
            continue
        while para:
            chunk, para = _cut(para, inner)
            lines.append(chunk)
    head = f" {title} " if title else ""
    pad = max(0, width - 2 - _w(head))
    top = " " + "_" * (pad // 2) + head + "_" * (pad - pad // 2)
    out = [top, "/" + " " * (width - 2) + "\\"]
    out += [f"| {line}{' ' * (inner - _w(line))} |" for line in lines]
    out += ["\\" + ("_/\\" * width)[: width - 2] + "/"]
    return "\n".join(out)


def _w(s: str) -> int:
    from wcwidth import wcswidth

    w = wcswidth(s)
    return w if w >= 0 else len(s)


def _cut(s: str, width: int) -> tuple[str, str]:
    from wcwidth import wcwidth

    used = 0
    for i, ch in enumerate(s):
        cw = max(0, wcwidth(ch))
        if used + cw > width:
            return s[:i], s[i:]
        used += cw
    return s, ""
