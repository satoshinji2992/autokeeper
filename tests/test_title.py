"""The title screen's moving cover and the 角色卡 viewer (no model calls)."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.widgets import OptionList

from autokeeper import imagery, paths, roster
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.tui import app as tui
from autokeeper.tui import cover
from autokeeper.tui.app import AutoKeeperApp, TitleScreen
from autokeeper.tui.cards import CardsScreen
from autokeeper.tui.sheet import SheetModal


def test_words_go_over_the_picture_cell_by_cell():
    grid = [[("x", Style(bgcolor="#112233"))] * 10 for _ in range(3)]
    cover.put(grid, cover.cells(Text("A B", style="red")), 2, 1, keep_ground=True)
    cover.put(grid, cover.cells(Text("守秘")), 6, 2, under=cover.PLAQUE)
    out = cover.compose(grid)
    assert out.plain.split("\n") == ["x" * 10, "xxAxBxxxxx", "xxxxxx守秘"]  # the space let the picture through
    a = grid[1][2][1]
    assert a.color is not None and a.bgcolor is not None and a.bgcolor.triplet.hex == "#112233"  # letters keep the sky's colour


def test_the_loop_has_frames_that_move(monkeypatch):
    if not imagery.available():
        return
    still, later = cover.frame(60, 20, 0.5, "photo", 0), cover.frame(60, 20, 0.5, "photo", cover.FRAMES // 2)
    assert len(still) >= 19 and len(still[0]) == 60
    assert still != later  # the sea has moved
    assert cover.frame(60, 20, 0.5, "photo", cover.FRAMES) != still  # the lightning frame


async def test_title_without_chafa_shows_the_eye_and_the_whole_menu(monkeypatch):
    monkeypatch.setattr(imagery, "available", lambda: False)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        assert isinstance(app.screen, TitleScreen)
        shown = app.screen.query_one(cover.Cover).plain
        assert "( () )" in shown and "Ctrl+Q" in shown and "新的调查" in shown and "离开" in shown
        menu = app.screen.query_one("#menu", OptionList)
        assert menu.region.bottom <= 24 and menu.region.x >= 0
        await pilot.click("#title-cover", offset=(menu.region.x + 3, menu.region.y + 2))  # the second line: 继续调查
        await pilot.pause()
        assert app.screen.__class__.__name__ == "LoadScreen"


async def test_cards_list_kept_cards_and_pregens(monkeypatch, tmp_path, skills_dir):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(imagery, "available", lambda: False)
    monkeypatch.setattr(tui, "modules", lambda: [EXAMPLE])
    m = Module.load(EXAMPLE)
    mine = m.pregens[0].model_copy(update={"id": "mine", "name": "我自己的人"})
    roster.keep(mine, {"made": "test"})
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(CardsScreen())
        await pilot.pause(0.5)
        screen = app.screen
        assert isinstance(screen, CardsScreen)
        assert "r:mine" in screen.cards and any(oid.startswith("m") for oid in screen.cards)
        assert screen.query_one("#choices", OptionList).highlighted_option.id == "r:mine"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SheetModal)


def test_sextants_take_one_cell_and_cjk_two():
    row = cover.cells(Text("🬀🬁🬂▌守秘"))[0]
    assert [ch for ch, _ in row] == ["🬀", "🬁", "🬂", "▌", "守", "", "秘", ""]  # 六分块 are narrow, past CJK in Unicode
    if imagery.available():
        fine = cover.frame(60, 20, 0.5, "fine", 0)
        assert fine and all(len(r) == 60 for r in fine)  # every row as wide as the screen
