"""开局演出 on screen (module/prelude.py): a few pictures with their captions, played over the game
screen while the director adapts the module to the investigators (Game.prepare_opening). Each
picture drifts slowly while its caption writes itself out. It ends once the last shot has been
read and the opening is ready, and the keeper begins at once. A key moves on to the next shot;
Esc skips the rest, and if the opening is not ready yet it says so and waits."""

from __future__ import annotations

import asyncio
import itertools
import subprocess
import time

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ... import artkit, imagery
from ...module import prelude
from ...module.schema import Module, PreludeFrame

HOLD = 5.0  # seconds a shot stays once its caption is written out (the LAN host waits as long: duration)
TYPE = 0.07  # seconds a character of a caption takes to appear
FADE = 0.8  # seconds a picture takes to come up, with animations on
DRIFT = 1.25  # a picture is drawn this much taller than the screen, and drifts down its length


def duration(shots: list[PreludeFrame]) -> float:
    """Seconds the shots take on screen when nobody skips."""
    return sum(len(shot.text.strip()) * TYPE + HOLD for shot in shots)


class PreludeScreen(ModalScreen[None]):
    CSS_PATH = "prelude.tcss"

    def __init__(self, m: Module, ready: asyncio.Future, *, style: str = "photo", animations: bool = True,
                 shots: list[PreludeFrame] | None = None):
        super().__init__()
        self.m, self.ready, self.style_name, self.animations = m, ready, style, animations
        self.shots = prelude.frames(m) if shots is None else shots  # a LAN seat's come from the host
        self._next = asyncio.Event()
        self._skip = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="prelude-view", can_focus=False):
            yield Static("", id="prelude-picture")
        yield Static("", id="prelude-caption")
        yield Static("", id="prelude-status")

    def on_mount(self) -> None:
        self.run_worker(self._play(), exclusive=True)

    async def _play(self) -> None:
        for shot in self.shots:
            if self._skip:
                break
            await self._show(shot)
        if not self.ready.done():
            await self._waiting()
        self.dismiss()

    async def _waiting(self) -> None:
        """The opening still being written after the shots: dots that move and the seconds so far,
        so a long wait does not look like a hang (the user, 2026-10-21)."""
        status = self.query_one("#prelude-status", Static)
        start = time.monotonic()
        for n in itertools.count():
            seconds = int(time.monotonic() - start)
            status.update(Text(f"开场准备中{'.' * (n % 3 + 1):<3}" + (f"  {seconds} 秒" if seconds >= 3 else ""), style="dim"))
            done, _ = await asyncio.wait({self.ready}, timeout=0.5)
            if done:
                return

    async def _show(self, shot: PreludeFrame) -> None:
        self._next.clear()
        view = self.query_one("#prelude-view", VerticalScroll)
        picture = self.query_one("#prelude-picture", Static)
        caption = self.query_one("#prelude-caption", Static)
        cols, rows = max(1, view.size.width), max(1, view.size.height)
        art = await self._picture(shot.art, cols, round(rows * DRIFT) if self.animations else rows)
        if self.animations:
            picture.styles.opacity = 0.0
        picture.update(art)
        view.scroll_to(y=0, animate=False, force=True)  # no scrollbar: moved by the drift alone
        if self.animations:
            picture.styles.animate("opacity", 1.0, duration=FADE)
        words = shot.text.strip()
        if self.animations:  # the picture drifts down its length while the caption is read (once it is laid out)
            drift = len(words) * TYPE + HOLD
            view.call_after_refresh(lambda: view.scroll_to(y=view.max_scroll_y, duration=drift, easing="linear", force=True))
            for n in range(1, len(words) + 1):
                if self._next.is_set():
                    break
                caption.update(Text(words[:n]))
                try:
                    await asyncio.wait_for(self._next.wait(), TYPE)
                except TimeoutError:
                    pass
            if not self._skip:
                self._next.clear()  # a key while it was writing only finishes the line
        caption.update(Text(words))
        if self._skip:
            return
        try:
            await asyncio.wait_for(self._next.wait(), HOLD)
        except TimeoutError:
            pass

    async def _picture(self, key: str, cols: int, rows: int) -> Text:
        """The shot's picture as text: the photo through chafa, else the module's drawing."""
        if key and key in self.m.pictures and imagery.available():
            try:
                ansi = await asyncio.to_thread(imagery.render, self.m.pictures[key], cols, rows, self.style_name, "", True)
                return Text.from_ansi(ansi)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass  # an unreadable picture: its drawing, or the caption alone
        if key and key in self.m.art:
            return artkit.colorize(self.m.art[key], self.m.art_colors.get(key))
        return Text("")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        if event.key == "escape":
            self._skip = True
        self._next.set()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self._next.set()
