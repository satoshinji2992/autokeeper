"""The keeper requests small on-demand tools through its existing durable session."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from autokeeper import config
from autokeeper.agents.cast import Cast, HostedReply, KeeperCall, KeeperToolCall, Narration
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401


def table():
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0]])
    roles = {role: SCRIPTED for role in config.DEFAULT_ROLES}
    cast = Cast(config.Config(roles=roles, visual_limit=0, imagegen=False, search="none"), module)
    return cast, state


@pytest.mark.parametrize("call_data", [
    {"tool": "world_query", "query": "守钟人答应下午回来", "limit": 3, "scope": ["agent:warden"]},
    {"tool": "world_schema", "kinds": ["follow"]},
    {"tool": "world_preview", "operations": [{"kind": "follow", "npc": "warden", "leader": "chen"}]},
    {"tool": "world_commit", "operations": [{"kind": "follow", "npc": "warden", "leader": "chen"}]},
    {"tool": "lore", "query": "钟楼窗边的痕迹", "keywords": ["钟楼"], "max_tokens": 700},
    {"tool": "director", "query": "准备报社旁的备用会面地点与所需素材，不改变调查员当前位置"},
])
@pytest.mark.parametrize("continuing", [False, True])
async def test_tools_use_the_existing_keeper_session_without_speaking_or_completing_the_request(call_data, continuing):
    cast, state = table()
    if continuing:
        state.pending_turn["request"] = {"actor": state.investigator.id, "remaining": "稍后再检查窗户"}
    SCRIPT.append({"kind": "tool", "tool": call_data})
    shown = []

    async def delta(text):
        shown.append(text)

    try:
        result = await cast.keeper(state, "当前报告", voices=[], check=lambda _: True, on_delta=delta)
        assert isinstance(result, Narration) and isinstance(result.call, KeeperToolCall)
        assert result.call.model_dump(exclude_defaults=True) == call_data
        assert not result.text and not result.shown and not result.call_error and not result.continuation
        assert not shown and len(SEEN) == 1 and not SCRIPT
        assert state.transcripts["keeper"][-1]["role"] == "assistant"
        assert json.loads(state.transcripts["keeper"][-1]["content"]) == {"kind": "tool", "tool": call_data}
        assert SEEN[-1]["schema"]["title"] == ("ContinuedHostedReply" if continuing else "HostedReply")
        assert "只用已有场面能力，不请求检索" not in str(SEEN[-1]["messages"])
    finally:
        await cast.close()


async def test_actual_tool_receipt_and_next_telling_share_history_without_a_second_agent():
    cast, state = table()
    SCRIPT.extend([
        {"kind": "tool", "tool": {"tool": "world_query", "query": "守钟人的承诺"}},
        {"kind": "tell", "telling": {"scene": {}, "text": "你等在门廊下，老人仍在钟楼里。"}},
    ])
    try:
        await cast.keeper(state, "当前报告", voices=["warden"], check=lambda _: True)
        session = cast.sessions["keeper"]
        result = await cast.keeper(state, "【工具实际回执】他只答应下午回来，眼下还没有离开钟楼。",
                                   voices=["warden"], check=lambda _: True)
        assert result.text == "你等在门廊下，老人仍在钟楼里。" and not result.call
        assert cast.sessions["keeper"] is session
        messages = str(SEEN[-1]["messages"])
        assert "world_query" in messages and "守钟人的承诺" in messages and "眼下还没有离开钟楼" in messages
        assert state.transcripts["keeper"][-1]["content"] == result.text
        assert len(SEEN) == 2 and state.usage["keeper"]["calls"] == 2
    finally:
        await cast.close()


@pytest.mark.parametrize("payload", [
    {"kind": "tool", "tool": {"tool": "world_query"}, "telling": {"scene": {}, "text": "未发生的结果"}},
    {"kind": "tool", "tool": {"tool": "world_query"}, "npc": "warden", "instruction": "开口"},
    {"kind": "tool", "tool": {"tool": "world_query"}, "private": True},
    {"kind": "tool"},
    {"kind": "npc", "npc": "warden", "instruction": "开口", "tool": {"tool": "world_query"}},
    {"kind": "tell", "telling": {"scene": {}, "text": "结果"}, "tool": {"tool": "world_query"}},
])
def test_tool_and_player_telling_or_voice_are_exclusive(payload):
    with pytest.raises(ValidationError):
        HostedReply.model_validate(payload)


@pytest.mark.parametrize("call_data", [
    {"tool": "world_query", "actor": "director"},
    {"tool": "world_query", "request_id": "user_supplied"},
    {"tool": "world_query", "reads": {"entity:warden": 1}},
    {"tool": "world_query", "kinds": ["follow"]},
    {"tool": "world_schema", "scope": ["entity:warden"]},
    {"tool": "world_commit", "operations": []},
    {"tool": "world_preview", "operations": []},
    {"tool": "world_commit", "query": "不应混用", "operations": [{"kind": "follow"}]},
    {"tool": "world_schema", "operations": [{"kind": "follow"}]},
    {"tool": "world_query", "max_tokens": 200},
    {"tool": "lore", "query": " "},
    {"tool": "lore", "query": "钟楼", "limit": 5},
    {"tool": "director"},
    {"tool": "director", "query": " "},
    {"tool": "director", "query": "准备一次遭遇", "max_tokens": 700},
    *[{"tool": "world_commit", "operations": [{"kind": "follow", field: "untrusted"}]}
      for field in ("actor", "id", "request_id", "reads")],
])
def test_tool_arguments_do_not_accept_host_identity_or_irrelevant_parameters(call_data):
    with pytest.raises(ValidationError):
        KeeperToolCall.model_validate(call_data)


async def test_invalid_semantic_tool_request_is_returned_as_a_call_error_and_not_remembered():
    cast, state = table()
    SCRIPT.append({"kind": "tool", "tool": {"tool": "world_commit", "operations": []}})
    try:
        result = await cast.keeper(state, "当前报告", voices=[], check=lambda _: True)
        assert result.call_error and not result.text and not result.call
        assert not state.transcripts["keeper"]
    finally:
        await cast.close()


def test_tool_contract_requests_world_schema_on_demand_instead_of_embedding_all_operations():
    schema = KeeperToolCall.model_json_schema()
    assert not schema.get("$defs")
    assert schema["properties"]["operations"]["items"] == {"type": "object", "additionalProperties": True}
    assert len(json.dumps(schema, ensure_ascii=False)) < 3500
    assert schema["additionalProperties"] is False


async def test_ordinary_voice_request_and_offline_non_tool_telling_keep_their_existing_paths():
    cast, state = table()
    SCRIPT.extend([
        {"kind": "npc", "npc": "warden", "instruction": "按本人实际知情回答。"},
        {"scene": {}, "text": "旧钟敲了一声。"},
        "你仍在钟楼门口。",
    ])
    try:
        voice = await cast.keeper(state, "需要回应", voices=["warden"], check=lambda _: True)
        assert isinstance(voice.call, KeeperCall) and voice.call.npc == "warden"
        telling = await cast.keeper(state, "离线叙事检查", check=lambda _: True)
        assert telling.text == "旧钟敲了一声。" and not telling.call
        plain = await cast.keeper(state, "普通回顾")
        assert plain == "你仍在钟楼门口。"
        assert [call["schema"].get("title") if call["schema"] else None for call in SEEN] == [
            "HostedReply", "Telling", None]
    finally:
        await cast.close()
