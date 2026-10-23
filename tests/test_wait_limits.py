"""An explicitly bounded wait cannot renew its allowance in a continuation."""

from __future__ import annotations

import pytest
from langgraph.types import Command
from pydantic import ValidationError

from autokeeper.agents.cast import Action, Failure, Plan, Ruling
from autokeeper.engine.state import GameState

from .test_request_execution import RequestCast, make, run, telling


def wait(summary, *, limit=None, until=""):
    return Action(kind="wait", summary=summary, wait_limit=limit, wait_until=until)


@pytest.mark.parametrize("first_minutes", [3, 10, 30])
async def test_repeated_waits_share_the_original_ten_minute_allowance(fake, first_minutes):
    original, tail = "最多等十分钟，再决定如何找人", "到人来时再谈，否则等我决定去哪里。"
    first, again = wait("等候第一段", limit=10), wait("继续等候", limit=300)
    cast = RequestCast({original: Plan(action=first, remaining=tail)}, {
        first.summary: Ruling(needs_roll=False, minutes=first_minutes),
        again.summary: Ruling(needs_roll=False, minutes=30),
    }, [telling(next=Plan(action=again, remaining=tail)) for _ in range(3)])
    game, graph = make(fake, cast)
    start = game.state.minutes
    try:
        events, offers = await run(graph, game, {"input": original})
        assert not offers and not game.state.pending_turn and game.state.minutes == start + 10
        assert len(cast.ruled) == (2 if first_minutes < 10 else 1)
        assert game.state.unfinished[game.state.investigator.id]["remaining"] == tail
        assert any(event.get("kind") == "pending" and "等待时限已到" in event["text"] for event in events)
        assert len([entry for entry in game.state.log if entry.kind == "player"]) == 1
    finally:
        await game.close()


@pytest.mark.parametrize("until,minutes", [("D1 20:25", 5), ("D1 20:19", 0), ("D2 00:00", 20)])
async def test_the_earlier_duration_or_absolute_deadline_bounds_the_actual_wait(fake, until, minutes):
    action = wait("有时长也有截止时刻", limit=20, until=until)
    cast = RequestCast({"等": Plan(action=action)}, {action.summary: Ruling(needs_roll=False, minutes=120)}, [telling()])
    game, graph = make(fake, cast)
    start = game.state.minutes
    try:
        await run(graph, game, {"input": "等"})
        assert game.state.minutes == start + minutes
    finally:
        await game.close()


async def test_a_saved_continuation_cannot_restart_its_original_wait_allowance(fake):
    first, again = wait("先等三分钟", limit=10), wait("接着等", limit=60)
    tail = "等到人来再商量去处。"
    cast = RequestCast({"原话": Plan(action=first, remaining=tail)}, {
        first.summary: Ruling(needs_roll=False, minutes=3), again.summary: Ruling(needs_roll=False, minutes=60),
    }, [telling(next=Plan(action=again, remaining=tail)), telling(next=Plan(action=again, remaining=tail))])
    game, graph = make(fake, cast)
    games, saves, start = [game], [], game.state.minutes

    def disconnect():
        if game.state.pending_turn.get("request", {}).get("ready"):
            saves.append(game.state.model_dump_json())
            raise ConnectionError("保存续办后中断")

    game.checkpoint = disconnect
    try:
        with pytest.raises(ConnectionError):
            await run(graph, game, {"input": "原话"})
        state = GameState.model_validate_json(saves[-1])
        assert state.pending_turn["request"]["wait_window"] == {"start": start, "end": start + 10}
        resumed, restored = make(fake, cast, m=game.m, state=state)
        games.append(resumed)
        await run(restored, resumed, {"input": "/retry"})
        assert state.minutes == start + 10 and len(cast.ruled) == 2
        assert not state.pending_turn
    finally:
        for instance in games:
            await instance.close()


async def test_parallel_work_does_not_consume_or_extend_another_players_personal_wait(fake):
    action, other = wait("我最多等十分钟", limit=10), wait("同伴忙三十分钟")
    cast = RequestCast({"我等": Plan(action=action), "他忙": Plan(action=other)}, {
        action.summary: Ruling(needs_roll=False, minutes=60), other.summary: Ruling(needs_roll=False, minutes=30),
    }, [telling(), telling()])
    game, graph = make(fake, cast, party=True)
    st, start = game.state, game.state.minutes
    try:
        game.begin_round()
        await game.arrange_party([("lin", "他忙"), ("zhouheng", "我等")])
        st.active = 1
        await run(graph, game, {"input": "他忙"})
        st.active = 0
        await run(graph, game, {"input": "我等"})
        assert st.minutes == start + 30
        assert st.round_elapsed == {"lin": 30, "zhouheng": 10}
    finally:
        await game.close()


async def test_explicit_failure_cost_is_not_erased_by_a_waiting_time_limit(fake):
    action = wait("警戒等十分钟", limit=10)
    cast = RequestCast({"原话": Plan(action=action)}, {action.summary: Ruling(
        needs_roll=True, skill="侦查", minutes=20, on_fail=Failure(minutes=5),
    )}, [telling()])
    game, graph = make(fake, cast)
    start = game.state.minutes
    fake.queue(99)
    try:
        _, offers = await run(graph, game, {"input": "原话"})
        assert offers and offers[0]["kind"] == "push"
        await run(graph, game, Command(resume="drop"))
        assert game.state.minutes == start + 15 and len(game.state.checks) == 1
    finally:
        await game.close()


async def test_reaching_the_wait_deadline_does_not_cancel_an_authorised_different_next_action(fake):
    waiting, work = wait("等十分钟", limit=10), Action(kind="use", summary="随后整理工具")
    cast = RequestCast({"原话": Plan(action=waiting, remaining="然后整理工具。")}, {
        waiting.summary: Ruling(needs_roll=False, minutes=30), work.summary: Ruling(needs_roll=False, minutes=2),
    }, [telling(next=Plan(action=work)), telling()])
    game, graph = make(fake, cast)
    start = game.state.minutes
    try:
        await run(graph, game, {"input": "原话"})
        assert game.state.minutes == start + 12 and len(cast.ruled) == 2
        assert not game.state.pending_turn and not game.state.unfinished
    finally:
        await game.close()


@pytest.mark.parametrize("until", ["D0 23:00", "D1 24:00", "D1 22:60", "明天晚上"])
def test_a_wait_deadline_uses_a_valid_explicit_game_clock(until):
    with pytest.raises(ValidationError):
        wait("等候", until=until)
