"""大场景 on screen: one big picture over everything, its title on a thin strip below
(engine/showcase.py). Any key or click, or SHOWN seconds, and it is gone; the story goes on
streaming behind it, and a roll prompt pushes it aside (GameScreen.make_way)."""

from __future__ import annotations

import asyncio
import subprocess

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.await_complete import AwaitComplete
from textual.screen import ModalScreen
from textual.widgets import Static

from ... import imagery

SHOWN = 8.0  # seconds before it goes by itself
FADE = 0.6  # seconds it takes to come up, with animations on


class ShowcaseScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    ShowcaseScreen { background: black; }
    ShowcaseScreen #showcase-picture { width: 100%; height: 1fr; content-align: center middle; background: black; }
    ShowcaseScreen #showcase-title {
        width: 100%; height: 1; content-align: center middle; background: $panel; color: $accent; text-style: bold;
    }
    """

    def __init__(self, picture: str, caption: str, *, style: str = "photo", fade: bool = True, face: bool = False,
                 seconds: float | None = None):
        super().__init__()
        self.picture, self.caption, self.style_name, self.fade = picture, caption, style, fade
        self.face = face  # a portrait (a creature first seen): whole and centred, not cropped to the screen
        self.seconds = seconds
        self._gone = False
        self._due = False  # its time ran out under another window: it goes once it is in front again

    def compose(self) -> ComposeResult:
        yield Static("", id="showcase-picture")
        yield Static(Text(self.caption), id="showcase-title")

    def on_mount(self) -> None:
        self.set_timer(self.seconds or SHOWN, self._time_up)
        self.run_worker(self._draw(), exclusive=True)

    def _time_up(self) -> None:
        self.leave()  # not returned: a timer awaits what its callback returns, and the screen cannot await its own going

    async def _draw(self) -> None:
        cols, rows = max(1, self.size.width), max(1, self.size.height - 1)
        try:
            if self.face:
                art = await asyncio.to_thread(imagery.render, self.picture, cols, rows, self.style_name, "", False, "", True)
            else:
                art = await asyncio.to_thread(imagery.render, self.picture, cols, rows, self.style_name, "", True)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.leave()  # an unreadable picture: no big scene, not a broken screen
            return
        found = self.query("#showcase-picture").results(Static)
        shown = next(found, None)
        if shown is None:  # dismissed while it was being drawn
            return
        if self.fade:
            shown.styles.opacity = 0.0
        shown.update(Text.from_ansi(art))
        if self.fade:
            shown.styles.animate("opacity", 1.0, duration=FADE)

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.leave()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.leave()

    def leave(self) -> AwaitComplete | None:
        """Gone, and only once: a key pressed just as the timer ran dismissed it twice, and the second
        set the result of a future already done (InvalidStateError, the user's Windows run, 2026-10-21).
        Only from the front: `is_current` holds under another window too, and `dismiss()` pops
        whatever is in front, so a timer that ran out under a handout closed the handout and left
        the picture up for good. Under another window it goes once it is in front again. Its own
        timer and handlers do not await the dismissal (GameScreen.make_way does)."""
        if self._gone:
            return None
        if not self.is_active:
            self._due = True
            return None
        self._gone = True
        return self.dismiss()

    def on_screen_resume(self) -> None:
        if self._due:
            self.leave()
