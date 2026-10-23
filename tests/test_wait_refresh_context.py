"""The real keeper transport receives a resumed intention, not a recycled new declaration."""

from __future__ import annotations

import json

import pytest

from .test_keeper_tools_execution import table
from .test_scene import SCRIPT, SEEN, sandbox  # noqa: F401

ORIGINAL = "先把封信交给守钟人，再守在门口十分钟；有人求救就停止。"
COMPLETED = "封信已交给守钟人，他接过放进内袋。"
WAIT = {"kind": "wait", "summary": "守在门口到原定时限；有人求救就停止", "target": "门口",
        "wait_limit": 10, "wait_until": ""}
PRIVATE = "SHOULD_NEVER_ENTER_A_PLAN_PROMPT_74619"


def pending(game, *, refreshing):
    state, actor = game.state, game.state.investigator.id
    completed = [{"turn": 1, "action": COMPLETED, "start": 1220, "end": 1222, "checks": []}]
    state.unfinished[actor] = {
        "original": ORIGINAL, "completed": completed, "remaining": "继续门口守候。", "reason": "等候期间有实际变化。",
        "execution": {"request": {"wait_window": {"start": 1222, "end": 1232}},
                      "arrangement": {"plan": {"people": PRIVATE}}, "ruling": {"reason": PRIVATE}},
        "backstage": PRIVATE,
    }
    state.pending_turn = {"wait_refresh": refreshing, "request": {
        "actor": actor, "original": ORIGINAL, "completed": completed, "remaining": "继续门口守候。",
        "wait_window": {"start": 1222, "end": 1232}, "wait_action": {**WAIT, "secret": PRIVATE},
        "world_state": PRIVATE, "part": 1,
    }}


@pytest.mark.parametrize("refreshing", [True, False])
async def test_real_plan_receives_only_the_wait_intent_actual_prefix_and_original_deadline(fake, refreshing):
    game, _graph, cast = table(fake, party=True)
    pending(game, refreshing=refreshing)
    SCRIPT.append({"action": WAIT})
    try:
        plan = await cast.plan(game.state, ORIGINAL)
        assert plan.action.kind == "wait" and not SCRIPT and len(SEEN) == 1
        prompt = SEEN[0]["messages"][-1]["content"]
        assert ORIGINAL in prompt and COMPLETED in prompt and "继续门口守候" in prompt
        assert '"start": 1222, "end": 1232' in prompt
        assert PRIVATE not in prompt and '"execution"' not in prompt and '"arrangement"' not in prompt
        if refreshing:
            assert "【自动续办：按当前记录重新判断原等待】" in prompt
            assert "【玩家】" not in prompt and "以本次玩家新输入为先" not in prompt
            assert "不是玩家的新声明" in prompt and "不能从现在重新计算相对时长" in prompt
            assert "不得重做其中的交谈、交物、检定、移动或其他前缀" in prompt
            assert json.dumps(WAIT, ensure_ascii=False) in prompt
        else:
            assert "【玩家】" + ORIGINAL in prompt and "以本次玩家新输入为先" in prompt
            assert "【自动续办" not in prompt
    finally:
        await game.close()
        await cast.close()


async def test_another_actors_wait_refresh_is_not_treated_as_this_players_automatic_request(fake):
    game, _graph, cast = table(fake, party=True)
    pending(game, refreshing=True)
    game.state.pending_turn["request"].update(actor="lin", original=PRIVATE)
    SCRIPT.append({"action": {"kind": "use", "summary": "周衡收起手边的工具"}})
    try:
        await cast.plan(game.state, "我改了主意，先收起工具。")
        prompt = SEEN[0]["messages"][-1]["content"]
        assert "【玩家】我改了主意，先收起工具。" in prompt
        assert PRIVATE not in prompt and "【自动续办" not in prompt
        assert "封信已交给守钟人" in prompt
        assert len(SEEN) == 1 and not SCRIPT
    finally:
        await game.close()
        await cast.close()
