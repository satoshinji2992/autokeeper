"""The player's own notes (笔记, F2 and 「/记 内容」): what to do with a line, and where they are kept.

The game writes nothing into them: what was found reaches the players in the story, and what they
make of it is theirs to write down (the user, 2026-10-22). A local game keeps them in its save, one
page per investigator (GameState.notebook). At a LAN table each player's notes stay on their own
machine: a file per game and seat (paths.table_notes), never sent to the host or to the other
players, and there again when they sit down at that seat once more. A browser's seat runs on the
host's machine (net/web.py): its notes live only as long as its page, and nothing is written there."""

from __future__ import annotations

from pathlib import Path

JOT = "/记"  # 「/记 内容」 in the input box: the line goes at the end of the notes, and no turn is taken


def line(typed: str) -> str:
    """What 「/记」 was given, as one line of the notes: the words after it, without a colon in front."""
    return " ".join(typed.split()).lstrip("：:").strip()


def append(notes: str, line: str) -> str:
    """The notes with `line` as their last line."""
    kept = notes.rstrip("\n")
    return f"{kept}\n{line}" if kept.strip() else line


def head(notes: str) -> list[str]:
    """The notes as the side column shows their start: the lines with something on them."""
    return [x.strip() for x in notes.splitlines() if x.strip()]


def read(path: Path) -> str:
    """A seat's notes kept on this machine; "" when there are none yet."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write(path: Path, text: str) -> None:
    """A seat's notes on this machine, written whole (empty: the file goes). OSError when the disk
    refuses: the screen says so, the page still holds what was typed."""
    if not text.strip():
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".tmp")
    partial.write_text(text, encoding="utf-8")
    partial.replace(path)
