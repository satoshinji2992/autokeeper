"""大场景 on screen: one big picture over everything, its title on a thin strip below
(engine/showcase.py). Any key or click, or SHOWN seconds, and it is gone; the story goes on
streaming behind it, and a roll prompt pushes it aside (GameScreen.make_way)."""

from __future__ import annotations

import asyncio
import subprocess

from rich.text import Text
from textual import events
from textual.app import ComposeResult
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

    def __init__(self, picture: str, caption: str, *, style: str = "photo", fade: bool = True):
        super().__init__()
        self.picture, self.caption, self.style_name, self.fade = picture, caption, style, fade
        self._gone = False

    def compose(self) -> ComposeResult:
        yield Static("", id="showcase-picture")
        yield Static(Text(self.caption), id="showcase-title")

    def on_mount(self) -> None:
        self.set_timer(SHOWN, self.leave)
        self.run_worker(self._draw(), exclusive=True)

    async def _draw(self) -> None:
        cols, rows = max(1, self.size.width), max(1, self.size.height - 1)
        try:
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

    def leave(self) -> None:
        """Gone, if it is still the screen in front (the timer may outlive it), and only once: a
        key pressed just as the timer ran dismissed it twice, and the second set the result of a
        future already done (InvalidStateError, the user's Windows run, 2026-10-21). Not awaited:
        its own timer and handlers must not wait for their screen to go."""
        if self.is_current and not self._gone:
            self._gone = True
            self.dismiss()
