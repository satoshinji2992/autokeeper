"""开始调查: pick a module and who plays, then start the game."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from ... import imagery, paths, roster
from ...engine.game import Game
from ...module.schema import Module
from ...rules.coc import Investigator
from ..library import art_text, load_module
from ..widgets import tips
from .game import GameScreen


class SetupScreen(Screen):
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, spectate: bool, module: Path | None = None, card: str | None = None):
        super().__init__()
        self.spectate = spectate  # AI players only; not `watch`, which is Textual's
        self.step = "module"
        self.module: Module | None = None
        self.lead: Investigator | None = None
        self.cards: list[Investigator] = []  # public-figure cards kept from before (roster.py)
        self.paths = paths.module_folders()
        self.preselected = module  # just imported: straight on to its investigators
        self.card = card  # from 角色卡: this investigator comes up highlighted

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(id="setup-title")
            with Horizontal(id="setup-body"):
                yield OptionList(id="choices")
                with VerticalScroll(id="preview"):
                    yield Static(id="preview-body")
            yield Static(id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._show_modules()
        if self.preselected in self.paths:
            ol = self.query_one("#choices", OptionList)
            ol.highlighted = self.paths.index(self.preselected)
            ol.action_select()

    def action_back(self) -> None:
        if self.step == "module":
            self.app.pop_screen()
        elif self.step == "investigator":
            self._show_modules()
        else:
            self._show_investigators()

    def _set(self, title: str, options: list[Option], hint: str) -> None:
        self.query_one("#setup-title", Static).update(Text(title, style="bold"))
        ol = self.query_one("#choices", OptionList)
        ol.clear_options()
        ol.add_options(options)
        ol.highlighted = 0
        ol.focus()
        self.query_one("#setup-hint", Static).update(hint)

    def _show_modules(self) -> None:
        self.step = "module"
        opts = []
        for p in self.paths:
            try:
                m = Module.load(p)
                opts.append(Option(f"《{m.title}》 {m.players}", id=str(p)))
            except Exception:  # broken module files should not crash the menu
                opts.append(Option(f"{p.name}（无法读取）", id=str(p), disabled=True))
        self._set("选择模组" + ("（AI 观战）" if self.spectate else ""), opts, "Enter 或空格选择 · Esc 返回")
        tips.first_game(self)  # no saves yet: 《追书人》 is the tutorial

    def _show_investigators(self) -> None:
        assert self.module
        self.step = "investigator"
        if self.spectate:
            opts = [Option(f"{n} 名 AI 调查员", id=f"party-{n}") for n in (1, 2, 3) if n <= max(1, len(self.module.pregens))]
            self._set(f"《{self.module.title}》· 观战人数", opts, "Enter 或空格开始 · Esc 返回")
            return
        self.cards = roster.saved()
        roster.attach(self.module, self.cards)
        opts = [Option(f"{p.name}  {p.occupation}", id=p.id) for p in self.module.pregens]
        opts += [Option(f"{c.name}  {c.occupation}（我的卡）", id=c.id) for c in self.cards]
        opts += [Option("＋ 创建调查员", id="card:new"), Option("＋ 导入已有的卡", id="card:import")]
        self._set(f"《{self.module.title}》· 选择你的调查员", opts, "Enter 或空格选择 · Esc 返回")
        if self.card in [o.id for o in opts]:
            self.query_one("#choices", OptionList).highlighted = [o.id for o in opts].index(self.card)

    def _investigator(self, inv_id: str) -> Investigator | None:
        assert self.module
        return next((p for p in [*self.module.pregens, *self.cards] if p.id == inv_id), None)

    def _carded(self, inv: Investigator | None) -> None:
        if inv is None:
            self._show_investigators()
            return
        self.lead = inv
        self._show_companions()

    def _show_companions(self) -> None:
        assert self.module and self.lead
        self.step = "companions"
        others = [p for p in self.module.pregens if p.id != self.lead.id]
        opts = [Option("独自调查", id="solo")]
        opts += [Option(f"带 {n} 名 AI 同伴", id=f"with-{n}") for n in (1, 2) if n <= len(others)]
        self._set("同伴", opts, "同伴由 AI 扮演，在你之后行动 · Esc 返回")

    @on(OptionList.OptionHighlighted, "#choices")
    def preview(self, event: OptionList.OptionHighlighted) -> None:
        body = self.query_one("#preview-body", Static)
        oid = event.option.id or ""
        if self.step == "module" and oid:
            m = Module.load(Path(oid))
            first = m.cover or next((loc.art for loc in m.locations if loc.art), None)
            t = Text()
            t.append(f"《{m.title}》", style="bold")
            t.append(f"  {m.subtitle}\n", style="dim")
            t.append(f"{m.era} · {m.setting} · {m.players} · {m.duration}\n\n", style="dim")
            header = t.copy()
            t.append_text(art_text(m, first))
            t.append("\n\n" + m.hook.strip() + "\n\n", style="italic")
            t.append(m.source, style="dim")
            body.update(t)
            if first in m.pictures:
                self._preview_original(m, first, header, oid)
        elif self.step == "investigator" and self.module and not self.spectate:
            if oid == "card:new":
                body.update(Text("输入公众人物姓名或人物描述。系统将检索资料、确认背景，并结合模组年代创建调查员。保存的人物卡可在之后的游戏中使用。", style="dim"))
            elif oid == "card:import":
                body.update(Text("支持导入骰娘 .st 指令、人物表格、PDF、Word、纸质人物卡照片，以及酒馆（SillyTavern）角色卡。已有数值按原样录入；仅含人设的角色卡将通过背景问答创建为调查员。", style="dim"))
            elif inv := self._investigator(oid):
                body.update(_investigator_card(self.module, inv, self.app.art_style, photo=False))  # type: ignore[attr-defined]
                if inv.portrait in self.module.pictures and imagery.available():
                    self._preview_card(self.module, inv, oid)

    @work(exclusive=True, group="module-preview")
    async def _preview_original(self, m: Module, key: str, header: Text, oid: str) -> None:
        # As wide as the preview pane (a fixed 40 columns left a small blur in a wide pane), cut
        # to its shape, in the art style the player chose.
        width = max(32, self.query_one("#preview-body", Static).content_size.width - 2)
        size = (width, max(10, min(24, round(width * 0.38))))
        style = self.app.art_style  # type: ignore[attr-defined]
        def draw() -> Text:
            if imagery.available():
                try:
                    return Text.from_ansi(imagery.render(m.pictures[key], *size, style, fill=True))
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass  # an unreadable picture: its hand-drawn art instead
            return art_text(m, key)

        picture = await asyncio.to_thread(draw)
        choices = self.query_one("#choices", OptionList)
        if self.step != "module" or choices.highlighted is None or choices.get_option_at_index(choices.highlighted).id != oid:
            return
        header.append_text(picture)
        header.append("\n\n" + m.hook.strip() + "\n\n", style="italic")
        header.append(m.source, style="dim")
        self.query_one("#preview-body", Static).update(header)

    @work(exclusive=True, group="card-preview", exit_on_error=False)
    async def _preview_card(self, m: Module, inv: Investigator, oid: str) -> None:
        """The card with its photo, drawn in a thread: ↑↓ through the investigators no longer
        waits for chafa at each new face."""
        card = await asyncio.to_thread(_investigator_card, m, inv, self.app.art_style)  # type: ignore[attr-defined]
        if not self.is_attached:  # chosen meanwhile, and on to the game
            return
        choices = self.query_one("#choices", OptionList)
        if self.step == "investigator" and choices.highlighted is not None and choices.get_option_at_index(choices.highlighted).id == oid:
            self.query_one("#preview-body", Static).update(card)

    @on(OptionList.OptionSelected, "#choices")
    def select(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id or ""
        if self.step == "module":
            self.module = load_module(Path(oid))
            if not self.module.pregens:
                self.app.notify("此模组未提供预设调查员。", severity="error")
                return
            self._show_investigators()
        elif self.step == "investigator":
            assert self.module
            if self.spectate:
                n = int(oid.split("-")[1])
                party = [p.model_copy(deep=True) for p in self.module.pregens[:n]]
                self._start(party, human=[])
            elif oid in ("card:new", "card:import"):
                from .card import CardScreen

                self.app.push_screen(CardScreen(self.module, start="figure" if oid == "card:new" else "import"), self._carded)
            else:
                self.lead = self._investigator(oid)
                self._show_companions()
        elif self.step == "companions":
            assert self.module and self.lead
            n = 0 if oid == "solo" else int(oid.split("-")[1])
            others = [p for p in self.module.pregens if p.id != self.lead.id][:n]
            party = [self.lead.model_copy(deep=True)] + [p.model_copy(deep=True) for p in others]
            self._start(party, human=[self.lead.id])

    def _start(self, party: list[Investigator], human: list[str]) -> None:
        assert self.module
        slot = f"{self.module.id}-{time.strftime('%m%d-%H%M%S')}"
        state = Game.new(self.module, party, slot=slot, human=human)
        self.app.switch_screen(GameScreen(self.module, state, spectate=self.spectate))


def _investigator_card(m: Module, inv: Investigator, style: str = "photo", *, photo: bool = True) -> Text:
    """`photo=False`: the hand-drawn face (or none) in its place, without waiting for chafa."""
    t = Text()
    t.append_text(art_text(m, inv.portrait, (32, 14) if photo and inv.portrait in m.pictures else None, style, face=True))
    t.append(f"\n\n{inv.name}", style="bold")
    t.append(f"  {inv.occupation} · {inv.age}岁\n", style="dim")
    for k, v in inv.backstory.items():
        t.append(f"{k}：", style="bold")
        t.append(f"{v}\n")
    t.append("\n" + "  ".join(f"{k}{v}" for k, v in inv.characteristics.items()) + f"  幸运{inv.luck}\n", style="dim")
    t.append("  ".join(f"{k}{v}" for k, v in sorted(inv.skills.items(), key=lambda kv: -kv[1])))
    return t


# -- the game -------------------------------------------------------------------------------
# Everything the game screen can do, for the Ctrl+P menu: (title, key, what it does, action).
# The footer shows the first few; the rest are a search away.
