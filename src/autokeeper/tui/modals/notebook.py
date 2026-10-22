"""F2, the notebook (笔记): the player's own page, written freely, and the papers handed to them
(资料: the module's letters, diaries, maps, telegrams), each opened again as it first was.

Nothing is written into the notes for the player. What was found reached them in the story, in the
narration and in people's own words; what they make of it is theirs to write down (the user,
2026-10-22: 「让玩家自己总结记录线索，系统直接给总结完的线索不太好」).

Where the notes are kept is the screen's business (GameScreen.notes and keep_notes): in the save in
a local game, on the player's own machine at a LAN table. What is typed is kept a moment after the
last key, and again on closing. Someone only watching has no notes: the window is the papers alone."""

from __future__ import annotations

from collections.abc import Callable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import OptionList, Static, TabbedContent, TabPane, TextArea
from textual.widgets.option_list import Option

PLACEHOLDER = "记下听到、看到的事和自己的推测。"
KEEP_AFTER = 1.0  # seconds after the last key before what was typed is kept


class NotebookModal(ModalScreen[None]):
    """`notes`: the player's notes (None: nobody plays here, there are none); `papers`: (id, title)
    of the handouts received; `keep(text)` keeps the notes, `read(id)` opens a paper over the window;
    `notice`: one line under the page on where the notes are kept, when it needs saying."""

    CSS_PATH = "notebook.tcss"
    BINDINGS = [
        Binding("escape", "close", "关闭"),
        Binding("tab", "turn", "翻页", show=False),  # the page has no use for a tab character
    ]

    def __init__(self, notes: str | None, papers: list[tuple[str, str]], *, keep: Callable[[str], None] | None = None,
                 read: Callable[[str], None] | None = None, notice: str = "", first: str = "notes"):
        super().__init__()
        self.notes, self.papers = notes, papers
        self.keep, self.read, self.notice = keep, read, notice
        self.first = "papers" if notes is None else first
        self.text = self.kept = notes or ""
        self._waiting: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="notebook-modal"):
            if self.notes is None:
                yield Static("资料", classes="modal-title")
                yield from self._papers()
            else:
                with TabbedContent(initial=f"{self.first}-tab", id="notebook-tabs"):
                    with TabPane("笔记", id="notes-tab"):
                        yield TextArea(self.notes, id="notes-text", soft_wrap=True, placeholder=PLACEHOLDER)
                        if self.notice:
                            yield Static(self.notice, classes="system")
                    with TabPane(f"资料 · {len(self.papers)} 份", id="papers-tab"):
                        yield from self._papers()
            yield Static("Tab 翻页 · 回车查看资料 · Esc 保存并关闭" if self.notes is not None else "回车查看资料 · Esc 关闭", classes="system")

    def _papers(self) -> ComposeResult:
        if self.papers:
            yield OptionList(*(Option(f"▤ {title}", id=hid) for hid, title in self.papers), id="papers")
        else:
            yield Static("暂无资料。", classes="system")

    def on_mount(self) -> None:
        if self.first == "notes" and self.notes is not None:
            page = self.query_one("#notes-text", TextArea)
            page.move_cursor(page.document.end)  # what is typed goes after what is there
            page.focus()
        else:
            for papers in self.query("#papers").results(OptionList):
                papers.focus()

    @on(TextArea.Changed, "#notes-text")
    def _typed(self, event: TextArea.Changed) -> None:
        self.text = event.text_area.text
        if self._waiting is not None:
            self._waiting.stop()
        self._waiting = self.set_timer(KEEP_AFTER, self._keep)

    def _keep(self) -> None:
        if self.keep is not None and self.notes is not None and self.text != self.kept:
            self.kept = self.text
            self.keep(self.text)

    @on(OptionList.OptionSelected, "#papers")
    def _open(self, event: OptionList.OptionSelected) -> None:
        if self.read is not None and event.option.id:
            self.read(event.option.id)

    def action_turn(self) -> None:
        """Tab: the other page, and the focus on it."""
        if self.notes is None:
            return
        tabs = self.query_one(TabbedContent)
        tabs.active = "papers-tab" if tabs.active == "notes-tab" else "notes-tab"
        for widget in self.query("#notes-text" if tabs.active == "notes-tab" else "#papers"):
            widget.focus()

    def action_close(self) -> None:
        self._keep()
        self.dismiss(None)

    def on_unmount(self) -> None:
        self._keep()  # gone some other way (the game it was over left): what was typed is not lost
