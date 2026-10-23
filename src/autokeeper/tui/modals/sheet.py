"""The whole investigator sheet, as on paper: portrait, characteristics with their half and
fifth, derived values, every skill (the raised ones stand out, improvement ticks shown),
what they carry (装备, always shown), the backstory grouped as the paper sheet has it. In a game, also 本局经历:
what they did and rolled, the papers handed to them, what it cost them, and the promises and
ties in the world ledger that are theirs, all of it what the table saw (GameState.deeds, which a
LAN screen gets through the sync). Never a clue's title: what they learnt the story told, and
the player keeps it in their own notes. ←/→ go through the party."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ...engine.state import Deed, GameState
from ...rules.catalog import SKILL_BASE, base_value
from ...rules.coc import Investigator
from ..drawing import art

FAMILIES = ("艺术与手艺", "外语", "母语", "科学", "驾驶", "生存")  # general entries replaced by the specialisms they have
# The backstory as the paper sheet groups it, by words in the field's name, since imported
# cards name them their own way; anything else comes last, as written.
GROUPS = (
    ("为人", ("个人描述", "外貌", "特质", "性格", "思想", "信念")),
    ("牵挂", ("重要之人", "之地", "地点", "宝贵", "珍视")),
    ("异闻", ("伤", "疤", "恐惧", "躁狂", "典籍", "咒", "神器", "接触", "遭遇")),
)
MARKS = {"act": "▸", "check": "◈", "handout": "▤", "sanity": "※", "damage": "✚"}
LEDGER = {"fact": "记下", "relationship": "关系", "promise": "约定", "fulfill": "约定", "transfer": "转交"}


def skill_rows(inv: Investigator) -> list[tuple[str, int | str, bool, bool]]:
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
        if name == "克苏鲁神话" and inv.san is None:
            rows.append((name, "未公开", False, False))
            continue
        base = base_value(name, inv.characteristics, default=1 if name in inv.skills else None)
        value = inv.skills.get(name, base)
        rows.append((name, value, value > base or (name == "克苏鲁神话" and value > 0), name in inv.marks))
    return rows


def sheet_view(inv: Investigator, portrait: Text | None = None, width: int | None = None, *, state: GameState | None = None) -> Group:
    """`width`: the room it has, when narrower than the sheet window; fewer skill columns then.
    `state`: the game they are in, for 本局经历."""
    face = portrait if portrait is not None and portrait.plain else None
    face_width = max((cell_len(line) for line in face.plain.splitlines()), default=0) if face else 0
    # A drawn photo must keep its width: squeezed into a narrower column, each of its lines
    # wraps in two and the face turns into stripes. Too narrow for face and words side by
    # side: the face goes above them.
    stacked = face is not None and width is not None and width < face_width + 2 + 26
    head = Table.grid(padding=(0, 2))
    if face is not None and not stacked:
        head.add_column(width=face_width, no_wrap=True)
    else:
        head.add_column(no_wrap=True)
    head.add_column()
    who = Text()
    who.append(f"{inv.name}\n", style="bold")
    who.append(f"{inv.occupation}\n", style="bold")
    facts = [f"{inv.age} 岁", inv.sex, f"住在{inv.residence}" if inv.residence else "", f"生于{inv.birthplace}" if inv.birthplace else ""]
    who.append(" · ".join(f for f in facts if f) + "\n\n", style="dim")
    who.append("HP  ")
    who.append(art.bar(inv.hp, inv.hp_max), style="bold red" if inv.hp * 3 <= inv.hp_max else "bold")
    who.append(f" {inv.hp}/{inv.hp_max}\nSAN ")
    if inv.san is None:
        who.append("未公开\n", style="dim")
    else:
        who.append(art.bar(inv.san, inv.san_max), style="bold magenta" if inv.san * 5 < inv.san_max * 3 else "bold")
        who.append(f" {inv.san}/{inv.san_max}\n")
    gap = "\n" if width is not None and width < 96 else "   "  # beside a portrait in a narrow pane: two lines
    who.append(f"MP {inv.mp}/{inv.mp_max}   幸运 {inv.luck}   MOV {inv.move}{gap}DB {inv.damage_bonus}   体格 {inv.build}\n")
    if inv.conditions:
        who.append("状态：" + "、".join(inv.conditions) + "\n", style="bold red")
    if stacked:
        assert face is not None
        face.no_wrap, face.overflow = True, "crop"
        head = Table.grid()
        head.add_column(no_wrap=True)
        head.add_row(face)
        head.add_row(who)
    else:
        head.add_row(face or Text(""), who)

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
    n = 4 if width is None or width >= 96 else 3 if width >= 72 else 2
    for i in range(n):  # n (name, value) pairs, values right-aligned
        skills.add_column(no_wrap=True)
        skills.add_column(justify="right", width=6 if inv.san is None else 3)
        if i < n - 1:
            skills.add_column(width=2)
    rows = skill_rows(inv)
    per = (len(rows) + n - 1) // n
    columns = [rows[i * per : (i + 1) * per] for i in range(n)]
    for r in range(per):
        line: list[Text] = []
        for c, col in enumerate(columns):
            if r < len(col):
                name, value, raised, ticked = col[r]
                style = "bold" if raised else "dim"
                line += [Text.assemble(("✓" if ticked else " ", "bold green"), (name, style)), Text(str(value), style=style)]
            else:
                line += [Text(""), Text("")]
            if c < n - 1:
                line.append(Text(""))
        skills.add_row(*line)

    rule = Text("─" * 40, style="dim")
    parts: list[RenderableType] = [head, rule, stats, rule, skills, rule, gear_view(inv, state)]
    if (story := backstory_view(inv)) is not None:
        parts += [rule, story]
    if state is not None:
        parts += [rule, record_view(state, inv)]
    return Group(*parts)


MODES = {"carried": "携带", "worn": "穿戴", "held": "手持", "concealed": "藏着"}
PROVENANCE = re.compile(r"^[（(【]?\s*AutoKeeper\s*(补充|补写|依原文大意补写)[）)】]?\s*[：:]?\s*")


def gear_view(inv: Investigator, state: GameState | None = None) -> Table:
    """装备, always on the sheet: what the world record has them hold (weapons, keys, evidence,
    with how they carry it and how many are left), then the rest of what they carry."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True, style="dim")
    grid.add_column(ratio=1)
    rows: list[Text] = []
    named: set[str] = set()
    record = getattr(state, "record", None)
    for item in (record.entities.values() if record is not None else ()):
        if item.kind != "item" or item.at != inv.id:
            continue
        line = Text(item.name, style="bold" if item.important else "")
        extra = [MODES[item.equipment]] if item.important else []
        extra += [f"剩 {item.quantity}"] if item.quantity != 1 else []
        if extra:
            line.append(f"（{'，'.join(extra)}）", style="dim")
        rows.append(line)
        named.add(item.name)
    rows += [Text(x) for x in inv.inventory if x not in named]
    grid.add_row("装备", Text("、").join(rows) if rows else Text("无", style="dim"))
    return grid


def backstory_view(inv: Investigator) -> Table | None:
    """Every backstory field, grouped (GROUPS); None for a bare card. Where the card came from
    (说明, an "AutoKeeper 补充" in front of a line) is not part of the investigator."""
    groups: dict[str, list[Text]] = {}
    for k, v in inv.backstory.items():
        words = PROVENANCE.sub("", str(v).strip())
        if k == "说明" or not words:
            continue
        name = next((g for g, words in GROUPS if any(w in k for w in words)), "其他")
        groups.setdefault(name, []).append(Text.assemble((f"{k}：", "bold"), words))
    if not groups:
        return None
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True, style="dim")
    grid.add_column(ratio=1)
    for name in [g for g, _ in GROUPS] + ["其他"]:
        for i, line in enumerate(groups.get(name, [])):
            grid.add_row(name if i == 0 else "", line)
    return grid


def record_view(st: GameState, inv: Investigator) -> Group:
    """本局经历: their own actions and what came of them, a line each under the game clock;
    the papers handed to them, what it cost them; the promises and ties of theirs in the world ledger."""
    mine = [d for d in st.deeds if d.who == inv.id and d.kind != "clue"]  # a clue's title: never shown (state.Deed)
    parts: list[RenderableType] = [Text("本局经历", style="bold")]
    if mine:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(no_wrap=True, style="dim")
        grid.add_column(ratio=1)
        acted = None  # the turn of the last action shown: what followed it stands under it
        for d in mine:
            grid.add_row("" if d.kind != "act" and d.turn == acted else d.time, _deed(d))
            acted = d.turn if d.kind == "act" else acted
        parts.append(grid)
    else:
        parts.append(Text("暂无行动记录。", style="dim"))
    rows: list[tuple[str, Text]] = []
    papers = "".join(f"〔{d.text}〕" for d in mine if d.kind == "handout")
    if papers:
        rows.append(("手稿", Text(papers, style="bold")))
    hurt = [int(n) for d in mine if d.kind == "damage" for n in re.findall(r"受到 (\d+) 点伤害", d.text)]
    lost = sum(int(n) for d in mine if d.kind == "sanity" for n in re.findall(r"= (\d+)（", d.text))
    worn = ([f"受伤 {len(hurt)} 次，共 {sum(hurt)} 点"] if hurt else []) + ([f"理智共失去 {lost} 点"] if lost else [])
    if worn or inv.conditions:
        rows.append(("身心", Text(" · ".join(worn + ["、".join(inv.conditions)] * bool(inv.conditions)))))
    names = [n for n in {inv.name, *inv.name.split("·")} if len(n) >= 2]
    for f in st.world.values():  # the world ledger's promises and ties that are theirs
        if f.who == inv.id or any(n in f.text for n in names):
            kept = "（已履行）" if f.status == "fulfilled" else "（未了）" if f.kind == "promise" else ""
            rows.append((LEDGER.get(f.kind, f.kind), Text(f.text + kept)))
    if rows:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(no_wrap=True, style="dim")
        grid.add_column(ratio=1)
        for label, line in rows:
            grid.add_row(label, line)
        parts += [Text(""), grid]
    return Group(*parts)


def _deed(d: Deed) -> Text:
    mark = MARKS.get(d.kind, "·")
    if d.kind == "act":
        return Text.assemble((f"{mark} ", "dim"), (d.text, "bold"))
    if d.kind == "handout":
        return Text.assemble((f"{mark} 手稿", "dim"), (f"〔{d.text}〕", "bold"))
    style = {"sanity": "magenta", "damage": "red"}.get(d.kind, "dim")
    return Text(f"{mark} {d.text}", style=style)


class SheetModal(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "关闭"),
        Binding("left", "turn(-1)", "上一位"),
        Binding("right,tab", "turn(1)", "下一位"),
    ]

    def __init__(
        self,
        party: list[Investigator],
        first: int = 0,
        portrait: Callable[[Investigator], Text | None] | None = None,
        state: GameState | None = None,
    ):
        """`state`: the game they are in (a game, a save), for 本局经历."""
        super().__init__()
        self.party, self.at = party, max(0, min(first, len(party) - 1))
        self.portrait = portrait
        self.state = state

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="sheet-modal"):
            yield Static(classes="modal-title", id="sheet-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(id="sheet-body")
            yield Static("←/→ 切换队友 · Esc 关闭" if len(self.party) > 1 else "Esc 关闭", classes="system")

    def on_mount(self) -> None:
        self._show_card()

    def _show_card(self) -> None:
        """The words at once; the face once it is drawn, off the UI thread (a photo goes through chafa,
        which held the window still for a moment each time it opened or turned the page)."""
        inv = self.party[self.at]
        where = f"  {self.at + 1}/{len(self.party)}" if len(self.party) > 1 else ""
        self.query_one("#sheet-title", Static).update(f"人物卡 · {inv.name}{where}")
        self.query_one("#sheet-body", Static).update(sheet_view(inv, None, self._width(), state=self.state))
        if self.portrait is not None:
            self._face(self.at)

    def _width(self) -> int:
        return min(120, self.app.size.width - 8)  # the window's own room: fewer skill columns on a small terminal

    @work(exclusive=True, group="sheet-face", exit_on_error=False)
    async def _face(self, at: int) -> None:
        inv = self.party[at]
        face = await asyncio.to_thread(self.portrait, inv)  # type: ignore[arg-type]
        if at == self.at and face is not None and self.is_mounted:
            self.query_one("#sheet-body", Static).update(sheet_view(inv, face, self._width(), state=self.state))

    def action_turn(self, step: int) -> None:
        self.at = (self.at + step) % len(self.party)
        self._show_card()
