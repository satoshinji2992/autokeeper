"""Player journeys and interrupted turns: consequences outlive models and screens."""

from __future__ import annotations

from itertools import pairwise

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper import config
from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import graph, world
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState, WorldChange
from autokeeper.module.schema import Item, Module
from autokeeper.net.hub import Hub, table
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, make
from .test_tui import canned, until  # noqa: F401


@pytest.fixture
def module():
    return Module.load(config.EXAMPLE)


async def test_walking_the_same_roads_never_changes_distance(fake):
    m = Module.load(config.MODULES / "zhuishuren")
    game, *_ = make(m, fake, {})
    route = game._route(m.start, "jinboer_jia")
    direct = game._travel(route[0], route[-1])
    assert direct == sum(game._travel(a, b) for a, b in pairwise(route)) == 45
    assert direct == game._travel(route[-1], route[0])
    await game.close()


async def test_strolling_does_not_complete_the_action_at_the_far_destination(fake):
    m = Module.load(config.MODULES / "zhuishuren")
    game, _, events = make(m, fake, {})
    action = Action(kind="investigate", summary="沿路走走，到金博尔家看看书房", destination="jinboer_jia", travel="stroll")
    ruling = await game.rule(action)
    turn = await game.resolve(action, ruling)
    assert game.state.location == "zhenzhongxin" and game.state.journey == "jinboer_jia"
    assert not game.state.clues and not any(e["type"] == "dice" for e in events)
    assert "不能替玩家继续赶路" in game._report(turn)
    # The player is free to sit here instead of finishing the journey.
    await game.resolve(Action(kind="daily", summary="坐在广场吃早饭"), Ruling(needs_roll=False, minutes=20))
    assert game.state.location == "zhenzhongxin" and "jinboer_jia" not in game.state.visited
    await game.close()


async def test_lending_returning_and_promises_survive_a_long_saved_conversation(module, fake):
    game, *_ = make(module, fake, {}, location="tower")
    st, inv = game.state, game.state.investigator
    inv.inventory.append("雨衣")
    world.apply(module, st, [WorldChange(kind="transfer", key="lend_coat", text="你把雨衣借给守钟人。",
                                       item="雨衣", owner="warden", npc="warden")], source="arbiter")
    assert "雨衣" not in inv.inventory and world.owner(module, st, "雨衣") == "warden"
    world.apply(module, st, [WorldChange(kind="promise", key="evening", npc="warden", text="今晚我在塔门口等你。")],
                source="warden", spoken="（擦了擦手）今晚我在塔门口等你。")
    for i in range(60):
        world.apply(module, st, [WorldChange(kind="fact", key=f"chat_{i}", text=f"聊了第{i}件日常小事。")], source="arbiter")
    loaded = GameState.model_validate_json(st.model_dump_json())
    assert "今晚我在塔门口等你" in world.context(loaded, npc="warden")
    assert "chat_59" not in world.context(loaded, npc="warden")  # another person did not overhear it
    world.apply(module, loaded, [WorldChange(kind="transfer", key="return_coat", text="雨衣还你，谢谢。",
                                           npc="warden", item="雨衣", owner=inv.id)], source="warden", spoken="雨衣还你，谢谢。")
    assert loaded.investigator.inventory.count("雨衣") == 1
    world.apply(module, loaded, [WorldChange(kind="fulfill", key="evening", text="在约定的塔门口碰面。", npc="warden")], source="arbiter")
    assert loaded.world["evening"].status == "fulfilled"
    await game.close()


async def test_objects_cannot_be_conjured_or_taken_from_an_absent_owner(module, fake):
    game, *_ = make(module, fake, {}, location="square")
    module.items.append(Item(id="coat", name="雨衣", description="油布雨衣", where="tower"))
    st = game.state
    world.apply(module, st, [WorldChange(kind="transfer", key="theft", text="拿走远处的雨衣", item="coat", owner=st.investigator.id),
                            WorldChange(kind="transfer", key="magic", text="获得金条", item="gold", owner=st.investigator.id)], source="arbiter")
    assert not st.world and not st.item_owners and "金条" not in st.investigator.inventory
    world.apply(module, st, [WorldChange(kind="promise", key="invented", text="我把秘密全告诉你。", npc="preacher")],
                source="preacher", spoken="今天天气很好。")
    assert not st.world
    await game.close()


async def test_greeting_is_a_conversation_not_three_more_model_calls(module, fake):
    chat = Action(kind="daily", summary="周衡向守钟人问好", npc="warden", speech="早上好。", casual=True)
    game, cast, _ = make(module, fake, {"你好": (chat, Ruling(needs_roll=False))}, location="tower")
    start = game.state.minutes
    app = graph.build(game)
    await app.ainvoke({"input": "你好"})
    assert len(cast.heard) == len(cast.npc_calls) == 1 and not cast.ruled and not cast.reports
    assert game.state.minutes == start and not game.state.clues and not game.state.pending_turn
    await game.close()


class Interrupted(FakeCast):
    failures = 1

    async def keeper(self, *args, **kwargs):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("provider unavailable after the dice already fell")
        return await super().keeper(*args, **kwargs)


async def test_a_saved_failed_narration_resumes_without_rolls_time_or_npc_duplicates(module, fake):
    action = Action(kind="talk", summary="周衡问守钟人当天的工作", npc="warden", speech="今天的钟怎么修？")
    ruling = Ruling(needs_roll=True, skill="说服", minutes=5)
    game, _, _events = make(module, fake, {}, location="tower")
    game.cast = Interrupted({"问工作": (action, ruling)})
    saved = []
    game.checkpoint = lambda: saved.append(game.state.model_dump_json())
    fake.queue(5)
    before = game.state.minutes
    app = graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": "journey"}}
    with pytest.raises(ConnectionError):
        await app.ainvoke({"input": "问工作"}, thread)
    assert game.state.pending_turn["talk_done"] and len(game.cast.npc_calls) == 1
    loaded = GameState.model_validate_json(saved[-1])
    assert loaded.minutes == before + 5 and len(loaded.checks) == 1
    await game.close()
    # A completely new graph, process and cast can resume the serialized stage journal.
    cast = FakeCast({})
    again = Game(Module.load(config.EXAMPLE), loaded, cast, Rules(dice=fake))
    other = graph.build(again)
    await other.ainvoke({"input": "/retry"})
    assert not cast.heard and not cast.ruled and not cast.npc_calls and len(cast.reports) == 1
    assert loaded.minutes == before + 5 and len(loaded.checks) == 1
    assert len([e for e in loaded.log if e.kind == "npc"]) == 1
    assert not loaded.pending_turn
    await again.close()


async def test_failed_intent_does_not_consume_a_turn_and_new_actions_cannot_replace_pending_ones(module, fake):
    game, cast, _ = make(module, fake, {})
    app = graph.build(game)
    with pytest.raises(KeyError):
        await app.ainvoke({"input": "问路"})
    assert game.state.turn == 0 and not game.state.log
    with pytest.raises(ValueError, match="/retry"):
        await app.ainvoke({"input": "换一件事"})
    cast.script["问路"] = (Action(kind="daily", summary="看看路牌"), Ruling(needs_roll=False))
    await app.ainvoke({"input": "/retry"})
    assert game.state.turn == 1 and len([e for e in game.state.log if e.kind == "player"]) == 1
    await game.close()


async def test_resuming_an_ai_companion_uses_the_original_investigator(module, fake):
    action = Action(kind="investigate", summary="同伴观察钟楼")
    ruling = Ruling(needs_roll=False, minutes=5)
    game, *_ = make(module, fake, {})
    friend = game.state.investigator.model_copy(deep=True, update={"id": "friend", "name": "同行者"})
    game.state.investigators.append(friend)
    game.state.active = 1
    game.cast = Interrupted({"观察": (action, ruling)})
    app = graph.build(game)
    with pytest.raises(ConnectionError):
        await app.ainvoke({"input": "观察"})
    assert game.state.pending_turn["active"] == 1
    game.state.active = 0  # the human's screen regains control while the provider is down
    await app.ainvoke({"input": "/retry"})
    assert game.state.active == 1 and len(game.cast.heard) == 1
    assert [e.who for e in game.state.log if e.kind == "player"] == ["同行者"]
    await game.close()


def test_network_projection_omits_unseen_content_and_its_art(fake):
    m = Module.load(config.MODULES / "zhuishuren")
    st = Game.new(m, m.pregens)
    book = table(m, st)
    assert "suidao" not in {loc["id"] for loc in book["locations"]}
    assert all(not loc["read_aloud"] and not loc["features"] for loc in book["locations"] if loc["id"] != st.location)
    assert {n["id"] for n in book["npcs"]} == {"zhanwuyuan"}
    assert not book["events"] and not book["clues"] and not book["handouts"] and not book["flags"]
    hub = Hub(config.MODULES / "zhuishuren", state=st, cast=FakeCast({}), rules=Rules(dice=fake))
    welcome = hub._welcome("player")
    assert "suidao" not in welcome["art"] and "douglas" not in welcome["art"]
    assert "pending_turn" not in welcome["state"]


async def test_narrator_never_receives_the_unresolved_mystery(module, fake):
    game, _cast, _ = make(module, fake, {}, location="tower")
    turn = await game.resolve(Action(kind="move", summary="走进钟楼", destination="tower"), Ruling(needs_roll=False))
    turn.arrived, turn.first_visit = "tower", True
    report = game._report(turn)
    assert module.truth.strip() not in report
    assert module.location("tower").keeper.strip() not in report
    assert module.clue("c_diary").text.strip() not in report
    await game.close()


async def test_twenty_turns_of_daily_life_keep_promises_and_leave_the_case_open(module, fake):
    script = {}
    for i in range(20):
        script[f"闲聊{i}"] = (Action(kind="daily", summary=f"周衡谈起第{i}件生活小事", npc="warden", casual=True),
                                Ruling(needs_roll=False))
    game, cast, _ = make(module, fake, script, location="tower")
    world.apply(module, game.state, [WorldChange(kind="promise", key="visit", npc="warden", text="晚上来找我。")],
                source="warden", spoken="晚上来找我。")
    app = graph.build(game)
    for text in script:
        await app.ainvoke({"input": text})
    assert game.state.turn == 20 and not game.state.ending and not game.state.clues
    assert "晚上来找我" in world.context(game.state, npc="warden")
    assert len(cast.npc_calls) == 20 and not cast.ruled and not cast.reports
    await game.close()


@pytest.mark.usefixtures("canned")
async def test_the_screen_recovers_from_a_provider_failure_and_accepts_retry(module):
    from textual.widgets import Input

    from autokeeper.tui.app import AutoKeeperApp, GameScreen

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(module, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.cast is not None and not screen.busy)
        calls = []

        async def intent(*args, **kwargs):
            calls.append("intent")
            return Action(kind="daily", summary="周衡坐在广场歇脚")

        async def ruling(*args, **kwargs):
            calls.append("rule")
            return Ruling(needs_roll=False, minutes=5)

        async def keeper(*args, **kwargs):
            calls.append("keeper")
            if calls.count("keeper") == 1:
                raise ConnectionError("provider offline")
            return "广场上的早点铺刚刚开门。"

        screen.cast.intent, screen.cast.arbiter, screen.cast.keeper = intent, ruling, keeper
        screen.query_one(Input).value = "先坐一会儿"
        await pilot.press("enter")
        assert await until(pilot, lambda: state.pending_turn.get("talk_done") and not screen.busy)
        clock = state.minutes
        screen.query_one(Input).value = "/retry"
        await pilot.press("enter")
        assert await until(pilot, lambda: not state.pending_turn and not screen.busy)
        assert calls == ["intent", "rule", "keeper", "keeper"]
        assert state.minutes == clock and state.turn == 1
