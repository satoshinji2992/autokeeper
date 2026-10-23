"""An authorized wait can continue in coarse intervals without another player declaration."""

from __future__ import annotations

import json

import pytest

from autokeeper.agents.cast import Action, Plan, Ruling
from autokeeper.engine.state import GameState, format_time
from autokeeper.module.schema import Event

from .test_request_execution import RequestCast, make, module, run, telling
from .test_scene import SCRIPT, SEEN, made, sandbox  # noqa: F401


async def test_wait_context_keeps_the_stop_condition_for_the_rules_assistant(fake):
    game, _ = made("tower", fake=fake)
    original = "在安全处守候到约定时间，有人敲门就停下；期间留意声音。"
    summary = "周衡在安全处守候到约定时间，期间留意声音；有人敲门就停止等待。"
    SCRIPT.extend([
        {"action": {"kind": "wait", "summary": summary}},
        {"needs_roll": False, "minutes": 23, "reason": "本段清醒守候"},
    ])
    try:
        plan = await game.cast.plan(game.state, original)
        ruling = await game.cast.arbiter(game.state, plan.action)
        assert plan.action.kind == "wait" and not plan.remaining and ruling.minutes == 23 and not ruling.needs_roll
        supplied = json.dumps(SEEN[-1]["messages"], ensure_ascii=False)
        assert summary in supplied and "有人敲门就停止等待" in supplied
        assert not SCRIPT
    finally:
        await game.close()


@pytest.mark.parametrize("recover", [False, True])
async def test_continued_wait_uses_the_same_request_and_keeps_elapsed_time_after_reload(fake, recover):
    original = "先安静守候一段；没有新情况就继续等到约定时间。"
    first = Action(kind="wait", summary="周衡在安全处安静守候，留意门外动静")
    second = Action(kind="wait", summary="没有新情况，周衡继续守候到约定时间")
    cast = RequestCast({original: Plan(action=first, remaining="没有新情况就继续守候到约定时间。")}, {
        first.summary: Ruling(needs_roll=False, minutes=7),
        second.summary: Ruling(needs_roll=False, minutes=23),
    }, [telling(next=Plan(action=second)), telling()])
    game, graph = make(fake, cast)
    games, saved = [game], []
    start = game.state.minutes

    def disconnect() -> None:
        pending = game.state.pending_turn
        done = pending.get("resolution", {}).get("steps", {}).get("elapsed", {}).get("done")
        if not saved and game.state.turn == 2 and done:
            saved.append(game.state.model_dump_json())
            raise ConnectionError("wait elapsed receipt saved")

    if recover:
        game.checkpoint = disconnect
    try:
        if recover:
            with pytest.raises(ConnectionError, match="wait elapsed receipt"):
                await run(graph, game, {"input": original})
            state = GameState.model_validate_json(saved[0])
            assert state.minutes == start + 30 and state.pending_turn["request"]["part"] == 1
            game, graph = make(fake, cast, m=game.m, state=state)
            games.append(game)
            _, offers = await run(graph, game, {"input": "/retry"})
        else:
            _, offers = await run(graph, game, {"input": original})
        assert not offers and not game.state.pending_turn and not game.state.unfinished
        assert game.state.minutes == start + 30 and len(game.state.checks) == 0
        assert cast.planned == [original] and [a.kind for a in cast.ruled] == ["wait", "wait"]
        assert len([entry for entry in game.state.log if entry.kind == "player"]) == 1
        assert len(cast.reports) == 2 and not cast.tellings and not fake.values
    finally:
        for old in games:
            await old.close()


async def test_an_actual_change_at_a_wait_boundary_keeps_the_unperformed_wait_for_a_new_decision(fake):
    original = "先在门边守候；若没新情况就继续等，听见求救先停下来。"
    first = Action(kind="wait", summary="周衡在门侧守候到下一次检查时点")
    second = Action(kind="wait", summary="周衡继续在安全处等待")
    pending = "楼下已经有人求救，先决定是否回应，再继续等待。"
    cast = RequestCast({original: Plan(action=first, remaining="没有新情况就继续等待；求救时先停下。")}, {
        first.summary: Ruling(needs_roll=False, minutes=7), second.summary: Ruling(needs_roll=False, minutes=23),
    }, [telling(pending=pending)])
    m = module()
    game, graph = make(fake, cast, m=m)
    start = game.state.minutes
    m.events = [Event(id="appeal", title="求救", when=format_time(start + 7), where="tower",
                      text="楼下传来求救声，需要调查员决定是否回应。", read_aloud="楼下有人喊：救救我！")]
    try:
        events, offers = await run(graph, game, {"input": original})
        assert not offers and game.state.minutes == start + 7 and game.state.fired == ["appeal"]
        assert [a.summary for a in cast.ruled] == [first.summary]
        assert game.state.unfinished[game.state.investigator.id]["remaining"] == pending
        assert len([event for event in events if event["type"] == "event" and event.get("id") == "appeal"]) == 1
        assert not game.state.pending_turn and not fake.values
    finally:
        await game.close()
