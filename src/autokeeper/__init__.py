"""AutoKeeper: an automated Call of Cthulhu 7th edition keeper for Chinese tabletop play."""

import sys

__version__ = "0.0.3.2"


def utf8_stdio() -> None:
    """Pipes and redirects on Windows default to the ANSI code page (cp1252, cp936),
    which cannot print every character the game uses. Switch them to UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


# Terminals that show 24-bit colour but do not always say so: inside WSL, Windows Terminal
# passes WT_SESSION and no COLORTERM, and Textual then squeezes photos into 256 colours,
# where blue-greys turn into the theme's navy and teal.
TRUECOLOR_PROGRAMS = {"vscode", "iterm.app", "wezterm", "hyper", "tabby", "ghostty", "warpterminal"}
TRUECOLOR_TERMS = ("kitty", "alacritty", "foot", "wezterm", "ghostty", "truecolor", "24bit", "direct")


def truecolor() -> bool:
    """Say COLORTERM=truecolor for a terminal known to show it; AUTOKEEPER_TRUECOLOR=1/0 decides
    outright. True when the game may draw in 24-bit colour."""
    import os

    forced = os.environ.get("AUTOKEEPER_TRUECOLOR", "")
    if forced in ("0", "1"):
        if forced == "1":
            os.environ["COLORTERM"] = "truecolor"
        return forced == "1"
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    term, program = os.environ.get("TERM", "").lower(), os.environ.get("TERM_PROGRAM", "").lower()
    vte = os.environ.get("VTE_VERSION", "0")
    known = (
        bool(os.environ.get("WT_SESSION") or os.environ.get("KONSOLE_VERSION"))
        or program in TRUECOLOR_PROGRAMS
        or any(t in term for t in TRUECOLOR_TERMS)
        or (vte.isdigit() and int(vte) >= 3600)
    )
    if known:
        os.environ["COLORTERM"] = "truecolor"
    return known


def main() -> None:
    utf8_stdio()
    truecolor()
    from .cli import app

    app()
