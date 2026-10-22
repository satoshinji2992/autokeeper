"""Developer inspection is private and read-only; all model replies are scripted."""

from __future__ import annotations

import asyncio
import io
import json

import aiohttp
import pytest
from rich.console import Console
from textual.widgets import Button

from autokeeper import config, paths
from autokeeper.agents.cast import Cast
from autokeeper.config import Config, Role
from autokeeper.engine import diagnostics, observability
from autokeeper.engine.developer_web import open_dashboard
from autokeeper.engine.game import Game
from autokeeper.engine.observability import Journal
from autokeeper.harness import Reply, Session
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.settings import SettingsModal
from autokeeper.tui.screens.game import GameScreen

from .test_engine import make
from .test_tui import canned, until  # noqa: F401


class DiagnosticModel(hm.Model):
    seen: list[dict] = []
    fail = False

    async def _call(self, messages, *, schema, images, on_delta):
        await asyncio.sleep(0)
        self.seen.append({"system": self.system, "messages": messages})
        if self.fail:
            raise RuntimeError("diagnostic model unavailable")
        reply = "根据第 3 轮记录，重复出现在旁白阶段。"
        if on_delta:
            await on_delta(reply)
        return Reply(text=reply, thinking="提供方返回的思考片段", usage={"input_tokens": 20, "output_tokens": 10})


@pytest.fixture
def diagnostic_model(monkeypatch):
    monkeypatch.setitem(hm.MODELS, "diagnostic-test", DiagnosticModel)
    monkeypatch.setattr(DiagnosticModel, "seen", [])
    monkeypatch.setattr(DiagnosticModel, "fail", False)
    role = Role("diagnostic-test", "test-model", None)
    return Config(roles={r: role for r in config.DEFAULT_ROLES} | {"diagnostic": role})


def game_with(cfg):
    m = Module.load(paths.EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    from autokeeper.rules.coc import Rules

    return Game(m, st, Cast(cfg, m), Rules())


def test_snapshot_keeps_private_world_data_and_is_detached(fake, monkeypatch):
    m = Module.load(paths.EXAMPLE)
    game, _, _ = make(m, fake, {})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-example-credential")
    game.state.memory = "请求失败：private-example-credential"
    game.state.notes["npc:warden"] = "人物自己的记忆"
    before = game.state.model_dump_json()
    data = diagnostics.snapshot(game, errors=["Authorization: Bearer abcdefghijklmnop"])
    assert game.state.model_dump_json() == before
    assert data["module"]["truth"] == m.truth
    assert any(f["kind"] == "secret" for f in data["world"]["facts"].values())
    serialized = json.dumps(data, ensure_ascii=False)
    assert "private-example-credential" not in serialized and "abcdefghijklmnop" not in serialized
    data["sessions"]["summaries"]["npc:warden"] = "changed"
    assert game.state.notes["npc:warden"] == "人物自己的记忆"
    output = diagnostics.export(data, "排查建议")
    assert output.is_relative_to(paths.dev_output("diagnostics"))
    assert json.loads(output.read_text(encoding="utf-8"))["analysis"] == "排查建议"


async def test_diagnosis_uses_its_own_session_and_only_records_usage(diagnostic_model):
    game = game_with(diagnostic_model)
    game.state.transcripts["keeper"] = [{"role": "user", "content": "既有剧情"}]
    data = diagnostics.snapshot(game)
    before = game.state.model_dump(exclude={"usage"})
    text = await diagnostics.analyze(game, data, "NPC 为什么重复台词？")
    assert "旁白阶段" in text and len(DiagnosticModel.seen) == 1
    assert DiagnosticModel.seen[0]["system"] == diagnostics.SYSTEM
    assert game.state.model_dump(exclude={"usage"}) == before
    assert game.state.usage["diagnostic"]["calls"] == 1
    assert not game.cast.sessions
    await game.close()
    await game.cast.close()


async def test_failed_diagnosis_does_not_change_the_game(diagnostic_model, monkeypatch):
    game = game_with(diagnostic_model)
    monkeypatch.setattr(DiagnosticModel, "fail", True)
    before = game.state.model_dump_json()
    with pytest.raises(RuntimeError):
        await diagnostics.analyze(game, diagnostics.snapshot(game), "排查错误")
    assert game.state.model_dump_json() == before
    await game.close()
    await game.cast.close()


async def test_full_call_capture_is_isolated_between_games_even_with_disk_trace_off(diagnostic_model, monkeypatch):
    monkeypatch.setenv("AUTOKEEPER_TRACE", "off")
    first, second = game_with(diagnostic_model), game_with(diagnostic_model)
    one = Session("npc", diagnostic_model.role("npc"), "第一局的系统资料")
    two = Session("npc", diagnostic_model.role("npc"), "第二局的系统资料")
    await asyncio.gather(first.cast._send("npc:first", one, first.state, "第一局问题"),
                         second.cast._send("npc:second", two, second.state, "第二局问题"))
    assert len(first.cast.debug_calls) == len(second.cast.debug_calls) == 1
    call = first.cast.debug_calls[0]
    assert call["session"] == "npc:first" and call["system"] == "第一局的系统资料"
    assert call["thinking"] == "提供方返回的思考片段" and call["input"][-1]["content"] == "第一局问题"
    assert second.cast.debug_calls[0]["system"] == "第二局的系统资料"
    assert not list(paths.traces().glob("*.jsonl"))
    await asyncio.gather(one.close(), two.close(), first.close(), second.close(), first.cast.close(), second.cast.close())


async def test_failed_role_calls_remain_available_for_diagnosis(diagnostic_model, monkeypatch):
    game = game_with(diagnostic_model)
    monkeypatch.setattr(DiagnosticModel, "fail", True)
    session = Session("keeper", diagnostic_model.role("keeper"), "叙述系统")
    with pytest.raises(RuntimeError):
        await game.cast._send("keeper", session, game.state, "本轮公开结果")
    call = diagnostics.snapshot(game)["calls"][-1]
    assert "RuntimeError" in call["error"] and call["input"][-1]["content"] == "本轮公开结果"
    await asyncio.gather(session.close(), game.close(), game.cast.close())


def test_large_diagnostic_material_is_bounded_valid_json_and_keeps_the_question(fake):
    game, _, _ = make(Module.load(paths.EXAMPLE), fake, {})
    data = diagnostics.snapshot(game)
    data["world"]["facts"] = {f"fact-{i}": {"text": "很长的资料" * 1000} for i in range(300)}
    text = diagnostics.material(data, "检查第 3 轮的重复对话")
    assert len(text) <= 65000 and "第 3 轮" in json.loads(text)["question"]
    assert "资料过大" in text or "截断" in text or "omitted_earlier_entries" in text


async def test_more_than_sixty_calls_survive_resuming_the_same_slot(diagnostic_model):
    game = game_with(diagnostic_model)
    session = Session("npc", diagnostic_model.role("npc"), "完整系统资料")
    for number in range(65):
        game.state.turn = number
        await game.cast._send("npc:first", session, game.state, f"请求 {number}")
    assert len(game.cast.debug_calls) == 60
    assert len(diagnostics.snapshot(game)["calls"]) == 65
    await asyncio.gather(session.close(), game.close(), game.cast.close())
    resumed = game_with(diagnostic_model)
    rows = diagnostics.snapshot(resumed)["calls"]
    assert len(rows) == 65 and rows[0]["input"][-1]["content"] == "请求 0"
    assert rows[-1]["input"][-1]["content"] == "请求 64"
    await asyncio.gather(resumed.close(), resumed.cast.close())


async def test_pending_model_inputs_and_stream_progress_are_recorded_before_completion(diagnostic_model, monkeypatch):
    game = game_with(diagnostic_model)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(self, messages, *, schema, images, on_delta):
        entered.set()
        await on_delta("已经返回的片段")
        await release.wait()
        return Reply(text="完整回复")

    monkeypatch.setattr(DiagnosticModel, "_call", slow)
    session = Session("npc", diagnostic_model.role("npc"), "人物背景")
    task = asyncio.create_task(game.cast._send("npc:first", session, game.state, "本轮问题", on_delta=lambda _: asyncio.sleep(0)))
    await entered.wait()
    await asyncio.sleep(0)
    rows, _ = game.debug_journal.read(kind="model")
    assert any(r["data"].get("phase") == "started" and r["data"]["input"][-1]["content"] == "本轮问题" for r in rows)
    assert any(r["data"].get("phase") == "delta" and r["data"]["text"] == "已经返回的片段" for r in rows)
    assert not game.debug_journal.calls()
    assert diagnostics.snapshot(game)["backstage"]["active_calls"][0]["session"] == "npc:first"
    release.set()
    await task
    assert game.debug_journal.calls()[0]["output"] == "完整回复"
    assert diagnostics.snapshot(game)["backstage"]["active_calls"] == []
    await asyncio.gather(session.close(), game.close(), game.cast.close())


async def test_web_reading_is_private_read_only_and_never_calls_the_model(diagnostic_model):
    game = game_with(diagnostic_model)
    before = game.state.model_dump_json()
    url = await open_dashboard(game)
    server = game.debug_server
    async with aiohttp.ClientSession() as http:
        async with http.get(url) as response:
            assert response.status == 200 and "终端继续游玩" in await response.text()
            assert response.headers["X-Frame-Options"] == "DENY"
            assert response.headers["Cache-Control"] == "no-store"
        async with http.get(url + "api/snapshot") as response:
            data = await response.json()
            assert data["module"]["truth"] == game.m.truth and data["calls"] == []
        async with http.get(url + "api/records?limit=1") as response:
            data = await response.json()
            assert len(data["records"]) == 1 and data["more"]
        async with http.get(url + "api/records?limit=bad") as response:
            assert response.status == 400
        async with http.get(url.replace(server.key, "wrong")) as response:
            assert response.status == 404
        async with http.get(url, headers={"Host": "hostile.example"}) as response:
            assert response.status == 403
        async with http.get(url, headers={"Origin": "https://hostile.example"}) as response:
            assert response.status == 403
        async with http.post(url + "api/analysis", json={"question": "unexpected"},
                             headers={"Sec-Fetch-Site": "cross-site"}) as response:
            assert response.status == 403
    assert not DiagnosticModel.seen and game.state.model_dump_json() == before
    await game.close()
    await game.cast.close()


async def test_web_analysis_requires_explicit_submission_and_selected_history_is_available(diagnostic_model, monkeypatch):
    game = game_with(diagnostic_model)
    game.debug_journal.append("world", {"move": "托马斯出发"}, turn=82)
    game.debug_journal.append("world", {"move": "另一回合"}, turn=90)
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def answer(owner, data, question, on_delta):
        requests.append((data, question))
        entered.set()
        await release.wait()
        await on_delta("排查中间结果")
        return "第 82 轮没有同行操作。"

    monkeypatch.setattr(diagnostics, "analyze", answer)
    url = await open_dashboard(game)
    before = game.state.model_dump_json()
    async with aiohttp.ClientSession() as http:
        async with http.post(url + "api/analysis", json={"question": "核对同行", "from_turn": 82, "to_turn": 84}) as response:
            assert response.status == 202
        await entered.wait()
        async with http.post(url + "api/analysis", json={"question": "重复请求"}) as response:
            assert response.status == 409
        assert requests[0][0]["history"][0]["data"]["move"] == "托马斯出发"
        assert all(82 <= r["turn"] <= 84 for r in requests[0][0]["history"])
        release.set()
        await game.debug_server.task
        async with http.get(url + "api/analysis") as response:
            data = await response.json()
            assert not data["working"] and "第 82 轮" in data["answer"]
        async with http.get(url + "api/export") as response:
            data = await response.json()
            assert any(r["data"].get("move") == "另一回合" for r in data["records"])
            assert "第 82 轮" in data["analysis"]["answer"]
    assert game.state.model_dump_json() == before
    await asyncio.gather(game.close(), game.cast.close())


def test_persistent_journal_sanitizes_credentials_and_recovers_complete_lines(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-secret-key")
    journal = Journal("../../some slot")
    journal.append("world", {"truth": "隐藏剧情", "Authorization": "Bearer secret", "text": "private-test-secret-key"})
    assert journal.path.parent == paths.games() / "debug"
    journal.path.write_text(journal.path.read_text(encoding="utf-8") + '{"incomplete":', encoding="utf-8")
    rows, cursor = journal.read()
    assert len(rows) == 1 and cursor == 1
    assert rows[0]["data"]["truth"] == "隐藏剧情"
    assert "private-test-secret-key" not in journal.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
@pytest.mark.usefixtures("canned")
async def test_f12_opens_a_webpage_while_terminal_play_remains_available(monkeypatch, size):
    m = Module.load(paths.EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="developer-web")
    app = AutoKeeperApp(theme="amber")
    opened = []
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    async with app.run_test(size=size) as pilot:
        screen = GameScreen(m, st, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        before = st.model_dump_json()
        await pilot.press("f12")
        assert await until(pilot, lambda: bool(opened))
        assert opened[0].startswith("http://127.0.0.1:") and app.screen is screen
        assert not screen.busy and st.model_dump_json() == before
        # the mode shows on the screen itself: the top bar and a panel of this turn's calls and the world
        panel = screen.query_one("#dev")
        bar = Console(width=200, record=True, file=io.StringIO())
        bar.print(screen.query_one("#topbar").content)
        assert panel.display and screen.has_class("-dev") and "开发者模式" in bar.export_text()
        shown_text = str(panel.body.render())
        assert "人物实际位置" in shown_text and "接下来" in shown_text
        server = screen.game.debug_server
        await pilot.press("f12")  # off again: the page stays, the screen goes back
        await pilot.pause()
        assert not panel.display and not screen.has_class("-dev") and len(opened) == 1
        screen._menu_chosen("settings")  # 设置 → 开发者模式, on at once with 设置 still open
        assert await until(pilot, lambda: isinstance(app.screen, SettingsModal))
        settings = app.screen
        await pilot.pause()
        assert not settings.query_one("#settings-plugins").display  # 规则插件 only in the mode
        settings.query_one("#settings-developer", Button).press()
        assert await until(pilot, lambda: panel.display)
        assert str(settings.query_one("#settings-developer", Button).label) == "关闭开发者模式"
        assert settings.query_one("#settings-plugins").display
        await pilot.press("escape")
        await pilot.pause()
        screen.action_open_dev_web()  # the page again, from the panel's link
        assert await until(pilot, lambda: len(opened) == 2)
        assert opened[0] == opened[1] and server is screen.game.debug_server


def test_history_selection_finds_an_old_turn_and_keeps_its_evidence_in_large_modules(fake):
    game, _, _ = make(Module.load(paths.EXAMPLE), fake, {})
    game.state.turn = 118
    for turn in range(80, 119):
        game.debug_journal.append("world", {"npc": "warden", "note": f"世界操作 {turn}"}, turn=turn)
    rows, _ = game.debug_journal.read()
    selected, scope = diagnostics.select_evidence(game, "核对第82轮的同行操作", rows)
    assert scope["mentioned_turns"] == [82] and all(81 <= r["turn"] <= 83 for r in selected)
    assert any(r["turn"] == 82 for r in selected)
    data = diagnostics.snapshot(game)
    data["history"], data["evidence_scope"] = selected, scope
    data["world"]["facts"] = {f"fact-{i}": {"text": "不相关的长资料" * 1000} for i in range(500)}
    material = diagnostics.material(data, "核对第82轮的同行操作")
    assert len(material) <= 65000 and "世界操作 82" in material and "世界操作 118" not in material
    assert "history" in __import__("json").loads(material)


def test_evidence_search_matches_partial_npc_names_and_an_explicit_record(fake):
    game, _, _ = make(Module.load(paths.EXAMPLE), fake, {})
    game.m.npcs[0].name = "托马斯·金博尔"
    game.state.turn = 118
    game.debug_journal.append("call", {"session": f"npc:{game.m.npcs[0].id}", "output": "我陪你去公墓。"}, turn=82)
    for turn in range(90, 119):
        game.debug_journal.append("call", {"session": "keeper", "output": "无关叙述"}, turn=turn)
    rows, _ = game.debug_journal.read()
    selected, scope = diagnostics.select_evidence(game, "托马斯为什么没有同行？", rows)
    assert game.m.npcs[0].id in scope["terms"] and any(r["turn"] == 82 for r in selected)
    old = next(r for r in rows if r.get("turn") == 82)
    selected, scope = diagnostics.select_evidence(game, f"检查记录 #{old['cursor']}", rows)
    assert old in selected and scope["requested_cursors"] == [old["cursor"]]


def test_streaming_events_do_not_repeatedly_serialize_game_state(fake, monkeypatch):
    from autokeeper.engine.observability import record_event

    game, _, _ = make(Module.load(paths.EXAMPLE), fake, {})
    snapshots = []
    monkeypatch.setattr(game.debug_journal, "sync", lambda game: snapshots.append(True))
    for _ in range(10):
        record_event(game, {"type": "keeper_delta", "text": "片段"})
    record_event(game, {"type": "keeper_start"})
    record_event(game, {"type": "keeper_end"})
    assert snapshots == []  # every event is kept; the whole state is taken once the turn is over
    record_event(game, {"type": "sheet"})
    assert snapshots == [True]
    rows, _ = game.debug_journal.read(kind="event")
    assert len(rows) == 13


def test_journal_continues_after_a_truncated_line_and_pages_backwards():
    journal = Journal("recover-records")
    journal.append("world", {"n": 1})
    with journal.path.open("a") as stream:
        stream.write('{"partial":')
    journal.append("world", {"n": 2})
    journal.append("world", {"n": 3})
    rows, _ = journal.read()
    assert [r["data"]["n"] for r in rows] == [1, 2, 3]
    recent, before = journal.recent(limit=2)
    assert [r["data"]["n"] for r in recent] == [2, 3]
    earlier, _ = journal.recent(before=before, limit=2)
    assert [r["data"]["n"] for r in earlier] == [1]


def test_journal_retains_every_segment_and_keeps_cursors_across_rotation_and_reload(monkeypatch):
    monkeypatch.setattr(observability, "MAX_BYTES", 1)
    journal = Journal("long-game")
    for n in range(1, 5):
        journal.append("call" if n % 2 == 0 else "world", {"n": n})
    first, cursor = journal.read(limit=3)
    original = journal.path.with_suffix(".1.jsonl").read_bytes()
    assert [r["data"]["n"] for r in first] == [1, 2, 3] and cursor == 3

    journal = Journal("long-game")  # a resumed game must continue, not overwrite archive .1
    for n in range(5, 16):
        journal.append("call" if n % 2 == 0 else "world", {"n": n})
    assert journal.path.with_suffix(".1.jsonl").read_bytes() == original
    later, final = journal.read(after=cursor)
    assert [r["data"]["n"] for r in later] == list(range(4, 16)) and final == 15
    assert [r["n"] for r in journal.calls()] == list(range(2, 16, 2))
    recent, before = journal.recent(limit=3)
    assert [r["data"]["n"] for r in recent] == [13, 14, 15] and before == 13
    previous, _ = journal.recent(before=before, limit=3, kind="call")
    assert [r["data"]["n"] for r in previous] == [8, 10, 12]


def test_journal_pages_past_an_interrupted_line_in_an_archived_segment(monkeypatch):
    journal = Journal("interrupted-archive")
    journal.append("world", {"n": 1})
    with journal.path.open("a") as stream:
        stream.write('{"partial":')
    monkeypatch.setattr(observability, "MAX_BYTES", 1)
    journal.append("world", {"n": 2})
    journal.append("world", {"n": 3})
    rows, cursor = journal.read()
    assert [r["data"]["n"] for r in rows] == [1, 2, 3] and cursor == 4
    resumed, final = journal.read(after=1)
    assert [r["data"]["n"] for r in resumed] == [2, 3] and final == cursor


def test_journal_read_in_progress_keeps_its_cursor_when_the_open_segment_rotates(monkeypatch):
    journal = Journal("reading-during-rotation")
    journal.append("world", {"n": 1})
    journal.append("world", {"n": 2})
    original_lines = journal._lines
    rotated = False

    def rotating_lines():
        nonlocal rotated
        for number, line in original_lines():
            yield number, line
            if not rotated:
                rotated = True
                monkeypatch.setattr(observability, "MAX_BYTES", 1)
                journal.append("world", {"n": 3})

    monkeypatch.setattr(journal, "_lines", rotating_lines)
    first, cursor = journal.read(limit=2)
    assert [r["data"]["n"] for r in first] == [1, 2] and cursor == 2
    assert journal.path.with_suffix(".1.jsonl").exists()

    journal.append("world", {"n": 4})  # rotate again before requesting the next page
    later, final = journal.read(after=cursor)
    assert [r["data"]["n"] for r in later] == [3, 4] and final == 4
    assert [r["cursor"] for r in first + later] == [1, 2, 3, 4]
    empty, unchanged = journal.read(after=final)
    assert empty == [] and unchanged == final


async def test_stateless_helpers_are_archived_without_creating_actor_memory(diagnostic_model, monkeypatch):
    game = game_with(diagnostic_model)

    async def helper(self, messages, *, schema, images, on_delta):
        data = {"look": "查看桌面", "ask": "询问近况", "dare": "敲门"}
        return Reply(text="进展摘要", data=data, usage={"input_tokens": 10, "output_tokens": 5})

    monkeypatch.setattr(DiagnosticModel, "_call", helper)
    before = dict(game.state.transcripts)
    await game.cast.progress(game.state, "玩家公开进展")
    await game.cast.suggest(game.state, game.state.investigator.id, "当前处境")
    assert game.state.transcripts == before
    assert {r["session"].split(":")[0] for r in game.debug_journal.calls()} == {"progress", "suggest"}
    assert all(game.state.usage[key]["calls"] == 1 for key in ("progress", "suggest"))
    await asyncio.gather(game.close(), game.cast.close())


async def test_graph_retrieval_records_its_model_calls_and_actual_context(diagnostic_model, monkeypatch):
    from autokeeper import knowledge

    game = game_with(diagnostic_model)
    session = Session("lore", diagnostic_model.role("lore"), "图谱检索")

    class LocalIndex:
        async def context(self, query, **kw):
            reply = await session.send(query)
            return "检索结果：" + reply.text

        async def close(self):
            await session.close()

    game.cast.kb = LocalIndex()
    monkeypatch.setattr(knowledge, "available", lambda: True)
    monkeypatch.setattr(knowledge, "exists", lambda _: True)
    text = await game.cast.lore(game.state, "钟楼线索", keywords=["钟楼"])
    calls = game.debug_journal.calls()
    assert calls[-1]["session"] == "lore" and calls[-1]["input"][-1]["content"] == "钟楼线索"
    assert game.state.usage["lore"]["calls"] == 1
    rows, _ = game.debug_journal.read(kind="retrieval")
    assert rows[-1]["data"]["context"] == text
    await asyncio.gather(game.close(), game.cast.close())
