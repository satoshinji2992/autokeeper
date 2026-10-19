"""The title screen: the cover, the menu, and the way into a game, a room or the cards."""

from __future__ import annotations

import random

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.strip import Strip
from textual.timer import Timer
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ... import imagery
from ..drawing import art, cover
from ..modals.guide import GuideScreen
from .saves import LoadScreen
from .setup import SetupScreen


class TitleScreen(Screen):
    """Cthulhu behind everything (cover.py), the name over the sky, a see-through menu in the
    middle. The menu is painted into the cover so the painting shows through it; the real
    OptionList sits underneath, out of sight, and takes the keys, and clicks are passed to it."""

    BINDINGS = [("f9", "settings", "设置")]
    MENU = 36

    def __init__(self) -> None:
        super().__init__()
        self.tick = 0
        self.flash = 0
        self.frames = 0  # how many of the loop's frames are drawn for the current size
        self.size_key: tuple[int, int, float, str] | None = None
        self.ticker: Timer | None = None
        # Rows ready to show, for this size and theme (the loop shows each frame again and
        # again): the painting with the name, the tagline and the hint over it, a frame each;
        # and the menu's rows over each frame, with the bar where it is.
        self.painted: dict[int, list[Strip]] = {}
        self.menus: dict[tuple[int, int | None], list[Strip]] = {}
        self.painted_for: tuple | None = None
        self.shown: tuple | None = None  # (frame, bar) on screen
        self.area: tuple[int, int] | None = None  # the screen's size, once known

    def compose(self) -> ComposeResult:
        yield OptionList(
            Option("新的调查", id="new"),
            Option("继续调查", id="continue"),
            Option("开房间", id="host"),
            Option("加入房间", id="join"),
            Option("观战（AI 调查员）", id="watch"),
            Option("角色卡", id="cards"),
            Option("导入模组", id="import"),
            Option("设置", id="settings"),
            Option("玩法说明", id="guide"),
            Option("离开", id="quit"),
            id="menu",
        )
        yield cover.Cover(id="title-cover")

    def on_mount(self) -> None:
        self.id = "title-screen"
        self.ticker = self.set_interval(0.12, self._tick)
        self.query_one("#menu", OptionList).focus()

    # Under another screen (the setup, a whole game) the sea stands still: repainted behind a
    # game eight times a second, the title took a third of a core and fed long GC pauses. Its
    # frames go too, till it is back: every full GC of the game would walk their cells.
    def on_screen_suspend(self) -> None:
        if self.ticker is not None:
            self.ticker.pause()
        self.painted, self.menus, self.painted_for, self.shown = {}, {}, None, None
        self.size_key, self.frames = None, 0
        cover.clear()

    def on_screen_resume(self) -> None:
        room = getattr(self.app, "room", None)  # a LAN room this app opened (tui/screens/host.py)
        label = "回到房间" if room is not None and room.is_open else "开房间"
        self.query_one("#menu", OptionList).replace_option_prompt("host", label)
        if self.ticker is not None:
            self.ticker.resume()
        if self.area is not None:  # the frames again, in the art style chosen meanwhile (F9)
            self._place(*self.area)

    def on_resize(self, event) -> None:
        self.area = (event.size.width, event.size.height)
        self._place(*self.area)

    def _stack(self, cols: int, rows: int) -> tuple[int, int, int]:
        """Rows of the name, the tagline and the menu: one block standing on the hint line, as
        on a poster, so his head can loom in the room above the name."""
        logo = len(art.logo_lines(cols, rows))
        menu_y = max(0, rows - self.query_one("#menu", OptionList).option_count - 4)
        top = max(0, menu_y - 3 - logo)
        return top, top + logo + 1, max(menu_y, top + logo + 3)

    def _menu_box(self, cols: int, rows: int) -> tuple[int, int, int, int]:
        """(x, y, width, height) of the menu, in the middle across, under the tagline."""
        h, w = self.query_one("#menu", OptionList).option_count + 2, min(self.MENU, cols)
        return (cols - w) // 2, self._stack(cols, rows)[2], w, h

    def _place(self, cols: int, rows: int) -> None:
        x, y, _, _ = self._menu_box(cols, rows)
        self.query_one("#menu", OptionList).styles.offset = (x, y)
        key = (cols, rows, cover.focus(cols, rows, self._stack(cols, rows)[0] / 2), getattr(self.app, "art_style", "photo"))
        if key != self.size_key:
            self.size_key, self.frames = key, 0
            if imagery.available() and cover.PICTURE.is_file():
                self._draw(key)
        self._paint()

    @work(thread=True, exclusive=True, group="cover")
    def _draw(self, key: tuple[int, int, float, str]) -> None:
        """The loop's frames for this size, first frame first, so the screen fills at once."""
        for k in range(cover.FRAMES + 1):  # the last one is the lightning
            if key != self.size_key:
                return
            cover.frame(*key, k)
            self.frames = k + 1

    def _tick(self) -> None:
        self.tick += 1
        if self.frames > cover.FRAMES and not self.flash and random.random() < 0.012:
            self.flash = 3  # about every ten seconds: lightning, twice
        self._paint()
        self.flash = max(0, self.flash - 1)

    def _paint(self) -> None:
        """The frame for this tick, unless it is the one on screen: the picture changes every
        other tick, the name glitches now and then, the bar moves with the keys."""
        if self.size_key is None:
            return
        k = cover.FRAMES if self.flash in (3, 1) else (self.tick // 2) % cover.FRAMES
        picture = (k if self.frames > k else 0) if self.frames else -1  # -1: nothing drawn yet
        view = self.query_one(cover.Cover)
        if picture < 0 and imagery.available() and cover.PICTURE.is_file() and view.rows:
            return  # back from a game: the last frame stays till the first of these is in
        flicker = random.choice([0.0] * 14 + [0.02, 0.05, 0.12])
        menu = self.query_one("#menu", OptionList)
        if (self.size_key, self.app.theme) != self.painted_for:
            self.painted, self.menus, self.painted_for = {}, {}, (self.size_key, self.app.theme)
        shown = (picture, menu.highlighted)
        if not flicker and shown == self.shown:
            return
        cols, rows = self.size_key[:2]
        if picture not in self.painted:
            self.painted[picture] = cover.strips(self._words(self._cells(picture)))
        lines = list(self.painted[picture])
        x, y, w, h = self._menu_box(cols, rows)
        if shown not in self.menus:
            if len(self.menus) > 40:  # the bar has been everywhere: start again
                self.menus.clear()
            under = self._cells(picture)[y : y + h]
            labels = [str(menu.get_option_at_index(i).prompt) for i in range(menu.option_count)]
            theme = self.app.current_theme
            cover.menu(under, (x, 0, w, h), labels, menu.highlighted, theme.primary, theme.foreground or "#f3d7a6", theme.surface or "#15110a")
            self.menus[shown] = cover.strips(under)
        lines[y : y + len(self.menus[shown])] = self.menus[shown]
        if flicker:  # only the name's rows are drawn again
            top = self._stack(cols, rows)[0]
            logo = cover.cells(art.title(cols, flicker, height=rows))
            over = self._cells(picture)[top : top + len(logo)]
            cover.put(over, logo, (cols - max(map(len, logo), default=0)) // 2, 0, keep_ground=True)
            lines[top : top + len(over)] = cover.strips(over)
        view.show(lines)
        self.shown = None if flicker else shown

    def _cells(self, picture: int) -> cover.Cells:
        """Frame `picture` of the painting (-1: the eye, or nothing yet), cells to lay words on."""
        assert self.size_key is not None
        cols, rows, middle, _ = self.size_key
        if picture >= 0:
            grid = [row[:] for row in cover.frame(*self.size_key, picture)]
        elif not imagery.available():
            grid = cover.eye(cols, rows, middle)
        else:
            grid = []
        return grid[:rows] + cover.blank(cols, rows - len(grid))  # chafa may come a row short

    def _words(self, grid: cover.Cells) -> cover.Cells:
        """The name, the tagline and the hint over the painting (the menu goes on by itself)."""
        assert self.size_key is not None
        cols, rows = self.size_key[:2]
        theme = self.app.current_theme
        top, tag, _ = self._stack(cols, rows)
        logo = cover.cells(art.title(cols, 0.0, height=rows))
        cover.put(grid, logo, (cols - max(map(len, logo), default=0)) // 2, top, keep_ground=True)
        words = cover.cells(Text(f" {art.TAGLINE} ", style=f"bold {theme.secondary or '#d98e04'}"))
        cover.put(grid, words, (cols - len(words[0])) // 2, tag, under=cover.PLAQUE)
        hint = cover.cells(Text(" ↑↓ 选择 · Enter 确认 · F9 设置 · Ctrl+Q 离开 ", style="#8a7a66"))
        cover.put(grid, hint, (cols - len(hint[0])) // 2, rows - 1, under=cover.PLAQUE)
        return grid

    def _option_at(self, x: int, y: int) -> int | None:
        if self.size_key is None:
            return None
        left, top, w, h = self._menu_box(*self.size_key[:2])
        return y - top - 1 if left < x < left + w - 1 and top < y < top + h - 1 else None

    def on_mouse_move(self, event: events.MouseMove) -> None:
        at = self._option_at(event.screen_x, event.screen_y)
        if at is not None and at != self.query_one("#menu", OptionList).highlighted:
            self.query_one("#menu", OptionList).highlighted = at
            self._paint()

    def on_click(self, event: events.Click) -> None:
        at = self._option_at(event.screen_x, event.screen_y)
        if at is not None:
            menu = self.query_one("#menu", OptionList)
            menu.highlighted = at
            menu.action_select()

    @on(OptionList.OptionHighlighted, "#menu")
    def _moved(self) -> None:
        self._paint()  # the painted menu follows the keys at once, not at the next tick

    @on(OptionList.OptionSelected, "#menu")
    def choose(self, event: OptionList.OptionSelected) -> None:
        choice = event.option.id
        if choice == "new":
            self.app.push_screen(SetupScreen(spectate=False))
        elif choice == "watch":
            self.app.push_screen(SetupScreen(spectate=True))
        elif choice == "continue":
            self.app.push_screen(LoadScreen())
        elif choice == "host":
            from .host import HostScreen, back_to_room

            self.app.push_screen(back_to_room(self.app) or HostScreen())
        elif choice == "join":
            from .net import JoinScreen

            self.app.push_screen(JoinScreen())
        elif choice == "cards":
            from .cards import CardsScreen

            self.app.push_screen(CardsScreen())
        elif choice == "import":
            from .importing import ImportScreen

            self.app.push_screen(ImportScreen())
        elif choice == "settings":
            self.action_settings()
        elif choice == "guide":
            self.app.push_screen(GuideScreen())
        elif choice == "quit":
            self.app.exit()

    def action_settings(self) -> None:
        from ..modals.settings import SettingsModal

        self.app.push_screen(SettingsModal())




# -- setup ---------------------------------------------------------------------------------
