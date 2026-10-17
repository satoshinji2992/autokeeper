"""AutoKeeper: an automated Call of Cthulhu 7th edition keeper for Chinese tabletop play."""

import sys

__version__ = "0.2.0"


def utf8_stdio() -> None:
    """Pipes and redirects on Windows default to the ANSI code page (cp1252, cp936),
    which cannot print every character the game uses. Switch them to UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    utf8_stdio()
    from .cli import app

    app()
