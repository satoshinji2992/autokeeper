"""Player-facing extension controls without exposing keeper state or source code."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..engine.state import GameState
from ..module.schema import Module


class PluginsModal(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "返回")]

    def __init__(self, module: Module, state: GameState):
        super().__init__()
        self.module, self.state = module, state

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal"):
            yield Static("规则插件", classes="modal-title")
            yield Static(Text("管理本局的额外规则。暂停后会保留已有状态。", style="dim"))
            if not self.module.plugins:
                yield Static("本局没有自定义插件。")
            for i, identity in enumerate(self.module.plugins):
                plugin = self.module.extensions.get(identity)
                yield Static(Text(f"{plugin.name if plugin else identity} · {plugin.version if plugin else '未加载'}"))
                with Horizontal(classes="buttons"):
                    yield Button(self._label(identity), id=f"plugin-{i}", disabled=plugin is None)
            yield Button("返回", id="plugin-back")

    def _label(self, identity: str) -> str:
        return "启用 / 重试" if identity in self.state.plugin_errors else "暂停"

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        if event.button.id == "plugin-back":
            self.dismiss()
            return
        identity = self.module.plugins[int(event.button.id.removeprefix("plugin-"))]
        if identity in self.state.plugin_errors:
            del self.state.plugin_errors[identity]
        else:
            self.state.plugin_errors[identity] = "已手动暂停"
        event.button.label = self._label(identity)
