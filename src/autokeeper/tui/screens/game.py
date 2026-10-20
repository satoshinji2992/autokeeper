"""The game screen: the story, the picture, the right column's pages, and every key of a game."""

from __future__ import annotations

import asyncio
import os
import time
import traceback
from collections.abc import Callable
from functools import partial
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from ... import config, imagery, paths, roster, weather
from ...agents.cast import Cast
from ...engine import debrief
from ...engine import graph as turn_graph
from ...engine.game import PLAY_WAIT, RETELL, Game, Told
from ...engine.state import GameState, time_of_day
from ...harness import models as channels
from ...module.schema import Module
from ...prefs import Prefs
from ...rules import commands
from ...rules.coc import Investigator, Rules
from ..library import THEMES, art_text, face_text, write_save
from ..modals.debrief import DebriefModal
from ..modals.dialogs import HandoutModal, NotebookModal, PushModal, RollModal, SketchModal
from ..modals.engine import EngineModal
from ..modals.guide import GuideScreen
from ..modals.mapview import MapModal
from ..modals.menu import MENU, Confirm, GameMenu
from ..modals.plugins import plugin_commands
from ..modals.retell import RetellModal
from ..modals.showcase import ShowcaseScreen
from ..modals.suggest import Ideas
from ..widgets import tips
from ..widgets.devpanel import DevPanel
from ..widgets.pages import PagesHead, PagesModal, background_view, progress_view
from ..widgets.panels import (
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
    skill_completions,
)
from ..widgets.plugin_ui import Plays, panel_view, tab_label
from ..widgets.speaker import Speaker
from .prelude import PreludeScreen
from .saves import LoadScreen

COMMANDS = (
    ("帮助", "F1", "玩法说明和按键", "help"),
    ("线索本", "F2", "找到的线索和手稿", "notes"),
    ("地图", "F3", "去过和听说过的地方", "map"),
    ("人物卡", "F4", "完整人物卡，←→ 切换调查员", "sheet"),
    ("行动建议", "F5", "根据当前情况生成三条行动建议", "suggest"),
    ("重讲上一段", "F6", "调整叙述方式，保留检定与线索", "retell"),
    ("前情回顾", "F7", "回顾本局已发生的事件", "recap"),
    ("经过", "", "行动、检定与发现记录；右栏也有", "page('history')"),
    ("进展", "Ctrl+G", "当前调查进展，有新进展时右栏标「进展·新」", "page('progress')"),
    ("背景", "", "开局已知信息与调查员背景", "page('background')"),
    ("整理进展", "Ctrl+Y", "整理当前调查进展，每轮一次", "recount"),
    ("手绘", "F8", "绘制并提交草图", "sketch"),
    ("换模型", "F9", "切换模型和思考强度", "engine"),
    ("复盘", "F10", "结局后查看真相、未发现线索与后日谈", "debrief"),
    ("开发者模式", "F12", "查看隐藏状态、角色会话与调用记录，使用 AI 排查异常", "developer"),
    ("继续叙述", "", "接续中断的一段，保留已发生的结果", "retry"),
    ("存档", "Ctrl+S", "", "save"),
    ("叙事偏好", "Ctrl+O", "篇幅、恐怖程度、节奏和细节描写", "prefs"),
    ("换画法", "Ctrl+A", "块字符、六分块、照片、纯 ASCII", "art_style"),
    ("骰子皮肤", "", "骨白、黑曜石、血珀、黄铜、青石、磷光", "dice_skin"),
    ("天气动画", "", "画面闪烁或卡顿时可关闭", "weather"),
    ("用量", "Ctrl+B", "模型用量与估算花费", "usage"),
    ("规则插件", "Ctrl+K", "查看、暂停或启用本局额外规则", "plugins"),
    ("回到标题", "", "保存进度并返回标题页", "leave_game"),
)




PAGES_MIN = 4  # the history's head and three lines; it opens in full with Ctrl+G


class GameScreen(Screen):
    BINDINGS = [
        # Esc and F1–F5 on the footer, the rest in order of use; everything common is in the Esc menu
        Binding("escape", "menu", "菜单", key_display="Esc"),
        Binding("f1", "help", "帮助"),
        Binding("f2", "notes", "线索本"),
        Binding("f3", "map", "地图"),
        Binding("f4", "sheet", "人物卡"),
        Binding("f5", "suggest", "行动建议"),
        Binding("f6", "retell", "重讲", show=False),
        Binding("f7,ctrl+r", "recap", "前情回顾", show=False),
        Binding("f8", "sketch", "手绘", show=False),
        Binding("f9", "engine", "换模型", show=False),
        Binding("f10", "debrief", "复盘", show=False),
        Binding("f12", "developer", "开发者模式", show=False),
        Binding("tab", "complete_skill(1)", "补全技能", show=False, priority=True),
        Binding("shift+tab", "complete_skill(-1)", "上个技能", show=False, priority=True),
        Binding("ctrl+s", "save", "存档", show=False),
        Binding("ctrl+a", "art_style", "画法", show=False, priority=True),  # before the input box takes it as Home
        Binding("ctrl+o", "prefs", "叙事偏好", show=False),
        Binding("ctrl+b", "usage", "用量", show=False),
        Binding("ctrl+k", "plugins", "规则插件", show=False, priority=True),
        Binding("ctrl+g", "page('progress')", "进展", show=False),  # 经过 / 进展 / 背景 as a page over the screen
        Binding("ctrl+y", "recount", "整理进展", show=False),
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
        self.dev_errors: list[str] = []
        self.dev_url = ""  # the developer web page, once started
        self._skill_choices: list[str] = []
        self._skill_at = -1
        self._skill_value = ""
        self.queued: str | None = None  # an action typed while the keeper told, to go in once they are done
        self._leave_when_ready: Callable[[], None] | None = None  # 讲完再走: where to go once the turn is over
        self.told: Static | None = None  # the story block of the latest narration, rewritten when it is told again
        self.afterword, self.writing = state.epilogue, False  # the epilogue as it comes in (engine/debrief.py)
        self.ideas = Ideas(self)  # F5 (tui/modals/suggest.py)
        self.plays = Plays(self)  # rule plugins' shows and prompts (tui/widgets/plugin_ui.py)
        self._thumbs: dict[tuple, Text | None] = {}  # side-column faces by (portrait, picture, style); None: being drawn
        self.view = (state.location, state.view)  # which picture of which place the scene shows (engine/views.py)

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                with Container(id="stage"):  # the scene, and over its right side whoever is talking
                    yield SceneArt(id="scene")
                    yield Speaker()
                yield StoryLog(id="story")
                yield DevPanel(id="dev")
                yield Static(id="status")
                yield OptionList(id="picker")
                yield Input(placeholder="输入行动或对话 · /kp 向守秘人提问 · Esc 菜单", id="command",
                            suggester=self._suggester(), select_on_focus=False)  # fmt: skip
            with Vertical(id="right"):
                yield SheetPanel(id="sheet")
                yield DiceTray(id="dice")
                with Vertical(id="pages"):
                    yield PagesHead(id="pages-head")
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
        self.query_one(DiceTray).tumbling = cfg.animations
        self.game.director_wait = PLAY_WAIT  # someone is waiting: a slow plan lands next turn instead
        self.game.write_progress = True  # someone reads it: the 进展 page
        self.game.world_later = True  # NPCs decide while the player reads, not while they wait
        self.game.world.reviewing = True  # the director looks backstage after a night or a journey (Game._look)
        channels.listeners.append(self._fell_back)
        paths.games().mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(paths.checkpoints())
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
        self.call_later(self.plays.ready)  # a plugin's question that waited behind another window

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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "complete_skill":
            box = self.query_one("#command", Input)
            return box.has_focus and box.cursor_position == len(box.value) and bool(skill_completions(box.value, self._viewer()))
        return super().check_action(action, parameters)

    # -- the list over the input box: the skills a check command can take -----------------------
    @on(events.Click, "#command")
    def _box_clicked(self) -> None:
        """A check command in the box (.ra 聆): its skills listed, to pick one by click."""
        box = self.query_one("#command", Input)
        if choices := skill_completions(box.value, self._viewer()):
            picker = self.query_one("#picker", OptionList)
            picker.clear_options()
            picker.add_options([Option(c, id=c) for c in choices])
            picker.highlighted = choices.index(box.value) if box.value in choices else 0
            picker.border_title = "技能"
            picker.display = True
            picker.focus()

    def _unpick(self) -> bool:
        picker = self.query_one("#picker", OptionList)
        if not picker.display:
            return False
        picker.display = False
        self.query_one("#command", Input).focus()
        return True

    @on(OptionList.OptionSelected, "#picker")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        key = event.option.id or ""
        box = self.query_one("#command", Input)
        box.value = self._skill_value = key
        box.cursor_position = len(key)
        self._unpick()

    def action_complete_skill(self, step: int = 1) -> None:
        box = self.query_one("#command", Input)
        if box.value != self._skill_value or not self._skill_choices:
            self._skill_choices = skill_completions(box.value, self._viewer())
            self._skill_at = -1 if step > 0 else 0
        if self._skill_choices:
            self._skill_at = (self._skill_at + step) % len(self._skill_choices)
            box.value = self._skill_value = self._skill_choices[self._skill_at]
            box.cursor_position = len(box.value)

    def _status(self, text: str) -> None:
        self.status_text = text

    def _spin(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self.spinner = (self.spinner + 1) % len(frames)
        line = f"{frames[self.spinner]} {self.status_text}" if self.status_text else ""
        if self.queued is not None:
            wait = "当前叙述已中断，/retry 继续后提交" if self.state.pending_turn and not self.busy else "待叙述结束后提交"
            line += f"{'  · ' if line else ' '}{wait}：{self.queued[:24]}（/undo 撤回）"
        if line != self._spun:  # an idle status line is not repainted ten times a second
            self._spun = line
            self.query_one("#status", Static).update(Text(line), layout=False)  # one row, whatever it says

    def _busy(self, busy: bool) -> None:
        """While a turn runs the box stays open: the next action can be typed ahead, and keys
        pressed mid-narration are not lost."""
        self.busy = busy
        if self.game is not None and (server := getattr(self.game, "debug_server", None)) is not None:
            server.busy = busy
        if not busy:
            self.status_text = ""
            if not self.spectate:
                for box in self.query(Input).results(Input):  # none once the screen is leaving
                    box.focus()
            if self._leave_when_ready is not None:
                go, self._leave_when_ready = self._leave_when_ready, None
                self.app.call_later(go)
            elif self.queued is not None:
                self.set_timer(0.05, self._send_queued)
            else:
                self.plays.ready()  # a plugin's question waited for the turn to end

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
        if self.view[0] == loc_id and self.view[1] and (self.view[1] in self.m.art or (self.view[1] in self.m.pictures and imagery.available())):
            name = self.view[1]  # another picture of this place: a corner they look at, the night, a moment
        if not (found := self.query(SceneArt)):  # the screen is being taken down: nothing to draw on
            return
        scene = found.first()
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
            people=self._faces(loc_id),
        )
        self._topbar()

    def _faces(self, loc_id: str) -> tuple[str, ...]:
        """The photos of the people here the investigators know (they have spoken with them), set
        into the scene's picture (imagery.cameos): nobody they have not met shows a face, so the
        figure by the tombstone is no one's yet; not the dead or the lost."""
        st = self.state
        known = [n for n in self.m.npcs_at(loc_id) if n.id in st.npcs and st.npcs[n.id].met and n.status not in ("死亡", "失踪")]
        return tuple(self.m.pictures[n.portrait] for n in known if n.portrait in self.m.pictures)[: imagery.CAMEOS]

    def _known_here(self) -> None:
        """Someone came in, left or was first spoken with: the faces in the scene again, if they changed."""
        scene = self.query(SceneArt)
        if scene and self._faces(self.state.location) != scene.first().people:
            self._scene(self.state.location)


    def _showcase(self, ev: dict) -> None:
        """A big scene over the screen, when this screen shows them and the picture is here; not
        over a roll prompt, nor over another one."""
        cfg = config.load()
        picture = ev.get("picture", "")
        if not cfg.showcase or not imagery.available() or not picture or not Path(picture).is_file():
            return
        if isinstance(self.app.screen, (RollModal, PushModal, ShowcaseScreen)):
            return
        self.app.push_screen(ShowcaseScreen(picture, ev.get("title", ""), style=self.app.art_style, fade=cfg.animations))  # type: ignore[attr-defined]

    async def make_way(self) -> None:
        """A big scene steps aside for what asks the player something (a roll, a push)."""
        if isinstance(self.app.screen, ShowcaseScreen):
            await self.app.screen.dismiss()

    def _sky(self, loc_id: str) -> str:
        """The narrated weather, if this place is under the open sky."""
        return self.state.sky if weather.outdoors(self.m.location(loc_id).name) else ""

    def on_resize(self, event) -> None:
        right = self.query_one("#right")
        width = event.size.width
        right.display = width >= 96
        right.styles.width = 40 if width >= 130 else 34
        # The dice tray as tall as 0.0.2 had it (thirteen lines on a tall screen); shrunk to make room
        # for the history it came out too small to read (the user, 2026-10-21). The history and the
        # clues give way instead: both open in full (Ctrl+G, F2). Only a very short screen trims it.
        tray = self.query_one(DiceTray)
        height = event.size.height
        sheet = 14 if height >= 40 else 10 if height >= 34 else 6
        clues = 6 if height >= 48 else 4 if height >= 40 else 3
        spare = (height - 2) - sheet - clues - PAGES_MIN  # the top bar and the footer take a line each
        tray.styles.height = max(5, min(13 if height >= 40 else 12 if height >= 34 else 11, spare))
        self.query_one(SheetPanel).styles.max_height = sheet
        self.query_one(CluePanel).styles.max_height = clues
        if right.display:
            self.call_after_refresh(tray.prepare)
        self.query_one(SceneArt).refit()  # a taller terminal gets a taller picture
        self.query_one(Speaker).refit()
        self.call_after_refresh(self._panels)  # the sheet fits the new column; HP and SAN go up top without it

    def action_dice_skin(self) -> None:
        from ..drawing import dice

        names = list(dice.SKINS)
        now = self.app.dice_skin  # type: ignore[attr-defined]
        self.app.dice_skin = names[(names.index(now) + 1) % len(names)] if now in names else names[0]  # type: ignore[attr-defined]
        config.save_ui_settings({"dice": self.app.dice_skin})  # type: ignore[attr-defined]
        tray = self.query_one(DiceTray)
        tray.prepare()
        if tray._view is None:  # nothing rolled yet: a throw to look at the skin
            tray.roll(dice.preview())
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
        self.query_one(DiceTray).redraw()

    @staticmethod
    def _engine_label(cfg: config.Config) -> str:
        keeper = cfg.role("keeper")
        label = f"{cfg.preset} · 守秘人 {keeper.model.split(':', 1)[-1]}"
        label += f" · 备用 {keeper.fallback.split(':', 1)[-1]}" if keeper.fallback else ""
        return label + ("" if cfg.mode == "标准" else f" · {cfg.mode}")

    def action_engine(self) -> None:
        if self.busy:
            self.app.notify("本回合结束后可切换模型。", severity="warning")
            return
        cfg = config.load()
        from ..modals.settings import model_choices

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
            cfg = config.load(choice=choice)
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
        self.query_one(DiceTray).tumbling = on  # the dice tumble or only come to rest
        self.app.notify(f"天气动画：{'开' if on else '关'}")

    def _topbar(self) -> None:
        """Where and when on the left; the turn and what it cost on the right. Without the side
        column, the investigator's HP and SAN come up here."""
        st = self.state
        when = time_of_day(st.minutes)
        left = Text(no_wrap=True, overflow="ellipsis")
        left.append(" AUTOKEEPER ", style="bold reverse")
        if self.has_class("-dev"):
            left.append(" 开发者模式 ", style="bold reverse")
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
        for scene in self.query(SceneArt):  # none while the screen is being taken down
            scene.set_light(when)

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
            turn = self.state.turn + (kind == "player")  # an action is shown before its turn is counted
            self.query_one(HistoryPanel).note(line, block, turn)

    def _panels(self) -> None:
        st = self.state
        inv = self._viewer()
        tall = self.app.size.height >= 34  # room for a face beside the bars
        face = self._thumb(inv) if tall else None
        self.query_one(SheetPanel).show(inv, face, st.investigators)
        self.story.mark(st.investigators, self._me())
        self.query_one(CluePanel).show([self.m.clue(c).title for c in st.clues])
        lost = 1 - inv.san / max(1, inv.characteristics.get("意志", inv.san) or 1)
        for scene in self.query(SceneArt):  # none while the screen is being taken down (a face drawn late)
            scene.madness = max(0.0, min(0.6, lost * 2))
        self._pages()
        self._topbar()

    def _thumb(self, inv: Investigator) -> Text | None:
        """The side column's face. A picture goes through chafa in a thread (a game started, or
        Ctrl+A, used to wait for it); the sheet shows without it until it is in."""
        style = "ascii" if self.app.art_style == "ascii" else "photo"  # type: ignore[attr-defined]  # outlines are noise this small
        key = (inv.portrait, self.m.pictures.get(inv.portrait or ""), style)
        if key in self._thumbs:
            return self._thumbs[key]
        if not (key[1] and imagery.available()):
            return art_text(self.m, inv.portrait)  # hand-drawn, or nothing
        self._thumbs[key] = None
        self._draw_thumb(key)
        return None

    @work(thread=True, group="thumb", exit_on_error=False)  # a game left meanwhile: nobody to show it to
    def _draw_thumb(self, key: tuple) -> None:
        face = art_text(self.m, key[0], PORTRAIT, key[2], face=True)
        if self.is_attached:
            self.app.call_from_thread(self._thumb_drawn, key, face)

    def _thumb_drawn(self, key: tuple, face: Text) -> None:
        self._thumbs[key] = face
        if self.is_attached:
            self._panels()

    def _pages(self) -> None:
        """进展, 背景 and the plugins' page are read over the screen (action_page), made as it opens;
        the head over 经过 names a plugin's page while there is one."""
        shown = self.state.plugin_panels
        self.query_one(PagesHead).plugins(tab_label(shown) if shown else "")

    def action_page(self, name: str) -> None:
        """经过, 进展 and 背景 (and a rule plugin's page) as a page over the screen, `name` first."""
        if name == "progress":  # read: nothing new waits on the head
            self.query_one(PagesHead).fresh(False)
        history = [w.content for w in self.query_one(HistoryPanel).query(Static) if not w.has_class("history-head")]
        shown = self.state.plugin_panels
        extra = (tab_label(shown), panel_view(shown, self.app.current_theme)) if shown else None
        self.app.push_screen(PagesModal(history, progress_view(self.state), background_view(self.m, self.state, self._viewer()), name, extra))

    def action_recount(self) -> None:
        """Ctrl+Y: the story so far, written now (between turns, once a turn)."""
        if self.game is None or self._turn_running():
            return
        why = self.game.recount()
        self.app.notify(why or "正在整理进展", severity="warning" if why else "information", timeout=3)

    # -- event handling ----------------------------------------------------------------------
    async def handle(self, ev: dict) -> None:
        t = ev.get("type")
        if self.has_class("-dev"):
            self.call_after_refresh(self._dev)
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
            self.view = (ev["location"], ev.get("view", ""))
            self._scene(ev["location"])
        elif t == "view":  # another picture of the same place
            self.view = (ev["location"], ev["key"])
            if ev["location"] == self.state.location:
                self._scene(ev["location"])
        elif t == "people" and ev["location"] == self.state.location:  # someone came in or left
            self._known_here()
        elif t == "time":
            self._topbar()
        elif t == "sky":
            self.query_one(SceneArt).set_sky(self._sky(self.state.location))
        elif t == "improv":  # what the director added is told in the story, never announced over it
            self._topbar()
        elif t == "art":
            here = self.m.location(self.state.location)
            faces = {n.portrait for n in self.m.npcs_at(here.id)}
            if ev["key"] in (here.art, self.view[1]) or ev["key"] in faces:  # its picture, or a face in it, came or changed
                self._scene(self.state.location)
            for card in self.query(ItemCard):
                if card.art_key == ev["key"]:
                    card.redraw()
            self._topbar()
            if self.game and not self.busy:
                write_save(self.state, self.m)
        elif t == "showcase":  # a big scene: one picture over the screen, the story going on behind it
            self._showcase(ev)
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
        elif t == "plugin_effect":  # a rule did something public: a compact, recognisable line
            body = Text()
            body.append(f"⚙ 〔{ev['name']}〕", style="bold #8ec7d6")
            body.append(ev["line"])
            story.add("system", body)
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
            self._pages()  # the opening, once told, is on the 背景 page
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
            art = next((c.art for c in self.m.clues if c.id == ev["id"]), "")
            if art and art in self.m.pictures and imagery.available():  # the thing it was found in, photographed
                story.add("clue", ItemCard(self.m, {"id": ev["id"], "name": f"〔{ev['title']}〕", "text": ev["text"], "art": art},
                                           self.app.art_style))  # fmt: skip
            else:
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
            self.app.push_screen(HandoutModal(ev["title"], ev["text"], pictures, kind=ev.get("kind", "信件")))
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
            self._known_here()  # spoken with: they know who this is now
        elif t == "performance":
            self._npc_line(ev, "performance")
            self._known_here()
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
        elif t == "progress":  # the story so far, written again in the background: a mark on its tab, no notice
            self.query_one(PagesHead).fresh(True)
            if self.game and not self.busy:
                write_save(self.state, self.m)
        elif t == "epilogue_start":
            self.afterword, self.writing = "", True
        elif t == "epilogue_delta":
            self.afterword += ev["text"]
        elif t == "epilogue":
            self.afterword, self.writing = ev["text"], False
            if ev.get("error"):
                self.app.notify(ev["error"], severity="error", timeout=6)
        elif t == "plugin_panel":
            self._pages()
        elif t in ("plugin_show", "plugin_prompt", "plugin_closed", "plugin_answered"):
            self.plays.handle(ev)
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
    async def _prelude(self) -> None:
        """The opening performance plays while the director adapts the module to these
        investigators (screens/prelude.py); the keeper begins as soon as both are done."""
        assert self.game
        ready = asyncio.ensure_future(self.game.prepare_opening())
        if not self.spectate:
            animations = config.load().animations
            await self.app.push_screen_wait(PreludeScreen(self.m, ready, style=self.app.art_style,  # type: ignore[attr-defined]
                                                          animations=animations))
            self.game.prelude_played = True
        await ready

    @work(exclusive=True, group="turn")
    async def boot(self) -> None:
        assert self.game and self.cast
        self._busy(True)
        warm = asyncio.create_task(self._warm())
        try:
            if self.state.pending_turn.get("opening"):
                await self._prelude()
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
                await self._prelude()
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

    # -- the Esc menu --------------------------------------------------------------------------
    def menu_items(self) -> list[tuple[str, str]]:
        """What the Esc menu offers on this screen (a LAN table leaves some out, tui/screens/net.py);
        规则插件 only when this game has any."""
        return [item for item in MENU if (item[0] != "plugins" or self.m.plugins)
                and (item[0] != "developer" or self.game is not None)]

    def action_developer(self) -> None:
        """F12: 开发者模式 on or off. On, the top bar says so and the panel under the story shows this
        turn's calls, the roll, where everyone really is and what is due; the first time, the web
        page with the full records opens too."""
        if self.game is None:
            self.app.notify("完整排查资料位于运行游戏的本机，联机客户端无法读取。", timeout=5)
            return
        panel = self.query_one(DevPanel)
        panel.display = not panel.display
        self.set_class(panel.display, "-dev")
        self._topbar()
        if panel.display:
            self._dev()
            if not self.dev_url:
                self.open_developer()

    def _dev(self) -> None:
        self.query_one(DevPanel).show(self.game, self.dev_url, self.dev_errors)

    def action_open_dev_web(self) -> None:
        self.open_developer()

    @work(exclusive=True, group="developer-web")
    async def open_developer(self) -> None:
        from ...engine.developer_web import open_dashboard

        assert self.game is not None
        try:
            url = await open_dashboard(self.game, errors=self.dev_errors, busy=self.busy)
        except OSError as exc:
            self.app.notify(f"开发者网页启动失败：{type(exc).__name__}", severity="error", timeout=8)
            return
        self.dev_url = url
        self._dev()
        self.app.open_url(url)
        self.app.notify(f"开发者网页：{url}", timeout=10)

    def action_menu(self) -> None:
        if self.plays.skip() or self._unpick():  # Esc first ends a plugin's show, or closes the list over the box
            return
        first = "title" if self.state.ending else "continue"  # after the end, Esc and Enter go back to the title
        self.app.push_screen(GameMenu(self.menu_items(), first), self._menu_chosen)

    def _menu_chosen(self, choice: str | None) -> None:
        if choice in (None, "continue"):
            if not self.spectate:
                self.query_one("#command", Input).focus()
            return
        run = {"save": self.action_save, "load": self.action_load, "settings": self.action_settings,
               "prefs": self.action_prefs, "engine": self.action_engine,
               "guide": lambda: self.app.push_screen(GuideScreen("play")),
               "keys": lambda: self.app.push_screen(GuideScreen("keys")),
               "title": self.action_leave_game, "quit": self.action_quit_game}  # fmt: skip
        if choice in run:
            run[choice]()
        else:  # an action of this screen (开发者模式, 规则插件, the LAN screen's own): run_action is a coroutine
            self.call_later(self.run_action, choice)

    def action_settings(self) -> None:
        from ..modals.settings import SettingsModal

        style = self.app.art_style  # type: ignore[attr-defined]
        self.app.push_screen(SettingsModal(), lambda _: self._settings_saved(style))

    def _settings_saved(self, style: str) -> None:
        """What the settings change on this screen shows at once: the pictures, the weather."""
        if self.app.art_style != style:  # type: ignore[attr-defined]
            self._scene(self.state.location)
            self._panels()
            self.query_one(Speaker).refit()
        scene = self.query_one(SceneArt)
        if scene.effects != config.load().animations:
            scene.toggle_effects()

    def action_load(self) -> None:
        if self._turn_running():
            return
        if not self.spectate:
            write_save(self.state, self.m)
        self.app.push_screen(LoadScreen(playing=self), self.load_other)

    def load_other(self, chosen: tuple[Module, GameState] | None) -> None:
        """Another save, picked from 读档: this game is saved and makes way for it."""
        if chosen is None:
            return
        if not self.spectate:
            write_save(self.state, self.m)
        module, state = chosen
        self.app.switch_screen(GameScreen(module, state, resume=True, spectate=not state.human))

    def _leave(self) -> None:
        self.app.pop_screen() if len(self.app.screen_stack) > 1 else self.app.exit()

    def action_leave_game(self) -> None:
        self._away(self._leave)

    def action_quit_game(self) -> None:
        self._away(self.app.exit)

    def _away(self, go: Callable[[], None]) -> None:
        """Save, then go. While the keeper is still telling, ask first: wait for the end of it,
        or go now (the turn is saved as far as it got, and loading goes on with it)."""

        def now() -> None:
            if self.game is not None and not self.spectate:  # at a LAN table the host keeps the save
                write_save(self.state, self.m)
            go()

        if not self.busy:
            now()
            return

        def answer(choice: str | None) -> None:
            if choice == "wait":
                self._leave_when_ready = now
            elif choice == "now":
                now()

        self.app.push_screen(Confirm("当前叙述尚未结束", [("wait", "叙述结束后离开"), ("now", "立即离开"), ("no", "取消")]), answer)

    @on(Input.Submitted, "#command")
    def submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if self.busy and text and text.split()[0] not in ("/notes", "/map", "/sheet", "/help", "/theme", "/prefs", "/usage", "/undo"):
            if (not text.startswith("/") or text.startswith("/kp ")) and self.queued is None and not self.state.ending:
                self.queued = text  # typed ahead: it goes in as soon as the keeper is done
                event.input.value = ""
            else:  # the line stays in the box
                self.app.notify("已有待提交行动，可用 /undo 撤回。" if self.queued else "本回合尚未结束，请在叙述结束后提交。", timeout=3)
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
            if text.startswith("/kp "):
                self.play(text)
                return
            self._slash(text)
            return
        if self.state.ending and not commands.instant(text):  # growth rolls (.en) and the like still work
            self.app.notify("本局已结束。F10 查看复盘，.en 进行成长检定，F7 查看前情回顾，Esc 打开菜单。")
            return
        self.play(text)

    def _slash(self, text: str) -> None:
        cmd, *args = text.split()
        cmd = cmd.lower()
        actions = {"/draw": self.action_sketch, "/notes": self.action_notes, "/map": self.action_map, "/sheet": self.action_sheet,
                   "/help": self.action_help, "/recap": self.action_recap, "/save": self.action_save, "/debrief": self.action_debrief,
                   "/prefs": self.action_prefs, "/suggest": self.action_suggest, "/usage": self.action_usage}
        actions["/dev"] = self.action_developer
        if cmd == "/kp":  # "/kp <话>" goes to the keeper (send_line); alone it has nothing to say
            self.app.notify("在 /kp 后输入要对守秘人说的话。", timeout=4)
        elif cmd == "/retry":
            self.action_retry()
        elif cmd == "/undo":  # the action typed ahead goes back in the box, to change or drop
            if self.queued is None:
                self.app.notify("暂无可撤回的行动。")
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
        elif any(name == cmd[1:] for name, _ in plugin_commands(self.m, self.state)):
            self.plugin_command(cmd[1:], " ".join(args))
        else:
            self.app.notify(f"未知命令：{cmd}", severity="warning")

    @work(exclusive=True, group="turn")
    async def play(self, text: str) -> None:
        assert self.graph and self.game
        self.ideas.drop()  # whatever F5 was still thinking of, this action came first
        self._busy(True)
        inv = self.state.investigator
        self.story.add("system" if text.startswith("/kp ") else "player",
                       Text(f"▶ {inv.name} → KP：{text[4:]}" if text.startswith("/kp ") else f"▶ {inv.name}：{text}"))
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
        self.dev_errors.append(f"第 {self.state.turn} 轮\n{''.join(traceback.format_exception(exc))}")
        if self.game is not None and (journal := getattr(self.game, "debug_journal", None)) is not None:
            journal.append("error", {"source": "turn", "traceback": self.dev_errors[-1]}, turn=self.state.turn)
            journal.sync(self.game)
        del self.dev_errors[:-20]
        text = f"这一段暂未完成：{config.plain(exc)}。输入 /retry 接着讲，已掷出的结果会保留。"
        self.story.end_keeper("这一段暂时中断。")
        self.story.add("system", Text(text))
        self.app.notify(text, severity="warning", timeout=12)
        write_save(self.state, self.m)

    def action_retry(self) -> None:
        if not self.busy:
            if self.game is None or self.state.pending_turn:
                self.play("/retry")
            else:
                self.app.notify("当前无中断回合，可直接提交行动。")

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
                await self.make_way()
                choice = "roll" if auto else await self.app.push_screen_wait(RollModal(offer))
            elif auto:
                choice = "push" if offer.get("can_push") else "luck" if offer.get("luck") is not None else "drop"
                self.story.add("system", Text(f"◇ AI 选择：{ {'push': '孤注一掷', 'luck': '花幸运', 'drop': '放弃'}[choice] }", style="dim"))
            else:
                await self.make_way()
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
        self._status(f"{inv.name} 正在决定行动…")
        situation = self._situation()
        action = await self.cast.investigator(st, inv.id, situation)
        self.story.add("player", Text(f"▶ {inv.name}（AI）：{action}"))
        await self._run_turn({"input": action, "by_hand": False}, auto=True)

    def _situation(self) -> str:
        from ...engine.autoplay import situation

        return situation(self.m, self.state)

    @work(exclusive=True, group="turn")
    async def autoplay(self) -> None:
        """Watch mode: every round the AI investigators propose in parallel (LangGraph Send)."""
        from ...engine.autoplay import party_graph, situation

        assert self.cast
        st = self.state
        party = party_graph(self.cast, st)
        self._busy(True)
        while not st.ending and st.turn < 60 and self._leave_when_ready is None:
            alive = [inv.id for inv in st.investigators if inv.can_act]
            if not alive:
                break
            self._status("AI 调查员在商量…")
            result = await party.ainvoke({"situation": situation(self.m, st), "ids": alive, "proposals": []})
            for inv_id, text in result["proposals"]:
                if st.ending or self._leave_when_ready is not None:  # 讲完再走: after this one
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
        t.append("F10 复盘 · " + ("" if self.spectate else ".en 做成长检定 · ") + "F7 前情回顾 · Esc 菜单", style="dim")
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
            self.app.push_screen(HandoutModal(h.title, h.text, [self.m.pictures[k] for k in h.images if k in self.m.pictures], kind=h.kind))

    def action_sheet(self) -> None:
        from ..modals.sheet import SheetModal

        party, viewer = self.state.investigators, self._viewer()
        first = next((i for i, inv in enumerate(party) if inv.id == viewer.id), 0)
        style = self.app.art_style  # type: ignore[attr-defined]

        def portrait(inv: Investigator) -> Text:
            return face_text(self.m, inv.portrait, style)

        self.app.push_screen(SheetModal(party, first, portrait, self.state))

    def _suggester(self):
        from ..modals.plugins import action_hints

        slash = [f"/{name}" for name, _ in plugin_commands(self.m, self.state)]
        return command_suggester(self._viewer(), [*slash, *action_hints(self.m, self.state)])

    # -- rule plugins: their questions and slash commands (tui/widgets/plugin_ui.py) -------------------
    def prompt_is_mine(self, who: str) -> bool:
        """A plugin's question for this screen: the investigator a player plays here."""
        return not self.spectate and who in self.state.human

    def plugin_answer(self, pid: str, value, by: str) -> None:
        self._plugin("answer", pid, value, by)

    def plugin_command(self, name: str, text: str) -> None:
        if self._turn_running():
            return
        self._plugin("command", name, text)

    @work(group="plugin")
    async def _plugin(self, kind: str, *args) -> None:
        """An answer, a slash command or a timeout, outside the turn: what it brings is told
        with the next narration, and the rule's own lines show at once."""
        assert self.game
        self._busy(True)
        try:
            self.game.emit = self.handle
            plugins = self.game.plugins
            if kind == "answer":
                pid, value, by = args
                why = await plugins.answer(pid, value, by=by)
            elif kind == "command":
                why = await plugins.command(*args)
            else:
                why = ""
                await plugins.expire()
            if why:
                self.app.notify(why, severity="warning", timeout=4)
            write_save(self.state, self.m)
            self._panels()
        finally:
            self._busy(False)

    def _sync_suggester(self) -> None:
        for box in self.query("#command").results(Input):
            box.suggester = self._suggester()

    def action_plugins(self) -> None:
        from ..modals.plugins import PluginsModal

        self.app.push_screen(PluginsModal(self.m, self.state, manage=True, drafter=self._house_rule), self._plugins_changed)

    async def _house_rule(self, text: str, progress) -> tuple[bool, str]:
        """Draft, test and install a player's house rule into this save (tui/modals/plugins.py). The
        author uses the configured models; only a plugin whose tests pass in the sandbox lands."""
        from ...plugins.author import house_rule

        assert self.game is not None
        ok, message = await house_rule(self.game.cast, self.m, self.state, text, on_step=progress)
        if ok:
            write_save(self.state, self.m)
            self._sync_suggester()
            self.story.add("system", Text(f"◆ 新规则：{message}", style="bold"))
        return ok, message

    def _plugins_changed(self, _) -> None:
        write_save(self.state, self.m)
        self._sync_suggester()

    def action_usage(self) -> None:
        from ..modals.usage import UsageModal

        self.app.push_screen(UsageModal(self.state, self.engine_label))

    def action_map(self) -> None:
        self.app.push_screen(MapModal(self.m, self.state, go=not self.spectate), self._head_for)

    def _head_for(self, place: str | None) -> None:
        """G on the map: 「我去…」 in the command box, for the player to send or change."""
        box = self.query_one("#command", Input)
        if place and not self.spectate and not box.disabled:
            box.value = f"我去{place}"
            box.cursor_position = len(box.value)
            box.focus()

    def action_prefs(self) -> None:
        from ..modals.prefs import PrefsModal

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
        """The director finished a plan while nobody was mid-turn: play it now. A plugin's
        question nobody answered in time takes its default."""
        if self.game and not self.busy and self.game.ready():
            self._interlude()
        elif self.game and not self.busy and any(p["deadline"] <= time.time() for p in self.game.plugins.open_prompts()):
            self._plugin("expire")

    @work(exclusive=True, group="turn")
    async def _interlude(self) -> None:
        assert self.game
        self._busy(True)
        self._status("守秘人正在讲述…")
        self.game.emit = self.handle
        try:
            await self.game.interlude()
            write_save(self.state, self.m)
        except Exception as exc:
            self._turn_error(exc)
        finally:
            self._busy(False)

    def _turn_running(self) -> bool:
        """Starting another turn-group worker would cancel the running turn."""
        if self.busy:
            self.app.notify("本回合尚未结束，请稍后操作。", severity="warning", timeout=3)
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
        self.app.notify("已存档")

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
            self.app.notify("叙述已更新，按 F6 重新选择重述内容。", severity="warning", timeout=5)
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
            self.app.notify(f"重述失败（{why}），原叙述已保留。", severity="error", timeout=6)
        else:
            write_save(self.state, self.m)
        self._busy(False)

    def action_debrief(self) -> None:
        """F10, /debrief: once it is over, the whole book, and the epilogue on request."""
        if not self.state.ending:
            self.app.notify("本局结束后可查看复盘。", severity="warning", timeout=3)
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
