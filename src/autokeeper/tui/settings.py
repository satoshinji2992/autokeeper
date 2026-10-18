"""The pre-game settings hub, available without opening a model session."""

from __future__ import annotations

import os

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static, Switch

from .. import config, imagery
from .prefs import PrefsModal
from .screens import EngineModal


def model_choices() -> dict[str, str]:
    return {"claude": "Claude · 使用本机登录或已配置的 API",
            "codex": "Codex · 使用本机登录，当前默认模型",
            "deepseek": "DeepSeek · 使用已配置的 API"} | {k: "" for k in config.PRESETS if k not in ("claude", "codex", "deepseek")}


class SettingsModal(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "返回")]
    DEFAULT_CSS = """
    #settings-panel { width: 76; max-width: 96%; height: auto; max-height: 90%; }
    #settings-panel Select { margin-bottom: 1; }
    #settings-panel .settings-row { height: 3; align: left middle; }
    #settings-panel .settings-row Static { width: 1fr; }
    #settings-panel .settings-row Switch { width: auto; }
    #settings-models, #settings-prefs {
        width: 100%; height: 1; margin-top: 1; border: none; content-align: left middle; text-align: left;
    }
    """

    def compose(self) -> ComposeResult:
        cfg = config.load()
        with VerticalScroll(id="settings-panel", classes="modal"):
            yield Static("设置", classes="modal-title")
            yield Button("模型设置 >", id="settings-models")
            yield Static(self._model_summary(), id="settings-model-summary")
            yield Button("叙事偏好 >", id="settings-prefs")
            yield Static("自由度", classes="modal-title")
            yield Select([("允许合理即兴，展开日常与支线", "即兴"), ("严格按模组已有内容", "严格")],
                         value=cfg.improv, allow_blank=False, id="settings-improv")
            yield Static("画面风格")
            yield Select([(name, key) for key, name in imagery.STYLE_NAMES.items()], value=self.app.art_style,
                         allow_blank=False, id="settings-art")
            yield Static("配色")
            yield Select([("琥珀", "amber"), ("磷绿", "phosphor"), ("薰衣草", "lavender"), ("血红", "blood")],
                         value=self.app.theme if self.app.theme in ("amber", "phosphor", "lavender", "blood") else "amber",
                         allow_blank=False, id="settings-theme")
            for label, key, value in (("天气与场景动画", "animations", cfg.animations),
                                      ("模组与即兴规则插件", "plugins", cfg.plugins),
                                      ("操作提示", "tips", cfg.tips),
                                      ("即兴人物、场景与物品配图", "pictures", cfg.visual_limit > 0),
                                      ("联网搜索与参考配图", "search", cfg.search != "none")):
                with Horizontal(classes="settings-row"):
                    yield Static(label)
                    yield Switch(value, id=f"settings-{key}")
            with Horizontal(classes="buttons"):
                yield Button("保存", id="settings-save", variant="primary")
                yield Button("返回", id="settings-back")

    def _model_summary(self) -> Text:
        cfg = config.load()
        return Text(f"当前：{cfg.preset} · {cfg.mode} · {cfg.role('keeper').model}", style="dim")

    @on(Button.Pressed, "#settings-models")
    def models(self) -> None:
        cfg, saved = config.load(), config.ui_settings()
        self.app.push_screen(EngineModal(model_choices(), cfg.preset,
                                        os.environ.get("AUTOKEEPER_MODEL", saved.get("model", "")),
                                        os.environ.get("AUTOKEEPER_FALLBACK", saved.get("fallback", "")), cfg.mode,
                                        in_game=False), self._models_saved)

    def _models_saved(self, choice: dict | None) -> None:
        if choice is None:
            return
        config.save_ui_settings(choice)
        for key, env in (("preset", "AUTOKEEPER_PRESET"), ("mode", "AUTOKEEPER_MODE"),
                         ("model", "AUTOKEEPER_MODEL"), ("fallback", "AUTOKEEPER_FALLBACK")):
            if choice.get(key):
                os.environ[env] = choice[key]
            else:
                os.environ.pop(env, None)
        self.query_one("#settings-model-summary", Static).update(self._model_summary())
        self.app.notify("模型设置已保存。")

    @on(Button.Pressed, "#settings-prefs")
    def preferences(self) -> None:
        self.app.push_screen(PrefsModal())

    @on(Button.Pressed, "#settings-save")
    def keep(self) -> None:
        cfg = config.load()
        choice = {key: self.query_one(f"#settings-{key}", Switch).value for key in ("animations", "tips", "plugins")}
        choice |= {"improv": self.query_one("#settings-improv", Select).value,
                   "art_style": self.query_one("#settings-art", Select).value,
                   "theme": self.query_one("#settings-theme", Select).value,
                   "visual_limit": max(1, cfg.visual_limit or 24) if self.query_one("#settings-pictures", Switch).value else 0,
                   "search": (cfg.search if cfg.search != "none" else "auto") if self.query_one("#settings-search", Switch).value else "none"}
        config.save_ui_settings(choice)
        os.environ["AUTOKEEPER_IMPROV"] = choice["improv"]
        os.environ["AUTOKEEPER_SEARCH"] = choice["search"]
        self.app.theme, self.app.art_style = choice["theme"], choice["art_style"]
        self.app.notify("设置已保存。")
        self.dismiss()

    @on(Button.Pressed, "#settings-back")
    def back(self) -> None:
        self.dismiss()
