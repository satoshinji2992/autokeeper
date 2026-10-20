"""All pre-game preferences can be reached and survive a fresh application."""

from __future__ import annotations

import pytest
from textual.widgets import Button, Input, OptionList, Select, Static, Switch

from autokeeper import config, paths
from autokeeper.prefs import Prefs
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.engine import EngineModal
from autokeeper.tui.modals.prefs import PrefsModal
from autokeeper.tui.modals.settings import SettingsModal
from autokeeper.tui.screens.setup import SetupScreen
from autokeeper.tui.screens.title import TitleScreen


@pytest.fixture(autouse=True)
def settings_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    for key in ("PRESET", "MODEL", "FALLBACK", "MODE", "IMPROV", "SEARCH"):
        monkeypatch.delenv(f"AUTOKEEPER_{key}", raising=False)


@pytest.mark.parametrize("size", [(140, 44), (80, 24)])
async def test_start_menu_has_settings_and_nested_pages_do_not_start_a_game(size):
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=size) as pilot:
        title = app.screen
        assert isinstance(title, TitleScreen)
        menu = title.query_one("#menu", OptionList)
        menu.highlighted = menu.get_option_index("settings")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SettingsModal)
        app.screen.query_one("#settings-models", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, EngineModal)
        assert app.screen.query_one("#preset", OptionList).get_option("codex")
        await pilot.press("escape")
        await pilot.pause()
        app.screen.query_one("#settings-prefs", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, PrefsModal)
        await pilot.press("escape", "escape")
        await pilot.pause()
        assert app.screen is title
        await pilot.press("f9")
        await pilot.pause()
        assert isinstance(app.screen, SettingsModal)


async def test_saved_settings_apply_after_restarting_without_environment_overrides(monkeypatch):
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press("f9")
        await pilot.pause()
        settings = app.screen
        settings.query_one("#settings-improv", Select).value = "严格"
        settings.query_one("#settings-art", Select).value = "ascii"
        settings.query_one("#settings-theme", Select).value = "phosphor"
        settings.query_one("#settings-animations", Switch).value = False
        settings.query_one("#settings-tips", Switch).value = False
        settings.query_one("#settings-pictures", Switch).value = False
        settings.query_one("#settings-search", Switch).value = False
        settings.query_one("#settings-plugins", Switch).value = False
        settings.query_one("#settings-save", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, TitleScreen)
        assert app.theme == "phosphor" and app.art_style == "ascii"
    monkeypatch.delenv("AUTOKEEPER_IMPROV", raising=False)
    monkeypatch.delenv("AUTOKEEPER_SEARCH", raising=False)
    cfg = config.load()
    assert cfg.improv == "严格" and cfg.search == "none" and cfg.visual_limit == 0
    assert not cfg.animations and not cfg.tips and not cfg.plugins
    again = AutoKeeperApp()
    async with again.run_test() as pilot:
        assert again.theme == "phosphor" and again.art_style == "ascii"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(again.screen, SetupScreen)


async def test_model_choice_persists_and_narrative_preferences_stay_available(monkeypatch):
    app = AutoKeeperApp(theme="amber")
    async with app.run_test() as pilot:
        await pilot.press("f9")
        await pilot.pause()
        settings = app.screen
        settings.query_one("#settings-models", Button).press()
        for _ in range(100):  # the dialog highlights the current preset as it mounts: choose only after that
            if app.screen is not settings and app.screen.is_mounted:
                break
            await pilot.pause(0.05)
        models = app.screen
        presets = models.query_one("#preset", OptionList)
        presets.highlighted = presets.get_option_index("codex")
        await pilot.pause()
        assert presets.get_option_at_index(presets.highlighted or 0).id == "codex"
        models.query_one("#apply", Button).press()
        for _ in range(100):  # the choice is saved in the dismiss callback: a slow runner takes more than a frame
            if app.screen is settings and config.load().role("keeper").backend == "codex":
                break
            await pilot.pause(0.05)
        assert app.screen is settings
        assert config.load().role("keeper").backend == "codex"
    monkeypatch.delenv("AUTOKEEPER_PRESET", raising=False)
    monkeypatch.delenv("AUTOKEEPER_MODE", raising=False)
    assert config.load().preset == "codex" and config.load().role("npc").model == "default"
    assert Prefs.load() == Prefs()


async def test_pictures_from_codex_are_off_with_the_reason_when_codex_cannot_draw(monkeypatch):
    from textual.widgets import Static

    from autokeeper import imagegen

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press("f9")
        await pilot.pause()
        switch = app.screen.query_one("#settings-imagegen", Switch)
        assert switch.disabled and not switch.value
        assert str(app.screen.query_one("#settings-imagegen-why", Static).render()) == "测试里不生图"  # one plain line
        app.screen.query_one("#settings-save", Button).press()
        await pilot.pause()
    assert config.load().imagegen  # the choice stays for when Codex is back
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    again = AutoKeeperApp(theme="amber")
    async with again.run_test(size=(100, 36)) as pilot:
        await pilot.press("f9")
        await pilot.pause()
        switch = again.screen.query_one("#settings-imagegen", Switch)
        assert not switch.disabled and switch.value  # on by default where Codex can draw
        assert not again.screen.query("#settings-imagegen-why")  # nothing to explain when it works
        switch.value = False
        again.screen.query_one("#settings-save", Button).press()
        await pilot.pause()
    assert not config.load().imagegen


async def test_the_model_settings_take_a_key_and_test_the_connection(monkeypatch):
    """The user, 2026-10-20: where in the game does the API key go, and how do I know it works?
    2026-10-21: the key field said only "API Key" under DeepSeek's "API", nothing said which role
    needs a model that sees pictures, no role could have a model of its own, and a test of Codex
    froze the page. The keys listed are the ones the chosen models need, each role can be given a
    model, and every model in use is tried in the background."""
    import asyncio

    saved, pinged = {}, []

    async def ping_role(role):
        pinged.append(role.model)
        await asyncio.sleep(0.05)
        return "" if role.model.startswith("deepseek:") else "没有回应"

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(config, "save_key", lambda name, value: saved.setdefault(name, value))
    monkeypatch.setattr(config, "ping_role", ping_role)
    monkeypatch.setattr(config, "claude_ready", lambda: False)
    monkeypatch.setattr(config, "claude_install_command", lambda: ["uv", "tool", "install"])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 50)) as pilot:
        result: list = []
        app.push_screen(EngineModal({"deepseek": "DeepSeek", "claude": "Claude"}, "claude", "", "", in_game=False, role_models={}),
                        result.append)
        await pilot.pause()
        modal = app.screen
        assert str(modal.query_one("#engine-tabs").get_tab("engine-roles").label) == "各分工模型"  # a page of its own
        modal.query_one("#preset", OptionList).highlighted = 0  # deepseek
        await pilot.pause()
        keys = list(modal.query("#keys Input"))
        assert [k.name for k in keys] == ["DEEPSEEK_API_KEY"] and "未填写" in keys[0].placeholder
        assert modal.query_one("#role-artist", Input).placeholder == "deepseek-v4-pro"  # the preset's own, in grey
        keys[0].value = "sk-test"
        modal.query_one("#ping", Button).press()
        await pilot.pause(0.3)
        assert saved == {"DEEPSEEK_API_KEY": "sk-test"}
        assert sorted(pinged) == ["deepseek:deepseek-flash", "deepseek:deepseek-v4-pro"]  # each model once
        text = str(modal.query_one("#ping-result", Static).render())
        assert "deepseek-flash" in text and "连接正常" in text
        modal.query_one("#role-vision", Input).value = "claude-haiku-5-5"  # 读图 on a model that sees
        await pilot.pause()
        assert [k.name for k in modal.query("#keys Input")] == ["DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"]
        modal.query_one("#ping", Button).press()
        await pilot.pause(0.3)
        assert "未安装 Claude 组件" in str(modal.query_one("#ping-result", Static).render())
        assert modal.query_one("#install", Button).display  # one press installs it
        modal.query_one("#apply", Button).press()
        await pilot.pause()
    assert result[0]["preset"] == "deepseek" and result[0]["role_models"] == {"vision": "claude-haiku-5-5"}


def test_a_role_given_its_own_model_keeps_it_and_tests_say_what_is_wrong_in_plain_words(monkeypatch, tmp_path):
    import asyncio

    monkeypatch.setattr(paths, "USER_DIR", tmp_path)
    config.save_ui_settings({"preset": "deepseek", "role_models": {"vision": "openai:gpt-5", "nobody": "x"}})
    cfg = config.load()
    assert cfg.role("vision").model == "openai:gpt-5" and cfg.role("keeper").model == "deepseek:deepseek-flash"
    assert config.load(choice={"preset": "claude", "role_models": {}}).role("vision").model == "claude-opus-5-5"
    assert ("OPENAI_API_KEY", "OpenAI", False) in config.keys_needed(cfg)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(config, "load_env", lambda: None)
    # no key: the model fails as it is made, which used to take the game down with it
    assert asyncio.run(config.ping_role(config.Role("langchain", "deepseek:deepseek-flash", None))) == "缺少 DeepSeek API Key"
    assert config.plain(ModuleNotFoundError("No module named 'claude_agent_sdk'")) == "未安装 Claude 组件"


def test_a_player_without_a_usable_model_is_asked_even_with_old_saves(monkeypatch):
    """Saves copied over from a checkout made an installed game skip the first-run question, and the
    default Claude it had no package for failed (ModuleNotFoundError). Asked until one usable is chosen."""
    from autokeeper import cli, config, paths

    for name in ("AUTOKEEPER_PRESET", "AUTOKEEPER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    paths.games().mkdir(parents=True, exist_ok=True)
    paths.save_index().write_text("{}", encoding="utf-8")  # games came over
    assert cli._new_player()
    config.save_ui_settings({"preset": "claude", "model": ""})
    monkeypatch.setattr(config, "claude_ready", lambda: False)
    assert cli._new_player()  # Claude chosen, its package missing
    config.save_ui_settings({"preset": "deepseek", "model": ""})
    assert not cli._new_player()
