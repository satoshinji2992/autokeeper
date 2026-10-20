"""Ideas for your next move, on demand: F5 or /suggest (SillyTavern calls it impersonate).

The investigator agent reads the same view the AI companions play from (engine/autoplay.py
situation: only what the players have seen) and offers three things your investigator
could do: look into something, talk to someone, and one bold or careful step. The one you
pick goes into the input box unsent, to send as it is or to change first.

Nothing opens unasked. While it thinks, a line above the input box says so and the box
stays open for typing; if you typed meanwhile, the ideas wait for the next F5 instead of
jumping in front of what you were writing. Ideas belong to the moment they were asked for:
once the game has moved on, F5 thinks again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ... import config

if TYPE_CHECKING:
    from ..screens.game import GameScreen


class IdeasModal(ModalScreen[str | None]):
    """The three ideas, just above the input box. Enter, Space or 1–3 take one; Esc closes."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "关闭"),
        Binding("space", "take_highlighted", show=False),  # a modal does not get the app's Space-confirms binding
        Binding("1", "take(0)", show=False),
        Binding("2", "take(1)", show=False),
        Binding("3", "take(2)", show=False),
    ]
    # Over the story, under the box: the game stays readable while you choose.
    CSS = """
    IdeasModal#ideas {
        align: center bottom;
        background: $background 45%;
    }
    IdeasModal .modal {
        margin-bottom: 4;
        min-width: 50;
        border: round $secondary;
    }
    IdeasModal OptionList {
        width: auto;
        height: auto;
        max-height: 12;
        border: none;
        padding: 0;
        background: $surface;
    }
    IdeasModal OptionList > .option-list--option {
        padding: 0 1;
    }
    IdeasModal .system {
        margin-top: 1;
    }
    """

    def __init__(self, who: str, ideas: list[str]):
        super().__init__(id="ideas")
        self.who, self.ideas = who, ideas

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(f"行动建议 · {self.who}", classes="modal-title")
            yield OptionList(*(Option(Text.assemble((f"{i + 1}  ", "dim"), idea)) for i, idea in enumerate(self.ideas)))
            yield Static("↑↓ 选择 · 回车或空格填入草稿 · Esc 关闭", classes="system")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.highlighted = 0
        ol.focus()

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.action_take(event.option_index)

    def action_take(self, index: int) -> None:
        if 0 <= index < len(self.ideas):
            self.dismiss(self.ideas[index])

    def action_take_highlighted(self) -> None:
        self.action_take(self.query_one(OptionList).highlighted or 0)


class Ideas:
    """F5 for one game screen. It thinks in a worker of its own, so a turn started meanwhile
    is never held up (it cancels the thinking instead), and keeps what it came up with for
    the moment of play it was asked in. The screen supplies the ideas (GameScreen.ideas_for:
    its own cast, or the host's at a shared table)."""

    def __init__(self, screen: GameScreen):
        self.screen = screen
        self.at: tuple = ()  # the moment of play the ideas are for
        self.texts: list[str] = []
        self.draft: str | None = None  # what the input box held when asked; None when not thinking

    def moment(self) -> tuple:
        st = self.screen.state
        return (st.turn, st.minutes, st.location, len(st.clues), len(st.log), self.screen._viewer().id)

    def ask(self) -> None:
        s = self.screen
        if s.spectate:
            s.app.notify("观战模式不提供行动建议。")
        elif s.state.ending:
            s.app.notify("本局已结束。")
        elif s.busy:
            s.app.notify("本回合结束后可生成行动建议。", timeout=3)
        elif self.draft is not None:
            s.app.notify("正在生成行动建议。", timeout=2)
        elif self.texts and self.at == self.moment():
            self.show()
        else:
            s.run_worker(self.think(), group="ideas", exclusive=True, exit_on_error=False)

    async def think(self) -> None:
        s = self.screen
        inv, at, box = s._viewer(), self.moment(), s.query_one("#command", Input)
        line = f"正在为 {inv.name} 生成行动建议…"
        self.draft = box.value
        s._status(line)  # the spinner above the input box
        try:
            texts = await s.ideas_for(inv)
        except Exception as exc:  # a failed call costs nothing but the ideas
            s.app.notify(f"行动建议生成失败：{config.plain(exc)}。可按 F5 重试。", severity="warning")
            return
        finally:
            draft, self.draft = self.draft, None
            if s.status_text == line:
                s._status("")
        if not texts:
            s.app.notify("未生成有效建议，可按 F5 重试。", severity="warning")
        elif self.moment() != at:
            s.app.notify("场景已变化，可按 F5 重新生成建议。")
        else:
            self.at, self.texts = at, texts
            if box.value != draft or s.app.screen is not s:  # typed meanwhile, or reading something else
                s.app.notify("行动建议已生成，按 F5 查看。", timeout=4)
            else:
                self.show()

    def show(self) -> None:
        s = self.screen
        s.app.push_screen(IdeasModal(s._viewer().name, self.texts), self.take)

    def take(self, text: str | None) -> None:
        """The chosen idea goes into the input box, unsent; Esc leaves the box as it was."""
        if not text:
            return
        box = self.screen.query_one("#command", Input)
        box.value = text
        box.cursor_position = len(text)
        box.focus()

    def drop(self) -> None:
        """A turn is starting: ideas still being thought of are for a moment that is passing."""
        self.screen.workers.cancel_group(self.screen, "ideas")
