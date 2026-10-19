"""The Esc menu over the game, and the small dialogs the save and card screens share: a
question with buttons, and a line to type."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

# (id, label) in the order shown; GameScreen.menu_items picks those that apply.
MENU = (
    ("continue", "继续"),
    ("save", "存档"),
    ("load", "读档"),
    ("settings", "设置"),
    ("prefs", "叙事偏好"),
    ("engine", "换模型"),
    ("guide", "新手指南"),
    ("keys", "按键说明"),
    ("title", "回到标题"),
    ("quit", "退出游戏"),
)


class GameMenu(ModalScreen[str | None]):
    """Esc in the game: a short list; Enter or a click picks, Esc goes back to the game."""

    BINDINGS = [Binding("escape", "dismiss(None)", "返回游戏")]

    def __init__(self, items: list[tuple[str, str]], first: str = "continue"):
        super().__init__()
        self.items, self.first = items, first

    def compose(self) -> ComposeResult:
        with Vertical(id="game-menu", classes="modal"):
            yield Static("菜单", classes="modal-title")
            yield OptionList(*(Option(label, id=key) for key, label in self.items), id="game-menu-list")
            yield Static("Enter 选择 · Esc 返回游戏", classes="system")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        keys = [key for key, _ in self.items]
        ol.highlighted = keys.index(self.first) if self.first in keys else 0
        ol.focus()

    @on(OptionList.OptionSelected)
    def pick(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class Confirm(ModalScreen[str | None]):
    """A question and its answers as buttons, the first one ready for Enter; Esc answers None."""

    BINDINGS = [Binding("escape", "dismiss(None)", "取消")]

    def __init__(self, question: str, answers: list[tuple[str, str]], detail: str = ""):
        super().__init__()
        self.question, self.answers, self.detail = question, answers, detail

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm", classes="modal"):
            yield Static(self.question, classes="modal-title")
            if self.detail:
                yield Static(Text(self.detail, style="dim"))
            with Horizontal(classes="buttons"):
                for i, (key, label) in enumerate(self.answers):
                    yield Button(label, id=f"answer-{key}", variant="primary" if i == 0 else "default")

    def on_mount(self) -> None:
        self.query(Button).first().focus()

    @on(Button.Pressed)
    def answer(self, event: Button.Pressed) -> None:
        self.dismiss((event.button.id or "").removeprefix("answer-"))


class AskText(ModalScreen[str | None]):
    """One line to type (a name, a folder). Enter keeps it, Esc leaves things as they were."""

    BINDINGS = [Binding("escape", "dismiss(None)", "取消")]

    def __init__(self, title: str, value: str = "", placeholder: str = ""):
        super().__init__()
        self.title_, self.value, self.placeholder = title, value, placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="ask-text", classes="modal"):
            yield Static(self.title_, classes="modal-title")
            yield Input(self.value, placeholder=self.placeholder, id="ask-input")
            yield Static("Enter 确定 · Esc 取消", classes="system")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def done(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())
