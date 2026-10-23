"""A separate composer for private table correspondence; ordinary input stays public."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static


class PrivateMessageModal(ModalScreen[tuple[str, str] | None]):
    CSS_PATH = "private.tcss"
    BINDINGS = [Binding("escape", "dismiss(None)", "取消")]

    def __init__(self, recipients: list[tuple[str, str]]):
        super().__init__()
        self.recipients = recipients

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="private-message"):
            yield Static("私信", classes="modal-title")
            yield Select([("守秘人", "kp"), *self.recipients], value="kp", allow_blank=False, id="private-recipient")
            yield Static(Text("仅收件人与守秘人可见。私信不占行动名额，不直接结算秘密行动。", style="dim"))
            yield Input(placeholder="私信正文", max_length=500, id="private-body")
            with Horizontal(classes="buttons"):
                yield Button("发送", id="private-send", variant="primary")
                yield Button("取消", id="private-cancel")

    def on_mount(self) -> None:
        self.query_one("#private-body", Input).focus()

    @on(Input.Submitted, "#private-body")
    def submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.send()

    @on(Button.Pressed, "#private-send")
    def send(self) -> None:
        body = self.query_one("#private-body", Input).value.strip()
        if not body:
            self.app.notify("请输入私信正文。", severity="warning")
            return
        target = self.query_one("#private-recipient", Select).value
        if isinstance(target, str):
            self.dismiss((target, body))

    @on(Button.Pressed, "#private-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
