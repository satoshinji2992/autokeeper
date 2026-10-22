"""A choice a fight stops for (engine/encounter): how the investigator answers a blow, before any of
its dice, or what they do instead when what they declared can no longer be done.

Each way of answering is a button with what it rolls and costs; the player may also write their own
answer (the keeper reads it). 「每次被攻击都询问」 is their own setting (设置 has it too): off, the game
asks the first time, when the answer they set does not fit, and when the danger rises a level
(engine/encounter/choices.should_ask). At a LAN table the host waits only so long: the countdown and
the closing are dialogs.Timed's, and at its end the host takes what they set.
"""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Static

from ... import config
from .dialogs import Timed


class ChoiceModal(Timed, ModalScreen[str | dict | None]):
    """The options as buttons; Escape takes the default (what they set). The answer is the option's id,
    or {"choice", "text", "ask"} when they wrote their own or changed the setting; None when the time
    ran out or the host settled it (nothing to send: dialogs.Timed)."""

    BINDINGS = [Binding("escape", "default", "按原定")]
    CSS_PATH = "reaction.tcss"
    CLOCK = "剩余 {left} 秒，超时按原定"

    def __init__(self, offer: dict):
        super().__init__()
        self.offer = offer
        self.reaction = offer.get("kind") == "reaction"
        self.asked_always = offer.get("ask") == "always"
        self.timed(offer)

    def compose(self) -> ComposeResult:
        o = self.offer
        title = "应对攻击" if self.reaction else "改选行动"
        with Vertical(classes="modal choice-modal"):
            yield Static(f"{title} · {o.get('name') or ''}", classes="modal-title")
            yield Static(o.get("prompt", ""), classes="modal-body")
            if self.left is not None:
                yield Static("", id="choice-clock", classes="choice-clock timed-clock")
            for i, option in enumerate(o.get("options", [])):
                default = option["id"] == o.get("default")
                with Vertical(classes="choice-option"):
                    yield Button(option["label"] + ("（原定）" if default else ""), id=f"o{i}", variant="primary" if default else "default")
                    if option.get("note"):
                        yield Static(Text(option["note"]), classes="choice-note")
            if o.get("free_text", True):
                yield Input(placeholder="或写下你的应对，回车提交", max_length=120, id="choice-own")
            if self.reaction:
                yield Checkbox("每次被攻击都询问", self.asked_always, id="choice-ask")
            with Horizontal(classes="buttons"):
                yield Button("按原定", id="choice-default")

    def on_mount(self) -> None:
        self.start_clock()

    def _ask(self) -> str:
        """The setting as the player leaves it: "" when unchanged (it is kept in 设置 too)."""
        if not self.reaction:
            return ""
        on = self.query_one("#choice-ask", Checkbox).value
        if on == self.asked_always:
            return ""
        config.save_ui_settings({"ask_every_blow": on})
        return "always" if on else "major"

    def _done(self, choice: str, text: str = "") -> None:
        if self.answered:
            return
        ask = self._ask()
        self._close({"choice": choice, "text": text, "ask": ask} if text or ask else choice)

    def action_default(self) -> None:
        self._done(self.offer.get("default", ""))

    @on(Button.Pressed)
    def chosen(self, event: Button.Pressed) -> None:
        if event.button.id == "choice-default":
            self._done(self.offer.get("default", ""))
            return
        index = int((event.button.id or "o0")[1:])
        self._done(self.offer["options"][index]["id"])

    @on(Input.Submitted, "#choice-own")
    def own(self, event: Input.Submitted) -> None:
        text = " ".join(event.value.split())
        if text:
            self._done("", text)


__all__ = ["ChoiceModal"]
