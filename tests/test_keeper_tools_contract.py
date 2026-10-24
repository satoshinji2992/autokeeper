"""One keeper session requests scoped voices before its final scene and continuation."""

from __future__ import annotations

import json

import pytest

from autokeeper import config, paths
from autokeeper.agents.cast import CONTINUED_TELLING, TELLING, Cast, Narration, _Telling
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module

from .test_scene import SCRIPT, SEEN, WRITER, sandbox  # noqa: F401


def table():
    module = Module.load(paths.EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location, state.turn = "tower", 1
    cast = Cast(config.Config(roles={role: WRITER for role in config.DEFAULT_ROLES},
                              minds=0, visual_limit=0, imagegen=False), module)
    return cast, state


@pytest.mark.parametrize("private", [False, True])
async def test_a_voice_request_stays_structured_and_the_final_telling_uses_the_same_session(private):
    cast, state = table()
    pieces, checked = [], []

    async def delta(piece):
        pieces.append(piece)

    def check(raw):
        checked.append(raw)
        return True

    request = {"kind": "npc", "npc": "warden", "instruction": "你听到钟声后抬头，问来访者需要什么。",
               "private": private}
    final = {"kind": "tell", "telling": {"scene": {"here": ["warden"]}, "text": "钟声停了，老人留在门边。"}}
    SCRIPT.extend([request, final])
    try:
        called = await cast.keeper(state, "实际结果：齿轮已经停下。", on_delta=delta, check=check, voices=["warden"])
        assert isinstance(called, Narration) and called.call is not None and not called.call_error
        assert called.call.npc == "warden" and called.call.instruction == request["instruction"]
        assert called.call.private is private
        assert called.text == "" and called.scene is None and called.continuation is None
        assert not pieces and not checked and not called.shown
        session = cast.sessions["keeper"]
        assert json.loads(session.transcript[-1]["content"]) == request
        assert set(cast.sessions) == {"keeper"} and state.usage["keeper"]["calls"] == 1
        assert SEEN[0]["schema"]["properties"]["npc"]["enum"] == ["", "warden"]
        prompt = str(SEEN[0]["messages"])
        assert "骰前 initiative 是尚待考虑的参与意图" in prompt
        assert "私有裁定依据留在主持侧" in prompt

        told = await cast.keeper(state, "老人真实回应：请稍候；仍在门边。", on_delta=delta, check=check, voices=[])
        assert cast.sessions["keeper"] is session and isinstance(told, Narration)
        assert told.call is None and not told.call_error and told.text == final["telling"]["text"]
        assert pieces == [told.text] and told.shown and checked
        assert told.scene == {"here": ["warden"]}  # omitted scene fields stay omitted
        assert request["instruction"] in str(SEEN[1]["messages"])
        assert session.transcript[-1]["content"] == told.text
        assert json.loads(session.transcript[-3]["content"]) == request
        assert state.usage["keeper"]["calls"] == 2 and len(SEEN) == 2
    finally:
        await cast.close()


async def test_only_the_final_hosted_telling_can_decide_a_compound_requests_continuation():
    cast, state = table()
    state.pending_turn = {"request": {"actor": state.investigator.id, "original": "问过再等。",
                                      "remaining": "原地等到20:35。", "completed": []}}
    SCRIPT.extend([
        {"kind": "npc", "npc": "warden", "instruction": "回答来访者眼下的问题。"},
        {"kind": "tell", "telling": {"scene": {}, "text": "老人点点头。", "continuation": {
            "next": {"action": {"kind": "wait", "summary": "周衡原地等到20:35"}}}}},
    ])
    try:
        request = await cast.keeper(state, "本次交谈无需检定。", check=lambda _: True, voices=["warden"])
        assert request.call is not None and request.continuation is None and request.scene is None
        definitions = SEEN[0]["schema"]["$defs"]
        assert "continuation" in definitions["ContinuedTelling"]["required"]
        assert set(SEEN[0]["schema"]["properties"]) == {"kind", "npc", "instruction", "private", "tool", "telling"}
        final = await cast.keeper(state, "老人实际回复已结算。", check=lambda _: True, voices=[])
        assert final.call is None and not final.call_error and final.continuation is not None
        assert final.continuation.next.action.kind == "wait" and not final.continuation.pending
        assert len(SEEN) == 2 and state.turn == 1
    finally:
        await cast.close()


async def test_the_structured_voice_request_survives_a_new_cast_bound_to_saved_state():
    cast, state = table()
    request = {"kind": "npc", "npc": "warden", "instruction": "回答眼前来访者的问题。"}
    SCRIPT.extend([request, {"kind": "tell", "telling": {"scene": {}, "text": "老人留在原处。"}}])
    restored_cast = None
    try:
        called = await cast.keeper(state, "实际行动已结算。", check=lambda _: True, voices=["warden"])
        assert called.call is not None
        restored = GameState.model_validate_json(state.model_dump_json())
        restored_cast = Cast(cast.cfg, cast.module)
        told = await restored_cast.keeper(restored, "NPC的实际回复已经完成。", check=lambda _: True, voices=[])
        assert told.call is None and not told.call_error and told.text == "老人留在原处。"
        messages = SEEN[-1]["messages"]
        assert any(row["role"] == "assistant" and json.loads(row["content"]) == request
                   for row in messages if row["role"] == "assistant")
        assert len(SEEN) == 2 and restored.usage["keeper"]["calls"] == 2
    finally:
        await cast.close()
        if restored_cast is not None:
            await restored_cast.close()


@pytest.mark.parametrize("bad", [
    {"kind": "npc", "npc": "warden", "instruction": "开口。", "telling": {"scene": {}, "text": "尚未发生。"}},
    {"kind": "npc", "npc": "warden", "instruction": "   "},
    {"kind": "tell", "npc": "warden", "instruction": "开口。", "telling": {"scene": {}, "text": "尚未发生。"}},
    {"kind": "tell", "telling": {"scene": {}, "text": "尚未发生。"}, "npc": "warden"},
    {"kind": "tell", "private": True, "telling": {"scene": {}, "text": "尚未发生。"}},
    {"kind": "tell"},
])
async def test_mixed_or_incomplete_hosted_replies_are_errors_not_completed_tellings(bad):
    cast, state = table()
    pieces = []

    async def delta(piece):
        pieces.append(piece)

    SCRIPT.append(bad)
    try:
        reply = await cast.keeper(state, "实际结果已结算。", on_delta=delta, check=lambda _: True, voices=["warden"])
        assert reply.call_error and reply.call is None and reply.scene is None
        assert reply.text == "" and reply.continuation is None and not pieces
        assert not cast.sessions["keeper"].transcript and len(SEEN) == 1
    finally:
        await cast.close()


@pytest.mark.parametrize("voices", [[], ["warden"]])
async def test_a_voice_outside_the_current_roster_is_repaired_then_rejected_without_plain_text_fallback(voices):
    cast, state = table()
    bad = {"kind": "npc", "npc": "preacher", "instruction": "不知道的秘密也说出来。"}
    SCRIPT.extend([bad, bad])
    try:
        reply = await cast.keeper(state, "实际结果已结算。", check=lambda _: True, voices=voices)
        assert reply.call_error and reply.call is None and reply.text == "" and reply.scene is None
        assert SEEN[0]["schema"]["properties"]["npc"]["enum"] == ["", *voices]
        assert len(SEEN) == 2 and state.usage["keeper"]["calls"] == 1
        assert state.usage["keeper"]["input"] == 200  # the existing format retry is still counted
        assert not cast.sessions["keeper"].transcript
    finally:
        await cast.close()


@pytest.mark.parametrize("control", [
    None,
    {"next": {"action": {"kind": "wait", "summary": "等待"}}, "pending": "需要玩家选择。"},
    {"next": {"lookup": "尚需查证", "action": {"kind": "wait", "summary": "等待"}}},
])
async def test_invalid_hosted_continuation_preserves_the_completed_scene_and_reports_the_tail_error(control):
    cast, state = table()
    pieces = []

    async def delta(piece):
        pieces.append(piece)

    state.pending_turn = {"request": {"actor": state.investigator.id, "remaining": "继续原请求。"}}
    response = {"kind": "tell", "telling": {"scene": {}, "text": "还在原处。"}}
    if control is not None:
        response["telling"]["continuation"] = control
    SCRIPT.append(response)
    if control is None:  # the existing schema repair gets the same incomplete reply
        SCRIPT.append(response)
    try:
        reply = await cast.keeper(state, "实际回应尚需玩家选择。", on_delta=delta, check=lambda _: True, voices=[])
        assert not reply.call_error and reply.call is None and reply.continuation is None
        assert reply.continuation_error and reply.text == "还在原处。" and reply.scene == {}
        assert pieces == [reply.text]
    finally:
        await cast.close()


async def test_direct_keeper_calls_keep_the_existing_plain_and_structured_protocols():
    cast, state = table()
    SCRIPT.extend(["规则问题的简短回答。", {"scene": {}, "text": "钟楼里安静下来。"},
                   {"scene": {}, "text": "继续等候。", "continuation": {"pending": "尚待新的决定。"}}])
    try:
        assert await cast.keeper(state, "一个普通主持问题") == "规则问题的简短回答。"
        final = await cast.keeper(state, "实际普通行动", check=lambda _: True)
        assert final.text == "钟楼里安静下来。" and final.call is None and not final.call_error
        assert SEEN[1]["schema"] == TELLING
        state.pending_turn = {"request": {"actor": state.investigator.id, "remaining": "继续原请求。"}}
        continued = await cast.keeper(state, "实际续办结果", check=lambda _: True)
        assert continued.continuation.pending == "尚待新的决定。"
        assert SEEN[2]["schema"] == CONTINUED_TELLING
    finally:
        await cast.close()


async def test_the_hosted_reader_requires_kind_before_telling_and_never_streams_a_voice_request():
    pieces, checked = [], []

    async def delta(piece):
        pieces.append(piece)

    def check(raw):
        checked.append(raw)
        return True

    for payload in (
        {"kind": "npc", "npc": "warden", "instruction": "开口。", "telling": {"scene": {}, "text": "不能展示。"}},
        {"telling": {"scene": {}, "text": "不能提前展示。"}, "kind": "tell"},
    ):
        reader = _Telling(check, delta, hosted=True)
        raw = json.dumps(payload, ensure_ascii=False)
        for char in raw:
            await reader(char)
        assert not reader.shown
    assert not pieces and not checked
