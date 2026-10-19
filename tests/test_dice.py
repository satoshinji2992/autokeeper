"""The dice tray: a physical throw replayed in the art style, the plain dice without chafa (no
model calls). Frames that need chafa skip without it, as on CI."""

from __future__ import annotations

import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw
from rich.text import Text

from autokeeper import config, imagery, paths
from autokeeper.config import Role
from autokeeper.engine.game import Game, _check_view
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules import physdice as pd
from autokeeper.rules.coc import Check, Level
from autokeeper.tui.drawing import dice, diceart

needs_chafa = pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")


def thrown(seed: int = 3, bonus: int = 0) -> dict:
    """A check's dice event, thrown for real."""
    return {"type": "dice", "who": "陈墨", **_check_view(Check("侦查", 60, Level.REGULAR, bonus, pd.PhysDice(seed=seed).check(60, bonus)))}


def test_the_last_frame_lies_as_the_dice_fell_with_the_counting_numbers_in_yellow():
    view = thrown(3)
    r = dice.replay(view)
    assert r is not None and r.gold == [True, True]
    last = r.frames - 1
    dice_now = [(kind, r.centres[last, i], r.turns[last, i]) for i, kind in enumerate(r.kinds)]
    plain, _ = diceart.picture("bone", 37, 10, r.tray, dice_now, view=r.view)
    gold, placed = diceart.picture("bone", 37, 10, r.tray, dice_now, gold=r.gold, view=r.view)
    assert [pd.label(kind, p.top) for kind, p in zip(r.kinds, placed, strict=True)] == view["throw"]["labels"]
    assert int(view["throw"]["labels"][0]) + int(view["throw"]["labels"][1]) == view["value"] % 100  # the top faces are the roll
    yellow = (np.abs(np.asarray(gold).astype(int) - (255, 215, 0)).sum(axis=2) < 60)
    assert yellow.sum() > 40 and not (np.abs(np.asarray(plain).astype(int) - (255, 215, 0)).sum(axis=2) < 60).any()
    for p in placed:  # the yellow is the top faces' own numbers: on those faces
        face = Image.new("1", plain.size)
        ImageDraw.Draw(face).polygon([tuple(c) for c in p.face], fill=1)
        on = np.asarray(face) & yellow
        assert on.sum() > 15
    outside = yellow.copy()
    for p in placed:
        face = Image.new("1", plain.size)
        ImageDraw.Draw(face).polygon([tuple(c) for c in p.face], fill=1)
        outside &= ~np.asarray(face)
    assert outside.sum() <= 0.1 * yellow.sum()  # a little of the dark rim's edge at most


def test_only_the_tens_die_that_counts_turns_yellow():
    view = thrown(9, bonus=1)
    r = dice.replay(view)
    assert r is not None and r.kinds == ["tens", "tens", "units"]
    assert r.gold == [view["kept"] == 0, view["kept"] == 1, True]
    assert dice.replay({"throw": None}) is None and dice.replay({"throw": {"poses": "nonsense"}}) is None


@needs_chafa
@pytest.mark.parametrize("style", ["photo", "ascii"])
def test_every_skin_paints_a_throw_in_the_art_style(monkeypatch, style):
    monkeypatch.setenv("COLORTERM", "truecolor")
    r = dice.replay(thrown(5))
    assert r is not None
    for key in dice.SKINS:
        for k in (0, r.frames // 2, r.frames - 1):
            text = dice.text(dice.frame(r, k, key, 37, 10, list(imagery.STYLES[style])))
            lines = text.plain.split("\n")
            assert len(lines) == 10 and all(len(line) == 37 for line in lines), (key, k)
            if style == "ascii":
                assert text.plain.isascii()
        colours = {span.style.color.triplet for span in text.spans if span.style and span.style.color and span.style.color.triplet}
        assert any(c.red > 190 and c.green > 150 and c.blue < 80 for c in colours), key  # yellow on the top faces (scanlines dim it)


def test_late_frames_are_skipped_and_the_last_always_shown():
    r = dice.replay(thrown(3))
    now = [0.0]

    def render(k: int) -> str:
        now[0] += 0.12  # a slow machine: a frame takes longer than the throw gives it
        return f"frame {k}"

    shown = []
    s = dice.Showing(r, render, clock=lambda: now[0], sleep=lambda t: now.__setitem__(0, now[0] + t))
    s.run(lambda k, text, last: shown.append((k, text, last)))
    assert shown[-1] == (r.frames - 1, f"frame {r.frames - 1}", True) and s.done
    assert 2 < len(shown) < r.frames / 2  # stop motion, not a slow throw
    assert [k for k, _, _ in shown] == sorted(k for k, _, _ in shown)
    rendered = []
    still = dice.Showing(r, lambda k: rendered.append(k) or "", animate=False)
    still.run(lambda *a: None)
    assert rendered == [r.frames - 1]  # animations off: only the dice at rest


@needs_chafa
def test_the_painter_process_sends_a_throw_on_its_own_clock():
    import threading

    r = dice.replay(thrown(3))
    got, done = [], threading.Event()

    def show(k, ansi, last, stats):
        got.append((k, ansi is not None, last, stats))
        if last:
            done.set()

    p = dice.Painter()
    try:
        assert p.throw((r, "brass", 31, 9, list(imagery.STYLES["photo"])), True, show)
        assert done.wait(20)
    finally:
        p.close()
    assert not p.broken and got[-1][0] == r.frames - 1 and got[-1][2] and all(ok for _, ok, _, _ in got)
    stats = got[-1][3]
    assert stats["shown"][-1] == r.frames - 1 and len(stats["paint"]) == len(stats["shown"])


class Canned(hm.Model):
    async def _call(self, messages, *, schema, images, on_delta):
        return Reply(text="前情回顾。", data={} if schema else None)


@pytest.fixture
def game(monkeypatch, tmp_path, skills_dir):

    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "module_folders", lambda: [EXAMPLE])
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    return m, Game.new(m, [lead], slot="dice", human=[lead.id])


async def test_without_chafa_the_tray_shows_the_plain_dice_and_the_number(monkeypatch, game):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.game import GameScreen
    from autokeeper.tui.widgets.panels import DiceTray

    monkeypatch.setattr(imagery, "available", lambda: False)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(GameScreen(*game, resume=True))
        await pilot.pause(0.5)
        view = thrown(3)
        await app.screen.handle(view)
        tray = app.screen.query_one(DiceTray)
        for _ in range(40):
            await pilot.pause(0.1)
            if str(view["value"]) in str(tray.render()):
                break
        shown = str(tray.render())
        tens, units = view["throw"]["labels"]
        assert tray.display and str(view["value"]) in shown and dice.digits(tens)[0] in shown and dice.digits(units)[0] in shown


async def test_cycling_the_skin_keeps_it_and_throws_a_look(monkeypatch, game):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.game import GameScreen
    from autokeeper.tui.widgets.panels import DiceTray

    monkeypatch.setattr(imagery, "available", lambda: False)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(GameScreen(*game, resume=True))
        await pilot.pause(0.5)
        names = list(dice.SKINS)
        start = app.dice_skin
        app.screen.action_dice_skin()
        await pilot.pause(0.2)
        assert app.dice_skin == names[(names.index(start) + 1) % len(names)]
        assert config.ui_settings()["dice"] == app.dice_skin
        tray = app.screen.query_one(DiceTray)
        assert tray.display and tray._view["skill"] == "试掷" and "throw" in tray._view
        for _ in range(len(names) - 1):
            app.screen.action_dice_skin()
        assert app.dice_skin == start  # round the whole set
        await pilot.pause(0.3)
        assert isinstance(tray.render(), Text) or tray.render() is not None
