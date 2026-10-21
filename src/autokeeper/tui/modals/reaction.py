"""A choice a fight stops for (engine/encounter): how the investigator answers a blow, before any of
its dice, or what they do instead when what they declared can no longer be done."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ChoiceModal(ModalScreen[str]):
    """The options as buttons; Escape takes the default (what they set for the round)."""

    BINDINGS = [Binding("escape", "default", "按原定")]

    def __init__(self, offer: dict):
        super().__init__()
        self.offer = offer

    def compose(self) -> ComposeResult:
        o = self.offer
        title = "怎么应对" if o.get("kind") == "reaction" else "改做什么"
        with Vertical(classes="modal"):
            yield Static(f"{title} · {o.get('name') or ''}", classes="modal-title")
            yield Static(o.get("prompt", ""), classes="modal-body")
            with Horizontal(classes="buttons"):
                for i, option in enumerate(o.get("options", [])):
                    yield Button(option["label"], id=f"o{i}", variant="primary" if option["id"] == o.get("default") else "default")

    def action_default(self) -> None:
        self.dismiss(self.offer.get("default", ""))

    @on(Button.Pressed)
    def chosen(self, event: Button.Pressed) -> None:
        index = int((event.button.id or "o0")[1:])
        self.dismiss(self.offer["options"][index]["id"])


__all__ = ["ChoiceModal"]
