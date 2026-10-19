"""选角色: the investigator for your seat at a LAN table, before the game starts. Your own cards
(我的角色, tui/screens/cards.py; none in a browser seat, which runs on the host's machine), the module's
pregens nobody else has, or a new one: AI 协助车卡 or 导入已有的卡, made on the host's models
(net/cards.py). The highlighted one's sheet is beside the list."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from ...module.schema import Module
from ...rules.coc import Investigator
from ..modals.menu import GameMenu
from ..modals.sheet import sheet_view
from .cards import ROOM, portrait

HINT = "[@click=screen.choose]Enter 选定[/] · [@click=screen.create]A 新建角色[/] · [@click=screen.back]Esc 返回[/]"


class SeatCardScreen(Screen[tuple[str, object] | None]):
    """Dismisses with ("mine", the card), ("pregen", its id), ("figure" or "import", "") for a
    new one, or None."""

    BINDINGS = [Binding("escape", "back", "返回", show=False), Binding("a", "create", "新建角色", show=False)]

    def __init__(self, m: Module, mine: list[Investigator] | None, taken: set[str], current: str = ""):
        super().__init__()
        self.m, self.mine, self.taken, self.current = m, mine, taken, current
        self.cards: dict[str, tuple[Investigator, Module | None]] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(Text("选角色", style="bold"), id="setup-title")
            with Horizontal(id="setup-body"):
                yield OptionList(id="choices")
                with VerticalScroll(id="preview"):
                    yield Static(id="preview-body")
            yield Static(HINT, id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        ol = self.query_one("#choices", OptionList)
        lit = f"bold {self.app.current_theme.primary}"

        def line(inv: Investigator, extra: str = "") -> Text:
            t = Text(f"  {inv.name}")
            t.append(f"  {inv.occupation.split('（')[0]}", style="dim")
            if extra:
                t.append(f"  {extra}", style="dim italic")
            t.truncate(ROOM, overflow="ellipsis")
            return t

        if self.mine is not None:
            ol.add_option(Option(Text("我的角色", style=lit), disabled=True))
            for inv in self.mine:
                self.cards[f"r:{inv.id}"] = (inv, None)
                ol.add_option(Option(line(inv), id=f"r:{inv.id}"))
            if not self.mine:
                ol.add_option(Option(Text("  还没有", style="dim"), disabled=True))
        if self.m.pregens:
            ol.add_option(Option(Text("模组预设", style=lit), disabled=True))
        for inv in self.m.pregens:
            self.cards[f"m:{inv.id}"] = (inv, self.m)
            busy = inv.id in self.taken
            note = "现在用的" if inv.id == self.current else "别人在用" if busy else ""
            ol.add_option(Option(line(inv, note), id=f"m:{inv.id}", disabled=busy))
        ol.add_option(Option(Text("＋ 新建角色"), id="create"))
        first = next((oid for oid in self.cards if oid == f"m:{self.current}"), None) or next(
            (oid for oid in self.cards if not ol.get_option(oid).disabled), "create")
        ol.highlighted = ol.get_option_index(first)
        ol.focus()

    @on(OptionList.OptionHighlighted, "#choices")
    def _moved(self, event: OptionList.OptionHighlighted) -> None:
        oid = event.option.id or ""
        if oid in self.cards:
            self._show(oid)
        else:
            self.workers.cancel_group(self, "card")
            self.query_one("#preview-body", Static).update(Text("由 AI 协助创建调查员，或导入已有的人物卡。创建过程使用房主配置的模型。", style="dim"))

    @work(exclusive=True, group="card")
    async def _show(self, oid: str) -> None:
        inv, m = self.cards[oid]
        body = self.query_one("#preview-body", Static)
        width = body.content_size.width or None
        body.update(sheet_view(inv, width=width))  # the words at once, the face when it is drawn
        face = await asyncio.to_thread(portrait, inv, m, self.app.art_style)  # type: ignore[attr-defined]
        if face is not None:
            body.update(sheet_view(inv, face, width))

    @on(OptionList.OptionSelected, "#choices")
    def _picked(self) -> None:
        self.action_choose()

    def action_choose(self) -> None:
        ol = self.query_one("#choices", OptionList)
        oid = ol.get_option_at_index(ol.highlighted).id if ol.highlighted is not None else None
        if oid == "create":
            self.action_create()
        elif oid in self.cards:
            inv, _ = self.cards[oid]
            self.dismiss(("mine", inv) if oid.startswith("r:") else ("pregen", inv.id))

    def action_create(self) -> None:
        items = [("figure", "AI 协助车卡"), ("import", "导入已有的卡")]
        self.app.push_screen(GameMenu(items, "figure", title="新建角色", hint="Enter 选择 · Esc 返回"),
                             lambda how: self.dismiss((how, "")) if how else None)  # fmt: skip

    def action_back(self) -> None:
        self.dismiss(None)
