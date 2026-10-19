"""The Esc menu in the game, the save manager (继续调查) and the card manager (角色卡); no
model calls, saves in a temp folder."""

from __future__ import annotations

import hashlib
import json
import time

import pytest
from rich.console import Console
from textual.widgets import Input, OptionList, Static

from autokeeper import config, imagery, roster
from autokeeper.config import EXAMPLE, Role
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules
from autokeeper.tui import app as tui
from autokeeper.tui import saves
from autokeeper.tui.app import AutoKeeperApp, GameScreen, SetupScreen, TitleScreen
from autokeeper.tui.cardedit import CardEditModal
from autokeeper.tui.cards import CardsScreen
from autokeeper.tui.guide import GuideScreen
from autokeeper.tui.menu import MENU, AskText, Confirm, GameMenu
from autokeeper.tui.saves import LoadScreen
from autokeeper.tui.settings import SettingsModal

from .test_tui import Canned, until


@pytest.fixture
def canned(monkeypatch, tmp_path, skills_dir):
    """As in test_tui: every agent answers from a can, saves go to a temp folder."""
    for var in ("AUTOKEEPER_PRESET", "AUTOKEEPER_MODEL", "AUTOKEEPER_FALLBACK", "AUTOKEEPER_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "SAVES", tmp_path)
    monkeypatch.setattr(tui, "modules", lambda: [EXAMPLE])
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    return cfg


def plain(renderable) -> str:
    console = Console(width=120, record=True)
    console.print(renderable)
    return console.export_text()


def options(ol: OptionList) -> list[str]:
    return [ol.get_option_at_index(i).id or "" for i in range(ol.option_count)]


def saved_game(m: Module, slot: str, *, turn: int = 1, updated: str | None = None, **change) -> GameState:
    lead = m.pregens[0].model_copy(deep=True)
    st = Game.new(m, [lead], slot=slot, human=[lead.id])
    st.turn = turn
    for k, v in change.items():
        setattr(st, k, v)
    tui.write_save(st, m)
    if updated:
        idx = tui.save_index()
        idx[slot]["updated"] = updated
        (config.SAVES / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    return st


async def game_at_rest(app, pilot, m: Module, slot: str = "menu") -> GameScreen:
    lead = m.pregens[0].model_copy(deep=True)
    screen = GameScreen(m, Game.new(m, [lead], slot=slot, human=[lead.id]), resume=True)
    app.push_screen(screen)
    assert await until(pilot, lambda: screen.game is not None and not screen.busy)
    return screen


async def pick(pilot, app, key: str) -> None:
    """Esc, then the menu entry `key`, by its place in the list."""
    await pilot.press("escape")
    await pilot.pause()
    menu = app.screen
    assert isinstance(menu, GameMenu)
    menu.query_one(OptionList).highlighted = options(menu.query_one(OptionList)).index(key)
    await pilot.press("enter")
    await pilot.pause()


# -- the Esc menu ----------------------------------------------------------------------------------
async def test_esc_opens_the_menu_and_each_entry_does_its_thing(canned):
    m = Module.load(EXAMPLE)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        screen = await game_at_rest(app, pilot, m)
        assert "Esc 菜单" in screen.query_one("#command", Input).placeholder
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, GameMenu)
        ol = app.screen.query_one(OptionList)
        assert options(ol) == [k for k, _ in MENU if k != "plugins"] and ol.highlighted == 0  # no plugins here
        await pilot.press("escape")  # Esc again: back in the game, the box ready for typing
        await pilot.pause()
        assert app.screen is screen and screen.query_one("#command", Input).has_focus
        await pick(pilot, app, "continue")
        assert app.screen is screen

        (config.SAVES / "menu.json").unlink(missing_ok=True)
        await pick(pilot, app, "save")
        assert app.screen is screen and (config.SAVES / "menu.json").exists()

        await pick(pilot, app, "settings")
        assert isinstance(app.screen, SettingsModal)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen

        await pick(pilot, app, "keys")
        assert isinstance(app.screen, GuideScreen) and app.screen.first_page == "keys"
        await pilot.press("escape")
        await pick(pilot, app, "guide")
        assert isinstance(app.screen, GuideScreen) and app.screen.first_page == "play"
        await pilot.press("escape")
        await pilot.pause()

        await pick(pilot, app, "load")
        assert isinstance(app.screen, LoadScreen) and app.screen.current == "menu"
        await pilot.press("escape")  # back to the same game
        await pilot.pause()
        assert app.screen is screen

        screen.busy = True  # mid-turn: going back to the title asks first
        await pick(pilot, app, "title")
        assert isinstance(app.screen, Confirm)
        await pilot.press("escape")  # no
        await pilot.pause()
        assert app.screen is screen
        await pick(pilot, app, "title")
        await pilot.click("#answer-wait")  # 讲完再走
        await pilot.pause()
        assert app.screen is screen
        screen._busy(False)  # the turn is over: now it goes
        assert await until(pilot, lambda: isinstance(app.screen, TitleScreen))


async def test_leaving_now_mid_turn_saves_first(canned):
    m = Module.load(EXAMPLE)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        screen = await game_at_rest(app, pilot, m, slot="now")
        (config.SAVES / "now.json").unlink(missing_ok=True)
        screen.busy = True
        await pick(pilot, app, "title")
        await pilot.click("#answer-now")
        assert await until(pilot, lambda: isinstance(app.screen, TitleScreen))
        assert (config.SAVES / "now.json").exists()


async def test_after_the_end_the_menu_starts_at_back_to_the_title(canned):
    m = Module.load(EXAMPLE)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        screen = await game_at_rest(app, pilot, m, slot="over")
        screen.state.ending = "stopped"
        await pilot.press("escape")
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        assert ol.get_option_at_index(ol.highlighted).id == "title"
        await pilot.press("enter")
        assert await until(pilot, lambda: isinstance(app.screen, TitleScreen))


async def test_loading_another_save_from_the_menu_takes_the_game_s_place(canned):
    m = Module.load(EXAMPLE)
    saved_game(m, "other", turn=5, updated="2020-01-01 10:00")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        screen = await game_at_rest(app, pilot, m, slot="playing")
        depth = len(app.screen_stack)
        await pick(pilot, app, "load")
        load = app.screen
        assert isinstance(load, LoadScreen) and [s.slot for s in load.list] == ["playing", "other"]
        assert "正在玩" in plain(load.query_one(OptionList).get_option_at_index(0).prompt)
        await pilot.press("d")  # the game being played cannot go to the trash
        await pilot.pause()
        assert app.screen is load and (config.SAVES / "playing.json").exists()
        load.query_one(OptionList).highlighted = load.query_one(OptionList).get_option_index("other")
        await pilot.press("enter")
        assert await until(pilot, lambda: isinstance(app.screen, GameScreen) and app.screen is not screen)
        assert app.screen.state.slot == "other" and len(app.screen_stack) == depth  # in the old game's place


async def test_the_lan_menu_leaves_out_what_the_host_does(monkeypatch, tmp_path, skills_dir, fake):
    from autokeeper.net.hub import Hub
    from autokeeper.tui.net import NetGameScreen

    from .test_net import NoticeCast, closed, opened

    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=NoticeCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小明"))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("y")  # ready: the game begins
        assert await until(pilot, lambda: isinstance(app.screen, NetGameScreen) and app.screen.my_turn)
        screen = app.screen
        assert screen.link.host
        await pilot.press("escape")
        await pilot.pause()
        shown = options(app.screen.query_one(OptionList))
        assert "load" not in shown and "save" not in shown and "engine" in shown and "settings" in shown and "title" in shown
        await pilot.press("escape")
        await pilot.pause()
        screen.link.host = False  # a guest: the host picks the models
        await pilot.press("escape")
        await pilot.pause()
        assert "engine" not in options(app.screen.query_one(OptionList))
        await pilot.press("escape")
    await closed(task)


# -- 继续调查 --------------------------------------------------------------------------------------
def test_last_played_in_plain_words():
    now = time.mktime((2026, 10, 19, 22, 0, 0, 0, 0, -1))
    assert saves.when("2026-10-19 21:30", now) == "今天 21:30"
    assert saves.when("2026-10-18 08:05", now) == "昨天 08:05"
    assert saves.when("2026-10-17 21:30", now) == "10月17日"
    assert saves.when("2025-03-02 21:30", now) == "2025年3月2日"
    assert saves.game_time(1440 + 9 * 60 + 5) == "第2天 09:05"


async def test_the_save_list_newest_first_with_its_labels_and_preview(canned, monkeypatch):
    monkeypatch.setattr(imagery, "available", lambda: False)
    m = Module.load(EXAMPLE)
    clue = m.clues[0]
    saved_game(m, "old", updated="2020-01-01 09:00", label="第一次试玩")
    saved_game(m, "lan-example-1", turn=22, updated="2021-05-01 09:00", ending=m.endings[0].id)
    newest = saved_game(m, "new", turn=7, minutes=14 * 60 + 20, clues=[clue.id])
    newest.progress.text = "守钟人说钟声会在夜里自己响起。"
    tui.write_save(newest, m)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(LoadScreen())
        await pilot.pause(0.5)
        screen = app.screen
        assert [s.slot for s in screen.list] == ["new", "lan-example-1", "old"]
        ol = screen.query_one(OptionList)
        rows = [plain(ol.get_option_at_index(ol.get_option_index(s)).prompt) for s in ("new", "lan-example-1", "old")]
        assert f"《{m.title}》" in rows[0] and "今天" in rows[0] and "第1天 14:20" in rows[0] and "第 7 轮" in rows[0]
        assert m.pregens[0].name in rows[0] and m.location(newest.location).name in rows[0]
        assert "联机" in rows[1] and "已结束" in rows[1] and "2021年5月1日" in rows[1]
        assert "第一次试玩" in rows[2] and "联机" not in rows[2]
        preview = plain(screen.query_one("#preview-body", Static).content)
        assert "HP" in preview and "SAN" in preview and clue.title in preview and "守钟人说钟声" in preview
        assert "Enter 继续" in str(screen.query_one("#setup-hint", Static).content)
        await pilot.press("enter")
        assert await until(pilot, lambda: isinstance(app.screen, GameScreen))
        assert app.screen.state.slot == "new"


async def test_rename_is_kept_in_the_save_and_survives_the_next_autosave(canned):
    m = Module.load(EXAMPLE)
    saved_game(m, "named")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(LoadScreen())
        await pilot.pause(0.3)
        await pilot.click("#setup-hint", offset=(14, 0))  # the keys on the hint line take a click too: R 改名
        await pilot.pause()
        assert isinstance(app.screen, AskText)
        app.screen.query_one(Input).value = "雨夜那一局"
        await pilot.press("enter")
        await pilot.pause()
        assert "雨夜那一局" in plain(app.screen.query_one(OptionList).get_option_at_index(0).prompt)
    state = GameState.model_validate_json((config.SAVES / "named.json").read_text(encoding="utf-8"))
    assert state.label == "雨夜那一局" and state.slot == "named"  # the file and its thread keep their name
    tui.write_save(state, m)  # playing on
    assert tui.save_index()["named"]["label"] == "雨夜那一局"
    saves.rename("named", "")
    assert saves.saves()[0].name == f"《{m.title}》"


async def test_delete_moves_the_save_to_the_trash_and_the_others_still_load(canned):
    m = Module.load(EXAMPLE)
    saved_game(m, "keep", updated="2020-01-01 09:00")
    saved_game(m, "gone")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(LoadScreen())
        await pilot.pause(0.3)
        screen = app.screen
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, Confirm)
        await pilot.press("escape")  # changed their mind
        await pilot.pause()
        assert (config.SAVES / "gone.json").exists()
        await pilot.press("d")
        await pilot.pause()
        await pilot.click("#answer-yes")
        await pilot.pause()
        assert app.screen is screen and [s.slot for s in screen.list] == ["keep"]
        assert not (config.SAVES / "gone.json").exists() and "gone" not in tui.save_index()
        trashed = list((config.SAVES / "trash").glob("*-gone/gone.json"))
        assert len(trashed) == 1 and "gone" in json.loads((trashed[0].parent / "index.json").read_text(encoding="utf-8"))
        await pilot.press("enter")
        assert await until(pilot, lambda: isinstance(app.screen, GameScreen))
        assert app.screen.state.slot == "keep"


async def test_a_copy_has_a_slot_of_its_own(canned):
    m = Module.load(EXAMPLE)
    saved_game(m, "first", turn=4, updated="2020-01-01 09:00")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(LoadScreen())
        await pilot.pause(0.3)
        await pilot.press("c")
        await pilot.pause()
        listed = app.screen.list
        assert len(listed) == 2 and listed[0].slot != "first" and listed[0].name == f"《{m.title}》 副本"
        assert listed[0].state.turn == 4 and listed[0].state.slot == listed[0].slot


async def test_a_broken_save_shows_greyed_and_can_be_deleted(canned):
    m = Module.load(EXAMPLE)
    saved_game(m, "fine", updated="2020-01-01 09:00")
    idx = tui.save_index()
    idx["broken"] = {"module": "example", "title": m.title, "updated": "2030-01-01 09:00"}
    (config.SAVES / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    (config.SAVES / "broken.json").write_text("{half a save", encoding="utf-8")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(LoadScreen())
        await pilot.pause(0.3)
        screen = app.screen
        assert screen.list[0].slot == "broken" and screen.list[0].state is None
        assert "读不了" in plain(screen.query_one(OptionList).get_option_at_index(0).prompt)
        assert "Enter 继续" not in str(screen.query_one("#setup-hint", Static).content)
        await pilot.press("enter")  # nothing to load: it says so and stays
        await pilot.pause()
        assert app.screen is screen
        await pilot.press("d")
        await pilot.pause()
        await pilot.click("#answer-yes")
        await pilot.pause()
        assert [s.slot for s in screen.list] == ["fine"] and list((config.SAVES / "trash").glob("*-broken/broken.json"))


# -- 角色卡 ----------------------------------------------------------------------------------------
def module_files() -> str:
    digest = hashlib.sha256()
    for f in sorted(p for p in EXAMPLE.rglob("*") if p.is_file()):
        digest.update(str(f.relative_to(EXAMPLE)).encode() + f.read_bytes())
    return digest.hexdigest()


async def test_the_cards_screen_groups_and_manages_cards(canned, monkeypatch, tmp_path):
    monkeypatch.setattr(imagery, "available", lambda: False)
    before = module_files()
    m = Module.load(EXAMPLE)
    pregen = m.pregens[0]
    roster.keep(pregen.model_copy(update={"id": "mine", "name": "我自己的人"}), {"made": "test"})
    saved_game(m, "played")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(CardsScreen())
        await pilot.pause(0.5)
        screen = app.screen
        ol = screen.query_one(OptionList)
        shown = [plain(ol.get_option_at_index(i).prompt).strip() for i in range(ol.option_count)]
        heads = [s for s in shown if s in ("我的角色", "存档里的", "模组预设")]
        assert heads == ["我的角色", "存档里的", "模组预设"]
        assert shown.index("我的角色") < shown.index("存档里的") < shown.index("模组预设")
        assert ol.highlighted_option.id == "r:mine"
        hint = str(screen.query_one("#setup-hint", Static).content)
        assert "E 编辑" in hint and "D 删除" in hint and "C 复制" not in hint and "A 新建角色" in hint

        # a pregen cannot change; it can be copied into 我的角色
        ol.highlighted = ol.get_option_index(f"m{EXAMPLE.name}:{pregen.id}")
        await pilot.pause()
        hint = str(screen.query_one("#setup-hint", Static).content)
        assert "C 复制成新角色" in hint and "E 编辑" not in hint
        await pilot.press("e")
        await pilot.pause()
        assert app.screen is screen  # not one of the player's: no form
        await pilot.press("c")
        await pilot.pause()
        copied = ol.highlighted_option.id
        assert copied.startswith("r:card-") and len(roster.saved()) == 2

        # edit the copy: a new name
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, CardEditModal)
        app.screen.query_one("#edit-name", Input).value = "新名字"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.screen is screen and {c.name for c in roster.saved()} == {"我自己的人", "新名字"}

        # export it
        out = tmp_path / "导出"
        await pilot.press("x")
        await pilot.pause()
        app.screen.query_one(Input).value = str(out)
        await pilot.press("enter")
        await pilot.pause()
        exported = json.loads((out / "新名字.json").read_text(encoding="utf-8"))
        assert exported["investigator"]["name"] == "新名字"

        # delete it: into the trash
        ol.highlighted = ol.get_option_index(copied)
        await pilot.press("d")
        await pilot.pause()
        await pilot.click("#answer-yes")
        await pilot.pause()
        assert [c.name for c in roster.saved()] == ["我自己的人"]
        assert list((config.SAVES / "trash").glob(f"*-{copied[2:]}/{copied[2:]}.json"))

        # a save's card copies too; the save is left as it was
        save_before = (config.SAVES / "played.json").read_bytes()
        save_card = next(oid for oid in screen.cards if oid.startswith("splayed:"))
        ol.highlighted = ol.get_option_index(save_card)
        await pilot.press("e")  # read-only: no form
        await pilot.pause()
        assert app.screen is screen
        await pilot.press("c")
        await pilot.pause()
        assert len(roster.saved()) == 2 and (config.SAVES / "played.json").read_bytes() == save_before

        # with a pregen, straight to 新的调查 with it picked
        ol.highlighted = ol.get_option_index(f"m{EXAMPLE.name}:{pregen.id}")
        await pilot.press("n")
        assert await until(pilot, lambda: isinstance(app.screen, SetupScreen) and app.screen.step == "investigator")
        setup = app.screen.query_one("#choices", OptionList)
        assert setup.highlighted_option.id == pregen.id
    assert module_files() == before  # the module's own pregens were never written to


async def test_new_cards_come_from_the_setup_s_flows_and_land_in_my_cards(canned, monkeypatch):
    from autokeeper.tui.card import CardScreen

    monkeypatch.setattr(imagery, "available", lambda: False)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(CardsScreen())
        await pilot.pause(0.5)
        screen = app.screen
        ol = screen.query_one(OptionList)
        assert "create" in options(ol)  # 我的角色 is empty: 新建角色 is there, and highlighted after the cards
        for down, start in ((0, "figure"), (1, "import")):
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, GameMenu)
            for _ in range(down):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, CardScreen) and app.screen.start == start
            if start == "figure":
                await pilot.press("escape")  # nothing made
                await pilot.pause()
                assert app.screen is screen and not roster.saved()
        made = Module.load(EXAMPLE).pregens[0].model_copy(update={"id": "card-made01", "name": "车出来的人", "portrait": "card-made01"})
        roster.keep(made, {"made": "test"})  # what CardScreen.keep does with the finished card
        app.screen.dismiss(made)
        await pilot.pause(0.3)
        assert app.screen is screen and ol.highlighted_option.id == "r:card-made01"
        assert "车出来的人" in plain(ol.highlighted_option.prompt)


async def test_editing_a_kept_card_changes_its_words_and_photo_never_its_numbers(canned, monkeypatch, tmp_path):
    from PIL import Image

    from autokeeper.tui.cards import update_kept

    monkeypatch.setattr(imagery, "available", lambda: False)
    m = Module.load(EXAMPLE)
    mine = m.pregens[0].model_copy(update={"id": "mine", "name": "原来的名字", "portrait": "mine", "backstory": {"说明": "玩家带来的人物卡"}})
    roster.keep(mine, {"made": "test"})
    picture = tmp_path / "新照片.png"
    Image.new("RGB", (60, 80), "#884422").save(picture)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(CardsScreen())
        await pilot.pause(0.5)
        await pilot.press("e")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, CardEditModal)
        assert not form.query(f"#edit-{'skills'}") and not form.query("#edit-luck")  # no box for a number
        assert "数值不能改" in "".join(str(w.render()) for w in form.query(Static))
        form.query_one("#edit-occupation", Input).value = "码头记者"
        form.query_one("#edit-age", Input).value = "41"
        form.query_one("#edit-residence", Input).value = "上海"
        keys = [form.query_one(f"#edit-story-{i}").parent.query_one(Static).render() for i in range(len(form.query("TextArea")) - 1)]
        assert "个人描述" in str(keys[0]) and "说明" in "".join(map(str, keys))  # the paper sheet's fields, and the card's own
        form.query_one("#edit-story-0").text = "瘦高，总戴一顶旧呢帽"
        form.query_one("#edit-inventory").text = "钢笔\n记者证"
        form.query_one("#edit-photo", Input).value = str(picture)
        form.query_one("#edit-save").scroll_visible(animate=False)
        await pilot.pause()
        await pilot.click("#edit-save")
        await pilot.pause(0.3)
        kept = roster.saved()[0]
        assert (kept.occupation, kept.age, kept.residence) == ("码头记者", 41, "上海")
        assert kept.backstory["个人描述"] == "瘦高，总戴一顶旧呢帽" and kept.backstory["说明"] == "玩家带来的人物卡"
        assert kept.inventory == ["钢笔", "记者证"] and (roster.folder() / "mine.jpg").is_file()
        assert (kept.characteristics, kept.skills, kept.hp, kept.san, kept.luck) == (mine.characteristics, mine.skills, mine.hp, mine.san, mine.luck)

        await pilot.press("e")  # Esc on the form changes nothing
        await pilot.pause()
        app.screen.query_one("#edit-name", Input).value = "不要了"
        await pilot.press("escape")
        await pilot.pause()
        assert roster.saved()[0].name == "原来的名字"
    update_kept(kept, {"skills": {"侦查": 99}, "luck": 99, "name": "改名"})  # numbers are not words: left alone
    again = roster.saved()[0]
    assert again.name == "改名" and again.skills == mine.skills and again.luck == mine.luck
