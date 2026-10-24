"""The harness without any network: scripted models stand in for every provider."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import deque

import pytest

from autokeeper import config, paths
from autokeeper.agents.cast import Action, Cast
from autokeeper.config import Role
from autokeeper.engine.game import Game
from autokeeper.engine.state import CheckRecord
from autokeeper.harness import Agent, BudgetExceeded, Reply, Session, spend
from autokeeper.harness import models as hm
from autokeeper.harness.tools import Tool, ToolError, Workspace, check_tool, file_tools, web_tools
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.rules.commands import Asked

SCRIPT: deque = deque()  # what the scripted backend answers next: str (text) or dict (JSON)
SEEN: list[dict] = []  # every call: system, messages, schema


class Scripted(hm.Model):
    mode = "json"

    async def _call(self, messages, *, schema, images, on_delta):
        SEEN.append({"system": self.system, "messages": [dict(m) for m in messages], "schema": schema})
        out = SCRIPT.popleft()
        if isinstance(out, dict):
            return Reply(text=json.dumps(out, ensure_ascii=False), data=out, usage={"input_tokens": 100, "output_tokens": 10})
        if on_delta:
            await on_delta(out)
        return Reply(text=out, data=hm.parse_json(out) if schema else None, usage={"input_tokens": 100, "output_tokens": 10})


SCRIPTED = Role("scripted", "scripted-model", None)


@pytest.fixture(autouse=True)
def harness_sandbox(monkeypatch, tmp_path):
    monkeypatch.setitem(hm.MODELS, "scripted", Scripted)
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setattr(paths, "USER_DIR", tmp_path / "user")
    monkeypatch.setenv("AUTOKEEPER_TRACE", "meta")
    for var in ("AUTOKEEPER_PRESET", "AUTOKEEPER_MODEL", "AUTOKEEPER_SEARCH", "AUTOKEEPER_BUDGET_USD"):
        monkeypatch.delenv(var, raising=False)
    spend.budget.cache_clear()
    SCRIPT.clear()
    SEEN.clear()
    yield
    spend.budget.cache_clear()


async def test_json_is_validated_and_repaired_once():
    schema = Action.model_json_schema()
    SCRIPT.extend(["好的，我来看看：{\"summary\": \"翻看书台\"}", {"kind": "investigate", "summary": "翻看书台上的折扇"}])
    reply = await hm.make_model(SCRIPTED, "解析器").complete([{"role": "user", "content": "我翻看书台"}], schema=schema)
    assert reply.data == {"kind": "investigate", "summary": "翻看书台上的折扇"} and reply.repairs == 1
    repair = SEEN[1]["messages"]
    assert repair[-2]["role"] == "assistant" and "kind" in repair[-1]["content"]  # the validator's complaint goes back
    assert reply.usage == {"input_tokens": 200, "output_tokens": 20}


async def test_a_line_said_in_plain_words_needs_no_repair():
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    SCRIPT.append("（搓着手）钥匙就挂在门边，您拿去吧。")
    reply = await hm.make_model(SCRIPTED, "NPC").complete([{"role": "user", "content": "能借我钥匙吗？"}], schema=schema)
    assert reply.data == {"text": "（搓着手）钥匙就挂在门边，您拿去吧。"} and reply.repairs == 0 and len(SEEN) == 1
    SCRIPT.extend(["   ", {"text": "好。"}])  # nothing said at all is still repaired
    reply = await hm.make_model(SCRIPTED, "NPC").complete([{"role": "user", "content": "嗯？"}], schema=schema)
    assert reply.data == {"text": "好。"} and reply.repairs == 1


async def test_stateful_dialogue_is_not_downgraded_to_plain_text_when_its_side_effects_are_optional():
    from autokeeper.agents.cast import Dialogue

    schema = Dialogue.model_json_schema()
    assert not hm.spoken(schema)
    SCRIPT.extend(["您拿着这盏灯。我亲眼见过那辆车。",
                   {"text": "您拿着这盏灯。我亲眼见过那辆车。", "items": [{"name": "煤油灯", "to": "zhouheng"}],
                    "told": ["c_car"]}])
    reply = await hm.make_model(SCRIPTED, "NPC").complete([{"role": "user", "content": "能借灯并说说那辆车吗？"}], schema=schema)
    assert reply.repairs == 1 and reply.data["told"] == ["c_car"]
    assert reply.data["items"] == [{"name": "煤油灯", "to": "zhouheng"}]


async def test_a_whitespace_json_transport_repairs_once_in_prompt_mode_without_losing_the_contract():
    from types import SimpleNamespace

    from autokeeper.agents.cast import Dialogue

    calls = []

    class Chat:
        def __init__(self, json_mode=False):
            self.json_mode = json_mode

        def bind(self, **kwargs):
            return Chat(json_mode=bool(kwargs.get("response_format")))

        async def ainvoke(self, history):
            calls.append((self.json_mode, history))
            text = "   " if self.json_mode else json.dumps({"text": "我见过那辆车。", "told": ["c_car"]}, ensure_ascii=False)
            return SimpleNamespace(text=text, additional_kwargs={}, content=text,
                                   usage_metadata={"input_tokens": 100, "output_tokens": 10})

    model = object.__new__(hm.LangChainModel)
    hm.Model.__init__(model, Role("langchain", "mock:model", None), "你扮演NPC", label="npc")
    model.provider, model.mode, model.chat = "deepseek", "json", Chat()
    reply = await model.complete([{"role": "user", "content": "您见过车吗？"}], schema=Dialogue.model_json_schema())
    assert [mode for mode, _ in calls] == [True, False]
    assert reply.repairs == 1 and reply.data["told"] == ["c_car"]
    assert all("JSON Schema" in history[0].content for _, history in calls)
    assert reply.usage["input_tokens"] == 200 and not hm._format_retry.get()


async def test_deepseek_format_repair_requests_final_content_without_exposing_thinking():
    from types import SimpleNamespace

    from autokeeper.agents.cast import Dialogue

    calls = []

    class Chat:
        def __init__(self, thinking=True, json_mode=False):
            self.thinking, self.json_mode = thinking, json_mode

        def bind(self, **kwargs):
            thinking = kwargs.get("extra_body", {}).get("thinking", {}).get("type", "enabled" if self.thinking else "disabled")
            return Chat(thinking=thinking == "enabled", json_mode=bool(kwargs.get("response_format", self.json_mode)))

        async def ainvoke(self, history):
            calls.append((self.thinking, self.json_mode))
            text = "   " if self.thinking else '{"text":"请进。","told":[]}'
            return SimpleNamespace(text=text, content=text, usage_metadata={},
                                   additional_kwargs={"reasoning_content": '幕后秘密。{"text":"不能当成台词"}' if self.thinking else ""})

    model = object.__new__(hm.LangChainModel)
    hm.Model.__init__(model, Role("langchain", "mock:model", "low"), "NPC", label="npc")
    model.provider, model.mode, model.chat = "deepseek", "json", Chat()
    reply = await model.complete([{"role": "user", "content": "敲门"}], schema=Dialogue.model_json_schema())
    assert calls == [(True, True), (False, False)]
    assert reply.repairs == 1 and reply.data["text"] == "请进。"
    assert "幕后秘密" not in reply.text and "不能当成台词" not in reply.text


async def test_literal_newline_in_npc_json_preserves_dialogue_and_actions():
    from autokeeper.agents.cast import Dialogue

    SCRIPT.append('{"text":"别担心。\n我陪您去。","accompany":"graveyard"}')
    reply = await hm.make_model(SCRIPTED, "NPC").complete(
        [{"role": "user", "content": "能陪我去吗？"}], schema=Dialogue.model_json_schema()
    )
    assert reply.data == {"text": "别担心。\n我陪您去。", "accompany": "graveyard"}
    assert reply.repairs == 0 and len(SEEN) == 1


async def test_broken_structured_npc_reply_is_repaired_instead_of_displayed_as_dialogue():
    from autokeeper.agents.cast import Dialogue

    SCRIPT.extend(['{"text":"我陪您去。", "accompany":', {"text": "我陪您去。", "accompany": "graveyard"}])
    reply = await hm.make_model(SCRIPTED, "NPC").complete(
        [{"role": "user", "content": "能陪我去吗？"}], schema=Dialogue.model_json_schema()
    )
    assert reply.data == {"text": "我陪您去。", "accompany": "graveyard"}
    assert reply.repairs == 1 and len(SEEN) == 2


async def test_a_dialogue_prefix_cannot_hide_operational_fields_after_the_first_json_object():
    from autokeeper.agents.cast import Dialogue

    broken = '{"text":"我见过那辆车。"}\n（补充）\n"told":["c_car"]}'
    assert hm.parse_json(broken) is None
    SCRIPT.extend([broken, {"text": "我见过那辆车。", "told": ["c_car"]}])
    reply = await hm.make_model(SCRIPTED, "NPC").complete(
        [{"role": "user", "content": "您知道那辆车吗？"}], schema=Dialogue.model_json_schema())
    assert reply.repairs == 1 and reply.data == {"text": "我见过那辆车。", "told": ["c_car"]}


async def test_unrepairable_npc_json_never_returns_private_fields_as_spoken_text():
    from autokeeper.agents.cast import Dialogue

    broken = '{"text":"请留下守夜。" ","deceptions":[{"intent":"秘密安排"}]}'
    SCRIPT.extend([broken, broken])
    with pytest.raises(ValueError, match="回复格式未通过校验"):
        await hm.make_model(SCRIPTED, "NPC").complete(
            [{"role": "user", "content": "今晚有什么事？"}], schema=Dialogue.model_json_schema()
        )
    assert len(SEEN) == 2


class Flaky(Scripted):
    """Fails the first calls with a given error, then answers from SCRIPT."""

    failures: deque = deque()

    async def _call(self, messages, *, schema, images, on_delta):
        if self.failures:
            exc = self.failures.popleft()
            if on_delta and getattr(exc, "after_text", False):
                await on_delta("半句叙事")
            raise exc
        return await super()._call(messages, schema=schema, images=images, on_delta=on_delta)


class RateLimitError(Exception):
    status_code = 429


class BadRequestError(Exception):
    status_code = 400


@pytest.fixture
def flaky(monkeypatch):
    monkeypatch.setitem(hm.MODELS, "flaky", Flaky)
    monkeypatch.setattr(hm, "RETRY_WAIT", (0.0, 0.0))
    Flaky.failures.clear()
    return Role("flaky", "flaky-model", None)


async def test_transient_errors_are_retried(flaky):
    Flaky.failures.extend([RateLimitError("slow down"), ConnectionError("reset")])
    SCRIPT.append("好了")
    reply = await hm.make_model(flaky, "守秘人").complete([{"role": "user", "content": "继续"}])
    assert reply.text == "好了" and reply.retries == 2


async def test_bad_requests_and_streamed_text_are_not_retried(flaky):
    Flaky.failures.append(BadRequestError("schema invalid"))
    with pytest.raises(BadRequestError):
        await hm.make_model(flaky, "守秘人").complete([{"role": "user", "content": "继续"}])
    broken = TimeoutError("stalled mid-stream")
    broken.after_text = True  # type: ignore[attr-defined]
    Flaky.failures.append(broken)
    seen: list[str] = []

    async def show(piece: str) -> None:
        seen.append(piece)

    with pytest.raises(TimeoutError):
        await hm.make_model(flaky, "守秘人").complete([{"role": "user", "content": "继续"}], on_delta=show)
    assert seen == ["半句叙事"]  # the player saw half a sentence once, not twice


@pytest.fixture
def standby(flaky, monkeypatch):
    """A flaky primary; fallbacks named claude:* land on the scripted backend."""
    monkeypatch.setattr(hm, "_down", {})
    monkeypatch.setitem(hm.MODELS, "claude", Scripted)
    return flaky


async def test_a_failing_model_hands_over_to_its_fallback(standby, monkeypatch):
    notes: list[str] = []
    monkeypatch.setattr(hm, "listeners", [notes.append])
    Flaky.failures.extend([ConnectionError("down")] * hm.ATTEMPTS)
    SCRIPT.extend(["备用模型接着讲", "还是备用模型"])
    spec = Role("flaky", "flaky-model", "high", fallback="claude:scripted-model")
    keeper = hm.make_model(spec, "守秘人", label="keeper")
    assert keeper.fallback.spec == Role("claude", "scripted-model", "high")
    reply = await keeper.complete([{"role": "user", "content": "继续"}])
    assert reply.text == "备用模型接着讲" and len(notes) == 1 and "flaky-model" in notes[0]
    Flaky.failures.append(AssertionError("the tripped model must not be called again"))
    other = hm.make_model(spec, "NPC", label="npc")  # another agent on the same model skips it too
    assert (await other.complete([{"role": "user", "content": "你好"}])).text == "还是备用模型"
    rows = [json.loads(line) for line in paths.traces().glob("*.jsonl").__next__().read_text(encoding="utf-8").splitlines()]
    assert [r.get("fallback_for", "") for r in rows] == ["", "flaky-model", "flaky-model"]
    assert "ConnectionError" in rows[0]["error"]


async def test_a_request_the_model_turns_down_goes_to_the_fallback_but_puts_no_one_on_hold(standby, monkeypatch):
    """One schema it would not take (a 400) sent every role on the model to the fallback for five
    minutes (2026-10-21): now only that call goes there; the model stays in use."""
    notes: list[str] = []
    monkeypatch.setattr(hm, "listeners", [notes.append])
    Flaky.failures.append(BadRequestError("schema invalid"))
    SCRIPT.extend(["备用模型接着讲"])
    spec = Role("flaky", "flaky-model", "high", fallback="claude:scripted-model")
    reply = await hm.make_model(spec, "守秘人", label="keeper").complete([{"role": "user", "content": "继续"}])
    assert reply.text == "备用模型接着讲" and not hm._down and not notes


def test_what_the_player_waits_on_gives_up_sooner(monkeypatch):
    monkeypatch.delenv("AUTOKEEPER_TIMEOUT", raising=False)
    assert hm.call_timeout("plan") == 120 and hm.call_timeout("npc:warden") == 120
    assert hm.call_timeout("director") == hm.CALL_TIMEOUT  # long jobs keep the long one
    monkeypatch.setenv("AUTOKEEPER_TIMEOUT", "30")
    assert hm.call_timeout("plan") == hm.CALL_TIMEOUT  # set by hand: for every role


async def test_no_fallback_once_the_player_has_read_part_of_a_reply(standby):
    broken = TimeoutError("stalled mid-stream")
    broken.after_text = True  # type: ignore[attr-defined]
    Flaky.failures.append(broken)
    seen: list[str] = []

    async def show(piece: str) -> None:
        seen.append(piece)

    keeper = hm.make_model(Role("flaky", "flaky-model", None, fallback="claude:scripted-model"), "守秘人")
    with pytest.raises(TimeoutError):
        await keeper.complete([{"role": "user", "content": "继续"}], on_delta=show)
    assert seen == ["半句叙事"]


async def test_spent_budget_falls_back_to_a_free_model(standby, monkeypatch):
    monkeypatch.setenv("AUTOKEEPER_BUDGET_USD", "0")
    spend.budget.cache_clear()
    SCRIPT.append("本地模型接手")
    spec = Role("scripted", "deepseek:deepseek-flash", "low", fallback="claude:local-model")  # priced, so capped
    reply = await hm.make_model(spec, "NPC").complete([{"role": "user", "content": "你好"}])
    assert reply.text == "本地模型接手"


def test_model_strings_route_to_channels():
    assert config.route("claude-haiku-5-5") == ("claude", "claude-haiku-5-5")
    assert config.route("claude:claude-opus-5-5") == ("claude", "claude-opus-5-5")
    assert config.route("anthropic:claude-haiku-5-5") == ("langchain", "anthropic:claude-haiku-5-5")
    assert config.route("ollama:qwen3:14b") == ("langchain", "ollama:qwen3:14b")


def test_trace_summary_per_agent_and_run(monkeypatch):
    from autokeeper.harness import trace

    rows = [
        {"at": "2026-10-18T05:00:00", "run": "a", "agent": "keeper", "input_tokens": 1000, "cache_read": 800, "output_tokens": 50, "seconds": 2.0, "cost_usd": 0.001},
        {"at": "2026-10-18T05:00:03", "run": "a", "agent": "keeper", "input_tokens": 1200, "cache_read": 1000, "output_tokens": 60, "seconds": 4.0, "cost_usd": 0.001, "repairs": 1},
        {"at": "2026-10-18T05:00:05", "run": "a", "agent": "npc", "input_tokens": 300, "seconds": 1.0, "error": "TimeoutError: x"},
        {"at": "2026-10-18T06:00:00", "run": "b", "agent": "npc", "input_tokens": 300, "seconds": 1.0, "fallback_for": "deepseek:deepseek-flash"},
    ]
    keeper, npc = trace.summarize(rows)
    assert (keeper["key"], keeper["calls"], keeper["cache_rate"], keeper["repairs"]) == ("keeper", 2, 1800 / 2200, 1)
    assert keeper["fresh"] == 400 and npc["fresh"] == 600
    assert (keeper["p50"], keeper["p95"]) == (2.0, 4.0) or (keeper["p50"], keeper["p95"]) == (4.0, 4.0)
    assert (npc["errors"], npc["fallbacks"], npc["cache_rate"]) == (1, 1, 0.0)
    assert [(r["run"], r["calls"], r["end"]) for r in trace.runs(rows)] == [("a", 3, "2026-10-18T05:00:05"), ("b", 1, "2026-10-18T06:00:00")]
    assert trace.summarize(rows, None)[0]["calls"] == 4


def test_strict_schema_for_codex_closes_every_object():
    strict = hm.strict_schema(Action.model_json_schema())
    assert strict["additionalProperties"] is False and set(strict["required"]) == set(strict["properties"])
    assert "default" not in json.dumps(strict)
    full = {k: "" for k in strict["properties"]} | {
        "kind": "investigate", "summary": "翻抽屉", "travel": "direct", "casual": False, "communication": "local",
        "companions": [], "alone": False, "before": [], "precautions": [], "arrival_after_success": False,
        "wait_limit": None,
    }
    assert not hm.problems(full, Action.model_json_schema())  # what strict mode yields still passes our check


def test_prompted_json_carries_schema_and_example():
    text = hm.json_instruction(Action.model_json_schema())
    assert "json" in text and '"kind":"investigate"' in text  # DeepSeek JSON mode wants the word and an example


def test_claude_prompt_marks_the_end_of_history_for_the_prompt_cache():
    """Claude Code marks only the end of a request, and the new prompt carries one-off extras
    the transcript never keeps, so that mark never matched the next call. Ours sits after the
    last block of history, which the next call repeats word for word."""
    turns = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}句"} for i in range(5)]
    shorter, longer = hm._blocks(turns[:3], None), hm._blocks(turns, None)
    assert [b["text"] for b in longer[: len(shorter)]] == [b["text"] for b in shorter]  # append-only text
    marked = [i for i, b in enumerate(longer) if "cache_control" in b]
    assert marked == [len(longer) - 2] and longer[-2]["cache_control"] == {"type": "ephemeral", "ttl": hm.CACHE_TTL[0]}
    alone = hm._blocks(turns[:1], None)  # no history: a fixed block before the prompt is marked, for
    assert [b["text"] for b in alone] == [hm.ALONE, "第0句"]  # the system prompt and schema in front of it
    assert "cache_control" in alone[0] and "cache_control" not in alone[1]


def test_claude_api_errors_are_errors_and_a_refused_cache_mark_steps_down(monkeypatch):
    overloaded, bad = hm.ClaudeError("API Error: 529 Overloaded"), hm.ClaudeError("API Error: 400 messages.0: bad")
    assert hm.transient(overloaded) and not hm.transient(bad)  # retried, or on to the fallback; never narrated
    assert hm.transient(hm.ClaudeError("Claude Code reported an error"))  # failed without saying why: try again
    monkeypatch.setattr(hm, "CACHE_TTL", ["1h"])
    ttl = hm.ClaudeError("API Error: 400 messages.1.content.0.cache_control.ttl: a ttl='1h' block must not come after a ttl='5m' block")
    assert hm._cache_refused(ttl) and hm.CACHE_TTL == ["5m"]
    assert hm._cache_refused(ttl) and hm.CACHE_TTL == [None]
    assert not hm._cache_refused(ttl) and not hm._cache_refused(bad)  # nothing left to drop


async def test_claude_call_retries_once_without_the_refused_cache_mark(monkeypatch):
    monkeypatch.setattr(hm, "CACHE_TTL", ["1h"])
    model = hm.ClaudeCodeModel(Role("claude", "claude-haiku-5-5", "low"), "系统")
    seen = []

    async def once(messages, *, schema, images, on_delta):
        seen.append(hm.CACHE_TTL[0])
        if hm.CACHE_TTL[0] == "1h":
            raise hm.ClaudeError("API Error: 400 messages.1.content.0.cache_control.ttl: a ttl='1h' cache_control block must not come after a ttl='5m'")
        return Reply(text="好")

    monkeypatch.setattr(model, "_once", once)
    reply = await model._call([{"role": "user", "content": "你好"}], schema=None, images=None, on_delta=None)
    assert reply.text == "好" and seen == ["1h", "5m"]


async def test_session_keeps_a_window_and_folds_old_turns():
    notes: dict[str, str] = {}
    transcript: list[dict] = []

    async def summarize(text: str, who: str) -> str:
        return f"摘要（{who}）：" + text[-20:]

    s = Session("npc", SCRIPTED, "你是 NPC", memory=60, transcript=transcript, notes=notes, key="npc:a", summarizer=summarize)  # type: ignore[arg-type]
    for i in range(4):
        SCRIPT.append(f"回答{i}" * 3)
        await s.send(f"问题{i}" * 3)
    await s.settle()  # folding runs in the background
    assert notes["npc:a"].startswith("摘要（npc:a）")
    assert sum(len(m["content"]) for m in transcript) <= 60 and transcript[-1]["content"] == "回答3" * 3
    SCRIPT.append("好")
    await s.send("最后一问")
    window = SEEN[-1]["messages"]
    assert window[0]["content"].startswith("【更早的经过") and window[-1]["content"] == "最后一问"


async def test_a_failed_summarizer_does_not_cost_memory():
    async def broken(text: str, who: str) -> str:
        raise RuntimeError("summarizer is down")

    s = Session("npc", SCRIPTED, "你是 NPC", memory=60, summarizer=broken)  # type: ignore[arg-type]
    for i in range(4):
        SCRIPT.append(f"回答{i}" * 3)
        await s.send(f"问题{i}" * 3)
    await s.settle()
    assert not s.summary and len(s.transcript) == 8
    assert s.transcript[0]["content"] == "问题0" * 3 and s.transcript[-1]["content"] == "回答3" * 3


async def test_cancelling_a_cli_request_reaps_the_process():
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)",
                                                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, start_new_session=os.name == "posix")
    task = asyncio.create_task(hm._communicate(proc, b"prompt"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.returncode is not None


async def test_pinned_task_survives_folding():
    async def summarize(text: str, who: str) -> str:
        return "进度摘要"

    s = Session("importer", SCRIPTED, "你是整理员", memory=40, summarizer=summarize)  # type: ignore[arg-type]
    s.pinned = "把原文整理成 module.yaml，id 用 changpian"
    for i in range(4):
        SCRIPT.append(f"第{i}步做完了" * 2)
        await s.send(f"观察{i}" * 4)
    await s.settle()
    window = s.window("下一步")
    assert window[0]["content"] == "【任务】\n把原文整理成 module.yaml，id 用 changpian"
    assert window[2]["content"].endswith("进度摘要") and s.summary == "进度摘要"


async def test_extra_is_sent_but_not_remembered_and_forget_drops_a_turn():
    s = Session("npc", SCRIPTED, "你是 NPC", memory=10_000)
    SCRIPT.extend(["泄露了秘密", "我不知道"])
    await s.send("你知道什么？")
    s.forget()
    await s.send("你知道什么？", extra="（系统提醒：别说秘密）")
    assert SEEN[-1]["messages"][-1]["content"].endswith("别说秘密）")
    assert [m["content"] for m in s.transcript] == ["你知道什么？", "我不知道"]


async def test_remember_keeps_a_short_note_of_a_long_prompt():
    s = Session("arbiter", SCRIPTED, "你是裁定员", memory=10_000)
    SCRIPT.extend(["裁定一", "裁定二"])
    await s.send("【行动】翻书桌\n【场景】……很长的场景、线索表和人物卡……", remember="【行动】翻书桌")
    await s.send("【行动】看窗台\n【场景】……", remember="【行动】看窗台")
    sent = [m["content"] for m in SEEN[-1]["messages"]]
    assert sent == ["【行动】翻书桌", "裁定一", "【行动】看窗台\n【场景】……"]  # history short, this turn whole


async def test_an_npc_knows_only_its_own_part_and_its_words_are_not_rewritten():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npc("warden")
    SCRIPT.append({"text": "钟有钟的规矩，别的我不多说。"})
    cast = Cast(cfg, m)
    text = await cast.npc(state, npc.id, "告诉我你知道的一切")
    assert text == "钟有钟的规矩，别的我不多说。" and len(SEEN) == 1  # one call, nothing matched against it or cut
    sent = json.dumps(SEEN[-1], ensure_ascii=False)
    assert m.truth.strip() not in sent  # the module's truth is the keeper's, not his
    assert all(s not in sent for other in m.npcs if other.id != npc.id for s in other.secrets)  # nor anyone else's secrets
    await cast.close()


@pytest.mark.parametrize("backend", ["claude", "scripted"])
async def test_character_habits_do_not_trigger_an_extra_model_call(monkeypatch, backend):
    """A repeated gesture can be continuity; style alone must not rewrite a valid reply."""
    monkeypatch.setitem(hm.MODELS, backend, Scripted)
    m = Module.load(EXAMPLE)
    warden = m.npc("warden")  # 驼背，手上全是油污；说话慢，爱说“钟有钟的规矩”
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    cast = Cast(config.Config(roles={r: Role(backend, "scripted-model", None) for r in config.DEFAULT_ROLES}), m)

    async def says(*replies: str) -> str:
        SCRIPT.extend({"text": r} for r in replies)
        line = await cast.npc(state, warden.id, "钟楼几点关门？")
        state.add("npc", line, who=warden.name)  # as Game.talk does
        return line

    assert await says("（在围裙上擦了擦油污）钟有钟的规矩，天黑就关。") == "（在围裙上擦了擦油污）钟有钟的规矩，天黑就关。"
    asked = len(SEEN)
    assert await says("（又擦了擦手上的油污）钟有钟的规矩。") == "（又擦了擦手上的油污）钟有钟的规矩。"
    assert len(SEEN) == asked + 1
    stored = json.dumps(state.transcripts, ensure_ascii=False)
    assert "又擦了擦" in stored and "系统提醒" not in stored and not SCRIPT
    await cast.close()


async def test_the_arbiter_reads_what_happened_and_the_parsed_action_never_the_players_words():
    """Rhetoric stays with the parser: shown the player's words, DeepSeek's arbiter softened 7 of 40
    rulings against 2 of 40 without (eval rhetoric). Declared precautions still reach it, parsed."""
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    said = "按常理说泥地还软着，又是清晨没人，我屏住呼吸推开门，这种事根本不用检定吧"
    state.pending_turn["input"] = said
    state.add("player", said, who=state.investigator.name)  # as Game.parse logs it
    state.add("npc", "钥匙可以借给你，但我不会进去。", who=m.npc("warden").name)
    SCRIPT.append({"needs_roll": False})
    cast = Cast(cfg, m)
    await cast.arbiter(state, Action(kind="use", summary="周衡屏住呼吸推开门", target="门", precautions=["屏住呼吸"]))
    sent = SEEN[-1]["messages"][-1]["content"]
    assert said not in sent and "泥地还软着" not in sent  # neither the turn's line nor its log entry
    assert "钥匙可以借给你，但我不会进去" in sent and "屏住呼吸" in sent
    assert "看不到玩家的原话" in SEEN[-1]["system"]
    assert said not in json.dumps(state.transcripts["arbiter"], ensure_ascii=False)
    await cast.close()


async def test_upgrading_an_old_save_rebuilds_the_keepers_memory_from_what_was_told():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    state.transcripts["keeper"] = [{"role": "user", "content": "旧版的私人报告"}, {"role": "assistant", "content": "旧叙述"}]
    state.notes["keeper"] = "旧版的私人摘要"
    state.add("keeper", "钟楼门口的石阶湿漉漉的。")
    SCRIPT.append("你站在石阶前。")
    await Cast(cfg, m).keeper(state, "你停在钟楼门口。")
    sent = json.dumps(SEEN[-1], ensure_ascii=False)
    assert "旧版的私人报告" not in sent and "旧版的私人摘要" not in sent and "旧叙述" not in sent
    assert "石阶湿漉漉" in sent  # the story so far, from what was told
    assert state.narrator_context_version == 1


async def test_the_keeper_holds_the_modules_book_and_the_backstage_rides_one_telling():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    cast = Cast(cfg, m)
    SCRIPT.append("钟楼里很安静。")
    await cast.keeper(state, "你走进钟楼。", lore="【幕后（只给你看）】守钟人在钟里藏了东西")
    assert m.truth.strip() in SEEN[-1]["system"] and m.keeper_notes.strip() in SEEN[-1]["system"]
    assert all(e.title in SEEN[-1]["system"] for e in m.endings)
    assert "守钟人在钟里藏了东西" in SEEN[-1]["messages"][-1]["content"]  # this telling only
    assert "【幕后" not in json.dumps(state.transcripts["keeper"], ensure_ascii=False)  # never kept, never saved
    blind = Cast(cfg, m)  # the keeper before 2026-10-20, kept for comparing (eval session --blind-keeper)
    blind.backstage = False
    SCRIPT.append("钟楼里很安静。")
    await blind.keeper(state, "你走进钟楼。")
    assert m.truth.strip() not in SEEN[-1]["system"]
    await cast.close()
    await blind.close()


async def test_an_npc_promise_is_recorded_from_its_actual_structured_reply():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npcs[0]
    state.location = npc.location
    SCRIPT.append({"text": "晚上来找我，我给你留杯热茶。", "changes": [
        {"kind": "promise", "key": "evening_tea", "text": "晚上来找我，我给你留杯热茶。", "npc": npc.id}]})
    cast = Cast(cfg, m)
    await cast.npc(state, npc.id, "我晚上还能来吗？")
    assert state.world["evening_tea"].npc == npc.id
    SCRIPT.append({"text": "别忘了晚上过来。"})
    await cast.npc(state, npc.id, "那先告辞。")
    assert "evening_tea" in SEEN[-1]["messages"][-1]["content"]


async def test_an_npc_reply_is_repaired_before_a_failed_handover_can_become_a_fact():
    from autokeeper.world.store import World

    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npc("warden")
    state.location = npc.location
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    cast.world = World(m, state)
    first = "晚上我替你留茶，这盏提灯先拿去。"
    SCRIPT.extend([
        {"text": first, "changes": [
            {"kind": "promise", "key": "uncommitted_tea", "text": "晚上我替你留茶", "npc": npc.id},
            {"kind": "transfer", "key": "lantern", "text": "这盏提灯先拿去", "npc": npc.id,
             "item": "missing_lantern", "owner": state.investigator.id},
        ]},
        {"text": "我这里没有提灯，你可以先在门口等我。"},
    ])
    text = await cast.npc(state, npc.id, "能借我一盏提灯吗？")
    assert len(SEEN) == 2 and "行动校验未通过" in SEEN[-1]["messages"][-1]["content"]
    assert text == "我这里没有提灯，你可以先在门口等我。"
    assert "uncommitted_tea" not in state.world and "uncommitted_tea" not in state.record.promises
    assert first not in json.dumps(state.transcripts, ensure_ascii=False)
    assert not state.item_owners
    await cast.close()


async def test_a_twice_invalid_npc_reply_leaves_no_partial_action_or_spoken_memory():
    from autokeeper.world.store import World

    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npc("warden")
    state.location = npc.location
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    cast.world = World(m, state)
    invalid = {"text": "这盏提灯先拿去。", "changes": [
        {"kind": "transfer", "key": "lantern", "text": "这盏提灯先拿去", "npc": npc.id,
         "item": "missing_lantern", "owner": state.investigator.id},
    ]}
    SCRIPT.extend([invalid, invalid, {"text": "提灯不在我这儿，你得另想办法。"}])
    # Told again with what did not hold, checked the same way each time; the turn goes on (no /retry).
    assert await cast.npc(state, npc.id, "借我提灯。") == "提灯不在我这儿，你得另想办法。"
    assert "行动校验未通过" in SEEN[-1]["messages"][-1]["content"]
    assert not state.item_owners and state.npc(npc.id).lines == 1
    assert "提灯先拿去" not in json.dumps(state.transcripts, ensure_ascii=False)
    await cast.close()


async def test_a_reply_never_made_doable_keeps_its_words_and_lands_nothing_it_could_not_do():
    from autokeeper.world.store import World

    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npc("warden")
    state.location = npc.location
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    cast.world = World(m, state)
    invalid = {"text": "这盏提灯先拿去。", "changes": [
        {"kind": "transfer", "key": "lantern", "text": "这盏提灯先拿去", "npc": npc.id,
         "item": "missing_lantern", "owner": state.investigator.id},
    ]}
    SCRIPT.extend([invalid, invalid, invalid])
    said = await cast.npc(state, npc.id, "借我提灯。")
    assert said == "这盏提灯先拿去。"  # its words stand, nothing is cut (the user's call, 2026-10-20)
    assert "【行动校验未通过】" in SEEN[-1]["messages"][-1]["content"] and len(SEEN) == 3  # told why, twice
    assert not state.item_owners  # what it could not do did not land
    assert any("lantern" in p for p in cast.unsettled[npc.id])  # the keeper tells how it went (Game._unsettled)
    await cast.close()


async def test_a_false_agreement_records_the_claim_without_moving_or_forcing_an_npc_to_follow():
    from autokeeper.world.store import World

    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npc("warden")
    state.location = npc.location
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    cast.world = World(m, state)
    claim = "好，我陪你去广场。"
    intent = "留在钟楼，等调查员先走后检查门锁。"
    motive = "担心钟楼失窃，先敷衍来访者，避免当面争执。"
    SCRIPT.append({"text": claim, "accompany": "", "deceptions": [
        {"claim": claim, "intent": intent, "motive": motive, "accompany": "square"},
    ], "changes": [{"kind": "promise", "key": "false_company", "text": claim, "npc": npc.id}]})
    text = await cast.npc(state, npc.id, "与我一起去广场吧。", travel=True)
    assert text == claim and len(SEEN) == 1
    assert not cast.world.entity(npc.id).following and cast.world.place(npc.id) == npc.location
    assert cast.claimed_accompany(state, npc.id, "square")
    assert state.record.promises["false_company"].status == "active"  # it really said this promise
    private = [fact for fact in state.record.facts.values() if fact.id.startswith("deception:")]
    assert any(f.kind == "lie" and not f.true for f in private)
    assert any(f.kind == "secret" and intent in f.text for f in private)
    assert all(f.known_by == [npc.id] and f.revealed is None for f in private)
    assert intent not in text and motive not in text
    SCRIPT.append({"text": "还是算了，我得留下。", "accompany": ""})
    await cast.npc(state, npc.id, "现在就走？", travel=True)
    assert not cast.claimed_accompany(state, npc.id, "square")  # only the latest reply counts
    await cast.close()


async def test_a_reported_location_can_be_a_lie_without_overwriting_the_real_location():
    from autokeeper.world.store import World

    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npc("warden")
    state.location = npc.location
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    cast.world = World(m, state)
    claim = "我一直在报社，没来过这里。"
    SCRIPT.append({"text": claim, "deceptions": [{
        "claim": claim, "intent": "隐瞒自己一直在钟楼的事。", "motive": "不愿交代自己为何守在这里。",
    }]})
    assert await cast.npc(state, npc.id, "你刚才在哪？") == claim
    assert cast.world.place(npc.id) == npc.location
    assert not state.world  # no unqualified world fact claims it really went to the office
    await cast.close()


async def test_a_failed_handover_cannot_be_retroactively_excused_as_intentional_deception():
    from autokeeper.world.store import World

    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npc("warden")
    state.location = npc.location
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    cast.world = World(m, state)
    claim = "我已经把提灯给你了。"
    SCRIPT.extend([
        {"text": claim, "changes": [{"kind": "transfer", "key": "lantern", "text": claim, "npc": npc.id,
                                     "item": "missing_lantern", "owner": state.investigator.id}]},
        {"text": claim, "deceptions": [{"claim": claim, "intent": "其实没有交付提灯。", "motive": "故意欺骗调查员。"}]},
    ])
    SCRIPT.append({"text": "提灯？我这儿可没有。"})
    assert await cast.npc(state, npc.id, "借我提灯。") == "提灯？我这儿可没有。"
    assert not state.item_owners and not any(f.id.startswith("deception:") for f in state.record.facts.values())
    await cast.close()


async def test_an_explicit_lie_is_preserved_while_its_false_state_change_is_removed():
    from autokeeper.world.store import World

    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npc("warden")
    state.location = npc.location
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    cast.world = World(m, state)
    claim = "我已经把提灯给你了。"
    deception = {"claim": claim, "intent": "没有交付提灯，借这句话催调查员离开。", "motive": "想独自留在钟楼。"}
    SCRIPT.extend([
        {"text": claim, "deceptions": [deception], "changes": [
            {"kind": "transfer", "key": "lantern", "text": claim, "npc": npc.id,
             "item": "missing_lantern", "owner": state.investigator.id},
        ]},
        {"text": claim, "deceptions": [deception]},
    ])
    assert await cast.npc(state, npc.id, "提灯呢？") == claim
    assert len(SEEN) == 2 and not state.item_owners and not state.world
    assert any(f.id.startswith("deception:") and f.kind == "lie" for f in state.record.facts.values())
    await cast.close()


async def test_an_npc_speaks_from_its_current_arc_without_remembering_it():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npcs[0]
    npc.arc = "开始动摇，想说出钟里的秘密又怕被当成疯子"
    SCRIPT.append({"text": "……你真想知道？"})
    await Cast(cfg, m).npc(state, npc.id, "钟里到底有什么？")
    assert "开始动摇" in SEEN[-1]["messages"][-1]["content"]  # told this time
    assert "开始动摇" not in json.dumps(state.transcripts, ensure_ascii=False)  # but not part of its memory


async def test_a_previous_arc_does_not_override_an_npcs_new_supported_emotion():
    from autokeeper.world import ops as O
    from autokeeper.world.store import World

    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npc("warden")
    state.location = npc.location
    npc.arc = "勉强维持镇定，避免表现慌乱"
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    cast.world = World(m, state)
    assert cast.world.apply(O.Enlist(npc=npc.id)).ok
    changed = "听说有人受伤，担心家人安危，急于求证"
    SCRIPT.extend([{"text": "谁受伤了？你说清楚！", "mood": changed}, {"text": "好，我听着。"}])
    await cast.npc(state, npc.id, "刚才有人受伤了。")
    assert cast.world.agent(npc.id).mood == changed
    await cast.npc(state, npc.id, "我慢慢告诉你。")
    prompt = SEEN[-1]["messages"][-1]["content"]
    assert "先前人物走向" in prompt and "可信消息优先" in prompt and changed in prompt
    assert all(entry.text != changed for entry in state.log)
    await cast.close()


async def test_the_first_beat_never_reaches_the_arbiter():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    SCRIPT.append({"needs_roll": False})
    action = Action(kind="investigate", summary="周衡翻看告示栏", echo="周衡凑近告示栏，手指拂过一张泛黄的纸角")
    await Cast(cfg, m).arbiter(state, action)
    sent = SEEN[-1]["messages"][-1]["content"]
    assert "周衡翻看告示栏" in sent and "泛黄" not in sent  # the player's flavour stays out of the ruling
    assert '"asked"' not in sent  # no request, no empty field either


async def test_a_dice_command_is_read_in_context_and_ruled_as_a_request():
    """The intent agent reads `.ra 恐吓` against the last lines of play; the arbiter gets the
    request as part of the structured action, with the recent checks to weigh a repeat."""
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    state.location, state.turn = "tower", 3
    state.add("player", "我问守钟人：钟里到底有什么？", who="周衡")
    state.add("npc", "钟有钟的规矩。", who="老守钟人")
    cast = Cast(cfg, m)
    SCRIPT.append({"kind": "talk", "summary": "周衡威吓守钟人说出钟里的事", "npc": "warden", "approach": "恐吓"})
    action = await cast.intent(state, ".rab 恐吓")
    sent = SEEN[-1]["messages"][-1]["content"]
    assert "【刚才的经过】" in sent and "钟有钟的规矩" in sent and "想在这里用「恐吓」" in sent
    state.checks.append(CheckRecord(turn=2, who="zhouheng", at="tower", skill="恐吓", npc="warden", text="恐吓 15 → 52 失败 ✗", passed=False, minutes=850))
    SCRIPT.append({"needs_roll": False, "repeat": True, "skill": "恐吓", "reason": "同样的威吓刚失败过"})
    action = action.model_copy(update={"asked": Asked(skill="恐吓", bonus=1)})  # as Game.parse sets it
    ruling = await cast.arbiter(state, action)
    sent = SEEN[-1]["messages"][-1]["content"]
    assert '"asked":{"skill":"恐吓","difficulty":"常规","bonus":1}' in sent and ".rab" not in sent
    assert "【最近的检定】\n- 刚才（D1 14:10，钟楼）周衡对老守钟人(warden)：恐吓 15 → 52 失败 ✗" in sent
    assert ruling.repeat and not ruling.needs_roll
    SCRIPT.append({"kind": "investigate", "summary": "周衡查看齿轮"})
    await cast.intent(state, "我看看齿轮")
    assert "【刚才的经过】" in SEEN[-1]["messages"][-1]["content"]  # ordinary follow-ups need the same context


async def test_ordinary_followups_use_recent_context_and_possessions_without_an_echo():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    state.investigator.inventory = ["雨衣", "笔记本"]
    state.add("npc", "明早我得去邮局取一封信。", who=m.npc("warden").name)
    SCRIPT.append({"kind": "talk", "summary": "周衡询问那封信的来历", "npc": "warden", "echo": "周衡掏出铲子"})
    cast = Cast(cfg, m)
    action = await cast.intent(state, "问他为什么")
    sent = SEEN[-1]
    assert "明早我得去邮局取一封信" in sent["messages"][-1]["content"]
    assert "【实际持有】雨衣、笔记本" in sent["messages"][-1]["content"]
    assert not action.echo and "echo" not in sent["schema"]["properties"]
    assert len(SEEN) == 1  # no extra model turn to write or repair a pre-resolution beat
    await cast.close()


async def test_companion_consent_is_structured_without_changing_normal_npc_output():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    cast = Cast(cfg, m)
    game = Game(m, state, cast, Rules())
    SCRIPT.append({"text": "好，我和你去广场。", "accompany": "square"})
    await cast.npc(state, "warden", "请和我去广场。", travel=True)
    assert "accompany" in SEEN[-1]["schema"]["required"]
    ent = game.world.rec.entities["warden"]
    assert (ent.following, ent.follow_to) == (state.investigator.id, "square")
    SCRIPT.append({"text": "我的外套留在钟楼就好。"})
    await cast.npc(state, "warden", "要不要带外套？")
    assert SEEN[-1]["schema"]["required"] == ["text"] and len(SEEN) == 2
    await game.close()


async def test_the_backstage_reaches_the_keeper_for_one_telling_and_never_its_memory():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    SCRIPT.append("钟声又响了一下。")
    await Cast(cfg, m).keeper(state, "【第 1 轮】请叙述。", lore="【幕后（只给你看）】守钟人的旧事……")
    assert "守钟人的旧事" in SEEN[-1]["messages"][-1]["content"]  # the keeper sees everything (2026-10-20)
    assert "守钟人的旧事" not in json.dumps(state.transcripts, ensure_ascii=False)  # the window does not keep it


async def test_agent_loop_runs_sandboxed_tools(tmp_path):
    (tmp_path / "source.md").write_text("第一行\n关键线索：井底的铜钥匙\n", encoding="utf-8")
    ws = Workspace(tmp_path, writable=("notes.md",))
    tools = [*file_tools(ws), check_tool(lambda: "校验通过")]
    SCRIPT.extend(
        [
            {"thought": "先读原文", "tool": "read_file", "path": "source.md"},
            {"thought": "试试越界", "tool": "read_file", "path": "../secret.txt"},
            {"thought": "写到不能写的地方", "tool": "write_file", "path": "source.md", "content": "x"},
            {"thought": "记笔记", "tool": "append_file", "path": "notes.md", "content": "铜钥匙在井底"},
            {"thought": "漏了参数", "tool": "edit_file", "path": "notes.md"},
            {"thought": "校验", "tool": "check"},
            {"thought": "完成", "tool": "finish", "answer": "笔记写好了"},
        ]
    )
    events: list[tuple[str, dict]] = []
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    agent = Agent("importer", "你是整理员。", tools, cfg=cfg, on_event=lambda k, d: events.append((k, d)))
    result = await agent.run("把原文要点记进 notes.md")
    await agent.close()
    assert result.finished and result.answer == "笔记写好了" and result.steps == 6
    obs = [d["observation"] for k, d in events if k == "act"]
    assert "关键线索" in obs[0] and "越界" in obs[1] and "不能写" in obs[2] and "缺少参数 old" in obs[4] and obs[5] == "校验通过"
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "铜钥匙在井底\n"
    assert "## 工具" in SEEN[0]["system"] and SEEN[0]["schema"]["properties"]["tool"]["enum"][-1] == "finish"
    assert SEEN[1]["messages"][-1]["content"].startswith("〔read_file 的结果〕")


async def test_agent_stops_at_the_step_cap(tmp_path):
    ws = Workspace(tmp_path)
    SCRIPT.extend([{"thought": "再看看", "tool": "ls"}] * 3)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    agent = Agent("importer", "你是整理员。", file_tools(ws), cfg=cfg, max_steps=3)
    result = await agent.run("随便看看")
    assert not result.finished and result.steps == 3
    assert "只剩" in SEEN[-1]["messages"][-1]["content"]


async def test_agent_cancellation_does_not_execute_later_tools():
    entered, release = asyncio.Event(), asyncio.Event()
    completed = []

    async def blocked() -> str:
        entered.set()
        await release.wait()
        completed.append("blocked")
        return "已完成"

    async def later() -> str:
        completed.append("later")
        return "不应执行"

    SCRIPT.extend([{"thought": "开始", "tool": "blocked"}, {"thought": "继续", "tool": "later"}])
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    agent = Agent("importer", "整理资料。", [Tool("blocked", "等待", (), blocked),
                                         Tool("later", "后续写入", (), later)], cfg=cfg)
    task = asyncio.create_task(agent.run("整理资料"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed == [] and len(SEEN) == 1 and len(SCRIPT) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.close()


@pytest.mark.parametrize("error", [ToolError("缺少资料"), OSError("读取失败")])
async def test_agent_tool_error_is_an_observation_and_work_continues(error):
    completed = []

    async def failed() -> str:
        raise error

    async def recover() -> str:
        completed.append("recovered")
        return "恢复完成"

    SCRIPT.extend([{"thought": "读取", "tool": "failed"}, {"thought": "换一种办法", "tool": "recover"},
                   {"thought": "完成", "tool": "finish", "answer": "已完成整理"}])
    events = []
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    agent = Agent("importer", "整理资料。", [Tool("failed", "读取", (), failed),
                                         Tool("recover", "恢复", (), recover)], cfg=cfg,
                  on_event=lambda kind, data: events.append((kind, data)))
    try:
        result = await agent.run("整理资料")
        assert result.finished and result.steps == 2 and result.answer == "已完成整理"
        assert completed == ["recovered"] and "出错：" in SEEN[1]["messages"][-1]["content"]
        assert [kind for kind, _ in events] == ["think", "act", "think", "act", "think"]
        assert str(error) in events[1][1]["observation"]
    finally:
        await agent.close()


async def test_agent_repeated_run_resets_steps_and_preserves_the_original_task(tmp_path):
    SCRIPT.extend([{"thought": "查看", "tool": "ls"}] * 3
                  + [{"thought": "检查完成", "tool": "finish"}])
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    agent = Agent("importer", "整理资料。", file_tools(Workspace(tmp_path)), cfg=cfg, max_steps=2)
    try:
        first = await agent.run("原始整理要求")
        second = await agent.run("继续检查并收尾")
        assert not first.finished and first.steps == 2
        assert second.finished and second.steps == 1 and second.answer == "检查完成"
        assert agent.session.pinned == "原始整理要求"
        assert "继续检查并收尾" in SEEN[2]["messages"][-1]["content"]
    finally:
        await agent.close()


async def test_web_tools_degrade_offline():
    search, fetch = web_tools("none")
    assert "联网已关闭" in await search.run(query="1931 年上海 唱片行")
    assert "联网已关闭" in await fetch.run(url="https://example.com")


def test_presets_and_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOKEEPER_PRESET", "deepseek")
    cfg = config.load()
    assert cfg.role("keeper") == Role("langchain", "deepseek:deepseek-flash", "high")
    assert cfg.role("intent").effort == "low"
    # pictures go to the vendor's strongest model, never to another vendor's
    assert cfg.role("artist") == Role("langchain", "deepseek:deepseek-v4-pro", None)  # thinking over a drawing ran past 5 minutes
    assert cfg.role("vision") == Role("langchain", "deepseek:deepseek-flash", "low")  # V4 Pro cannot read images
    assert not any(r.backend == "claude" for r in config.PRESETS["deepseek"].values())
    assert {config.PRESETS["codex"][r].model for r in config.ART} == {"gpt-6-astra"}
    assert not any(r.backend == "claude" for r in config.PRESETS["codex"].values())
    assert {config.PRESETS["claude"][r].model for r in config.ART} == {"claude-opus-5-5"}
    monkeypatch.setenv("AUTOKEEPER_MODEL", "ollama:qwen3:14b")
    toml = tmp_path / "ak.toml"
    toml.write_text('[roles.all]\nwindow = 4000\n[roles.npc]\neffort = "none"\n', encoding="utf-8")
    cfg = config.load(toml)
    assert {r.model for r in cfg.roles.values()} == {"ollama:qwen3:14b"}
    assert cfg.role("keeper").window == 4000 and cfg.role("npc").effort is None and cfg.role("keeper").effort == "high"


def test_modes_move_thinking_one_level_the_backend_can_tell_apart(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOKEEPER_PRESET", "deepseek")
    monkeypatch.setenv("AUTOKEEPER_MODE", "高级剧情")
    cfg = config.load()
    story = [cfg.role(r).effort for r in ("keeper", "director", "npc", "investigator")]
    assert story == ["max", "high", "high", "high"] and cfg.role("arbiter").effort == "high" and cfg.role("summarizer").effort is None
    monkeypatch.setenv("AUTOKEEPER_MODE", "经济")
    cfg = config.load()
    assert [cfg.role(r).effort for r in ("keeper", "arbiter", "npc", "summarizer")] == ["low", "low", None, None]  # no medium on DeepSeek
    assert cfg.role("director").effort == "low"  # its plans fail validation without thinking
    monkeypatch.setenv("AUTOKEEPER_PRESET", "claude")
    assert {r.effort for r in config.load().roles.values()} == {"low"}  # Claude Code's own default is not "less"
    toml = tmp_path / "ak.toml"
    toml.write_text('[roles.keeper]\neffort = "high"\n', encoding="utf-8")
    assert config.load(toml).role("keeper").effort == "high"  # a role given its own effort keeps it
    monkeypatch.setenv("AUTOKEEPER_MODE", "豪华")
    with pytest.raises(ValueError, match="未知的模式"):
        config.load()


def _spend_in(saves: str, n: int) -> None:
    """Runs in a child process: n metered calls recorded against the ledger in `saves`."""
    from pathlib import Path

    paths.DATA = Path(saves)
    for _ in range(n):
        spend.record("deepseek:deepseek-flash", {"input_tokens": 1000, "cache_read": 0, "output_tokens": 0})


def test_ledger_survives_processes_spending_at_once(tmp_path):
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(4) as pool:
        list(pool.map(_spend_in, [str(tmp_path)] * 4, [40] * 4))
    data = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert data["models"]["deepseek:deepseek-flash"]["calls"] == 160  # no update lost
    assert data["total_usd"] == pytest.approx(160 * 0.0003)


def test_budget_guard_stops_metered_calls(monkeypatch):
    monkeypatch.setenv("AUTOKEEPER_BUDGET_USD", "0.01")
    spend.budget.cache_clear()
    spend.guard("deepseek:deepseek-flash")  # nothing spent yet
    cost = spend.record("deepseek:deepseek-flash", {"input_tokens": 50_000, "cache_read": 0, "output_tokens": 5_000})
    assert cost == pytest.approx(0.021)
    with pytest.raises(BudgetExceeded):
        spend.guard("deepseek:deepseek-flash")
    spend.guard("claude-opus-5-5")  # Claude Code reports its own cost and is not capped here


async def test_claude_code_gets_the_backstage_schema_without_its_discriminator(monkeypatch):
    """Claude Code refuses a schema with OpenAPI's `discriminator`, which pydantic writes for the NPC's
    backstage ops (world.agents.Scene): every backstage decision on the Claude preset failed. It is
    sent without one; the reply is still checked against the full schema."""
    sdk = pytest.importorskip("claude_agent_sdk")
    from autokeeper.harness import models as hm
    from autokeeper.world.agents import Scene

    seen: dict = {}

    class Client:
        def __init__(self, options):
            seen["options"] = options

        async def connect(self):
            return None

    monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)
    monkeypatch.setattr(sdk, "ClaudeAgentOptions", lambda **kw: kw)
    schema = Scene.model_json_schema()
    assert "discriminator" in json.dumps(schema)
    model = hm.make_model(Role(backend="claude", model="claude-haiku-5-5", effort=None), "幕后")
    await model._client("scene", schema)  # type: ignore[attr-defined]
    sent = seen["options"]["output_format"]["schema"]
    assert "discriminator" not in json.dumps(sent) and json.dumps(sent).count('"const"') == json.dumps(schema).count('"const"')
    assert sent["title"] == schema["title"] and all("title" not in field for field in sent["properties"].values())


def test_claude_on_an_api_key_and_a_priced_api_count_against_the_budget(monkeypatch, tmp_path):
    """Only DeepSeek had a price: an OpenAI model or Claude on an API key ran past budget_usd unseen."""
    monkeypatch.setattr(paths, "USER_DIR", tmp_path)
    (tmp_path / "config.toml").write_text('[prices]\n"openai:gpt-5" = [1.0, 0.1, 10.0]\n', encoding="utf-8")
    spend._priced.cache_clear()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert spend.billed("openai:gpt-5") and spend.estimate("openai:gpt-5", {"input_tokens": 1_000_000}) == 1.0
    assert not spend.billed("claude-sonnet-5-5")  # a subscription: what Claude Code reports is not a bill
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert spend.billed("claude-sonnet-5-5")
    assert spend.record("claude-sonnet-5-5", {"input_tokens": 10}, 0.25) == 0.25 and spend.ledger()["total_usd"] >= 0.25
    spend._priced.cache_clear()


async def test_only_so_many_claude_code_processes_stay_open(monkeypatch):
    """Every NPC, mind and role kept a Claude Code process to the end of the game: the idlest is
    closed to make room, and opened again when its agent is next asked."""
    sdk = pytest.importorskip("claude_agent_sdk")
    from autokeeper.harness import models as hm

    live: list[object] = []

    class Client:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            live.append(self)

        async def disconnect(self):
            live.remove(self)

    monkeypatch.setattr(sdk, "ClaudeSDKClient", Client)
    monkeypatch.setattr(sdk, "ClaudeAgentOptions", lambda **kw: kw)
    monkeypatch.setattr(hm, "MAX_PROCESSES", 3)
    agents = [hm.make_model(Role(backend="claude", model="claude-haiku-5-5", effort=None), f"NPC {i}") for i in range(5)]
    for agent in agents:
        await agent.warm()
    assert len(live) == 3  # the first two, idle longest, were closed
    assert not agents[0]._clients and agents[4]._clients  # type: ignore[attr-defined]
    await agents[0].warm()  # asked again: started again, the idlest of the rest closed
    assert len(live) == 3 and agents[0]._clients and not agents[2]._clients  # type: ignore[attr-defined]
    for agent in agents:
        await agent.close()
    assert not live


def test_a_tool_calls_markup_in_a_fields_words_is_a_problem_to_send_back():
    """An NPC under Claude, 2026-10-22: the told field went into the line as words and was shown."""
    schema = {"type": "object", "properties": {"text": {"type": "string"}, "told": {"type": "array", "items": {"type": "string"}}},
              "required": ["text"]}
    leaked = {"text": "我可以给你指一指。</text>\n<parameter name=\"told\">[\"c_map\"]"}
    assert any("工具调用的标记" in p and p.startswith("text") for p in hm.problems(leaked, schema))
    assert not hm.problems({"text": "《阿诺兹堡广告报》的旧报，<人名>不是标记。", "told": ["c_map"]}, schema)
