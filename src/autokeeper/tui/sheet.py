"""The whole investigator sheet, as on paper: portrait, characteristics with their half and
fifth, derived values, every skill (the raised ones stand out, improvement ticks shown),
backstory and belongings. ←/→ go through the party."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..rules.catalog import SKILL_BASE, base_value
from ..rules.coc import Investigator
from . import art

FAMILIES = ("艺术与手艺", "外语", "母语", "科学", "驾驶", "生存")  # general entries replaced by the specialisms they have


def skill_rows(inv: Investigator) -> list[tuple[str, int, bool, bool]]:
    """(name, value, raised above its base, ticked for improvement) for every skill on the sheet."""
    names = [k for k in SKILL_BASE if k not in ("克苏鲁神话", "信用评级")]
    special = [k for k in inv.skills if k not in SKILL_BASE]
    for family in FAMILIES:
        mine = [k for k in special if k.split("(")[0] == family]
        if mine and family in names:
            at = names.index(family)
            names[at : at + 1] = mine
    names += [k for k in special if k not in names]
    names = ["信用评级", "克苏鲁神话", *names]
    rows = []
    for name in names:
        value = inv.skills.get(name, base_value(name, inv.characteristics))
        rows.append((name, value, value > base_value(name, inv.characteristics) or (name == "克苏鲁神话" and value > 0), name in inv.marks))
    return rows


def sheet_view(inv: Investigator, portrait: Text | None = None) -> Group:
    head = Table.grid(padding=(0, 2))
    head.add_column()
    head.add_column()
    who = Text()
    who.append(f"{inv.name}\n", style="bold")
    who.append(f"{inv.occupation}\n", style="bold")
    facts = [f"{inv.age} 岁", inv.sex, f"住在{inv.residence}" if inv.residence else "", f"生于{inv.birthplace}" if inv.birthplace else ""]
    who.append(" · ".join(f for f in facts if f) + "\n\n", style="dim")
    who.append("HP  ")
    who.append(art.bar(inv.hp, inv.hp_max), style="bold red" if inv.hp * 3 <= inv.hp_max else "bold")
    who.append(f" {inv.hp}/{inv.hp_max}\nSAN ")
    who.append(art.bar(inv.san, inv.san_max), style="bold magenta" if inv.san * 5 < inv.san_max * 3 else "bold")
    who.append(f" {inv.san}/{inv.san_max}\n")
    who.append(f"MP {inv.mp}/{inv.mp_max}   幸运 {inv.luck}   MOV {inv.move}   DB {inv.damage_bonus}   体格 {inv.build}\n")
    if inv.conditions:
        who.append("状态：" + "、".join(inv.conditions) + "\n", style="bold red")
    head.add_row(portrait if portrait is not None and portrait.plain else Text(""), who)

    stats = Table.grid(padding=(0, 3))
    for _ in range(4):
        stats.add_column()
    cells = []
    for k, v in inv.characteristics.items():
        cell = Text()
        cell.append(f"{k} ", style="dim")
        cell.append(f"{v:>2}", style="bold")
        cell.append(f"  {v // 2}/{v // 5}", style="dim")
        cells.append(cell)
    for i in range(0, len(cells), 4):
        stats.add_row(*cells[i : i + 4])

    skills = Table.grid(padding=(0, 1))
    for i in range(4):  # four (name, value) pairs, values right-aligned
        skills.add_column(no_wrap=True)
        skills.add_column(justify="right", width=3)
        if i < 3:
            skills.add_column(width=2)
    rows = skill_rows(inv)
    per = (len(rows) + 3) // 4
    columns = [rows[i * per : (i + 1) * per] for i in range(4)]
    for r in range(per):
        line: list[Text] = []
        for c, col in enumerate(columns):
            if r < len(col):
                name, value, raised, ticked = col[r]
                style = "bold" if raised else "dim"
                line += [Text.assemble(("✓" if ticked else " ", "bold green"), (name, style)), Text(str(value), style=style)]
            else:
                line += [Text(""), Text("")]
            if c < 3:
                line.append(Text(""))
        skills.add_row(*line)

    story = Text()
    for k, v in inv.backstory.items():
        if k != "说明" and v:
            story.append(f"{k}：", style="bold")
            story.append(f"{v}\n")
    if inv.inventory:
        story.append("随身：", style="bold")
        story.append("、".join(inv.inventory) + "\n")
    rule = Text("─" * 40, style="dim")
    parts = [head, rule, stats, rule, skills]
    if story.plain:
        parts += [rule, story]
    return Group(*parts)


class SheetModal(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "关闭"),
        Binding("left", "turn(-1)", "上一位"),
        Binding("right,tab", "turn(1)", "下一位"),
    ]

    def __init__(self, party: list[Investigator], first: int = 0, portrait: Callable[[Investigator], Text | None] | None = None):
        super().__init__()
        self.party, self.at = party, max(0, min(first, len(party) - 1))
        self.portrait = portrait

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="sheet-modal"):
            yield Static(classes="modal-title", id="sheet-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(id="sheet-body")
            yield Static("←/→ 切换队友 · Esc 关闭" if len(self.party) > 1 else "Esc 关闭", classes="system")

    def on_mount(self) -> None:
        self._show_card()

    def _show_card(self) -> None:
        inv = self.party[self.at]
        where = f"  {self.at + 1}/{len(self.party)}" if len(self.party) > 1 else ""
        self.query_one("#sheet-title", Static).update(f"人物卡 · {inv.name}{where}")
        self.query_one("#sheet-body", Static).update(sheet_view(inv, self.portrait(inv) if self.portrait else None))

    def action_turn(self, step: int) -> None:
        self.at = (self.at + step) % len(self.party)
        self._show_card()
