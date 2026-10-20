"""The pages under the sheet in the right column: 经过 (what happened, a line each: the history
panel), 进展 (the story so far, written again when the story has moved: engine/progress.py)
and 背景 (what the investigators knew when it began, and who they are). Ctrl+G turns the page,
and a click on a tab; without the column (a narrow terminal) the same pages open over the
story. A new account comes in silently: its tab says 进展·新 until the page is opened.

Everything here is what the players were shown: the hook, the keeper's opening as it was
told, the cards. Never the module's truth or the keeper's notes; a LAN screen, which has no
log, gets the account and the opening through the state sync."""

from __future__ import annotations

import re
from collections.abc import Iterable

from rich.console import RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static, TabbedContent, TabPane

from ...engine.state import GameState
from ...module.schema import Module
from ...rules.coc import Investigator

PAGES = {"history": "经过", "progress": "进展", "background": "背景"}
PLUGINS = "plugins"  # the rule plugins' panels (tui/widgets/plugin_ui.py): a fourth page, only while they have one
FRESH = "进展·新"  # the 进展 tab while a new account waits to be read
DIGEST = ("思想与信念", "重要之人", "意义非凡之地", "宝贵之物")  # why they would go into this: what they hold to
# A space between Chinese and a number ("1922 年") is where Rich breaks a narrow line: the
# number is left alone on its line. The pages run such text together.
GAP = re.compile(r"(?<=[\u3000-\u9fff\uff00-\uffef])[ \t]+(?=[0-9A-Za-z])|(?<=[0-9A-Za-z])[ \t]+(?=[\u3000-\u9fff\uff00-\uffef])")


class Page(VerticalScroll):
    """One page of text that scrolls."""

    def __init__(self, body: RenderableType = "", **kw):
        super().__init__(**kw)
        self.body = Static(body, classes="page-text")

    def compose(self) -> ComposeResult:
        yield self.body

    def show(self, body: RenderableType) -> None:
        self.body.update(body)


def pages(history: Widget, first: str = "history") -> ComposeResult:
    """The three pages as tabs; `history` is the 经过 page (a HistoryPanel at the table)."""
    with TabbedContent(id="pages", initial=f"{first}-page"):
        with TabPane(PAGES["history"], id="history-page"):
            yield history
        with TabPane(PAGES["progress"], id="progress-page"):
            yield Page(id="progress", classes="page")
        with TabPane(PAGES["background"], id="background-page"):
            yield Page(id="background", classes="page")
        with TabPane("更多", id=f"{PLUGINS}-page"):
            yield Page(id="plugin-panels", classes="page")


def panels(tabs: TabbedContent, page: Page, body: RenderableType | None, label: str = "") -> None:
    """The plugins' page: shown under `label` with `body`, or hidden when there is nothing on it."""
    tab = tabs.get_tab(f"{PLUGINS}-page")
    if body is None:
        if tab.display:
            if tabs.active == f"{PLUGINS}-page":
                tabs.active = "history-page"
            tabs.hide_tab(f"{PLUGINS}-page")
        return
    page.show(body)
    tab.label = label
    if not tab.display:
        tabs.show_tab(f"{PLUGINS}-page")


def turn(tabs: TabbedContent, step: int = 1) -> str:
    """The next page (or the one before); its name."""
    names = [*PAGES, *([PLUGINS] if tabs.get_tab(f"{PLUGINS}-page").display else [])]
    now = tabs.active.removesuffix("-page")
    name = names[(names.index(now) + step) % len(names)] if now in names else names[0]
    tabs.active = f"{name}-page"
    return name


# -- what the pages say ------------------------------------------------------------------
def progress_view(st: GameState) -> Text:
    """The story so far and when it was written; the promises not yet kept."""
    p = st.progress
    out = Text()
    if p.text:
        out.append(f"更新于第 {p.turn} 轮 · Ctrl+Y 手动整理\n", style="dim")
        out.append(tight(p.text.strip()))
    else:
        out.append("调查进展随新发现自动更新，Ctrl+Y 可手动整理。", style="dim")
    promises = [f for f in st.world.values() if f.kind == "promise" and f.status == "active"]
    if promises:
        out.append("\n\n待履行的约定\n", style="bold")
        out.append("\n".join(f"· {f.text}" for f in promises))
    return out


def background_view(m: Module, st: GameState, me: Investigator | None) -> Text:
    """What they knew when it began: the module's premise and hook, the opening as the keeper
    told it, and who they are."""
    out = Text()
    out.append(f"《{m.title}》\n", style="bold")
    for line in (m.subtitle, "，".join(x for x in (m.era, m.setting) if x)):
        if line.strip():
            out.append(tight(line.strip()) + "\n", style="dim")
    if m.hook.strip():
        out.append("\n缘起\n", style="bold")
        out.append(paragraphs(m.hook) + "\n")
    if opening := prologue(m, st):
        out.append("\n开场\n", style="bold")
        out.append(paragraphs(opening) + "\n")
    if me is not None:
        out.append(f"\n{me.name}\n", style="bold")
        out.append_text(digest(me))
    others = [i for i in st.investigators if me is None or i.id != me.id]
    if others:
        out.append("\n同伴\n", style="bold")
        out.append("\n".join(f"{i.name} · {i.occupation}" if i.occupation else i.name for i in others) + "\n")
    out.append("\nF4 查看完整人物背景", style="dim")
    return out


def prologue(m: Module, st: GameState) -> str:
    """The opening as it was told; for a save from before that was kept, the first narration
    still in the log, else the module's own opening once play is past it (a LAN screen has
    it blank). Before the keeper has told it, nothing."""
    told = next((e.text for e in st.log if e.kind == "keeper" and e.turn == 0), "")
    return st.prologue or told or (m.opening if st.turn else "")


def digest(inv: Investigator) -> Text:
    """Who they are, in a few lines: what they do and where they live, and what they hold to."""
    out = Text()
    who = [inv.occupation, f"{inv.age} 岁" if inv.age else "", f"住在{inv.residence}" if inv.residence else ""]
    out.append(" · ".join(x for x in who if x) + "\n", style="dim")
    shown = [(k, inv.backstory[k]) for k in DIGEST if inv.backstory.get(k)]
    if not shown and inv.backstory.get("个人描述"):
        shown = [("个人描述", inv.backstory["个人描述"])]
    for k, v in shown:
        out.append(f"{k}：", style="bold")
        out.append(tight(v.strip()) + "\n")
    if not shown:
        out.append("未填写人物背景。\n", style="dim")
    return out


def paragraphs(text: str) -> str:
    """Module text as the page shows it: wrapped lines joined (YAML wraps them), the blank
    line between paragraphs kept."""
    parts = [p for p in text.strip().split("\n\n") if p.strip()]
    return tight("\n".join("".join(line.strip() for line in p.splitlines()) for p in parts))


def tight(text: str) -> str:
    return GAP.sub("", text)


# -- a narrow terminal: the pages over the story ---------------------------------------------
class PagesModal(ModalScreen[None]):
    """The right column's pages, when the terminal is too narrow for the column. 经过 is a copy
    of its lines; the story itself is right behind."""

    BINDINGS = [
        Binding("escape", "dismiss", "关闭"),
        Binding("ctrl+g,tab", "turn_page", "翻页"),
    ]

    def __init__(self, history: Iterable[RenderableType], progress: RenderableType, background: RenderableType, first: str = "progress",
                 extra: tuple[str, RenderableType] | None = None):
        super().__init__()
        self.history, self.first = list(history), first
        self.progress, self.background = progress, background
        self.extra = extra  # the plugins' page: its tab label and what is on it

    def compose(self) -> ComposeResult:
        lines = Text("\n").join(r for r in self.history if isinstance(r, Text)) if self.history else Text("暂无事件记录。", style="dim")
        with Vertical(classes="modal", id="pages-modal"):
            yield from pages(Page(lines, id="history-copy", classes="page"), self.first)
            yield Static("Ctrl+G 或 Tab 翻页 · Esc 关闭", classes="system")

    def on_mount(self) -> None:
        self.query_one("#progress", Page).show(self.progress)
        self.query_one("#background", Page).show(self.background)
        label, body = self.extra or ("", None)
        panels(self.query_one(TabbedContent), self.query_one("#plugin-panels", Page), body, label)

    def action_turn_page(self) -> None:
        turn(self.query_one(TabbedContent))


class PagesHead(Static):
    """The right column's head over 经过: clicked (or Ctrl+G), 经过, 进展 and 背景 open as a page
    over the screen. The column keeps only 经过: tabs under the sheet and the dice were squeezed out
    of sight (the user, 2026-10-21)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.fresh(False)

    def fresh(self, new: bool) -> None:
        """进展·新 while a new account waits to be read."""
        self.update(Text.assemble(("经过", "bold"), "  ", (f"{FRESH if new else PAGES['progress']} · 背景 ▸", "bold" if new else "dim"),
                                  ("  点击展开", "dim")))

    async def on_click(self) -> None:
        await self.screen.run_action("page('progress')")


def mark(tabs: TabbedContent, fresh: bool) -> None:
    """进展·新 on the tab while a new account waits to be read; 进展 once it is."""
    tabs.get_tab("progress-page").label = FRESH if fresh else PAGES["progress"]
