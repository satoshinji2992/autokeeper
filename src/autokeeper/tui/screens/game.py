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
from rich.cells import cell_len
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
from ...engine import chat, debrief, idea
from ...engine import graph as turn_graph
from ...engine.encounter import views
from ...engine.game import PLAY_WAIT, RETELL, Game, Told, ai_choice
from ...engine.state import GameState, time_of_day
from ...harness import models as channels
from ...module.schema import Module
from ...prefs import Prefs
from ...rules import commands
from ...rules.coc import Investigator, Rules
from .. import notes
from ..library import THEMES, art_text, face_text, write_save
from ..modals.debrief import DebriefModal
from ..modals.deck import DeckCardModal
from ..modals.dialogs import HandoutModal, PushModal, RollModal, SketchModal
from ..modals.engine import EngineModal
from ..modals.guide import GuideScreen
from ..modals.mapview import MapModal
from ..modals.menu import MENU, SETTINGS, Confirm, GameMenu
from ..modals.notebook import NotebookModal
from ..modals.plugins import plugin_commands
from ..modals.reaction import ChoiceModal
from ..modals.retell import RetellModal
from ..modals.showcase import ShowcaseScreen
from ..modals.suggest import Ideas
from ..widgets import tips
from ..widgets.devpanel import DevPanel
from ..widgets.fight import (
    Arena,
    BloodFlash,
    banner,
    chase_lines,
    fight_card,
    pulse_rate,
    title_frames,
)
from ..widgets.pages import Page, PagesHead, PagesModal, background_view, progress_view
from ..widgets.panels import (
    LIGHT_MARK,
    PORTRAIT,
    DiceTray,
    HistoryPanel,
    ItemCard,
    NotesPanel,
    RollCard,
    SceneArt,
    SheetPanel,
    StoryLog,
    command_suggester,
    history_line,
    log_line,
    skill_completions,
    speech,
)
from ..widgets.plugin_ui import Plays, panel_view, tab_label
from ..widgets.speaker import Speaker
from .prelude import PreludeScreen
from .saves import LoadScreen

COMMANDS = (
    ("帮助", "F1", "玩法说明和按键", "help"),
    ("笔记", "F2", "自己的笔记和收到的资料", "notes"),
    ("地图", "F3", "去过和听说过的地方", "map"),
    ("人物卡", "F4", "完整人物卡，←→ 切换调查员", "sheet"),
    ("行动建议", "F5", "根据当前情况生成三条行动建议", "suggest"),
    ("重讲上一段", "F6", "调整叙述方式，保留检定结果与对话", "retell"),
    ("前情回顾", "F7", "回顾本局已发生的事件", "recap"),
    ("经过", "", "行动与检定的记录；右栏也有", "page('history')"),
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
    ("规则插件", "Ctrl+K", "查看、暂停或启用本局额外规则", "plugins"),  # 开发者模式 only (check_action)
    ("回到标题", "", "保存进度并返回标题页", "leave_game"),
)




# Commands that open a window or write a note: they work while the keeper is telling (notes.JOT too).
ANYTIME = ("/notes", "/map", "/sheet", "/help", "/theme", "/prefs", "/usage", "/undo")
BATTLE_SECONDS, CHASE_SECONDS = 20.0, 12.0  # how long a fight's and a chase's round waits for the player (设置 turns it off)
FIGHT_EVENTS = ("fight_start", "reveal", "fight_round", "fight_act", "fight_state", "fight_say", "fight_end",
                "chase_start", "chase_round", "chase_state", "chase_end")
PAGES_MIN = 4  # the history's head and three lines; it opens in full with Ctrl+G
STORY_SHARE, STORY_MIN = 0.28, 8  # what the story keeps under a fight's or a chase's stage, the round's list open
PICKER_ROWS = (5, 8)  # the round's list, scrolling past that: the stage keeps the rest (13 rows of 36)
RISK_WORDS = {2: "可能被击倒", 3: "可能致命"}  # beside the answer to blows: how bad the worst blow in sight could be


def speech_line(who: str, to: str, text: str) -> Text:
    """An investigator's words to the party (engine/chat.py) in the story: the name, then the line."""
    return Text(f"▷ {who}{f' → {to}' if to else ''}：{text}")


class GameScreen(Screen):
    BINDINGS = [
        # Esc and F1–F5 on the footer, the rest in order of use; everything common is in the Esc menu
        Binding("escape", "menu", "菜单", key_display="Esc"),
        Binding("f1", "help", "帮助"),
        Binding("f2", "notes", "笔记"),
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
        Binding("ctrl+l", "idea", "灵感检定", show=False),
        Binding("ctrl+n", "fold", "收起画面", show=False),
    ]
    SHOW_ENGINE = False  # the model is in Ctrl+B and F9; a LAN table says whose turn it is up there

    def __init__(self, module: Module, state: GameState, *, resume: bool = False, spectate: bool = False):
        super().__init__()
        self.m, self.state, self.resume, self.spectate = module, state, resume, spectate
        roster.attach(module, state.investigators)  # public-figure cards bring their own photos
        self.cast: Cast | None = None
        self.game: Game | None = None
        self.graph = None
        self.thread = {"configurable": {"thread_id": state.slot}, "recursion_limit": 100}  # a fight asks a few times a turn (graph offer)
        self.met: set[str] = set()
        self.spinner = 0
        self.status_text = ""
        self._spun = ""
        self.engine_label = ""
        self.busy = False
        self._engine_after: dict | None = None  # a model switch asked for mid-turn, made once it ends
        self.dev_errors: list[str] = []
        self.dev_url = ""  # the developer web page, once started
        self._skill_choices: list[str] = []
        self._skill_at = -1
        self._skill_value = ""
        self.queued: str | None = None  # an action typed while the keeper told, to go in once they are done
        self.spoken: list[tuple[str, chat.Said]] = []  # words to the party, recorded once no turn runs (_heard)
        self._leave_when_ready: Callable[[], None] | None = None  # 讲完再走: where to go once the turn is over
        self.told: Static | None = None  # the story block of the latest narration, rewritten when it is told again
        self.afterword, self.writing = state.epilogue, False  # the epilogue as it comes in (engine/debrief.py)
        self.ideas = Ideas(self)  # F5 (tui/modals/suggest.py)
        self.plays = Plays(self)  # rule plugins' shows and prompts (tui/widgets/plugin_ui.py)
        self._thumbs: dict[tuple, Text | None] = {}  # side-column faces by (portrait, picture, style); None: being drawn
        self.view = (state.location, state.view)  # which picture of which place the scene shows (engine/views.py)
        # A fight's or a chase's choices over the input box (_offer_orders) and the clock on them (_start_clock)
        self._battle_picking, self._battle_title = False, ""  # the list is up; its title
        self._fold = 0  # Ctrl+N: 0 all shown, 1 the stage folded, 2 the round's list folded too
        self._clock = None  # the round's countdown while the list is up
        self._beating, self._beat_on = False, False  # a fight's heartbeat is going; the beat is on the stage now
        self._typing_until = 0.0  # the player is typing: the screen holds still until then (typing)
        self._carded: set[str] = set()  # this round's exchanges, each on its card: not again as a line
        self._clock_paused, self._clock_total, self._clock_left, self._clock_until, self._fallback = False, 0.0, 0.0, 0.0, ""

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                with Container(id="stage"):  # the scene, and over its right side whoever is talking
                    yield SceneArt(id="scene")
                    yield Speaker()
                    yield Arena()  # a fight or a chase in place of it (widgets/fight.py)
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
                    for name in ("progress", "background", "plugins"):  # turned to in place (PagesHead)
                        yield Page(id=f"side-{name}", classes="page side-page")
                yield NotesPanel(id="notes")
        yield Footer()

    async def on_mount(self) -> None:
        self._start_history([e for e in self.state.log])
        cfg = config.load()
        self.engine_label = self._engine_label(cfg)
        # Off the screen's thread: the model clients' first import and the world record's replay
        # took over a second, and the screen stood still that long.
        self.cast, self.game = await asyncio.to_thread(self._build, cfg)
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
        self.game.ask_reactions = "always" if cfg.ask_every_blow else "major"  # 设置: 每次被攻击都询问应对
        self._scene(self.state.location)
        self._panels()
        self.call_after_refresh(self._battle_resumed)
        self.set_interval(0.1, self._spin)
        self.set_interval(1.0, self._backstage)
        if self.spectate:
            self.query_one(Input).disabled = True
        self.boot()

    def _build(self, cfg) -> tuple[Cast, Game]:
        cast = Cast(cfg, self.m)
        return cast, Game(self.m, self.state, cast, Rules(), emit=self.handle)

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
        self._pause_clock()  # the round's choice does not run out behind help, the menu or a handout

    def on_screen_resume(self) -> None:
        self._resume_clock()
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
            self._close_orders()  # an order of their own, typed over the round's list
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
        if action == "complete_skill" and self._battle_picking:
            return True  # Tab over a fight's choices: the next answer to blows
        if action == "complete_skill":
            box = self.query_one("#command", Input)
            return box.has_focus and box.cursor_position == len(box.value) and bool(skill_completions(box.value, self._viewer()))
        if action == "plugins":  # 规则插件 only in 开发者模式; otherwise Ctrl+K is the input box's own
            return self.developing
        if action == "developer":  # F12 where the game runs on this machine (a LAN table: the host's own screen)
            return self.may_develop or self.developing  # on, it can always be turned off
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
        self._close_orders()
        picker.display = False
        self.query_one("#command", Input).focus()
        return True

    @on(OptionList.OptionSelected, "#picker")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        key = event.option.id or ""
        box = self.query_one("#command", Input)
        if self._battle_picking:  # a fight's or a chase's order: sent as picked
            self._unpick()
            if not self.busy:
                self.send_line(key)
            elif self.queued is None:  # (the list closes as a turn starts) as a line typed ahead would go
                self.queued = key
            return
        box.value = self._skill_value = key
        box.cursor_position = len(key)
        self._unpick()

    def action_complete_skill(self, step: int = 1) -> None:
        if self._battle_picking:
            self._next_stance(step)
            return
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
        if busy:
            self._close_orders()  # the round is being settled: its list is done with
        else:
            self.status_text = ""
            self._heard()
            if (choice := self._engine_after) is not None:  # the switch asked for while the turn ran
                self._engine_after = None
                self._apply_engine(choice)
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
                self.call_after_refresh(self._offer_orders)  # in a fight or a chase: this round's choices

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
        shown = ShowcaseScreen(picture, ev.get("title", ""), style=self.app.art_style, fade=cfg.animations)  # type: ignore[attr-defined]
        self.run_worker(self._over_all(shown), group="reveal")

    async def _over_all(self, shown: ShowcaseScreen) -> None:
        """A picture over the whole screen (a big scene, a face), once the blood flash has drained: one
        pushed over the flash was closed by the flash's going. Never over a roll prompt, a fight's question
        or another one."""
        await self._after_flash()
        if self.is_attached and not isinstance(self.app.screen, (RollModal, PushModal, ChoiceModal, ShowcaseScreen)):
            self.app.push_screen(shown)

    async def _after_flash(self) -> None:
        """Wait for the blood flash in front to drain (under half a second); one still up after a second goes."""
        for _ in range(20):
            if not isinstance(self.app.screen, BloodFlash):
                return
            await asyncio.sleep(0.05)
        if isinstance(flash := self.app.screen, BloodFlash) and (gone := flash.finish()) is not None:
            await gone

    def _dealt(self, card: dict) -> None:
        """A keeper card someone was dealt: a line in the story, and the card itself."""
        line = Text(f"▣ {card.get('who', '')} {card.get('kind', '')}〔{card.get('name', '')}〕", style="bold")
        self.story.add("sanity", line)
        self._flash_off()
        self.app.push_screen(DeckCardModal(card))

    def _flash_off(self) -> None:
        """A window coming up over the screen (a handout) ends the blood flash first."""
        if isinstance(flash := self.app.screen, BloodFlash):
            flash.finish()

    async def make_way(self) -> None:
        """A big scene, a face, the blood flash step aside for what asks the player something (a roll, a
        push), each through its own leaving: only the screen in front goes, and only once."""
        for _ in range(4):
            top = self.app.screen
            gone = top.leave() if isinstance(top, ShowcaseScreen) else top.finish() if isinstance(top, BloodFlash) else None
            if gone is None:
                return
            await gone

    def _sky(self, loc_id: str) -> str:
        """The narrated weather, if this place is under the open sky."""
        return self.state.sky if weather.outdoors(self.m.location(loc_id).name) else ""

    def on_resize(self, event) -> None:
        right = self.query_one("#right")
        width = event.size.width
        right.display = width >= 96
        # The dice tray as tall as 0.0.2 had it (thirteen lines on a tall screen); shrunk to make room
        # for the history it came out too small to read (the user, 2026-10-21). The history and the
        # notes give way instead: both open in full (Ctrl+G, F2). Only a very short screen trims it.
        tray = self.query_one(DiceTray)
        height = event.size.height
        sheet = 14 if height >= 40 else 10 if height >= 34 else 6
        jotted = 6 if height >= 48 else 4 if height >= 40 else 3  # the start of the notes
        spare = (height - 2) - sheet - jotted - PAGES_MIN  # the top bar and the footer take a line each
        tray.styles.height = max(5, min(13 if height >= 40 else 12 if height >= 34 else 11, spare))
        self.query_one(SheetPanel).styles.max_height = sheet
        self.query_one(NotesPanel).styles.max_height = jotted
        if right.display:
            self.call_after_refresh(tray.prepare)
        self.query_one(SceneArt).refit()  # a taller terminal gets a taller picture
        self.query_one(Speaker).refit()
        self.call_after_refresh(self._panels)  # the sheet fits the new column; HP and SAN go up top without it
        self._fit_stage()  # a fight's stage and list in what the new size leaves them

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
        if not choice:
            return
        if self.busy:  # a turn began while the window was open: never cut it short, switch once it is told
            self._engine_after = choice
            self.app.notify("模型将在本回合结束后切换。", timeout=4)
            return
        self._apply_engine(choice)

    @work(exclusive=True, group="engine")  # never the turn's group: an exclusive worker there cancels the turn
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
        self._animations(on)
        self.app.notify(f"天气动画：{'开' if on else '关'}")

    def _moving(self) -> bool:
        """设置's 天气与场景动画: off, nothing on the screen moves of itself (the weather, the stage's
        picture, the lurches, the heartbeat, the title's glitch)."""
        return self.query_one(SceneArt).effects

    def _animations(self, on: bool) -> None:
        """Animations switched on or off (Ctrl+P's 天气动画, 设置): at once, not at the next blow."""
        self.query_one(DiceTray).tumbling = on  # the dice tumble or only come to rest
        self.query_one(Arena).canvas.moving = on

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
        now = self._battle_now()
        if now is not None and now["kind"] == "fight":
            left.append(f"   ⚔ 战斗 · 第 {max(1, now['round'])} 轮 ", style="bold #ff4d4d")
            inv = self._viewer()
            beat = "♡" if self._beat_on else "♥"
            left.append(f"{beat} {inv.hp}/{inv.hp_max}", style="bold #c1121f" if inv.hp * 3 <= inv.hp_max else "bold #ffb000")
        elif now is not None:
            left.append(f"   ⟫ 追逐 · 第 {max(1, now['round'])} 轮", style="bold #9fb4c8")
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
            said = isinstance(body, Text) and body.plain.startswith("▷")  # words to the party take no turn
            turn = self.state.turn + (kind == "player" and not said)  # an action is shown before its turn is counted
            self.query_one(HistoryPanel).note(line, block, turn)

    def _panels(self) -> None:
        st = self.state
        inv = self._viewer()
        tall = self.app.size.height >= 34  # room for a face beside the bars
        face = self._thumb(inv) if tall else None
        self.query_one(SheetPanel).show(inv, face, st.investigators)
        self.story.mark(st.investigators, self._me())
        self.query_one(NotesPanel).show(self.notes())
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
        head = self.query_one(PagesHead)
        head.plugins(tab_label(shown) if shown else "")
        if head.active == "plugins" and not shown:
            self.action_side_page("history")
        self._side_fill()

    def action_side_page(self, name: str) -> None:
        """The right column turned to `name` (a tab of PagesHead): 经过, 进展, 背景 or a plugin's page."""
        head = self.query_one(PagesHead)
        head.turn(name)
        self.query_one(HistoryPanel).display = name == "history"
        for page in self.query(".side-page").results(Page):
            page.display = page.id == f"side-{name}"
        self._side_fill()

    def _side_fill(self) -> None:
        """The column's page, written again from the game as it is now (when one is showing)."""
        name = self.query_one(PagesHead).active
        if name == "history":
            return
        page = self.query_one(f"#side-{name}", Page)
        if name == "progress":
            page.show(progress_view(self.state))
        elif name == "background":
            page.show(background_view(self.m, self.state, self._viewer()))
        elif shown := self.state.plugin_panels:
            page.show(panel_view(shown, self.app.current_theme))

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
        elif t == "speech":  # a teammate's words to the party, at a shared table (net/hub.Hub._speak)
            who = f"{ev['who']}（{ev['player']}）" if ev.get("player") else ev["who"]
            story.add("player", speech_line(who, ev.get("to", ""), ev["text"]))
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
            if not ev.get("fighter"):  # a fight's or a chase's wound is on its card or in its line already
                story.add("system", Text(f"✚ {ev['who']} {ev['text']}", style="bold red"))
            self._panels()
        elif t == "plugin_status":
            self.app.notify("一项扩展规则已暂停。" + ("可按 Ctrl+K 查看。" if self.developing else ""), severity="warning")
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
        elif t == "held":  # a LAN round: the others' actions first, then one telling of them all
            story.add("system", Text("本轮其他调查员的行动结算后，守秘人一并讲述。", style="dim"))
            self._status("")
        elif t in FIGHT_EVENTS:
            self._battle_event(ev)
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
        elif t == "idea_offer":  # the game stalled: the keeper offers an Idea roll (engine/idea.py)
            story.add("system", Text(f"◇ {ev['text']}", style="bold"))
        # A clue found ("clue") shows nothing: the players hear it in the story, in the narration or
        # someone's words, and keep their own notes (the user, 2026-10-22: no summary handed to them).
        elif t == "handout":
            story.add("handout", Text(f"▤ 手稿：{ev['title']}（F2 可重看）", style="bold"))
            handout = next((h for h in self.m.handouts if h.id == ev.get("id")), None)
            pictures = self.m.handout_pictures(handout) if handout else []
            self._flash_off()
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
            if ev.get("card"):  # a phobia, a mania or a bout drawn from the imported deck (cards.py)
                self._dealt(ev["card"])
        elif t == "card":  # a mishap the arbiter dealt
            self._dealt(ev)
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
            self._battle_over()  # over in the middle of a fight or a chase: the stage goes back to the scene
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
            if self.query_one(PagesHead).active == "progress":
                self._side_fill()  # being read: it changes in place
            else:
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

    # -- a fight and a chase on the stage (widgets/fight.py) ----------------------------------
    def _battle_event(self, ev: dict) -> None:
        t = ev["type"]
        story, scene, arena = self.story, self.query_one(SceneArt), self.query_one(Arena)
        style = getattr(self.app, "art_style", "photo")
        arena.canvas.moving = scene.effects
        if t == "fight_start":
            self.query_one(Speaker).leave()
            scene.display = False  # the stage is the fight's: whoever they face, as large as it holds
            arena.show(ev["fight"], self.m, style, rows=self._stage_rows()[0])
            self.add_class("-fight")
            self.remove_class("-chase")
            story.add("event", banner(ev.get("line", "").replace("【", "").replace("】", " · ") or "战斗开始"))
            self._title("战斗")
            self._heartbeat()
            if not self.busy:
                self.call_after_refresh(self._offer_orders)
        elif t == "reveal":  # a creature first seen: over the whole screen, before the sanity roll
            self.run_worker(self._reveal(ev.get("portrait", ""), ev.get("name", ""), 3.5), group="reveal")
        elif t == "fight_round":
            self._carded.clear()
            arena.round_begins(ev["order"], ev["fight"])
            story.add("system", Text(f"── 第 {ev['round']} 轮 ──", style="#7a0a0a", justify="center"))
        elif t == "fight_act":
            story.add("fightcard", fight_card(ev))
            self._carded.add(ev.get("line", ""))
            arena.act(ev)
            if ev.get("dice"):
                self.query_one(DiceTray).roll(ev["dice"])
            if ev.get("damage") and ev.get("winner") != "none" and ev.get("hurt_side") == "party":
                self._wound(ev.get("hurt", ""), ev["damage"])
        elif t == "fight_state":
            arena.show(ev["fight"], self.m, style)
            if lines := [line for line in ev.get("lines") or [] if line not in self._carded]:  # not the exchange again
                story.add("system", Text("\n".join(f"· {line}" for line in lines), style="#b39b8a"))
            self._panels()
        elif t == "fight_say":
            body = Text()
            body.append(f"{ev['name']}\n", style="bold")
            body.append(ev["text"])
            story.add("npc", body)
        elif t == "fight_end":
            self._close_orders()
            self.remove_class("-fight")
            self._beat_off()
            if (now := self._battle_now()) is None or now["kind"] != "chase":
                arena.hide()
                scene.display = True
            story.add("event", banner(ev.get("text", "战斗结束"), colour="#e8dec4"))
            self._panels()
        elif t == "chase_start":
            self.query_one(Speaker).leave()
            scene.display = False  # the place is on the stage still, rushing, and what is behind you in it
            arena.show_chase(ev["chase"], self.m, style, rows=self._stage_rows()[0], scene=scene.image or "")
            self.add_class("-chase")
            self.remove_class("-fight")
            story.add("event", banner((ev.get("line") or "追逐开始").replace("【", "").replace("】", " · "), colour="#9fb4c8"))
            if ev.get("speeds"):
                story.add("system", chase_lines(ev["speeds"]))
            self._title("追逐", colour="#9fb4c8")
            self._heartbeat()
            if not self.busy:
                self.call_after_refresh(self._offer_orders)
        elif t in ("chase_round", "chase_state"):
            arena.show_chase(ev["chase"], self.m, style, scene=scene.image or "")
            lines = ev.get("lines") or []
            if lines:
                story.add("system", chase_lines(lines))
            if any(w in line for line in lines for w in ("栽", "挡住", "受了")):
                self._jolt(3)
            elif t == "chase_round" or lines:
                self._jolt(1)  # every stretch run: the picture jolts
            self._panels()
        elif t == "chase_end":
            self._close_orders()
            self.remove_class("-chase")
            self._beat_off()
            if (now := self._battle_now()) is None or now["kind"] != "fight":
                arena.hide()
                scene.display = True
            if ev.get("end") == "caught":  # it is on you: its face over everything, and blood
                self.run_worker(self._reveal(ev.get("portrait", ""), f"{ev.get('hunter_name', '')}追上来了", 2.2), group="reveal")
                self._flash(0.8)
            story.add("event", banner(ev.get("text", "追逐结束"), colour="#e8dec4"))
        self._topbar()

    def _stage_rows(self) -> tuple[int, int]:
        """A fight's or a chase's stage and the round's list over the box, in rows: what is left once the
        top bar, the footer, the status line and the input box have theirs (at a LAN table its turn
        line, with F12 the developer panel) and the story its share to read in (STORY_SHARE of the
        screen, STORY_MIN rows at least: 10 of 36, 8 of 30). The list takes a third of the rest, PICKER_ROWS,
        and scrolls past that; the stage the remainder, 4 rows at least, so on a short terminal the box
        stays on the screen (80x24 left the story one row and the box below the bottom). The user, 2026-10-22:
        the stage of a fight or a chase wants more room than the review's first share gave it (8 rows of 36)."""
        height = self.app.size.height
        fixed = 2 + 1 + 3 + 1 + len(self.query("#turnline"))  # bars, status, box, the stage's edge; LAN's turn line
        fixed += sum(9 for panel in self.query(DevPanel) if panel.display)
        rest = height - fixed
        story = max(0, min(max(STORY_MIN, round(height * STORY_SHARE)), rest - 9))
        picker = max(PICKER_ROWS[0], min(PICKER_ROWS[1], (rest - story) // 3))
        return max(4, min(32, rest - story - picker)), picker

    def _fit_stage(self) -> None:
        """The stage and the list as tall as _stage_rows has them: on a resize, a fight or a chase
        beginning, the developer panel opening. The round's list is written again for the new width."""
        rows, picker = self._stage_rows()
        for options in self.query("#picker").results(OptionList):
            options.styles.max_height = picker
        for arena in self.query(Arena).results(Arena):
            if arena.display:
                arena.styles.height = rows
        if self._battle_picking:
            self.call_after_refresh(self._offer_orders)

    async def _reveal(self, portrait: str, caption: str, seconds: float) -> None:
        """A face over the whole screen: a creature first seen, the thing that caught you (after the
        blood it drew: _over_all)."""
        path = self.m.pictures.get(portrait or "", "")
        if not path or not config.load().showcase:  # 设置's 大场景 off: no picture over the whole screen
            return
        if not Path(path).is_file():
            return
        await self._over_all(ShowcaseScreen(path, caption, style=self.app.art_style, fade=False, face=True, seconds=seconds))  # type: ignore[attr-defined]

    def _wound(self, who: str, damage: int) -> None:
        """An investigator hurt: blood floods the screen, the screen lurches, harder the worse the blow."""
        inv = next((i for i in self.state.investigators if i.id == who), None)
        share = damage / max(1, inv.hp_max) if inv is not None else 0.3
        self._flash(min(1.0, share * 2))
        self._jolt(2 + int(share * 6))

    def _flash(self, strength: float) -> None:
        if self.app.screen is self and self.query_one(SceneArt).effects:
            self.app.push_screen(BloodFlash(strength))
        else:
            self.add_class("-hit")
            self.set_timer(0.25, lambda: self.remove_class("-hit"))

    def _jolt(self, strength: int = 2) -> None:
        """The screen lurches: the whole of it when a blow lands, the stage when a step goes wrong
        (not with animations off)."""
        if not self._moving():
            return
        main = self.query_one("#main")
        amp = max(1, min(4, strength))
        seq = iter([(amp, 0), (-amp, 1), (amp - 1, -1), (-(amp - 1), 0), (0, 0)])

        def step() -> None:
            try:
                main.styles.offset = next(seq)
                self.set_timer(0.04, step)
            except StopIteration:
                pass

        step()

    def action_fold(self) -> None:
        """Ctrl+N, for room to read: the stage folds away (the scene, a fight's or a chase's stage); in a
        fight or a chase, pressed again, the round's list too (orders can still be typed); again, both
        come back."""
        if self._fold == 0:
            self._fold = 1
        elif self._fold == 1 and self._battle_picking:
            self._fold = 2
            self._unpick()
        else:
            self._fold = 0
        self.set_class(self._fold >= 1, "-folded")
        self.app.notify({0: "画面已展开。", 1: "画面已收起，Ctrl+N 展开。", 2: "画面与选项已收起，Ctrl+N 展开。"}[self._fold], timeout=2)
        if self._fold == 0:
            self.call_after_refresh(self._offer_orders)

    def _battle_over(self) -> None:
        """No fight or chase on the stage any more, whatever the state still holds (the game ended in
        the middle of one): the list, the clock and the heartbeat stop, the scene comes back."""
        self._fold = min(self._fold, 1)  # the next fight's list comes up
        self._close_orders()
        self.remove_class("-fight", "-chase")
        self._beat_off()
        arena = self.query_one(Arena)
        if arena.display:
            arena.hide()
            self.query_one(SceneArt).display = True

    def _battle_now(self) -> dict | None:
        """The fight or the chase going on as the players' screens hold it, with each investigator's
        choices this round (engine/encounter/views.table); None outside one."""
        if self.game is None or self.state.ending:
            return None
        return views.table(self.game)

    def _in_battle(self) -> bool:
        """Cheaper than _battle_now for the heartbeat: whether a fight or a chase is going on."""
        return self.game is not None and not self.state.ending and self.game.battling()

    def _battle_resumed(self) -> None:
        """A save opened in the middle of a fight or a chase: the stage as it stood (not after the end)."""
        now = self._battle_now()
        if now is None:
            return
        if now["kind"] == "fight":
            self._battle_event({"type": "fight_start", "fight": now["fight"], "line": "战斗继续"})
        else:
            self._battle_event({"type": "chase_start", "chase": now["chase"], "line": "追逐继续"})

    def _title(self, word: str, colour: str = "#c1121f") -> None:
        """The word across the stage as a fight or a chase begins, glitching in and fading (with
        animations off, standing a moment and gone)."""
        stage = self.query_one("#stage")
        width = stage.size.width or 80
        title = Static(classes="battle-title")
        stage.mount(title)
        frames = title_frames(word, width)
        if not self._moving():
            title.update(frames[-1])
            self.set_timer(1.2, title.remove)
            return

        def play(k: int = 0) -> None:
            if not title.is_attached:
                return
            if k < len(frames):
                title.update(frames[k])
                self.set_timer(0.07, lambda: play(k + 1))
            else:
                title.styles.animate("opacity", 0.0, duration=0.9, on_complete=title.remove)

        play()

    def _heartbeat(self) -> None:
        """While a fight or a chase is on, a heartbeat on the top bar's heart and the stage's edge,
        quicker the worse the investigator is hurt. Only the stage's own class changes: on the whole
        screen it restyled some three hundred widgets a beat. It stands still with animations off and
        while the player types."""
        if self._beating:
            return
        self._beating = True

        def beat(on: bool = True) -> None:
            st = self.state
            if not self.is_attached or st.ending or not self._in_battle():
                self._beating = False
                self._beat_off()
                return
            lit = on and self._moving() and time.monotonic() >= self._typing_until
            if lit != self._beat_on:
                self._beat_on = lit
                for stage in self.query("#stage"):
                    stage.set_class(lit, "-beat")
                self._topbar()
            inv = self._viewer()
            self.set_timer(0.12 if on else pulse_rate(inv.hp, inv.hp_max), lambda: beat(not on))

        beat()

    def _beat_off(self) -> None:
        self._beat_on = False
        for stage in self.query("#stage"):
            stage.remove_class("-beat")

    # -- the round's choices over the input box, on a clock -------------------------------------
    def _may_order(self) -> bool:
        """Whether the round's choices may come up now: between turns (at a LAN table, while the round
        is open to this seat and nothing of theirs is in it yet: tui/screens/net.py)."""
        return not self.busy

    def _next_stance(self, step: int) -> None:
        """Tab over a fight's choices: the next way of answering blows (dodge, fight back, cover when
        someone has a gun), set in the engine at once (Encounters.set_react: no action spent, no model
        asked) and shown once under the list. The choices stay as they are; the clock goes on."""
        now = self._battle_now()
        me = self._viewer().id
        if now is None or now["kind"] != "fight" or me not in now["choices"] or self.busy or not self._battle_picking:
            return
        ways = list(now.get("answers", {}).get(me) or [])
        if not ways:
            return
        current = now["presets"].get(me, "dodge")
        self._set_react(ways[(ways.index(current) + step) % len(ways) if current in ways else 0])

    def _set_react(self, react: str) -> None:
        """The answer to blows set for this investigator (a LAN table asks the host: tui/screens/net.py)."""
        if self.game is None:
            return
        if why := self.game.set_react(self._viewer().id, react):
            self.app.notify(f"{why}。", severity="warning", timeout=3)
            return
        write_save(self.state, self.m)
        self._react_shown()

    def _react_shown(self) -> None:
        """The answer to blows under the round's list, and on the stage's column, as the fight now has it."""
        now = self._battle_now()
        if now is None or now["kind"] != "fight":
            return
        for picker in self.query("#picker").results(OptionList):
            if self._battle_picking:
                picker.border_subtitle = self._stance_line(now)
        arena = self.query_one(Arena)
        if arena.display and arena.mode == "fight":
            arena.show(now["fight"], self.m, self.app.art_style)  # type: ignore[attr-defined]

    def _stance_line(self, now: dict) -> str:
        """「被攻击时：闪避 · Tab 切换」, once, under a fight's list; with how bad the worst blow could be."""
        me = self._viewer().id
        if now["kind"] != "fight" or me not in now.get("presets", {}):
            return ""
        from ...engine.encounter.choices import STANCE_WORDS

        word = STANCE_WORDS.get(now["presets"][me], "闪避")
        line = f"被攻击时：[b]{word}[/b]" + (" · Tab 切换" if len(now.get("answers", {}).get(me) or []) > 1 else "")
        risk = RISK_WORDS.get(now.get("risk", {}).get(me, 0), "")
        return line + (f" · [b $error]⚠ {risk}[/]" if risk else "")

    def _order_rows(self, rows: list[tuple[str, str, str]]) -> list[Option]:
        """The round's choices as one row each, the actions in a column and their rolls after them in
        grey, cut short with an ellipsis where the list is too narrow, never wrapped."""
        width = max(16, (self.query_one("#left").size.width or self.app.size.width) - 4)  # the border and the padding
        column = min(max((cell_len(label) for _, label, _ in rows), default=0), max(8, width - 10))
        out = []
        for words, label, note in rows:
            line = Text(no_wrap=True, overflow="ellipsis", end="")
            line.append(label, style="bold")
            if note:
                line.append(" " * max(2, column - cell_len(label) + 2))
                line.append(note, style="dim")
            out.append(Option(line, id=words))
        return out

    def _offer_orders(self) -> None:
        """In a fight or a chase, the investigator's choices for the round over the input box; not once
        the game has ended in the middle of one. Someone who cannot act in a fight is offered to let the
        round go on (继续): those who can still act do."""
        now = self._battle_now()
        if now is None:
            self._close_orders()
            return
        if self.spectate or not self.is_attached or not self._may_order():
            return
        me = self._viewer().id
        lists = now["choices"].get(me)
        fighting = now["kind"] == "fight"
        if not lists:
            if fighting and self.game is not None and getattr(self, "link", None) is None:
                choices = [("/encounter", "继续", "本轮无法行动，由其他人继续")]
                title = "战斗 · 回车继续"
            else:
                return
        else:
            choices = [(words, words, note) for words, note in lists]
            title = "战斗行动 · 回车选定 · Esc 手动输入" if fighting else "追逐行动 · 回车选定 · Esc 手动输入"
        if not choices or self._fold >= 2:  # Ctrl+N folded the list: it comes back with the next Ctrl+N
            return
        picker = self.query_one("#picker", OptionList)
        at = picker.highlighted if self._battle_picking else None  # the same round written again (a resize, Tab): the same row
        picker.clear_options()
        picker.add_options(self._order_rows(choices))
        picker.highlighted = min(at or 0, picker.option_count - 1)
        picker.border_title = title
        picker.border_subtitle = self._stance_line(now) if lists else ""
        picker.display = True
        picker.focus()
        self._battle_picking, self._battle_title = True, title
        if self._clock is not None:  # Tab: the same round, the same deadline
            self._clock_tick()
        elif config.load().battle_timer and getattr(self, "link", None) is None and lists:  # a LAN round has its own window
            hold = next((w for w, _, _ in choices if w.startswith("防守")), "") if fighting else choices[0][0]
            if hold:
                self._start_clock(BATTLE_SECONDS if fighting else CHASE_SECONDS, hold)

    def _close_orders(self) -> None:
        """The round's list over the box goes, and its clock with it: the player writes their own order
        (typing, Esc), the turn is under way, the fight or the chase is over. A list left up after a
        fight sent its first line as the next action."""
        self._stop_clock()
        if self._battle_picking:
            self._battle_picking = False
            for picker in self.query("#picker").results(OptionList):
                picker.display = False
                picker.remove_class("-urgent")

    def _stop_clock(self) -> None:
        if self._clock is not None:
            self._clock.stop()
            self._clock = None
        self._clock_paused = False

    def _start_clock(self, seconds: float, fallback: str) -> None:
        """The round's choice waits only so long: a draining bar on the list, then the investigator does
        only `fallback` (defends; in a chase, runs flat out). It stands still while another window is
        over the screen (help, the menu, a handout: on_screen_suspend) and goes on from where it was."""
        self._stop_clock()
        self._clock_total, self._clock_left, self._fallback = seconds, seconds, fallback
        self._clock_until = time.monotonic() + seconds
        self._clock = self.set_interval(0.25, self._clock_tick)
        if self.is_active:
            self._clock_tick()
        else:  # offered while a window was up: it waits for the screen
            self._pause_clock()

    def _pause_clock(self) -> None:
        if self._clock is not None and not self._clock_paused:
            self._clock_left = max(0.0, self._clock_until - time.monotonic())
            self._clock.pause()
            self._clock_paused = True

    def _resume_clock(self) -> None:
        if self._clock is not None and self._clock_paused:
            self._clock_until = time.monotonic() + self._clock_left
            self._clock_paused = False
            self._clock.resume()

    def _clock_tick(self) -> None:
        now = self._battle_now()
        if self.busy or not self.is_attached or not self._battle_picking or now is None:
            self._stop_clock()
            return
        if self._clock_paused:
            return
        left = self._clock_until - time.monotonic()
        share = max(0.0, left / max(0.1, self._clock_total))
        bar = "█" * round(12 * share) + "░" * (12 - round(12 * share))
        picker = self.query_one("#picker", OptionList)
        picker.border_title = f"{self._battle_title} · ⏳ {bar} {max(0, int(left + 0.99))} 秒"
        picker.set_class(left <= 5, "-urgent")
        if left <= 0:
            fallback, fighting = self._fallback, now["kind"] == "fight"
            self._close_orders()
            self.query_one("#command", Input).focus()
            self.app.notify(f"未在时限内选择，本轮改为{'防守' if fighting else fallback}。", severity="warning", timeout=4)
            self.send_line(fallback)

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
        body.append_text(speech(ev["text"]))
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
                    if entry.turn == self.state.turn and entry.kind in ("dice", "npc", "event"):
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
        """What the Esc menu offers on this screen (a LAN table leaves some out, tui/screens/net.py)."""
        return list(MENU)

    def settings_items(self) -> list[tuple[str, str]]:
        """What 设置 opens on this screen besides its switches (SETTINGS): 开发者模式 needs the
        game on this machine."""
        return [item for item in SETTINGS if item[0] != "developer" or self.may_develop]

    @property
    def may_develop(self) -> bool:
        """开发者模式 can be opened here: the game runs on this machine (at a LAN table, only the host's
        own screen, tui/screens/net.py)."""
        return self.game is not None

    @property
    def developing(self) -> bool:
        """开发者模式 is on: the panel under the story shows, and the rule plugins can be opened."""
        return any(panel.display for panel in self.query(DevPanel))

    def action_developer(self) -> None:
        """F12: 开发者模式 on or off. On, the top bar says so and the panel under the story shows this
        turn's calls, the roll, where everyone really is and what is due; the first time, the web
        page with the full records opens too."""
        if not (self.may_develop or self.developing):
            return
        panel = self.query_one(DevPanel)
        panel.display = not panel.display
        self._fit_stage()
        self.set_class(panel.display, "-dev")
        self._topbar()
        self.refresh_bindings()  # Ctrl+K comes and goes with it
        if panel.display:
            self._dev()
            if not self.dev_url:
                self.open_developer()

    def _dev(self) -> None:
        self.query_one(DevPanel).show(self.game, self.dev_url, self.dev_errors)

    def action_open_dev_web(self) -> None:
        self.open_developer()

    async def _dashboard(self) -> str:
        """The developer web page on this game, started once; its URL."""
        from ...engine.developer_web import open_dashboard

        assert self.game is not None
        return await open_dashboard(self.game, errors=self.dev_errors, busy=self.busy)

    @work(exclusive=True, group="developer-web")
    async def open_developer(self) -> None:
        try:
            url = await self._dashboard()
        except (OSError, RuntimeError) as exc:  # RuntimeError: a LAN room that closed meanwhile
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
               "guide": lambda: self.app.push_screen(GuideScreen("play")),
               "title": self.action_leave_game, "quit": self.action_quit_game}  # fmt: skip
        if choice in run:
            run[choice]()
        else:  # an action of this screen (the LAN screen's 关闭房间): run_action is a coroutine
            self.call_later(self.run_action, choice)

    def action_settings(self) -> None:
        from ..modals.settings import SettingsModal

        style = self.app.art_style  # type: ignore[attr-defined]
        self.app.push_screen(SettingsModal(self), lambda _: self._settings_saved(style))

    def _settings_saved(self, style: str) -> None:
        """What the settings change on this screen shows at once: the pictures, the weather."""
        if self.app.art_style != style:  # type: ignore[attr-defined]
            self._scene(self.state.location)
            self._panels()
            self.query_one(Speaker).refit()
        cfg = config.load()
        scene = self.query_one(SceneArt)
        if scene.effects != cfg.animations:
            scene.toggle_effects()
            self._animations(cfg.animations)
        if not cfg.battle_timer and self._clock is not None:  # 限时选择 off: the round's list waits as long as it takes
            self._stop_clock()
            picker = self.query_one("#picker", OptionList)
            picker.border_title = self._battle_title
            picker.remove_class("-urgent")

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
        if text:
            self._close_orders()  # their own order instead of the round's list
        talk = chat.ooc(text) is not None or self._said(text) is not None  # shown at once, never queued as an action
        if self.busy and text and not talk and text.split()[0] not in ANYTIME and not text.startswith(notes.JOT):
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
    def typing(self, event: Input.Changed) -> None:
        # Every weather frame moves the terminal cursor across the screen and back; on terminals
        # without synchronized output that makes the caret and the IME box jump while typing.
        self.query_one(SceneArt).quiet(4.0)
        self.query_one(Arena).quiet(4.0)  # in a fight the stage is the picture that moves
        self._typing_until = time.monotonic() + 4.0  # and the heartbeat
        if event.value and self._battle_picking:  # writing their own order, in the box the list was over
            self._close_orders()

    def send_line(self, text: str) -> None:
        """One line from the input box: out-of-character chat, words to the party, a slash command,
        or the investigator's action (engine/chat.py)."""
        if (aside := chat.ooc(text)) is not None:  # nobody else at this table: shown, never recorded
            if aside:
                self.story.add("system", Text(f"【场外】{aside}", style="italic dim"))
            return
        if text.startswith("/"):
            if text.startswith("/kp "):
                self.play(text)
                return
            self._slash(text)
            return
        if self.state.ending and not commands.instant(text):  # growth rolls (.en) and the like still work
            self.app.notify("本局已结束。F10 查看复盘，.en 进行成长检定，F7 查看前情回顾，Esc 打开菜单。")
            return
        if (said := self._said(text)) is not None:
            self.speak(said)
            return
        self.play(text)

    def _speaker(self):
        """Who says what is typed: the investigator you play, else whoever is acting."""
        me = self._me()
        return next((i for i in self.state.investigators if i.id == me), self.state.investigator)

    def _said(self, text: str) -> chat.Said | None:
        """The words, if the line is said to the party and someone of it is here to hear them."""
        if self.spectate or self.game is None or self.state.ending:
            return None
        me = self._speaker()
        return chat.said(text, [i.name for i in self.state.investigators if i.id != me.id])

    def speak(self, said: chat.Said) -> None:
        """Words to the party: shown at once, recorded when no turn runs. No keeper, no action;
        the AI companions read them in their next situation (engine/autoplay.situation)."""
        inv = self._speaker()
        self.story.add("player", speech_line(inv.name, said.to, said.line))
        self.spoken.append((inv.id, said))
        if not self.busy:
            self._heard()

    def _heard(self) -> None:
        spoken, self.spoken = self.spoken, []
        if self.game is None:
            return
        for who, said in spoken:
            self.game.speak(who, said.line, said.to)
        if spoken:
            write_save(self.state, self.m)

    def _slash(self, text: str) -> None:
        cmd, *args = text.split()
        cmd = cmd.lower()
        ruled = [name for name, _ in plugin_commands(self.m, self.state)]
        if text.startswith(notes.JOT) and cmd[1:] not in ruled:  # 「/记 内容」 (or 「/记内容」): no turn, no keeper
            self.jot(text.removeprefix(notes.JOT))
            return
        actions = {"/draw": self.action_sketch, "/notes": self.action_notes, "/map": self.action_map, "/sheet": self.action_sheet,
                   "/help": self.action_help, "/recap": self.action_recap, "/save": self.action_save, "/debrief": self.action_debrief,
                   "/prefs": self.action_prefs, "/suggest": self.action_suggest, "/usage": self.action_usage}
        if self.may_develop:
            actions["/dev"] = self.action_developer
        if cmd in idea.WORDS:
            self.action_idea()
        elif cmd == "/kp":  # "/kp <话>" goes to the keeper (send_line); alone it has nothing to say
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
        elif cmd[1:] in ruled:
            self.plugin_command(cmd[1:], " ".join(args))
        else:
            self.app.notify(f"未知命令：{cmd}", severity="warning")

    @work(exclusive=True, group="turn")
    async def play(self, text: str) -> None:
        assert self.graph and self.game
        self.ideas.drop()  # whatever F5 was still thinking of, this action came first
        self._busy(True)
        inv = self.state.investigator
        said = "提出灵感检定" if idea.asked(text) else text
        self.story.add("system" if text.startswith("/kp ") else "player",
                       Text(f"▶ {inv.name} → KP：{text[4:]}" if text.startswith("/kp ") else f"▶ {inv.name}：{said}"))
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

    def action_idea(self) -> None:
        """Ctrl+L or /灵感: an Idea roll for the party, when one can be made now."""
        if self.spectate or self.game is None:
            return
        if self.busy:
            self.app.notify("本回合结束后可做灵感检定。", timeout=3)
        elif why := self.game.idea_refused():
            self.app.notify(why, timeout=5)
        else:
            self.play(idea.WORDS[0])

    def action_retry(self) -> None:
        if not self.busy:
            if self.game is None or self.state.pending_turn:
                self.play("/retry")
            else:
                self.app.notify("当前无中断回合，可直接提交行动。")

    async def _run_turn(self, inp, auto: bool = False, push: str = "") -> None:
        """One action through the turn graph. With `auto` (an AI investigator's) a push offer is
        answered with the push it said it would make (`push`, agents/cast.Proposal), if any."""
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
                choice = "roll" if auto else (await self.app.push_screen_wait(RollModal(offer)) or "roll")
            elif offer.get("kind") in ("reaction", "redeclare"):  # a fight stops to ask (engine/encounter)
                if auto:
                    choice = offer.get("default", "")
                else:
                    self._status("等待选择应对方式")
                    await self.make_way()
                    choice = await self.app.push_screen_wait(ChoiceModal(offer))
                    choice = offer.get("default", "") if choice is None else choice
            elif auto:
                choice = ai_choice(offer, push)
                said = f"孤注一掷：{choice['how']}" if isinstance(choice, dict) else {"luck": "花幸运", "drop": "放弃"}[choice]
                self.story.add("system", Text(f"◇ AI 选择：{said}", style="dim"))
            else:
                await self.make_way()
                choice = await self.app.push_screen_wait(PushModal(offer)) or "drop"
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
        if not ai or st.ending or st.turn % 2 or self._battle_now() is not None:
            return  # in a fight or a chase they act in its rounds (engine/encounter: Cast.encounter_choice)
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
        move = await self.cast.investigator(st, inv.id, situation, stances=views.stances(self.game, inv.id))
        if move is None:  # no action it could play (Cast.investigator): none is made up
            self.story.add("system", Text(f"{inv.name}（AI）本轮未行动。", style="dim"))
            return
        if move.react:  # its answer to blows from now on, as a player's Tab sets it
            self.game.set_react(inv.id, move.react)
        self.story.add("player", Text(f"▶ {inv.name}（AI）：{move.text}"))
        await self._run_turn({"input": move.text, "by_hand": False}, auto=True, push=move.push)

    def _situation(self) -> str:
        from ...engine.autoplay import situation

        return situation(self.m, self.state, self._viewer().id, self.game)

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
            result = await party.ainvoke({"situation": situation(self.m, st, game=self.game), "ids": alive, "proposals": [],
                                          "stances": {x: views.stances(self.game, x) for x in alive}})
            if not result["proposals"]:  # no AI investigator gave an action it could play: watching stops
                self.story.add("system", Text("AI 调查员本轮均未给出有效行动，观战停止。", style="dim"))
                break
            for inv_id, text, push, react in result["proposals"]:
                if st.ending or self._leave_when_ready is not None:  # 讲完再走: after this one
                    break
                if react:  # its answer to blows from now on, as a player's Tab sets it
                    self.game.set_react(inv_id, react)
                st.active = next(i for i, inv in enumerate(st.investigators) if inv.id == inv_id)
                self._panels()
                self.story.add("player", Text(f"▶ {st.investigator.name}（AI）：{text}"))
                await self._run_turn({"input": text, "by_hand": False}, auto=True, push=push)
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

    # -- the player's own notes (F2, /记; tui/notes.py) -----------------------------------------------
    def action_notes(self) -> None:
        """F2, /notes: the notebook, its page the player's notes, then the papers handed to them."""
        papers = [(h.id, h.title) for hid in self.state.handouts for h in self.m.handouts if h.id == hid]
        self.app.push_screen(NotebookModal(self.notes(), papers, keep=self.keep_notes, read=self._read_paper,
                                           notice=self._notes_notice()))

    def _read_paper(self, hid: str) -> None:
        h = self.m.handout(hid)
        self.app.push_screen(HandoutModal(h.title, h.text, self.m.handout_pictures(h), kind=h.kind))

    def _notes_owner(self) -> str | None:
        """Whose notes this screen keeps: the investigator played here (whoever acts, at a screen
        several play); None when nobody plays here (watching)."""
        return None if self.spectate or not self.state.investigators else self._speaker().id

    def notes(self) -> str | None:
        """The notes of this screen's player; None when there are none to keep (watching)."""
        who = self._notes_owner()
        return None if who is None else self.state.notebook.get(who, "")

    def keep_notes(self, text: str) -> None:
        """In a local game the notes go with the save: kept now, written with it once no turn runs
        (a turn's own checkpoints carry them meanwhile)."""
        who = self._notes_owner()
        if who is None:
            return
        if text.strip():
            self.state.notebook[who] = text
        else:
            self.state.notebook.pop(who, None)
        if self.game is not None and not self.busy:
            write_save(self.state, self.m)
        self._notes_shown()

    def _notes_notice(self) -> str:
        """A line under the page on where the notes are kept, where that needs saying."""
        return ""

    def _notes_shown(self) -> None:
        for panel in self.query(NotesPanel):  # none while the screen is being taken down
            panel.show(self.notes())

    def jot(self, typed: str) -> None:
        """「/记 内容」: the line at the end of the notes, from the input box; no turn is taken."""
        text, line = self.notes(), notes.line(typed)
        if text is None:
            self.app.notify("观战时没有笔记。", timeout=3)
        elif not line:
            self.app.notify("请在 /记 后输入要记下的内容。", timeout=4)
        else:
            self.keep_notes(notes.append(text, line))
            self.app.notify("已记入笔记。", timeout=2)

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
        """Ctrl+K or 设置 → 规则插件, in 开发者模式 only (check_action): the rules of this game,
        paused or started again, and a house rule added in a sentence."""
        from ..modals.plugins import PluginsModal

        if not self.developing:
            return

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
