"""Sitting at someone else's table: join a Hub, pick a seat, play your investigator.

The game screen is the ordinary one; what changes is where things come from. Events,
state and whose turn it is arrive from the Hub; the input box sends the action when it
is your turn, and anything that starts with a full-width bracket is table talk.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..engine.state import GameState, LogEntry
from ..module.schema import Module
from ..net.link import Link
from ..rules import commands
from .app import GameScreen
from .screens import PushModal, RollModal

TALK = ("（", "(")  # table talk, heard by everyone, never an action


def table_module(welcome: dict[str, Any]) -> Module:
    """The module as the Hub shares it, with its ASCII art and the photos this machine can
    open (all of them when the screen runs on the host, as in the browser)."""
    m = Module.model_validate(welcome["module"])
    m.art, m.art_colors = welcome.get("art", {}), welcome.get("colors", {})
    m.pictures = {k: v for k, v in welcome.get("pictures", {}).items() if Path(v).is_file()}
    return m


class JoinScreen(Screen):
    """The host's address and your name."""

    BINDINGS = [("escape", "back", "返回")]

    def __init__(self, address: str = "", name: str = ""):
        super().__init__()
        self.address, self.name_ = address, name

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
            self.app.switch_screen(LobbyScreen(addr, name))

    def action_back(self) -> None:
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()
        else:
            self.app.exit()


class LobbyScreen(Screen):
    """Connected: take a free seat, watch, or start with the empty seats played by the AI."""

    BINDINGS = [("escape", "leave", "离开")]

    def __init__(self, address: str, name: str):
        super().__init__()
        self.link = Link(address, name)
        self.welcome: dict[str, Any] | None = None
        self.seats: list[dict[str, Any]] = []
        self.chosen: str | None = None  # the seat taken, "" for watching
        self.gone = False

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(f"正在连接 {self.link.url} …", id="lobby-title", classes="modal-title")
            yield Static("", id="lobby-hint")
            yield OptionList(id="choices")
        yield Footer()

    async def on_mount(self) -> None:
        try:
            await self.link.connect()
        except Exception as exc:  # wrong address, host not up yet
            self.app.notify(f"连不上房间：{exc}", severity="error", timeout=8)
            self.app.switch_screen(JoinScreen(self.link.url.removeprefix("ws://").removesuffix("/ws"), self.link.name))
            return
        self.link.handler = self.on_msg
        self.app.run_worker(self.link.pump, name="link", group="link", exit_on_error=False)

    async def on_msg(self, msg: dict[str, Any]) -> None:
        t = msg["t"]
        if t == "welcome":
            self.welcome = msg
            self.seats = msg["seats"]
            title = msg["module"].get("title", "")
            self.query_one("#lobby-title", Static).update(f"《{title}》 的房间")
            self._list_seats()
        elif t == "seats":
            self.seats = msg["seats"]
            self._list_seats()
        elif t in ("sync", "turn") and self.welcome is not None and self.chosen is None:
            self.welcome["state"] = msg["state"]  # keep up while still choosing
        elif t in ("begin", "turn", "event") and self.chosen is not None and self.welcome is not None:
            self._enter([msg])
        elif t == "error":
            self.app.notify(msg["text"], severity="warning")
        elif t == "closed" and not self.gone:
            self.app.notify("房主关闭了房间", severity="error")

    def _list_seats(self) -> None:
        ol = self.query_one(OptionList)
        kept = ol.highlighted if ol.option_count else None  # someone else sitting down must not move your cursor
        ol.clear_options()
        mine = self.chosen
        for s in self.seats:
            label = f"  {s['name']}（{s['occupation']}）  "
            if not s["human"]:
                ol.add_option(Option(label + "AI 扮演", id=f"ai:{s['investigator']}", disabled=True))
            elif s["online"]:
                tag = "你" if s["investigator"] == mine else s["player"]
                ol.add_option(Option(label + f"{tag} 已就座", id=f"seat:{s['investigator']}", disabled=s["investigator"] != mine))
            else:
                ol.add_option(Option(label + "空位，选我", id=f"seat:{s['investigator']}"))
        ol.add_option(Option("  只观战", id="watch"))
        ol.add_option(Option("  现在开始（空座交给 AI）", id="start"))
        started = self.welcome and self.welcome.get("started")
        hint = "游戏已经开始了，选个空位加入，或者观战。" if started else "选一个座位，人齐了就开局。"
        self.query_one("#lobby-hint", Static).update(Text(hint, style="dim"))
        free = next((i for i, s in enumerate(self.seats) if s["human"] and not s["online"]), len(self.seats))
        ol.highlighted = kept if kept is not None and kept < ol.option_count else free  # first time: Enter takes the first free seat
        ol.focus()

    @on(OptionList.OptionSelected, "#choices")
    async def pick(self, event: OptionList.OptionSelected) -> None:
        choice = event.option.id or ""
        if choice.startswith("seat:"):
            self.chosen = choice[5:]
            await self.link.send({"t": "sit", "investigator": self.chosen})
        elif choice == "watch":
            self.chosen = ""
        elif choice == "start":
            if self.chosen is None:
                self.chosen = ""
            await self.link.send({"t": "start"})
        if self.chosen is not None and self.welcome and self.welcome.get("started"):
            self._enter([])

    def _enter(self, first: list[dict[str, Any]]) -> None:
        assert self.welcome is not None
        w = self.welcome
        state = GameState.model_validate(w["state"])
        m = table_module(w)
        screen = NetGameScreen(m, state, self.link, self.chosen or None, [LogEntry.model_validate(e) for e in w.get("log", [])])
        screen.inbox.extend(first)
        self.link.handler = screen.on_msg
        self.gone = True
        self.app.switch_screen(screen)

    async def action_leave(self) -> None:
        self.gone = True
        await self.link.close()
        self.app.pop_screen() if len(self.app.screen_stack) > 1 else self.app.exit()


class NetGameScreen(GameScreen):
    """The game screen at a shared table."""

    def __init__(self, module: Module, state: GameState, link: Link, seat: str | None, backlog: list[LogEntry]):
        super().__init__(module, state, resume=True, spectate=False)
        self.link, self.seat, self.backlog = link, seat, backlog
        self.my_turn = False
        self.inbox: list[dict[str, Any]] = []  # messages that arrive before the screen is up
        self.ready = False

    async def on_mount(self, event: events.Mount) -> None:
        event.prevent_default()  # no local agents, checkpoints or opening: the Hub runs the game
        self.engine_label = "局域网联机" + ("" if self.seat else " · 观战")
        self._scene(self.state.location)
        self._panels()
        self.set_interval(0.1, self._spin)
        for e in self.backlog:  # what happened before you sat down
            who = f"{e.who}：" if e.who else ""
            self.story.add("system" if e.kind not in ("keeper", "npc") else e.kind, Text(f"{who}{e.text}", style="dim"))
        self.query_one(Input).placeholder = "等你的回合时说出行动；全角括号开头是场外聊天，如（我去倒杯水）"
        self.ready = True
        while self.inbox:
            await self.on_msg(self.inbox.pop(0))

    async def on_unmount(self) -> None:
        await self.link.close()

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
            await self.handle(msg["ev"])
        elif t == "module":  # the director added places and people
            m = Module.model_validate(msg["module"])
            m.art, m.art_colors, m.pictures = self.m.art, self.m.art_colors, self.m.pictures
            self.m = m
        elif t == "sync":
            self._sync(msg["state"])
        elif t == "turn":
            self._sync(msg["state"])
            self.my_turn = bool(self.seat) and msg["investigator"] == self.seat
            who = "你" if self.my_turn else f"{msg['name']}（{msg['player'] or 'AI'}）"
            self.engine_label = f"局域网联机 · 轮到{who}"
            self._topbar()
            if self.my_turn:
                self.app.notify("轮到你了", timeout=4)
                self.query_one(Input).focus()
        elif t == "offer":  # throw your own dice, or push / spend luck after a failure
            offer = msg["offer"]
            choice = await self.app.push_screen_wait(RollModal(offer) if offer.get("kind") == "roll" else PushModal(offer))
            await self.link.send({"t": "choice", "mode": choice})
        elif t == "say":
            self.story.add("system", Text(f"【场外】{msg['from']}：{msg['text']}", style="italic dim"))
        elif t == "over":
            self._sync(msg["state"])
            self.engine_label = "局域网联机 · 本局结束"
            self._topbar()
        elif t == "error":
            self.app.notify(msg["text"], severity="warning")
        elif t == "closed":
            self.app.notify("和房主的连接断了", severity="error", timeout=10)

    async def handle(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "said":
            who = f"{ev['who']}（{ev.get('player') or 'AI'}）"
            self.story.add("player", Text(f"▶ {who}：{ev['text']}"))
            return
        if t in ("clue", "handout"):  # hidden from this screen until now
            items = self.m.clues if t == "clue" else self.m.handouts
            if found := next((x for x in items if x.id == ev["id"]), None):
                found.title, found.text = ev["title"], ev["text"]
        await super().handle(ev)

    def send_line(self, text: str) -> None:
        if text.startswith("/"):
            self._slash(text)
        elif text.startswith(TALK):
            self.run_worker(partial(self.link.send, {"t": "say", "text": text}))
        elif not self.seat:
            self.app.notify("你在观战；全角括号开头的话是场外聊天")
        elif self.state.ending and commands.instant(text):  # after the end: growth rolls (.en), the sheet
            self.run_worker(partial(self.link.send, {"t": "act", "text": text}))
        elif not self.my_turn:
            self.app.notify("还没轮到你")
        else:
            if not commands.instant(text):  # .r, .st and the like leave the turn with you
                self.my_turn = False
            self.run_worker(partial(self.link.send, {"t": "act", "text": text}))

    # The host runs the agents: switching models, sketches, recaps and saving happen there.
    def action_engine(self) -> None:
        self.app.notify("联机时由房主决定用哪个模型")

    def action_sketch(self) -> None:
        self.app.notify("联机时暂时不能手绘")

    def action_recap(self) -> None:
        self.app.notify("联机时由房主那边回顾前情")

    def action_save(self) -> None:
        self.app.notify("房主那边每回合都会自动存档")
