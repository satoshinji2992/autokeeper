"""All pre-game preferences can be reached and survive a fresh application."""

from __future__ import annotations

import pytest
from textual.widgets import Button, OptionList, Select, Switch

from autokeeper import config
from autokeeper.prefs import Prefs
from autokeeper.tui.app import AutoKeeperApp, SetupScreen, TitleScreen
from autokeeper.tui.prefs import PrefsModal
from autokeeper.tui.screens import EngineModal
from autokeeper.tui.settings import SettingsModal


@pytest.fixture(autouse=True)
def settings_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
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
