"""角色卡: every investigator on this machine, from the title screen. The cards kept from
车卡, the player's investigator in each save (as they stand now), and each module's pregens; the
highlighted one's full sheet beside the list, Enter for the sheet on its own."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from .. import artkit, config, imagery, roster
from ..module.schema import Module
from ..rules.coc import Investigator
from .sheet import SheetModal, sheet_view

FACE = (30, 14)
ROOM = 34  # the list's width inside its border and padding: longer lines are cut, not wrapped


class CardsScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    def __init__(self) -> None:
        super().__init__()
        # option id -> (the investigator, the group it is shown with, the module its pictures come from)
        self.cards: dict[str, tuple[Investigator, list[Investigator], Module | None]] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(Text("角色卡", style="bold"), id="setup-title")
            with Horizontal(id="setup-body"):
                yield OptionList(id="choices")
                with VerticalScroll(id="preview"):
                    yield Static(id="preview-body")
            yield Static("↑↓ 翻看 · Enter 或空格单独打开 · Esc 返回", id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        from .app import load_module, modules, save_index

        groups: list[tuple[str, list[Investigator], Module | None, str]] = []
        mine = roster.saved()
        groups.append(("我的调查员", mine, None, "r"))
        loaded: dict[str, Module] = {}

        def module(path: Path) -> Module | None:
            if str(path) not in loaded:
                try:
                    loaded[str(path)] = load_module(path)
                except Exception:  # a broken module still lets the rest of the list show
                    return None
            return loaded[str(path)]

        seen: set[tuple[str, str]] = set()  # (module, investigator): only the newest save of each
        for slot, meta in sorted(save_index().items(), key=lambda kv: kv[1].get("updated", ""), reverse=True):
            try:
                state = json.loads((config.SAVES / f"{slot}.json").read_text(encoding="utf-8"))
                party = [Investigator.model_validate(p) for p in state["investigators"] if p["id"] in state.get("human", [])]
            except (OSError, ValueError, KeyError):
                continue
            party = [p for p in party if (meta.get("module", ""), p.id) not in seen]
            seen.update((meta.get("module", ""), p.id) for p in party)
            if party:  # games only watched have no one of the player's
                when = str(meta.get("updated", ""))[5:]
                groups.append((f"《{meta.get('title', '?')}》存档 {when}", party, module(Path(meta.get("path", ""))), f"s{slot}"))
        for path in modules():
            m = module(path)
            if m and m.pregens:
                groups.append((f"《{m.title}》预设", list(m.pregens), m, f"m{path.name}"))

        ol = self.query_one("#choices", OptionList)
        for title, party, m, tag in groups:
            head = Text(title, style="dim")
            head.truncate(ROOM, overflow="ellipsis")
            ol.add_option(Option(head, disabled=True))
            if not party:
                ol.add_option(Option("  （还没有。新的调查 → 创建调查员）", disabled=True))
            for inv in party:
                oid = f"{tag}:{inv.id}"
                self.cards[oid] = (inv, party, m)
                label = Text(f"  {inv.name}")
                label.append(f"  {inv.occupation.split('（')[0]}", style="dim")
                label.truncate(ROOM, overflow="ellipsis")
                ol.add_option(Option(label, id=oid))
        first = next((i for i in range(ol.option_count) if not ol.get_option_at_index(i).disabled), None)
        if first is not None:
            ol.highlighted = first
        ol.focus()

    def _portrait(self, inv: Investigator, m: Module | None, style: str) -> Text | None:
        path = str(roster.folder() / f"{inv.portrait}.jpg") if m is None else m.pictures.get(inv.portrait)
        if path and Path(path).exists() and imagery.available():
            try:
                return Text.from_ansi(imagery.render(path, *FACE, style, face=True))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        if m is not None and inv.portrait in m.art:
            return artkit.colorize(m.art[inv.portrait], m.art_colors.get(inv.portrait))
        return None

    @on(OptionList.OptionHighlighted, "#choices")
    def preview(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id in self.cards:
            self._show(event.option.id)

    @work(exclusive=True, group="card")
    async def _show(self, oid: str) -> None:
        inv, _, m = self.cards[oid]
        body = self.query_one("#preview-body", Static)
        width = body.content_size.width or None
        body.update(sheet_view(inv, width=width))  # the words at once, the face when it is drawn
        face = await asyncio.to_thread(self._portrait, inv, m, self.app.art_style)  # type: ignore[attr-defined]
        if face is not None:
            body.update(sheet_view(inv, face, width))

    @on(OptionList.OptionSelected, "#choices")
    def open(self, event: OptionList.OptionSelected) -> None:
        if event.option.id not in self.cards:
            return
        inv, party, m = self.cards[event.option.id]
        style = self.app.art_style  # type: ignore[attr-defined]
        self.app.push_screen(SheetModal(party, party.index(inv), lambda i: self._portrait(i, m, style)))
