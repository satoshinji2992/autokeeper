"""Turn recovery and cancellable planning use local scripted models and isolated worlds."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper import paths
from autokeeper.agents.cast import Cast, ContinuedTelling
from autokeeper.engine import presentation
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module

from .test_engine import make
from .test_keeper_tools_execution import final, role_calls, shut, table
from .test_request_execution import run
from .test_scene import SCRIPT, SEEN, Writer, sandbox  # noqa: F401


async def test_empty_chronicle_preserves_the_previous_summary_and_uncompressed_source_log(fake):
    module = Module.load(paths.EXAMPLE)
    game, cast, _ = make(module, fake, {})
    state = game.state
    state.memory = "更早已经明确约定，凌晨三点到桥头会合。"
    state.add("keeper", "重要原话：我会在桥头等你，不要去旅馆。")
    for number in range(48):
        state.add("keeper", f"普通公开经过 {number}。")
    previous = state.memory, [entry.model_dump(mode="json") for entry in state.log]
    requests = []

    async def empty_summary(current, entries):
        requests.append(entries)
        return ""

    cast.chronicle = empty_summary
    try:
        await game.remember()
        assert len(requests) == 1 and "重要原话" in requests[0]
        assert (state.memory, [entry.model_dump(mode="json") for entry in state.log]) == previous
    finally:
        await game.close()


async def test_corrective_telling_delivers_and_persists_its_valid_private_observation_and_media(fake):
    game, graph, cast = table(fake, party=True)
    private = "林秋独自听见自己口袋里的表停了一拍。"
    media = {"ref": "handout:h_diary", "to": ["lin"]}
    # Confirm that a missing resource or unknown recipient cannot explain the failed delivery.
    assert presentation.prepare(game.m, game.state, media, "audit:media")["audience"] == ["lin"]
    bad = final("布道者走到了你身边。", scene={"here": ["preacher"]},
                continuation={"pending": "等玩家决定下一步。"})
    corrective = {"scene": {"here": ["warden"]}, "text": "老人仍在钟楼，你暂时没有离开。",
                  "asides": [{"to": ["lin"], "text": private}], "media": [media],
                  "continuation": {"pending": "等玩家决定下一步。"}}
    ContinuedTelling.model_validate(corrective)
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡整理衣领"}, "remaining": "听完回应后再决定是否出发。"},
        {"needs_roll": False, "minutes": 0},
        bad, bad, corrective,
    ])
    try:
        events, offers = await run(graph, game, {"input": "我整理衣领，听完回应后再决定是否出发。"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert len(role_calls("ContinuedHostedReply")) == 2
        assert len(role_calls("ContinuedTelling")) == 1
        restored = GameState.model_validate_json(game.state.model_dump_json())
        observed = {
            "private_event": any(event["type"] == "aside" and event.get("text") == private
                                 and event.get("audience") == ["lin"] for event in events),
            "media_event": any(event["type"] == "presentation" and event.get("audience") == ["lin"]
                               for event in events),
            "private_saved": any(entry.text == private and entry.audience == ["lin"]
                                 for entry in [*restored.log, *restored.private_history]),
            "media_saved": any(item["ref"] == media["ref"] and item["audience"] == ["lin"]
                               for item in restored.presentations),
        }
        assert observed == dict.fromkeys(observed, True)
    finally:
        await shut([game], [cast])


def unchanged_story(state):
    return state.model_dump(mode="json", exclude={"usage"})


def preparation_reply(who="zhouheng", text="整理衣领"):
    return {"steps": [{"token": "0", "plan": {"action": {"kind": "use", "summary": text}}}]}


async def test_one_player_preparation_uses_an_isolated_conversation_and_seals_without_another_model_call(fake, monkeypatch):
    game, _, cast = table(fake)
    state = game.state
    who = state.investigator.id
    state.transcripts["keeper"] = [{"role": "user", "content": "之前的正式行动"},
                                    {"role": "assistant", "content": "之前实际发生的经过"}]
    state.narrator_context_version = 1
    state.notes["keeper"] = "先前的约定。"
    before = unchanged_story(state)
    proposal = preparation_reply(who)
    proposal["steps"][0]["plan"]["people"] = [{"id": "night_porter", "name": "夜班门房", "role": "值班门房"}]
    SCRIPT.append(proposal)

    def no_world_write(*args, **kwargs):
        pytest.fail("Preparing or sealing a batch cannot execute a world operation")

    monkeypatch.setattr(game.world, "apply", no_world_write)
    checkpoints = []
    game.checkpoint = lambda: checkpoints.append(state.model_dump_json())
    try:
        draft = await game.prepare_party([(who, "整理衣领")])
        assert unchanged_story(state) == before and not checkpoints and "keeper" not in cast.sessions
        assert state.usage["plan"]["calls"] == 1
        assert not SCRIPT and len(role_calls("PartyPlan")) == 1
        assert "之前实际发生的经过" in str(SEEN[-1]["messages"])
        assert game.seal_party(draft) == [(who, "整理衣领")]
        assert len(checkpoints) == 1 and state.round_plan["planned"]
        assert len(role_calls("PartyPlan")) == 1 and state.usage["plan"]["calls"] == 1
        assert state.transcripts["keeper"][-1]["content"] == "之前实际发生的经过"
    finally:
        await shut([game], [cast])


async def test_cancelling_a_query_followup_keeps_its_completed_draft_out_of_the_live_world_and_memory(fake, monkeypatch):
    game, _, cast = table(fake)
    state = game.state
    who = state.investigator.id
    before = unchanged_story(state)
    queried, waiting = [], asyncio.Event()
    original = Writer._call
    calls = 0

    async def model_call(model, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            waiting.set()
            await asyncio.Event().wait()
        reply = await original(model, messages, **kwargs)
        reply.cost_usd = 0.005
        return reply

    async def lookup(scratch, snapshot, query):
        assert scratch is not cast and snapshot is not state
        queried.append(query)
        return "已查到：书房里只有登记过的物件。"

    monkeypatch.setattr(Writer, "_call", model_call)
    monkeypatch.setattr(Cast, "look_up", lookup)
    first = preparation_reply(who)
    first["lookup"] = "查询书房里的物件"
    first["steps"][0]["plan"]["people"] = [{"id": "discarded_porter", "name": "未落实的门房"}]
    SCRIPT.append(first)
    task = asyncio.create_task(game.prepare_party([(who, "查看物件")]))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(queried) == 1 and unchanged_story(state) == before
        assert state.usage["plan"]["input"] == 100 and state.usage["plan"]["calls"] == 2
        assert state.usage["plan"]["cost_usd"] == pytest.approx(0.005)
        assert not game._late.notes and not state.round_plan and "keeper" not in cast.sessions
        SCRIPT.append(preparation_reply(who, "重新查看门口"))
        draft = await game.prepare_party([(who, "重新查看门口")])
        assert "查询书房里的物件" not in str(SEEN[-1]["messages"])
        assert unchanged_story(state) == before
        known = dict(state.usage["plan"])
        assert known["input"] == 200 and known["cost_usd"] == pytest.approx(0.01)
        assert game.seal_party(draft) == [(who, "重新查看门口")]
        assert state.usage["plan"] == known
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await shut([game], [cast])


async def test_an_invalid_preparation_does_not_seal_or_execute_an_unplanned_batch(fake):
    from autokeeper.engine.game import PreparationError

    from .test_party_arrangement import PartyCast, make_hub

    hub = make_hub(fake, PartyCast(fail=True))
    before = hub.state.model_dump_json()
    try:
        with pytest.raises(PreparationError, match="尚未完成安排"):
            await hub.game.prepare_party([("zhouheng", "查看门口"), ("lin", "查看窗边")])
        assert hub.state.model_dump_json() == before and not hub.game._late.notes
    finally:
        await hub.game.close()


async def test_preparation_refuses_a_changed_local_clock_but_not_an_unrelated_global_sequence(fake):
    from autokeeper.engine.game import PreparationError

    game, _, cast = table(fake)
    who = game.state.investigator.id
    SCRIPT.extend([preparation_reply(who), preparation_reply(who)])
    try:
        draft = await game.prepare_party([(who, "整理衣领")])
        game.state.minutes += 1
        with pytest.raises(PreparationError, match="场面已变化"):
            game.seal_party(draft)
        assert not game.state.round_plan
        draft = await game.prepare_party([(who, "整理衣领")])
        game.world.rec.seq += 1  # Another scene's unrelated operation is not an invalidation policy.
        assert game.seal_party(draft) == [(who, "整理衣领")]
    finally:
        await shut([game], [cast])


async def test_hosted_format_error_is_repaired_without_requiring_a_manual_retry_or_repeating_a_roll(fake):
    game, graph, cast = table(fake)
    broken = final("门边空空的。") | {"asides": [], "media": []}
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡查看门边", "target": "门边"}},
        {"needs_roll": True, "skill": "侦查", "minutes": 1},
        broken, broken, final("门边空空的。"),
    ])
    fake.queue(1)
    try:
        events, offers = await run(graph, game, {"input": "我查看门边。"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert len(game.state.checks) == 1 and len(role_calls("Ruling")) == 1
        assert len(role_calls("HostedReply")) == 3
        assert "主持回复格式需修正" in str(role_calls("HostedReply")[-1]["messages"])
        assert [event["text"] for event in events if event["type"] == "keeper_delta"] == ["门边空空的。"]
    finally:
        await shut([game], [cast])


async def test_repeated_semantically_invalid_hosted_reply_stops_without_executing_its_requested_voice(fake):
    from autokeeper.engine.game import PreparationError

    game, graph, cast = table(fake)
    mixed = {"kind": "tell", "npc": "warden", "instruction": "把东西交出来。",
             "telling": {"scene": {}, "text": "不能既请求配音又宣告它已发生。"}}
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡整理衣领"}},
        {"needs_roll": False, "minutes": 0},
        mixed, mixed,
    ])
    try:
        with pytest.raises(PreparationError, match="主持回复尚未通过校验"):
            await run(graph, game, {"input": "我整理衣领。"})
        assert not SCRIPT and len(role_calls("HostedReply")) == 2
        assert not role_calls("Dialogue") and game.state.pending_turn
    finally:
        await shut([game], [cast])


async def test_unrepairable_preparation_keeps_known_usage_even_without_a_successful_session_reply(fake, monkeypatch):
    from autokeeper.engine.game import PreparationError

    game, _, cast = table(fake)
    who = game.state.investigator.id
    before = unchanged_story(game.state)
    original = Writer._call

    async def billed_model(model, messages, **kwargs):
        reply = await original(model, messages, **kwargs)
        reply.cost_usd = 0.005  # An offline stand-in for a provider's reported cost.
        return reply

    monkeypatch.setattr(Writer, "_call", billed_model)
    SCRIPT.extend([" ", " "])
    try:
        with pytest.raises(PreparationError, match="尚未完成安排"):
            await game.prepare_party([(who, "查看门边")])
        assert not SCRIPT and unchanged_story(game.state) == before
        usage = game.state.usage["plan"]
        assert usage["calls"] == 1 and usage["input"] == 200 and usage["output"] == 20
        assert usage["cost_usd"] == pytest.approx(0.01)
        assert not game.state.transcripts and not game.state.round_plan
    finally:
        await shut([game], [cast])


async def test_compaction_retains_private_mail_and_events_appended_while_the_summary_is_pending(fake):
    game, cast, _ = make(Module.load(paths.EXAMPLE), fake, {})
    state = game.state
    for number in range(49):
        state.add("keeper", f"原有公开经过 {number}。")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_summary(current, entries):
        entered.set()
        await release.wait()
        return "已压缩的公开经过。"

    cast.chronicle = slow_summary
    task = asyncio.create_task(game.remember())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        who = state.investigator.id
        private = state.add("keeper", "按你刚才的私信：你仍持有那把钥匙。", audience=[who])
        notice = state.add("event", "门外传来了巡警的脚步声。")
        release.set()
        await task
        assert state.memory == "已压缩的公开经过。" and len(state.log) == 26
        assert state.log[-2:] == [private, notice]
        assert private.audience == [who]
        restored = GameState.model_validate_json(state.model_dump_json())
        assert restored.log[-2].text == private.text and restored.log[-1].text == notice.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await game.close()


async def test_hosted_envelope_repair_keeps_a_settled_npc_handover_and_voice_once(fake):
    from .test_keeper_tools_execution import voice

    game, graph, cast = table(fake)
    who = game.state.investigator.id
    broken = final("器材整理好了。") | {"asides": [], "media": []}
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡检查随身器材", "target": "随身器材"}},
        {"needs_roll": True, "skill": "侦查", "minutes": 1},
        voice("warden", "拿一块擦手布递给来访者。"),
        {"text": "这块布给你，拿着擦手。", "items": [{"name": "擦手布", "to": who}]},
        broken, broken, final("器材整理好了。"),
    ])
    fake.queue(1)
    try:
        events, offers = await run(graph, game, {"input": "我检查随身器材。"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert len(game.state.checks) == 1 and len(role_calls("Ruling")) == 1
        assert len(role_calls("Dialogue")) == 1 and len(role_calls("HostedReply")) == 4
        assert game.state.investigator.inventory.count("擦手布") == 1
        assert [event["text"] for event in events if event["type"] == "npc"] == ["这块布给你，拿着擦手。"]
        assert [event["text"] for event in events if event["type"] == "keeper_delta"] == ["器材整理好了。"]
        assert len([entry for entry in game.state.log if entry.kind == "npc"]) == 1
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("entrypoint", ["prepare", "arrange", "seal"])
async def test_a_batch_cannot_accept_two_declarations_for_the_same_investigator(fake, entrypoint):
    from autokeeper.engine.game import PreparationError

    game, _, cast = table(fake)
    who = game.state.investigator.id
    order = [(who, "看门边"), (who, "看窗边")]
    try:
        with pytest.raises(PreparationError, match=r"每名调查员.*一份声明"):
            if entrypoint == "prepare":
                await game.prepare_party(order)
            elif entrypoint == "arrange":
                await game.arrange_party(order)
            else:
                draft = game._party_preparation(order[:1], None)
                draft.order = order
                game.seal_party(draft)
        assert not game.state.round_plan and not role_calls("PartyPlan")
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("structured", [False, True], ids=["text-stream", "json-stream"])
@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_a_cancelled_or_timed_out_stream_keeps_reported_usage_without_publishing_a_draft(
        monkeypatch, structured, stop):
    from types import SimpleNamespace

    from autokeeper.agents.cast import TELLING
    from autokeeper.config import Role
    from autokeeper.harness import models as hm
    from autokeeper.harness import trace

    waiting = asyncio.Event()
    buffered, rows = [], []
    draft = '{"scene":{},"text":"尚未完成的草稿'

    class Chat:
        def bind(self, **kwargs):
            return self

        async def astream(self, history):
            yield SimpleNamespace(text=draft, content=draft, additional_kwargs={"reasoning_content": "私有推理"},
                                  usage_metadata={"input_tokens": 321, "output_tokens": 17,
                                                  "input_token_details": {"cache_read": 100}})
            waiting.set()
            await asyncio.Event().wait()

    model = object.__new__(hm.LangChainModel)
    hm.Model.__init__(model, Role("langchain", "deepseek:deepseek-flash", None), "临时准备", label="plan")
    model.provider, model.mode, model.chat = "deepseek", "json", Chat()
    monkeypatch.setattr(hm, "ATTEMPTS", 1)
    if stop == "timeout":
        monkeypatch.setattr(hm, "call_timeout", lambda label: 0.1)

    async def buffer(piece):
        buffered.append(piece)

    buffer.shown = False
    with trace.capture(rows.append):
        task = asyncio.create_task(model.complete([{"role": "user", "content": "准备本轮行动"}],
                                                 schema=TELLING if structured else None, on_delta=buffer))
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            if stop == "cancel":
                task.cancel()
                error_type = asyncio.CancelledError
            else:
                error_type = TimeoutError
            with pytest.raises(error_type):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await model.close()
    completed, = (row for row in rows if row["phase"] == "complete")
    assert completed["input_tokens"] == 321 and completed["output_tokens"] == 17
    assert completed["cache_read"] == 100 and completed["cost_usd"] > 0
    assert completed["output"] == "" and completed["data"] is None
    assert completed["thinking"] == "" and buffered == [draft]


@pytest.mark.parametrize("phase", ["assistant-usage", "final-usage"])
async def test_claude_cancellation_keeps_known_usage_and_discards_the_client_and_draft(monkeypatch, phase):
    sdk = pytest.importorskip("claude_agent_sdk")
    from autokeeper.config import Role
    from autokeeper.harness import models as hm
    from autokeeper.harness import trace

    waiting, disconnected = asyncio.Event(), asyncio.Event()
    rows = []

    class Client:
        async def query(self, prompt):
            pass

        async def receive_response(self):
            yield sdk.AssistantMessage(content=[sdk.TextBlock(text="未完成的私有草稿")], model="mock",
                                       usage={"input_tokens": 200, "cache_creation_input_tokens": 10,
                                              "cache_read_input_tokens": 50, "output_tokens": 17})
            if phase == "final-usage":
                yield sdk.ResultMessage(subtype="success", duration_ms=10, duration_api_ms=10, is_error=False,
                                        num_turns=1, session_id="offline", total_cost_usd=0.03,
                                        usage={"input_tokens": 300, "cache_creation_input_tokens": 20,
                                               "cache_read_input_tokens": 100, "output_tokens": 50})
            waiting.set()
            await asyncio.Event().wait()

        async def disconnect(self):
            disconnected.set()

    model = hm.ClaudeCodeModel(Role("claude", "claude-haiku-5-5", None), "临时准备", label="plan")
    client = Client()

    async def local_client(key, schema):
        model._clients[key] = client
        return client

    monkeypatch.setattr(model, "_client", local_client)
    with trace.capture(rows.append):
        task = asyncio.create_task(model.complete([{"role": "user", "content": "准备本轮行动"}]))
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(disconnected.wait(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await model.close()
    completed, = (row for row in rows if row["phase"] == "complete")
    expected = (260, 17, 50) if phase == "assistant-usage" else (420, 50, 100)
    assert (completed["input_tokens"], completed["output_tokens"], completed["cache_read"]) == expected
    if phase == "final-usage":
        assert completed["cost_usd"] == pytest.approx(0.03)
    assert completed["output"] == "" and completed["data"] is None and not model._clients


async def test_a_retry_counts_a_failed_attempts_reported_usage_once_without_reusing_its_draft(monkeypatch):
    from autokeeper.config import Role
    from autokeeper.harness import models as hm
    from autokeeper.harness import trace

    class Retry(hm.Model):
        calls = 0

        async def _call(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                error = ConnectionError("offline scripted disconnection")
                hm._carry_usage(error, hm.Reply(text="丢弃草稿", data={"private": "不能当成回复"},
                                               usage={"input_tokens": 7, "output_tokens": 2}, cost_usd=0.003))
                assert error.partial_reply.text == "" and error.partial_reply.data is None
                raise error
            return hm.Reply(text="合法回复", usage={"input_tokens": 11, "output_tokens": 3}, cost_usd=0.004)

    model = Retry(Role("scripted", "offline-retry", None), "临时准备", label="plan")
    monkeypatch.setattr(hm, "RETRY_WAIT", (0, 0))
    rows = []
    with trace.capture(rows.append):
        reply = await model.complete([{"role": "user", "content": "准备行动"}])
    assert reply.text == "合法回复" and reply.data is None and reply.retries == 1
    assert reply.usage == {"input_tokens": 18, "output_tokens": 5}
    assert reply.cost_usd == pytest.approx(0.007)
    completed, = (row for row in rows if row["phase"] == "complete")
    assert completed["input_tokens"] == 18 and completed["output"] == "合法回复"
