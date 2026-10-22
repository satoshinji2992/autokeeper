"""开房间: a LAN room opened from the title screen. Pick the module (the bundled ones, then your
own), who plays each investigator (玩家 / AI / 不上场) and how many empty seats there are for
players who bring their own (each player may also swap their pregen in the lobby), a new game or a
LAN save to go on with,
your name, the ports and whether friends get a web page; then on into the lobby as its host.

The room (net/room.py, the same one `autokeeper host` opens) lives as long as this app, or
until its host closes it; the title screen's 回到房间 goes back to it."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, ContentSwitcher, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from ... import imagery, paths
from ...engine.state import SaveRefused
from ...module.schema import Module
from ...net import room as lan
from ...rules.coc import Investigator
from ..library import art_text, face_text, load_module
from ..modals.sheet import sheet_view
from ..widgets.waiting import wait_on
from .lobby import NARROW

ROOM_KEYS = "Enter 切换玩家或 AI · Delete 删除座位 · Tab 选择调查员 · Esc 换模组"
OWN = "own"  # a seat whose player chooses in the lobby: a card of their own, a pregen, or one made there


def back_to_room(app: Any) -> Screen | None:
    """The lobby of the room this app has open, or None."""
    room = getattr(app, "room", None)
    if room is None or not room.is_open or room.hub is None:
        return None
    from .lobby import LobbyScreen

    return LobbyScreen(f"127.0.0.1:{room.port}", lan.recall().get("name") or "房主", key=room.hub.key, room=room)


class HostScreen(Screen):
    CSS_PATH = "room.tcss"
    BINDINGS = [Binding("escape", "back", "返回"), Binding("f1", "help", "遇到问题"),
                Binding("delete", "drop_seat", "删除座位", show=False)]

    def __init__(self) -> None:
        super().__init__()
        self.paths = paths.module_folders()
        self.step = "module"
        self.path: Path | None = None
        self.module: Module | None = None
        self.slot = ""  # the LAN save to go on with; "" for a new game
        self.party: list[Investigator] = []  # who may be played: the module's pregens, or the saved party
        # The seats, each who sits there (玩家 / AI) and the investigator it uses ("" for the player to
        # choose in the lobby). A seat line says who sits; the investigator is chosen under the list
        # (the user, 2026-10-21: a pregen on the seat read as already chosen).
        self.seats: list[dict[str, str]] = []
        self.faces: dict[tuple[str, str], Text] = {}
        self.ports = (lan.HUB_PORT, lan.WEB_PORT)  # taken ones are swapped for free ones (net/room.py any_port)
        self.trouble = ""  # the last error's technical part, for the 遇到问题 page

    def compose(self) -> ComposeResult:
        last = lan.recall()
        with Vertical(id="setup"):
            yield Static(id="setup-title")
            with ContentSwitcher(initial="setup-body", id="host-steps"):
                with Horizontal(id="setup-body"):
                    yield OptionList(id="choices")
                    with VerticalScroll(id="preview"):
                        yield Static(id="preview-body")
                with Horizontal(id="host-room"):
                    with VerticalScroll(id="host-form"):
                        yield OptionList(id="host-seats")
                        yield OptionList(id="host-investigators")
                        yield OptionList(id="host-saves")
                        with Horizontal(classes="field"):
                            yield Static("名字", classes="field-label")
                            yield Input(str(last.get("name", "")), placeholder="房主", id="host-name", max_length=16, compact=True)
                        yield Checkbox("同时开网页版", bool(last.get("web", True)), id="host-web", compact=True)
                        yield Static(id="host-note")
                        with Horizontal(classes="buttons"):
                            yield Button("开房间", id="host-open", variant="primary", compact=True)
                            yield Button("返回", id="host-back", compact=True)
                            yield Button("遇到问题", id="host-help", compact=True)
                    with VerticalScroll(id="host-preview"):
                        yield Static(id="host-card")
            yield Static(id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#host-seats", OptionList).border_title = "座位"
        self.query_one("#host-investigators", OptionList).border_title = "调查员"
        self.query_one("#host-saves", OptionList).border_title = "存档"
        self._show_modules()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < NARROW + 14, "-narrow")  # the form and a whole card need about 110 columns

    def action_back(self) -> None:
        if self.step == "room":
            self._show_modules()
        else:
            self.app.pop_screen()

    # -- the module ------------------------------------------------------------------------
    def _show_modules(self) -> None:
        self.step = "module"
        self.query_one(ContentSwitcher).current = "setup-body"
        self.query_one("#setup-title", Static).update(Text("开房间 · 选模组", style="bold"))
        ol = self.query_one("#choices", OptionList)
        ol.clear_options()
        for p in self.paths:
            try:
                m = Module.load(p)
                ol.add_option(Option(f"《{m.title}》 {m.players}", id=str(p), disabled=not m.pregens))
            except Exception:  # broken module files should not crash the menu
                ol.add_option(Option(f"{p.name}（无法读取）", id=str(p), disabled=True))
        if self.path is not None and self.path in self.paths:
            ol.highlighted = self.paths.index(self.path)
        elif ol.option_count:
            ol.highlighted = 0
        ol.focus()
        self.query_one("#setup-hint", Static).update("Enter 选择 · Esc 返回")

    @on(OptionList.OptionHighlighted, "#choices")
    def _module_preview(self, event: OptionList.OptionHighlighted) -> None:
        oid = event.option.id or ""
        try:
            m = Module.load(Path(oid))
        except Exception:  # listed as unreadable already
            return
        first = m.cover or next((loc.art for loc in m.locations if loc.art), None)
        self.query_one("#preview-body", Static).update(self._module_text(m, art_text(m, first)))
        if first in m.pictures and imagery.available():
            self._module_picture(m, first, oid)

    @staticmethod
    def _module_text(m: Module, picture: Text) -> Text:
        t = Text()
        t.append(f"《{m.title}》", style="bold")
        t.append(f"  {m.subtitle}\n", style="dim")
        t.append(" · ".join(x for x in (m.era, m.setting, m.players, m.duration) if x) + "\n\n", style="dim")
        t.append_text(picture)
        t.append("\n\n" + m.hook.strip() + "\n\n", style="italic")
        t.append(f"预设调查员 {len(m.pregens)} 位\n" + "、".join(p.name for p in m.pregens), style="dim")
        return t

    @work(exclusive=True, group="host-module")
    async def _module_picture(self, m: Module, key: str, oid: str) -> None:
        """The picture as wide as the pane, as the setup screen draws it (chafa, in a thread)."""
        room = self.query_one("#preview-body", Static).content_size.width - 1
        width = room if room >= 16 else 32  # never wider than the pane: a wider picture wraps into noise
        size, style = (width, max(8, min(24, round(width * 0.38)))), self.app.art_style  # type: ignore[attr-defined]

        def draw() -> Text:
            try:
                return Text.from_ansi(imagery.render(m.pictures[key], *size, style, fill=True))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return art_text(m, key)

        picture = await asyncio.to_thread(draw)
        ol = self.query_one("#choices", OptionList)
        if self.step == "module" and ol.highlighted is not None and ol.get_option_at_index(ol.highlighted).id == oid:
            self.query_one("#preview-body", Static).update(self._module_text(m, picture))

    @on(OptionList.OptionSelected, "#choices")
    def _module_chosen(self, event: OptionList.OptionSelected) -> None:
        self.path = Path(event.option.id or "")
        self.module = load_module(self.path)
        if not self.module.pregens:
            self.app.notify("此模组未提供预设调查员，无法创建房间。", severity="error")
            return
        self.slot = ""
        self._seat_pregens()
        self._show_room()

    # -- the room ----------------------------------------------------------------------------
    def _seat_pregens(self) -> None:
        """A new game: the first pregen played by a player, the others by the AI."""
        assert self.module
        self.party = list(self.module.pregens)
        self.seats = [{"who": "玩家" if i == 0 else "AI", "inv": p.id} for i, p in enumerate(self.party)]

    def _show_room(self) -> None:
        assert self.module
        self.step = "room"
        self.query_one(ContentSwitcher).current = "host-room"
        self.query_one("#setup-title", Static).update(Text(f"开房间 · 《{self.module.title}》", style="bold"))
        self._list_saves()
        self._list_seats()
        self.query_one("#host-seats", OptionList).focus()
        self.query_one("#setup-hint", Static).update(ROOM_KEYS)
        self._note()

    def _seat_prompt(self, i: int) -> Text:
        """The seat and who sits in it; the investigator is under the list."""
        theme = self.app.current_theme
        who = self.seats[i]["who"]
        t = Text(f"座位 {i + 1}  ", style="bold")
        t.append(who, style=f"bold {theme.accent or 'yellow'}" if who == "玩家" else f"bold {theme.secondary or 'cyan'}")
        if who == "玩家":
            t.append("  待入座", style="dim")
        return t

    def _list_seats(self) -> None:
        ol = self.query_one("#host-seats", OptionList)
        kept = ol.highlighted if ol.option_count else 0
        ol.clear_options()
        ol.add_options([Option(self._seat_prompt(i), id=f"seat-{i}") for i in range(len(self.seats))])
        if not self.slot and len(self.seats) < len(self.party) + lan.BLANKS:  # a saved party is as it was
            ol.add_option(Option(Text("＋ 加一个座位", style="dim"), id="add-seat"))
        ol.highlighted = min(kept or 0, ol.option_count - 1)
        self._list_investigators()

    def _seat_at(self) -> int | None:
        ol = self.query_one("#host-seats", OptionList)
        oid = ol.get_option_at_index(ol.highlighted).id if ol.highlighted is not None and ol.option_count else ""
        return int(oid.removeprefix("seat-")) if oid and oid.startswith("seat-") else None

    def _list_investigators(self) -> None:
        """The investigators the highlighted seat may use: a player's own choice in the lobby, or a
        pregen no other seat has; a saved game keeps its own."""
        ol = self.query_one("#host-investigators", OptionList)
        i = self._seat_at()
        ol.clear_options()
        if i is None:
            ol.border_title = "调查员"
            ol.add_option(Option(Text("新座位由玩家在大厅选择角色或车卡。", style="dim"), disabled=True))
            return
        seat = self.seats[i]
        ol.border_title = f"调查员 · 座位 {i + 1}"
        if self.slot:  # going on with a save: the party stays as it was
            inv = next(x for x in self.party if x.id == seat["inv"])
            ol.add_option(Option(Text.assemble((inv.name, "bold"), (f" · {inv.occupation}（继续存档时不可更换）", "dim")), id=inv.id))
            ol.highlighted = 0
            return
        taken = {s["inv"]: n for n, s in enumerate(self.seats) if s["inv"] and n != i}
        if seat["who"] == "玩家":
            ol.add_option(Option(Text.assemble(("入座后选择", "bold"), ("  自有角色、模组预设或车卡", "dim")), id=OWN))
        for inv in self.party:
            line = Text.assemble((inv.name, "bold"), (f" · {inv.occupation.split('（')[0]} · {inv.age} 岁", "dim"))
            if inv.id in taken:
                line.append(f"  座位 {taken[inv.id] + 1}", style="dim")
            ol.add_option(Option(line, id=inv.id, disabled=inv.id in taken))
        current = seat["inv"] or OWN
        ol.highlighted = next((n for n in range(ol.option_count) if ol.get_option_at_index(n).id == current), 0)

    @on(OptionList.OptionSelected, "#host-investigators")
    def _investigator_chosen(self, event: OptionList.OptionSelected) -> None:
        i = self._seat_at()
        if i is None or self.slot:
            return
        self.seats[i]["inv"] = "" if event.option.id == OWN else event.option.id or ""
        self._list_investigators()
        self._seat_card(i)
        self._note()

    @on(OptionList.OptionSelected, "#host-seats")
    def _cycle(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id or ""
        if oid == "add-seat":  # a player who brings their own, or makes one in the lobby
            self.seats.append({"who": "玩家", "inv": ""})
            self._list_seats()
            self._note()
            return
        i = int(oid.removeprefix("seat-"))
        seat = self.seats[i]
        if seat["who"] == "玩家":
            if not seat["inv"]:  # the AI needs someone to play
                free = next((inv.id for inv in self.party if all(s["inv"] != inv.id for s in self.seats)), "")
                if not free:
                    self.app.notify("AI 座位需要一位调查员，当前没有空闲的模组预设调查员。", severity="warning")
                    return
                seat["inv"] = free
            seat["who"] = "AI"
        else:
            seat["who"] = "玩家"
        self.query_one("#host-seats", OptionList).replace_option_prompt(oid, self._seat_prompt(i))
        self._list_investigators()
        self._seat_card(i)
        self._note()

    def action_drop_seat(self) -> None:
        i = self._seat_at()
        if self.slot or i is None or not self.query_one("#host-seats", OptionList).has_focus:
            return
        if len(self.seats) == 1:
            self.app.notify("至少保留一个座位。", severity="warning")
            return
        del self.seats[i]
        self._list_seats()
        self._note()

    @on(OptionList.OptionHighlighted, "#host-seats")
    def _seat_moved(self) -> None:
        self._list_investigators()
        self._seat_card(self._seat_at())

    def _seat_card(self, i: int | None) -> None:
        """The investigator the seat uses, as the lobby and the game's F4 show them."""
        card = self.query_one("#host-card", Static)
        if i is None:
            card.update(Text("加一个座位，给入座后自己选择角色的朋友。", style="dim"))
            return
        inv = next((x for x in self.party if x.id == self.seats[i]["inv"]), None)
        if inv is None or self.module is None:
            card.update(Text("坐在这里的玩家在大厅选择角色：自有角色、模组预设，或车卡。", style="dim"))
            return
        style = self.app.art_style  # type: ignore[attr-defined]
        picture = inv.portrait in self.module.pictures and imagery.available()
        face = self.faces.get((inv.portrait, style)) if picture else art_text(self.module, inv.portrait)
        width = max(40, self.query_one("#host-preview").scrollable_content_region.width - 2)
        card.update(sheet_view(inv, face, width))
        if picture and face is None:
            self._draw_face(inv, style)

    @work(exclusive=True, group="host-face")
    async def _draw_face(self, inv: Investigator, style: str) -> None:
        assert self.module is not None
        m = self.module

        self.faces[(inv.portrait, style)] = await asyncio.to_thread(face_text, m, inv.portrait, style)
        if (i := self._seat_at()) is not None and self.seats[i]["inv"] == inv.id:
            self._seat_card(i)

    def _list_saves(self) -> None:
        assert self.module
        ol = self.query_one("#host-saves", OptionList)
        ol.clear_options()
        ol.add_option(Option("新开一局", id="new"))
        saves = lan.lan_saves(self.module.id)[:8]
        for slot, meta in saves:
            label = f"接着玩 · {meta.get('party', '')} · {meta.get('clock', '')} · {meta.get('location', '')}"
            label += " · 已结局" if meta.get("ending") else ""
            ol.add_option(Option(f"{label}  [{meta.get('updated', '')}]", id=f"slot:{slot}"))
        ol.display = bool(saves)  # nothing to go on with: a new game, no choice to show
        ol.highlighted = 0 if not self.slot else next(
            (i for i in range(ol.option_count) if ol.get_option_at_index(i).id == f"slot:{self.slot}"), 0)

    @on(OptionList.OptionSelected, "#host-saves")
    def _save_chosen(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id or ""
        if oid == "new":
            self.slot = ""
            self._seat_pregens()
        else:
            try:
                state = lan.saved(oid.removeprefix("slot:"))
            except SaveRefused as exc:
                self.app.notify(f"{exc}。", severity="error")
                return
            except (OSError, ValueError):
                self.app.notify("无法读取这个存档。", severity="error")
                return
            self.slot = state.slot
            self.party = list(state.investigators)
            self.seats = [{"who": "玩家" if inv.id in state.human else "AI", "inv": inv.id} for inv in self.party]
        self._list_seats()
        self._note()
        if self.slot:
            self.app.notify("继续存档时需保留全部调查员。", timeout=3)

    def _note(self) -> None:
        players = sum(1 for s in self.seats if s["who"] == "玩家")
        ai = len(self.seats) - players
        t = Text(f"{players} 位玩家 · {ai} 位 AI", style="bold")
        if self.slot:
            t.append(" · 继续已有存档")
        else:  # where a card of one's own is made: not here, in the lobby (the user, 2026-10-21: 在哪新建角色?)
            t.append("\n开房间后在大厅入座，按 R 选择角色或车卡。", style="dim")
        self.query_one("#host-note", Static).update(t)

    # -- opening --------------------------------------------------------------------------------
    @on(Button.Pressed, "#host-back")
    def _back(self) -> None:
        self.action_back()

    @on(Button.Pressed, "#host-help")
    def action_help(self) -> None:
        from ..modals.trouble import TroubleModal

        self.app.push_screen(TroubleModal({"port": self.ports[0], "web": self.ports[1]}, detail=self.trouble))

    @on(Input.Submitted, "#host-room Input")
    @on(Button.Pressed, "#host-open")
    def open_room(self) -> None:
        if self.path is None or self.module is None:
            return
        name = self.query_one("#host-name", Input).value.strip()[:16] or "房主"
        port, web_port = self.ports
        web = self.query_one("#host-web", Checkbox).value
        if not self.seats:
            self.app.notify("至少保留一个座位。", severity="error")
            return
        # The room's form of it: each pregen 玩家 / AI / 不上场, then the seats chosen in the lobby.
        used = {s["inv"]: s["who"] for s in self.seats if s["inv"]}
        seats = [used.get(inv.id, "不上场") for inv in self.party] + ["玩家"] * sum(1 for s in self.seats if not s["inv"])
        state = None
        if self.slot:
            state = lan.saved(self.slot)
            state.human = [inv.id for inv in state.investigators if used.get(inv.id) == "玩家"]
        self._open(name, port, web_port, web, None if state else seats, state)

    @work(exclusive=True, group="host")
    async def _open(self, name: str, port: int, web_port: int, web: bool, seats: list[str] | None, state: Any) -> None:
        assert self.path is not None
        button = self.query_one("#host-open", Button)
        button.disabled = True
        hint = self.query_one("#setup-hint", Static)
        wait_on(hint, "正在创建房间")
        if (old := getattr(self.app, "room", None)) is not None:  # one room at a time; this one had stopped
            self.app.room = None  # type: ignore[attr-defined]
            await asyncio.to_thread(old.close)
        room = lan.Room(self.path, seats=seats, state=state, port=port, web_port=web_port, any_port=True)
        try:
            await asyncio.to_thread(room.open)
            if web:
                paths.logs().mkdir(parents=True, exist_ok=True)
                with open(paths.web_log(), "a", encoding="utf-8") as log:  # the page keeps its own handle
                    await asyncio.to_thread(room.start_web, log)
        except lan.RoomError as exc:
            await asyncio.to_thread(room.close)
            self.trouble = exc.detail
            self.app.notify(f"{exc}。可参阅「遇到问题」。", severity="error", timeout=10)
            button.disabled = False
            hint.update(ROOM_KEYS)
            return
        lan.remember(name=name, web=web)
        self.app.room = room  # type: ignore[attr-defined]
        from .lobby import LobbyScreen

        self.app.switch_screen(LobbyScreen(f"127.0.0.1:{room.port}", name, key=room.hub.key if room.hub else "", room=room))

