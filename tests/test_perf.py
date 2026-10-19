"""Smoothness: the work the game screen does per frame and per event stays bounded, and the
layout holds still while the scene loads (no model calls)."""

from __future__ import annotations

import gc
import shutil
import threading

import pytest
from rich.text import Text
from textual.content import Content
from textual.screen import Screen

from autokeeper import imagery, paths
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.drawing import cover
from autokeeper.tui.screens.game import GameScreen
from autokeeper.tui.screens.setup import SetupScreen
from autokeeper.tui.screens.title import TitleScreen
from autokeeper.tui.widgets.panels import PORTRAIT, Folded, HistoryLine, SceneArt, StoryLog

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


async def test_the_title_stands_still_under_another_screen():
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        title = app.screen
        assert isinstance(title, TitleScreen)
        await pilot.pause(0.3)
        assert title.tick > 0  # the sea swells
        app.push_screen(Screen())  # a game, the setup: anything over it
        await pilot.pause(0.1)
        still = title.tick
        await pilot.pause(0.6)
        assert title.tick == still  # not painted behind the game, eight times a second
        assert not title.painted and cover.frame.cache_info().currsize <= 1  # nor kept, for each GC of the game to walk
        app.pop_screen()
        await pilot.pause(0.6)
        assert title.tick > still
        if imagery.available():
            assert await until(pilot, lambda: title.frames > 0)  # the painting again, without chafa


async def story_screen(app, pilot) -> GameScreen:
    m, state = square()
    screen = GameScreen(m, state, resume=True)
    app.push_screen(screen)
    assert await until(pilot, lambda: screen.game is not None and not screen.busy)
    return screen


@pytest.mark.usefixtures("canned")
async def test_a_long_story_keeps_its_latest_blocks_and_folds_the_rest():
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await story_screen(app, pilot)
        story = screen.story
        before = len(story.children)  # the recap's
        clue = story.add("clue", Text("〔旧报〕\n钟楼十年前也停过一次。"))  # a line in the history panel too
        for i in range(400):
            story.add("system", Text(f"第 {i} 段"))
        await pilot.pause()
        blocks = [w for w in story.children if not isinstance(w, Folded) and w.is_attached]
        assert len(blocks) <= StoryLog.KEEP + StoryLog.FOLD  # every layout walks these, not four hundred
        assert "第 399 段" in str(blocks[-1].render()) and story.is_vertical_scroll_end
        header = story.query_one(Folded)
        folded = before + 401 - len(blocks)
        assert f"前面还有 {folded} 段" in str(header.render())

        header.on_click()  # a batch comes back above, the page stays put
        await pilot.pause()
        assert len([w for w in story.children if not isinstance(w, Folded)]) == len(blocks) + StoryLog.FOLD
        assert f"前面还有 {folded - StoryLog.FOLD} 段" in str(header.render())

        line = next(h for h in screen.query(HistoryLine) if h.block is clue)
        assert clue.parent is None  # folded away
        line.on_click()  # the history panel still takes you there
        await pilot.pause()
        await pilot.pause()
        back = next(w for w in story.children if "钟楼十年前也停过一次" in str(w.render()))
        assert back.has_class("clue") and story.scroll_offset.y <= back.virtual_region.y <= story.scroll_offset.y + story.size.height


@pytest.mark.usefixtures("canned")
async def test_a_narration_is_laid_out_once_a_line_not_once_a_tick(monkeypatch):
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await story_screen(app, pilot)
        story = screen.story
        layouts = []
        real = Screen._refresh_layout

        def counted(self, *a, **k):
            layouts.append(1)
            return real(self, *a, **k)

        monkeypatch.setattr(Screen, "_refresh_layout", counted)
        story.begin_keeper()
        words = "钟声停了很久，广场上的鸽子却一只也没有飞走，它们都朝着钟楼的方向站着。" * 8
        for i in range(0, len(words), 6):
            story.feed(words[i : i + 6])
        assert await until(pilot, lambda: not story._typing, 100)
        lines = story._live.size.height
        story.end_keeper(words)
        await pilot.pause()
        # A layout (and a scroll) when it takes another line; the other ticks only repaint it.
        assert lines >= 3 and len(layouts) <= 2 * lines + 4
        assert words in str(story.children[-1].render())


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
@pytest.mark.usefixtures("canned")
async def test_no_picture_is_rendered_on_the_ui_thread_when_a_game_starts(monkeypatch):
    runs, real = [], imagery.render.__wrapped__

    def render(*args, **kwargs):  # every chafa run, uncached: its size, and whether it ran on the UI thread
        runs.append((args[1:3], threading.current_thread() is threading.main_thread()))
        return real(*args, **kwargs)

    monkeypatch.setattr(imagery, "render", render)
    m = Module.load(paths.MODULES / "zhuishuren")
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="thumb", human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        assert await until(pilot, lambda: any(size == PORTRAIT for size, _ in runs))  # the side column's face too
        assert not any(on_ui for _, on_ui in runs)


def test_the_ui_lets_a_frames_objects_die_young():
    AutoKeeperApp(theme="amber")
    assert gc.get_threshold()[0] >= 10_000  # not a full collection every second or two of play


@pytest.mark.usefixtures("canned")
async def test_weather_frames_become_the_same_content_with_each_style_turned_once():
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await story_screen(app, pilot)  # the square, in fog
        scene = screen.query_one(SceneArt)
        assert await until(pilot, lambda: scene._atmo is not None)
        for _ in range(3):
            frame = scene._atmo.frame()
            mine, theirs = scene._content(frame), Content.from_rich_text(frame, console=app.console)
            assert mine.plain == theirs.plain and mine.spans == theirs.spans
        assert len(scene._styles) < len(frame.spans) * 3  # turned once each, not once a frame


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
async def test_going_through_the_investigators_never_waits_for_chafa(monkeypatch, skills_dir):
    runs, real = [], imagery.render.__wrapped__

    def render(*args, **kwargs):
        runs.append((args[1:3], threading.current_thread() is threading.main_thread()))
        return real(*args, **kwargs)

    monkeypatch.setattr(imagery, "render", render)
    monkeypatch.setattr(paths, "module_folders", lambda: [paths.MODULES / "zhuishuren"])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        app.push_screen(SetupScreen(spectate=False))
        await pilot.pause()
        await pilot.press("enter")  # 《追书人》
        assert await until(pilot, lambda: getattr(app.screen, "step", "") == "investigator")
        await pilot.press("down")
        assert await until(pilot, lambda: sum(size == (32, 14) for size, _ in runs) >= 2)  # both faces, photos
        assert not any(on_ui for _, on_ui in runs)
