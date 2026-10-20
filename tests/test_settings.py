"""All pre-game preferences can be reached and survive a fresh application."""

from __future__ import annotations

import pytest
from textual.widgets import Button, Input, OptionList, Select, Static, Switch

from autokeeper import config, paths
from autokeeper.prefs import Prefs
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.dialogs import EngineModal
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
    模型设置 takes the chosen preset's key (kept as `autokeeper login` keeps it) and tries it."""
    from autokeeper import config

    saved, pinged = {}, []

    async def ping(preset):
        pinged.append(preset)
        return "" if preset == "deepseek" else "RuntimeError: 用 Claude 要先装上它的组件"

    monkeypatch.setattr(config, "save_key", lambda name, value: saved.setdefault(name, value))
    monkeypatch.setattr(config, "ping", ping)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 44)) as pilot:
        app.push_screen(EngineModal({"deepseek": "DeepSeek", "claude": "Claude"}, "claude", "", "", in_game=False))
        await pilot.pause()
        modal = app.screen
        modal.query_one("#preset", OptionList).highlighted = 0  # deepseek
        modal.query_one("#key", Input).value = "sk-test"
        modal.query_one("#ping", Button).press()
        await pilot.pause(0.3)
        assert saved == {"DEEPSEEK_API_KEY": "sk-test"} and pinged == ["deepseek"]
        assert "连通了" in str(modal.query_one("#ping-result", Static).render())
        modal.query_one("#preset", OptionList).highlighted = 1  # claude, without its package
        modal.query_one("#ping", Button).press()
        await pilot.pause(0.3)
        assert "没连通" in str(modal.query_one("#ping-result", Static).render())


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
