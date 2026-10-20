"""A waiting line that moves: the words, dots that come and go, and the seconds so far.

A still 「正在……」 reads as a hang (the user, 2026-10-21, on 开场准备中). It stops by itself once
anything else is written where it stands, so whoever puts the next words there need not know it."""

from __future__ import annotations

import time

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static


class Waiting:
    def __init__(self, widget: Static, words: str, *, style: str = "bold", after: str = ""):
        self.widget, self.words, self.style, self.after = widget, words.rstrip("…. "), style, after
        self.start, self.beat, self.shown = time.monotonic(), 0, None
        self.timer: Timer | None = widget.set_interval(0.4, self._tick)
        self._tick()

    def _tick(self) -> None:
        if self.shown is not None and self.widget.content is not self.shown:  # written over: done
            self.stop()
            return
        self.beat += 1
        seconds = int(time.monotonic() - self.start)
        line = Text(f"{self.words}{'.' * (self.beat % 3 + 1):<3}", style=self.style)
        if seconds >= 3:
            line.append(f"  {seconds} 秒", style="dim")
        if self.after:
            line.append(self.after, style="dim")
        self.shown = line
        self.widget.update(line)

    def stop(self) -> None:
        if self.timer is not None:
            self.timer.stop()
            self.timer = None


def wait_on(widget: Static, words: str, **kw) -> Waiting:
    """`words` waiting on `widget`, in place of any waiting there before."""
    old = getattr(widget, "_waiting", None)
    if old is not None:
        old.stop()
    widget._waiting = Waiting(widget, words, **kw)  # type: ignore[attr-defined]
    return widget._waiting  # type: ignore[attr-defined]
