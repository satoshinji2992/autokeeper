"""模型设置: which vendor, the key it needs, how hard it thinks, and a model for any one role.

The user, 2026-10-21: DeepSeek was "an API" and so was the key field under it, nothing said which
role needs a model that can see pictures, and no role could be given a model of its own."""

from __future__ import annotations

import asyncio
import os

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, RadioButton, RadioSet, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from ... import config

MODE_NOTES = {
    "经济": "降低各分工的思考强度",
    "标准": "使用各分工的预设强度",
    "高级剧情": "提高叙事相关分工的思考强度，可能增加等待和用量",
}
EXAMPLES = "deepseek:deepseek-flash、claude-sonnet-5-5、codex:default、openai:gpt-5、ollama:qwen3:14b"


def short(model: str) -> str:
    return model.split(":", 1)[-1] if model.split(":", 1)[0] in ("deepseek", "claude") else model


def who(roles: list[str]) -> str:
    names = [config.ROLES_SHOWN[r][0] for r in roles if r in config.ROLES_SHOWN]
    return "、".join(names) if len(names) <= 4 else f"{'、'.join(names[:3])} 等 {len(names)} 项"


class EngineModal(ModalScreen[dict | None]):
    """Switch models mid-game. Every agent's memory lives in the save, not in the model, so
    the new one carries on where the old one stopped."""

    CSS_PATH = "engine.tcss"
    BINDINGS = [Binding("escape", "dismiss(None)", "取消")]

    def __init__(self, presets: dict[str, str], preset: str, model: str, fallback: str, mode: str = "标准", *,
                 in_game: bool = True, role_models: dict[str, str] | None = None):
        super().__init__()
        self.presets, self.preset, self.model, self.fallback, self.mode = presets, preset, model, fallback, mode
        self.in_game = in_game
        self.role_models = dict(config.ui_settings().get("role_models", {}) if role_models is None else role_models)
        self.typed: dict[str, str] = {}  # keys typed in, kept while the list of keys is redrawn
        self._redrawing = asyncio.Lock()  # one redraw at a time: the old rows gone before the new come

    def compose(self) -> ComposeResult:
        with Vertical(id="engine-modal", classes="modal"):
            yield Static("模型设置", classes="modal-title")
            # Two pages: the vendor, its key and how hard it thinks; and each role's own model, which
            # under the first page's fold went unfound (the user, 2026-10-21).
            with TabbedContent(id="engine-tabs"):
                with TabPane("模型方案", id="engine-plan"), VerticalScroll(classes="engine-page"):
                    yield from self._plan()
                with TabPane("各分工模型", id="engine-roles"), VerticalScroll(classes="engine-page"):
                    yield from self._roles()
            yield Static("", id="ping-result")
            with Horizontal(classes="buttons"):
                yield Button("应用", id="apply", variant="primary")
                yield Button("测试连接", id="ping")
                yield Button("安装 Claude 组件", id="install")
                yield Button("取消", id="cancel")

    def _plan(self) -> ComposeResult:
        yield Static(Text("切换模型后保留本局记录。" if self.in_game else "选择模型方案；各分工的模型在「各分工模型」页单独指定。",
                          style="dim"))
        yield Static("模型方案", classes="engine-head")
        options = [Option(Text.assemble((f"{name:<9}", "bold"), desc), id=name) for name, desc in self.presets.items()]
        yield OptionList(*options, id="preset")
        yield Static("API Key", classes="engine-head")
        yield Vertical(id="keys")
        yield Static("思考强度", classes="engine-head")
        with RadioSet(id="mode"):
            for name, note in MODE_NOTES.items():
                yield RadioButton(Text.assemble((name.ljust(4, "　"), "bold"), "  ", note), value=name == self.mode, name=name)

    def _roles(self) -> ComposeResult:
        yield Static(Text(f"留空使用方案默认模型（灰色）。示例：{EXAMPLES}。「读图」需选择支持图像输入的模型。",
                          style="dim"), classes="engine-note")
        with Horizontal(classes="role-row"):
            yield Static(Text.assemble(("全部分工", "bold"), ("  单项设置优先", "dim")), classes="role-name")
            yield Input(self.model, placeholder="未指定", id="model", classes="role-model")
        for name, (label, what) in config.ROLES_SHOWN.items():
            with Horizontal(classes="role-row"):
                yield Static(Text.assemble((label, "bold"), (f"  {what}", "dim")), classes="role-name")
                yield Input(self.role_models.get(name, ""), id=f"role-{name}", classes="role-model")
        with Horizontal(classes="role-row"):
            yield Static(Text.assemble(("备用模型", "bold"), ("  主模型出错或超出预算时使用", "dim")), classes="role-name")
            yield Input(self.fallback, placeholder="未指定", id="fallback", classes="role-model")

    async def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.highlighted = list(self.presets).index(self.preset) if self.preset in self.presets else 0
        self.query_one("#install", Button).display = False
        await self._refresh()

    # -- what is chosen -------------------------------------------------------------------------
    def _chosen(self) -> str:
        ol = self.query_one(OptionList)
        return ol.get_option_at_index(ol.highlighted or 0).id or self.preset

    def _mode(self) -> str:
        pressed = self.query_one(RadioSet).pressed_button
        return pressed.name if pressed is not None and pressed.name else self.mode

    def _choice(self) -> dict:
        roles = {name: v for name in config.ROLES_SHOWN if (v := self.query_one(f"#role-{name}", Input).value.strip())}
        return {"preset": self._chosen(), "mode": self._mode(), "model": self.query_one("#model", Input).value.strip(),
                "fallback": self.query_one("#fallback", Input).value.strip(), "role_models": roles}

    def _config(self, choice: dict | None = None) -> config.Config | None:
        try:
            return config.load(choice=choice or self._choice())
        except ValueError:
            return None

    @on(OptionList.OptionHighlighted, "#preset")
    @on(RadioSet.Changed)
    async def _preset_changed(self) -> None:
        await self._refresh()

    @on(Input.Changed, ".role-model")
    async def _model_changed(self) -> None:
        await self._keys()

    async def _refresh(self) -> None:
        """The roles' default models (the preset's, before anything typed) and the keys they need."""
        bare = self._config(self._choice() | {"model": "", "role_models": {}})
        if bare is not None:
            for name in config.ROLES_SHOWN:
                self.query_one(f"#role-{name}", Input).placeholder = short(bare.role(name).model)
        await self._keys()

    async def _keys(self) -> None:
        async with self._redrawing:
            await self._redraw_keys()

    async def _redraw_keys(self) -> None:
        box = self.query_one("#keys", Vertical)
        for field in box.query(Input):
            self.typed[field.name or ""] = field.value
        await box.remove_children()
        cfg = self._config()
        needed = config.keys_needed(cfg) if cfg else []
        rows = []
        for name, whose, optional in needed:
            note = "可选，留空使用本机 Claude Code 登录" if optional else ("已保存，留空保持不变" if os.environ.get(name) else "未填写")
            rows.append(Horizontal(Static(Text.assemble((f"{whose} API Key", "bold"), (f"  {name}", "dim")), classes="role-name"),
                                   Input(self.typed.get(name, ""), placeholder=note, password=True, name=name, classes="key"),
                                   classes="role-row"))
        if not needed:
            rows.append(Static(Text("当前模型无需 API Key。", style="dim")))
        await box.mount_all(rows)

    def _save_keys(self) -> None:
        """Keys typed in: kept where every later start finds them (config.save_key)."""
        for field in self.query_one("#keys", Vertical).query(Input):
            self.typed[field.name or ""] = field.value
        for name, value in self.typed.items():
            if name and value.strip():
                config.save_key(name, value.strip())

    # -- testing --------------------------------------------------------------------------------
    @on(Button.Pressed, "#ping")
    def test(self) -> None:
        self._save_keys()
        self._ping(self._choice())

    @work(exclusive=True, group="ping")
    async def _ping(self, choice: dict) -> None:
        """Every model the roles run on, tried at once, each line filled in as it answers: in a
        worker, so the page stays usable while Codex (some seconds a call) is asked."""
        result = self.query_one("#ping-result", Static)
        cfg = self._config(choice)
        if cfg is None:
            result.update(Text("✗ 模型配置无效，请检查分工中的模型名称。", style="red"))
            return
        models = config.models_in_use(cfg)
        lines = {key: Text(f"… 正在测试 {short(key[1])}（{who(roles)}）", style="dim") for key, roles in models.items()}

        def show() -> None:
            result.update(Text("\n").join(lines.values()))

        missing = not config.claude_ready()
        self.query_one("#install", Button).display = missing and any(b == "claude" for b, _ in models)

        async def one(key: tuple[str, str], roles: list[str]) -> None:
            if key[0] == "claude" and missing:
                problem = "未安装 Claude 组件" if config.claude_install_command() else config.CLAUDE_EXTRA
            else:
                problem = await config.ping_role(cfg.role(roles[0]))
            label = f"{short(key[1])}（{who(roles)}）"
            lines[key] = Text(f"✓ {label} 连接正常", style="green") if not problem else Text(f"✗ {label}：{problem}", style="red")
            show()

        show()
        await asyncio.gather(*(one(key, roles) for key, roles in models.items()))

    @on(Button.Pressed, "#install")
    def install(self) -> None:
        self._install()

    @work(exclusive=True, group="ping")
    async def _install(self) -> None:
        result, button = self.query_one("#ping-result", Static), self.query_one("#install", Button)
        button.disabled = True
        result.update(Text("正在安装 Claude 组件（约 250 MB）…", style="dim"))
        problem = await config.install_claude()
        button.disabled = False
        if problem:
            result.update(Text(f"✗ {problem}", style="red"))
            return
        button.display = False
        result.update(Text("✓ Claude 组件已安装。", style="green"))

    # -- leaving --------------------------------------------------------------------------------
    @on(Button.Pressed, "#apply, #cancel")
    def choose(self, event: Button.Pressed) -> None:
        if self.app.screen is not self:  # pressed again while closing: the screens under it are not this one's to close
            return
        if event.button.id != "apply":
            self.dismiss(None)
            return
        self._save_keys()
        self.dismiss(self._choice())
