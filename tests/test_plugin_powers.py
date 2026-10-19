"""Plugins with more to say: panels, shows, questions and little games, timers, items, a hint
for the keeper, NPC attitudes and slash commands. The code still runs only in the sandbox;
what it shows or asks for is data the app draws (plugins/schema.py, tui/plugin_ui.py)."""

from __future__ import annotations

import io
import json
import time

import pytest
from pydantic import ValidationError
from textual.widgets import Button, Static

from autokeeper import paths
from autokeeper.agents.director import Improv
from autokeeper.engine.game import Game, Turn
from autokeeper.module import improv
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub, public_plugins
from autokeeper.paths import EXAMPLE
from autokeeper.plugins.runtime import verify
from autokeeper.plugins.schema import Case, Command, Effect, Output, Panel, Plugin, check
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.game import GameScreen
from autokeeper.tui.widgets.plugin_ui import PromptModal, ShowPlayer, panel_view, tab_label

from .test_engine import make
from .test_net import TableCast
from .test_tui import canned, story_text, until  # noqa: F401  (canned is a fixture)

LOCK = '''def handle(event, ctx, api):
    state = dict(ctx["state"])
    if event["hook"] == "command":
        state["dials"] = []
        return {"state": state, "effects": [ask(0)]}
    if event["hook"] == "answer":
        if event["by"] != "player":
            state["gave_up"] = event["by"]
            return {"state": state, "effects": []}
        dials = list(state.get("dials", [])) + [event["value"]]
        state["dials"] = dials
        if len(dials) < 3:
            return {"state": state, "effects": [ask(len(dials))]}
        state["open"] = dials == [3, 1, 4]
        return {"state": state, "effects": [{"kind": "note", "text": "锁开了" if state["open"] else "锁没开"}]}
    return {"state": state, "effects": []}


def ask(i):
    return {"kind": "prompt", "key": "dial" + str(i), "title": "保险箱", "style": "number", "low": 0, "high": 9,
            "text": "第 " + str(i + 1) + " 个转盘拨到几？"}
'''


def lock(**changes) -> Plugin:
    return Plugin(
        id="lock", name="保险箱", description="三个转盘的密码锁。", code=LOCK, hooks=["command", "answer"],
        capabilities=["prompt", "note"], commands=[Command(name="开锁", description="拨保险箱的密码")],
        tests=[Case(name="开始", event={"hook": "command", "command": "开锁", "text": ""}, expected_state={"dials": []},
                    expected_effects=[{"kind": "prompt", "key": "dial0", "title": "保险箱", "style": "number", "low": 0, "high": 9,
                                       "text": "第 1 个转盘拨到几？"}]),
               Case(name="拨对", event={"hook": "answer", "prompt": "dial2", "value": 4, "by": "player"},
                    state={"dials": [3, 1]}, expected_state={"open": True}, expected_effects=[{"kind": "note", "text": "锁开了"}])],
    ).model_copy(update=changes)


def trusted(game: Game, *plugins: Plugin) -> None:
    """Install plugins whose own tests already ran: the handler still runs in the sandbox."""
    for plugin in plugins:
        game.m.extensions[plugin.id] = plugin
        if plugin.id not in game.m.plugins:
            game.m.plugins.append(plugin.id)
        game.plugins.verified.add(plugin.fingerprint)


def party(module: Module, n: int = 2) -> list:
    lead = module.pregens[0]
    return [lead.model_copy(deep=True, update={"id": f"{lead.id}{i}" if i else lead.id, "name": f"{lead.name}{i}" if i else lead.name})
            for i in range(n)]


# -- schema and caps -----------------------------------------------------------------------
def test_panels_shows_and_prompts_are_capped_in_plain_words():
    with pytest.raises(ValidationError, match="12"):
        Panel(key="p", title="太多", rows=[{"text": "x"}] * 13)
    with pytest.raises(ValidationError, match="列"):
        Panel(key="p", title="太宽", rows=[{"kind": "art", "art": "x" * 37}])
    with pytest.raises(ValidationError, match="颜色"):
        Panel(key="p", title="颜色", rows=[{"text": "x", "color": "chartreuse"}])
    with pytest.raises(ValidationError):
        Output(state={}, panels=[{"key": f"p{i}", "title": "页"} for i in range(3)])  # two panels at most
    with pytest.raises(ValidationError, match="20"):
        Effect(kind="show", frames=[{"text": "长", "ms": 8000}] * 3)
    with pytest.raises(ValidationError, match="16"):
        Effect(kind="show", frames=[{"art": "\n".join("x" * 10 for _ in range(17))}])
    with pytest.raises(ValidationError, match="options"):
        Effect(kind="prompt", key="q", text="选一个", options=["只有一个"])
    with pytest.raises(ValidationError, match="board"):
        Effect(kind="prompt", key="q", text="点一格", style="board", board=[["x"] * 9])
    with pytest.raises(ValidationError, match="default"):
        Effect(kind="prompt", key="q", text="选", options=["甲", "乙"], default="丙")
    with pytest.raises(ValidationError, match="hint"):
        Effect(kind="hint", text="很长" * 50)
    with pytest.raises(ValidationError, match="schedule"):
        Effect(kind="schedule", key="dawn", at="明早")
    assert Effect(kind="prompt", key="q", text="点一格", style="board", board=[["a", "b"], ["c"]]).board  # ragged rows are fine


def test_commands_and_answer_powers_need_their_hooks_and_cannot_shadow_the_games_own():
    assert any("command 钩子" in p for p in check(lock(hooks=["answer"])))
    assert any("answer 钩子" in p for p in check(lock(hooks=["command"])))
    assert any("重名" in p for p in check(lock(commands=[Command(name="map", description="地图")])))
    assert not check(lock())


async def test_a_plugin_that_listens_for_answers_must_test_them():
    plugin = lock(tests=[lock().tests[0]])  # only the command
    problems = await verify(plugin)
    assert any("answer" in p for p in problems)
    assert not await verify(lock())


# -- each new effect -------------------------------------------------------------------------
KINDS = '''def handle(event, ctx, api):
    state = dict(ctx["state"])
    who = ctx["acting"]
    if event["hook"] == "turn" and not state.get("done"):
        state["done"] = True
        panel = {"key": "ritual", "title": "仪式倒数", "rows": [
            {"kind": "bar", "text": "烛火", "value": 3, "max": 6, "color": "warning"},
            {"kind": "list", "text": "线索板", "items": ["脚印", "旧报纸"]},
            {"kind": "table", "cells": [["物品", "价钱"], ["煤油", "2 角"]]},
            {"kind": "art", "art": " /\\\\ \\n |##|", "color": "accent"}]}
        return {"state": state, "panels": [panel], "effects": [
            {"kind": "item", "who": who, "key": "铜钥匙", "amount": 1},
            {"kind": "hint", "text": "窗外的乌鸦叫了三声"},
            {"kind": "attitude", "npc": "NPC", "amount": 2, "text": "帮他修好了钟"},
            {"kind": "timer", "key": "candle", "minutes": 30},
            {"kind": "timer", "key": "bell", "rounds": 2},
            {"kind": "schedule", "key": "dawn", "at": "D2 06:00"},
            {"kind": "show", "shape": "stage", "title": "号外", "frames": [{"text": "号外！", "ms": 400}, {"art": "EXTRA", "ms": 400}]},
            {"kind": "show", "shape": "inline", "title": "第二段", "frames": [{"text": "多余的", "ms": 400}]}]}
    if event["hook"] == "timer":
        state["fired"] = list(state.get("fired", [])) + [event["key"]]
        return {"state": state, "effects": [{"kind": "note", "text": "到点了：" + event["key"]}]}
    return {"state": state, "effects": []}
'''


async def test_every_new_effect_lands_where_it_belongs(fake):
    module = Module.load(EXAMPLE)
    npc = module.npcs[0]
    plugin = Plugin(id="kinds", name="样样有", description="什么都来一点。", code=KINDS.replace("NPC", npc.id),
                    hooks=["turn", "timer"], capabilities=["panel", "show", "timer", "item", "hint", "attitude", "note"],
                    tests=[Case(name="x", event={"hook": "timer", "key": "candle"}, expected_state={"fired": ["candle"]},
                                expected_effects=[{"kind": "note", "text": "到点了：candle"}])])
    game, _, events = make(module, fake, {})
    trusted(game, plugin)
    st, turn = game.state, Turn()
    st.fired = [e.id for e in module.events]  # the module's own timed scenes stay out of it
    await game.plugins.run("turn", turn)
    assert "铜钥匙" in st.investigator.inventory  # item
    assert turn.hints == ["〔样样有〕窗外的乌鸦叫了三声"] and "规则提示" in game._report(turn)  # hint, woven in this turn
    fact = next(f for f in st.world.values() if f.npc == npc.id)
    assert fact.kind == "relationship" and "更亲近" in fact.text and "kinds" not in json.dumps(st.world, default=str)  # attitude
    panels = st.plugin_panels["样样有"]
    assert panels[0]["title"] == "仪式倒数" and panels[0]["rows"][0]["value"] == 3  # panel, public by name
    shows = [e for e in events if e["type"] == "plugin_show"]
    assert len(shows) == 1 and shows[0]["shape"] == "stage" and len(shows[0]["frames"]) == 2  # one show a turn
    assert {t["key"] for t in st.plugin_timers} == {"candle", "bell", "dawn"}
    st.minutes += 30
    await game._time(turn)  # game minutes: the candle burns down
    assert st.plugin_data["kinds"]["fired"] == ["candle"]
    st.turn += 2
    await game._time(turn)  # rounds: the bell
    st.minutes = 1440 + 6 * 60
    await game._time(turn)  # a game time: dawn
    assert st.plugin_data["kinds"]["fired"] == ["candle", "bell", "dawn"] and not st.plugin_timers
    assert any(e["type"] == "plugin_effect" and "到点了：dawn" in e["line"] for e in events)


async def test_roll_and_clue_hooks_follow_checks_and_finds(fake):
    code = '''def handle(event, ctx, api):
    seen = list(ctx["state"].get("seen", []))
    seen.append(event["hook"] + ":" + str(event.get("skill", event.get("clue", ""))) + ":" + str(event.get("passed", "")))
    return {"state": {"seen": seen}, "effects": []}
'''
    module = Module.load(EXAMPLE)
    clue = module.clues[0]
    plugin = Plugin(id="watch", name="旁观", description="记下检定和线索。", code=code, hooks=["roll", "clue"], capabilities=[],
                    tests=[Case(name="r", event={"hook": "roll", "skill": "侦查", "passed": True}, expected_state={"seen": ["roll:侦查:True"]}),
                           Case(name="c", event={"hook": "clue", "clue": "x"}, expected_state={"seen": ["clue:x:"]})])
    assert not await verify(plugin)
    from autokeeper.agents.cast import Action, Ruling

    game, _, _ = make(module, fake, {}, location=clue.where)
    trusted(game, plugin)
    fake.queue(1)
    ruling = Ruling(needs_roll=True, skill="侦查", minutes=5, clues_on_success=[clue.id])
    await game.resolve(Action(kind="investigate", summary="仔细看看", target=clue.feature or "四周"), ruling)
    seen = game.state.plugin_data["watch"]["seen"]
    assert seen[0] == "roll:侦查:True" and seen[1] == f"clue:{clue.id}:"


# -- questions and little games ----------------------------------------------------------------
async def test_a_three_dial_lock_loops_prompt_answer_prompt_and_keeps_its_state(fake):
    module = Module.load(EXAMPLE)
    game, _, events = make(module, fake, {})
    trusted(game, lock())
    plugins = game.plugins
    assert await plugins.command("开锁") == ""
    for value in (3, 1):
        prompt = next(e for e in reversed(events) if e["type"] == "plugin_prompt")
        assert "default" not in prompt and "ai" not in prompt  # what stands without an answer stays on the host
        assert "这个回答不行" in await plugins.answer(prompt["id"], "三")  # not a number: refused, nothing happens
        assert await plugins.answer(prompt["id"], value) == ""
    assert game.state.plugin_data["lock"]["dials"] == [3, 1]
    last = next(e for e in reversed(events) if e["type"] == "plugin_prompt")
    assert last["text"] == "第 3 个转盘拨到几？"
    assert await plugins.answer(last["id"], 4) == ""
    assert game.state.plugin_data["lock"]["open"] is True and not game.state.plugin_prompts
    assert "锁开了" in game._late.notes  # told with the next narration
    assert await plugins.answer(last["id"], 4) == "这道题已经结束了"


async def test_a_question_nobody_answers_takes_its_default(fake):
    module = Module.load(EXAMPLE)
    game, _, events = make(module, fake, {})
    trusted(game, lock())
    await game.plugins.command("开锁")
    await game.plugins.expire()  # not yet
    assert game.state.plugin_prompts
    await game.plugins.expire(now=time.time() + 1000)
    assert game.state.plugin_data["lock"]["gave_up"] == "timeout" and not game.state.plugin_prompts
    answered = next(e for e in events if e["type"] == "plugin_answered")
    assert answered["by"] == "timeout" and answered["text"] == "0"


async def test_ai_seats_answer_at_once_by_default_or_at_random(fake):
    module = Module.load(EXAMPLE)
    lead, mate = party(module)
    state = Game.new(module, [lead, mate], human=[lead.id])
    game = Game(module, state, TableCast(), Rules(dice=fake))
    code = '''def handle(event, ctx, api):
    if event["hook"] == "turn":
        return {"state": {}, "effects": [{"kind": "prompt", "key": "pick", "who": ctx["investigators"][1]["id"], "style": "choice",
                "text": "抽一张牌", "options": ["红桃", "黑桃", "方块"], "ai": AI}]}
    return {"state": {"got": event["value"], "by": event["by"]}, "effects": []}
'''
    for policy, expected in (("default", "红桃"), ("random", "方块")):
        plugin = Plugin(id="cards", name="牌局", description="和 NPC 打牌。", code=code.replace("AI", repr(policy)), hooks=["turn", "answer"],
                        capabilities=["prompt"], tests=[Case(name="a", event={"hook": "answer", "value": "红桃", "by": "ai"},
                                                             expected_state={"got": "红桃"})])
        state.plugin_receipts.clear()
        trusted(game, plugin)
        if policy == "random":
            fake.queue_totals(3)
        await game.plugins.run("turn", Turn())
        assert state.plugin_data["cards"] == {"got": expected, "by": "ai"} and not state.plugin_prompts
        assert not fake.totals


async def test_one_open_question_per_player_and_a_game_stops_at_thirty_steps(fake):
    module = Module.load(EXAMPLE)
    game, _, events = make(module, fake, {})
    forever = '''def handle(event, ctx, api):
    return {"state": {"n": event.get("step", 0)}, "effects": [{"kind": "prompt", "key": "again", "text": "再来？", "options": ["来", "不来"]}]}
'''
    plugin = Plugin(id="forever", name="没完没了", description="一直问。", code=forever, hooks=["command", "answer"], capabilities=["prompt"],
                    commands=[Command(name="再来", description="再来一局")], tests=[Case(name="x", event={"hook": "answer"}, expected_effects=[
                        {"kind": "prompt", "key": "again", "text": "再来？", "options": ["来", "不来"]}]),
                        Case(name="y", event={"hook": "command"}, expected_effects=[{"kind": "prompt", "key": "again", "text": "再来？", "options": ["来", "不来"]}])])
    trusted(game, plugin, lock())
    await game.plugins.command("开锁")
    await game.plugins.command("再来")  # another plugin's question waits behind the open one
    assert [p["open"] for p in game.state.plugin_prompts] == [True, False]
    await game.plugins.command("开锁")  # the same plugin again: its new question replaces the old
    assert [p["plugin"] for p in game.state.plugin_prompts] == ["lock", "forever"]
    assert [p["open"] for p in game.state.plugin_prompts] == [True, False]
    lock_prompt = game.plugins.open_prompts()[0]
    await game.plugins.answer(lock_prompt["id"], None, by="skip")
    for _ in range(40):
        open_ = game.plugins.open_prompts()
        if not open_:
            break
        await game.plugins.answer(open_[0]["id"], "来")
    assert game.state.plugin_data["forever"]["n"] == 30 and not game.plugins.open_prompts()
    assert any(e["type"] == "plugin_effect" and "到此为止" in e["line"] for e in events)


async def test_an_answer_is_only_data_for_the_sandbox(fake):
    """Whatever a player types reaches the plugin as a string; the sandbox wall is unchanged."""
    module = Module.load(EXAMPLE)
    game, _, _ = make(module, fake, {})
    echo = '''def handle(event, ctx, api):
    if event["hook"] == "command":
        return {"state": {}, "effects": [{"kind": "prompt", "key": "say", "style": "text", "text": "说点什么"}]}
    return {"state": {"said": event["value"]}, "effects": []}
'''
    plugin = Plugin(id="echo", name="回声", description="复述。", code=echo, hooks=["command", "answer"], capabilities=["prompt"],
                    commands=[Command(name="回声", description="说一句")],
                    tests=[Case(name="a", event={"hook": "answer", "value": "你好"}, expected_state={"said": "你好"}),
                           Case(name="c", event={"hook": "command"}, expected_effects=[{"kind": "prompt", "key": "say", "style": "text", "text": "说点什么"}])])
    trusted(game, plugin)
    await game.plugins.command("回声")
    sneaky = "__import__('os').system('echo hi')"
    await game.plugins.answer(game.plugins.open_prompts()[0]["id"], sneaky)
    assert game.state.plugin_data["echo"] == {"said": sneaky}
    assert check(plugin.model_copy(update={"code": echo + "\nx = open('a')"}))  # the AST wall still stands


# -- the director's cap -------------------------------------------------------------------------
def test_the_director_writes_at_most_six_plugins_a_game_but_may_upgrade_them():
    module = Module.load(EXAMPLE)
    for i in range(6):
        improv.apply(module, Improv(kind="展开", reason="x", plugins=[lock(id=f"lock{i}")]))
    assert len(module.directed) == 6
    problems = improv.check(module, Improv(kind="展开", reason="x", plugins=[lock(id="lock9")]), at=module.start)
    assert any("6 个插件" in p for p in problems)
    assert not improv.check(module, Improv(kind="展开", reason="x", plugins=[lock(id="lock0", version="0.2.0")]), at=module.start)


# -- the screen -----------------------------------------------------------------------------------
PANELS = {"仪式": [{"key": "ritual", "title": "仪式倒数", "rows": [
    {"kind": "bar", "text": "烛火", "value": 4, "max": 6, "color": "warning"},
    {"kind": "text", "text": "钟敲十二下时仪式开始"},
    {"kind": "list", "text": "线索板", "items": ["钟楼的脚印", "缺页的日记"]},
    {"kind": "table", "cells": [["人", "去向"], ["守钟人", "钟楼"]]},
    {"kind": "art", "art": "  )\n (_)\n |=|", "color": "accent"}]}]}


def rendered(renderable) -> str:
    from rich.console import Console

    console = Console(file=io.StringIO(), width=36, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def test_panels_render_with_their_plugins_name_and_name_their_tab():
    text = rendered(panel_view(PANELS))
    assert "〔仪式〕" in text and "仪式倒数" in text and "████████░░░░ 4/6" in text and "钟楼的脚印" in text and "守钟人" in text
    assert tab_label(PANELS) == "仪式倒数"
    assert tab_label({**PANELS, "电台": [{"key": "dial", "title": "频率", "rows": []}]}) == "更多"


@pytest.mark.usefixtures("canned")
async def test_the_plugins_page_shows_its_panels_in_the_right_column():
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="panel", human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        tabs = screen.query_one("#pages")
        assert not tabs.get_tab("plugins-page").display  # no panel yet: no page
        state.plugin_panels = PANELS
        await screen.handle({"type": "plugin_panel", "name": "仪式"})
        await pilot.pause()
        assert tabs.get_tab("plugins-page").display and str(tabs.get_tab("plugins-page").label) == "仪式倒数"
        screen.action_page("plugins")
        await pilot.pause()
        page = rendered(screen.query_one("#plugin-panels").body.content)
        assert "烛火" in page and "〔仪式〕" in page
        app.save_screenshot("plugin-panel.svg", path=str(paths.DATA))
        state.plugin_panels = {}
        await screen.handle({"type": "plugin_panel", "name": "仪式"})
        await pilot.pause()
        assert not tabs.get_tab("plugins-page").display and tabs.active != "plugins-page"


SHOW = {"type": "plugin_show", "name": "号外", "title": "号外！号外！", "shape": "stage", "frames": [
    {"art": "+--------------+\n| ARNOLDSBURG  |\n|   GAZETTE    |\n+--------------+", "text": "", "ms": 400},
    {"art": "墓地夜半狂欢？", "text": "警方搜遍公墓，一无所获", "ms": 3000, "color": "warning"}]}


@pytest.mark.usefixtures("canned")
async def test_a_show_plays_over_the_scene_can_be_skipped_and_shows_only_its_end_without_animations():
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="show", human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        scene = screen.query_one("#scene")
        await screen.handle(SHOW)
        await pilot.pause()
        player = screen.query_one(ShowPlayer)
        assert not scene.display and player.index == 0 and "ARNOLDSBURG" in str(player.render())
        assert await until(pilot, lambda: player.index == 1)  # the next frame comes by itself
        app.save_screenshot("plugin-show.svg", path=str(paths.DATA))
        assert "〔号外〕" in story_text(screen)  # labelled in the story too
        screen.action_menu()  # Esc: skip
        await pilot.pause()
        assert not screen.query(ShowPlayer) and scene.display
        scene.effects = False  # animations off: the last frame only
        await screen.handle({**SHOW, "shape": "inline"})
        await pilot.pause()
        inline = screen.query_one(ShowPlayer)
        assert inline.index == 1 and inline.done and "警方搜遍公墓" in str(inline.render()) and "ARNOLDSBURG" not in str(inline.render())


@pytest.mark.usefixtures("canned")
async def test_a_mini_game_comes_up_after_the_turn_and_answers_go_to_the_game():
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="game", human=[lead.id])
    board = '''def handle(event, ctx, api):
    if event["hook"] == "command":
        return {"state": {}, "effects": [{"kind": "prompt", "key": "cell", "title": "通灵板", "style": "board",
                "text": "指针在板上打转。点一个字母，看它停在哪里。",
                "board": [["Y", "E", "S"], ["N", "O", " "], ["A", "B", "C"]]}]}
    return {"state": {"cell": event["value"], "by": event["by"]}, "effects": [{"kind": "note", "text": "指针停住了"}]}
'''
    plugin = Plugin(id="ouija", name="通灵板", description="请亡魂指字。", code=board, hooks=["command", "answer"], capabilities=["prompt", "note"],
                    commands=[Command(name="通灵", description="把手指放在指针上")],
                    tests=[Case(name="a", event={"hook": "answer", "value": [0, 0], "by": "player"}, expected_state={"cell": [0, 0]},
                                expected_effects=[{"kind": "note", "text": "指针停住了"}]),
                           Case(name="c", event={"hook": "command"}, expected_effects=[{"kind": "prompt", "key": "cell", "title": "通灵板", "style": "board",
                                "text": "指针在板上打转。点一个字母，看它停在哪里。", "board": [["Y", "E", "S"], ["N", "O", " "], ["A", "B", "C"]]}])])
    m.plugins, m.extensions = [plugin.id], {plugin.id: plugin}
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        screen.game.plugins.verified.add(plugin.fingerprint)
        assert "/通灵" in screen._suggester()._suggestions  # the command box knows the command
        screen.send_line("/通灵")
        assert await until(pilot, lambda: isinstance(app.screen, PromptModal), tries=150)
        modal = app.screen
        shown = "".join(str(w.render()) for w in modal.query(Static))
        assert "〔通灵板〕" in shown and "点一个字母" in shown and "没答就按默认算" in shown
        app.save_screenshot("plugin-prompt.svg", path=str(paths.DATA))
        modal.query_one("#cell-1-1", Button).press()
        assert await until(pilot, lambda: state.plugin_data.get("ouija", {}).get("cell") == [1, 1], tries=150)
        assert await until(pilot, lambda: "指针停住了" in story_text(screen), tries=150)
        assert state.plugin_data["ouija"]["by"] == "player" and app.screen is screen
        screen.send_line("/通灵")
        assert await until(pilot, lambda: isinstance(app.screen, PromptModal), tries=150)
        await pilot.press("escape")  # 先不玩
        assert await until(pilot, lambda: state.plugin_data["ouija"]["by"] == "skip", tries=150)


# -- a LAN table ---------------------------------------------------------------------------------
def lan(fake) -> tuple[Hub, list[tuple[str, dict]]]:
    module = Module.load(EXAMPLE)
    lead, mate = party(module)
    state = Game.new(module, [lead, mate], human=[lead.id, mate.id])
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    sent: list[tuple[str, dict]] = []

    async def send(cid, msg):
        sent.append((cid, msg))

    hub._send = send  # type: ignore[method-assign]
    hub.clients = {"c1": None, "c2": None}  # type: ignore[dict-item]
    hub.sits = {lead.id: "c1", mate.id: "c2"}
    trusted(hub.game, lock())
    return hub, sent


async def test_at_a_lan_table_a_question_goes_to_its_player_and_the_answer_back_to_the_host(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    hub, sent = lan(fake)
    mate = hub.state.investigators[1].id
    await hub._on("c2", {"t": "command", "name": "开锁", "text": ""})
    await hub._plays()
    prompts = [(cid, m["ev"]) for cid, m in sent if m.get("t") == "event" and m["ev"].get("type") == "plugin_prompt"]
    assert [cid for cid, _ in prompts] == ["c2"] and prompts[0][1]["who"] == mate  # to the player asked, nobody else
    assert "default" not in prompts[0][1] and "plugin" not in prompts[0][1]
    pid = prompts[0][1]["id"]
    sent.clear()
    await hub._on("c1", {"t": "answer", "prompt": pid, "value": 3})  # someone else's question
    await hub._plays()
    assert ("c1", {"t": "error", "text": "这道题不是问你的"}) in sent and "dials" in hub.state.plugin_data["lock"]
    assert hub.state.plugin_data["lock"]["dials"] == []
    await hub._on("c2", {"t": "answer", "prompt": pid, "value": 3})
    await hub._plays()
    assert hub.state.plugin_data["lock"]["dials"] == [3]
    nxt = [m["ev"] for cid, m in sent if cid == "c2" and m.get("t") == "event" and m["ev"].get("type") == "plugin_prompt"]
    assert nxt and nxt[-1]["text"] == "第 2 个转盘拨到几？"
    hub.sits[mate] = None  # they left: the question takes its default
    await hub.game.plugins.expire(now=0)  # nothing is due yet; leaving is enough
    await hub.game.plugins.command("开锁", who=mate)
    assert hub.state.plugin_data["lock"]["gave_up"] == "ai" and not hub.game.plugins.open_prompts()


async def test_lan_screens_get_panels_shows_and_commands_but_no_hidden_plugin_state(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    hub, sent = lan(fake)
    hub.state.plugin_panels = PANELS
    await hub.game.plugins.command("开锁", who=hub.state.investigators[0].id)
    hub.state.plugin_timers = [{"plugin": "lock", "key": "secret_timer", "minutes": 99}]
    view = hub._view()
    assert view["plugin_panels"] == PANELS
    for private in ("plugin_prompts", "plugin_timers", "plugin_marks", "plugin_serial", "plugin_data"):
        assert private not in view
    welcome = json.dumps(hub._welcome("c1"), ensure_ascii=False)
    assert "secret_timer" not in welcome and "dial0" not in welcome and '"lock"' not in welcome
    info = public_plugins(hub.m, hub.state)[0]
    assert info["commands"] == [["开锁", "拨保险箱的密码"]] and "请你回答问题或玩个小游戏" in info["does"] and "id" not in info
    sent.clear()
    await hub._event({"type": "plugin_show", "plugin": "lock", "name": "保险箱", "title": "咔哒", "shape": "inline", "frames": []})
    shows = [m["ev"] for _, m in sent if m.get("t") == "event"]
    assert len(shows) == 2 and all("plugin" not in ev for ev in shows)  # to everyone, without the id


# -- the bundled example ---------------------------------------------------------------------------
async def test_the_paper_chase_bookshelf_is_a_panel_and_a_little_game_that_spoils_nothing(fake):
    module = Module.load(paths.MODULES / "zhuishuren")
    shelf = module.extensions["shujia"]
    assert not await verify(shelf)  # its own tests pass in the sandbox
    assert not shelf.flags and "clue" not in shelf.capabilities and "flag" not in shelf.capabilities  # nothing to finish the case with
    game, _, events = make(module, fake, {}, location="shufang")
    st = game.state
    st.human = [st.investigator.id]
    turn = Turn(clues=["c_shelf_gap"])
    await game.plugins.settled(turn)  # the shelf's gaps are found
    assert st.plugin_panels["书房的旧书架"][0]["title"] == "旧书架"
    assert await game.plugins.command("理书架") == ""
    for book in ("红字", "草叶集", "白鲸", "瓦尔登湖"):  # one wrong guess on the way
        await game.plugins.answer(game.plugins.open_prompts()[0]["id"], book)
    data = st.plugin_data["shujia"]
    assert data["done"] and data["tries"] == 1 and data["placed"] == ["红字", "白鲸", "瓦尔登湖", "草叶集"]
    assert any(f.npc == "thomas" for f in st.world.values()) and game._late.hints
    assert any(e["type"] == "plugin_show" for e in events)
    shown = json.dumps([e for e in events if e["type"].startswith("plugin_")], ensure_ascii=False)
    for secret in ("食尸鬼", "道格拉斯本人", "隧道", "地下"):  # nothing it shows gives the case away
        assert secret not in shown
