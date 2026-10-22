"""One keeper session carries a request's next prefix, without replaying player input."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from autokeeper import config
from autokeeper.agents.cast import (
    ACTION_RULES,
    CONTINUED_TELLING,
    PLAN_SYSTEM,
    TELLING,
    Action,
    Cast,
    Continuation,
    ContinuedTelling,
    Narration,
    Plan,
)
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401


def table():
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    return cast, state


def request(state):
    row = {
        "id": "request-1",
        "actor": state.investigator.id,
        "original": "先问守钟人借钥匙，再在这里等到20:35；有人打断就先停下。",
        "origin_turn": 1,
        "part": 0,
        "started": state.minutes,
        "previous_turn": 0,
        "remaining": "在这里等到20:35；有人打断就先停下。",
        "completed": [{"turn": 1, "actual": "守钟人已把钥匙交给调查员；无需检定。"}],
    }
    state.pending_turn["request"] = row
    return row


def waiting():
    return {"action": {"kind": "wait", "summary": "周衡原地等到20:35"}, "remaining": ""}


def test_plan_partitions_intent_without_prearranging_results():
    plan = Plan(action=Action(kind="talk", summary="周衡向守钟人借钥匙", npc="warden"),
                remaining="拿到钥匙后查看房间，然后返回报社。")
    assert plan.action.destination == "" and plan.remaining.startswith("拿到钥匙后")
    assert Plan(action=plan.action).remaining == ""
    assert "当前可执行前缀" in ACTION_RULES and "remaining" in PLAN_SYSTEM
    assert "等回答后由玩家继续" not in ACTION_RULES


@pytest.mark.parametrize("value", [{"next": waiting()}, {"pending": "等玩家回答守钟人的问题，再考虑原安排。"}, {}])
def test_continuation_expresses_next_pause_or_done(value):
    decision = Continuation.model_validate(value)
    assert bool(decision.next) == bool(value.get("next"))
    assert decision.pending == value.get("pending", "")


def test_continuation_rejects_executing_and_returning_the_same_request():
    with pytest.raises(ValidationError, match="不能同时非空"):
        Continuation.model_validate({"next": waiting(), "pending": "等待玩家新选择。"})
    assert Continuation(next=Plan.model_validate(waiting()), pending="  ").pending == ""


def test_continuation_cannot_discard_an_unexecuted_lookup():
    plan = waiting() | {"lookup": "先查清这座钟楼今晚何时关门"}
    # Lookup remains legal for the initial planner, which actually performs it.
    assert Plan.model_validate(plan).lookup
    with pytest.raises(ValidationError, match=r"next.lookup 必须为空"):
        Continuation.model_validate({"next": plan})
    assert Continuation(pending="等查明关门时间后，再决定是否继续等候。").next is None


def test_only_the_extended_telling_requires_a_continuation():
    assert "continuation" not in TELLING["properties"]
    assert "continuation" in CONTINUED_TELLING["required"]
    with pytest.raises(ValidationError, match="continuation"):
        ContinuedTelling.model_validate({"scene": {}, "text": "他交出了钥匙。"})
    assert ContinuedTelling(scene={}, text="", continuation={}).continuation.next is None


async def test_remaining_uses_the_same_keeper_call_and_session_with_original_and_actual_results():
    cast, state = table()
    try:
        SCRIPT.append({"scene": {}, "text": "钟楼里很安静。"})
        ordinary = await cast.keeper(state, "周衡走进钟楼。", check=lambda _: True)
        session = cast.sessions["keeper"]
        assert isinstance(ordinary, Narration) and ordinary.continuation is None
        assert SEEN[0]["schema"] == TELLING

        row = request(state)
        SCRIPT.append({"scene": {}, "text": "钥匙已经交到了你手里。", "continuation": {"next": waiting()}})
        told = await cast.keeper(state, "实际结果：守钟人交出钥匙，当前段结束。", check=lambda _: True)
        assert cast.sessions["keeper"] is session and set(cast.sessions) == {"keeper"}
        assert len(SEEN) == 2 and SEEN[1]["schema"] == CONTINUED_TELLING
        prompt = SEEN[1]["messages"][-1]["content"]
        assert row["original"] in prompt and row["remaining"] in prompt
        assert row["completed"][0]["actual"] in prompt and "当前段结束" in prompt
        assert told.continuation.next.action.kind == "wait" and not told.continuation_error
        # A proposed future plan is not an executed action or a second player declaration.
        assert state.turn == 0 and state.minutes == row["started"] and state.log == []
        assert state.transcripts["keeper"][-1]["content"] == told.text
        assert "continuation" not in state.transcripts["keeper"][-1]["content"]

        state.pending_turn.clear()
        SCRIPT.append({"scene": {}, "text": "钟声响起。"})
        await cast.keeper(state, "钟声响起。", check=lambda _: True)
        assert len(SEEN) == 3 and SEEN[2]["schema"] == TELLING and cast.sessions["keeper"] is session
    finally:
        await cast.close()


@pytest.mark.parametrize("remaining,actor", [("", None), ("  ", None), ("稍后查看房间", "someone_else")])
async def test_empty_or_another_actors_request_keeps_the_ordinary_schema_and_call_count(remaining, actor):
    cast, state = table()
    try:
        row = request(state)
        row.update(remaining=remaining, actor=actor or state.investigator.id)
        SCRIPT.append({"scene": {}, "text": "屋里静了下来。"})
        told = await cast.keeper(state, "屋里静了下来。", check=lambda _: True)
        assert len(SEEN) == 1 and SEEN[0]["schema"] == TELLING
        assert told.continuation is None and not told.continuation_error
        assert "【本次原请求的续办】" not in SEEN[0]["messages"][-1]["content"]
    finally:
        await cast.close()


async def test_plain_keeper_path_still_requires_structured_continuation_when_remaining():
    cast, state = table()
    try:
        request(state)
        SCRIPT.append({"scene": {}, "text": "", "continuation": {"pending": "守钟人提出问题，等玩家回答。"}})
        told = await cast.keeper(state, "守钟人问你为何借钥匙。")
        assert isinstance(told, Narration) and told.text == ""
        assert told.continuation.pending == "守钟人提出问题，等玩家回答。"
        assert len(SEEN) == 1 and SEEN[0]["schema"] == CONTINUED_TELLING
    finally:
        await cast.close()


async def test_missing_continuation_reuses_the_harness_single_format_repair():
    cast, state = table()
    try:
        request(state)
        SCRIPT.extend([
            {"scene": {}, "text": "他把钥匙递给了你。"},
            {"scene": {}, "text": "他把钥匙递给了你。", "continuation": {"next": waiting()}},
        ])
        told = await cast.keeper(state, "钥匙已交付。", check=lambda _: True)
        assert len(SEEN) == 2 and not SCRIPT and set(cast.sessions) == {"keeper"}
        assert "continuation" in SEEN[1]["messages"][-1]["content"]
        assert told.continuation.next.action.kind == "wait" and not told.continuation_error
    finally:
        await cast.close()


async def test_missing_continuation_after_repair_preserves_the_telling_as_an_explicit_error():
    cast, state = table()
    try:
        request(state)
        SCRIPT.extend([{"scene": {}, "text": "钥匙已经交付。"}, {"scene": {}, "text": "钥匙已经交付。"}])
        told = await cast.keeper(state, "钥匙已交付。", check=lambda _: True)
        assert len(SEEN) == 2 and not SCRIPT
        assert told.scene == {} and told.text == "钥匙已经交付。"
        assert told.continuation is None and "不能视为完成" in told.continuation_error
    finally:
        await cast.close()


async def test_conflicting_continuation_is_an_error_instead_of_done():
    cast, state = table()
    try:
        request(state)
        SCRIPT.append({"scene": {}, "text": "钥匙已经交付。",
                       "continuation": {"next": waiting(), "pending": "等玩家作新选择。"}})
        told = await cast.keeper(state, "钥匙已交付。", check=lambda _: True)
        assert len(SEEN) == 1 and told.continuation is None and told.continuation_error
    finally:
        await cast.close()


async def test_unfinished_context_is_personal_and_new_input_wins_without_replaying_a_plan():
    cast, state = table()
    try:
        who = state.investigator.id
        personal = {"original": "先核对私人账目，再离开。", "remaining": "核对私人账目。",
                    "completed": [{"actual": "只取得了账本，尚未核对。"}]}
        state = state.model_copy(update={"unfinished": {who: personal, "someone_else": {"original": "另一个人的密谈。"}}})
        prompt = cast._heard(state, "取消核账，我先离开。")
        assert personal["original"] in prompt and personal["completed"][0]["actual"] in prompt
        assert "取消核账，我先离开。" in prompt and "以本次玩家新输入为先" in prompt
        assert "本人未办意图的当前版本" in prompt and "旧意图合并到 remaining" in prompt
        assert "不能自动恢复旧动作" in prompt and "另一个人的密谈" not in prompt

        SCRIPT.append({"text": "钟楼还没关门。"})
        await cast.npc(state, "warden", "你还在这里吗？")
        assert len(SEEN) == 1 and "私人账目" not in json.dumps(SEEN[0], ensure_ascii=False)
        assert "另一个人的密谈" not in json.dumps(SEEN[0], ensure_ascii=False)
    finally:
        await cast.close()
