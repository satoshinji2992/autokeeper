"""The same real keeper requests scene preparation and receives its durable result.

All roles use the local scripted writer. No image generation, provider requests, or real saves.
"""

from __future__ import annotations

import copy
import json

import pytest

from autokeeper.agents.director import Improv
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.state import GameState

from .test_keeper_memory_runtime import memory, words
from .test_keeper_tools_execution import final, role_calls, shut, table
from .test_request_execution import run
from .test_scene import SCRIPT, sandbox  # noqa: F401

ORIGINAL = "我把清点册合上，等老人继续说。"
BODY = "老人把清点册收好，窗外仍传来淅沥的雨声。"
REQUEST = "为广场上的布道者准备一条保护证人的私有行动安排，并补充报社旁的备用场所；调查员留在钟楼。"
PRIVATE_REASON = "幕后证人已经察觉有人跟踪，布道者准备抢在看守人之前转移证词。"
PRIVATE_GOAL = "私下保护证人，避免钟楼里的调查员知悉转移安排。"
PRIVATE_PLACE = "证词临时保管间"
STEP = "检查自己保管的证词是否完整，决定稍后如何与证人联络"


def prepared(game) -> dict:
    return {
        "kind": "展开", "reason": PRIVATE_REASON,
        # The new place is away from the players. Its illustration is not needed to prepare it.
        "locations": [{"id": "witness_archive", "name": PRIVATE_PLACE,
                       "read_aloud": "报社旁一间狭窄储藏室，百叶窗关着。", "exits": ["office"]}],
        "backstage": [
            {"kind": "enlist", "npc": "preacher", "goals": [PRIVATE_GOAL], "mood": "忧虑而有明确打算"},
            {"kind": "plan", "npc": "preacher", "steps": [
                {"id": "protect_testimony", "text": STEP, "completion": "report",
                 "due": game.state.minutes + 90, "meet": False},
            ]},
            {"kind": "schedule", "event": "check_witness", "at": game.state.minutes + 120,
             "place": "square", "participants": ["preacher"], "text": "布道者确认广场上的碰面安排"},
        ],
    }


def script(game) -> None:
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡合上清点册"}},
        {"needs_roll": False, "minutes": 0},
        {"kind": "tool", "tool": {"tool": "director", "query": REQUEST}},
        prepared(game),
        final(BODY),
    ])


def assert_prepared(game) -> None:
    world, state = game.world, game.state
    assert state.location == "tower" and world.place("preacher") == "square"
    assert game.m.location("witness_archive").name == PRIVATE_PLACE
    assert game.m.location("office").exits.count("witness_archive") == 1
    assert [place.id for place in game.m.locations].count("witness_archive") == 1
    assert world.agent("preacher").goals == [PRIVATE_GOAL]
    steps = [step for step in world.agent("preacher").plan if step.id == "protect_testimony"]
    assert len(steps) == 1 and steps[0].status == "pending" and steps[0].text == STEP
    assert steps[0].due == state.minutes + 90
    assert world.rec.scheduled["check_witness"].at == state.minutes + 120
    assert world.rec.scheduled["check_witness"].status == "pending"
    assert len(state.improv) == 1
    for kind in ("enlist", "plan", "schedule"):
        assert len([entry for entry in world.rec.journal if entry.actor == "director" and entry.kind == kind]) == 1
    assert not state.clues and not state.handouts and not state.checks


def assert_private(events) -> None:
    exposed = json.dumps(events, ensure_ascii=False)
    for secret in (PRIVATE_REASON, PRIVATE_GOAL, PRIVATE_PLACE, STEP, REQUEST):
        assert secret not in exposed
    for event in events:
        if event["type"] == "improv":
            assert event == {"type": "improv", "kind": "展开"}


async def test_keeper_requests_director_in_its_tool_loop_and_only_narrates_after_the_package_lands(fake):
    game, graph, cast = table(fake)
    script(game)
    start = game.state.minutes
    try:
        events, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert_prepared(game)
        assert game.state.minutes == start and game.state.turn == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("Improv")) == 1
        hosted = role_calls("HostedReply")
        assert len(hosted) == 2 and not role_calls("Dialogue")
        assert REQUEST in words(role_calls("Improv")[0]["messages"])
        result_context = words(hosted[-1]["messages"])
        assert '"tool": "director"' in result_context and '"status": "prepared"' in result_context
        assert PRIVATE_PLACE in result_context
        assert '"tool": "director"' in memory(game.state)
        assert '"status": "prepared"' in memory(game.state)
        assert [event["text"] for event in events if event["type"] == "keeper_delta"] == [BODY]
        assert_private(events)
        assert not hasattr(game, "brain") and not hasattr(game, "_look")
    finally:
        await shut([game], [cast])


async def test_director_preparation_cannot_change_an_investigators_formal_condition(fake):
    game, _, cast = table(fake)
    try:
        who = game.state.investigator.id
        plan = Improv.model_validate({"kind": "展开", "reason": "准备拦路遭遇",
                                     "backstage": [{"kind": "condition", "entity": who, "add": "昏迷"}]})
        assert any("正式规则结算" in problem for problem in game._trial(plan, game.state.location))
        assert "昏迷" not in game.state.investigator.conditions
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("stage", ["request-saved", "applied-before-result", "result-saved", "final-saved"])
async def test_director_tool_restore_reuses_applied_package_and_never_repeats_world_or_model_work(fake, monkeypatch, stage):
    game, graph, cast = table(fake)
    base = game.m.model_copy(deep=True)
    script(game)
    games, casts, saved, shown = [game], [cast], [], []
    original_record = turn_graph.record_event

    def record(current, event):
        shown.append(copy.deepcopy(event))
        original_record(current, event)

    monkeypatch.setattr(turn_graph, "record_event", record)

    def disconnect():
        receipt = game.state.pending_turn.get("keeper", {})
        rows = [row for row in receipt.get("calls", []) if row.get("tool") == "director"]
        row = rows[-1] if rows else {}
        package = row.get("director_package", {})
        ready = {
            "request-saved": bool(rows) and not package,
            "applied-before-result": bool(package.get("applied")) and "result" not in row,
            "result-saved": bool(row.get("result")) and not row.get("done"),
            "final-saved": "text" in receipt and not receipt.get("landed"),
        }[stage]
        if ready and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError(f"导演工具断线：{stage}")

    game.checkpoint = disconnect
    try:
        with pytest.raises(RuntimeError, match=f"导演工具断线：{stage}"):
            await run(graph, game, {"input": ORIGINAL})
        assert len(saved) == 1
        restored = GameState.model_validate_json(saved[0])
        game, graph, cast = table(fake, module=base, state=restored)
        games.append(game)
        casts.append(cast)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert_prepared(game)
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("Improv")) == 1
        assert len(role_calls("HostedReply")) == 2 and not role_calls("Dialogue")
        assert game.state.turn == 1
        assert [entry.text for entry in game.state.log if entry.kind == "keeper"] == [BODY]
        assert len([event for event in shown if event["type"] == "keeper_delta"]) == 1
        assert memory(game.state).count('"status": "prepared"') == 1
        assert_private(shown)
    finally:
        await shut(games, casts)
