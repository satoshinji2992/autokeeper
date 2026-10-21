"""A keeper card dealt in play (cards.py): a phobia, a mania, a bout of madness or a mishap. Its
words as the card has them; 查看卡面 opens the card's picture."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

ACCENT = {"madness": "#7fbf7f", "mishap": "#d46a6a", "weapon": "#7f9fd4", "npc": "#d4a76a"}


def card_text(card: dict) -> Text:
    """The body of a dealt card (cards.view): its English name, quote and paragraphs."""
    body = Text()
    if card.get("english"):
        body.append(f"{card['english']}\n", style="italic")
    if card.get("quote"):
        body.append(f"{card['quote']}\n", style="dim")
    body.append("\n")
    for para in (card.get("text") or "").split("\n"):
        body.append(f"{para}\n")
    return body


class DeckCardModal(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "关闭"), ("enter", "dismiss", "关闭")]

    def __init__(self, card: dict):
        super().__init__()
        self.card = card

    def compose(self) -> ComposeResult:
        c = self.card
        title = Text()
        title.append(f"{c.get('kind', '')}〔{c.get('name', '')}〕", style=f"bold {ACCENT.get(c.get('deck', ''), '')}")
        if c.get("who"):
            title.append(f" · {c['who']}")
        with Vertical(classes="modal", id="deck-card"):
            yield Static(title, classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(card_text(c))
            with Horizontal(classes="buttons"):
                if c.get("image"):
                    yield Button("查看卡面", id="deck-face")
                yield Button("收起", id="deck-close", variant="primary")

    @on(Button.Pressed, "#deck-close")
    def close(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#deck-face")
    def face(self) -> None:
        path = Path(self.card.get("image", "")).resolve()
        if not path.is_file():
            self.app.notify("卡面图片不可用，请重新导入卡组。", severity="warning")
        elif self.app.is_web:
            self.app.deliver_binary(path, open_method="browser")
        else:
            self.app.open_url(path.as_uri())
