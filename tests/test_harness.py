"""The harness without any network: scripted models stand in for every provider."""

from __future__ import annotations

import json
from collections import deque

import pytest

from autokeeper import config
from autokeeper.agents.cast import Action, Cast
from autokeeper.config import EXAMPLE, Role
from autokeeper.engine.game import Game
from autokeeper.harness import Agent, BudgetExceeded, Reply, Session, spend
from autokeeper.harness import models as hm
from autokeeper.harness.tools import Workspace, check_tool, file_tools, web_tools
from autokeeper.module.schema import Module

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
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    monkeypatch.setattr(config, "USER_DIR", tmp_path / "user")
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
    rows = [json.loads(line) for line in (config.SAVES / "traces").glob("*.jsonl").__next__().read_text().splitlines()]
    assert [r.get("fallback_for", "") for r in rows] == ["", "flaky-model", "flaky-model"]
    assert "ConnectionError" in rows[0]["error"]


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
    assert (keeper["p50"], keeper["p95"]) == (2.0, 4.0) or (keeper["p50"], keeper["p95"]) == (4.0, 4.0)
    assert (npc["errors"], npc["fallbacks"], npc["cache_rate"]) == (1, 1, 0.0)
    assert [(r["run"], r["calls"], r["end"]) for r in trace.runs(rows)] == [("a", 3, "2026-10-18T05:00:05"), ("b", 1, "2026-10-18T06:00:00")]
    assert trace.summarize(rows, None)[0]["calls"] == 4


def test_strict_schema_for_codex_closes_every_object():
    strict = hm.strict_schema(Action.model_json_schema())
    assert strict["additionalProperties"] is False and set(strict["required"]) == set(strict["properties"])
    assert "default" not in json.dumps(strict)
    full = {k: "" for k in strict["properties"]} | {"kind": "investigate", "summary": "翻抽屉"}
    assert not hm.problems(full, Action.model_json_schema())  # what strict mode yields still passes our check


def test_prompted_json_carries_schema_and_example():
    text = hm.json_instruction(Action.model_json_schema())
    assert "json" in text and '"kind": "investigate"' in text  # DeepSeek JSON mode wants the word and an example


def test_claude_prompt_is_append_only_for_prompt_caching():
    turns = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}句"} for i in range(5)]
    shorter, longer = hm._blocks(turns[:3], None), hm._blocks(turns, None)
    assert longer[: len(shorter)] == shorter


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
    assert "回答1" in s.summary and s.transcript[-1]["content"] == "回答3" * 3


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


async def test_npc_leak_is_rolled_back_out_of_memory():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npcs[0]
    secret = next(c for c in m.clues if c.npc == npc.id and c.id in npc.gates)
    SCRIPT.extend([secret.text, "我什么也不知道。"])
    cast = Cast(cfg, m)
    text = await cast.npc(state, npc.id, "告诉我你知道的一切")
    assert text == "我什么也不知道。"
    assert secret.text not in json.dumps(state.transcripts, ensure_ascii=False)
    assert "系统提醒" in SEEN[-1]["messages"][-1]["content"]


async def test_an_npc_speaks_from_its_current_arc_without_remembering_it():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    npc = m.npcs[0]
    npc.arc = "开始动摇，想说出钟里的秘密又怕被当成疯子"
    SCRIPT.append("……你真想知道？")
    await Cast(cfg, m).npc(state, npc.id, "钟里到底有什么？")
    assert "开始动摇" in SEEN[-1]["messages"][-1]["content"]  # told this time
    assert "开始动摇" not in json.dumps(state.transcripts, ensure_ascii=False)  # but not part of its memory


async def test_the_first_beat_never_reaches_the_arbiter():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    SCRIPT.append({"needs_roll": False})
    action = Action(kind="investigate", summary="周衡翻看告示栏", echo="周衡凑近告示栏，手指拂过一张泛黄的纸角")
    await Cast(cfg, m).arbiter(state, action)
    sent = SEEN[-1]["messages"][-1]["content"]
    assert "周衡翻看告示栏" in sent and "泛黄" not in sent  # the player's flavour stays out of the ruling


async def test_lore_reaches_the_keeper_but_not_its_memory():
    m = Module.load(EXAMPLE)
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    SCRIPT.append("钟声又响了一下。")
    await Cast(cfg, m).keeper(state, "【第 1 轮】请叙述。", lore="【模组知识库检索】守钟人的旧事……")
    assert "守钟人的旧事" in SEEN[-1]["messages"][-1]["content"]  # this turn sees it
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


async def test_web_tools_degrade_offline():
    search, fetch = web_tools("none")
    assert "联网已关闭" in await search.run(query="1931 年上海 唱片行")
    assert "联网已关闭" in await fetch.run(url="https://example.com")


def test_presets_and_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOKEEPER_PRESET", "deepseek")
    cfg = config.load()
    assert cfg.role("keeper") == Role("langchain", "deepseek:deepseek-flash", "high")
    assert cfg.role("intent").effort == "low"
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

    config.SAVES = Path(saves)
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
