"""F10 after the ending: the debrief (engine/debrief.py) and, below it, the epilogue.

The debrief is drawn once; the epilogue is read from the screen underneath, which follows
the keeper's events, so it keeps filling in even if this is closed and opened again while
the keeper is still talking.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ...engine.debrief import ClueNote, Debrief
from ..widgets.panels import MISS, PASS


def view(d: Debrief) -> Text:
    t = Text()
    t.append(f"结局：{d.ending}\n", style="bold")
    if d.ending_text:
        t.append(f"{d.ending_text}\n", style="italic")
    _head(t, "真相")
    t.append(f"{d.truth or '（模组未提供）'}\n")

    _head(t, f"线索：找到 {len(d.found)} 条，共 {len(d.found) + len(d.missed)} 条")
    for c in d.found:
        _clue(t, c, found=True)
    for c in d.missed:
        _clue(t, c, found=False)

    if d.deductions:
        _head(t, "线索关联")
        for text, got, total in d.deductions:
            t.append(f"  {text}")
            t.append(f"（线索 {got}/{total}）\n" if total else "\n", style="dim")

    if d.people:
        _head(t, "人物")
        for p in d.people:
            t.append(f"  {p.name}", style="bold")
            t.append(f"  {p.role} · {'已遇见' if p.met else '未遇见'}\n", style="dim")
            for label, items in (("目标", [p.goal] if p.goal else []), ("秘密", p.secrets), ("谎言", p.lies)):
                for item in items:
                    t.append(f"    {label}：{item}\n")

    if d.roads:
        _head(t, "其他结局")
        for r in d.roads:
            t.append(f"  {r.title}\n", style="bold")
            for line in r.how:
                t.append(f"    {line}\n")
            if r.text:
                t.append(f"    {r.text}\n", style="dim italic")

    if d.turns:
        _head(t, "本局事件")
        for line in d.turns:
            t.append(f"  {line}\n")
    if d.unvisited:
        t.append(f"\n未到访地点：{'、'.join(d.unvisited)}\n", style="dim")
    return t


def _head(t: Text, title: str) -> None:
    t.append(f"\n{title}\n", style="bold underline")


def _clue(t: Text, c: ClueNote, *, found: bool) -> None:
    t.append("  ✓ " if found else "  ✗ ", style=f"bold {PASS if found else MISS}")
    t.append(f"〔{c.title}〕", style="bold")
    t.append(c.where)
    if not found:
        t.append(f" · {c.how}", style="dim")
    if c.core:
        t.append(" · 关键", style="bold")
    t.append("\n")
    if not found and c.text:
        t.append(f"      {c.text}\n", style="dim")


def epilogue_view(text: str, writing: bool) -> Text:
    t = Text()
    _head(t, "后日谈")
    if not text:
        t.append("正在生成后日谈……" if writing else "按 E 生成后日谈，查看已遇见人物与调查员的后续经历。", style="dim")
        return t
    for para in text.strip().split("\n"):
        name, sep, rest = para.partition("：")
        if sep and 0 < len(name) <= 12:  # a paragraph that opens with whose it is
            t.append(name + sep, style="bold")
            t.append(rest + "\n")
        else:
            t.append(para + "\n")
    return t


class DebriefModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "关闭"), Binding("e", "epilogue", "后日谈")]

    def __init__(self, debrief: Debrief, so_far: Callable[[], tuple[str, bool]], ask: Callable[[], None]):
        """`so_far` gives the epilogue as far as it has come and whether the keeper is still
        at it; `ask` asks for it."""
        super().__init__()
        self.debrief, self.so_far, self.ask_keeper = debrief, so_far, ask
        self.painted: tuple[str, bool] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="debrief-modal"):
            yield Static(f"复盘 · 《{self.debrief.title}》", classes="modal-title")
            with VerticalScroll(classes="modal-body", id="debrief-body"):
                yield Static(view(self.debrief))
                yield Static(id="epilogue")
            yield Static("↑↓ 翻看 · E 后日谈 · Esc 关闭", classes="system")

    def on_mount(self) -> None:
        self._size_body()
        self._paint_epilogue()
        self.set_interval(0.1, self._paint_epilogue)
        self.query_one("#debrief-body").focus()

    def on_resize(self) -> None:
        self._size_body()

    def _size_body(self) -> None:
        # The debrief is long: most of the screen, where .modal-body stops at 30 rows.
        self.query_one("#debrief-body").styles.max_height = max(8, self.app.size.height * 9 // 10 - 6)

    def action_epilogue(self) -> None:
        text, writing = self.so_far()
        if not text and not writing:
            self.ask_keeper()
        self.query_one("#epilogue").scroll_visible()

    def _paint_epilogue(self) -> None:
        now = self.so_far()
        if now == self.painted:
            return
        self.painted = now
        self.query_one("#epilogue", Static).update(epilogue_view(*now))
        if now[1]:  # the keeper is talking: follow the words
            self.query_one("#debrief-body", VerticalScroll).scroll_end(animate=False)
