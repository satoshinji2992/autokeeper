"""开房间: a LAN room opened from the title screen. Pick the module (the bundled ones, then your
own), who plays each investigator (玩家 / AI / 不上场), a new game or a LAN save to go on with,
your name, the ports and whether friends get a web page; then on into the lobby as its host.

The room (net/room.py, the same one `autokeeper host` opens) lives as long as this app, or
until its host closes it; the title screen's 回到房间 goes back to it."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from rich.cells import cell_len
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, ContentSwitcher, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from .. import config, imagery
from ..module.schema import Module
from ..net import room as lan
from ..rules.coc import Investigator
from .app import art_text, load_module, modules
from .lobby import FACE, NARROW
from .sheet import sheet_view

CYCLE = {"玩家": "AI", "AI": "不上场", "不上场": "玩家"}
GOING_ON = {"玩家": "AI", "AI": "玩家"}  # a saved party has nobody to leave out


def back_to_room(app: Any) -> Screen | None:
    """The lobby of the room this app has open, or None."""
    room = getattr(app, "room", None)
    if room is None or not room.is_open or room.hub is None:
        return None
    from .lobby import LobbyScreen

    return LobbyScreen(f"127.0.0.1:{room.port}", lan.recall().get("name") or "房主", key=room.hub.key, room=room)


class HostScreen(Screen):
    CSS_PATH = "room.tcss"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self.paths = modules()
        self.step = "module"
        self.path: Path | None = None
        self.module: Module | None = None
        self.slot = ""  # the LAN save to go on with; "" for a new game
        self.party: list[Investigator] = []  # the seats: the module's pregens, or the saved party
        self.plan: dict[str, str] = {}  # investigator id -> 玩家 / AI / 不上场
        self.faces: dict[tuple[str, str], Text] = {}

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
                        yield OptionList(id="host-saves")
                        with Horizontal(classes="field"):
                            yield Static("你的名字", classes="field-label")
                            yield Input(str(last.get("name", "")), placeholder="朋友看到的名字", id="host-name", max_length=16, compact=True)
                        with Horizontal(classes="field"):
                            yield Static("联机端口", classes="field-label")
                            yield Input(str(last.get("port", lan.HUB_PORT)), id="host-port", type="integer", max_length=5, compact=True)
                            yield Static("网页端口", classes="field-label")
                            yield Input(str(last.get("web_port", lan.WEB_PORT)), id="host-web-port", type="integer", max_length=5, compact=True)
                        yield Checkbox("同时开网页：朋友用浏览器打开就能玩，什么都不用装", bool(last.get("web", True)), id="host-web", compact=True)
                        yield Static(id="host-note")
                        with Horizontal(classes="buttons"):
                            yield Button("开房间", id="host-open", variant="primary", compact=True)
                            yield Button("返回", id="host-back", compact=True)
                    with VerticalScroll(id="host-preview"):
                        yield Static(id="host-card")
            yield Static(id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#host-seats", OptionList).border_title = "座位：回车或点一下换 玩家 / AI / 不上场"
        self.query_one("#host-saves", OptionList).border_title = "新开一局，还是接着玩"
        self._show_modules()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < NARROW, "-narrow")

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
            except Exception as exc:  # broken module files should not crash the menu
                ol.add_option(Option(f"{p.name}（无法读取：{exc.__class__.__name__}）", id=str(p), disabled=True))
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
            self.app.notify("这个模组没有预设调查员，联机只能用预设调查员", severity="error")
            return
        self.slot = ""
        self._seat_pregens()
        self._show_room()

    # -- the room ----------------------------------------------------------------------------
    def _seat_pregens(self) -> None:
        assert self.module
        self.party = list(self.module.pregens)
        self.plan = {p.id: "玩家" if i == 0 else "AI" for i, p in enumerate(self.party)}

    def _show_room(self) -> None:
        assert self.module
        self.step = "room"
        self.query_one(ContentSwitcher).current = "host-room"
        self.query_one("#setup-title", Static).update(Text(f"开房间 · 《{self.module.title}》", style="bold"))
        self._list_saves()
        self._list_seats()
        self.query_one("#host-seats", OptionList).focus()
        self.query_one("#setup-hint", Static).update("回车或点击换座位 · Tab 下一项 · Esc 换模组")
        self._note()

    def _seat_prompt(self, inv: Investigator) -> Text:
        theme = self.app.current_theme
        who = self.plan.get(inv.id, "AI")
        colour = {"玩家": f"bold {theme.accent or 'yellow'}", "AI": f"bold {theme.secondary or 'cyan'}", "不上场": "dim"}[who]
        t = Text()
        t.append(who + " " * (8 - cell_len(who)), style=colour)
        t.append(inv.name, style="bold" if who != "不上场" else "dim strike")
        t.append(f"  {inv.occupation} · {inv.age} 岁", style="dim")
        return t

    def _list_seats(self) -> None:
        ol = self.query_one("#host-seats", OptionList)
        kept = ol.highlighted if ol.option_count else 0
        ol.clear_options()
        ol.add_options([Option(self._seat_prompt(inv), id=inv.id) for inv in self.party])
        ol.highlighted = min(kept or 0, len(self.party) - 1)

    @on(OptionList.OptionSelected, "#host-seats")
    def _cycle(self, event: OptionList.OptionSelected) -> None:
        inv_id = event.option.id or ""
        turn = GOING_ON if self.slot else CYCLE
        self.plan[inv_id] = turn.get(self.plan.get(inv_id, "AI"), "AI")
        inv = next(i for i in self.party if i.id == inv_id)
        self.query_one("#host-seats", OptionList).replace_option_prompt(inv_id, self._seat_prompt(inv))
        self._note()

    @on(OptionList.OptionHighlighted, "#host-seats")
    def _seat_moved(self, event: OptionList.OptionHighlighted) -> None:
        self._seat_card(event.option.id or "")

    def _seat_card(self, inv_id: str) -> None:
        """The highlighted investigator, as the lobby and the game's F4 show them."""
        inv = next((i for i in self.party if i.id == inv_id), None)
        if inv is None or self.module is None:
            return
        style = self.app.art_style  # type: ignore[attr-defined]
        picture = inv.portrait in self.module.pictures and imagery.available()
        face = self.faces.get((inv.portrait, style)) if picture else art_text(self.module, inv.portrait)
        width = max(40, self.query_one("#host-preview").scrollable_content_region.width - 2)
        self.query_one("#host-card", Static).update(sheet_view(inv, face, width))
        if picture and face is None:
            self._draw_face(inv, style)

    @work(exclusive=True, group="host-face")
    async def _draw_face(self, inv: Investigator, style: str) -> None:
        assert self.module is not None
        m = self.module

        def draw() -> Text:
            try:
                return Text.from_ansi(imagery.render(m.pictures[inv.portrait], *FACE, style, face=True))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return art_text(m, inv.portrait)

        self.faces[(inv.portrait, style)] = await asyncio.to_thread(draw)
        seats = self.query_one("#host-seats", OptionList)
        if seats.highlighted is not None and seats.get_option_at_index(seats.highlighted).id == inv.id:
            self._seat_card(inv.id)

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
            except (OSError, ValueError) as exc:
                self.app.notify(f"读不了这个存档：{exc.__class__.__name__}", severity="error")
                return
            self.slot = state.slot
            self.party = list(state.investigators)
            self.plan = {inv.id: "玩家" if inv.id in state.human else "AI" for inv in self.party}
        self._list_seats()
        self._note()
        self.app.notify("新开一局" if not self.slot else "接着玩这个存档：座位可以换成玩家或 AI", timeout=3)

    def _note(self) -> None:
        players = sum(1 for v in self.plan.values() if v == "玩家")
        ai = sum(1 for v in self.plan.values() if v == "AI")
        t = Text()
        t.append(f"{players} 位玩家 · {ai} 位 AI" + ("" if not self.slot else " · 接着玩存档"), style="bold")
        t.append(f"\n{lan.RULE}。", style="dim")
        self.query_one("#host-note", Static).update(t)

    # -- opening --------------------------------------------------------------------------------
    @on(Button.Pressed, "#host-back")
    def _back(self) -> None:
        self.action_back()

    @on(Input.Submitted, "#host-room Input")
    @on(Button.Pressed, "#host-open")
    def open_room(self) -> None:
        if self.path is None or self.module is None:
            return
        name = self.query_one("#host-name", Input).value.strip()[:16] or "房主"
        try:
            port, web_port = (int(self.query_one(f"#{i}", Input).value) for i in ("host-port", "host-web-port"))
        except ValueError:
            self.app.notify("端口要填数字，比如 8765 和 8000", severity="error")
            return
        web = self.query_one("#host-web", Checkbox).value
        if not (1 <= port <= 65535 and 1 <= web_port <= 65535):
            self.app.notify("端口要在 1 到 65535 之间", severity="error")
            return
        if web and port == web_port:
            self.app.notify("联机端口和网页端口不能是同一个", severity="error")
            return
        seats = [self.plan.get(inv.id, "AI") for inv in self.party]
        if all(s == "不上场" for s in seats):
            self.app.notify("至少要有一位调查员上场", severity="error")
            return
        state = None
        if self.slot:
            state = lan.saved(self.slot)
            state.human = [inv.id for inv in state.investigators if self.plan.get(inv.id) == "玩家"]
        self._open(name, port, web_port, web, None if state else seats, state)

    @work(exclusive=True, group="host")
    async def _open(self, name: str, port: int, web_port: int, web: bool, seats: list[str] | None, state: Any) -> None:
        assert self.path is not None
        button = self.query_one("#host-open", Button)
        button.disabled = True
        hint = self.query_one("#setup-hint", Static)
        hint.update(Text("正在开房间……", style="bold"))
        if (old := getattr(self.app, "room", None)) is not None:  # one room at a time; this one had stopped
            self.app.room = None  # type: ignore[attr-defined]
            await asyncio.to_thread(old.close)
        room = lan.Room(self.path, seats=seats, state=state, port=port, web_port=web_port)
        try:
            await asyncio.to_thread(room.open)
            if web:
                config.SAVES.mkdir(parents=True, exist_ok=True)
                with open(config.SAVES / "web.log", "a", encoding="utf-8") as log:  # the page keeps its own handle
                    await asyncio.to_thread(room.start_web, log)
        except lan.RoomError as exc:
            await asyncio.to_thread(room.close)
            self.app.notify(f"房间没开成：{exc}", severity="error", timeout=10)
            button.disabled = False
            hint.update("回车或点击换座位 · Tab 下一项 · Esc 换模组")
            return
        lan.remember(name=name, port=port, web_port=web_port, web=web)
        self.app.room = room  # type: ignore[attr-defined]
        from .lobby import LobbyScreen

        self.app.switch_screen(LobbyScreen(f"127.0.0.1:{room.port}", name, key=room.hub.key if room.hub else "", room=room))

