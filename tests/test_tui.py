"""Terminal UI smoke tests (no model calls): title, setup flow, small terminals, pictures."""

from __future__ import annotations

import shutil

import pytest
from PIL import Image

from autokeeper import imagery
from autokeeper.config import EXAMPLE
from autokeeper.tui import app as tui
from autokeeper.tui.app import AutoKeeperApp, SetupScreen, TitleScreen


@pytest.mark.parametrize("size", [(140, 44), (80, 24)])
async def test_title_and_setup_flow(monkeypatch, size):
    monkeypatch.setattr(tui, "modules", lambda: [EXAMPLE])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, TitleScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.step == "investigator"
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.step == "companions"


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
def test_picture_renders_to_requested_size(tmp_path):
    img = tmp_path / "scene.jpg"
    Image.radial_gradient("L").convert("RGB").resize((320, 160)).save(img)
    out = imagery.render(str(img), 40, 10, "picture")
    lines = [line for line in out.split("\n") if line]
    assert 1 <= len(lines) <= 10
    assert "\x1b[" in out
    assert imagery.render(str(img), 40, 10, "ascii").isascii()
