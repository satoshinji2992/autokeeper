"""AutoKeeper terminal UI (Textual). Also runs in a browser via textual-serve."""

from __future__ import annotations

import asyncio
import gc
import json
import os
import random
import subprocess
import time
from functools import partial
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import CommandPalette, DiscoveryHit, Hit, Hits, Provider
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.strip import Strip
from textual.theme import Theme
from textual.timer import Timer
from textual.widgets import Button, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from .. import artkit, config, imagery, roster, weather
from ..agents.cast import Cast
from ..engine import debrief
from ..engine import graph as turn_graph
from ..engine.game import PLAY_WAIT, RETELL, Game, Told
from ..engine.state import GameState, time_of_day
from ..harness import models as channels
from ..module.compiler import compile_module, skill_name
from ..module.schema import Module
from ..prefs import Prefs
from ..rules import commands
from ..rules.coc import Investigator, Rules
from . import art, cover, tips
from .debrief import DebriefModal
from .guide import GuideScreen
from .mapview import MapModal
from .retell import RetellModal
from .screens import EngineModal, HandoutModal, NotebookModal, PushModal, RollModal, SketchModal
from .speaker import Speaker
from .suggest import Ideas
from .widgets import (
    LIGHT_MARK,
    PORTRAIT,
    CluePanel,
    DiceTray,
    HistoryPanel,
    ItemCard,
    RollCard,
    SceneArt,
    SheetPanel,
    StoryLog,
    command_suggester,
    history_line,
    log_line,
)

THEMES = [
    Theme("amber", primary="#ffb000", secondary="#d98e04", accent="#ff6b35", foreground="#f3d7a6", background="#0c0a06",
          surface="#15110a", panel="#211a0f", error="#e5383b", success="#9bd770", warning="#ffd166", dark=True),
    Theme("phosphor", primary="#33ff66", secondary="#1fbf4c", accent="#b3ff66", foreground="#b8ffc8", background="#040a05",
          surface="#08140a", panel="#0d1f10", error="#ff5555", success="#33ff66", warning="#e6ff66", dark=True),
    Theme("lavender", primary="#9a7fc4", secondary="#c3b1e1", accent="#ff8fab", foreground="#ece6f6", background="#0f0c14",
          surface="#17121f", panel="#221a2e", error="#ff6b6b", success="#a0e8af", warning="#ffd6a5", dark=True),
    Theme("blood", primary="#c1121f", secondary="#e85d04", accent="#ffba08", foreground="#f1e3e3", background="#0b0505",
          surface="#160909", panel="#220d0d", error="#ff4d4d", success="#90be6d", warning="#ffba08", dark=True),
]


# -- library and saves ---------------------------------------------------------------
def modules() -> list[Path]:
    """The modules to play: the bundled ones (《追书人》), then the player's own. The format
    sample (config.EXAMPLE) is for the importer and the tests, not for playing."""
    found = []
    for base in (config.MODULES, config.LOCAL_MODULES):
        if base.is_dir():
            found += [p for p in sorted(base.iterdir()) if (p / "module.yaml").exists()]
    return found


def save_index() -> dict:
    f = config.SAVES / "index.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def write_save(state: GameState, module: Module) -> None:
    config.SAVES.mkdir(parents=True, exist_ok=True)
    target = config.SAVES / f"{state.slot}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(state.model_dump_json(indent=1), encoding="utf-8")
    temporary.replace(target)
    idx = save_index()
    idx[state.slot] = {
        "module": module.id,
        "title": module.title,
        "party": "、".join(i.name for i in state.investigators),
        "clock": state.clock,
        "location": module.location(state.location).name,
        "ending": state.ending,
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "path": str(_module_dir(module.id)),
    }
    (config.SAVES / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


def _module_dir(module_id: str) -> Path:
    for p in [*modules(), config.EXAMPLE]:  # the format sample is not offered, but its saves still load
        if p.name == module_id:
            return p
    raise FileNotFoundError(module_id)


def load_module(path: Path) -> Module:
    m = Module.load(path)
    if not (config.SKILLS / skill_name(m) / "SKILL.md").exists():
        compile_module(m, config.SKILLS, source=path / "source")
    return m


def art_text(m: Module, name: str | None, size: tuple[int, int] | None = None, style: str = "photo", *, face: bool = False) -> Text:
    """A picture (chafa) when the module has one and a size is given, else the hand-drawn art.
    `face`: a portrait, cropped round the face to fill `size`."""
    if name and size and name in m.pictures and imagery.available():
        try:
            return Text.from_ansi(imagery.render(m.pictures[name], *size, style, face=face))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass  # a missing or unreadable picture must not break selection or hide its ASCII fallback
    if not name or name not in m.art:
        return Text("")
    return artkit.colorize(m.art[name], m.art_colors.get(name))


# -- title -------------------------------------------------------------------------------
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
        self.room: tuple[int, int] | None = None  # the screen's size, once known

    def compose(self) -> ComposeResult:
        yield OptionList(
            Option("新的调查", id="new"),
            Option("继续调查", id="continue"),
            Option("观战（AI 调查员）", id="watch"),
            Option("加入房间", id="join"),
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
        if self.ticker is not None:
            self.ticker.resume()
        if self.room is not None:  # the frames again, in the art style chosen meanwhile (F9)
            self._place(*self.room)

    def on_resize(self, event) -> None:
        self.room = (event.size.width, event.size.height)
        self._place(*self.room)

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
        from .settings import SettingsModal

        self.app.push_screen(SettingsModal())




# -- setup ---------------------------------------------------------------------------------
class SetupScreen(Screen):
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, spectate: bool, module: Path | None = None):
        super().__init__()
        self.spectate = spectate  # AI players only; not `watch`, which is Textual's
        self.step = "module"
        self.module: Module | None = None
        self.lead: Investigator | None = None
        self.cards: list[Investigator] = []  # public-figure cards kept from before (roster.py)
        self.paths = modules()
        self.preselected = module  # just imported: straight on to its investigators

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(id="setup-title")
            with Horizontal(id="setup-body"):
                yield OptionList(id="choices")
                with VerticalScroll(id="preview"):
                    yield Static(id="preview-body")
            yield Static(id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._show_modules()
        if self.preselected in self.paths:
            ol = self.query_one("#choices", OptionList)
            ol.highlighted = self.paths.index(self.preselected)
            ol.action_select()

    def action_back(self) -> None:
        if self.step == "module":
            self.app.pop_screen()
        elif self.step == "investigator":
            self._show_modules()
        else:
            self._show_investigators()

    def _set(self, title: str, options: list[Option], hint: str) -> None:
        self.query_one("#setup-title", Static).update(Text(title, style="bold"))
        ol = self.query_one("#choices", OptionList)
        ol.clear_options()
        ol.add_options(options)
        ol.highlighted = 0
        ol.focus()
        self.query_one("#setup-hint", Static).update(hint)

    def _show_modules(self) -> None:
        self.step = "module"
        opts = []
        for p in self.paths:
            try:
                m = Module.load(p)
                opts.append(Option(f"《{m.title}》 {m.players}", id=str(p)))
            except Exception as exc:  # broken module files should not crash the menu
                opts.append(Option(f"{p.name}（无法读取：{exc.__class__.__name__}）", id=str(p), disabled=True))
        self._set("选择模组" + ("（AI 观战）" if self.spectate else ""), opts, "Enter 或空格选择 · Esc 返回")
        tips.first_game(self)  # no saves yet: 《追书人》 is the tutorial

    def _show_investigators(self) -> None:
        assert self.module
        self.step = "investigator"
        if self.spectate:
            opts = [Option(f"{n} 名 AI 调查员", id=f"party-{n}") for n in (1, 2, 3) if n <= max(1, len(self.module.pregens))]
            self._set(f"《{self.module.title}》· 观战人数", opts, "Enter 或空格开始 · Esc 返回")
            return
        self.cards = roster.saved()
        roster.attach(self.module, self.cards)
        opts = [Option(f"{p.name}  {p.occupation}", id=p.id) for p in self.module.pregens]
        opts += [Option(f"{c.name}  {c.occupation}（我的卡）", id=c.id) for c in self.cards]
        opts += [Option("＋ 创建调查员", id="card:new"), Option("＋ 导入已有的卡", id="card:import")]
        self._set(f"《{self.module.title}》· 选择你的调查员", opts, "Enter 或空格选择 · Esc 返回")

    def _investigator(self, inv_id: str) -> Investigator | None:
        assert self.module
        return next((p for p in [*self.module.pregens, *self.cards] if p.id == inv_id), None)

    def _carded(self, inv: Investigator | None) -> None:
        if inv is None:
            self._show_investigators()
            return
        self.lead = inv
        self._show_companions()

    def _show_companions(self) -> None:
        assert self.module and self.lead
        self.step = "companions"
        others = [p for p in self.module.pregens if p.id != self.lead.id]
        opts = [Option("独自调查", id="solo")]
        opts += [Option(f"带 {n} 名 AI 同伴", id=f"with-{n}") for n in (1, 2) if n <= len(others)]
        self._set("同伴", opts, "同伴由 AI 扮演，在你之后行动 · Esc 返回")

    @on(OptionList.OptionHighlighted, "#choices")
    def preview(self, event: OptionList.OptionHighlighted) -> None:
        body = self.query_one("#preview-body", Static)
        oid = event.option.id or ""
        if self.step == "module" and oid:
            m = Module.load(Path(oid))
            first = m.cover or next((loc.art for loc in m.locations if loc.art), None)
            t = Text()
            t.append(f"《{m.title}》", style="bold")
            t.append(f"  {m.subtitle}\n", style="dim")
            t.append(f"{m.era} · {m.setting} · {m.players} · {m.duration}\n\n", style="dim")
            header = t.copy()
            t.append_text(art_text(m, first))
            t.append("\n\n" + m.hook.strip() + "\n\n", style="italic")
            t.append(m.source, style="dim")
            body.update(t)
            if first in m.pictures:
                self._preview_original(m, first, header, oid)
        elif self.step == "investigator" and self.module and not self.spectate:
            if oid == "card:new":
                body.update(Text("说一位公众人物的名字，或者描述你想玩的人。AutoKeeper 会查资料、问你几个问题，再按这个模组的年代做成调查员。做好的卡会留着，下次开团还能选。", style="dim"))
            elif oid == "card:import":
                body.update(Text("带上你自己的卡：骰娘的 .st 指令、车卡器导出的表格、PDF 或 Word 人物卡、纸质卡的照片，或者酒馆（SillyTavern）的角色卡。数值照原样录入；只有人设的角色卡，会问你几个问题再车成调查员。", style="dim"))
            elif inv := self._investigator(oid):
                body.update(_investigator_card(self.module, inv, self.app.art_style))  # type: ignore[attr-defined]

    @work(exclusive=True, group="module-preview")
    async def _preview_original(self, m: Module, key: str, header: Text, oid: str) -> None:
        # As wide as the preview pane (a fixed 40 columns left a small blur in a wide pane), cut
        # to its shape, in the art style the player chose.
        width = max(32, self.query_one("#preview-body", Static).content_size.width - 2)
        size = (width, max(10, min(24, round(width * 0.38))))
        style = self.app.art_style  # type: ignore[attr-defined]
        def draw() -> Text:
            if imagery.available():
                try:
                    return Text.from_ansi(imagery.render(m.pictures[key], *size, style, fill=True))
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass  # an unreadable picture: its hand-drawn art instead
            return art_text(m, key)

        picture = await asyncio.to_thread(draw)
        choices = self.query_one("#choices", OptionList)
        if self.step != "module" or choices.highlighted is None or choices.get_option_at_index(choices.highlighted).id != oid:
            return
        header.append_text(picture)
        header.append("\n\n" + m.hook.strip() + "\n\n", style="italic")
        header.append(m.source, style="dim")
        self.query_one("#preview-body", Static).update(header)

    @on(OptionList.OptionSelected, "#choices")
    def select(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id or ""
        if self.step == "module":
            self.module = load_module(Path(oid))
            if not self.module.pregens:
                self.app.notify("这个模组没有预设调查员", severity="error")
                return
            self._show_investigators()
        elif self.step == "investigator":
            assert self.module
            if self.spectate:
                n = int(oid.split("-")[1])
                party = [p.model_copy(deep=True) for p in self.module.pregens[:n]]
                self._start(party, human=[])
            elif oid in ("card:new", "card:import"):
                from .card import CardScreen

                self.app.push_screen(CardScreen(self.module, start="figure" if oid == "card:new" else "import"), self._carded)
            else:
                self.lead = self._investigator(oid)
                self._show_companions()
        elif self.step == "companions":
            assert self.module and self.lead
            n = 0 if oid == "solo" else int(oid.split("-")[1])
            others = [p for p in self.module.pregens if p.id != self.lead.id][:n]
            party = [self.lead.model_copy(deep=True)] + [p.model_copy(deep=True) for p in others]
            self._start(party, human=[self.lead.id])

    def _start(self, party: list[Investigator], human: list[str]) -> None:
        assert self.module
        slot = f"{self.module.id}-{time.strftime('%m%d-%H%M%S')}"
        state = Game.new(self.module, party, slot=slot, human=human)
        self.app.switch_screen(GameScreen(self.module, state, spectate=self.spectate))


def _investigator_card(m: Module, inv: Investigator, style: str = "photo") -> Text:
    t = Text()
    t.append_text(art_text(m, inv.portrait, (32, 14) if inv.portrait in m.pictures else None, style, face=True))
    t.append(f"\n\n{inv.name}", style="bold")
    t.append(f"  {inv.occupation} · {inv.age}岁\n", style="dim")
    for k, v in inv.backstory.items():
        t.append(f"{k}：", style="bold")
        t.append(f"{v}\n")
    t.append("\n" + "  ".join(f"{k}{v}" for k, v in inv.characteristics.items()) + f"  幸运{inv.luck}\n", style="dim")
    t.append("  ".join(f"{k}{v}" for k, v in sorted(inv.skills.items(), key=lambda kv: -kv[1])))
    return t


class LoadScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(Text("继续调查", style="bold"), id="setup-title")
            yield OptionList(id="choices", classes="saves")
            yield Static("Enter 或空格读档 · Esc 返回", id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        ol = self.query_one("#choices", OptionList)
        idx = save_index()
        for slot, meta in sorted(idx.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True):
            label = f"《{meta['title']}》 {meta['party']} · {meta['clock']} · {meta['location']}"
            if meta.get("ending"):
                label += " · 已结局"
            ol.add_option(Option(f"{label}   [{meta['updated']}]", id=slot))
        if not idx:
            ol.add_option(Option("还没有存档", disabled=True))
        else:
            ol.highlighted = 0  # Enter loads the newest save straight away
        ol.focus()

    @on(OptionList.OptionSelected, "#choices")
    def load(self, event: OptionList.OptionSelected) -> None:
        slot = event.option.id or ""
        meta = save_index()[slot]
        path = Path(meta["path"])
        module = load_module(path if path.exists() else _module_dir(meta["module"]))  # the module may have moved
        state = GameState.model_validate_json((config.SAVES / f"{slot}.json").read_text(encoding="utf-8"))
        self.app.switch_screen(GameScreen(module, state, resume=True, spectate=not state.human))


# -- the game -------------------------------------------------------------------------------
# Everything the game screen can do, for the Ctrl+P menu: (title, key, what it does, action).
# The footer shows the first few; the rest are a search away.
COMMANDS = (
    ("帮助", "F1", "玩法说明和按键", "help"),
    ("线索本", "F2", "找到的线索和手稿", "notes"),
    ("地图", "F3", "去过和听说过的地方", "map"),
    ("人物卡", "F4", "整张调查员卡，←→ 换队友", "sheet"),
    ("出主意", "F5", "照眼下的情况想三件能做的事", "suggest"),
    ("重讲上一段", "F6", "骰子和线索不变，换个讲法", "retell"),
    ("前情回顾", "F7", "从头讲一遍到现在", "recap"),
    ("手绘", "F8", "画一张图给守秘人看", "sketch"),
    ("换模型", "F9", "切换模型和思考强度", "engine"),
    ("复盘", "F10", "结局以后：真相、漏掉的线索、后日谈", "debrief"),
    ("接着讲", "", "接续中断的一段，保留已发生的结果", "retry"),
    ("存档", "Ctrl+S", "", "save"),
    ("叙事偏好", "Ctrl+O", "篇幅、恐怖程度、节奏和细节描写", "prefs"),
    ("换画法", "Ctrl+A", "块字符、六分块、照片、纯 ASCII", "art_style"),
    ("骰子皮肤", "", "骨白、黑曜石、血珀、黄铜、旧日、磷光", "dice_skin"),
    ("天气动画", "", "开或关；画面晃眼或卡顿时关掉", "weather"),
    ("用量", "Ctrl+B", "模型用量与估算花费", "usage"),
    ("规则插件", "Ctrl+K", "查看、暂停或启用本局额外规则", "plugins"),
    ("回到标题", "", "先存档，再回标题画面", "leave_game"),
)


class GameScreen(Screen):
    BINDINGS = [
        # F1–F5 on the footer, the rest in order of use; the weather switch is in the Ctrl+P menu
        Binding("f1", "help", "帮助"),
        Binding("f2", "notes", "线索本"),
        Binding("f3", "map", "地图"),
        Binding("f4", "sheet", "人物卡"),
        Binding("f5", "suggest", "出主意"),
        Binding("f6", "retell", "重讲", show=False),
        Binding("f7,ctrl+r", "recap", "前情回顾", show=False),
        Binding("f8", "sketch", "手绘", show=False),
        Binding("f9", "engine", "换模型", show=False),
        Binding("f10", "debrief", "复盘", show=False),
        Binding("ctrl+s", "save", "存档", show=False),
        Binding("ctrl+a", "art_style", "画法", show=False, priority=True),  # before the input box takes it as Home
        Binding("ctrl+o", "prefs", "叙事偏好", show=False),
        Binding("ctrl+b", "usage", "用量", show=False),
        Binding("ctrl+k", "plugins", "规则插件", show=False, priority=True),
        Binding("escape", "focus_input", "输入", show=False),
    ]
    SHOW_ENGINE = False  # the model is in Ctrl+B and F9; a LAN table says whose turn it is up there

    def __init__(self, module: Module, state: GameState, *, resume: bool = False, spectate: bool = False):
        super().__init__()
        self.m, self.state, self.resume, self.spectate = module, state, resume, spectate
        roster.attach(module, state.investigators)  # public-figure cards bring their own photos
        self.cast: Cast | None = None
        self.game: Game | None = None
        self.graph = None
        self.thread = {"configurable": {"thread_id": state.slot}}
        self.met: set[str] = set()
        self.spinner = 0
        self.status_text = ""
        self._spun = ""
        self.engine_label = ""
        self.busy = False
        self.queued: str | None = None  # an action typed while the keeper told, to go in once they are done
        self._leave_when_ready = False
        self.told: Static | None = None  # the story block of the latest narration, rewritten when it is told again
        self.afterword, self.writing = state.epilogue, False  # the epilogue as it comes in (engine/debrief.py)
        self.ideas = Ideas(self)  # F5 (tui/suggest.py)
        self._thumbs: dict[tuple[str | None, str], Text | None] = {}  # side-column faces; None: being drawn

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                with Container(id="stage"):  # the scene, and over its right side whoever is talking
                    yield SceneArt(id="scene")
                    yield Speaker()
                yield StoryLog(id="story")
                yield Static(id="status")
                yield Input(placeholder="输入行动或对话 · Ctrl+P 查看操作", id="command",
                            suggester=command_suggester(self._viewer()), select_on_focus=False)  # fmt: skip
            with Vertical(id="right"):
                yield SheetPanel(id="sheet")
                yield DiceTray(id="dice")
                yield HistoryPanel(id="history")
                yield CluePanel(id="clues")
        yield Footer()

    async def on_mount(self) -> None:
        self._start_history([e for e in self.state.log])
        cfg = config.load()
        self.engine_label = self._engine_label(cfg)
        self.cast = Cast(cfg, self.m)
        self.game = Game(self.m, self.state, self.cast, Rules(), emit=self.handle)
        self.query_one(SceneArt).effects = cfg.animations
        self.game.director_wait = PLAY_WAIT  # someone is waiting: a slow plan lands next turn instead
        channels.listeners.append(self._fell_back)
        config.SAVES.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(config.SAVES / "checkpoints.sqlite")
        saver = AsyncSqliteSaver(self._db)
        await saver.setup()
        self.graph = turn_graph.build(self.game, saver)
        self.game.checkpoint = lambda: write_save(self.state, self.m)
        self._scene(self.state.location)
        self._panels()
        self.set_interval(0.1, self._spin)
        self.set_interval(1.0, self._backstage)
        if self.spectate:
            self.query_one(Input).disabled = True
        self.boot()

    async def on_unmount(self) -> None:
        # A turn may still be writing its checkpoint: stop it before the database closes, or it
        # fails with "Cannot operate on a closed database" (leaving mid-turn, closing the app).
        stopped = self.app.workers.cancel_node(self)
        await asyncio.gather(*(w.wait() for w in stopped), return_exceptions=True)
        if self._fell_back in channels.listeners:
            channels.listeners.remove(self._fell_back)
        if self.game:
            await self.game.close()
        if self.cast:
            await self.cast.close()
        if getattr(self, "_db", None):
            await self._db.close()

    # -- helpers ---------------------------------------------------------------------------
    def _fell_back(self, note: str) -> None:
        self.app.notify(note, title="改用备用模型", severity="warning", timeout=12)

    @property
    def story(self) -> StoryLog:
        return self.query_one(StoryLog)

    # A modal on top (a roll, a handout) holds the story still while the turn goes on under
    # it; coming back, pick up the newest lines again unless the reader had scrolled away.
    def on_screen_suspend(self) -> None:
        self.following = all(s.is_vertical_scroll_end for s in self.query(StoryLog))

    def on_screen_resume(self) -> None:
        if getattr(self, "following", True):
            for s in self.query(StoryLog):
                s.scroll_end(animate=False)
        if self.focused is None and not self.spectate:
            for box in self.query("#command").results(Input):
                box.focus()

    # A click on the story or the stage takes the focus off the command box. Whatever is typed
    # or pasted then still goes into the box, instead of waiting for Esc to bring it back.
    def _typing(self) -> Input | None:
        box = self.query_one("#command", Input)
        return None if self.spectate or box.has_focus or box.disabled else box

    def on_key(self, event: events.Key) -> None:
        if event.is_printable and event.character and (box := self._typing()):
            box.focus()
            box.insert_text_at_cursor(event.character)
            event.prevent_default()
            event.stop()

    def on_paste(self, event: events.Paste) -> None:
        if event.text and (box := self._typing()):
            box.focus()
            box.insert_text_at_cursor(event.text.splitlines()[0])  # as the box takes a paste
            event.prevent_default()
            event.stop()

    def _status(self, text: str) -> None:
        self.status_text = text

    def _spin(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self.spinner = (self.spinner + 1) % len(frames)
        line = f"{frames[self.spinner]} {self.status_text}" if self.status_text else ""
        if self.queued is not None:
            wait = "这一段中断了，/retry 讲完再交" if self.state.pending_turn and not self.busy else "讲完就交"
            line += f"{'  · ' if line else ' '}{wait}：{self.queued[:24]}（/undo 撤回）"
        if line != self._spun:  # an idle status line is not repainted ten times a second
            self._spun = line
            self.query_one("#status", Static).update(Text(line), layout=False)  # one row, whatever it says

    def _busy(self, busy: bool) -> None:
        """While a turn runs the box stays open: the next action can be typed ahead, and keys
        pressed mid-narration are not lost."""
        self.busy = busy
        if not busy:
            self.status_text = ""
            if not self.spectate:
                self.query_one(Input).focus()
            if self._leave_when_ready:
                self._leave_when_ready = False
                self.app.call_later(self._leave)
            elif self.queued is not None:
                self.set_timer(0.05, self._send_queued)

    def _send_queued(self) -> None:
        """The keeper is done: the action typed ahead goes in. A turn that broke off goes on
        first (/retry); after the end the line goes back in the box."""
        if self.busy or self.queued is None or self.state.pending_turn:
            return
        text, self.queued = self.queued, None
        if self.state.ending and not commands.instant(text):
            box = self.query_one("#command", Input)
            box.value = box.value or text
            return
        self.send_line(text)

    def _scene(self, loc_id: str) -> None:
        loc = self.m.location(loc_id)
        name = loc.art or ""
        scene = self.query_one(SceneArt)
        scene.style_name = self.app.art_style  # type: ignore[attr-defined]
        scene.when = time_of_day(self.state.minutes)  # lit before the first frame, no bright flash
        scene.show(
            loc.name,
            self.m.art.get(name, ""),
            self.m.art_colors.get(name),
            self.m.pictures.get(name),
            weather=loc.weather,
            description=loc.read_aloud,
            sky=self._sky(loc_id),
        )
        self._topbar()

    def _sky(self, loc_id: str) -> str:
        """The narrated weather, if this place is under the open sky."""
        return self.state.sky if weather.outdoors(self.m.location(loc_id).name) else ""

    def on_resize(self, event) -> None:
        right = self.query_one("#right")
        width = event.size.width
        right.display = width >= 96
        right.styles.width = 40 if width >= 130 else 34
        self.query_one(SceneArt).refit()  # a taller terminal gets a taller picture
        self.query_one(Speaker).refit()
        self.call_after_refresh(self._panels)  # the sheet fits the new column; HP and SAN go up top without it

    def action_dice_skin(self) -> None:
        from . import dice

        names = list(dice.SKINS)
        now = self.app.dice_skin  # type: ignore[attr-defined]
        self.app.dice_skin = names[(names.index(now) + 1) % len(names)] if now in names else names[0]  # type: ignore[attr-defined]
        config.save_ui_settings({"dice": self.app.dice_skin})  # type: ignore[attr-defined]
        tray = self.query_one(DiceTray)
        if tray._view is None:  # nothing rolled yet: a look at the skin
            tray.roll({"skill": "皮肤预览", "target": 50, "value": 37, "level": "常规成功"})
        else:
            tray.redraw()
        self.app.notify(f"骰子皮肤：{dice.SKINS[self.app.dice_skin].name}")  # type: ignore[attr-defined]

    def action_art_style(self) -> None:
        styles, now = list(imagery.STYLES), self.app.art_style  # type: ignore[attr-defined]
        self.app.art_style = styles[(styles.index(now) + 1) % len(styles)] if now in styles else styles[0]  # type: ignore[attr-defined]
        self.app.notify(f"画面：{imagery.STYLE_NAMES[self.app.art_style]}")  # type: ignore[attr-defined]
        self._scene(self.state.location)
        self._panels()
        self.query_one(Speaker).refit()

    @staticmethod
    def _engine_label(cfg: config.Config) -> str:
        keeper = cfg.role("keeper")
        label = f"{cfg.preset} · 守秘人 {keeper.model.split(':', 1)[-1]}"
        label += f" · 备用 {keeper.fallback.split(':', 1)[-1]}" if keeper.fallback else ""
        return label + ("" if cfg.mode == "标准" else f" · {cfg.mode}")

    def action_engine(self) -> None:
        if self.busy:
            self.app.notify("等这一回合结束再换模型", severity="warning")
            return
        cfg = config.load()
        from .settings import model_choices

        presets, saved = model_choices(), config.ui_settings()
        model, fallback = os.environ.get("AUTOKEEPER_MODEL", saved.get("model", "")), os.environ.get("AUTOKEEPER_FALLBACK", saved.get("fallback", ""))
        self.app.push_screen(EngineModal(presets, cfg.preset, model, fallback, cfg.mode), self._switch_engine)

    def _switch_engine(self, choice: dict | None) -> None:
        if choice:
            self._apply_engine(choice)

    @work(exclusive=True, group="turn")
    async def _apply_engine(self, choice: dict) -> None:
        """New channels for every agent; the transcripts stay in the state, so nobody forgets."""
        assert self.game
        os.environ["AUTOKEEPER_PRESET"] = choice["preset"]
        os.environ["AUTOKEEPER_MODE"] = choice.get("mode", "标准")
        for key, env in (("model", "AUTOKEEPER_MODEL"), ("fallback", "AUTOKEEPER_FALLBACK")):
            if choice[key]:
                os.environ[env] = choice[key]
            else:
                os.environ.pop(env, None)
        try:
            cfg = config.load()
        except ValueError as exc:
            self.app.notify(str(exc), severity="error")
            return
        config.save_ui_settings(choice)
        old, self.cast = self.cast, Cast(cfg, self.m)
        await self.game.replace_cast(self.cast)
        self.engine_label = self._engine_label(cfg)
        self._topbar()
        self.app.notify(f"已切换为 {self.engine_label}。")
        if old is not None:
            await old.close()

    def action_weather(self) -> None:
        on = self.query_one(SceneArt).toggle_effects()
        self.app.notify(f"天气动画：{'开' if on else '关'}")

    def _topbar(self) -> None:
        """Where and when on the left; the turn and what it cost on the right. Without the side
        column, the investigator's HP and SAN come up here."""
        st = self.state
        when = time_of_day(st.minutes)
        left = Text(no_wrap=True, overflow="ellipsis")
        left.append(" AUTOKEEPER ", style="bold reverse")
        left.append(f"  《{self.m.title}》  ", style="bold")
        left.append(f"{LIGHT_MARK.get(when, '☀')} {st.clock} {when}  ", style="bold")
        left.append(f"◆ {self.m.location(st.location).name}")
        right = Text(no_wrap=True)
        if not self.query_one("#right").display:
            inv = self._viewer()
            right.append(f"HP {inv.hp}/{inv.hp_max}  SAN {inv.san}  ", style="bold red" if inv.hp * 3 <= inv.hp_max else "bold")
        tokens = sum(int(u.get("input", 0) + u.get("output", 0)) for u in st.usage.values())
        cost = sum(u.get("cost_usd", 0.0) for u in st.usage.values())
        right.append(f"第 {st.turn} 轮 · {tokens / 1000:.1f}k tokens" + (f" ${cost:.2f}" if cost else ""), style="dim")
        if self.SHOW_ENGINE and self.engine_label:
            right.append(f" · {self.engine_label}", style="dim")
        bar = Table.grid(expand=True)
        bar.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        bar.add_column(justify="right", no_wrap=True)
        bar.add_row(left, right)
        self.query_one("#topbar", Static).update(bar, layout=False)  # one row
        self.query_one(SceneArt).set_light(when)

    def _viewer(self):
        """Whose sheet the right column shows: here, whoever is acting."""
        return self.state.investigator

    def _me(self) -> str | None:
        """The investigator whose name is lit as yours: the one you play, if one."""
        return self.state.human[0] if len(self.state.human) == 1 else None

    def _start_history(self, entries) -> None:
        """What happened before this screen came up, then every block the story adds."""
        panel = self.query_one(HistoryPanel)
        for e in entries:
            if (line := log_line(e)) is not None:
                panel.note(line, None, e.turn)
        self.story.on_entry = self._history

    def _history(self, kind: str, body, block) -> None:
        if (line := history_line(kind, body, block)) is not None:
            self.query_one(HistoryPanel).note(line, block, self.state.turn)

    def _panels(self) -> None:
        st = self.state
        inv = self._viewer()
        tall = self.app.size.height >= 34  # room for a face beside the bars
        face = self._thumb(inv) if tall else None
        self.query_one(SheetPanel).show(inv, face, st.investigators)
        self.story.mark(st.investigators, self._me())
        self.query_one(CluePanel).show([self.m.clue(c).title for c in st.clues])
        lost = 1 - inv.san / max(1, inv.characteristics.get("意志", inv.san) or 1)
        self.query_one(SceneArt).madness = max(0.0, min(0.6, lost * 2))
        self._topbar()

    def _thumb(self, inv: Investigator) -> Text | None:
        """The side column's face. A picture goes through chafa in a thread (a game started, or
        Ctrl+A, used to wait for it); the sheet shows without it until it is in."""
        key = (inv.portrait, "ascii" if self.app.art_style == "ascii" else "photo")  # type: ignore[attr-defined]  # outlines are noise this small
        if key in self._thumbs:
            return self._thumbs[key]
        if not (inv.portrait in self.m.pictures and imagery.available()):
            return art_text(self.m, inv.portrait)  # hand-drawn, or nothing
        self._thumbs[key] = None
        self._draw_thumb(key)
        return None

    @work(thread=True, group="thumb", exit_on_error=False)  # a game left meanwhile: nobody to show it to
    def _draw_thumb(self, key: tuple[str | None, str]) -> None:
        face = art_text(self.m, key[0], PORTRAIT, key[1], face=True)
        if self.is_attached:
            self.app.call_from_thread(self._thumb_drawn, key, face)

    def _thumb_drawn(self, key: tuple[str | None, str], face: Text) -> None:
        self._thumbs[key] = face
        if self.is_attached:
            self._panels()

    # -- event handling ----------------------------------------------------------------------
    async def handle(self, ev: dict) -> None:
        t = ev.get("type")
        story = self.story
        if t == "art":
            key = ev["key"]
            self.m.art[key], self.m.art_colors[key] = ev["art"], ev["mask"]
            if ev.get("picture") and Path(ev["picture"]).is_file():
                self.m.pictures[key] = ev["picture"]
        self.query_one(Speaker).follow(ev, self.m)  # before the scene changes, so it can scan in
        if t == "status":
            self._status(ev["text"])
        elif t == "move":
            self._scene(ev["location"])
        elif t == "time":
            self._topbar()
        elif t == "sky":
            self.query_one(SceneArt).set_sky(self._sky(self.state.location))
        elif t == "improv":  # what the director added is told in the story, never announced over it
            self._topbar()
        elif t == "art":
            if self.m.location(self.state.location).art == ev["key"]:
                self._scene(self.state.location)
            for card in self.query(ItemCard):
                if card.art_key == ev["key"]:
                    card.redraw()
            self._topbar()
            if self.game and not self.busy:
                write_save(self.state, self.m)
        elif t == "item":
            existing = next((card for card in self.query(ItemCard) if card.item_id == ev["id"]), None)
            if existing is not None:
                existing.title, existing.description = ev["name"], ev["text"]
                existing.redraw()
                existing.scroll_visible()
            else:
                story.add("item", ItemCard(self.m, ev, self.app.art_style))
        elif t == "damage":
            story.add("system", Text(f"✚ {ev['who']} {ev['text']}", style="bold red"))
            self._panels()
        elif t == "plugin_status":
            self.app.notify("一项扩展规则已暂停，可按 Ctrl+K 查看。", severity="warning")
        elif t == "opening":
            story.add("system", Text(ev["text"], style="italic"))
        elif t == "echo":  # the first beat, while the ruling and the narration are worked out
            story.add("echo", Text(ev["text"]))
        elif t == "keeper_start":  # told again: the same block, new words
            self._status("守秘人换个讲法…" if ev.get("again") else "守秘人正在讲述…")
            self.told = story.begin_keeper(self.told if ev.get("again") else None)
        elif t == "keeper_delta":
            story.feed(ev["text"])
        elif t == "keeper_end":
            story.end_keeper(ev["text"])
            self._status("")
        elif t == "waived":  # a check asked for with .ra that the keeper needs no dice for
            story.add("dice", Text(f"◈ {ev['who']} 的{ev['skill']}不用掷：守秘人直接说结果", style="dim"))
        elif t == "repeat":  # the same attempt again: no dice, the earlier result stands
            story.add("dice", Text(f"◈ {ev['who']} 不重掷：{ev['text']}（刚才的结果不变）", style="dim"))
        elif t == "dice":
            self.query_one(DiceTray).roll(ev)
            story.add("dice", RollCard(ev))
            self._panels()
        elif t == "outcome":  # what the roll led to, under its card
            cards = story.query(RollCard)
            if cards:
                cards.last().settle(ev["lines"])
        elif t == "command":
            story.add("dice", Text(f"◈ {ev.get('who', '')}：{ev['text']}"))
        elif t == "clue":
            body = Text()
            body.append(f"〔{ev['title']}〕\n", style="bold")
            body.append(ev["text"])
            story.add("clue", body)
            self._panels()
            self.app.notify(f"获得线索：{ev['title']}", timeout=3)
        elif t == "handout":
            story.add("handout", Text(f"▤ 手稿：{ev['title']}（F2 可重看）", style="bold"))
            handout = next((h for h in self.m.handouts if h.id == ev.get("id")), None)
            pictures = [self.m.pictures[k] for k in handout.images if k in self.m.pictures] if handout else []
            self.app.push_screen(HandoutModal(ev["title"], ev["text"], pictures))
        elif t == "sanity":
            body = Text()
            body.append(f"※ {ev['who']} 理智 {ev['before']} → {ev['after']}（-{ev['loss']}）", style="bold")
            for k, label in (("temporary", "临时疯狂"), ("indefinite", "不定性疯狂"), ("permanent", "永久疯狂")):
                if ev.get(k):
                    body.append(f"  {label}", style="bold red reverse")
            story.add("sanity", body)
            if ev.get("loss"):
                self.query_one(SceneArt).glitch(10 + min(30, ev["loss"] * 3))
            self._panels()
        elif t == "event":
            body = Text()
            body.append(f"━━━━ {ev['title']} ━━━━\n", style="bold")
            if ev.get("text"):
                body.append(ev["text"], style="italic")
            story.add("event", body)
        elif t == "npc":
            self._npc_line(ev, "npc")
        elif t == "performance":
            self._npc_line(ev, "performance")
            self.query_one(SceneArt).glitch(24)
        elif t == "luck":
            story.add("system", Text(f"◎ {ev['who']} 花掉 {ev['spent']} 点幸运（剩 {ev['left']}）"))
            self._panels()
        elif t == "ending":
            body = Text()
            body.append(f"【结局 · {ev['title']}】\n\n", style="bold")
            body.append(ev["text"] + "\n")
            if ev.get("reward"):
                body.append(f"\n{ev['reward']}", style="dim")
            for note in ev.get("notes", []):
                body.append(f"\n{note}", style="bold")
            story.add("ending", body)
        elif t == "sheet":
            self._panels()
        elif t == "epilogue_start":
            self.afterword, self.writing = "", True
        elif t == "epilogue_delta":
            self.afterword += ev["text"]
        elif t == "epilogue":
            self.afterword, self.writing = ev["text"], False
            if ev.get("error"):
                self.app.notify(ev["error"], severity="error", timeout=6)
        tips.after(self, ev)  # the first time something happens: one line on how it works

    def _npc_line(self, ev: dict, kind: str) -> None:
        """Their face is on the stage while they talk (Speaker), so the story keeps to words."""
        npc = self.m.npc(ev["npc"])
        body = Text()
        first = npc.id not in self.met
        self.met.add(npc.id)
        body.append(f"{ev['name']}", style="bold")
        # What they look like, the first time; never their role, which is the keeper's note
        # ("失踪的叔叔，如今是半食尸鬼——那个偷书贼").
        body.append("  登台\n" if kind != "npc" else f"  {npc.look}\n" if first and npc.look else "\n", style="dim")
        body.append(ev["text"])
        self.story.add(kind, body)

    # -- turns ----------------------------------------------------------------------------------
    @work(exclusive=True, group="turn")
    async def boot(self) -> None:
        assert self.game and self.cast
        self._busy(True)
        warm = asyncio.create_task(self._warm())
        try:
            if self.state.pending_turn.get("opening"):
                await self.game.start()
            elif self.state.pending_turn:
                self.story.add("system", Text("上一回合尚未讲完，正在接续已保存的结果。"))
                for entry in self.state.log:
                    if entry.turn == self.state.turn and entry.kind in ("dice", "npc", "event", "clue"):
                        self.story.add(entry.kind, Text(entry.text))
                await self._run_turn({"input": "/retry"})
            elif self.resume:
                self._status("正在回顾前情…")
                self.story.add("event", Text("━━━━ 前情回顾 ━━━━", style="bold"))
                self.story.begin_keeper()
                text = await self.cast.recap(self.state, on_delta=self._feed)
                self.story.end_keeper(text)
                self._scene(self.state.location)
            else:
                self._status("守秘人正在准备开场…")
                await self.game.start()
                write_save(self.state, self.m)
            await warm
        except Exception as exc:
            self._turn_error(exc)
        finally:
            if not warm.done():
                warm.cancel()
                await asyncio.gather(warm, return_exceptions=True)
            self.game.visuals.resume()
            self._busy(False)
        if self.spectate:
            self.autoplay()

    async def _feed(self, text: str) -> None:
        self.story.feed(text)

    async def _warm(self) -> None:
        """While the opening plays: start the agents' processes."""
        assert self.cast
        try:
            await self.cast.warm(self.state)
        except Exception:  # warming is an optimisation; failures surface on first real use
            pass

    def action_focus_input(self) -> None:
        if self.state.ending and not self.writing:  # finish saving a last dice command before leaving
            if self.busy:
                self._leave_when_ready = True
            else:
                self._leave()
        elif not self.spectate:
            self.query_one(Input).focus()

    def _leave(self) -> None:
        self.app.pop_screen() if len(self.app.screen_stack) > 1 else self.app.exit()

    def action_leave_game(self) -> None:
        if self.busy:
            self.app.notify("等这一回合结束再走", severity="warning")
            return
        if not self.spectate:
            self.action_save()
        self._leave()

    @on(Input.Submitted, "#command")
    def submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if self.busy and text and text.split()[0] not in ("/notes", "/map", "/sheet", "/help", "/theme", "/prefs", "/usage", "/undo"):
            if not text.startswith("/") and self.queued is None and not self.state.ending:
                self.queued = text  # typed ahead: it goes in as soon as the keeper is done
                event.input.value = ""
            else:  # the line stays in the box
                self.app.notify("已经记下一条了，讲完先交那条；/undo 撤回它" if self.queued else "这一回合还没结束，等它讲完再按回车", timeout=3)
            return
        event.input.value = ""
        if text:
            self.send_line(text)
        else:
            self.empty_enter()

    def empty_enter(self) -> None:
        """Enter on an empty box: nothing here; at a shared table, the end of your round."""

    @on(Input.Changed, "#command")
    def typing(self) -> None:
        # Every weather frame moves the terminal cursor across the screen and back; on terminals
        # without synchronized output that makes the caret and the IME box jump while typing.
        self.query_one(SceneArt).quiet(4.0)

    def send_line(self, text: str) -> None:
        """One line from the input box: a slash command, or the investigator's action."""
        if text.startswith("/"):
            self._slash(text)
            return
        if self.state.ending and not commands.instant(text):  # growth rolls (.en) and the like still work
            self.app.notify("这局已经结束了。F10 复盘，.en 做成长检定，F7 回顾这一局，Esc 回到标题。")
            return
        self.play(text)

    def _slash(self, text: str) -> None:
        cmd, *args = text.split()
        cmd = cmd.lower()
        actions = {"/draw": self.action_sketch, "/notes": self.action_notes, "/map": self.action_map, "/sheet": self.action_sheet,
                   "/help": self.action_help, "/recap": self.action_recap, "/save": self.action_save, "/debrief": self.action_debrief,
                   "/prefs": self.action_prefs, "/suggest": self.action_suggest, "/usage": self.action_usage}
        if cmd == "/retry":
            self.action_retry()
        elif cmd == "/undo":  # the action typed ahead goes back in the box, to change or drop
            if self.queued is None:
                self.app.notify("没有可以撤回的行动")
            else:
                self.query_one("#command", Input).value, self.queued = self.queued, None
        elif cmd == "/retell":  # /retell 短一点 skips the list
            self.action_retell(args[0] if args else "")
        elif cmd in actions:
            actions[cmd]()
        elif cmd == "/theme":
            names = [th.name for th in THEMES]
            self.app.theme = names[(names.index(self.app.theme) + 1) % len(names)] if self.app.theme in names else names[0]
        elif cmd == "/quit":
            self._leave()
        else:
            self.app.notify(f"不认识的命令：{cmd}", severity="warning")

    @work(exclusive=True, group="turn")
    async def play(self, text: str) -> None:
        assert self.graph and self.game
        self.ideas.drop()  # whatever F5 was still thinking of, this action came first
        self._busy(True)
        inv = self.state.investigator
        self.story.add("player", Text(f"▶ {inv.name}：{text}"))
        turn = self.state.turn
        active = self.state.active
        try:
            if self.state.pending_turn.get("opening"):
                await self.game.start()
            else:
                await self._run_turn({"input": text, "by_hand": True})
                if self.state.turn != turn:
                    await self._companions()
        except Exception as exc:
            self._turn_error(exc)
        finally:
            if self.state.active != active:
                self.state.active = active
                self._panels()
            if self.game:
                self.game.visuals.resume()
            self._busy(False)

    def _turn_error(self, exc: Exception) -> None:
        text = f"这一段暂未完成（{type(exc).__name__}）。输入 /retry 接着讲，已掷出的结果会保留。"
        self.story.end_keeper("这一段暂时中断。")
        self.story.add("system", Text(text))
        self.app.notify(text, severity="warning", timeout=12)
        write_save(self.state, self.m)

    def action_retry(self) -> None:
        if not self.busy:
            if self.game is None or self.state.pending_turn:
                self.play("/retry")
            else:
                self.app.notify("目前没有中断的回合，可以继续说出行动。")

    async def _run_turn(self, inp, auto: bool = False) -> None:
        assert self.graph
        ended = self.state.ending
        while True:
            offer = None
            async for mode, chunk in self.graph.astream(inp, self.thread, stream_mode=["custom", "updates"]):
                if mode == "custom":
                    await self.handle(chunk)
                elif isinstance(chunk, dict) and "__interrupt__" in chunk:
                    offer = chunk["__interrupt__"][0].value
            if offer is None:
                break
            if offer.get("kind") == "roll":  # the player throws their own dice
                self._status("等你掷骰…")
                choice = "roll" if auto else await self.app.push_screen_wait(RollModal(offer))
            elif auto:
                choice = "push" if offer.get("can_push") else "luck" if offer.get("luck") is not None else "drop"
                self.story.add("system", Text(f"◇ AI 选择：{ {'push': '孤注一掷', 'luck': '花幸运', 'drop': '放弃'}[choice] }", style="dim"))
            else:
                choice = await self.app.push_screen_wait(PushModal(offer))
            inp = Command(resume=choice)
        assert self.game
        self.game.emit = self.handle
        write_save(self.state, self.m)
        self._topbar()
        if self.state.ending and not ended:
            self._ending_summary()

    async def _companions(self) -> None:
        """AI companions act after the human, every other turn."""
        st = self.state
        ai = [i for i, inv in enumerate(st.investigators) if inv.id not in st.human and inv.can_act]
        if not ai or st.ending or st.turn % 2:
            return
        human_idx = st.active
        try:
            for idx in ai:
                if st.ending:
                    break
                st.active = idx
                await self._ai_act(idx)
        finally:
            st.active = human_idx
        self._panels()

    async def _ai_act(self, idx: int) -> None:
        assert self.cast
        st = self.state
        inv = st.investigators[idx]
        self._status(f"{inv.name}在想下一步…")
        situation = self._situation()
        action = await self.cast.investigator(st, inv.id, situation)
        self.story.add("player", Text(f"▶ {inv.name}（AI）：{action}"))
        await self._run_turn({"input": action, "by_hand": False}, auto=True)

    def _situation(self) -> str:
        from ..engine.autoplay import situation

        return situation(self.m, self.state)

    @work(exclusive=True, group="turn")
    async def autoplay(self) -> None:
        """Watch mode: every round the AI investigators propose in parallel (LangGraph Send)."""
        from ..engine.autoplay import party_graph, situation

        assert self.cast
        st = self.state
        party = party_graph(self.cast, st)
        self._busy(True)
        while not st.ending and st.turn < 60:
            alive = [inv.id for inv in st.investigators if inv.can_act]
            if not alive:
                break
            self._status("AI 调查员在商量…")
            result = await party.ainvoke({"situation": situation(self.m, st), "ids": alive, "proposals": []})
            for inv_id, text in result["proposals"]:
                if st.ending:
                    break
                st.active = next(i for i, inv in enumerate(st.investigators) if inv.id == inv_id)
                self._panels()
                self.story.add("player", Text(f"▶ {st.investigator.name}（AI）：{text}"))
                await self._run_turn({"input": text, "by_hand": False}, auto=True)
        self._busy(False)

    def _ending_summary(self) -> None:
        st = self.state
        found = len(st.clues)
        total = len(self.m.clues)
        t = Text()
        t.append(f"线索 {found}/{total} · 用时至 {st.clock} · 共 {st.turn} 轮\n", style="bold")
        for inv in st.investigators:
            t.append(f"{inv.name}：HP {inv.hp}/{inv.hp_max}  理智 {inv.san}  {'、'.join(inv.conditions)}\n")
        calls = sum(int(u["calls"]) for u in st.usage.values())
        tokens = sum(int(u["input"] + u["output"]) for u in st.usage.values())
        t.append(f"\n模型调用 {calls} 次 · {tokens} tokens\n", style="dim")
        t.append("F10 复盘 · " + ("" if self.spectate else ".en 做成长检定 · ") + "F7 回顾这一局 · Esc 回到标题", style="dim")
        self.story.add("ending", t)

    # -- actions -----------------------------------------------------------------------------------
    def action_help(self) -> None:
        self.app.push_screen(GuideScreen(tips.page(self) or "keys"))  # where the last hint pointed, else the keys

    @work(group="ui")
    async def action_notes(self) -> None:
        st = self.state
        clues = [(self.m.clue(c).title, self.m.clue(c).text.strip()) for c in st.clues]
        handouts = [(h, self.m.handout(h).title) for h in st.handouts]
        facts = [f"{'已履行' if f.status == 'fulfilled' else '记下了'}：{f.text}" for f in st.world.values()]
        chosen = await self.app.push_screen_wait(NotebookModal(clues, handouts, facts))
        if chosen:
            h = self.m.handout(chosen)
            self.app.push_screen(HandoutModal(h.title, h.text, [self.m.pictures[k] for k in h.images if k in self.m.pictures]))

    def action_sheet(self) -> None:
        from .sheet import SheetModal

        party, viewer = self.state.investigators, self._viewer()
        first = next((i for i, inv in enumerate(party) if inv.id == viewer.id), 0)
        style = self.app.art_style  # type: ignore[attr-defined]

        def portrait(inv: Investigator) -> Text:
            return art_text(self.m, inv.portrait, (30, 14) if inv.portrait in self.m.pictures else None, style, face=True)

        self.app.push_screen(SheetModal(party, first, portrait))

    def action_plugins(self) -> None:
        from .plugins import PluginsModal

        self.app.push_screen(PluginsModal(self.m, self.state), self._plugins_changed)

    def _plugins_changed(self, _) -> None:
        write_save(self.state, self.m)

    def action_usage(self) -> None:
        from .usage import UsageModal

        self.app.push_screen(UsageModal(self.state, self.engine_label))

    def action_map(self) -> None:
        self.app.push_screen(MapModal(self.m, self.state))

    def action_prefs(self) -> None:
        from .prefs import PrefsModal

        self.app.push_screen(PrefsModal(self.cast.prefs if self.cast else None), self._prefs_saved)

    def _prefs_saved(self, chosen: Prefs | None) -> None:
        """Mid-turn is fine: the next narration reads them, and nothing already told changes."""
        if chosen is not None and self.cast is not None:
            self.cast.prefs = chosen

    def action_suggest(self) -> None:
        self.ideas.ask()

    async def ideas_for(self, inv: Investigator) -> list[str]:
        """Three things the player's investigator could do next (F5): the investigator role
        reads what the players have seen, as the AI companions do."""
        assert self.cast
        return await self.cast.suggest(self.state, inv.id, self._situation())

    def _backstage(self) -> None:
        """The director finished a plan while nobody was mid-turn: play it now."""
        if self.game and not self.busy and self.game.ready():
            self._interlude()

    @work(exclusive=True, group="turn")
    async def _interlude(self) -> None:
        assert self.game
        self._busy(True)
        self._status("守秘人正在讲述…")
        self.game.emit = self.handle
        await self.game.interlude()
        write_save(self.state, self.m)
        self._busy(False)

    def _turn_running(self) -> bool:
        """Starting another turn-group worker would cancel the running turn."""
        if self.busy:
            self.app.notify("等这一回合结束再来", severity="warning", timeout=3)
        return self.busy

    def action_sketch(self) -> None:
        if not self._turn_running():
            self._sketch()

    def action_recap(self) -> None:
        if not self._turn_running():
            self._recap()

    @work(exclusive=True, group="turn")
    async def _sketch(self) -> None:
        assert self.game
        st = self.state
        prompt = next((p for p in self.m.sketches if not p.after or p.after in st.clues), None)
        ask = prompt.ask if prompt else "画下你想给守秘人看的东西"
        png = await self.app.push_screen_wait(SketchModal(ask))
        if not png:
            return
        self._busy(True)
        self.game.emit = self.handle
        self.story.add("player", Text(f"▶ {st.investigator.name} 交出了一幅草图"))
        await self.game.sketch(png, prompt.id if prompt else None)
        write_save(self.state, self.m)
        self._busy(False)

    @work(exclusive=True, group="turn")
    async def _recap(self) -> None:
        assert self.cast
        self._busy(True)
        self._status("正在回顾前情…")
        self.story.add("event", Text("━━━━ 前情回顾 ━━━━", style="bold"))
        self.story.begin_keeper()
        text = await self.cast.recap(self.state, on_delta=self._feed)
        self.story.end_keeper(text)
        self._busy(False)

    def action_save(self) -> None:
        write_save(self.state, self.m)
        self.app.notify(f"已存档：{self.state.slot}")

    # -- telling it again, and after the end -------------------------------------------------
    def action_retell(self, nudge: str = "") -> None:
        """F6, /retell: the latest narration in other words; the dice and clues stay."""
        if self._turn_running() or self.game is None:
            return
        why = self.game.retell_blocked() or ("" if not nudge or nudge in RETELL else f"可以这样要：{'、'.join(RETELL)}")
        if why:
            self.app.notify(why, severity="warning", timeout=5)
        elif nudge:
            self._retell(nudge)
        else:
            self.app.push_screen(RetellModal(), partial(self._retell_chosen, told=self.game.told))

    def _retell_chosen(self, nudge: str | None, told: Told | None) -> None:
        """While the list was open the keeper may have gone on (an interlude): then it is not
        the narration they asked about any more."""
        if not nudge or self._turn_running() or self.game is None:
            return
        if self.game.told is not told:
            self.app.notify("守秘人已经讲到下一段了，想重讲就再按一次 F6", severity="warning", timeout=5)
            return
        self._retell(nudge)

    @work(exclusive=True, group="turn")
    async def _retell(self, nudge: str) -> None:
        assert self.game
        self._busy(True)
        self.game.emit = self.handle
        try:
            await self.game.retell(nudge)
        except Exception as exc:  # the old telling is back on screen
            why = str(exc) if isinstance(exc, ValueError) else exc.__class__.__name__
            self.app.notify(f"没能重讲（{why}），原来那段还在", severity="error", timeout=6)
        else:
            write_save(self.state, self.m)
        self._busy(False)

    def action_debrief(self) -> None:
        """F10, /debrief: once it is over, the whole book, and the epilogue on request."""
        if not self.state.ending:
            self.app.notify("复盘要等这一局结束", severity="warning", timeout=3)
            return
        self.app.push_screen(DebriefModal(debrief.build(self.m, self.state), self._epilogue_so_far, self._ask_epilogue))

    def _epilogue_so_far(self) -> tuple[str, bool]:
        return self.afterword or self.state.epilogue, self.writing

    def _ask_epilogue(self) -> None:
        if not self._turn_running():
            self._epilogue()

    @work(exclusive=True, group="turn")
    async def _epilogue(self) -> None:
        assert self.game
        self._busy(True)
        self._status("守秘人在讲后日谈…")
        self.game.emit = self.handle
        try:
            await debrief.epilogue(self.game)
        except Exception:  # the event already said so
            pass
        else:
            write_save(self.state, self.m)
        self._busy(False)


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
            self.push_screen(CommandPalette(placeholder="找功能：地图、存档、换画法……", id="--command-palette"))


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

    def on_mount(self) -> None:
        for th in THEMES:
            self.register_theme(th)
        self.theme = self._theme_name or config.load().theme
        if not imagery.available():  # pictures fall back to the hand-drawn art without a word otherwise
            self.notify(f"没找到 chafa，照片显示不出来，只能看手绘字符画。安装：{imagery.install_hint()}，装完重开。",
                        title="缺少 chafa", severity="warning", timeout=15)
        if self.join:
            from .net import JoinScreen, LobbyScreen

            address, name = self.join
            self.push_screen(LobbyScreen(address, name) if address and name else JoinScreen(address, name))
        else:
            self.push_screen(TitleScreen())

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
