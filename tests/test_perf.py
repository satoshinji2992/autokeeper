"""Smoothness: the work the game screen does per frame and per event stays bounded, and the
layout holds still while the scene loads (no model calls)."""

from __future__ import annotations

import threading

import pytest
from rich.text import Text

from autokeeper.config import EXAMPLE
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.tui.app import AutoKeeperApp, GameScreen
from autokeeper.tui.widgets import SceneArt

from .test_speaker import drawn
from .test_tui import canned, until  # noqa: F401  (canned: a fixture, used by name)


def square() -> tuple[Module, Game]:
    m = Module.load(EXAMPLE)  # starts on the square: hand-drawn, nine rows, in fog
    lead = m.pregens[0].model_copy(deep=True)
    return m, Game.new(m, [lead], slot="perf", human=[lead.id])


@pytest.mark.usefixtures("canned")
async def test_a_scene_is_drawn_once_at_its_real_width_and_scans_in_at_full_height(monkeypatch):
    loads, heights = [], []
    real_load, real_update = SceneArt._load, SceneArt.update

    async def load(self, reveal):
        loads.append((reveal, self._fit()))
        await real_load(self, reveal)

    def update(self, content="", *, layout=True):
        if isinstance(content, Text):
            heights.append(content.plain.count("\n") + 1)
        real_update(self, content, layout=layout)

    monkeypatch.setattr(SceneArt, "_load", load)
    monkeypatch.setattr(SceneArt, "update", update)
    m, state = square()
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)  # the recap after it shows the same scene again
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        scene = screen.query_one(SceneArt)
        assert await until(pilot, lambda: drawn(scene) and scene._atmo is not None)
        await pilot.pause(0.5)
        assert loads == [(True, scene._fit())]  # once, at the panel's own width, scanning in
        assert set(heights) == {9}  # the scan-in and the weather after it: as tall as the art, row for row


@pytest.mark.usefixtures("canned")
async def test_just_after_a_resize_the_old_art_is_cropped_not_wrapped(monkeypatch):
    m, state = square()
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        scene = screen.query_one(SceneArt)
        assert await until(pilot, lambda: drawn(scene))
        tall, gate, real = scene.outer_size.height, threading.Event(), SceneArt._build

        def held(self, kinds):  # the drawing for the new size takes its time
            gate.wait(5)
            return real(self, kinds)

        monkeypatch.setattr(SceneArt, "_build", held)
        try:
            await pilot.resize_terminal(60, 30)  # the art, centred for 140 columns, is wider than the panel now
            await pilot.pause(0.2)
            assert scene.outer_size.height == tall  # not wrapped onto twice as many rows
        finally:
            gate.set()
        assert await until(pilot, lambda: drawn(scene) and scene._fitted == scene._fit())
        assert scene.outer_size.height == tall
