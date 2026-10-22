"""Player journeys and interrupted turns: consequences outlive models and screens."""

from __future__ import annotations

from itertools import pairwise

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper import paths
from autokeeper.agents.cast import Action, BeforeTravel, Ruling
from autokeeper.engine import graph, world
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import CheckRecord, GameState, WorldChange
from autokeeper.module.schema import Check as ModuleCheck
from autokeeper.module.schema import Item, Module
from autokeeper.net.hub import Hub, table
from autokeeper.rules.coc import Rules
from autokeeper.world import ops as O
from autokeeper.world.ops import Follow

from .test_engine import FakeCast, make, run_turn
from .test_tui import canned, until  # noqa: F401


@pytest.fixture
def module():
    return Module.load(paths.EXAMPLE)


async def test_walking_the_same_roads_never_changes_distance(fake):
    m = Module.load(paths.MODULES / "zhuishuren")
    game, *_ = make(m, fake, {})
    route = game._route(m.start, "jinboer_jia")
    direct = game._travel(route[0], route[-1])
    assert direct == sum(game._travel(a, b) for a, b in pairwise(route)) == 45
    assert direct == game._travel(route[-1], route[0])
    await game.close()


async def test_strolling_does_not_complete_the_action_at_the_far_destination(fake):
    m = Module.load(paths.MODULES / "zhuishuren")
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


@pytest.mark.parametrize("value", [10, 90])
async def test_observing_an_npc_neither_starts_a_conversation_nor_unlocks_testimony(module, fake, value):
    action = Action(kind="talk", summary="周衡观察守钟人回答时的神态", npc="warden", approach="心理学")
    # An incorrect arbiter must not make psychology bypass the persuasion gate.
    ruling = Ruling(needs_roll=True, skill="心理学", clues_on_success=["c_diary"], auto_clues=["c_diary"])
    game, cast, _ = make(module, fake, {"观察他": (action, ruling)}, location="tower")
    fake.queue(value)
    await run_turn(graph.build(game, InMemorySaver()), "观察他", {"configurable": {"thread_id": "psychology"}}, resume="drop")
    assert not cast.npc_calls and not game.state.clues and not game.state.npc("warden").unlocked
    assert not game.state.pending_push and not game.state.handouts
    assert len(cast.reports) == 1 and "心理学" in cast.reports[0]
    await game.close()


async def test_an_explicit_psychology_clue_remains_obtainable(module, fake):
    module.npc("warden").gates["c_diary"] = ModuleCheck(skill="心理学")
    action = Action(kind="talk", summary="周衡留意守钟人谈到日记时的反应", npc="warden", approach="心理学")
    ruling = Ruling(needs_roll=True, skill="心理学", clues_on_success=["c_diary"])
    game, cast, _ = make(module, fake, {"观察": (action, ruling)}, location="tower")
    fake.queue(10)
    await graph.build(game).ainvoke({"input": "观察"})
    assert game.state.clues == ["c_diary"] and not cast.npc_calls
    assert "c_diary" in game.state.npc("warden").unlocked
    await game.close()


async def test_check_results_do_not_supply_an_npc_with_unspoken_observations(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    action = Action(kind="talk", summary="周衡问守钟人几点关门", speech="几点关门？", npc="warden", approach="心理学")
    fake.queue(10)
    check = game.rules.check(game.state.investigator, "心理学")
    turn = Turn(action=action, checks=[check])
    message = game._npc_message(turn, "warden")
    assert "几点关门？" in message and "心理学" not in message
    assert "被拆穿" not in message and "检定：" not in message
    # A repeated private observation must not be turned into a repeated persuasion either.
    turn.checks.clear()
    turn.earlier = CheckRecord(turn=0, who=game.state.investigator.id, at="tower", skill="心理学", npc="warden",
                               text=check.describe(), passed=True, minutes=game.state.minutes)
    assert "检定：" not in game._npc_message(turn, "warden")
    await game.close()


async def test_an_npc_keeps_permission_to_discuss_clues_unlocked_on_previous_turns(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    game.state.npc("warden").unlocked.append("c_diary")
    game.state.clues.append("c_diary")
    turn = Turn(action=Action(kind="talk", summary="周衡追问刚才的日记", speech="你刚才说的是什么意思？", npc="warden"))
    message = game._npc_message(turn, "warden")
    assert "已解锁" in message and "c_diary" in message
    assert "别说出门槛线索" not in message
    await game.close()


async def test_narration_receives_current_inventory_and_ignores_legacy_first_beats(module, fake):
    game, _, _ = make(module, fake, {})
    game.state.investigator.inventory = ["笔记本"]
    turn = Turn(action=Action(kind="other", summary="周衡想挖开墓穴", echo="周衡拿起铲子"))
    report = game._report(turn)
    assert "【实际持有】笔记本" in report and "周衡拿起铲子" not in report
    game.state.investigator.inventory.clear()
    assert "【实际持有】未登记随身物品" in game._report(turn)
    await game.close()


async def test_an_explicit_destination_overrides_an_intermediate_exit_and_default_strolling(fake):
    m = Module.load(paths.MODULES / "zhuishuren")
    wrong = Action(kind="move", summary="出门去公墓与托马斯会合", destination="jiefang", travel="stroll")
    game, _, _ = make(m, fake, {"出门去公墓和托马斯会合": (wrong, Ruling(needs_roll=False))}, location="jinboer_jia")
    action = await game.parse("出门去公墓和托马斯会合")
    assert action.destination == "mudi" and action.travel == "direct"
    turn = await game.resolve(action, await game.rule(action))
    assert turn.arrived == "mudi" and game.state.location == "mudi" and not game.state.journey
    await game.close()


async def test_the_first_stop_of_a_plan_stays_the_destination(fake):
    m = Module.load(paths.MODULES / "zhuishuren")
    text = "先去书房找托马斯，再去公墓"
    first = Action(kind="move", summary="先去书房，之后再去公墓", destination="shufang")
    game, _, _ = make(m, fake, {text: (first, Ruling(needs_roll=False))}, location="jinboer_jia")
    assert (await game.parse(text)).destination == "shufang"
    await game.close()


async def test_a_destination_in_quoted_speech_does_not_change_where_the_player_is_going(fake):
    m = Module.load(paths.MODULES / "zhuishuren")
    text = "我去书房问托马斯：你去公墓吗？"
    action = Action(kind="talk", summary="去书房询问托马斯", destination="shufang", npc="thomas", speech="你去公墓吗？")
    game, _, _ = make(m, fake, {text: (action, Ruling(needs_roll=False))}, location="jinboer_jia")
    assert (await game.parse(text)).destination == "shufang"
    await game.close()


async def test_private_instructions_finish_before_the_group_departs_and_survive_reload(module, fake):
    module.npc("preacher").location = "tower"
    action = Action(kind="move", summary="先私下交代布道者留意门口，再和守钟人去报社", destination="office", companions=["warden"],
                    before=[BeforeTravel(npc="preacher", speech="请留意门口，不要让别人知道。", private=True)])
    game, cast, _ = make(module, fake, {"一起出发": (action, Ruling(needs_roll=False))}, location="tower")
    calls = []

    async def answer(state, nid, message, **kwargs):
        calls.append((nid, state.location, kwargs))
        if nid == "warden":
            assert game.world.apply(Follow(npc=nid, actor=nid, leader=state.investigator.id, destination="office")).ok
            return "好，我和你去报社。"
        return "明白，我会留意。"

    cast.npc = answer
    await graph.build(game).ainvoke({"input": "一起出发"})
    assert [c[0] for c in calls] == ["preacher", "warden"] and all(c[1] == "tower" for c in calls)
    assert calls[0][2]["private"] and calls[1][2]["travel"]
    private = next(e for e in game.world.rec.events if "不要让别人知道" in e.text)
    assert private.visibility == "private" and "warden" not in private.witnesses
    assert game.state.location == "office" and module.npc("warden").location == "office"
    assert module.npc("preacher").location == "tower"
    assert not game.world.rec.entities["warden"].following
    loaded = GameState.model_validate_json(game.state.model_dump_json())
    assert loaded.record.entities["warden"].at == "office"
    assert "老守钟人" in cast.reports[-1] and "一同抵达" in cast.reports[-1]
    await game.close()


async def test_declining_a_companion_request_does_not_send_the_player_off_alone(module, fake):
    action = Action(kind="move", summary="和守钟人去广场", destination="square", companions=["warden"])
    game, cast, _ = make(module, fake, {"一起走": (action, Ruling(needs_roll=False))}, location="tower")

    async def refuse(*args, **kwargs):
        return "现在不行，钟还没修完。"

    cast.npc = refuse
    before = game.state.minutes
    await graph.build(game).ainvoke({"input": "一起走"})
    assert game.state.location == "tower" and game.state.minutes == before
    assert "尚未同意同行" in cast.reports[-1] and "尚未出发" in cast.reports[-1]
    assert not cast.ruled
    await game.close()


async def test_a_predeparture_failure_retries_only_the_unfinished_conversation(module, fake):
    module.npc("preacher").location = "tower"
    action = Action(kind="move", summary="告诉布道者后邀请守钟人同行", destination="square", companions=["warden"],
                    before=[BeforeTravel(npc="preacher", speech="替我留意一下门口。", private=True)])
    game, cast, _ = make(module, fake, {"出发": (action, Ruling(needs_roll=False))}, location="tower")
    calls = []

    async def answer(state, nid, message, **kwargs):
        calls.append(nid)
        if nid == "warden" and calls.count(nid) == 1:
            raise ConnectionError("consent response interrupted")
        if nid == "warden":
            game.world.apply(Follow(npc=nid, actor=nid, leader=state.investigator.id, destination="square"))
        return "好。"

    cast.npc = answer
    app = graph.build(game)
    with pytest.raises(ConnectionError):
        await app.ainvoke({"input": "出发"})
    assert game.state.location == "tower"
    # Rebuild the graph from the saved stage journal, as after a process restart.
    await graph.build(game).ainvoke({"input": "/retry"})
    assert calls == ["preacher", "warden", "warden"] and game.state.location == "square"
    assert len([e for e in game.state.log if e.kind == "npc"]) == 2
    await game.close()


async def test_explicit_solo_travel_does_not_take_an_agreed_companion(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    assert game.world.apply(Follow(npc="warden", actor="warden", leader=game.state.investigator.id, destination="square")).ok
    await game.resolve(Action(kind="move", summary="独自去广场", destination="square", alone=True), Ruling(needs_roll=False))
    assert module.npc("warden").location == "tower" and not game.world.rec.entities["warden"].following
    await game.close()


async def test_an_agreed_companion_comes_along_a_detour_until_the_place_it_agreed_to(module, fake):
    module.npc("warden").location = "square"
    game, _, _ = make(module, fake, {}, location="square")
    game.world.apply(O.Move(entity="warden", to="square"))
    assert game.world.apply(Follow(npc="warden", actor="warden", leader=game.state.investigator.id, destination="office")).ok
    turn = await game.resolve(Action(kind="move", summary="先绕去钟楼", destination="tower"), Ruling(needs_roll=False))
    assert game.world.place("warden") == "tower" and game.world.rec.entities["warden"].follow_to == "office"
    assert any("一同抵达" in n for n in turn.notes)
    await game.resolve(Action(kind="move", summary="去报社", destination="office"), Ruling(needs_roll=False))
    assert game.world.place("warden") == "office" and not game.world.rec.entities["warden"].following
    await game.close()


async def test_someone_asked_along_who_did_not_come_is_named_for_the_telling(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    action = Action(kind="move", summary="和守钟人去广场", destination="square", companions=["warden"])
    turn = await game.resolve(action, Ruling(needs_roll=False))
    assert game.state.location == "square" and game.world.place("warden") == "tower"
    assert any("没有一起来" in n and "不要写成同行" in n for n in turn.notes)
    await game.close()


async def test_explicit_kp_messages_bypass_actions_and_leave_the_world_and_pending_scene_untouched(module, fake):
    game, cast, _ = make(module, fake, {})
    game._late.notes.append("门外有一封信送到，等待下一次行动时呈现。")
    before = (game.state.turn, game.state.minutes, game.world.rec.seq)
    await graph.build(game).ainvoke({"input": "/kp 我说去看看，是指上文的书房。"})
    assert (game.state.turn, game.state.minutes, game.world.rec.seq) == before
    assert not cast.heard and not cast.ruled and not cast.npc_calls
    assert "玩家在问游戏外的问题" in cast.reports[-1] and "上文的书房" in cast.reports[-1]
    assert game._late.notes and not [e for e in game.state.log if e.kind == "player"]
    await game.close()


async def test_an_npc_and_bystanders_cannot_read_a_players_concealed_intent_from_the_summary(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    action = Action(kind="talk", summary="周衡隐瞒报社发现的怪物，只佯称一无所获", npc="warden",
                    visible="摊了摊手", speech="什么都没查到。", approach="话术")
    message = game._npc_message(Turn(action=action), "warden")
    game._exchanged("warden", action, "那就明天再查吧。")
    assert "什么都没查到" in message and "隐瞒" not in message and "怪物" not in message
    assert "怪物" not in game.world.rec.events[-1].text
    # With no spoken line the summary still does not become something the NPC heard.
    silent = action.model_copy(update={"speech": ""})
    game.state.turn += 1
    message = game._npc_message(Turn(action=silent), "warden")
    game._exchanged("warden", silent, "你还想问什么？")
    assert "摊了摊手" in message and "怪物" not in message
    assert "摊了摊手" in game.world.rec.events[-1].text
    assert "怪物" not in game.world.rec.events[-1].text
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
    ruling = Ruling(needs_roll=True, skill="说服", minutes=5, note="楼上传来一阵敲门声")
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
    again = Game(Module.load(paths.EXAMPLE), loaded, cast, Rules(dice=fake))
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
    m = Module.load(paths.MODULES / "zhuishuren")
    st = Game.new(m, m.pregens)
    book = table(m, st)
    assert "suidao" not in {loc["id"] for loc in book["locations"]}
    assert all(not loc["read_aloud"] and not loc["features"] for loc in book["locations"] if loc["id"] != st.location)
    assert {n["id"] for n in book["npcs"]} == {"zhanwuyuan"}
    assert not book["events"] and not book["clues"] and not book["handouts"] and not book["flags"]
    hub = Hub(paths.MODULES / "zhuishuren", state=st, cast=FakeCast({}), rules=Rules(dice=fake))
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
async def test_a_kp_line_typed_ahead_stays_a_question_for_the_keeper(module, monkeypatch):
    """No input modes (the user, 2026-10-21): /kp marks a line as for the keeper; the rest is read
    as it comes (an action, or a question out of the game)."""
    from textual.widgets import Input

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.game import GameScreen

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = GameScreen(module, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.cast is not None and not screen.busy)
        sent = []
        monkeypatch.setattr(screen, "send_line", sent.append)
        box = screen.query_one("#command", Input)
        assert not box.border_title and "/kp" in box.placeholder
        box.value = "/kp 我指的是刚才提到的书房。"
        screen.busy = True
        await pilot.press("enter")
        assert screen.queued == "/kp 我指的是刚才提到的书房。" and not sent
        screen._busy(False)
        assert await until(pilot, lambda: bool(sent))
        assert sent == ["/kp 我指的是刚才提到的书房。"]


@pytest.mark.usefixtures("canned")
async def test_clicking_the_input_box_lists_a_check_s_skills(module):
    """The user, 2026-10-21: a check's skill can be picked from a list, by click."""
    from textual.widgets import Input, OptionList

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.game import GameScreen

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = GameScreen(module, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.cast is not None and not screen.busy)
        box, picker = screen.query_one("#command", Input), screen.query_one("#picker", OptionList)
        box.value = ".ra 聆"
        await pilot.click("#command", offset=(4, 1))
        await pilot.pause()
        assert picker.display and picker.get_option_at_index(0).id == ".ra 聆听"
        await pilot.press("enter")
        await pilot.pause()
        assert box.value == ".ra 聆听" and not picker.display
        box.value = ".ra 聆"
        await pilot.click("#command", offset=(4, 1))
        await pilot.pause()
        await pilot.press("escape")  # closes the list, not the game's menu
        await pilot.pause()
        assert not picker.display and app.screen is screen


@pytest.mark.usefixtures("canned")
async def test_the_screen_recovers_from_a_provider_failure_and_accepts_retry(module):
    from textual.widgets import Input

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.game import GameScreen

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
