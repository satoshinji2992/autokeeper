"""F6: the keeper tells the latest narration again (engine: Game.retell).

The dice, the clues and what the NPCs said stay as they were; only the keeper's words
change. The player picks how from a short fixed list and never types a wish of their own:
the keeper knows the module's secrets, and a free request is a way to talk them out of it.
"""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...engine.game import RETELL

HINTS = {  # what each choice means, as the list shows it
    "换个讲法": "同样的事，换一种说法",
    "短一点": "缩短篇幅，保留主要信息",
    "更克制": "少形容，不渲染",
    "多写细节": "看到、听到、摸到的东西",
    "更阴森": "加强环境中的不安感",
}


class RetellModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "取消")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="retell-modal"):
            yield Static("重讲上一段", classes="modal-title")
            yield Static(Text("骰子、线索和 NPC 说过的话都不变，守秘人只换个讲法。", style="dim"))
            options = [Option(Text.assemble((name.ljust(5, "　"), "bold"), HINTS.get(name, "")), id=name) for name in RETELL]
            yield OptionList(*options, id="nudges")
            yield Static("Enter 选择 · Esc 取消", classes="system")

    def on_mount(self) -> None:
        nudges = self.query_one(OptionList)
        nudges.highlighted = 0  # Enter at once: same facts, other words
        nudges.focus()

    @on(OptionList.OptionSelected, "#nudges")
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)
