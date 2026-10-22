"""The save format (engine/state.SAVE_FORMAT): a save of another version is refused on every way in (the saves
screen behind 继续调查, a LAN room from the host screen or `autokeeper host --load`) and its file is never
written. The review of 2026-10-22: a 0.0.3 save with a chase going on loaded as an ordinary game, without
its `chase` and `pending_resolution`, and the next autosave wrote it back so. Saves in a temp folder."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from textual.widgets import OptionList, Static

import autokeeper
from autokeeper import paths
from autokeeper.engine.game import Game
from autokeeper.engine.state import SAVE_FORMAT, GameState, SaveRefused, read_save
from autokeeper.module.schema import Module
from autokeeper.net import room as lan
from autokeeper.paths import EXAMPLE
from autokeeper.tui import library

from .test_menus import canned, plain  # noqa: F401  (a fixture)
from .test_room import shown, stand_ins  # noqa: F401  (a fixture)
from .test_tui import until

REFUSED = f"旧版本存档，{autokeeper.__version__} 无法读取"


def old_save(m: Module, slot: str, updated: str = "2030-01-01 09:00") -> bytes:
    """A save as 0.0.3 wrote it, a chase going on: no save format, `chase` and `pending_resolution` in it,
    and its line in the index. Its bytes, to compare after."""
    raw = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot=slot, human=["zhouheng"]).model_dump(mode="json")
    del raw["save_format"]
    raw["label"] = "雨夜追逐"
    raw["chase"] = {"round": 2, "active": True, "route": "office"}
    raw["pending_resolution"] = {"identity": {"kind": "respond", "id": "r2"}, "steps": {}}
    paths.games().mkdir(parents=True, exist_ok=True)
    data = json.dumps(raw, ensure_ascii=False, indent=1).encode()
    paths.save(slot).write_bytes(data)
    idx = library.save_index()
    idx[slot] = {"label": "雨夜追逐", "module": m.id, "title": m.title, "party": "周衡", "clock": "D1 22:00",
                 "location": "报社", "ending": None, "updated": updated, "path": str(EXAMPLE)}
    paths.save_index().write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def test_an_old_save_is_refused_and_a_new_one_reads_back_whole():
    m = Module.load(EXAMPLE)
    before = old_save(m, "old")
    with pytest.raises(SaveRefused) as refused:
        read_save(paths.save("old").read_text(encoding="utf-8"))
    assert str(refused.value) == REFUSED and refused.value.found is None
    assert paths.save("old").read_bytes() == before
    newer = json.loads(before) | {"save_format": SAVE_FORMAT + 1}
    with pytest.raises(SaveRefused) as newer_refused:
        read_save(json.dumps(newer))
    assert str(newer_refused.value).startswith("新版本存档")
    with pytest.raises(ValueError) as broken:
        read_save("[1, 2]")
    assert not isinstance(broken.value, SaveRefused)

    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="new", human=["zhouheng"])
    state.turn, state.label = 3, "新的一局"
    state.add("player", "我去钟楼")
    library.write_save(state, m)
    assert json.loads(paths.save("new").read_text(encoding="utf-8"))["save_format"] == SAVE_FORMAT
    again = read_save(paths.save("new").read_text(encoding="utf-8"))
    assert again.model_dump() == state.model_dump()
    library.write_save(again, m)  # and once more, unchanged
    assert read_save(paths.save("new").read_text(encoding="utf-8")).model_dump() == state.model_dump()
    assert library.save_index()["old"]["label"] == "雨夜追逐"  # writing another save keeps the old one's line
    assert paths.save("old").read_bytes() == before


async def test_continue_lists_an_old_save_and_never_opens_or_writes_it(canned):  # noqa: F811
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.menu import AskText, Confirm
    from autokeeper.tui.screens.saves import LoadScreen
    from autokeeper.tui.screens.title import TitleScreen

    m = Module.load(EXAMPLE)
    library.write_save(Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="fine", human=["zhouheng"]), m)
    idx = library.save_index()
    idx["fine"]["updated"] = "2020-01-01 09:00"
    paths.save_index().write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    before = old_save(m, "old")
    said: list[str] = []
    app = AutoKeeperApp(theme="amber")
    real = app.notify
    app.notify = lambda message, **kw: (said.append(str(message)), real(message, **kw))[-1]  # type: ignore[method-assign]
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        assert isinstance(app.screen, TitleScreen)
        menu = app.screen.query_one("#menu", OptionList)
        menu.highlighted = menu.get_option_index("continue")  # 继续调查
        await pilot.press("enter")
        assert await until(pilot, lambda: isinstance(app.screen, LoadScreen))
        screen = app.screen
        await pilot.pause(0.2)
        assert [s.slot for s in screen.list] == ["old", "fine"] and screen.list[0].refused == REFUSED
        line = plain(screen.query_one(OptionList).get_option_at_index(0).prompt)
        assert "雨夜追逐" in line and "旧版本" in line and REFUSED in line
        assert REFUSED in plain(screen.query_one("#preview-body", Static).content)
        hint = str(screen.query_one("#setup-hint", Static).content)
        assert "Enter 继续" not in hint and "C 复制" not in hint and "R 改名" in hint and "D 删除" in hint
        await pilot.press("enter")  # not loaded: it says why and stays
        await pilot.pause(0.2)
        assert app.screen is screen and said[-1] == f"{REFUSED}。"
        await pilot.press("c")  # no copy of it
        await pilot.pause(0.2)
        assert [s.slot for s in screen.list] == ["old", "fine"]
        await pilot.press("r")  # a name of its own: in the index only
        await pilot.pause(0.2)
        assert isinstance(app.screen, AskText)
        app.screen.query_one("Input").value = "0.0.3 的那局"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert app.screen is screen and "0.0.3 的那局" in plain(screen.query_one(OptionList).get_option_at_index(0).prompt)
        assert library.save_index()["old"]["label"] == "0.0.3 的那局" and paths.save("old").read_bytes() == before
        await pilot.press("d")  # cleaned up: into the trash as it was
        await pilot.pause(0.2)
        assert isinstance(app.screen, Confirm)
        await pilot.click("#answer-yes")
        await pilot.pause(0.2)
        assert [s.slot for s in screen.list] == ["fine"] and "old" not in library.save_index()
    [trashed] = paths.trash().glob("*-old/old.json")
    assert trashed.read_bytes() == before
    assert json.loads((trashed.parent / "index.json").read_text(encoding="utf-8"))["old"]["label"] == "0.0.3 的那局"


def test_a_lan_room_refuses_an_old_save(stand_ins):  # noqa: F811
    m = Module.load(EXAMPLE)
    before = old_save(m, "lan-example-0101-000000")
    assert [slot for slot, _ in lan.lan_saves(m.id)] == ["lan-example-0101-000000"]  # still listed
    with pytest.raises(SaveRefused) as refused:
        lan.saved("lan-example-0101-000000")
    assert str(refused.value) == REFUSED
    assert paths.save("lan-example-0101-000000").read_bytes() == before


async def test_the_host_screen_refuses_an_old_lan_save(monkeypatch, stand_ins):  # noqa: F811
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.host import HostScreen

    m = Module.load(EXAMPLE)
    before = old_save(m, "lan-example-0101-000000")
    monkeypatch.setattr(paths, "module_folders", lambda: [EXAMPLE])
    said: list[str] = []
    app = AutoKeeperApp(theme="amber")
    real = app.notify
    app.notify = lambda message, **kw: (said.append(str(message)), real(message, **kw))[-1]  # type: ignore[method-assign]
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.3)
        app.push_screen(HostScreen())
        await pilot.pause(0.2)
        screen = app.screen
        await pilot.press("enter")  # the only module
        await pilot.pause(0.2)
        listed = screen.query_one("#host-saves", OptionList)
        assert listed.display and listed.option_count == 2
        listed.focus()
        await pilot.press("down", "enter")
        await pilot.pause(0.2)
        assert said[-1] == f"{REFUSED}。" and screen.slot == "" and "继续已有存档" not in shown(screen, "#host-note")
    assert paths.save("lan-example-0101-000000").read_bytes() == before


def test_autokeeper_host_load_refuses_an_old_save(stand_ins):  # noqa: F811
    from typer.testing import CliRunner

    from autokeeper.cli import app as cli

    m = Module.load(EXAMPLE)
    before = old_save(m, "lan-example-0101-000000")
    result = CliRunner().invoke(cli, ["host", str(EXAMPLE), "--no-web", "--load", "lan-example-0101-000000"])
    assert result.exit_code == 1 and REFUSED in result.output.replace("\n", "")
    assert paths.save("lan-example-0101-000000").read_bytes() == before


def test_the_cards_screen_shows_an_old_save_s_card_without_reading_the_game():
    from autokeeper.tui.screens.cards import CardsScreen

    m = Module.load(EXAMPLE)
    raw = json.loads(old_save(m, "old"))
    screen = SimpleNamespace(saves={"sold": raw})
    assert CardsScreen._game(screen, "sold:zhouheng") is None  # type: ignore[arg-type]
    fine = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="fine").model_dump(mode="json")
    screen.saves["sfine"] = fine
    assert isinstance(CardsScreen._game(screen, "sfine:zhouheng"), GameState)  # type: ignore[arg-type]
