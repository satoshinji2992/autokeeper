"""A choice a fight stops for (engine/encounter): how the investigator answers a blow, before any of
its dice, or what they do instead when what they declared can no longer be done.

Each way of answering is a button with what it rolls and costs; the player may also write their own
answer (the keeper reads it). 「每次被攻击都询问」 is their own setting (设置 has it too): off, the game
asks only the first time and when a blow could put them down. At a LAN table the answer has a
deadline: the countdown runs on the dialog, and at its end what they set for the round stands.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Static

from ... import config


class ChoiceModal(ModalScreen[str | dict]):
    """The options as buttons; Escape takes the default (what they set for the round). The answer is
    the option's id, or {"choice", "text", "ask"} when they wrote their own or changed the setting."""

    BINDINGS = [Binding("escape", "default", "按原定")]
    CSS_PATH = "reaction.tcss"

    def __init__(self, offer: dict):
        super().__init__()
        self.offer = offer
        self.reaction = offer.get("kind") == "reaction"
        self.asked_always = offer.get("ask") == "always"
        self.answered = False

    def compose(self) -> ComposeResult:
        o = self.offer
        title = "应对攻击" if self.reaction else "改做什么"
        with Vertical(classes="modal choice-modal"):
            yield Static(f"{title} · {o.get('name') or ''}", classes="modal-title")
            yield Static(o.get("prompt", ""), classes="modal-body")
            if o.get("deadline"):
                yield Static("", id="choice-clock", classes="choice-clock")
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
        if self.offer.get("deadline"):
            self._tick()
            self.set_interval(0.5, self._tick)

    def _tick(self) -> None:
        left = float(self.offer.get("deadline") or 0) - time.time()
        if left <= 0:
            self.action_default()
            return
        for clock in self.query("#choice-clock").results(Static):
            clock.update(f"剩余 {int(left + 0.99)} 秒，超时按原定")

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
        self.answered = True
        ask = self._ask()
        self.dismiss({"choice": choice, "text": text, "ask": ask} if text or ask else choice)

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
