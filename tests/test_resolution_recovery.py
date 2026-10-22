"""Committed turn mechanics survive failed notifications, cancellation and save reloads."""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from autokeeper.agents.cast import Action, Failure, IdeaFailure, Ruling
from autokeeper.engine import graph
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, make


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


def turn_going(game: Game, text: str) -> None:
    """A turn begun as the turn graph begins one (graph.route): its journal (pending_turn) keeps the
    stages a retry goes on from."""
    game.state.pending_turn = {"input": text}


def snapshots(game: Game) -> list[str]:
    saved: list[str] = []
    game.checkpoint = lambda: saved.append(game.state.model_dump_json())
    return saved


def reload_game(module: Module, saved: str, fake) -> tuple[Game, list[dict]]:
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    game = Game(module.model_copy(deep=True), GameState.model_validate_json(saved), FakeCast({}), Rules(fake), emit)
    return game, events


async def test_failed_dice_delivery_resumes_saved_roll_route_and_partial_turn(module, fake):
    action = Action(kind="investigate", summary="去报社查旧报", destination="office", target="旧报合订本")
    ruling = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"], minutes=15)
    game, _, _ = make(module, fake.queue(5), {})
    turn_going(game, "去报社查旧报")
    saved = snapshots(game)
    start = game.state.minutes

    async def broken(event: dict) -> None:
        if event["type"] == "dice":
            raise ConnectionError("disconnected after the roll")

    game.emit = broken
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    state = GameState.model_validate_json(saved[-1])
    assert len(state.checks) == 1 and state.location == "office"
    assert state.pending_turn["resolution"]["steps"]["check"]["done"]

    resumed, events = reload_game(module, saved[-1], fake)
    turn = await resumed.resolve(action, ruling)
    assert len(turn.checks) == len(resumed.state.checks) == 1
    assert turn.arrived == "office" and turn.first_visit and turn.route == ["square", "office"]
    assert turn.clues == resumed.state.clues == ["c_archive"]
    assert resumed.state.minutes == start + 10 + 15
    assert len(fake.values) == 0
    assert sum(event["type"] == "dice" for event in events) == 1
    assert resumed.state.pending_turn["resolution"]["complete"]
    await game.close()
    await resumed.close()


async def test_clue_delivery_failure_keeps_handout_and_sanity_and_finishes_notifications(module, fake):
    module.clue("c_diary").sanity = "1/1"
    action = Action(kind="investigate", summary="查看日记", target="守钟人的柜子")
    ruling = Ruling(needs_roll=False, auto_clues=["c_diary"], minutes=7)
    game, _, _ = make(module, fake.queue(20), {}, "tower")
    turn_going(game, "查看日记")
    saved = snapshots(game)
    before = game.state.investigator.san

    async def broken(event: dict) -> None:
        if event["type"] == "clue":
            raise RuntimeError("clue display failed")

    game.emit = broken
    with pytest.raises(RuntimeError):
        await game.resolve(action, ruling)
    assert game.state.clues == ["c_diary"] and game.state.handouts == ["h_diary"]
    assert game.state.investigator.san == before - 1
    resumed, events = reload_game(module, saved[-1], fake)
    turn = await resumed.resolve(action, ruling)
    assert turn.clues == ["c_diary"] and len(turn.sanity) == 1
    assert resumed.state.investigator.san == before - 1
    assert [event["type"] for event in events][:3] == ["clue", "handout", "sanity"]
    assert sum(entry.kind == "sanity" for entry in resumed.state.log) == 1
    await game.close()
    await resumed.close()


@pytest.mark.parametrize("interrupted", ["event", "move", "ending"])
async def test_cancelled_timed_event_resumes_move_damage_and_ending_once_after_reload(module, fake, interrupted):
    module.events.append(Event(id="collapse", title="楼板塌陷", when=module.start_time, text="事故",
                               read_aloud="脚下的木板断裂。", move_to="office", damage="1D3", ending="stopped"))
    action = Action(kind="wait", summary="稍候")
    ruling = Ruling(needs_roll=False, minutes=12)
    game, _, _ = make(module, fake.queue_totals(2, 3), {})
    turn_going(game, "稍候")
    saved = snapshots(game)
    started = asyncio.Event()
    hp, start = game.state.investigator.hp, game.state.minutes

    async def blocked(event: dict) -> None:
        if event["type"] == interrupted:
            started.set()
            await asyncio.Event().wait()

    game.emit = blocked
    task = asyncio.create_task(game.resolve(action, ruling))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert game.state.fired.count("collapse") == 1
    assert game.state.investigator.hp == hp - (2 if interrupted == "ending" else 0)

    resumed, events = reload_game(module, saved[-1], fake)
    turn = await resumed.resolve(action, ruling)
    assert resumed.state.fired.count("collapse") == 1
    assert resumed.state.location == "office" and resumed.state.investigator.hp == hp - 2
    assert resumed.state.ending == "stopped" and turn.ending == "stopped"
    assert resumed.state.minutes == start + 12
    assert [event.id for event in turn.events].count("collapse") == 1
    assert len(fake.totals) == 0
    assert sum(event["type"] == "damage" for event in events) == (0 if interrupted == "ending" else 1)
    assert sum(entry.kind == "ending" for entry in resumed.state.log) == 1
    await game.close()
    await resumed.close()


async def test_failure_after_damage_does_not_repeat_damage_or_time(module, fake):
    module.events.append(Event(id="hurt", title="坠物", when=module.start_time, text="砸伤", damage="1D3"))
    action = Action(kind="wait", summary="等十分钟")
    ruling = Ruling(needs_roll=False, minutes=10)
    game, _, _ = make(module, fake.queue_totals(2), {})
    turn_going(game, "等十分钟")
    saved = snapshots(game)
    hp, start = game.state.investigator.hp, game.state.minutes

    async def broken(event: dict) -> None:
        if event["type"] == "damage":
            raise RuntimeError("failed after damage settled")

    game.emit = broken
    with pytest.raises(RuntimeError):
        await game.resolve(action, ruling)
    resumed, _ = reload_game(module, saved[-1], fake)
    turn = await resumed.resolve(action, ruling)
    assert resumed.state.investigator.hp == hp - 2
    assert resumed.state.minutes == start + 10
    assert sum(entry.kind == "damage" for entry in resumed.state.log) == 1
    assert len([note for note in turn.notes if "2" in note]) == 1
    await game.close()
    await resumed.close()


async def test_completed_plugins_skip_while_unfinished_phase_retries_and_the_next_turn_is_its_own(module, fake):
    action = Action(kind="daily", summary="整理笔记")
    ruling = Ruling(needs_roll=False, minutes=3)
    game, _, _ = make(module, fake, {})
    turn_going(game, "整理笔记")
    snapshots(game)
    calls: list[str] = []
    original = game._time

    async def plugin(hook: str, turn, **details) -> None:
        calls.append(hook)

    async def time(turn) -> None:
        calls.append("time")
        if calls.count("time") == 1:
            raise RuntimeError("world model unavailable")
        await original(turn)

    game.plugins.run = plugin
    game._time = time
    start = game.state.minutes
    with pytest.raises(RuntimeError):
        await game.resolve(action, ruling)
    await game.resolve(action, ruling)
    assert calls.count("turn") == 1 and calls.count("time") == 2
    assert game.state.minutes == start + 3
    assert game.state.pending_turn["resolution"]["complete"]
    await game.resolve(action, ruling)  # the same turn again: nothing settles twice
    assert game.state.minutes == start + 3 and calls.count("turn") == 1
    turn_going(game, "整理笔记")  # the next turn
    await game.resolve(action, ruling)
    assert game.state.minutes == start + 6 and calls.count("turn") == 2
    await game.close()


async def test_graph_retry_after_manual_roll_does_not_request_or_settle_roll_again(module, fake):
    action = Action(kind="investigate", summary="查阅旧报", target="旧报合订本")
    ruling = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"], minutes=4)
    game, _, _ = make(module, fake.queue(5), {"查旧报": (action, ruling)}, "office")
    saved = snapshots(game)

    async def broken(hook: str, turn, **details) -> None:
        if hook == "turn":
            raise RuntimeError("plugin unavailable")

    game.plugins.run = broken
    app = graph.build(game, InMemorySaver())
    config = {"configurable": {"thread_id": "manual"}}
    first = [row async for row in app.astream({"input": "查旧报", "by_hand": True}, config)]
    assert any("__interrupt__" in row for row in first)
    with pytest.raises(RuntimeError):
        await app.ainvoke(Command(resume=True), config)
    assert "turn" not in game.state.pending_turn
    assert game.state.pending_turn["resolution"]["steps"]["check"]["done"]
    resumed, _ = reload_game(module, saved[-1], fake)
    fresh = graph.build(resumed, InMemorySaver())
    chunks = [row async for row in fresh.astream({"input": "/retry"}, {"configurable": {"thread_id": "resume"}})]
    assert not any("__interrupt__" in row for row in chunks)
    assert resumed.state.clues == ["c_archive"] and len(resumed.state.checks) == 1
    assert not resumed.state.pending_turn
    await game.close()
    await resumed.close()


async def test_flag_already_committed_still_finishes_its_event_after_retry(module, fake):
    module.flags["alarm"] = "警铃已响"
    module.events.append(Event(id="alarm-event", title="警铃", when="flag:alarm", text="警铃",
                               read_aloud="铃声响起。", damage="1D3"))
    action = Action(kind="investigate", summary="拉下警铃")
    ruling = Ruling(needs_roll=False, set_flags=["alarm"], minutes=4)
    game, _, _ = make(module, fake.queue_totals(1), {})
    turn_going(game, "拉下警铃")
    saved = snapshots(game)
    hp = game.state.investigator.hp

    async def broken(event: dict) -> None:
        if event["type"] == "flag":
            raise RuntimeError("flag screen failed")

    game.emit = broken
    with pytest.raises(RuntimeError):
        await game.resolve(action, ruling)
    assert game.state.flags == ["alarm"] and "alarm-event" not in game.state.fired
    resumed, _ = reload_game(module, saved[-1], fake)
    turn = await resumed.resolve(action, ruling)
    assert resumed.state.flags == ["alarm"]
    assert resumed.state.fired.count("alarm-event") == 1
    assert resumed.state.investigator.hp == hp - 1
    assert [event.id for event in turn.events] == ["alarm-event"]
    await game.close()
    await resumed.close()


async def test_sanity_tool_receipts_keep_completed_rolls_if_the_madness_table_fails(module, fake):
    module.clue("c_diary").sanity = "0/1D6"
    action = Action(kind="investigate", summary="阅读骇人的日记", target="守钟人的柜子")
    ruling = Ruling(needs_roll=False, auto_clues=["c_diary"], minutes=5)
    fake.queue(90, 5).queue_totals(5)  # SAN fails; five points lost; INT succeeds; temporary madness.
    game, _, _ = make(module, fake, {}, "tower")
    turn_going(game, "阅读骇人的日记")
    saved = snapshots(game)
    san = game.state.investigator.san
    table = fake.table

    def unavailable(name: str) -> str:
        raise ConnectionError("madness table bridge unavailable")

    fake.table = unavailable
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    assert not game.state.clues and not game.state.handouts
    assert game.state.investigator.san == san and not game.state.investigator.conditions
    assert not fake.values and not fake.totals
    receipt = game.state.pending_turn["resolution"]["steps"]["clue:c_diary/commit"]
    assert not receipt["done"] and len(receipt["tools"]) == 3
    fake.table = table
    resumed, _ = reload_game(module, saved[-1], fake)
    turn = await resumed.resolve(action, ruling)
    assert resumed.state.investigator.san == san - 5
    assert "临时疯狂" in resumed.state.investigator.conditions
    assert turn.sanity[0][1].temporary
    assert resumed.state.clues == ["c_diary"] and resumed.state.handouts == ["h_diary"]
    assert len(fake.tables) == 1
    await game.close()
    await resumed.close()


async def test_damage_tool_receipt_does_not_reroll_damage_if_constitution_check_fails(module, fake):
    module.events.append(Event(id="heavy-hurt", title="坠落", when=module.start_time, text="坠落", damage="1D10"))
    action = Action(kind="wait", summary="等待")
    ruling = Ruling(needs_roll=False, minutes=2)
    game, _, _ = make(module, fake, {})
    turn_going(game, "等待")
    saved = snapshots(game)
    hp = game.state.investigator.hp
    amount = (game.state.investigator.hp_max + 1) // 2
    fake.queue_totals(amount)
    with pytest.raises(IndexError):  # bridge fails after damage total but before CON result
        await game.resolve(action, ruling)
    assert game.state.investigator.hp == hp and not game.state.investigator.conditions
    assert not fake.totals
    fake.queue(90)
    resumed, _ = reload_game(module, saved[-1], fake)
    await resumed.resolve(action, ruling)
    assert resumed.state.investigator.hp == hp - amount
    assert "重伤" in resumed.state.investigator.conditions
    assert sum(entry.kind == "damage" and "受到" in entry.text for entry in resumed.state.log) == 1
    assert "昏迷" not in resumed.state.investigator.conditions  # the solo game advances until they wake
    assert sum(entry.kind == "damage" and "醒了过来" in entry.text for entry in resumed.state.log) == 1
    await game.close()
    await resumed.close()


PUSH = {"choice": "push", "how": "把那一年的合订本一页页翻完"}  # FakeCast.repush: it can be done, in ten minutes


@pytest.mark.parametrize("choice", [PUSH, "luck", "drop"])
async def test_cancelled_push_choice_resumes_without_repaying_rerolling_or_regaining(module, fake, choice):
    action = Action(kind="investigate", summary="查旧报", target="旧报合订本")
    # A failure the module lets find the paper at a price: what giving up settles, and resumes.
    ruling = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"], minutes=3,
                    on_fail=Failure(text="翻到天黑才找着", clues=["c_archive"], minutes=20))
    fake.queue(65)
    if choice == PUSH:
        fake.queue(5)
    game, _, _ = make(module, fake, {}, "office")
    turn_going(game, "查旧报")
    saved = snapshots(game)
    first = await game.resolve(action, ruling)
    assert first.push and game.state.pending_push
    luck, start = game.state.investigator.luck, game.state.minutes
    started = asyncio.Event()

    async def blocked(event: dict) -> None:
        if event["type"] == "clue":
            started.set()
            await asyncio.Event().wait()

    game.emit = blocked
    task = asyncio.create_task(game.push(choice))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert game.state.pending_push is None
    assert game.state.clues == ["c_archive"]
    resumed, _ = reload_game(module, saved[-1], fake)
    turn = await resumed.push(choice)
    assert resumed.state.clues == turn.clues == ["c_archive"]
    assert sum(entry.kind == "clue" for entry in resumed.state.log) == 1
    assert resumed.state.investigator.luck == luck - (5 if choice == "luck" else 0)
    assert resumed.state.minutes == start + (10 if choice == PUSH else 20 if choice == "drop" else 0)  # a push takes its own time
    assert len(turn.checks) == (1 if choice == PUSH else 0)
    assert not fake.values and resumed.state.pending_turn["push_resolution"]["complete"]
    await game.close()
    await resumed.close()


@pytest.mark.parametrize("choice", [PUSH, "luck"])
async def test_graph_restores_push_choice_and_merges_its_turn_once_after_json_reload(module, fake, choice):
    action = Action(kind="investigate", summary="查旧报", target="旧报合订本")
    ruling = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"], minutes=3)
    fake.queue(65)
    if choice == PUSH:
        fake.queue(5)
    game, _, _ = make(module, fake, {"查旧报": (action, ruling)}, "office")
    saved = snapshots(game)
    luck = game.state.investigator.luck

    async def broken(turn) -> None:
        if turn.clues:
            raise RuntimeError("clue plugin unavailable after push")

    game.plugins.settled = broken
    app = graph.build(game, InMemorySaver())
    config = {"configurable": {"thread_id": "choice"}}
    first = [row async for row in app.astream({"input": "查旧报"}, config)]
    assert any("__interrupt__" in row for row in first)
    with pytest.raises(RuntimeError):
        await app.ainvoke(Command(resume=choice), config)
    assert game.state.pending_turn["push_choice"] == choice
    assert game.state.pending_turn["push_resolution"]["steps"]["check"]["done"]
    resumed, _ = reload_game(module, saved[-1], fake)
    fresh = graph.build(resumed, InMemorySaver())
    rows = [row async for row in fresh.astream({"input": "/retry"}, {"configurable": {"thread_id": "continued"}})]
    assert not any("__interrupt__" in row for row in rows)
    assert resumed.state.clues == ["c_archive"]
    assert resumed.state.investigator.luck == luck - (5 if choice == "luck" else 0)
    assert sum(entry.kind == "dice" for entry in resumed.state.log) == 2
    assert sum(entry.kind == "clue" for entry in resumed.state.log) == 1
    assert not resumed.state.pending_turn and not fake.values
    await game.close()
    await resumed.close()


async def test_an_explicit_keeper_offer_resumes_from_the_saved_telling_and_is_made_once(module, fake):
    action = Action(kind="investigate", summary="查看告示栏", target="告示栏")
    ruling = Ruling(needs_roll=False, minutes=1)
    game, _, _ = make(module, fake, {})
    game.state.turn, game.state.progress_at = 20, 0
    game.state.clues = ["c_notice"]  # the notice gives nothing new: a stall
    turn_going(game, "查看告示栏")
    saved = snapshots(game)
    turn = await game.resolve(action, ruling)
    assert game.state.idea_offer == 0  # the counter alone offered nothing
    game.state.pending_turn["keeper"] = {"text": "我们可以重新梳理一下线索。", "offer_idea": True}
    game.checkpoint()

    async def broken(event: dict) -> None:
        if event["type"] == "idea_offer":
            raise RuntimeError("failed after the offer was made")

    game.emit = broken
    with pytest.raises(RuntimeError):
        await game.narrate(turn)
    assert game.state.progress_at == game.state.turn == game.state.idea_offer
    resumed, _ = reload_game(module, saved[-1], fake)
    offers: list[dict] = []

    async def heard(event: dict) -> None:
        if event["type"] == "idea_offer":
            offers.append(event)

    resumed.emit = heard
    await resumed.narrate(turn)
    assert len(offers) == 1 and resumed.state.idea_offer == 20
    await game.close()
    await resumed.close()


@pytest.mark.parametrize("legacy", [False, True])
async def test_a_failed_idea_roll_keeps_its_arrangement_and_elapsed_time_after_reload(module, fake, legacy):
    game, cast, _ = make(module, fake, {})
    game.state.turn = 1
    choices = []

    async def arrange(state, clue_id):
        choices.append(clue_id)
        return IdeaFailure(minutes=17, situation="钟楼的异响迫使调查员重新查看齿轮。")

    cast.idea_failure = arrange
    turn_going(game, "/灵感")
    saved = snapshots(game)
    start, action = game.state.minutes, game.idea_action()
    ruling = Ruling(needs_roll=True, skill="灵感")
    fake.queue(99, 10)

    async def broken(event):
        if event["type"] == "clue":
            raise ConnectionError("disconnected after the failed Idea roll was settled")

    game.emit = broken
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    kept = GameState.model_validate_json(saved[-1])
    if legacy:  # a pre-0.0.5 save: time already passed, no small keeper arrangement was recorded
        kept.pending_turn["resolution"]["steps"].pop("idea-arrangement")
        kept.minutes = start + 180
    resumed, _ = reload_game(module, kept.model_dump_json(), fake)  # the old cast has no idea_failure method
    turn = await resumed.resolve(action, ruling)
    assert choices == ["c_gears"] and not fake.values
    assert resumed.state.minutes - start == (180 if legacy else 17) and turn.clues == ["c_gears"]
    assert ("又过去了3 小时" if legacy else "又过去了17 分钟") in "\n".join(turn.notes)
    assert len(resumed.state.ideas) == 1 and sum(e.kind == "dice" for e in resumed.state.log) == 1
    await game.close()
    await resumed.close()


async def test_a_push_that_fails_with_a_sanity_cost_takes_it_once_even_when_resumed(module, fake):
    """PUSH_SYSTEM names 理智损失 among a failed push's consequences: Failure.sanity is that SAN roll,
    settled as an event's (Game._lose_sanity), in its own stage: resumed after a broken connection,
    it is neither rolled nor lost again."""
    action = Action(kind="investigate", summary="查旧报", target="旧报合订本")
    ruling = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"], minutes=3,
                    on_push_fail=Failure(text="翻到一张不该看的照片", sanity="1/1D4"))
    fake.queue(65, 95, 99).queue_totals(3)  # the check fails, the push fails, the SAN roll fails: 1D4 = 3
    game, _, _ = make(module, fake, {}, "office")
    turn_going(game, "查旧报")
    saved = snapshots(game)
    await game.resolve(action, ruling)
    san = game.state.investigator.san

    async def broken(event: dict) -> None:
        if event["type"] == "sanity":
            raise ConnectionError("disconnected after the SAN roll")

    game.emit = broken
    with pytest.raises(ConnectionError):
        await game.push(PUSH)
    assert game.state.investigator.san == san - 3
    resumed, events = reload_game(module, saved[-1], fake)  # no dice left: a second roll would fail
    turn = await resumed.push(PUSH)
    assert resumed.state.investigator.san == san - 3 and [s.loss for _, s in turn.sanity] == [3]
    assert sum(entry.kind == "sanity" for entry in resumed.state.log) == 1
    assert [e["type"] for e in events].count("sanity") == 1  # the notice the connection lost, shown now
    assert any("理智受到冲击" in note for note in turn.notes)
    assert not fake.values and not fake.totals and resumed.state.pending_turn["push_resolution"]["complete"]
    await game.close()
    await resumed.close()


def test_a_failures_sanity_cost_is_written_as_the_book_writes_it():
    assert Failure(sanity="０/１Ｄ６").sanity == "0/1D6"
    for bad in ("1D3", "0/1D6+DB", "掉一点理智"):
        with pytest.raises(ValueError):
            Failure(sanity=bad)
