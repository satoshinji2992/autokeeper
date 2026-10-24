"""准备房间: the lobby of a LAN room. Who sits where and who each investigator is (portrait, the
whole sheet), how friends get in (every address of the host, the page for browsers), the version
and the round's rule, and the module as the players may read it: the Hub's table copy (net/hub.py
table()), never the truth, the keeper's notes or anyone's secrets.

Take a free seat, watch, or start with the empty seats played by the AI; the host who opened the
room in this app can also close it. Before a new game starts, the player in a seat may choose who
they play (选角色, tui/screens/seatcard.py): one of their own cards, a free pregen, or one made here on the
host's models (the card screen of tui/screens/card.py with a net/cards.py RemoteBuilder); then say they are
ready. Everyone ready, the game begins."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, OptionList, Static
from textual.widgets.option_list import Option

from ... import __version__, imagery, roster
from ...engine.state import GameState, LogEntry
from ...module.schema import Module
from ...net import media
from ...net import room as lan
from ...net.link import Link
from ...rules.coc import Investigator
from ..library import art_text, face_key, face_text
from ..modals.sheet import SheetModal, sheet_view
from ..widgets.waiting import wait_on
from .net import NetGameScreen, leave, table_module, with_private_history

NARROW = 96  # fewer columns than this: no sheet beside the seats (人物卡 opens it)
CONNECT_SECONDS = 10  # joining a room gives up after this


def gist(inv: Investigator, width: int = 40) -> str:
    """One line of who they are: the first thing their backstory says."""
    for key, value in inv.backstory.items():
        if key != "说明" and value.strip():
            line = value.strip().removeprefix("AutoKeeper 补充：").splitlines()[0]
            return line if len(line) <= width else line[: width - 1] + "…"
    return ""


def clip(text: Text, width: int) -> Text:
    """Each line cut to the width with an ellipsis: an option's lines never wrap."""
    lines = []
    for line in text.split("\n"):
        line.truncate(max(4, width), overflow="ellipsis")
        lines.append(line)
    return Text("\n").join(lines)


class CloseRoomModal(ModalScreen[bool]):
    DEFAULT_CSS = "#close-room { max-width: 60; }"
    BINDINGS = [Binding("escape", "dismiss(False)", "取消")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="close-room"):
            yield Static("关闭房间？", classes="modal-title")
            yield Static("关闭后所有玩家断开连接，网页版同时停止。存档保留，可在下次创建房间时继续。", classes="modal-body")
            with Horizontal(classes="buttons"):
                yield Button("关闭房间", id="yes", variant="error")
                yield Button("取消", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed)
    def chosen(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class LobbyScreen(Screen):
    """Connected to a room: choose a seat, watch, or start; then the game screen."""

    CSS_PATH = "room.tcss"
    BINDINGS = [
        Binding("escape", "leave", "离开"),
        Binding("w", "watch", "观战"),
        Binding("s", "start", "开始游戏"),
        Binding("c", "sheet", "人物卡"),
        Binding("r", "pick", "选角色 · 车卡"),
        Binding("y", "ready", "准备"),
        Binding("x", "close_room", "关闭房间"),
        Binding("h", "help", "遇到问题"),
    ]

    def __init__(self, address: str, name: str, *, key: str = "", room: lan.Room | None = None):
        super().__init__()
        self.link = Link(address, name, key)
        self.room = room  # opened by this app: its host may close it here
        self.welcome: dict[str, Any] | None = None
        self._welcomed = asyncio.Event()
        self._watching = False
        self.m: Module | None = None  # the table's copy, with its art and pictures
        self.state: GameState | None = None
        self.seats: list[dict[str, Any]] = []
        self.chosen: str | None = None  # the seat taken, "" for watching
        self.pending_seat: str | None = None  # wait for its personal snapshot before entering the game
        self.gone = False
        self.faces: dict[tuple[str, str, str], Text] = {}  # picture key, style and source version
        self.width, self.short = 40, False  # the seat list's lines: how wide, and whether two a seat
        self.lit: int | None = None  # the seat drawn for the cursor
        self._game_handler: Any = None  # the game screen's, once the link is handed on to it
        self.bring = False  # seats may still change their investigator (a new game, not begun)
        self.remote: Any = None  # net/cards.py RemoteBuilder  # the card being made on the host's models
        self.guest = bool(os.environ.get("AUTOKEEPER_GUEST"))  # a browser's seat, on the host's machine

    def compose(self) -> ComposeResult:
        with Vertical(id="lobby"):
            yield Static(f"正在连接 {self.link.url} …", id="lobby-head")
            with Horizontal(id="lobby-body"):
                with Vertical(id="lobby-left"):
                    yield OptionList(id="seats")
                    yield Static(id="lobby-investigator")
                    yield Static(id="lobby-hint")
                    with VerticalScroll(id="lobby-info"):
                        yield Static(id="lobby-room")
                        yield Static(id="lobby-module")
                with VerticalScroll(id="lobby-card"):
                    yield Static(id="lobby-sheet")
            with Vertical(id="lobby-controls"):
                with Horizontal(id="lobby-actions"):
                    yield Button("入座", id="sit", variant="primary", compact=True)
                    yield Button("选角色 · 车卡", id="pick", variant="primary", compact=True)
                    yield Button("准备", id="ready", compact=True)
                    yield Button("观战", id="watch", compact=True)
                    yield Button("人物卡", id="sheet", compact=True)
                    yield Button("离开", id="leave", compact=True)
                    yield Button("遇到问题", id="help", compact=True)
                    yield Button("关闭房间", id="close_room", variant="error", compact=True)
                with Horizontal(id="lobby-start"):
                    yield Button("开始游戏", id="start", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#seats", OptionList).border_title = "座位"
        self.query_one("#close_room", Button).display = self.room is not None
        if self.room is not None:
            self.query_one("#help", Button).label = "联机故障排查"
        self.query_one("#lobby-info").border_title = "房间"
        self._connect()

    @work(exclusive=True, group="connect")
    async def _connect(self) -> None:
        """In the background, ten seconds at most: a wrong address that still routes somewhere held
        the lobby frozen for up to five minutes, Esc included (2026-10-21)."""
        waiting = wait_on(self.query_one("#lobby-head", Static), f"正在连接 {self.link.url}", style="")
        try:
            async with asyncio.timeout(CONNECT_SECONDS):
                await self.link.connect()
                self.link.handler = self.on_msg
                self.app.run_worker(self.link.pump, name="link", group="link", exit_on_error=False)
                await self._welcomed.wait()
        except Exception:  # wrong address, host not up yet, a firewall in between
            waiting.stop()
            await self.link.close()
            self.app.notify("无法连接这个房间。请检查地址和网络，或查看「遇到问题」。", title="连接房间失败",
                            severity="error", timeout=8)
            if self.room is not None:
                self.app.pop_screen()
            else:
                from .net import JoinScreen

                self.app.switch_screen(JoinScreen(self.link.url, self.link.name))
            return
        waiting.stop()

    async def on_unmount(self) -> None:
        if self.link.handler != self._game_handler:  # not handed on to the game screen: hang up
            await self.link.close()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < NARROW, "-narrow")
        if self.welcome is not None:
            self.call_after_refresh(self._show_all)

    # -- what arrives -----------------------------------------------------------------------
    async def on_msg(self, msg: dict[str, Any]) -> None:
        t = msg["t"]
        if t == "welcome":
            self.welcome = msg
            self.link.host = bool(msg.get("host"))
            if (theirs := msg.get("version")) and theirs != __version__:
                self.app.notify("客户端与房主版本不一致，可能影响联机。", severity="warning", timeout=12)
            self.m = table_module(msg)
            self.state = GameState.model_validate(msg["state"])
            self.seats = msg["seats"]
            self.bring = bool(msg.get("bring"))
            self._show_all()
            if not self.guest and self.room is None:
                lan.remember(address=self.link.url, name=self.link.name)
            self._welcomed.set()
        elif t == "seats":
            self.seats = msg["seats"]
            self.bring = bool(msg.get("bring", self.bring))
            self._list_seats()
            self._head()
            self._card()
        elif t == "watching":
            self.chosen = ""
            self._watching = False
            self.welcome = msg
            self.state = GameState.model_validate(msg["state"])
            self.seats = msg["seats"]
            self.bring = bool(msg.get("bring"))
            if self.started:
                self._enter([])
            else:
                self._show_all()
        elif t == "seated":  # a confirmed seat, or a brought card's replacement investigator
            self.chosen = msg["investigator"]
            self.pending_seat = None
            if "state" in msg:
                self.welcome = msg
                self.m = table_module(msg)
                self.state = GameState.model_validate(msg["state"])
                self.seats = msg["seats"]
                self.bring = bool(msg.get("bring"))
                if self.started:
                    self._enter([msg["pool"]])
                else:
                    self._show_all()
                    if any(s.get("blank") and s["investigator"] == self.chosen for s in self.seats) and self.bring:
                        self.action_pick()
        elif t == "history" and self.welcome is not None and msg.get("seat") == self.chosen:
            self.welcome["log"] = with_private_history(self.welcome.get("log", []), msg.get("log", []))
        elif t == "media_sync" and self.welcome is not None and msg.get("seat") == self.chosen:
            self.welcome["media"] = msg
        elif t == "face" and self.m is not None:  # the picture of a card someone brought
            if path := media.unpack(msg.get("picture_data", "")):
                self.m.pictures[str(msg.get("key", ""))] = path
                self._card()
        elif t == "card" and self.remote is not None:
            self.remote.reply(msg)
        elif t in ("sync", "turn") and self.welcome is not None:
            self.welcome["state"] = msg["state"]  # keep up while still choosing
            self.state = GameState.model_validate(msg["state"])
            self._card()
        elif t == "module" and self.welcome is not None and self.m is not None:  # found places and people, as the game screen takes them
            m = Module.model_validate(msg["module"])
            m.art, m.art_colors, m.pictures = self.m.art, self.m.art_colors, self.m.pictures
            m.art.update(msg.get("art", {}))
            m.art_colors.update(msg.get("colors", {}))
            self.m, self.welcome["module"] = m, msg["module"]
            self._show_all()
        elif t == "begin" and self.welcome is not None:
            self.welcome["started"], self.bring = True, False
            if self.chosen is None:
                self._show_all()
        elif t == "error":
            if self.pending_seat is not None:
                self.pending_seat = None
                self._buttons()
            self.app.notify(msg["text"], severity="warning")
        elif t == "closed" and self.remote is not None:
            self.remote.reply(msg)
        elif t == "unsent":
            self.pending_seat = None
            if self.remote is not None:
                self.remote.reply({"t": "closed"})
            self.app.notify("消息未发送，连接已断开。请重新加入房间。", severity="error", timeout=10)
        if t == "closed" and not self.gone:
            self.gone = True
            self.app.notify(msg.get("text") or "与房主的连接已断开。", severity="error")
            self.query_one("#lobby-hint", Static).update(Text("连接已中断，按 Esc 返回。", style="bold"))
            self._buttons()
        if (t in ("begin", "turn", "event") and self.chosen is not None and self.pending_seat is None
                and self.welcome is not None and not self.gone):
            self._enter([msg])

    @property
    def started(self) -> bool:
        return bool(self.welcome and self.welcome.get("started"))

    def _investigator(self, inv_id: str) -> Investigator | None:
        return next((i for i in self.state.investigators if i.id == inv_id), None) if self.state else None

    def _highlighted(self) -> dict[str, Any] | None:
        ol = self.query_one("#seats", OptionList)
        if ol.highlighted is None or not ol.option_count:
            return None
        oid = ol.get_option_at_index(ol.highlighted).id or ""
        return next((s for s in self.seats if s["investigator"] == oid), None)

    # -- drawing ----------------------------------------------------------------------------
    def _show_all(self) -> None:
        self._head()
        self._list_seats()
        self._info()
        self._card()

    def _head(self) -> None:
        if self.welcome is None:
            return
        title = self.welcome["module"].get("title", "")
        players = [s for s in self.seats if s["human"]]
        left = Text(no_wrap=True, overflow="ellipsis")
        left.append(" 准备房间 ", style="bold reverse")
        left.append(f"  《{title}》", style="bold")
        right = Text(no_wrap=True)
        if self.link.host:
            right.append("你是房主 · ", style="bold")
        right.append(f"{sum(s['online'] for s in players)}/{len(players)} 位玩家在座 · ")
        right.append("已开局" if self.started else "等待玩家入座")
        bar = Table.grid(expand=True)
        bar.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        bar.add_column(justify="right", no_wrap=True)
        bar.add_row(left, right)
        self.query_one("#lobby-head", Static).update(bar)

    def _seat_line(self, seat: dict[str, Any], lit: bool = False, number: int = 0) -> Text:
        """The seat, who sits in it, and whether they are ready; the investigator is shown under the
        list (_investigator_line). `lit`: under the cursor, where only its own colours read well."""
        t = Text(f"座位 {number}  ", style="bold")
        if not seat["human"]:
            t.append("AI", style="" if lit else "bold cyan")
            return clip(t, self.width)
        t.append("玩家  ", style="" if lit else "bold yellow")
        ready = " · 已准备" if seat.get("ready") else " · 未选择角色" if seat.get("blank") else ""
        if seat["investigator"] == self.chosen:
            t.append(f"{self.link.name}（你）{ready}", style="bold" if lit else "bold green")
        elif seat.get("ai_takeover"):
            t.append("AI 临时代管", style="" if lit else "cyan")
        elif seat["online"]:
            t.append(f"{seat['player']}{ready}", style="" if lit else "green")
        else:
            t.append("待重连" if self.started else "待入座", style="" if lit else "dim")
        brought = {"own": "自有角色", "new": "新建角色"}.get(seat.get("origin", ""), "")
        if self.link.host and (brought or seat.get("note")):  # who brought what, for the host
            t.append("  " + " · ".join(x for x in (brought, seat.get("note", "")) if x), style="" if lit else "dim")
        return clip(t, self.width)

    def _investigator_line(self) -> None:
        """Under the seats: the highlighted seat's investigator, and how to change your own."""
        seat = self._highlighted()
        line = Text("调查员：", style="bold")
        inv = self._investigator(seat["investigator"]) if seat and not seat.get("blank") else None
        if seat is None:
            line = Text("")
        elif inv is None:
            line.append("未选择", style="dim")
        else:
            line.append(inv.name, style="bold")
            line.append(f" · {seat.get('occupation') or inv.occupation} · {inv.age} 岁", style="dim")
        if seat is not None and seat["investigator"] == self.chosen and self.bring and not seat.get("ready"):
            line.append("  R 选择角色或车卡", style="dim")
        self.query_one("#lobby-investigator", Static).update(clip(line, self.width))

    def _list_seats(self) -> None:
        ol = self.query_one("#seats", OptionList)
        kept = ol.highlighted if ol.option_count else None  # someone else sitting down must not move your cursor
        room = ol.scrollable_content_region.width
        self.width = max(20, room - 2 if room > 4 else self.size.width - 8)
        self.short = self.size.height < 32 and len(self.seats) > 3  # a short terminal: two lines a seat
        free = next((i for i, s in enumerate(self.seats) if s["human"] and not s["online"]), 0)
        at = kept if kept is not None and kept < len(self.seats) else free  # first time: Enter takes the first free seat
        ol.clear_options()
        for i, s in enumerate(self.seats):  # an AI's or a taken seat can still be looked at, not sat in
            ol.add_option(Option(self._seat_line(s, i == at, i + 1), id=s["investigator"]))
        self.lit = at
        ol.highlighted = at
        if self.focused is None or self.focused is ol:
            ol.focus()
        if self.gone:
            return
        if self.started:
            hint = "游戏已开始，可选择空座加入或观战。"
        elif self.chosen == "":
            hint = "观战中，开局后自动进入"
        elif self.chosen and self.bring:
            mine = next((s for s in self.seats if s["investigator"] == self.chosen), {})
            if mine.get("blank"):
                hint = "R 选择角色或车卡 · Y 准备"
            elif mine.get("ready"):
                hint = "已准备，座位与角色已锁定 · Y 取消准备"
            else:
                hint = "R 更换角色或车卡 · Y 准备 · 全员准备后开始"
        elif self.chosen:
            hint = "全员准备后自动开始；也可立即开始，空座由 AI 接替。"
        else:
            hint = "选择空座，回车入座。"
        self.query_one("#lobby-hint", Static).update(Text(hint, style="dim"))
        self._buttons()

    def _relight(self) -> None:
        """The cursor moved: the seat it left gets its colours back, the one it is on loses them."""
        ol = self.query_one("#seats", OptionList)
        for i in {self.lit, ol.highlighted}:
            if i is not None and i < min(ol.option_count, len(self.seats)):
                ol.replace_option_prompt_at_index(i, self._seat_line(self.seats[i], i == ol.highlighted, i + 1))
        self.lit = ol.highlighted

    def _buttons(self) -> None:
        seat = self._highlighted()
        mine = self._my_seat()
        locked = bool(self.bring and mine and mine.get("ready"))
        sittable = bool(seat and seat["human"] and (not seat["online"] or seat["investigator"] == self.chosen))
        self.query_one("#sit", Button).disabled = not sittable or locked or self.gone or self.pending_seat is not None
        self.query_one("#sit", Button).label = "收回控制" if seat and seat.get("ai_takeover") else "入座"
        self.query_one("#start", Button).disabled = self.started or self.gone
        self.query_one("#watch", Button).disabled = self.gone or locked
        choosing = bool(self.bring and mine and not self.gone)
        self.query_one("#sit", Button).display = not choosing
        for button in ("#pick", "#ready"):
            self.query_one(button, Button).display = choosing
        self.query_one("#pick", Button).disabled = locked
        self.query_one("#ready", Button).label = "取消准备" if mine and mine.get("ready") else "准备"
        self.refresh_bindings()

    def _info(self) -> None:
        w = self.welcome or {}
        self.query_one("#lobby-room", Static).update(self._room_text(w))
        book = w.get("module") or {}
        intro = Text()
        intro.append(f"《{book.get('title', '')}》", style="bold")
        if book.get("subtitle"):
            intro.append(f"  {book['subtitle']}", style="dim")
        facts = [book.get(k, "") for k in ("era", "setting", "players", "duration")]
        if any(facts):
            intro.append("\n" + " · ".join(f for f in facts if f), style="dim")
        if book.get("hook"):
            intro.append("\n" + book["hook"].strip(), style="italic")
        self.query_one("#lobby-module", Static).update(intro)

    def _room_text(self, w: dict[str, Any]) -> Text:
        """The one thing to send friends: the page's address on the best network, or the
        address for 加入房间. Every other address is on the 遇到问题 page."""
        info = w.get("room") or {}
        rows = [str(ip) for ip, _ in info.get("addresses", [])]
        port, web = int(info.get("port") or 0), int(info.get("web") or 0)
        if not rows:  # Preserve the actual route, including default web ports and proxy paths.
            t = Text("客户端加入地址：", style="bold")
            t.append(self.link.url, style="bold underline")
            return t
        t = Text()
        if web:
            t.append("发给朋友：", style="bold")
            t.append(lan.page_url(rows[0], web), style="bold underline")
            t.append("\n浏览器访问地址", style="dim")
        else:
            t.append("客户端加入地址：", style="bold")
            t.append(lan.join_target(rows[0], port), style="bold underline")
        return t

    def _card(self) -> None:
        """The highlighted seat's investigator, as the game's F4 shows them; the picture comes
        a moment later (chafa), the hand-drawn art before it."""
        self._investigator_line()
        seat = self._highlighted()
        pane = self.query_one("#lobby-card")
        inv = self._investigator(seat["investigator"]) if seat else None
        if seat and seat.get("blank") and not self.has_class("-narrow"):
            pane.border_title = "人物卡"
            self.query_one("#lobby-sheet", Static).update(Text("空座位。入座后可选择角色。", style="dim"))
            return
        if inv is None or self.m is None or self.has_class("-narrow"):
            if not self.has_class("-narrow"):
                pane.border_title = "人物卡"
                self.query_one("#lobby-sheet", Static).update(Text("等待角色资料…", style="dim"))
            return
        pane.border_title = f"人物卡 · {inv.name}"
        style = self.app.art_style  # type: ignore[attr-defined]
        picture = inv.portrait in self.m.pictures and imagery.available()
        face = self.faces.get(face_key(self.m, inv.portrait, style)) if picture else art_text(self.m, inv.portrait)
        width = max(40, pane.scrollable_content_region.width - 2)
        self.query_one("#lobby-sheet", Static).update(sheet_view(inv, face, width))
        if picture and face is None:
            self._draw_face(inv, style)

    @work(exclusive=True, group="lobby-face")
    async def _draw_face(self, inv: Investigator, style: str) -> None:
        assert self.m is not None
        m = self.m

        key = face_key(m, inv.portrait, style)
        face = await asyncio.to_thread(face_text, m, inv.portrait, style)
        if self.m is None or key != face_key(self.m, inv.portrait, style):
            return
        self.faces[key] = face
        seat = self._highlighted()
        if seat and seat["investigator"] == inv.id:
            self._card()

    @on(OptionList.OptionHighlighted, "#seats")
    def _moved(self) -> None:
        self._relight()
        self._buttons()
        self._card()

    # -- choosing -----------------------------------------------------------------------------
    @on(OptionList.OptionSelected, "#seats")
    async def _picked(self) -> None:
        await self.action_sit()

    @on(Button.Pressed, "#lobby-controls Button")
    async def _pressed(self, event: Button.Pressed) -> None:
        await self.run_action(event.button.id or "")

    async def action_sit(self) -> None:
        seat = self._highlighted()
        if self.gone or seat is None or self.pending_seat is not None:
            return
        if self._seat_locked():
            return
        if not seat["human"]:
            self.app.notify("这个座位由 AI 扮演", severity="warning")
            return
        if seat["online"] and seat["investigator"] != self.chosen:
            self.app.notify(f"{seat['player']} 已占用此座位", severity="warning")
            return
        self.pending_seat = seat["investigator"]
        self._buttons()
        await self.link.send({"t": "sit", "investigator": self.pending_seat})

    # -- your investigator, before the start ------------------------------------------------
    def _my_seat(self) -> dict[str, Any] | None:
        return next((s for s in self.seats if s["investigator"] == self.chosen), None) if self.chosen else None

    def _seat_locked(self) -> bool:
        mine = self._my_seat()
        if self.bring and mine and mine.get("ready"):
            self.app.notify("请先取消准备，再更换座位或角色。", severity="warning")
            return True
        return False

    def action_pick(self) -> None:
        from .seatcard import SeatCardScreen

        seat = self._my_seat()
        if self.gone or self.m is None or not self.bring:
            return
        if seat is None:
            self.app.notify("入座后可选择角色。", severity="warning")
            return
        if self._seat_locked():
            return
        mine = None if self.guest else roster.saved()  # a browser seat: the cards on this machine are the host's
        taken = {s["investigator"] for s in self.seats} - {seat["investigator"]}
        self.app.push_screen(SeatCardScreen(self.m, mine, taken, seat["investigator"]), self._picked_card)

    def _picked_card(self, choice: tuple[str, object] | None) -> None:
        if choice is None:
            return
        kind, value = choice
        if kind == "pregen":
            self.run_worker(self.link.send({"t": "bring", "pregen": value}))
        elif kind == "mine":
            self.run_worker(self._bring(value))  # type: ignore[arg-type]
        else:
            from ...net.cards import RemoteBuilder
            from .card import CardScreen

            assert self.m is not None
            self.remote = RemoteBuilder(self.link, self.m, save=not self.guest, files=not self.guest,
                                        notify=lambda text: self.app.notify(text, severity="warning"))
            self.app.push_screen(CardScreen(self.m, self.remote, start=str(kind)), self._made)  # type: ignore[arg-type]

    async def _bring(self, inv: Investigator, picture: str = "", made: bool = False) -> None:
        from .cards import photo

        if not picture and (path := photo(inv, None)) is not None:
            picture = await asyncio.to_thread(media.pack, str(path))
        await self.link.send({"t": "bring", "investigator": inv.model_dump(mode="json"), "portrait": picture, "made": made})

    def _made(self, card: Investigator | None) -> None:
        remote, self.remote = self.remote, None
        if remote is None:
            return
        self.run_worker(remote.end())
        if card is not None:
            self.run_worker(self._bring(card, remote.portrait_data, made=True))
            if remote.save:
                self.app.notify(f"{card.name} 已保存至“我的角色”", timeout=4)

    async def action_ready(self) -> None:
        seat = self._my_seat()
        if self.gone or seat is None or not self.bring:
            return
        if seat.get("blank"):
            self.app.notify("请先选择角色。", severity="warning")
            return
        await self.link.send({"t": "ready", "on": not seat.get("ready")})

    async def action_watch(self) -> None:
        if self.gone or self.pending_seat is not None or self._watching:
            return
        if self._seat_locked():
            return
        self._watching = True
        if not await self.link.send({"t": "watch"}):
            self._watching = False

    async def action_start(self) -> None:
        if self.gone or self.started or self.pending_seat is not None:
            return
        if self.chosen is None:
            self.chosen = ""
        await self.link.send({"t": "start"})

    def action_sheet(self) -> None:
        if self.state is None or self.m is None:
            return
        party, m, style = self.state.investigators, self.m, self.app.art_style  # type: ignore[attr-defined]
        seat = self._highlighted()
        first = next((i for i, inv in enumerate(party) if seat and inv.id == seat["investigator"]), 0)

        def portrait(inv: Investigator) -> Text:
            return self.faces.get(face_key(m, inv.portrait, style)) or face_text(m, inv.portrait, style)

        self.app.push_screen(SheetModal(party, first, portrait))

    def _enter(self, first: list[dict[str, Any]]) -> None:
        assert self.welcome is not None
        while self.app.screen is not self and len(self.app.screen_stack) > 1:  # still choosing a card: the game has begun
            self.app.pop_screen()
        w = self.welcome
        state = GameState.model_validate(w["state"])
        screen = NetGameScreen(self.m or table_module(w), state, self.link, self.chosen or None,
                               [LogEntry.model_validate(e) for e in w.get("log", [])])
        screen.inbox.extend(first)
        if w.get("media"):
            screen.inbox.insert(0, {"t": "media_sync", "seat": self.chosen or None, **w["media"]})
        self.link.handler = self._game_handler = screen.on_msg
        self.gone = True
        self.app.switch_screen(screen)

    async def action_leave(self) -> None:
        self.gone = True
        await self.link.close()
        if self.room is not None and self.room.is_open:
            self.app.notify("房间保持运行，可从标题页选择“回到房间”。", timeout=8)
        leave(self.app)

    def action_help(self) -> None:
        from ..modals.trouble import TroubleModal

        w = self.welcome or {}
        self.app.push_screen(TroubleModal(w.get("room"), theirs=w.get("version", "")))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "close_room":
            return self.room is not None
        if action in ("pick", "ready"):
            return self.bring and self._my_seat() is not None
        return True

    def action_close_room(self) -> None:
        if self.room is not None:
            self.app.push_screen(CloseRoomModal(), self._close_room)

    @work(exclusive=True, group="room")
    async def _close_room(self, sure: bool | None) -> None:
        if not sure or self.room is None:
            return
        self.gone = True
        await self.link.close()
        await close_room(self.app)
        leave(self.app)


async def close_room(app: Any) -> None:
    """Close the room this app opened (net/room.py): the page, then the Hub."""
    room, app.room = getattr(app, "room", None), None
    if room is not None:
        await asyncio.to_thread(room.close)
        app.notify(f"《{room.title}》的房间已关闭" if room.title else "房间已关闭")
