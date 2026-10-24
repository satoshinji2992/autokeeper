"""One conversation may use separate plan/telling transports without separate memory owners."""

from __future__ import annotations

import asyncio
import copy
import json
from collections import defaultdict, deque
from types import SimpleNamespace

import pytest

from autokeeper.config import Role
from autokeeper.harness import ContextBudgetExceeded, Reply, Session, trace
from autokeeper.harness import models as hm

PLAN = {"title": "OfflinePlan", "type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}
TELLING = {"title": "OfflineTelling", "type": "object", "properties": {"text": {"type": "string"}},
           "required": ["text"]}


def structured(data: dict) -> Reply:
    return Reply(text=json.dumps(data, ensure_ascii=False), data=data)


def spec(name: str, *, limit: int = 0, window: int = 0, effort: str = "low", fallback: str = "") -> Role:
    return Role("shared_channel_probe", name, effort, window=window, max_input_chars=limit, fallback=fallback)


@pytest.fixture
async def channel(monkeypatch):
    calls, sessions, models = [], [], []
    answers = defaultdict(deque)

    class Probe(hm.Model):
        mode = "json"

        async def _call(self, messages, *, schema, images, on_delta):
            calls.append({"model": self.spec.model, "spec": self.spec, "label": self.label,
                          "messages": copy.deepcopy(messages), "schema": copy.deepcopy(schema)})
            answer = answers[self.spec.model].popleft()
            if isinstance(answer, Exception):
                raise answer
            return answer

    def session(**kwargs):
        result = Session("keeper", kwargs.pop("route", spec("keeper", effort="high")), "共同守秘人", **kwargs)
        sessions.append(result)
        return result

    def model(route, *, label="plan", system="共同守秘人"):
        result = hm.make_model(route, system, label=label)
        models.append(result)
        return result

    monkeypatch.setitem(hm.MODELS, "shared_channel_probe", Probe)
    monkeypatch.setitem(hm.MODELS, "claude", Probe)
    monkeypatch.setattr(hm, "_down", {})
    monkeypatch.setattr(hm, "listeners", [])
    monkeypatch.setenv("AUTOKEEPER_TRACE", "off")
    monkeypatch.delenv("AUTOKEEPER_BUDGET_USD", raising=False)
    yield SimpleNamespace(calls=calls, answers=answers, session=session, model=model)
    for owner in sessions:
        await owner.close()
    for transport in models:
        await transport.close()


async def test_model_override_keeps_one_transcript_and_does_not_leave_a_default_schema(channel):
    transcript, notes = [], {}
    session = channel.session(memory=10_000, transcript=transcript, notes=notes, key="keeper")
    keeper_model, keeper_spec = session.model, session.spec
    plan = channel.model(spec("plan", effort="low", window=12))
    channel.answers["plan"].append(structured({"kind": "investigate"}))
    channel.answers["keeper"].extend([structured({"text": "骰后发现锁已打开。"}), Reply(text="补述实际现场。"),
                                      Reply(text="本局结束。")])

    await session.send("整份规划现场", remember="玩家要开锁", extra="仅此轮格式提醒", schema=PLAN, model=plan)
    await session.send("实际骰与工具回执", remember="开锁成功；NPC已交钥匙", schema=TELLING)
    await session.send("重新叙述")
    await session.send("尾声")

    assert [call["model"] for call in channel.calls] == ["plan", "keeper", "keeper", "keeper"]
    assert [call["schema"] for call in channel.calls] == [PLAN, TELLING, None, None]
    assert channel.calls[0]["spec"].effort == "low" and channel.calls[1]["spec"].effort == "high"
    assert channel.calls[1]["messages"][:-1] == transcript[:2]
    assert channel.calls[2]["messages"][:-1] == transcript[:4]
    assert channel.calls[0]["messages"][-1]["content"] == "整份规划现场仅此轮格式提醒"
    kept = "\n".join(row["content"] for row in transcript)
    assert "玩家要开锁" in kept and "开锁成功；NPC已交钥匙" in kept
    assert "整份规划现场" not in kept and "仅此轮格式提醒" not in kept
    assert session.model is keeper_model and session.spec is keeper_spec and session.schema is None
    assert session.memory == 10_000 and session.key == "keeper" and session.transcript is transcript
    assert not notes and session._folding is None


@pytest.mark.parametrize("owner_limit,plan_limit,allowed", [(1, 10_000, True), (10_000, 1, False)])
async def test_override_uses_its_own_hard_budget_in_both_directions(channel, owner_limit, plan_limit, allowed):
    transcript = [{"role": "user", "content": "已完成的动作"}, {"role": "assistant", "content": "实际结果"}]
    original = copy.deepcopy(transcript)
    session = channel.session(route=spec("keeper", limit=owner_limit), memory=10_000, transcript=transcript)
    plan = channel.model(spec("plan", limit=plan_limit), system="不同长度的实际规划系统")
    expected = plan.input_size(session.window("当前请求及临时资料"), PLAN)
    parts = session.context_size("当前请求", PLAN, extra="及临时资料", model=plan)
    assert all(parts[key] == value for key, value in expected.items())
    assert parts["history_chars"] == sum(len(row["content"]) for row in original)
    channel.answers["plan"].append(structured({"kind": "wait"}))

    if allowed:
        await session.send("当前请求", extra="及临时资料", schema=PLAN, model=plan)
        assert len(channel.calls) == 1 and channel.calls[0]["model"] == "plan"
    else:
        with pytest.raises(ContextBudgetExceeded) as error:
            await session.send("当前请求", extra="及临时资料", schema=PLAN, model=plan)
        assert error.value.parts == parts and not channel.calls and transcript == original
    assert session.spec.max_input_chars == owner_limit and session.model.spec.max_input_chars == owner_limit


async def test_override_fallback_and_trace_keep_the_phase_route_without_switching_the_owner(channel):
    class Rejected(Exception):
        status_code = 400

    session = channel.session(memory=10_000, key="keeper")
    session.pinned = "共享的尚未完成事项"
    plan = channel.model(spec("plan", limit=10_000, effort="low", fallback="claude:plan-standby"))
    assert plan.fallback.spec.effort == "low" and plan.fallback.spec.max_input_chars == 10_000
    channel.answers["plan"].append(Rejected("离线模拟该传输拒绝本次请求"))
    channel.answers["plan-standby"].append(structured({"kind": "investigate"}))
    channel.answers["keeper"].append(Reply(text="按实际检定继续。"))
    expected = session.context_size("规划动作", PLAN, extra="仅规划参考", model=plan)
    rows = []
    with trace.capture(rows.append):
        await session.send("规划动作", schema=PLAN, extra="仅规划参考", model=plan)
        await session.send("检定已经完成")

    assert [call["model"] for call in channel.calls] == ["plan", "plan-standby", "keeper"]
    assert channel.calls[0]["messages"] == channel.calls[1]["messages"]
    complete = [row for row in rows if row["phase"] == "complete"]
    assert [(row["agent"], row["model"], row["effort"]) for row in complete] == [
        ("plan", "plan", "low"), ("plan", "plan-standby", "low"), ("keeper", "keeper", "high")]
    assert complete[0]["context"] == expected and complete[0]["chars_in"] == expected["total_chars"]
    assert complete[1]["fallback_for"] == "plan" and "error" not in complete[1]
    assert complete[1]["chars_in"] == expected["total_chars"]
    assert "kind" in channel.calls[2]["messages"][-2]["content"]
    assert not hm._down and session.model.spec.model == "keeper" and session.key == "keeper"


async def test_an_override_budget_refusal_cannot_use_its_uncapped_fallback(channel):
    session = channel.session(memory=1000)
    plan = channel.model(spec("plan", limit=1))
    plan.fallback = channel.model(spec("uncapped-standby"))
    with pytest.raises(ContextBudgetExceeded):
        await session.send("这个规划明显超过一个字符", model=plan)
    assert not channel.calls and not session.transcript and not hm._down


async def test_override_compaction_uses_the_active_limit_and_only_the_canonical_summary_key(channel):
    summarized = []

    async def summarize(text, who):
        summarized.append((text, who))
        return "先前承诺尚未兑现。"

    transcript = [{"role": "user", "content": "早前调查" * 100}, {"role": "assistant", "content": "早前结果" * 100},
                  {"role": "user", "content": "最近请求"}, {"role": "assistant", "content": "最近回复"}]
    notes = {"keeper": "旧摘要", "npc:warden": "他自己的记忆"}
    session = channel.session(memory=10_000, transcript=transcript, notes=notes, key="keeper", summarizer=summarize)
    plan = channel.model(spec("plan", limit=250))
    channel.answers["plan"].append(Reply(text="动作已规划。"))
    await session.send("继续当前调查", model=plan)

    assert len(summarized) == 1 and summarized[0][1] == "keeper"
    assert "旧摘要" in summarized[0][0] and "早前调查" in summarized[0][0]
    assert notes == {"keeper": "先前承诺尚未兑现。", "npc:warden": "他自己的记忆"}
    assert "早前调查" not in "".join(row["content"] for row in transcript)
    sent = "".join(row["content"] for row in channel.calls[0]["messages"])
    assert "先前承诺尚未兑现。" in sent and "最近请求" in sent
    assert plan.input_size(channel.calls[0]["messages"])["total_chars"] <= 250
    assert session._folding is None and session.memory == 10_000


@pytest.mark.parametrize("failure", ["raises", "empty", "too_long"])
async def test_override_summary_failure_keeps_the_raw_history_and_existing_notes(channel, failure):
    summarized = []

    async def summarize(text, who):
        summarized.append(who)
        if failure == "raises":
            raise RuntimeError("离线摘要失败")
        return "" if failure == "empty" else "放不进预算的摘要" * 100

    transcript = [{"role": "user", "content": "旧请求" * 100}, {"role": "assistant", "content": "旧回复" * 100},
                  {"role": "user", "content": "最近请求"}, {"role": "assistant", "content": "最近回复"}]
    original = copy.deepcopy(transcript)
    notes = {"keeper": "保留原摘要", "plan": "其他旧笔记"}
    session = channel.session(memory=10_000, transcript=transcript, notes=notes, key="keeper", summarizer=summarize)
    plan = channel.model(spec("plan", limit=250))
    with pytest.raises(ContextBudgetExceeded):
        await session.send("新请求", model=plan)
    assert summarized == ["keeper"] and not channel.calls
    assert transcript == original and notes == {"keeper": "保留原摘要", "plan": "其他旧笔记"}
    assert session._folding is None


async def test_pending_fold_finishes_once_before_a_rebound_session_sends_with_another_model(channel):
    started, release = asyncio.Event(), asyncio.Event()
    summaries = []

    async def summarize(text, who):
        summaries.append((text, who))
        started.set()
        await release.wait()
        return "旧对象的承诺。"

    old = [{"role": "user", "content": "旧动作" * 10}, {"role": "assistant", "content": "旧结果" * 10}]
    old_notes = {"keeper": "原摘要"}
    session = channel.session(memory=40, transcript=old, notes=old_notes, key="keeper", summarizer=summarize)
    channel.answers["keeper"].append(Reply(text="最后已完成的结果"))
    await session.send("最后一个旧请求")
    await started.wait()
    folding = session._folding
    rebound = [{"role": "user", "content": "载入动作"}, {"role": "assistant", "content": "载入结果"}]
    rebound_notes = {"keeper": "读档摘要", "npc:warden": "人物自己的记忆"}
    session.bind(rebound, rebound_notes)
    plan = channel.model(spec("plan"))
    channel.answers["plan"].append(Reply(text="好"))
    sending = asyncio.create_task(session.send("继续", model=plan))
    await asyncio.sleep(0)
    assert len(channel.calls) == 1 and not sending.done()
    release.set()
    await sending

    assert folding.done() and len(summaries) == 1 and summaries[0][1] == "keeper"
    assert old_notes == {"keeper": "旧对象的承诺。"}
    assert old == [{"role": "user", "content": "最后一个旧请求"}, {"role": "assistant", "content": "最后已完成的结果"}]
    assert rebound_notes == {"keeper": "读档摘要", "npc:warden": "人物自己的记忆"}
    assert rebound[:2] == [{"role": "user", "content": "载入动作"}, {"role": "assistant", "content": "载入结果"}]
    sent = "".join(row["content"] for row in channel.calls[-1]["messages"])
    assert "读档摘要" in sent and "载入结果" in sent and "旧对象的承诺" not in sent
    assert session._folding is None


async def test_recovery_of_a_cancelled_fold_preserves_and_compacts_the_saved_history_once(channel):
    started, release = asyncio.Event(), asyncio.Event()
    summaries = []

    async def blocked_summary(text, who):
        started.set()
        await release.wait()
        return "不应完成的旧进程摘要"

    transcript = [{"role": "user", "content": "原动作" * 10}, {"role": "assistant", "content": "原结果" * 10}]
    notes = {"keeper": "先前摘要"}
    session = channel.session(memory=40, transcript=transcript, notes=notes, key="keeper", summarizer=blocked_summary)
    channel.answers["keeper"].append(Reply(text="实际回复"))
    await session.send("尚未压缩的新动作")
    await started.wait()
    saved_transcript, saved_notes = copy.deepcopy(transcript), dict(notes)
    await session.close()
    assert transcript == saved_transcript and notes == saved_notes and session._folding is None

    async def restored_summary(text, who):
        summaries.append((text, who))
        return "原动作已有结果，之后的新动作也已回复。"

    restored = channel.session(memory=40, transcript=saved_transcript, notes=saved_notes,
                               key="keeper", summarizer=restored_summary)
    plan = channel.model(spec("plan"))
    channel.answers["plan"].append(Reply(text="继续规划"))
    await restored.send("读档后继续", model=plan)
    await restored.settle()
    assert len(summaries) == 1 and summaries[0][1] == "keeper"
    assert summaries[0][0].count("尚未压缩的新动作") == 1 and summaries[0][0].count("实际回复") == 1
    assert saved_notes == {"keeper": "原动作已有结果，之后的新动作也已回复。"}
    assert saved_transcript == [{"role": "user", "content": "读档后继续"},
                                {"role": "assistant", "content": "继续规划"}]
    assert len(channel.calls) == 2 and [call["model"] for call in channel.calls] == ["keeper", "plan"]
