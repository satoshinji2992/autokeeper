"""The beginner's guide and the first-time hints (no model calls): the guide opens from the
title and from F1 and turns its pages; a hint comes the first time, once per player, at
most one a turn, never when hints are off; a new player is pointed to 《追书人》."""

from __future__ import annotations

import json

import pytest
from textual.widgets import Checkbox, ContentSwitcher, OptionList

from autokeeper import config
from autokeeper.config import EXAMPLE, Role
from autokeeper.engine.game import Game
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules
from autokeeper.tui import app as tui
from autokeeper.tui import tips
from autokeeper.tui.app import AutoKeeperApp, GameScreen, SetupScreen, TitleScreen
from autokeeper.tui.guide import PAGES, GuideScreen
from autokeeper.tui.screens import HandoutModal, RollModal
from autokeeper.tui.tips import TipLine

TUTORIAL = config.MODULES / tips.TUTORIAL


class Canned(hm.Model):
    async def _call(self, messages, *, schema, images, on_delta):
        text = "你站在钟楼底下，告示栏上的纸被风掀起一角。"
        if on_delta:
            await on_delta(text)
        return Reply(text=text, data={} if schema else None)


@pytest.fixture
def canned(monkeypatch, tmp_path, skills_dir):
    """Every agent answers from a can; saves, and the hints already shown, go to a temp folder."""
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    return cfg


def shown(screen) -> list[str]:
    return [str(w.render()) for w in screen.story.query(TipLine)]


async def until(pilot, test, tries: int = 50) -> bool:
    for _ in range(tries):
        if test():
            return True
        await pilot.pause(0.1)
    return test()


async def open_game(app, pilot, *, resume: bool = False, location: str = "") -> GameScreen:
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="example-0101-000000", human=[lead.id])
    state.location = location or state.location
    screen = GameScreen(m, state, resume=resume)
    app.push_screen(screen)
    assert await until(pilot, lambda: screen.game is not None and not screen.busy and len(screen.story.children) > 0)
    await pilot.pause(0.2)
    return screen


def dice(who: str, value: int = 23, target: int = 50) -> dict:
    passed = value <= target
    return {
        "type": "dice", "who": who, "skill": "侦查", "target": target, "hard": target // 2, "extreme": target // 5,
        "difficulty": "常规", "bonus": 0, "value": value, "candidates": [value], "tens": [value // 10 * 10],
        "units": value % 10, "kept": 0, "level": "常规成功" if passed else "失败", "passed": passed, "pushed": False,
        "text": f"侦查 {target} → {value}",
    }  # fmt: skip


async def turn(screen: GameScreen, *events: dict) -> None:
    """One turn: its events, then the end of the narration, where waiting hints show."""
    screen.state.turn += 1
    for ev in events:
        await screen.handle(ev)
    await screen.handle({"type": "keeper_end", "text": "钟楼里的齿轮咔哒响了一下。"})


@pytest.mark.parametrize("size", [(140, 44), (80, 24)])
async def test_the_guide_opens_from_the_title_and_turns_its_pages(size, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVES", tmp_path)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        menu = app.screen.query_one("#menu", OptionList)
        assert menu.region.bottom <= size[1]
        assert menu.max_scroll_y == 0  # all title options, including Quit, fit without scrolling
        menu.highlighted = menu.get_option_index("guide")
        await pilot.press("enter")
        await pilot.pause()
        guide = app.screen
        assert isinstance(guide, GuideScreen)
        pages = guide.query_one(ContentSwitcher)
        assert pages.current == "guide-play"
        await pilot.press("down")
        assert pages.current == "guide-screen"
        await pilot.press("down", "down")
        assert pages.current == "guide-push"
        await pilot.press("end")  # the last page is the key reference
        assert pages.current == "guide-keys"
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, TitleScreen)
    assert [p.id for p in PAGES][:4] == ["play", "screen", "dice", "push"]


async def test_f1_opens_the_guide_where_the_last_hint_pointed(canned):
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await open_game(app, pilot)
        assert [t.startswith("轮到你了") for t in shown(screen)] == [True]  # after the opening: how to begin
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, GuideScreen) and app.screen.query_one(ContentSwitcher).current == "guide-play"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("f1")  # nothing new since: the key reference, as F1 always was
        await pilot.pause()
        assert app.screen.query_one(ContentSwitcher).current == "guide-keys"
        await pilot.press("f1")  # F1 again closes it
        await pilot.pause()
        assert app.screen is screen


async def test_a_hint_comes_once_per_player(canned):
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await open_game(app, pilot)
        name = screen.state.investigator.name
        await turn(screen, dice(name))
        assert len(shown(screen)) == 2 and "结果卡这样看" in shown(screen)[-1]
        await turn(screen, dice(name, 80))
        assert len(shown(screen)) == 2  # the second roll: nothing new to say
    assert sorted(json.loads((config.SAVES / tips.FILE).read_text(encoding="utf-8"))["seen"]) == ["check", "start"]

    app = AutoKeeperApp(theme="amber")  # the same player, another day
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await open_game(app, pilot)
        await turn(screen, dice(screen.state.investigator.name, 5))
        assert shown(screen) == []


async def test_at_most_one_hint_a_turn_the_rest_wait(canned):
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await open_game(app, pilot, resume=True)  # a loaded game: no opening, no "how to begin"
        name = screen.state.investigator.name
        clue = {"type": "clue", "id": "c_notice", "title": "告示", "text": "午夜的钟声多了一下。"}
        npc = {"type": "npc", "npc": "warden", "name": "老守钟人", "text": "钟有钟的规矩。", "portrait": ""}
        await turn(screen, dice(name), clue, npc)
        assert len(shown(screen)) == 1 and "线索本" in shown(screen)[0]  # the clue first
        await screen.handle({"type": "keeper_end", "text": "就在这时，楼梯上传来脚步声。"})  # an interlude, same turn
        assert len(shown(screen)) == 1
        await turn(screen)
        await turn(screen)
        assert len(shown(screen)) == 3 and "结果卡这样看" in shown(screen)[1] and "跟人说话" in shown(screen)[2]
        await turn(screen)  # four turns and nothing found: there is F5
        assert len(shown(screen)) == 4 and "F5" in shown(screen)[3]
        await turn(screen)
        assert len(shown(screen)) == 4  # nothing left waiting


async def test_a_real_turn_ends_with_one_hint_after_its_narration(canned, fake):
    from .test_engine import HARD_TALK, PLEAD, FakeCast

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await open_game(app, pilot, resume=True, location="tower")
        screen.game.cast, screen.game.rules = FakeCast({"我劝守钟人说实话": (PLEAD, HARD_TALK)}), Rules(dice=fake)
        fake.queue(8)  # 说服 40, hard: an extreme success, and the diary with it
        screen.send_line("我劝守钟人说实话")
        assert await until(pilot, lambda: isinstance(app.screen, RollModal))
        await pilot.pause(0.5)
        await pilot.press("enter")
        assert await until(pilot, lambda: isinstance(app.screen, HandoutModal))
        await pilot.press("escape")
        assert await until(pilot, lambda: app.screen is screen and not screen.busy)
        # a roll, a clue, a handout and an NPC's answer all came up: the rarest is told, after the story
        assert len(shown(screen)) == 1 and "手稿" in shown(screen)[0]
        assert isinstance(screen.story.children[-1], TipLine)


async def test_hints_off_means_none_and_the_guide_switches_them_back_on(canned):
    canned.tips = False  # tips = false in the config file
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await open_game(app, pilot)
        await turn(screen, dice(screen.state.investigator.name))
        assert shown(screen) == []
        await pilot.press("f1")
        await pilot.pause()
        switch = app.screen.query_one("#guide-tips", Checkbox)
        assert not switch.value
        switch.value = True  # the switch at the foot of the guide
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await turn(screen, dice(screen.state.investigator.name))
        assert len(shown(screen)) == 1 and "结果卡这样看" in shown(screen)[0]
    assert tips.enabled() and json.loads((config.SAVES / tips.FILE).read_text(encoding="utf-8"))["on"] is True


def test_the_config_file_turns_hints_off(tmp_path):
    f = tmp_path / "autokeeper.toml"
    f.write_text("tips = false\n", encoding="utf-8")
    assert config.load(f).tips is False


async def test_a_new_player_is_pointed_to_the_tutorial(monkeypatch, tmp_path, skills_dir):
    monkeypatch.setattr(config, "SAVES", tmp_path)
    monkeypatch.setattr(tui, "modules", lambda: [EXAMPLE, TUTORIAL])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.push_screen(SetupScreen(spectate=False))
        await pilot.pause()
        choices = app.screen.query_one("#choices", OptionList)
        assert choices.highlighted == 1 and "《追书人》 · 初次推荐" in str(choices.get_option(str(TUTORIAL)).prompt)
        await pilot.press("enter")  # Enter takes it
        await pilot.pause()
        assert app.screen.module and app.screen.module.id == "zhuishuren"
    (tmp_path / "index.json").write_text(json.dumps({"zhuishuren-1": {"module": "zhuishuren"}}), encoding="utf-8")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:  # played before: no suggestion
        await pilot.pause()
        app.push_screen(SetupScreen(spectate=False))
        await pilot.pause()
        choices = app.screen.query_one("#choices", OptionList)
        assert choices.highlighted == 0 and "初次推荐" not in str(choices.get_option(str(TUTORIAL)).prompt)


def test_the_tutorial_is_not_given_away():
    """The hints and the guide say only what 《追书人》's hook and opening tell: no place,
    person, clue, handout, event or ending the player has not come across, and none of the
    things a first game there turns on."""
    m = Module.load(TUTORIAL)
    told = m.hook + m.opening + m.location(m.start).read_aloud
    texts = [t.text for t in tips.TIPS.values()] + list(tips.OPENINGS.values()) + [p.text for p in PAGES]
    names = [n.name for n in m.npcs] + [loc.name for loc in m.locations] + [c.title for c in m.clues]
    names += [h.title for h in m.handouts] + [e.title for e in m.events] + [e.title for e in m.endings]
    names = [n for n in names if len(n) >= 3]  # "是他", an event's title, is also just words
    names += ["守墓人", "陵墓", "隧道", "食尸鬼", "日记", "剪报", "窗锁", "窗框", "足迹", "脚印", "人影", "监视", "铲", "花瓶", "私酒"]
    for text in texts:
        for name in names:
            assert name not in text or name in told, (name, text[:40])
