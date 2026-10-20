"""AutoKeeper terminal UI (Textual). Also runs in a browser via textual-serve."""

from __future__ import annotations

import asyncio
import gc
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from textual.app import App
from textual.binding import Binding
from textual.command import CommandPalette, DiscoveryHit, Hit, Hits, Provider
from textual.widgets import Button, OptionList

from .. import config, imagery
from . import keys
from .library import THEMES
from .screens.game import COMMANDS, GameScreen
from .screens.title import TitleScreen

keys.install()


class Menu(Provider):
    """The Ctrl+P menu, in the game's own words and in the order of COMMANDS (Textual's own
    English entries, sorted by name, are left out)."""

    def _entries(self):
        app = self.app
        if isinstance(self.screen, GameScreen):
            for title, key, what, action in COMMANDS:
                yield f"{title}  {key}".rstrip(), what, partial(self.screen.run_action, action)
        elif isinstance(self.screen, TitleScreen):
            yield "设置  F9", "AI 模型、叙事偏好、画面和动画", self.screen.action_settings
        yield "换配色  Ctrl+T", "琥珀、磷绿、薰衣草、血红轮换", app.action_next_theme  # type: ignore[attr-defined]
        yield "离开  Ctrl+Q", "关掉 AutoKeeper", app.action_quit

    async def discover(self) -> Hits:
        for name, what, run in self._entries():
            yield DiscoveryHit(name, run, help=what)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, what, run in self._entries():
            score = max(matcher.match(name), matcher.match(what) * 0.5)
            if score > 0:
                yield Hit(score, matcher.highlight(name), run, help=what)


class AutoKeeperApp(App):
    CSS_PATH = "app.tcss"
    COMMANDS = {Menu}
    TITLE = "AutoKeeper"
    BINDINGS = [
        Binding("ctrl+q", "quit", "离开"),
        Binding("ctrl+t", "next_theme", "换配色", show=False),
        Binding("ctrl+p", "command_palette", "全部功能", show=False, priority=True),
        Binding("space", "confirm", "确认", show=False),
    ]

    def action_command_palette(self) -> None:
        if not CommandPalette.is_open(self):
            self.push_screen(CommandPalette(placeholder="搜索功能：地图、存档、画面风格", id="--command-palette"))


    def __init__(self, theme: str | None = None, join: tuple[str, str] | None = None):
        super().__init__()
        # Each frame (weather, a narration's tick, the title's sea) makes thousands of short-lived
        # objects; at Python's default thresholds (700, 10, 10) they kept reaching the old
        # generation, and a game stopped for a 150-250 ms full collection every second or two.
        # A larger young generation lets them die young: a full collection every few minutes.
        gc.set_threshold(10_000, 20, 20)
        self._theme_name = theme
        self.art_style = config.load().art_style
        self.dice_skin = config.load().dice
        self.join = join  # (host address, player name): straight to someone's table
        self.room = None  # the LAN room opened from the title screen (net/room.py), closed with the app

    def on_mount(self) -> None:
        if sys.platform == "win32":
            # On Windows Textual's timers sleep in the default thread pool, a thread each: a game
            # screen's timers could take every thread of it (cpu + 4), and the moods, portraits
            # and pictures sent to a thread waited behind them.
            asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=64))
        for th in THEMES:
            self.register_theme(th)
        self.theme = self._theme_name or config.load().theme
        if not imagery.available():  # pictures fall back to the hand-drawn art without a word otherwise
            self.notify(f"未检测到 chafa，照片暂以手绘字符画替代。安装命令：{imagery.install_hint()}。安装后请重启程序。",
                        title="缺少 chafa", severity="warning", timeout=15)
        if self.join:
            from .screens.lobby import LobbyScreen
            from .screens.net import JoinScreen

            address, name = self.join
            self.push_screen(LobbyScreen(address, name) if address and name else JoinScreen(address, name))
        else:
            self.push_screen(TitleScreen())

    async def on_unmount(self) -> None:
        if self.room is not None:  # quitting closes the room: the page first, then the Hub
            room, self.room = self.room, None
            await asyncio.to_thread(room.close)

    def notify(self, message, *, title: str = "", severity="information", timeout: float | None = None, markup: bool = True) -> None:
        """The same notice twice while the first still shows (a key pressed again) is dropped:
        stacked toasts only crowd the screen, and some terminals garble wide characters there."""
        now, key = time.monotonic(), (str(message), title)
        shown = getattr(self, "_notices", {})
        if now - shown.get(key, -1e9) < (timeout or 5):
            return
        self._notices = {k: v for k, v in shown.items() if now - v < 30} | {key: now}
        super().notify(message, title=title, severity=severity, timeout=timeout, markup=markup)

    def action_confirm(self) -> None:
        """Space confirms like Enter in lists and on buttons. Inputs keep their spaces: they
        take the key before it ever reaches a binding."""
        focused = self.focused
        if isinstance(focused, OptionList):
            focused.action_select()
        elif isinstance(focused, Button):
            focused.press()

    def action_next_theme(self) -> None:
        names = [th.name for th in THEMES]
        self.theme = names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else names[0]
        self.notify(f"配色：{self.theme}")
