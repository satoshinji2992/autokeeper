"""Planning and telling share one durable memory while using their own model transports."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from types import SimpleNamespace

import pytest

from autokeeper import config, paths
from autokeeper.agents.cast import Cast, PartyInput
from autokeeper.config import Role
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.harness import ContextBudgetExceeded, Session
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module


@pytest.fixture
def transport(monkeypatch):
    calls, answers = [], deque()

    class Transport(hm.Model):
        mode = "json"

        async def _call(self, messages, *, schema, images, on_delta):
            calls.append({"model": self.spec.model, "effort": self.spec.effort,
                          "limit": self.spec.max_input_chars, "system": self.system,
                          "messages": [dict(row) for row in messages], "schema": schema,
                          "context": self._input_parts(messages, schema)})
            out = answers.popleft()
            text = json.dumps(out, ensure_ascii=False) if isinstance(out, dict) else out
            if on_delta:
                await on_delta(text)
            return hm.Reply(text=text, data=out if isinstance(out, dict) else None,
                            usage={"input_tokens": 10, "output_tokens": 3})

    monkeypatch.setitem(hm.MODELS, "shared-memory", Transport)
    monkeypatch.setenv("AUTOKEEPER_TRACE", "off")
    return SimpleNamespace(calls=calls, answers=answers)


def table():
    module = Module.load(paths.EXAMPLE)
    first = module.pregens[0].model_copy(deep=True)
    other = first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [first, other], human=[first.id, other.id])
    state.location, state.turn = "tower", 1
    roles = {role: Role("shared-memory", role, "high") for role in config.DEFAULT_ROLES}
    roles["plan"] = Role("shared-memory", "planner", "low", max_input_chars=100_000)
    roles["keeper"] = Role("shared-memory", "host", "high", max_input_chars=200_000)
    return Cast(config.Config(roles=roles), module), state


def plan(summary="周衡整理工具", **extra):
    return {"action": {"kind": "use", "summary": summary}, **extra}


def tell(text="老人留在门边。"):
    return {"kind": "tell", "telling": {"scene": {}, "text": text}}


def batch(first="a", second="b"):
    return {"steps": [{"token": first, "plan": plan()}, {"token": second, "plan": plan("林秋整理衣物")}]}


async def test_plan_voice_telling_batch_and_next_plan_share_one_history_but_keep_phase_transports(transport):
    cast, state = table()
    transport.answers.extend([plan(), {"kind": "npc", "npc": "warden", "instruction": "询问来访者是否需要帮忙。"},
                              tell(), batch(), plan("周衡等候")])
    try:
        await cast.plan(state, "我不声张，先整理工具。")
        session = cast.sessions["keeper"]
        await cast.keeper(state, "整份动态现场仅本轮使用。", remember="【实际执行】器材已经整理。",
                          voices=["warden"], check=lambda _: True)
        assert json.loads(session.transcript[-1]["content"])["kind"] == "npc"
        await cast.keeper(state, "NPC实际回应：老人留在门边。", remember="【实际回应】老人询问是否需要帮忙。",
                          voices=[], check=lambda _: True)
        assert session.transcript[-1]["content"] == "老人留在门边。"
        await cast.party_plan(state, [PartyInput(token="a", who=state.investigators[0].id, text="我整理工具。"),
                                      PartyInput(token="b", who="lin", text="我整理衣物。")])
        await cast.plan(state, "我先在这里等着。")

        assert cast.sessions["keeper"] is session and session.transcript is state.transcripts["keeper"]
        assert "plan" not in state.transcripts and not cast.sessions["plan"].transcript
        assert cast.sessions["plan"]._folding is None and cast.sessions["plan"].memory == 0
        assert [row["model"] for row in transport.calls] == ["planner", "host", "host", "planner", "planner"]
        assert [row["effort"] for row in transport.calls] == ["low", "high", "high", "low", "low"]
        assert [row["limit"] for row in transport.calls] == [100_000, 200_000, 200_000, 100_000, 100_000]
        assert [row["schema"]["title"] for row in transport.calls] == ["Plan", "HostedReply", "HostedReply", "PartyPlan", "Plan"]
        assert session.schema is None and session.model.spec.model == "host" and session.spec.model == "host"
        assert state.usage["plan"]["calls"] == 3 and state.usage["keeper"]["calls"] == 2
        assert [row["session"] for row in cast.debug_calls] == ["plan", "keeper", "keeper", "plan", "plan"]
        last = str(transport.calls[-1]["messages"])
        assert "我不声张，先整理工具。" in last and "老人留在门边。" in last and "询问来访者是否需要帮忙" in last
        assert '"token": "a"' in last and '"actor": "lin"' in last
        users = [row["content"] for row in session.transcript if row["role"] == "user"]
        assert "整份动态现场仅本轮使用。" not in "".join(users)
        assert all("尚未执行" in text for text in (users[0], users[-2], users[-1]))
    finally:
        await cast.close()


@pytest.mark.parametrize("mode", ["bad-call", "bad-scene"])
async def test_remember_matches_rejected_tellings_without_removing_the_prior_plan(transport, mode):
    cast, state = table()
    transport.answers.extend([plan(), ({"kind": "npc", "npc": "warden", "instruction": "开口。", "telling": {"scene": {}, "text": "未定。"}}
                                      if mode == "bad-call" else tell("尚未成立。"))])
    try:
        await cast.plan(state, "先整理工具。")
        prior = [dict(row) for row in state.transcripts["keeper"]]
        response = await cast.keeper(state, "很长的完整主持报告", remember="本段实际新增结果", voices=["warden"],
                                     check=lambda _: mode != "bad-scene")
        assert bool(response.call_error) is (mode == "bad-call")
        assert state.transcripts["keeper"] == prior
    finally:
        await cast.close()


async def test_reloaded_planning_reads_the_same_keeper_transcript_without_making_another_owner(transport):
    cast, state = table()
    restored_cast = None
    transport.answers.extend([plan(), tell("你把铜盒留在原处。"), plan("周衡继续等候")])
    try:
        await cast.plan(state, "先看看铜盒，不带走。")
        await cast.keeper(state, "铜盒仍在桌上。", remember="本段确实没有移动铜盒。", voices=[], check=lambda _: True)
        restored = GameState.model_validate_json(state.model_dump_json())
        restored_cast = Cast(cast.cfg, cast.module)
        await restored_cast.plan(restored, "我继续等候。")
        prompt = str(transport.calls[-1]["messages"])
        assert "先看看铜盒，不带走。" in prompt and "你把铜盒留在原处。" in prompt
        assert restored_cast.sessions["keeper"].transcript is restored.transcripts["keeper"]
        assert not restored_cast.sessions["plan"].transcript and "plan" not in restored.transcripts
    finally:
        await cast.close()
        if restored_cast is not None:
            await restored_cast.close()


async def test_query_and_repair_remember_the_actual_lookup_and_draft_problem_not_the_full_scene(transport, monkeypatch):
    cast, state = table()
    transport.answers.extend([plan(lookup="借阅铜盒的约定"), plan("尚未修正的草案"), plan("周衡查看铜盒")])

    async def lookup(*_):
        return "约定来源记录：只准在原地查看。"

    monkeypatch.setattr(cast, "look_up", lookup)
    try:
        actual = await cast.plan(state, "我在原地看看铜盒。", check=lambda p: ["不得把铜盒带走"]
                                 if p.action.summary == "尚未修正的草案" else [])
        assert actual.action.summary == "周衡查看铜盒"
        users = [row["content"] for row in state.transcripts["keeper"] if row["role"] == "user"]
        assert len(users) == 3 and all("尚未执行" in row for row in users)
        assert "借阅铜盒的约定" in users[1] and "约定来源记录：只准在原地查看。" in users[1]
        assert "不得把铜盒带走" in users[2] and "【草案未落实原因】" in users[2]
        assert all("【可能动手的】" not in row and "【场景】" not in row for row in users)
        assert all(call["model"] == "planner" for call in transport.calls)
    finally:
        await cast.close()


async def test_a_party_repair_keeps_actor_tokens_and_failure_reason_in_the_shared_draft_history(transport):
    cast, state = table()
    transport.answers.extend([batch("a", "a"), batch()])
    try:
        result = await cast.party_plan(state, [PartyInput(token="a", who=state.investigators[0].id, text="我查看铜盒。"),
                                              PartyInput(token="b", who="lin", text="我留在门边。")])
        assert result is not None and [step.token for step in result.steps] == ["a", "b"]
        users = [row["content"] for row in state.transcripts["keeper"] if row["role"] == "user"]
        assert len(users) == 2 and '"token": "b"' in users[0] and '"actor": "lin"' in users[0]
        assert "【草案未落实原因】" in users[1] and "尚未执行" in users[1]
        assert "【共用场景" not in "".join(users) and "plan" not in state.transcripts
    finally:
        await cast.close()


@pytest.mark.parametrize("selected_limit,default_limit,blocked", [(100, 2000, True), (2000, 100, False)])
async def test_a_model_override_uses_its_own_input_budget_without_mutating_the_session(transport, selected_limit, default_limit, blocked):
    owner = Session("keeper", Role("shared-memory", "host", "high", max_input_chars=default_limit), "主持", memory=5000)
    alternate = hm.make_model(Role("shared-memory", "planner", "low", max_input_chars=selected_limit), "计划工具", label="plan")
    prompt = "较长输入" * 80
    transport.answers.append("完成")
    try:
        measured = owner.context_size(prompt, model=alternate)
        assert measured["max_input_chars"] == selected_limit
        if blocked:
            with pytest.raises(ContextBudgetExceeded) as error:
                await owner.send(prompt, model=alternate)
            assert error.value.parts["max_input_chars"] == selected_limit and not transport.calls
        else:
            assert (await owner.send(prompt, model=alternate)).text == "完成"
            assert transport.calls[-1]["model"] == "planner"
            assert transport.calls[-1]["context"]["max_input_chars"] == selected_limit
        assert owner.spec.max_input_chars == default_limit and owner.model.spec.model == "host"
    finally:
        await owner.close()
        await alternate.close()


async def test_a_stricter_plan_transport_compacts_the_canonical_memory_before_sending(transport):
    history = [
        {"role": "user", "content": "旧的未执行安排" * 100}, {"role": "assistant", "content": "旧草案" * 100},
        {"role": "user", "content": "中途请求" * 100}, {"role": "assistant", "content": "中途回应" * 100},
        {"role": "user", "content": "最近实际发生的事"}, {"role": "assistant", "content": "老人留下了"},
    ]
    summaries = []

    async def summarize(text, who):
        summaries.append((text, who))
        return "旧约定尚未兑现。"

    owner = Session("keeper", Role("shared-memory", "host", "high", max_input_chars=100_000), "主持", memory=10_000,
                    transcript=history, notes={}, key="keeper", summarizer=summarize)
    alternate = hm.make_model(Role("shared-memory", "planner", "low", max_input_chars=200), "计划", label="plan")
    transport.answers.append("新草案")
    try:
        assert (await owner.send("下一步计划", model=alternate)).text == "新草案"
        assert len(summaries) == 1 and summaries[0][1] == "keeper"
        assert owner.notes == {"keeper": "旧约定尚未兑现。"}
        sent = str(transport.calls[0]["messages"])
        assert "最近实际发生的事" in sent and "旧约定尚未兑现" in sent and "旧草案旧草案" not in sent
        assert transport.calls[0]["context"]["total_chars"] <= 200
        assert owner.spec.max_input_chars == 100_000 and owner.model.spec.model == "host"
    finally:
        await owner.close()
        await alternate.close()


async def test_plan_and_keeper_wait_for_one_canonical_background_compaction(transport):
    cast, state = table()
    entered, release = asyncio.Event(), asyncio.Event()
    summaries = []
    keeper = cast._keeper(state)
    keeper.memory = 200

    async def summarize(text, who):
        summaries.append((text, who))
        entered.set()
        await release.wait()
        return "旧安排尚未执行；已讲述的经过以正式记录为准。"

    keeper.summarizer = summarize
    transport.answers.extend([plan(), tell(), plan("周衡继续等待")])
    try:
        await cast.plan(state, "我先把手里的工具一件件排好，并记下它们的位置。" * 10)
        await cast.keeper(state, "实际结果", remember="已实际摆好工具。", voices=[], check=lambda _: True)
        await asyncio.wait_for(entered.wait(), timeout=1)
        pending = asyncio.create_task(cast.plan(state, "接着等候。"))
        await asyncio.sleep(0)
        assert len(transport.calls) == 2 and not pending.done()
        assert cast.sessions["plan"]._folding is None and not cast.sessions["plan"].transcript
        release.set()
        await pending
        await keeper.settle()
        assert summaries and all(who == "keeper" for _, who in summaries)
        assert "keeper" in state.notes and "plan" not in state.notes
        assert "旧安排尚未执行" in str(transport.calls[-1]["messages"])
    finally:
        release.set()
        await cast.close()


@pytest.mark.parametrize("remember", [None, "本段已发生结果的记忆增量"])
async def test_planning_schema_does_not_become_the_default_for_retell_or_epilogue(transport, remember):
    cast, state = table()
    transport.answers.extend([plan(), "本段普通叙述。", "换一种说法。", "结局以后。"])
    try:
        await cast.plan(state, "我等着。")
        assert await cast.keeper(state, "本段报告", remember=remember) == "本段普通叙述。"
        assert await cast.retell(state, "本段报告", "本段普通叙述。", remember=remember) == "换一种说法。"
        assert await cast.epilogue(state, "说明结局以后。") == "结局以后。"
        assert [row["schema"] is None for row in transport.calls] == [False, True, True, True]
        assert cast.sessions["keeper"].schema is None and state.transcripts["keeper"][-1]["content"] == "换一种说法。"
        assert state.transcripts["keeper"][-2]["content"] == ("本段报告" if remember is None else remember)
        assert transport.calls[2]["messages"][-1]["content"] == "本段报告"
    finally:
        await cast.close()


async def test_retell_cannot_remove_an_intervening_plan_from_the_shared_history(transport):
    cast, state = table()
    transport.answers.extend([plan(), "原来的叙述。", plan("周衡查看门边")])
    try:
        await cast.plan(state, "先整理工具。")
        await cast.keeper(state, "原来的完整报告", remember="原来的实际结果增量")
        await cast.plan(state, "然后看门边。")
        original = [dict(row) for row in state.transcripts["keeper"]]
        with pytest.raises(ValueError, match="最后一段不是这段叙述"):
            await cast.retell(state, "原来的完整报告", "原来的叙述。", remember="原来的实际结果增量")
        assert state.transcripts["keeper"] == original and len(transport.calls) == 3
    finally:
        await cast.close()
