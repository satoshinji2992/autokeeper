"""Sitting at someone else's table: join a Hub, pick a seat, play your investigator.

The game screen is the ordinary one; what changes is where things come from. Events,
state and whose turn it is arrive from the Hub; the input box sends the action when it
is your turn, words to the party as speech, and out-of-character chat (engine/chat.py) to everyone.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import math
import os
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from ... import config, paths
from ...engine import audience, chat, idea
from ...engine.game import RETELL
from ...engine.state import GameState, LogEntry
from ...module.schema import Module, PreludeFrame
from ...net import media
from ...net import room as lan
from ...net.link import Link
from ...rules import commands
from .. import notes
from ..modals.dialogs import PushModal, RollModal, Timed
from ..modals.engine import EngineModal
from ..modals.private import PrivateMessageModal
from ..modals.retell import RetellModal
from ..widgets.panels import HistoryPanel, SceneArt, log_line
from .game import GameScreen
from .prelude import PreludeScreen

if TYPE_CHECKING:
    from ...net.hub import Hub

IDEAS_WAIT = 60.0  # seconds F5 waits for the host's ideas
HUB_WAIT = 10.0  # seconds the host's screen waits for the Hub's loop to read its game (开发者模式)
PRIVATE_COMMANDS = ("/私聊", "/w")
PRIVATE_LINE = re.compile(r'''(?P<name>"[^"]+"|'[^']+'|“[^”]+”|\S+)\s+(?P<text>.+)''', re.S)


def private_line(screen: NetGameScreen, text: str) -> None:
    """Resolve the one named recipient locally; the Hub authorizes delivery and echoes it."""
    if not screen.seat:
        screen.app.notify("观战模式下无法发送私语。", severity="warning")
        return
    if screen.state.ending:
        screen.app.notify("本局已结束，无法发送角色私语。", severity="warning")
        return
    parts = text.split(maxsplit=1)
    found = PRIVATE_LINE.fullmatch(parts[1].strip()) if len(parts) == 2 else None
    if found is None:
        screen.app.notify('用法：/私聊 调查员姓名 内容；姓名含空格时用双引号。', timeout=6)
        return
    name, body = found.group("name"), found.group("text").strip()
    if name.startswith(('"', "'", "“")):
        name = name[1:-1]
    roster = screen.state.investigators
    matches = [i for i in roster if i.id == name] or [i for i in roster if i.name == name]
    if len(matches) != 1:
        screen.app.notify("有多名调查员使用这个名字，请使用角色编号。" if matches else f"未找到该调查员：{name}",
                          severity="warning")
        return
    target = matches[0]
    if target.id == screen.seat:
        screen.app.notify("请选择其他调查员作为收件人。", severity="warning")
        return
    apart, place = getattr(screen.state, "apart", {}), getattr(screen.state, "location", "")
    if apart.get(target.id, place) != apart.get(screen.seat, place):
        screen.app.notify("对方不在同一地点，无法直接私语。", severity="warning")
        return
    screen.run_worker(partial(screen.link.send, {"t": "speak", "text": body, "to": [target.id], "channel": "whisper"}))


def with_private_history(public: list[dict[str, Any]], private: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace the previous seat's private backlog when choosing a seat in the lobby."""
    entries = [e for e in public if e.get("audience") is None] + private
    return sorted(entries, key=lambda e: e.get("turn", 0))


def history_text(entry: LogEntry) -> Text:
    """The restored line keeps its privacy label, without exposing internal audience ids."""
    private = getattr(entry, "audience", None) is not None
    if private and entry.kind == "keeper" and not getattr(entry, "delivery", ""):
        return Text(f"【守秘人·私密】{entry.text}", style="dim")
    label = "【私语】" if private and entry.kind == "speech" else ""
    who = f"{entry.who}：" if entry.who else ""
    return Text(f"{label}{who}{entry.text}", style="dim")


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
    """The host's address and your name; the last ones you used are filled in. In a browser's
    seat the address is the page's own and the name is the visitor's: neither is the host's."""

    BINDINGS = [("escape", "back", "返回")]

    def __init__(self, address: str = "", name: str = ""):
        super().__init__()
        self.guest = bool(address and os.environ.get("AUTOKEEPER_GUEST"))  # started by the room's page
        last = {} if self.guest else lan.recall()  # the host's own settings live on this machine too
        self.address, self.name_ = address or last.get("address", ""), name or last.get("name", "")

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static("加入局域网房间", classes="modal-title")
            hint = "起个名字，回车加入" if self.guest else "填房主给你的地址（比如 192.168.1.5），再起个名字，回车加入。"
            yield Static(Text(hint, style="dim"))
            yield Input(self.address, placeholder="房间地址", id="addr")
            yield Input(self.name_, placeholder="玩家名称", id="name", max_length=16)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#addr", Input).display = not self.guest
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

            if not self.guest:
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
        if getattr(self.screen, "seat", None) and not self.screen.state.ending:
            yield "私信 /sc", "选择收件人并发送私信", self.screen.action_private_message
        if getattr(self.app, "room", None) is not None:
            yield "关闭房间", "所有人断开，存档还在", partial(self.screen.run_action, "close_room")

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

    SHOW_ENGINE = True  # "轮到谁" is on the top bar

    def menu_items(self) -> list[tuple[str, str]]:
        """The Esc menu at a shared table: the host saves; 关闭房间 before 回到标题 for the
        host who opened the room in this app."""
        items = [item for item in super().menu_items() if item[0] not in ("save", "load")]
        if self.seat and not self.state.ending:
            items.insert(1, ("private_message", "私信 /sc"))
        if getattr(self.app, "room", None) is not None:  # the room was opened in this app
            items.insert([key for key, _ in items].index("title"), ("close_room", "关闭房间"))
        return items

    def settings_items(self) -> list[tuple[str, str]]:
        """设置 at a shared table: the host tells, so 叙事偏好 is theirs, and only the host changes
        models; 开发者模式 needs the game, which runs in the host's own app (may_develop)."""
        off = {"prefs"} | (set() if self.link.host else {"engine"})
        return [item for item in super().settings_items() if item[0] not in off]

    def __init__(self, module: Module, state: GameState, link: Link, seat: str | None, backlog: list[LogEntry]):
        super().__init__(module, state, resume=True, spectate=False)
        self.link, self.seat, self.backlog = link, seat, backlog
        self.my_turn = False  # the pool is open and takes your actions: what you send goes in
        self.pool_open = False
        self.pool: dict[str, Any] = {}  # the Hub's last word on it: capacity, count, by, done, waiting
        self.pooled: list[str] = []  # what you put in this round
        self.held: list[str] = []  # what you said for the next round, where it goes in first
        self.waiting_on: list[str] = []
        self.settling = ""  # whose action the keeper is settling
        self.retry_due = False  # your settled action broke off: /retry goes on with it
        self.inbox: list[dict[str, Any]] = []  # messages that arrive before the screen is up
        self.ready = False
        self.ideas_due: asyncio.Future | None = None  # the Hub's answer to F5
        self._pending_module: Module | None = None
        self._pending_state: GameState | None = None
        # The player's own notes (tui/notes.py): this machine's, never sent anywhere. A browser's seat runs
        # on the host's machine (net/web.py): its notes live as long as its page, and nothing is written.
        self.guest = bool(os.environ.get("AUTOKEEPER_GUEST"))
        self.jotted: str | None = None  # read from this machine the first time they are wanted
        self._peeking = self._peek_again = False  # the developer panel is being made on the Hub's loop; again after

    async def on_mount(self, event: events.Mount) -> None:
        event.prevent_default()  # no local agents, checkpoints or opening: the Hub runs the game
        self._start_history(self.backlog)
        await self.query_one("#left").mount(Static(id="turnline"), before=self.query_one("#command", Input))
        self._turnline()
        self._fit_stage()  # the turn line takes a row from a fight's stage
        self.query_one(SceneArt).effects = config.load().animations
        self.engine_label = "局域网联机" + ("" if self.seat else " · 观战")
        self._scene(self.state.location)
        self._panels()
        self.call_after_refresh(self._battle_resumed)  # sat down in the middle of a fight or a chase
        self.set_interval(0.1, self._spin)
        self.set_interval(0.5, self._countdown)
        for e in self.backlog:  # what happened before you sat down
            block = self.story.add("system" if e.kind not in ("keeper", "npc") else e.kind, history_text(e),
                                   delivery=e.delivery)
            if e.kind == "keeper" and (e.audience is None or e.delivery):
                self.told = block
        self.ready = True
        while self.inbox:
            await self.on_msg(self.inbox.pop(0))

    async def on_unmount(self) -> None:
        await self.link.close()

    def _me(self) -> str | None:
        return self.seat or None

    # -- the player's own notes: on this machine, per game and seat (tui/notes.py) -----------------------
    def _notes_owner(self) -> str | None:
        return self.seat or None  # watching: no notes

    def _notes_file(self) -> Path:
        return paths.table_notes(self.state.module, self.state.slot, self.seat or "")

    def notes(self) -> str | None:
        if not self.seat:
            return None
        if self.jotted is None:  # sat down here before: what was written then
            self.jotted = "" if self.guest else notes.read(self._notes_file())
        return self.jotted

    def keep_notes(self, text: str) -> bool:
        """Kept on this machine, never sent: not to the host, not to the others."""
        if not self.seat:
            return False
        self.jotted = text
        if not self.guest:
            try:
                notes.write(self._notes_file(), text)
            except OSError as exc:
                self.app.notify(f"笔记未能保存：{exc}", severity="error", timeout=6)
                self._notes_shown()
                return False
        self._notes_shown()
        return True

    def _notes_notice(self) -> str:
        if self.guest:
            return "网页版不保存笔记，关闭或刷新页面后即清空。"
        return "笔记只保存在这台电脑上，不会发给房主和其他玩家。"

    def _countdown(self) -> None:
        """The round's window counting down on the turn line."""
        if getattr(self, "closes", None) is not None and self.pool_open:
            self._turnline()

    def _turnline(self) -> None:
        line = self.query_one("#turnline", Static)
        others = "、".join(self.waiting_on)
        room = f"行动 {self.pool.get('count', 0)}/{self.pool.get('capacity', 0)}"
        fresh = self.my_turn and not self.pooled
        closes = getattr(self, "closes", None)
        if closes is not None:  # the moment others can still act in, counting down
            room += f" · {max(0, math.ceil(closes - time.monotonic()))} 秒后结算"
        if self.pool_open and self.seat and not self.pooled and self.my_turn:
            text = f" ▶ 新的一轮 · {room}"
        elif self.pool_open and self.seat and self.my_turn:
            text = f" 已交：{self.pooled[-1][:24]} · 还能交 1 条，空行回车结束 · {room}"
        elif self.pool_open and self.seat:
            text = " 这轮交完了" + (f" · 等 {others}" if others else "") + f" · {room} · 输入 /undo 撤回"
        elif self.pool_open:
            text = (f" 等 {others} · " if others else " ") + room
        elif self.settling:
            text = f" 守秘人在讲 {self.settling}"
        else:
            text = " 等待开局…"
        if self.seat and self.held:  # said meanwhile: it is not lost
            first = self.held[0] if len(self.held[0]) <= 10 else self.held[0][:9] + "…"
            more = f" 等 {len(self.held)} 条" if len(self.held) > 1 else ""
            text += f" · 下一轮先交：{first}{more}（输入 /undo 撤回）"
        line.update(Text(text + " ", style="bold" if fresh else "", no_wrap=True, overflow="ellipsis"))
        line.set_class(fresh, "mine")
        inp = self.query_one("#command", Input)
        inp.set_class(fresh, "mine")
        inp.placeholder = ("这一轮做什么" if fresh else
                           "还能交一条，空行回车结束" if self.my_turn else
                           "守秘人在讲，现在交的留到下一轮" if self.seat else
                           "观战模式下可用 // 开头发送场外消息")
        if self.seat:
            inp.placeholder += " · 默认公开，/sc 私信"

    @work(group="offer")
    async def _offer(self, offer: dict[str, Any]) -> None:
        """Your dice, or your push: in a worker, so what the others do keeps coming in while you
        decide (waiting here held every message behind the dialog, 2026-10-21)."""
        await self.make_way()  # a big scene steps aside
        if offer.get("kind") in ("reaction", "redeclare"):  # a fight stops to ask this seat how it answers
            from ..modals.reaction import ChoiceModal

            choice = await self.app.push_screen_wait(ChoiceModal(offer))
        else:
            choice = await self.app.push_screen_wait(RollModal(offer) if offer.get("kind") == "roll" else PushModal(offer))
        if choice is None:  # the time ran out, or the host settled it: the host takes what was set, nothing goes back
            return
        answer = {"t": "respond", "id": offer.get("respond_id", "")}  # the host takes it once, for this offer only
        if isinstance(choice, dict) and offer.get("kind") in ("reaction", "redeclare"):
            answer |= {"choice": choice.get("choice", ""), "text": choice.get("text", ""), "ask": choice.get("ask", "")}
        else:
            answer["choice"] = choice
        await self.link.send(answer)

    def _viewer(self):
        """Your own investigator's sheet; when watching, whoever is acting."""
        if self.seat:
            return self.state.find(self.seat)
        return self.state.investigator

    def _battle_now(self) -> dict | None:
        """The fight or the chase as the host sent it (engine/encounter/views.table): this screen has no game."""
        return None if self.state.ending else self.state.encounter_public

    def _in_battle(self) -> bool:
        return self._battle_now() is not None

    def _set_react(self, react: str) -> None:
        """Tab at a shared table: the host sets it (Hub "react"); its state comes back with the answer."""
        self.run_worker(self.link.send({"t": "react", "react": react}), group="react")

    def _sync(self, view: dict[str, Any]) -> None:
        updated = GameState.model_validate(view)
        if self._pending_module and any(location.id == updated.location for location in self._pending_module.locations):
            self.m, self._pending_module = self._pending_module, None
        if not any(location.id == updated.location for location in self.m.locations):
            self._pending_state = updated
            return  # keep a coherent screen until this location's module envelope arrives
        previous = self.state
        self.state, self._pending_state = updated, None
        if (previous.location, previous.view) != (self.state.location, self.state.view):
            self.view = (self.state.location, self.state.view)
        if any(location.id == self.state.location for location in self.m.locations):
            self._scene(self.state.location)
        self._panels()
        self._battle_synced()

    def _may_order(self) -> bool:
        """The round's choices come up while the round is open to this seat and nothing of theirs is in it."""
        return bool(self.seat) and self.pool_open and self.my_turn and not self.pooled

    def _battle_synced(self) -> None:
        """A fight or a chase as the Hub's state now has it. This screen hears of one from its events
        before its state does (the Hub sends the state after the turn), so at fight_start the list had
        nothing to offer and the heartbeat stopped at once: both come with the state, and the list with
        every round that opens to this seat; it goes when the round closes."""
        st = self.state
        if st.ending or self._battle_now() is None:
            self._close_orders()
            return
        self._heartbeat()
        self._topbar()
        if not self._may_order():
            self._close_orders()
        elif not self._battle_picking:
            self._offer_orders()
        else:
            self._react_shown()  # the answer to blows as the host now has it (Tab)

    async def on_msg(self, msg: dict[str, Any]) -> None:
        if not self.ready:
            self.inbox.append(msg)
            return
        t = msg["t"]
        if self.has_class("-dev") and t != "event":  # an event's own turn comes in handle()
            self._dev()
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
        elif t == "prelude":  # the opening performance, played while the host prepares the opening
            self.m.art.update(msg.get("art", {}))
            self.m.art_colors.update(msg.get("colors", {}))
            self.m.pictures.update({k: path for k, data in msg.get("picture_data", {}).items() if (path := media.unpack(data))})
            self._prelude_ready = asyncio.get_running_loop().create_future()
            shots = [PreludeFrame.model_validate(f) for f in msg.get("frames", [])]
            self.app.push_screen(PreludeScreen(self.m, self._prelude_ready, shots=shots, style=self.app.art_style,  # type: ignore[attr-defined]
                                               animations=config.load().animations))
        elif t == "prelude_ready":
            if (ready := getattr(self, "_prelude_ready", None)) is not None and not ready.done():
                ready.set_result(None)
        elif t == "module":  # the director added places and people
            m = Module.model_validate(msg["module"])
            m.art, m.art_colors, m.pictures = self.m.art, self.m.art_colors, self.m.pictures
            m.art.update(msg.get("art", {}))
            m.art_colors.update(msg.get("colors", {}))
            matching = self._pending_state or self.state
            if not any(location.id == matching.location for location in m.locations):
                self._pending_module = m
            else:
                self.m, self._pending_module = m, None
            if self._pending_state and any(location.id == self._pending_state.location for location in self.m.locations):
                self._sync(self._pending_state.model_dump(mode="json"))
            elif any(location.id == self.state.location for location in self.m.locations):
                self._scene(self.state.location)
        elif t == "sync":
            self._sync(msg["state"])
        elif t == "pool":
            was_open, held = self.pool_open, len(self.held)
            self.pool_open, self.pool = bool(msg["open"]), msg
            left = msg.get("closes_in")  # the round's moment, once someone acted (net/hub.py WINDOW)
            self.closes = time.monotonic() + left if self.pool_open and left is not None else None
            self.waiting_on = msg.get("waiting", [])
            mine = msg.get("mine") or {}  # the Hub's word on your own lines
            self.pooled = list(mine.get("now", [])) if self.pool_open else []
            self.held = list(mine.get("next", []))
            self.my_turn = self.pool_open and bool(self.seat) and self.seat in msg.get("open_to", [])
            if len(self.held) > held:
                self.app.notify("本轮行动名额已满，已排入下一轮。" if self.pool_open else "正在叙述，行动已排入下一轮。", timeout=4)
            if self.pool_open and not was_open:
                self.settling = ""
                self.engine_label = "局域网联机 · 新的一轮"
                self._topbar()
                if self.seat:
                    self.app.notify("新回合已开始，预先输入的行动已提交。" if self.pooled else "新的一轮", timeout=4)
                    self.query_one(Input).focus()
            self._turnline()
            self._battle_synced()  # in a fight or a chase: this round's choices, or none once theirs is in
        elif t == "turn":  # the pool is closed: the keeper settles this seat's action
            self._sync(msg["state"])
            self.my_turn = False
            mine = bool(self.seat) and msg["investigator"] == self.seat
            who = "你的行动" if mine else f"{msg['name']}（{msg['player'] or 'AI'}）"
            self.settling = who + (f"（{msg['index']}/{msg['total']}）" if msg.get("total", 0) > 1 else "")
            self.engine_label = f"局域网联机 · {self.settling}"
            self._topbar()
            self._turnline()
        elif t == "offer":  # throw your own dice, or push / spend luck after a failure
            self._offer(msg["offer"])
        elif t == "offer_closed":  # the host stopped waiting and took the default: that window goes, sending nothing
            for screen in list(self.app.screen_stack):
                asked = getattr(screen, "offer", None) or getattr(screen, "ask", None)
                if isinstance(screen, Timed) and isinstance(asked, dict) and asked.get("respond_id") == msg.get("id"):
                    screen.settled()
        elif t == "say":
            self.story.add("system", Text(f"【场外】{msg['from']}：{msg['text']}", style="italic dim"))
        elif t == "media_sync" and msg.get("seat") == self.seat:
            self._media_slots.clear()
            self._media_papers.clear()
            for ev in msg.get("papers", []):
                self._present(self._media_unpacked(ev), display=False)
            for ev in msg.get("slots", {}).values():
                self._present(self._media_unpacked(ev))
            self._scene(self.state.location)
        elif t == "history" and msg.get("seat") == self.seat:
            # The public backlog arrived before this seat was chosen. Restore only the private
            # entries the Hub sent for it, without repeating them if the reply arrives twice.
            restored = [LogEntry.model_validate(e) for e in msg.get("log", [])]
            known = Counter(e.model_dump_json() for e in self.backlog)
            for entry in restored:
                key = entry.model_dump_json()
                if known[key]:
                    known[key] -= 1
                    continue
                self.backlog.append(entry)
                block = self.story.add("keeper" if entry.kind == "keeper" else "system", history_text(entry),
                                       delivery=entry.delivery)
                if (line := log_line(entry)) is not None:
                    self.query_one(HistoryPanel).note(line, block, entry.turn)
        elif t == "notice":  # an answer that came too late, or twice: the host ignored it
            self.app.notify(msg.get("text", ""), severity="warning", timeout=4)
        elif t == "ideas":  # for this screen alone
            if self.ideas_due is not None and not self.ideas_due.done():
                self.ideas_due.set_result(msg.get("ideas", []))
        elif t == "over":
            self._sync(msg["state"])
            self.engine_label = "局域网联机 · 本局结束"
            self._topbar()
            if msg.get("ending"):
                self.story.add("system", Text("本局已结束：F10 查看复盘、真相、未发现线索与其他结局" + ("；.en 做成长检定" if self.seat else ""), style="dim"))
        elif t == "error":
            if msg.get("retry"):
                self.status_text = ""
                self.retry_due = True
                self.story.pause_keeper()
            self.app.notify(msg["text"], severity="warning")
        elif t == "closed":
            self.pool_open, self.my_turn = False, False
            self.engine_label = "局域网联机 · 连接已断开"
            self._topbar()
            self.app.notify(msg.get("text") or "与房主的连接已断开。", severity="error", timeout=10)
        elif t == "unsent":
            original = msg.get("message", {})
            body, kind = str(original.get("text") or ""), original.get("t")
            retry = body if kind in ("act", "speak") else ""
            if kind == "kp_private":
                retry = "/sc " + body
            elif kind == "say":
                retry = "// " + body
            elif kind == "speak" and original.get("to"):
                retry = f"/w {original['to'][0]} {body}"
            if retry:
                command = self.query_one("#command", Input)
                if not command.value:
                    command.value = retry
                self.story.add("system", Text(f"【未发送】{retry}"))
            self.app.notify("消息未发送，连接已断开。请重新加入房间；待答选择会在入座后恢复。", severity="error", timeout=10)

    async def handle(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "presentation":
            self._present(self._media_unpacked(ev))
            return
        if t == "aside":
            self.story.add("keeper", Text(f"【守秘人·私密】{ev['text']}"))
            return
        if t == "speech" and ev.get("channel") in ("whisper", "note"):
            who = f"{ev['who']}（{ev['player']}）" if ev.get("player") else ev["who"]
            label = "私语" if ev["channel"] == "whisper" else "纸条"
            self.story.add("player", Text(f"▷ 【{label}】{who} → {ev.get('to') or '收件人'}：{ev['text']}"))
            return
        if t == "said":
            who = f"{ev['who']}（{ev.get('player') or 'AI'}）"
            text = ev["text"]
            self.story.add("system" if text.startswith("/kp ") else "player",
                           Text(f"▶ {who} → KP：{text[4:]}" if text.startswith("/kp ") else f"▶ {who}：{text}"))
            return
        if t == "encounter_wait":  # the table waits for one player's answer in a fight
            what = {"reaction": "应对攻击", "redeclare": "改选行动", "roll": "掷骰"}.get(ev.get("kind", ""), "选择")
            limit = f"，最多 {int(ev['closes_in'])} 秒" if ev.get("closes_in") else ""
            self.app.notify(f"等待{ev.get('who') or '其他玩家'}{what}{limit}。", timeout=4)
            return
        if t == "retold":  # the keeper is about to tell the last narration again, for everyone
            self.app.notify(f"{ev.get('who') or '有人'}请守秘人重讲上一段" + ("" if ev.get("nudge") == "换个讲法" else f"：{ev.get('nudge')}"), timeout=4)
            return
        if t == "handout":  # hidden from this screen until now
            if found := next((x for x in self.m.handouts if x.id == ev["id"]), None):
                found.title, found.text = ev["title"], ev["text"]
        await super().handle(ev)

    @staticmethod
    def _media_unpacked(event: dict) -> dict:
        ev = copy.deepcopy(event)
        payload = ev.get("payload", {})
        if payload.get("kind") == "handout":
            payload["pictures"] = {key: path for key, data in payload.pop("picture_data", {}).items()
                                   if (path := media.unpack(data, max_size=1600))}
        elif payload.get("kind") == "image":
            payload["picture"] = media.unpack(payload.pop("picture_data", ""), max_size=1600)
        return ev

    def action_private_message(self) -> None:
        if not self.seat or self.state.ending:
            self.app.notify("当前无法发送私信。", severity="warning")
            return
        nearby = set(audience.listeners(self.state, self.seat))
        recipients = [(inv.name, f"seat:{inv.id}") for inv in self.state.investigators
                      if inv.id != self.seat and inv.id in nearby]
        self.app.push_screen(PrivateMessageModal(recipients), self._private_composed)

    def _private_composed(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        target, body = result
        packet = ({"t": "kp_private", "text": body, "request": uuid.uuid4().hex} if target == "kp" else
                  {"t": "speak", "text": body, "to": [target.removeprefix("seat:")], "channel": "whisper"})
        self.run_worker(partial(self.link.send, packet))

    def send_line(self, text: str) -> None:
        word = text.split()[0].lower() if text.split() else ""
        if word in PRIVATE_COMMANDS:
            private_line(self, text)
        elif word in ("/sc", "/私信kp"):
            body = text.partition(" ")[2].strip()
            if not self.seat:
                self.app.notify("观战模式下无法向守秘人私信。", severity="warning")
            elif not body:
                self.action_private_message()
            else:
                self.run_worker(partial(self.link.send, {"t": "kp_private", "text": body, "request": uuid.uuid4().hex}))
        elif (aside := chat.ooc(text)) is not None:  # out of character: to everyone, spectators too, never an action
            if aside:
                self.run_worker(partial(self.link.send, {"t": "say", "text": aside}))
        elif word == "/undo" and self.seat and not self.state.ending:
            if self.pooled or self.held:  # the Hub takes back your latest: the next round's first
                self.run_worker(partial(self.link.send, {"t": "undo"}))
            else:
                self.app.notify("暂无可撤回的行动。")
        elif word in ("/done", "/pass"):
            self.empty_enter()
        elif text.startswith("/kp "):
            if self.seat:
                self.run_worker(partial(self.link.send, {"t": "act", "text": text}))
            else:
                self.app.notify("观战模式下无可操作的调查员。")
        elif text.startswith("/"):
            self._slash(text)
        elif not self.seat:
            self.app.notify("观战模式下可用 // 开头发送场外消息。")
        elif self.state.ending and not commands.instant(text):  # after the end: growth rolls (.en), the sheet
            self.app.notify("本局已结束。F10 查看复盘，.en 进行成长检定。")
        elif chat.said(text, [i.name for i in self.state.investigators if i.id != self.seat]):  # to the party: no slot, no keeper
            self.run_worker(partial(self.link.send, {"t": "speak", "text": text}))
        elif self.state.ending or commands.instant(text):  # .r, .st: not an action; while the keeper tells, right after
            if not self.pool_open and not self.state.ending:
                self.app.notify("本轮叙述结束后可掷骰。", timeout=3)
            self.run_worker(partial(self.link.send, {"t": "act", "text": text}))
        elif not self.my_turn and len(self.held) >= 2:
            self.app.notify("下一轮已有两条行动，无法继续添加。")
        else:  # in this round if it has room for you, else first in the next: the Hub says which
            self.run_worker(partial(self.link.send, {"t": "act", "text": text}))

    def empty_enter(self) -> None:
        """Enter on an empty box while the pool is open: nothing more from you this round."""
        if self.pool_open and self.seat:
            self.my_turn = False
            self._turnline()
            self.run_worker(partial(self.link.send, {"t": "done"}))

    def action_idea(self) -> None:
        """Ctrl+L, /灵感 or the menu at a shared table: asked for this seat; the Hub says if it cannot be made."""
        if not self.seat:
            self.app.notify("观战模式下无可操作的调查员。")
        elif self.state.ending:
            self.app.notify("本局已结束。")
        else:
            self.run_worker(partial(self.link.send, {"t": "act", "text": idea.WORDS[0]}))

    def action_retry(self) -> None:
        if self.seat and self.retry_due:
            self.retry_due = False
            self.run_worker(partial(self.link.send, {"t": "act", "text": "/retry"}))
        else:
            self.app.notify("等待房主恢复当前回合后可重试。")

    # A rule plugin's question comes to this screen only when it is for your seat (Hub._event);
    # the answer and a plugin's /command go to the host, where the plugin runs.
    def prompt_is_mine(self, who: str) -> bool:
        return bool(self.seat) and who == self.seat

    def plugin_answer(self, pid: str, value, by: str) -> None:
        self.run_worker(partial(self.link.send, {"t": "answer", "prompt": pid, "value": value, "skip": by == "skip"}))

    def plugin_command(self, name: str, text: str) -> None:
        if not self.seat:
            self.app.notify("观战模式下无可操作的调查员。")
            return
        self.run_worker(partial(self.link.send, {"t": "command", "name": name, "text": text}))

    def action_suggest(self) -> None:
        if self.seat:
            super().action_suggest()
        else:
            self.app.notify("观战模式下无可操作的调查员。")

    def action_recount(self) -> None:
        """Ctrl+Y: the host writes the story so far now; every screen gets it with the state."""
        if self.seat:
            self.run_worker(partial(self.link.send, {"t": "recount"}))
        else:
            self.app.notify("观战模式下由房主自动整理调查进展。")

    async def ideas_for(self, inv) -> list[str]:
        """The host's investigator agent thinks for this seat (Hub._ideas), from the same view
        the AI seats get; the answer comes back to this screen alone."""
        self.ideas_due = asyncio.get_running_loop().create_future()
        await self.link.send({"t": "suggest"})
        return await asyncio.wait_for(self.ideas_due, IDEAS_WAIT)

    # The host runs the agents: switching models, sketches, recaps and saving happen there.
    def action_engine(self) -> None:
        if not self.link.host:
            self.app.notify("联机模型由房主配置。")
            return
        from ..modals.settings import model_choices

        cfg, saved = config.load(), config.ui_settings()
        model, fallback = os.environ.get("AUTOKEEPER_MODEL", saved.get("model", "")), os.environ.get("AUTOKEEPER_FALLBACK", saved.get("fallback", ""))
        self.app.push_screen(EngineModal(model_choices(), cfg.preset, model, fallback, cfg.mode), self._ask_engine)

    def _ask_engine(self, choice: dict | None) -> None:
        if choice:
            self.run_worker(partial(self.link.send, {"t": "engine", "choice": choice}))

    def action_sketch(self) -> None:
        self.app.notify("联机模式暂不支持手绘。")

    def action_recap(self) -> None:
        self.app.notify("前情回顾由房主发起。")

    def action_save(self) -> None:
        self.app.notify("房主设备每回合自动保存进度。")

    # Telling again and the epilogue happen on the host too, asked for from here.
    def action_retell(self, nudge: str = "") -> None:
        if not self.seat:
            self.app.notify("观战模式不支持请求重述。")
        elif nudge and nudge not in RETELL:
            self.app.notify(f"可选重述方式：{'、'.join(RETELL)}", severity="warning")
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
        self.app.notify("联机叙事偏好由房主配置。")

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
        self.app.notify("模型调用由房主设备发起，用量记录保存在房主设备。")

    # -- 开发者模式 on the host's own screen ---------------------------------------------------------
    # The room this app opened (app.room, net/room.py) runs its Hub, and the Hub's Game, in a thread
    # with a loop of its own. The host's screen reads that game there (Room.run): the panel is made
    # on the Hub's loop and only shown here, and what changes the game (a house rule, a pause) goes
    # to the Hub as the host's message. Nothing of it goes to the other screens.
    def _room(self) -> lan.Room | None:
        """The room this app opened, when this screen is its host's."""
        room = getattr(self.app, "room", None)
        if not self.link.host or room is None or room.hub is None or not room.is_open:
            return None
        return room if urlsplit(self.link.url).port == room.port else None  # not another room on this machine

    @property
    def may_develop(self) -> bool:
        return self._room() is not None

    async def _on_hub(self, work: Callable[[Hub], Any]) -> Any:
        """work(hub) called on the Hub's loop (awaited there if it gives an awaitable), its result here.
        RuntimeError when the room has closed, TimeoutError when its loop is held up."""
        room = self._room()
        if room is None:
            raise RuntimeError("房间已关闭")
        hub = room.hub  # taken here: the Hub's thread has no app to ask

        async def there() -> Any:
            out = work(hub)
            return await out if inspect.isawaitable(out) else out

        return await asyncio.wait_for(asyncio.wrap_future(room.run(there())), HUB_WAIT)

    def _dev(self) -> None:
        """The panel again, from the Hub's game; asked for while it is being made, once more after."""
        if self._peeking:
            self._peek_again = True
            return
        self._peeking = True
        self.run_worker(self._peek(), group="devpanel", exit_on_error=False)

    async def _peek(self) -> None:
        from ..widgets.devpanel import DevPanel, view

        url, errors = self.dev_url, list(self.dev_errors)

        def made(hub: Hub) -> Text:  # on the Hub's loop: nothing changes the game while it is read
            if (server := getattr(hub.game, "debug_server", None)) is not None:
                server.busy = hub.started.is_set() and not hub.open and not hub.over  # the keeper is settling
            return view(hub.game, url, errors)

        try:
            while True:
                self._peek_again = False
                try:
                    text = await self._on_hub(made)
                except (RuntimeError, TimeoutError):  # the room closed, or its loop is held up: the panel stays
                    return
                self.query_one(DevPanel).put(text)
                if not self._peek_again:
                    return
                url, errors = self.dev_url, list(self.dev_errors)
        finally:
            self._peeking = False

    async def _dashboard(self) -> str:
        """The developer web page on the Hub's game, served from the Hub's loop."""
        from ...engine.developer_web import open_dashboard

        errors = self.dev_errors
        return await self._on_hub(lambda hub: open_dashboard(hub.game, errors=errors))

    @work(exclusive=True, group="plugins")
    async def action_plugins(self) -> None:
        """Ctrl+K or 设置 → 规则插件 in the host's 开发者模式: a copy of the Hub's rules, made on its loop."""
        from ..modals.plugins import PluginsModal

        if not self.developing:
            return

        def copied(hub: Hub) -> tuple[Module, GameState]:
            m, st = hub.m, hub.state
            module = m.model_copy(update={"plugins": list(m.plugins), "extensions": dict(m.extensions),
                                          "extension_errors": dict(m.extension_errors)})
            # Only what the window reads, copied: the rest of the Hub's state stays where it is.
            state = st.model_copy(update={"plugin_errors": dict(st.plugin_errors), "plugin_status": dict(st.plugin_status),
                                          "plugin_log": copy.deepcopy(st.plugin_log),
                                          "plugin_panels": copy.deepcopy(st.plugin_panels)})
            return module, state

        try:
            module, state = await self._on_hub(copied)
        except (RuntimeError, TimeoutError):
            self.app.notify("房间已关闭。", severity="warning")
            return
        self.app.push_screen(PluginsModal(module, state, manage=True, send=self._ask_house_rule, toggle=self._pause_plugin))

    def _ask_house_rule(self, text: str) -> None:
        """The host's own message to the Hub (Hub._house_rules drafts and installs it between actions)."""
        self.run_worker(partial(self.link.send, {"t": "house_rule", "text": text}))

    def _pause_plugin(self, identity: str, paused: bool) -> None:
        self.run_worker(partial(self.link.send, {"t": "pause_plugin", "id": identity, "paused": paused}))
