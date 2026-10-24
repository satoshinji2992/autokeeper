"""Configured character budgets guard the final request, including repair and direct calls."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from autokeeper.config import Role
from autokeeper.harness import ContextBudgetExceeded, Session, Summarizer, trace
from autokeeper.harness import models as hm


@pytest.fixture
def probe(monkeypatch):
    calls, answers = [], []

    class Probe(hm.Model):
        mode = "json"

        async def _call(self, messages, *, schema, images, on_delta):
            calls.append((self.spec.model, [dict(message) for message in messages], schema))
            await asyncio.sleep(0)
            return answers.pop(0) if answers else hm.Reply(text="收到", usage={"input_tokens": 11, "output_tokens": 3})

    monkeypatch.setitem(hm.MODELS, "context_probe", Probe)
    monkeypatch.setattr(hm, "_down", {})
    return SimpleNamespace(model=Probe, calls=calls, answers=answers)


def _spec(limit=0):
    return Role("context_probe", "offline-context", None, max_input_chars=limit)


def test_context_parts_count_system_schema_extra_and_wrappers_once(probe):
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    transcript = [{"role": "user", "content": "旧提问"}, {"role": "assistant", "content": "旧回复"}]
    session = Session("npc", _spec(), "系统", schema=schema, memory=1000,
                      transcript=transcript, notes={"npc": "旧承诺"})
    session.pinned = "当前任务"
    parts = session.context_size("新问题", extra="仅限这次的资料")
    actual = "系统" + hm.json_instruction(schema) + "".join(m["content"] for m in session.window("新问题仅限这次的资料"))
    assert parts["total_chars"] == len(actual) and parts["utf8_bytes"] == len(actual.encode("utf-8"))
    assert parts["extra_chars"] == len("仅限这次的资料") and parts["schema_chars"] == len(hm.schema_text(schema))
    additive = ("system_chars", "history_chars", "summary_chars", "pinned_chars", "prompt_chars", "extra_chars",
                "wrapper_chars", "schema_chars", "format_chars")
    assert sum(parts[key] for key in additive) == parts["total_chars"]


@pytest.mark.parametrize("component", ["system", "schema", "extra"])
async def test_fixed_input_components_cannot_bypass_the_session_budget(probe, component):
    system = "大" * 200 if component == "system" else "系统"
    schema = {"type": "object", "description": "模式" * 200} if component == "schema" else None
    extra = "临时资料" * 100 if component == "extra" else ""
    session = Session("keeper", _spec(100), system, schema=schema)
    with pytest.raises(ContextBudgetExceeded) as error:
        await session.send("现在处理", extra=extra)
    assert error.value.parts["total_chars"] > 100 and not probe.calls
    assert not session.transcript


def _history():
    return [{"role": "user", "content": "旧请求" * 100}, {"role": "assistant", "content": "旧答复" * 100},
            {"role": "user", "content": "中间请求" * 100}, {"role": "assistant", "content": "中间答复" * 100},
            {"role": "user", "content": "最近的问题"}, {"role": "assistant", "content": "最近的回答"}]


async def test_one_successful_compaction_makes_the_actual_request_fit(probe):
    summaries = []

    async def summarize(text, who):
        summaries.append((text, who))
        return "原先的承诺尚未履行。"

    session = Session("npc", _spec(160), "角色", memory=10_000, transcript=_history(), summarizer=summarize)
    assert (await session.send("接着说。", extra="只谈眼前。")).text == "收到"
    assert len(summaries) == len(probe.calls) == 1
    sent = "".join(message["content"] for message in probe.calls[0][1])
    assert "原先的承诺尚未履行" in sent and "最近的问题" in sent and "旧请求旧请求" not in sent
    assert session.model.input_size(probe.calls[0][1])["total_chars"] <= 160
    await session.close()


@pytest.mark.parametrize("failure", ["raises", "empty", "too_long"])
async def test_a_failed_or_insufficient_summary_keeps_the_original_and_does_not_send(probe, failure):
    summaries = []

    async def summarize(text, who):
        summaries.append(text)
        if failure == "raises":
            raise RuntimeError("离线摘要失败")
        return "" if failure == "empty" else "过大的摘要" * 100

    original = _history()
    transcript = [dict(row) for row in original]
    notes = {"npc": "原摘要"}
    session = Session("npc", _spec(160), "角色", memory=10_000, transcript=transcript, notes=notes, summarizer=summarize)
    with pytest.raises(ContextBudgetExceeded):
        await session.send("当前问题。")
    assert len(summaries) == 1 and not probe.calls
    assert transcript == original and notes == {"npc": "原摘要"}
    await session.close()


async def test_a_stateless_preparation_request_does_not_attempt_to_compact(probe):
    async def summarize(*args):
        pytest.fail("无状态调用不能压缩其他角色的记忆")

    session = Session("director", _spec(100), "角色", memory=0, summarizer=summarize)
    with pytest.raises(ContextBudgetExceeded):
        await session.send("当前世界切片" * 100)
    assert not probe.calls
    await session.close()


@pytest.mark.parametrize("cooldown", [False, True])
async def test_a_direct_model_budget_refusal_does_not_fallback_or_start_a_cooldown(probe, cooldown):
    primary = probe.model(_spec(5), "系统")
    primary.fallback = probe.model(_spec(), "系统")
    key = (primary.spec.backend, primary.spec.model, primary.spec.base_url)
    if cooldown:
        hm._down[key] = time.monotonic() + 300
    before = dict(hm._down)
    with pytest.raises(ContextBudgetExceeded) as error:
        await primary.complete([{"role": "user", "content": "不可发送的长输入"}])
    assert not probe.calls and hm._down == before
    assert not hm.transient(error.value)


async def test_an_oversized_repair_is_refused_and_the_first_paid_usage_is_counted_once(probe, monkeypatch):
    schema = {"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}
    messages = [{"role": "user", "content": "按格式回答"}]
    measured = probe.model(_spec(), "系统").input_size(messages, schema)["total_chars"]
    model = probe.model(_spec(measured + 30), "系统")
    model.fallback = probe.model(_spec(), "备用系统")
    probe.answers.append(hm.Reply(text="无效的原输出" * 200, usage={"input_tokens": 11, "output_tokens": 3}))
    recorded = []
    monkeypatch.setattr(hm.spend, "record", lambda name, usage: recorded.append(dict(usage)) or 0.0)
    rows = []
    with trace.capture(rows.append), pytest.raises(ContextBudgetExceeded):
        await model.complete(messages, schema=schema)
    assert len(probe.calls) == 1 and not hm._down
    assert recorded == [{"input_tokens": 11, "output_tokens": 3}]
    blocked = next(row for row in rows if row["phase"] == "context_blocked")
    assert blocked["context"]["total_chars"] > measured + 30
    assert "ContextBudgetExceeded" in next(row for row in rows if row["phase"] == "complete")["error"]


async def test_a_summarizer_direct_call_has_its_own_hard_budget(probe):
    summarizer = Summarizer(_spec(5))
    with pytest.raises(ContextBudgetExceeded):
        await summarizer("要压缩的资料", "npc:warden")
    assert not probe.calls
    await summarizer.close()


@pytest.mark.parametrize("mode", ["native", "json"])
async def test_native_and_prompted_schema_modes_enforce_the_same_counting_contract(probe, mode):
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    messages = [{"role": "user", "content": "问题"}]
    model = probe.model(_spec(), "系统")
    model.mode = mode
    parts = model.input_size(messages, schema)
    contract = hm.json_instruction(schema) if mode == "json" else hm.schema_text(schema)
    assert parts["total_chars"] == len("系统问题" + contract)
    model.spec = _spec(parts["total_chars"] - 1)
    with pytest.raises(ContextBudgetExceeded):
        await model.complete(messages, schema=schema)
    assert not probe.calls


def test_a_configured_budget_is_retained_by_automatically_created_fallbacks(probe, monkeypatch):
    monkeypatch.setitem(hm.MODELS, "claude", probe.model)
    spec = Role("context_probe", "offline-primary", None, fallback="claude:offline-fallback", max_input_chars=250)
    model = hm.make_model(spec, "系统")
    assert model.fallback.spec.max_input_chars == 250


async def test_concurrent_sessions_keep_separate_context_breakdowns(probe):
    first = Session("first", _spec(), "甲系统", memory=1000, key="npc:a")
    second = Session("second", _spec(), "乙系统更长", memory=0, key="director:b")
    first.pinned = "甲的任务"
    expected = {"first": first.context_size("甲问题", extra="甲的临时资料"), "second": second.context_size("乙问题", extra="乙")}
    rows = []
    with trace.capture(rows.append):
        await asyncio.gather(first.send("甲问题", extra="甲的临时资料"), second.send("乙问题", extra="乙"))
    complete = {row["agent"]: row for row in rows if row["phase"] == "complete"}
    assert all(complete[key]["context"] == parts for key, parts in expected.items())
    assert all(complete[key]["chars_in"] == parts["total_chars"] for key, parts in expected.items())
    assert complete["first"]["call_id"] != complete["second"]["call_id"]
    await first.close()
    await second.close()


async def test_a_nested_direct_model_call_cannot_borrow_its_callers_session_context(probe):
    inner = probe.model(_spec(), "内部摘要系统", label="inner")

    class Outer(probe.model):
        async def _call(self, messages, *, schema, images, on_delta):
            await inner.complete([{"role": "user", "content": "内部资料"}])
            return hm.Reply(text="外部回复")

    session = Session("outer", _spec(), "外部系统", memory=1000)
    session.model = Outer(_spec(), "外部系统", label="outer")
    session.pinned = "只有外部会话的任务"
    rows = []
    with trace.capture(rows.append):
        await session.send("外部问题", extra="外部附言")
    complete = {row["agent"]: row for row in rows if row["phase"] == "complete"}
    assert "pinned_chars" not in complete["inner"]["context"]
    assert complete["inner"]["context"]["total_chars"] == len("内部摘要系统内部资料")
    assert complete["outer"]["context"]["pinned_chars"] == len(session.pinned)
    await session.close()
