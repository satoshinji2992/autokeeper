"""准备房间: the lobby of a LAN room. Who sits where and who each investigator is (portrait, the
whole sheet), how friends get in (every address of the host, the page for browsers), the version
and the round's rule, and the module as the players may read it: the Hub's table copy (net/hub.py
table()), never the truth, the keeper's notes or anyone's secrets.

Take a free seat, watch, or start with the empty seats played by the AI; the host who opened the
room in this app can also close it."""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any
from urllib.parse import urlparse

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, OptionList, Static
from textual.widgets.option_list import Option

from .. import __version__, imagery
from ..engine.state import GameState, LogEntry
from ..module.schema import Module
from ..net import room as lan
from ..net.link import Link
from ..rules.coc import Investigator
from .app import art_text
from .net import NetGameScreen, leave, table_module
from .sheet import SheetModal, sheet_view

NARROW = 96  # fewer columns than this: no sheet beside the seats (人物卡 opens it)
FACE = (30, 14)  # the portrait on the sheet, as in the game's F4


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
            yield Static("所有人都会断开，网页版也会关掉。存档还在，下次开房间可以接着玩。", classes="modal-body")
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
        Binding("s", "start", "现在开始"),
        Binding("c", "sheet", "人物卡"),
        Binding("x", "close_room", "关闭房间"),
    ]

    def __init__(self, address: str, name: str, *, key: str = "", room: lan.Room | None = None):
        super().__init__()
        self.link = Link(address, name, key)
        self.room = room  # opened by this app: its host may close it here
        self.welcome: dict[str, Any] | None = None
        self.m: Module | None = None  # the table's copy, with its art and pictures
        self.state: GameState | None = None
        self.seats: list[dict[str, Any]] = []
        self.chosen: str | None = None  # the seat taken, "" for watching
        self.gone = False
        self.faces: dict[tuple[str, str], Text] = {}  # portraits drawn so far: (picture, art style) -> art
        self.width, self.short = 40, False  # the seat list's lines: how wide, and whether two a seat
        self.lit: int | None = None  # the seat drawn for the cursor
        self._game_handler: Any = None  # the game screen's, once the link is handed on to it

    def compose(self) -> ComposeResult:
        with Vertical(id="lobby"):
            yield Static(f"正在连接 {self.link.url} …", id="lobby-head")
            with Horizontal(id="lobby-body"):
                with Vertical(id="lobby-left"):
                    yield OptionList(id="seats")
                    yield Static(id="lobby-hint")
                    with VerticalScroll(id="lobby-info"):
                        yield Static(id="lobby-room")
                        yield Static(id="lobby-module")
                with VerticalScroll(id="lobby-card"):
                    yield Static(id="lobby-sheet")
            with Horizontal(id="lobby-actions"):
                yield Button("坐这个位置", id="sit", variant="primary", compact=True)
                yield Button("观战", id="watch", compact=True)
                yield Button("现在开始", id="start", compact=True)
                yield Button("人物卡", id="sheet", compact=True)
                yield Button("离开", id="leave", compact=True)
                yield Button("关闭房间", id="close_room", variant="error", compact=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#seats", OptionList).border_title = "座位"
        self.query_one("#close_room", Button).display = self.room is not None
        self.query_one("#lobby-info").border_title = "房间"
        try:
            await self.link.connect()
        except Exception as exc:  # wrong address, host not up yet
            await self.link.close()
            self.app.notify(str(exc) or type(exc).__name__, title="连不上房间", severity="error", timeout=8)
            if self.room is not None:
                self.app.pop_screen()
            else:
                from .net import JoinScreen

                self.app.switch_screen(JoinScreen(self.link.url.removeprefix("ws://").removesuffix("/ws"), self.link.name))
            return
        self.link.handler = self.on_msg
        self.app.run_worker(self.link.pump, name="link", group="link", exit_on_error=False)

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
                self.app.notify(f"房主是 {theirs}，你是 {__version__}，版本不同可能出错", severity="warning", timeout=12)
            self.m = table_module(msg)
            self.state = GameState.model_validate(msg["state"])
            self.seats = msg["seats"]
            self._show_all()
        elif t == "seats":
            self.seats = msg["seats"]
            self._list_seats()
            self._head()
        elif t in ("sync", "turn") and self.welcome is not None:
            self.welcome["state"] = msg["state"]  # keep up while still choosing
            self.state = GameState.model_validate(msg["state"])
        elif t == "module" and self.welcome is not None and self.m is not None:  # found places and people, as the game screen takes them
            m = Module.model_validate(msg["module"])
            m.art, m.art_colors, m.pictures = self.m.art, self.m.art_colors, self.m.pictures
            m.art.update(msg.get("art", {}))
            m.art_colors.update(msg.get("colors", {}))
            self.m, self.welcome["module"] = m, msg["module"]
        elif t == "begin" and self.welcome is not None:
            self.welcome["started"] = True
            if self.chosen is None:
                self._show_all()
        elif t == "error":
            self.app.notify(msg["text"], severity="warning")
        elif t == "closed" and not self.gone:
            self.gone = True
            self.app.notify("房主关闭了房间", severity="error")
            self.query_one("#lobby-hint", Static).update(Text("房间关了，Esc 离开", style="bold"))
            self._buttons()
        if t in ("begin", "turn", "event") and self.chosen is not None and self.welcome is not None and not self.gone:
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
        right.append("已开局" if self.started else "等人入座")
        bar = Table.grid(expand=True)
        bar.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        bar.add_column(justify="right", no_wrap=True)
        bar.add_row(left, right)
        self.query_one("#lobby-head", Static).update(bar)

    def _seat_line(self, seat: dict[str, Any], lit: bool = False) -> Text:
        """Name, occupation and age; who sits there; one line of who they are. `lit`: under the
        cursor, where only the cursor's own colours read well."""
        inv = self._investigator(seat["investigator"])
        t = Text()
        t.append(seat["name"], style="bold")
        t.append(f"  {seat['occupation']}" + (f" · {inv.age} 岁" if inv else ""), style="" if lit else "dim")
        if not seat["human"]:
            t.append("\n  ◆ AI 扮演", style="" if lit else "dim")
        elif seat["investigator"] == self.chosen:
            t.append("\n  ● 你", style="bold" if lit else "bold green")
        elif seat["online"]:
            t.append(f"\n  ● {seat['player']}", style="" if lit else "green")
        else:
            t.append("\n  ○ 空座" + ("，AI 代打" if self.started else ""), style="bold" if lit else "bold yellow")
        if inv and not self.short and (line := gist(inv, 60)):
            t.append(f"\n  {line}", style="italic" if lit else "dim italic")
        return clip(t, self.width)

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
            ol.add_option(Option(self._seat_line(s, i == at), id=s["investigator"]))
        self.lit = at
        ol.highlighted = at
        if self.focused is None or self.focused is ol:
            ol.focus()
        if self.gone:
            return
        if self.started:
            hint = "已经开局了，选个空座或者观战"
        elif self.chosen == "":
            hint = "观战中，开局后自动进入"
        elif self.chosen:
            hint = "人齐了自动开局，也可以现在开始，空座交给 AI"
        else:
            hint = "选个空座，回车坐下"
        self.query_one("#lobby-hint", Static).update(Text(hint, style="dim"))
        self._buttons()

    def _relight(self) -> None:
        """The cursor moved: the seat it left gets its colours back, the one it is on loses them."""
        ol = self.query_one("#seats", OptionList)
        for i in {self.lit, ol.highlighted}:
            if i is not None and i < min(ol.option_count, len(self.seats)):
                ol.replace_option_prompt_at_index(i, self._seat_line(self.seats[i], i == ol.highlighted))
        self.lit = ol.highlighted

    def _buttons(self) -> None:
        seat = self._highlighted()
        sittable = bool(seat and seat["human"] and (not seat["online"] or seat["investigator"] == self.chosen))
        self.query_one("#sit", Button).disabled = not sittable or self.gone
        self.query_one("#start", Button).disabled = self.started or self.gone
        self.query_one("#watch", Button).disabled = self.gone

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

    def _room_text(self, w: dict[str, Any]) -> Group:
        """How friends get in: every address the host has, the page if there is one; then this
        table's round and the version."""
        info = w.get("room") or {}
        rows = [(str(ip), str(label)) for ip, label in info.get("addresses", [])]
        port, web = int(info.get("port") or 0), int(info.get("web") or 0)
        if not rows:  # a host without the room's addresses: the one this screen came in by
            url = urlparse(self.link.url)
            rows, port = [(url.hostname or "", "")], url.port or lan.HUB_PORT
        grid = Table.grid(padding=(0, 2))
        grid.add_column(no_wrap=True)
        grid.add_column(style="dim", no_wrap=True)
        tail = Text()
        if web:
            head = Text("网页版", style="bold")
            for ip, label in rows:
                grid.add_row(f"  {lan.page_url(ip, web)}", label)
            tail.append(f"加入房间 {lan.join_target(rows[0][0], port)}")
        else:
            head = Text("加入房间", style="bold")
            for ip, label in rows:
                grid.add_row(f"  {lan.join_target(ip, port)}", label)
            tail.append("没开网页版，朋友要装 AutoKeeper 才能进", style="dim")
        if players := sum(1 for s in self.seats if s["human"]):
            tail.append(f"\n每轮 {lan.round_size(players)} 条行动")
        theirs = w.get("version") or __version__
        tail.append(f"\n版本 {theirs}" + ("" if theirs == __version__ else f"（你是 {__version__}）"))
        if self.link.host:
            tail.append(f"\n连不上就查防火墙，放行 {port}" + (f" 和 {web}" if web else ""), style="dim")
        if info.get("hint"):
            tail.append(f"\n{info['hint']}", style="bold yellow")
        return Group(head, grid, tail)

    def _card(self) -> None:
        """The highlighted seat's investigator, as the game's F4 shows them; the picture comes
        a moment later (chafa), the hand-drawn art before it."""
        seat = self._highlighted()
        pane = self.query_one("#lobby-card")
        inv = self._investigator(seat["investigator"]) if seat else None
        if inv is None or self.m is None or self.has_class("-narrow"):
            return
        pane.border_title = f"人物卡 · {inv.name}"
        style = self.app.art_style  # type: ignore[attr-defined]
        picture = inv.portrait in self.m.pictures and imagery.available()
        face = self.faces.get((inv.portrait, style)) if picture else art_text(self.m, inv.portrait)
        width = max(40, pane.scrollable_content_region.width - 2)
        self.query_one("#lobby-sheet", Static).update(sheet_view(inv, face, width))
        if picture and face is None:
            self._draw_face(inv, style)

    @work(exclusive=True, group="lobby-face")
    async def _draw_face(self, inv: Investigator, style: str) -> None:
        assert self.m is not None
        m = self.m

        def draw() -> Text:
            try:
                return Text.from_ansi(imagery.render(m.pictures[inv.portrait], *FACE, style, face=True))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return art_text(m, inv.portrait)  # an unreadable picture: its hand-drawn art

        self.faces[(inv.portrait, style)] = await asyncio.to_thread(draw)
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

    @on(Button.Pressed, "#lobby-actions Button")
    async def _pressed(self, event: Button.Pressed) -> None:
        await self.run_action(event.button.id or "")

    async def action_sit(self) -> None:
        seat = self._highlighted()
        if self.gone or seat is None:
            return
        if not seat["human"]:
            self.app.notify("这个座位由 AI 扮演", severity="warning")
            return
        if seat["online"] and seat["investigator"] != self.chosen:
            self.app.notify(f"{seat['player']} 坐在这里了", severity="warning")
            return
        self.chosen = seat["investigator"]
        await self.link.send({"t": "sit", "investigator": self.chosen})
        if self.started:
            self._enter([])

    async def action_watch(self) -> None:
        if self.gone:
            return
        self.chosen = ""
        if self.started:
            self._enter([])
        else:
            self._list_seats()

    async def action_start(self) -> None:
        if self.gone or self.started:
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
            return self.faces.get((inv.portrait, style)) or art_text(m, inv.portrait, FACE if inv.portrait in m.pictures else None, style, face=True)

        self.app.push_screen(SheetModal(party, first, portrait))

    def _enter(self, first: list[dict[str, Any]]) -> None:
        assert self.welcome is not None
        w = self.welcome
        state = GameState.model_validate(w["state"])
        screen = NetGameScreen(self.m or table_module(w), state, self.link, self.chosen or None,
                               [LogEntry.model_validate(e) for e in w.get("log", [])])
        screen.inbox.extend(first)
        self.link.handler = self._game_handler = screen.on_msg
        self.gone = True
        self.app.switch_screen(screen)

    async def action_leave(self) -> None:
        self.gone = True
        await self.link.close()
        if self.room is not None and self.room.is_open:
            self.app.notify("房间还开着，标题画面「回到房间」可以回来", timeout=8)
        leave(self.app)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "close_room":
            return self.room is not None
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
        app.notify(f"《{room.title}》的房间关了" if room.title else "房间关了")
