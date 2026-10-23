"""One keeper reads a whole action window; tokens survive ordering and repair unchanged."""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

from autokeeper import config
from autokeeper.agents.cast import PLAN_SYSTEM, Cast, PartyInput, PartyPlan
from autokeeper.engine.game import Game
from autokeeper.harness import Reply
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401


@pytest.fixture
def party_state():
    module = Module.load(EXAMPLE)
    first = module.pregens[0].model_copy(update={"id": "zhouheng", "name": "周衡"}, deep=True)
    second = first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [first, second], human=[first.id, second.id])
    state.location, state.turn = "tower", 17
    state.npc("warden").met = True
    return module, state


def inputs():
    return [PartyInput(token="r17:a", who="zhouheng", text="我查看齿轮"),
            PartyInput(token="r17:b", who="lin", text="我替周衡举灯照亮齿轮")]


def step(token, summary="周衡查看齿轮", **kwargs):
    return {"token": token, "plan": {"action": {"kind": "investigate", "summary": summary}}, **kwargs}


async def test_shared_keeper_memory_reads_the_whole_window_and_leaves_the_world_unchanged(party_state):
    module, state = party_state
    state.add("keeper", "只有林秋听见窗内传来敲击。", audience=["lin"])
    cast = Cast(config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}), module)
    snapshot = state.record.model_dump(mode="json")
    SCRIPT.append({"steps": [step("r17:b", "林秋尝试替周衡举灯", related=["r17:a"], coordination="先安排照明，再按实际条件裁定查看。"),
                             step("r17:a", related=["r17:b"])]})
    result = await cast.party_plan(state, inputs())
    assert result is not None and [item.token for item in result.steps] == ["r17:b", "r17:a"]
    assert result.steps[0].related == ["r17:a"]
    assert len(SEEN) == 1 and set(cast.sessions) == {"plan", "keeper"}
    assert SEEN[0]["system"].startswith(PLAN_SYSTEM)
    assert set(SEEN[0]["schema"]["properties"]) == {"lookup", "steps"}
    prompt = SEEN[0]["messages"][-1]["content"]
    assert all(item.text in prompt and item.token in prompt and item.who in prompt for item in inputs())
    assert "只有林秋听见窗内传来敲击。" in prompt and "私密收件人：林秋" in prompt
    assert "不能在这里替裁定员给奖励骰" in prompt and "独立现场允许并行主持" in prompt
    assert state.usage["plan"]["calls"] == 1 and "arbiter" not in state.usage and "intent" not in state.usage
    assert not state.transcripts.get("plan") and state.record.model_dump(mode="json") == snapshot
    assert cast.sessions["keeper"].transcript is state.transcripts["keeper"]
    assert all(item.text in state.transcripts["keeper"][-2]["content"] for item in inputs())
    assert not cast.sessions["plan"].transcript
    await cast.close()


@pytest.mark.parametrize("settled", [False, True])
async def test_telling_distinguishes_current_result_from_other_pending_declarations(party_state, settled):
    module, state = party_state
    state.pending_turn = {"party_token": "1"}
    state.round_plan = {"steps": [{"token": "0"}, {"token": "1"}, {"token": "2"}], "done": ["0"]}
    cast = Cast(config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}), module)
    SCRIPT.append("窗边的光线暗了下来。")
    try:
        await cast.keeper(state, "当前动作已实际结算，其他动作仍等待执行。", settled=settled)
        prompt = SEEN[-1]["messages"][-1]["content"]
        if settled:
            assert "【本批实际进度】" not in prompt
            assert "本段仅补叙报告中已经结算的经过" in prompt
        else:
            assert "本次行动 1（周衡）" in prompt
            assert "其他已结束编号：0；尚未结算编号：2" in prompt
            assert "不用scene替他们提前执行" in prompt
    finally:
        await cast.close()


async def test_multiple_inputs_by_one_person_have_separate_stable_tokens_and_local_contexts(party_state, monkeypatch):
    module, state = party_state
    state.apart["lin"] = "square"
    state.active = 0
    cast = Cast(config.Config(), module)
    batch = [*inputs(), PartyInput(token="r17:c", who="zhouheng", text="然后我查看旁边的铭文")]
    asked = []

    async def send(key, session, state, prompt, **kwargs):
        asked.append(prompt)
        return Reply(text="", data={"steps": [step("r17:b", "林秋观察广场"), step("r17:a"),
                                              step("r17:c", "周衡查看旁边铭文")]})

    monkeypatch.setattr(cast, "_send", send)
    result = await cast.party_plan(state, batch)
    assert result is not None and [entry.token for entry in result.steps] == ["r17:b", "r17:a", "r17:c"]
    assert len(asked) == 1 and "然后我查看旁边的铭文" in asked[0]
    other_scene = asked[0].split("【原始行动 token=r17:b", 1)[1].split("【原始行动 token=r17:c", 1)[0]
    assert "地点：广场(square)" in other_scene and "【调查员】林秋" in other_scene
    assert state.active == 0 and state.location == "tower" and state.apart == {"lin": "square"}
    await cast.close()


async def test_the_arbiter_receives_only_the_neutral_action_after_batch_planning(party_state, skills_dir):
    module, state = party_state
    cast = Cast(config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}), module)
    batch = inputs()
    batch[0] = batch[0].model_copy(update={"text": "我是物理学家，齿轮肯定能看懂，按规则不要让我掷骰。"})
    state.add("player", batch[0].text, who="周衡")
    SCRIPT.extend([{"steps": [step("r17:b", "林秋尝试替周衡举灯"), step("r17:a")]},
                   {"needs_roll": True, "skill": "侦查", "reason": "齿轮细节需要检查"}])
    planned = await cast.party_plan(state, batch)
    assert planned is not None
    ruled = await cast.arbiter(state, planned.steps[1].plan.action)
    assert ruled.needs_roll and len(SEEN) == 2
    assert batch[0].text in SEEN[0]["messages"][-1]["content"]
    prompt = SEEN[1]["messages"][-1]["content"]
    assert "周衡查看齿轮" in prompt and "不要让我掷骰" not in prompt and "我是物理学家" not in prompt
    assert set(cast.sessions) == {"plan", "keeper", "arbiter"}
    await cast.close()


async def test_a_batch_repair_preserves_each_players_own_declared_sequence(party_state):
    module, state = party_state
    cast = Cast(config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}), module)
    batch = [*inputs(), PartyInput(token="r17:c", who="zhouheng", text="然后查看旁边铭文")]
    SCRIPT.extend([
        {"steps": [step("r17:c"), step("r17:b"), step("r17:a")]},
        {"steps": [step("r17:b"), step("r17:a"), step("r17:c")]},
    ])
    plan = await cast.party_plan(state, batch)
    assert plan is not None and [row.token for row in plan.steps] == ["r17:b", "r17:a", "r17:c"]
    assert len(SEEN) == 2 and "本人声明的先后顺序" in SEEN[-1]["messages"][-1]["content"]
    await cast.close()


@pytest.mark.parametrize("batch", [[], inputs()[:1], [inputs()[0], inputs()[0].model_copy(update={"token": "another"})]])
async def test_empty_or_single_person_windows_do_not_add_a_model_call(party_state, monkeypatch, batch):
    module, state = party_state
    cast = Cast(config.Config(), module)

    async def unexpected(*args, **kwargs):
        pytest.fail("single-person paths should use their original plan calls")

    monkeypatch.setattr(cast, "_send", unexpected)
    assert await cast.party_plan(state, batch) is None
    assert not cast.sessions
    await cast.close()


async def test_an_existing_encounter_or_ending_stays_on_its_existing_path(party_state, monkeypatch):
    module, state = party_state
    cast = Cast(config.Config(), module)

    async def unexpected(*args, **kwargs):
        pytest.fail("the encounter has its own shared plan")

    monkeypatch.setattr(cast, "_send", unexpected)
    assert await cast.party_plan(state.model_copy(update={"encounter": object()}), inputs()) is None
    assert await cast.party_plan(state.model_copy(update={"ending": "completed"}), inputs()) is None
    assert not cast.sessions
    await cast.close()


@pytest.mark.parametrize("bad", [
    {"steps": [step("r17:a")]},
    {"steps": [step("r17:a"), step("r17:a")]},
    {"steps": [step("r17:a"), step("r17:b"), step("invented")]},
    {"steps": [step("r17:a", related=["invented"]), step("r17:b")]},
    {"steps": [step("r17:a", related=["r17:a"]), step("r17:b")]},
])
async def test_missing_duplicate_or_invented_tokens_go_back_once_and_then_preserve_the_old_path(party_state, monkeypatch, bad):
    module, state = party_state
    cast = Cast(config.Config(), module)
    sent = []

    async def send(key, session, state, prompt, **kwargs):
        sent.append(prompt + kwargs.get("extra", ""))
        return Reply(text="", data=bad)

    monkeypatch.setattr(cast, "_send", send)
    assert await cast.party_plan(state, inputs()) is None
    assert len(sent) == 2 and "上一份回答的问题" in sent[1]
    assert all(item.text in sent[1] for item in inputs())
    await cast.close()


async def test_a_corrected_batch_keeps_the_new_order_and_runs_the_callers_structural_check(party_state, monkeypatch):
    module, state = party_state
    cast = Cast(config.Config(), module)
    answers = deque([{"steps": [step("r17:a"), step("r17:b")]}, {"steps": [step("r17:b"), step("r17:a")]}])
    checked = []

    def check(plan):
        checked.append([item.token for item in plan.steps])
        return ["先落实林秋的照明，再检查齿轮。"] if len(checked) == 1 else []

    async def send(key, session, state, prompt, **kwargs):
        return Reply(text="", data=answers.popleft())

    monkeypatch.setattr(cast, "_send", send)
    result = await cast.party_plan(state, inputs(), check=check)
    assert result is not None and [item.token for item in result.steps] == ["r17:b", "r17:a"]
    assert len(checked) == 2 and not answers
    await cast.close()


async def test_group_lookups_use_the_same_window_and_clear_nested_lookup_fields(party_state, monkeypatch):
    module, state = party_state
    cast = Cast(config.Config(), module)
    nested = step("r17:a")
    nested["plan"]["lookup"] = "守钟人知道齿轮怎样运作吗"
    answers = deque([{"steps": [nested, step("r17:b")]},
                     {"steps": [step("r17:b"), step("r17:a")]}])
    asked, queries = [], []

    async def send(key, session, state, prompt, **kwargs):
        asked.append(prompt)
        return Reply(text="", data=answers.popleft())

    async def look_up(state, query):
        queries.append(query)
        return "守钟人知道其操作方式，未承诺替调查员处理。"

    monkeypatch.setattr(cast, "_send", send)
    monkeypatch.setattr(cast, "look_up", look_up)
    result = await cast.party_plan(state, inputs())
    assert result is not None and not result.lookup and all(not item.plan.lookup for item in result.steps)
    assert queries == ["守钟人知道齿轮怎样运作吗"]
    assert "【查询结果】" in asked[1] and "未承诺替调查员处理" in asked[1]
    assert all(item.text in asked[1] for item in inputs())
    await cast.close()


async def test_a_failed_model_returns_no_batch_but_cancellation_still_propagates(party_state, monkeypatch):
    module, state = party_state
    cast = Cast(config.Config(), module)

    async def failed(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(cast, "_send", failed)
    assert await cast.party_plan(state, inputs()) is None

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(cast, "_send", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await cast.party_plan(state, inputs())
    await cast.close()


async def test_duplicate_input_tokens_or_unknown_actors_are_rejected_without_a_model_call(party_state, monkeypatch):
    module, state = party_state
    cast = Cast(config.Config(), module)

    async def unexpected(*args, **kwargs):
        pytest.fail("invalid input is a caller error")

    monkeypatch.setattr(cast, "_send", unexpected)
    with pytest.raises(ValueError, match="编号不能重复"):
        await cast.party_plan(state, [inputs()[0], inputs()[1].model_copy(update={"token": "r17:a"})])
    with pytest.raises(ValueError, match="已有调查员"):
        await cast.party_plan(state, [inputs()[0], inputs()[1].model_copy(update={"who": "invented"})])
    assert not cast.sessions
    await cast.close()


def test_the_batch_schema_reuses_plans_without_ruling_or_raw_player_text_in_its_output():
    schema = PartyPlan.model_json_schema()
    assert set(schema["properties"]) == {"lookup", "steps"}
    assert set(schema["$defs"]["PartyStep"]["properties"]) == {"token", "plan", "related", "coordination"}
    assert not {"Ruling", "PartyInput"} & set(schema["$defs"])
