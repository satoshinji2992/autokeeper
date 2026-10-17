"""Terminal UI smoke tests (no model calls): title, setup flow, small terminals, pictures."""

from __future__ import annotations

import shutil

import pytest
from PIL import Image
from rich.text import Text

from autokeeper import artkit, config, imagery
from autokeeper.config import EXAMPLE, Role
from autokeeper.engine.game import Game
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.tui import app as tui
from autokeeper.tui.app import AutoKeeperApp, GameScreen, LoadScreen, SetupScreen, TitleScreen
from autokeeper.tui.widgets import SceneArt


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


class Canned(hm.Model):
    async def _call(self, messages, *, schema, images, on_delta):
        text = "上回书说到，诸位还在钟楼底下。"
        if on_delta:
            await on_delta(text)
        return Reply(text=text, data={} if schema else None)


async def test_loading_a_save_opens_the_game_at_night(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVES", tmp_path)
    monkeypatch.setattr(tui, "modules", lambda: [EXAMPLE])
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="night", human=[lead.id])
    state.minutes = 23 * 60 + 10
    tui.write_save(state, m)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        await pilot.press("down", "enter")  # 继续调查
        await pilot.pause()
        assert isinstance(app.screen, LoadScreen)
        await pilot.press("enter")  # the newest save is highlighted
        await pilot.pause(0.5)
        assert isinstance(app.screen, GameScreen)
        scene = app.screen.query_one(SceneArt)
        assert scene.when == "深夜" and scene.place


def test_ascii_art_keeps_lamplight_after_dark():
    lit = artkit.colorize("#", "Y", when="深夜")
    dim = artkit.colorize("|", "n", when="深夜")
    assert str(lit.spans[0].style) == artkit.PALETTE["Y"]  # a lit window stays amber
    assert str(dim.spans[0].style) != artkit.PALETTE["n"]  # wood darkens at night


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
def test_scene_picture_fills_the_panel_without_losing_the_subject(tmp_path):
    img = tmp_path / "tall.png"
    Image.new("RGB", (200, 300), (180, 60, 40)).save(img)
    framed = imagery.frame(Image.open(img).convert("RGB"), 80, 15)
    assert abs(framed.width / framed.height - 80 * 0.5 / 15) < 0.05  # the panel's shape
    assert framed.getpixel((framed.width // 2, framed.height // 2)) == (180, 60, 40)  # subject intact
    out = imagery.render(str(img), 80, 15, "picture", "深夜", True)
    assert max(len(Text.from_ansi(line).plain) for line in out.split("\n")) >= 70


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
def test_picture_renders_to_requested_size(tmp_path):
    img = tmp_path / "scene.jpg"
    Image.radial_gradient("L").convert("RGB").resize((320, 160)).save(img)
    out = imagery.render(str(img), 40, 10, "picture")
    lines = [line for line in out.split("\n") if line]
    assert 1 <= len(lines) <= 10
    assert "\x1b[" in out
    assert imagery.render(str(img), 40, 10, "ascii").isascii()
