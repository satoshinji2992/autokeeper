"""Sitting at someone else's table: join a Hub, pick a seat, play your investigator.

The game screen is the ordinary one; what changes is where things come from. Events,
state and whose turn it is arrive from the Hub; the input box sends the action when it
is your turn, and anything that starts with a full-width bracket is table talk.
"""

from __future__ import annotations

import asyncio
import os
from functools import partial
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from .. import config
from ..engine.game import RETELL
from ..engine.state import GameState, LogEntry
from ..module.schema import Module
from ..net import media
from ..net import room as lan
from ..net.link import Link
from ..rules import commands
from .app import GameScreen
from .retell import RetellModal
from .screens import EngineModal, PushModal, RollModal
from .widgets import SceneArt

TALK = ("（", "(")  # table talk, heard by everyone, never an action
IDEAS_WAIT = 60.0  # seconds F5 waits for the host's ideas


def table_module(welcome: dict[str, Any]) -> Module:
    """The module as the Hub shares it, with its ASCII art and the photos this machine can
    open (all of them when the screen runs on the host, as in the browser)."""
    m = Module.model_validate(welcome["module"])
    m.art, m.art_colors = welcome.get("art", {}), welcome.get("colors", {})
    m.pictures = {k: v for k, v in welcome.get("pictures", {}).items() if Path(v).is_file()}
    handouts = {key for h in m.handouts for key in h.images}
    m.pictures.update({k: path for k, data in welcome.get("picture_data", {}).items()
                       if (path := media.unpack(data, max_size=1600 if k in handouts else 512))})
    return m


class JoinScreen(Screen):
    """The host's address and your name; the last ones you used are filled in."""

    BINDINGS = [("escape", "back", "返回")]

    def __init__(self, address: str = "", name: str = ""):
        super().__init__()
        last = lan.recall()
        self.address, self.name_ = address or last.get("address", ""), name or last.get("name", "")

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static("加入局域网房间", classes="modal-title")
            yield Static(Text("填房主给你的地址（比如 192.168.1.5），再起个名字，回车加入。", style="dim"))
            yield Input(self.address, placeholder="房主的地址", id="addr")
            yield Input(self.name_, placeholder="你的名字", id="name")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#name" if self.address else "#addr", Input).focus()

    @on(Input.Submitted)
    def go(self) -> None:
        addr, name = self.query_one("#addr", Input).value.strip(), self.query_one("#name", Input).value.strip()
        if not addr:
            self.query_one("#addr", Input).focus()
        elif not name:
            self.query_one("#name", Input).focus()
        else:
            from .lobby import LobbyScreen

            lan.remember(address=addr, name=name)
            self.app.switch_screen(LobbyScreen(addr, name))

    def action_back(self) -> None:
        leave(self.app)


def leave(app) -> None:
    """Back to the title; started as `autokeeper join` (a browser's seat), there is none: quit."""
    if len(app.screen_stack) > 2:
        app.pop_screen()
    else:
        app.exit()


class RoomCommands(Provider):
    """Ctrl+P at the table, for the host who opened the room in this app: close it."""

    def _entries(self):
        if getattr(self.app, "room", None) is not None:
            yield "关闭房间", "所有人都会断开，网页也会关掉；存档留着", partial(self.screen.run_action, "close_room")

    async def discover(self) -> Hits:
        for name, what, run in self._entries():
            yield DiscoveryHit(name, run, help=what)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, what, run in self._entries():
            if (score := max(matcher.match(name), matcher.match(what) * 0.5)) > 0:
                yield Hit(score, matcher.highlight(name), run, help=what)


class NetGameScreen(GameScreen):
    """The game screen at a shared table."""

    COMMANDS = {RoomCommands}

    def action_plugins(self) -> None:
        self.app.notify("扩展规则由房主管理，会随游戏同步结算结果。")

    SHOW_ENGINE = True  # "轮到谁" is on the top bar

    def __init__(self, module: Module, state: GameState, link: Link, seat: str | None, backlog: list[LogEntry]):
        super().__init__(module, state, resume=True, spectate=False)
        self.link, self.seat, self.backlog = link, seat, backlog
        self.my_turn = False  # the pool is open and takes your actions: what you send goes in
        self.pool_open = False
        self.pool: dict[str, Any] = {}  # the Hub's last word on it: capacity, count, by, done, waiting
        self.pooled: list[str] = []  # what you put in this round
        self.waiting_on: list[str] = []
        self.settling = ""  # whose action the keeper is settling
        self.retry_due = False  # your settled action broke off: /retry goes on with it
        self.inbox: list[dict[str, Any]] = []  # messages that arrive before the screen is up
        self.ready = False
        self.ideas_due: asyncio.Future | None = None  # the Hub's answer to F5

    async def on_mount(self, event: events.Mount) -> None:
        event.prevent_default()  # no local agents, checkpoints or opening: the Hub runs the game
        self._start_history(self.backlog)
        await self.query_one("#left").mount(Static(id="turnline"), before=self.query_one("#command", Input))
        self._turnline()
        self.query_one(SceneArt).effects = config.load().animations
        self.engine_label = "局域网联机" + ("" if self.seat else " · 观战")
        self._scene(self.state.location)
        self._panels()
        self.set_interval(0.1, self._spin)
        for e in self.backlog:  # what happened before you sat down
            who = f"{e.who}：" if e.who else ""
            block = self.story.add("system" if e.kind not in ("keeper", "npc") else e.kind, Text(f"{who}{e.text}", style="dim"))
            if e.kind == "keeper":  # told again, it is rewritten in place
                self.told = block
        self.query_one(Input).placeholder = "等你的回合时说出行动；全角括号开头是场外聊天，如（我去倒杯水）"
        self.ready = True
        while self.inbox:
            await self.on_msg(self.inbox.pop(0))

    async def on_unmount(self) -> None:
        await self.link.close()

    def _me(self) -> str | None:
        return self.seat or None

    def _turnline(self) -> None:
        line = self.query_one("#turnline", Static)
        others = "、".join(self.waiting_on)
        room = f"池子 {self.pool.get('count', 0)}/{self.pool.get('capacity', 0)}"
        fresh = self.my_turn and not self.pooled
        if self.pool_open and self.seat and not self.pooled and self.my_turn:
            text = f" ▶ 新的一轮：说出你的行动（{room}，每人最多 2 条）"
        elif self.pool_open and self.seat and self.my_turn:
            text = f" 已交：{self.pooled[-1][:24]} · 还能再交 1 条，或直接回车表示说完了 · {room}"
        elif self.pool_open and self.seat:
            text = " 你这一轮交完了" + (f"，等 {others}" if others else "") + f" · {room} · /undo 撤回上一条"
        elif self.pool_open:
            text = " 这一轮在收集行动" + (f"：还在等 {others}" if others else "") + f" · {room}"
        elif self.settling:
            text = f" 守秘人正在结算本轮：{self.settling}……讲完才能继续"
        else:
            text = " 等待开局…"
        line.update(Text(text + " ", style="bold" if fresh else ""))
        line.set_class(fresh, "mine")
        inp = self.query_one("#command", Input)
        inp.set_class(fresh, "mine")
        inp.placeholder = ("说出这一轮你要做什么，回车发送" if fresh else
                           "还能再交一条；什么都不写直接回车 = 这轮说完了" if self.my_turn else
                           "守秘人讲完这一轮才能行动；全角括号开头是场外聊天，如（我去倒杯水）")

    def _viewer(self):
        """Your own investigator's sheet; when watching, whoever is acting."""
        if self.seat:
            return self.state.find(self.seat)
        return self.state.investigator

    def _sync(self, view: dict[str, Any]) -> None:
        self.state = GameState.model_validate(view)
        self._panels()

    async def on_msg(self, msg: dict[str, Any]) -> None:
        if not self.ready:
            self.inbox.append(msg)
            return
        t = msg["t"]
        if t == "event":
            pictures = msg["ev"].get("picture_data")
            if isinstance(pictures, dict) and msg["ev"].get("type") == "handout":
                allowed = {key for h in self.m.handouts for key in h.images}
                self.m.pictures.update({k: path for k, data in pictures.items()
                                        if k in allowed and (path := media.unpack(data, max_size=1600))})
            elif pictures:
                if path := media.unpack(pictures):
                    msg["ev"]["picture"] = path
            await self.handle(msg["ev"])
        elif t == "module":  # the director added places and people
            m = Module.model_validate(msg["module"])
            m.art, m.art_colors, m.pictures = self.m.art, self.m.art_colors, self.m.pictures
            m.art.update(msg.get("art", {}))
            m.art_colors.update(msg.get("colors", {}))
            self.m = m
        elif t == "sync":
            self._sync(msg["state"])
        elif t == "pool":
            was_open = self.pool_open
            self.pool_open, self.pool = bool(msg["open"]), msg
            self.waiting_on = msg.get("waiting", [])
            mine = int(msg.get("by", {}).get(self.seat or "", 0))
            self.pooled = self.pooled[:mine] if self.pool_open and was_open else []
            done = (self.seat or "") in msg.get("done", [])
            self.my_turn = self.pool_open and bool(self.seat) and not done and mine < 2 and msg.get("count", 0) < msg.get("capacity", 0)
            if self.pool_open and not was_open:
                self.settling = ""
                self.engine_label = "局域网联机 · 新的一轮"
                self._topbar()
                if self.seat:
                    self.app.notify("新的一轮：说出你的行动", timeout=4)
                    self.query_one(Input).focus()
            self._turnline()
        elif t == "turn":  # the pool is closed: the keeper settles this seat's action
            self._sync(msg["state"])
            self.my_turn = False
            mine = bool(self.seat) and msg["investigator"] == self.seat
            who = "你的行动" if mine else f"{msg['name']}（{msg['player'] or 'AI'}）"
            self.settling = who + (f"（{msg['index']}/{msg['total']}）" if msg.get("total", 0) > 1 else "")
            self.engine_label = f"局域网联机 · 结算{self.settling}的行动"
            self._topbar()
            self._turnline()
        elif t == "offer":  # throw your own dice, or push / spend luck after a failure
            offer = msg["offer"]
            choice = await self.app.push_screen_wait(RollModal(offer) if offer.get("kind") == "roll" else PushModal(offer))
            await self.link.send({"t": "choice", "mode": choice})
        elif t == "say":
            self.story.add("system", Text(f"【场外】{msg['from']}：{msg['text']}", style="italic dim"))
        elif t == "ideas":  # for this screen alone
            if self.ideas_due is not None and not self.ideas_due.done():
                self.ideas_due.set_result(msg.get("ideas", []))
        elif t == "over":
            self._sync(msg["state"])
            self.engine_label = "局域网联机 · 本局结束"
            self._topbar()
            if msg.get("ending"):
                self.story.add("system", Text("这一局结束了：F10 复盘，看真相、漏掉的线索和没走到的结局" + ("；.en 做成长检定" if self.seat else ""), style="dim"))
        elif t == "error":
            if msg.get("retry"):
                self.status_text = ""
                self.retry_due = True
                self.story.end_keeper("这一段暂时中断，输入 /retry 接着讲。")
            self.app.notify(msg["text"], severity="warning")
        elif t == "closed":
            self.app.notify("和房主的连接断了", severity="error", timeout=10)

    async def handle(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "said":
            who = f"{ev['who']}（{ev.get('player') or 'AI'}）"
            self.story.add("player", Text(f"▶ {who}：{ev['text']}"))
            return
        if t == "retold":  # the keeper is about to tell the last narration again, for everyone
            self.app.notify(f"{ev.get('who') or '有人'}请守秘人重讲上一段" + ("" if ev.get("nudge") == "换个讲法" else f"：{ev.get('nudge')}"), timeout=4)
            return
        if t in ("clue", "handout"):  # hidden from this screen until now
            items = self.m.clues if t == "clue" else self.m.handouts
            if found := next((x for x in items if x.id == ev["id"]), None):
                found.title, found.text = ev["title"], ev["text"]
        await super().handle(ev)

    def send_line(self, text: str) -> None:
        word = text.split()[0] if text.split() else ""
        if word == "/undo" and self.pool_open and self.seat:
            if self.pooled:
                self.pooled.pop()
            self.run_worker(partial(self.link.send, {"t": "undo"}))
        elif word in ("/done", "/pass"):
            self.empty_enter()
        elif text.startswith("/"):
            self._slash(text)
        elif text.startswith(TALK):
            self.run_worker(partial(self.link.send, {"t": "say", "text": text}))
        elif not self.seat:
            self.app.notify("你在观战；全角括号开头的话是场外聊天")
        elif self.state.ending and commands.instant(text):  # after the end: growth rolls (.en), the sheet
            self.run_worker(partial(self.link.send, {"t": "act", "text": text}))

        elif commands.instant(text) and self.pool_open and self.seat:  # .r, .st: at once, not an action
            self.run_worker(partial(self.link.send, {"t": "act", "text": text}))
        elif not self.my_turn:
            self.app.notify("这一轮你交完了；/undo 撤回上一条" if self.pool_open and self.seat else "守秘人讲完这一轮才能行动")
        else:
            self.pooled.append(text)
            self.my_turn = len(self.pooled) < 2
            self._turnline()
            self.run_worker(partial(self.link.send, {"t": "act", "text": text}))

    def empty_enter(self) -> None:
        """Enter on an empty box while the pool is open: nothing more from you this round."""
        if self.pool_open and self.seat:
            self.my_turn = False
            self._turnline()
            self.run_worker(partial(self.link.send, {"t": "done"}))

    def action_retry(self) -> None:
        if self.seat and self.retry_due:
            self.retry_due = False
            self.run_worker(partial(self.link.send, {"t": "act", "text": "/retry"}))
        else:
            self.app.notify("等房主允许接续这一回合后再试。")

    def action_suggest(self) -> None:
        if self.seat:
            super().action_suggest()
        else:
            self.app.notify("你在观战，没有自己的调查员")

    async def ideas_for(self, inv) -> list[str]:
        """The host's investigator agent thinks for this seat (Hub._ideas), from the same view
        the AI seats get; the answer comes back to this screen alone."""
        self.ideas_due = asyncio.get_running_loop().create_future()
        await self.link.send({"t": "suggest"})
        return await asyncio.wait_for(self.ideas_due, IDEAS_WAIT)

    # The host runs the agents: switching models, sketches, recaps and saving happen there.
    def action_engine(self) -> None:
        if not self.link.host:
            self.app.notify("联机时由房主决定用哪个模型")
            return
        from .settings import model_choices

        cfg, saved = config.load(), config.ui_settings()
        model, fallback = os.environ.get("AUTOKEEPER_MODEL", saved.get("model", "")), os.environ.get("AUTOKEEPER_FALLBACK", saved.get("fallback", ""))
        self.app.push_screen(EngineModal(model_choices(), cfg.preset, model, fallback, cfg.mode), self._ask_engine)

    def _ask_engine(self, choice: dict | None) -> None:
        if choice:
            self.run_worker(partial(self.link.send, {"t": "engine", "choice": choice}))

    def action_sketch(self) -> None:
        self.app.notify("联机时暂时不能手绘")

    def action_recap(self) -> None:
        self.app.notify("联机时由房主那边回顾前情")

    def action_save(self) -> None:
        self.app.notify("房主那边每回合都会自动存档")

    # Telling again and the epilogue happen on the host too, asked for from here.
    def action_retell(self, nudge: str = "") -> None:
        if not self.seat:
            self.app.notify("你在观战，不能请守秘人重讲")
        elif nudge and nudge not in RETELL:
            self.app.notify(f"可以这样要：{'、'.join(RETELL)}", severity="warning")
        elif nudge:
            self._ask_retell(nudge)
        else:
            self.app.push_screen(RetellModal(), self._ask_retell)

    def _ask_retell(self, nudge: str | None) -> None:
        if nudge:
            self.run_worker(partial(self.link.send, {"t": "retell", "nudge": nudge}))

    def _ask_epilogue(self) -> None:
        self.run_worker(partial(self.link.send, {"t": "epilogue"}))

    def action_prefs(self) -> None:
        self.app.notify("联机时按房主那边的叙事偏好来讲")

    def _leave(self) -> None:
        leave(self.app)

    def action_close_room(self) -> None:
        if getattr(self.app, "room", None) is not None:
            from .lobby import CloseRoomModal

            self.app.push_screen(CloseRoomModal(), self._close_room)

    @work(exclusive=True, group="room")
    async def _close_room(self, sure: bool | None) -> None:
        if sure:
            from .lobby import close_room

            await self.link.close()
            await close_room(self.app)
            self._leave()

    def action_usage(self) -> None:
        self.app.notify("联机时模型跑在房主那边，用量由房主记账")
