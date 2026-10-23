"""Real keeper tools retain new receipts once, without stale scenes or replayed work.

Character measurements use the existing scripted writer, never provider token estimates.
The same two-voice scene before this change used 13,755 / 14,493 / 15,269 input
characters; the first NPC's exact line occurred 0 / 2 / 4 times in those windows.
"""

from __future__ import annotations

import copy
import json

import pytest

from autokeeper.engine import graph as turn_graph
from autokeeper.engine.state import GameState
from autokeeper.harness.context import Session
from autokeeper.world import knowledge
from autokeeper.world import ops as O

from .test_keeper_tools_execution import final, role_calls, shut, table, voice
from .test_request_execution import run
from .test_scene import SCRIPT, SEEN, sandbox  # noqa: F401

ORIGINAL = "我检查一下随身器材。"
FIRST = "拿着这块布，擦擦手。"
SECOND = "我在门外等你们。"
BASELINE_INPUT_CHARS = (13755, 14493, 15269)


@pytest.fixture
def keeper_sizes(monkeypatch):
    measured = []
    original = Session.send

    async def send(self, prompt, **kwargs):
        if self.key == "keeper" and kwargs.get("schema", {}).get("title") in {"HostedReply", "ContinuedHostedReply"}:
            measured.append({
                "prompt": prompt,
                "remember": kwargs.get("remember"),
                "parts": self.context_size(prompt, kwargs.get("schema"), extra=kwargs.get("extra", "")),
            })
        return await original(self, prompt, **kwargs)

    monkeypatch.setattr(Session, "send", send)
    return measured


def two_people(fake, **kwargs):
    game, graph, cast = table(fake, **kwargs)
    if kwargs.get("state") is None:
        assert game.world.apply(O.Move(entity="preacher", to="tower", actor="engine")).ok
    return game, graph, cast


def script(game, *, private=False, leaves=False):
    first = FIRST + game.m.clue("c_diary").text if private else FIRST
    answer = {"text": first, "items": [{"name": "擦手布", "to": game.state.investigator.id}]}
    if private:
        game.state.npc("warden").unlocked.append("c_diary")
        answer["told"] = ["c_diary"]
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡检查随身器材", "target": "随身器材"}},
        {"needs_roll": True, "skill": "侦查", "minutes": 3},
        voice("warden", "看见他整理工具，拿一块擦手布给他。", private=private),
        answer,
        voice("preacher", "看见他整理工具，告诉他你在门外等候。"),
        {"text": SECOND, **({"leave": "square"} if leaves else {})},
        final("器材整理好了。"),
    ])
    return first


def words(messages):
    return "\n".join(message["content"] for message in messages)


def memory(state):
    return words(state.transcripts["keeper"])


def assert_once(state, first=FIRST):
    remembered = memory(state)
    assert remembered.count(first) == remembered.count(SECOND) == 1
    assert remembered.count("【第 1 轮｜") == 1


async def next_action(graph, game):
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡收好擦手布"}},
        {"needs_roll": False, "minutes": 0},
        final("擦手布收好了。"),
    ])
    _, offers = await run(graph, game, {"input": "我收好擦手布。"})
    assert not offers and not SCRIPT
    return role_calls("Plan")[-1]


async def test_two_actual_voices_keep_a_live_complete_report_and_only_new_receipts_in_memory(fake, keeper_sizes):
    game, graph, cast = two_people(fake)
    script(game)
    fake.queue(1)
    start = game.state.minutes
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT
        hosted = role_calls("HostedReply")
        assert len(hosted) == 3 and len(role_calls("Dialogue")) == 2
        measured = [{"step": index + 1, "input_chars": row["parts"]["total_chars"],
                     "history_chars": row["parts"]["history_chars"],
                     "first_line_copies": words(call["messages"]).count(FIRST)}
                    for index, (row, call) in enumerate(zip(keeper_sizes, hosted, strict=True))]
        print(json.dumps({"unit": "Unicode characters, not tokens", "before_input_chars": BASELINE_INPUT_CHARS,
                          "after": measured,
                          "after_transcript_chars": sum(len(m["content"]) for m in game.state.transcripts["keeper"])},
                         ensure_ascii=False))
        assert len(game.state.checks) == 1 and game.state.checks[0].passed and not fake.values
        assert game.state.minutes == start + 3 and game.state.investigator.inventory.count("擦手布") == 1
        for call in hosted:
            current = call["messages"][-1]["content"]
            assert "周衡检查随身器材" in current and "侦查" in current
            assert current.count(FIRST) <= 1 and current.count(SECOND) <= 1
        assert "擦手布" in hosted[1]["messages"][-1]["content"]
        assert FIRST in words(hosted[-1]["messages"]) and SECOND in words(hosted[-1]["messages"])
        assert_once(game.state)
        planned = await next_action(graph, game)
        assert FIRST in words(planned["messages"]) and SECOND in words(planned["messages"])
        assert len(role_calls("Plan")) == 2 and len(role_calls("Dialogue")) == 2
    finally:
        await shut([game], [cast])


async def test_private_disclosure_and_real_handover_survive_deduplication_without_teaching_another_npc(fake):
    game, graph, cast = two_people(fake, party=True)
    actor = game.state.investigator.id
    first = script(game, private=True, leaves=True)
    fake.queue(1)
    try:
        events, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT
        assert_once(game.state, first)
        assert game.state.investigator.inventory.count("擦手布") == 1
        assert game.world.place("preacher") == "square"
        assert "preacher" not in role_calls("HostedReply")[-1]["schema"]["properties"]["npc"]["enum"]
        recorded = memory(game.state)
        assert '"told": ["c_diary"]' in recorded and '"private": true' in recorded
        assert "广场" in recorded  # Its actual departure, beyond its promise to wait outside.
        assert "clue:c_diary" in {f.id for f in knowledge.known(game.world.rec, actor)}
        assert "clue:c_diary" not in {f.id for f in knowledge.known(game.world.rec, "lin")}
        assert "c_diary" not in game.state.clues
        private = next(event for event in events if event["type"] == "npc" and event["text"] == first)
        assert private["audience"] == [actor]
        assert first not in words(role_calls("Dialogue")[1]["messages"])
        planned = await next_action(graph, game)
        assert first in words(planned["messages"]) and '"told": ["c_diary"]' in words(planned["messages"])
    finally:
        await shut([game], [cast])


async def test_a_saved_tool_that_becomes_impossible_retains_its_problem_once_and_never_invents_a_reply(fake):
    game, graph, cast = two_people(fake)
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡检查随身器材", "target": "随身器材"}},
        {"needs_roll": True, "skill": "侦查", "minutes": 3},
        voice("warden", "看见他整理工具，拿一块擦手布给他。"),
        {"text": FIRST, "items": [{"name": "擦手布", "to": game.state.investigator.id}]},
        voice("preacher", "问他是否整理完毕。"),
        final("器材整理好了，另一人已经离开。"),
    ])
    fake.queue(1)
    moved = False

    def leave():
        nonlocal moved
        calls = game.state.pending_turn.get("keeper", {}).get("calls", [])
        if len(calls) == 2 and not calls[-1].get("done") and not moved:
            moved = True
            assert game.world.apply(O.Move(entity="preacher", to="square", actor="engine")).ok

    game.checkpoint = leave
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT and moved
        assert len(role_calls("HostedReply")) == 3 and len(role_calls("Dialogue")) == 1
        remembered = memory(game.state)
        assert remembered.count(FIRST) == 1
        assert '"problem"' in remembered and "人物不在当前现场，未执行配音或操作" in remembered
        assert "人物不在当前现场，未执行配音或操作" in words(role_calls("HostedReply")[-1]["messages"])
        assert len([entry for entry in game.state.log if entry.kind == "npc"]) == 1
        assert game.state.investigator.inventory.count("擦手布") == 1 and len(game.state.checks) == 1
        planned = await next_action(graph, game)
        assert "人物不在当前现场，未执行配音或操作" in words(planned["messages"])
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize(("stage", "private"), [
    *[(stage, False) for stage in ("before-call", "first-call", "first-reply", "first-shown", "first-done",
                                  "second-call", "second-reply", "second-shown", "second-done", "final-saved")],
    ("first-shown", True), ("second-done", True),
])
async def test_two_voice_receipts_restore_once_at_every_saved_boundary(fake, monkeypatch, stage, private):
    game, graph, cast = two_people(fake, party=private)
    games, casts, saved, emitted = [game], [cast], [], []
    first = script(game, private=private)
    fake.queue(1)
    start = game.state.minutes
    original_record = turn_graph.record_event

    def record(g, event):
        emitted.append(copy.deepcopy(event))
        original_record(g, event)

    monkeypatch.setattr(turn_graph, "record_event", record)

    def disconnect():
        journal = game.state.pending_turn
        receipt = journal.get("keeper", {})
        calls = receipt.get("calls", [])
        replies = receipt.get("voice_turn", {}).get("replies", {})
        if stage == "before-call":
            ready = journal.get("talk_done") and not calls
        elif stage == "final-saved":
            ready = "text" in receipt and not receipt.get("landed")
        else:
            part, point = stage.split("-")
            index, npc = (0, "warden") if part == "first" else (1, "preacher")
            said = replies.get(f"keeper:{index}:{npc}", {})
            exists = len(calls) > index
            ready = exists and {
                "call": not said,
                "reply": bool(said) and not said.get("shown"),
                "shown": said.get("shown") and not calls[index].get("done"),
                "done": calls[index].get("done") and len(calls) == index + 1,
            }[point]
        if ready and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError(f"断线：{stage}")

    game.checkpoint = disconnect
    try:
        with pytest.raises(RuntimeError, match=f"断线：{stage}"):
            await run(graph, game, {"input": ORIGINAL})
        assert len(saved) == 1
        restored = GameState.model_validate_json(saved[0])
        game, graph, cast = two_people(fake, module=game.m.model_copy(deep=True), state=restored)
        games.append(game)
        casts.append(cast)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert_once(game.state, first)
        assert game.state.turn == 1 and game.state.minutes == start + 3
        assert len(game.state.checks) == 1 and not fake.values
        assert game.state.investigator.inventory.count("擦手布") == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert len(role_calls("Dialogue")) == 2 and len(role_calls("HostedReply")) == 3
        for line in (first, SECOND):
            assert len([entry for entry in game.state.log if entry.kind == "npc" and entry.text == line]) == 1
            assert len([event for event in emitted if event["type"] == "npc" and event["text"] == line]) == 1
        if private:
            actor = game.state.investigator.id
            assert "clue:c_diary" in {f.id for f in knowledge.known(game.world.rec, actor)}
            assert "clue:c_diary" not in {f.id for f in knowledge.known(game.world.rec, "lin")}
            assert "c_diary" not in game.state.clues
        planned = await next_action(graph, game)
        assert first in words(planned["messages"]) and SECOND in words(planned["messages"])
    finally:
        await shut(games, casts)


async def test_retelling_after_two_tools_replaces_the_final_exchange_using_its_actual_report_and_memory(fake):
    game, graph, cast = two_people(fake)
    script(game)
    fake.queue(1)
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT and game.told is not None
        told = game.told
        report, remembered = told.report, told.memory
        assert FIRST in report and SECOND in report
        assert remembered == game.state.transcripts["keeper"][-2]["content"]
        before = (game.state.turn, game.state.minutes, copy.deepcopy(game.state.checks),
                  list(game.state.investigator.inventory), len(game.world.rec.journal),
                  len(game.state.log), len(game.state.transcripts["keeper"]))
        retold_events = []

        async def emit(event):
            retold_events.append(copy.deepcopy(event))

        game.emit = emit  # Retell runs outside the completed graph's stream context, on the screen's listener.
        replacement = "你收起器材，擦手布仍在手里。"
        SCRIPT.append(replacement)
        assert await game.retell("短一点") == replacement and not SCRIPT
        assert SEEN[-1]["schema"] is None and SEEN[-1]["messages"][-1]["content"].startswith(report)
        assert game.state.transcripts["keeper"][-2:] == [
            {"role": "user", "content": remembered}, {"role": "assistant", "content": replacement},
        ]
        assert "器材整理好了。" not in memory(game.state)
        assert_once(game.state)
        assert before == (game.state.turn, game.state.minutes, game.state.checks,
                          game.state.investigator.inventory, len(game.world.rec.journal),
                          len(game.state.log), len(game.state.transcripts["keeper"]))
        assert [entry.text for entry in game.state.log if entry.kind == "keeper"] == [replacement]
        assert retold_events[0] == {"type": "keeper_start", "again": True}
        assert retold_events[-1] == {"type": "keeper_end", "text": replacement}
        assert len(role_calls("Dialogue")) == 2 and len(role_calls("HostedReply")) == 3
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1 and not fake.values
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("fixed", [False, True], ids=["rewrite", "fixed-scene-prose"])
async def test_rejected_scene_does_not_acknowledge_or_lose_an_already_executed_tool_receipt(fake, fixed):
    game, graph, cast = two_people(fake)
    bad = final("两人已经消失了。", scene={"here": []})  # Existing people cannot vanish without a destination.
    final_text = "两人仍在钟楼，你收好了器材。"
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡检查随身器材", "target": "随身器材"}},
        {"needs_roll": True, "skill": "侦查", "minutes": 3},
        voice("warden", "看见他整理工具，拿一块擦手布给他。"),
        {"text": FIRST, "items": [{"name": "擦手布", "to": game.state.investigator.id}]},
        bad,
        *([copy.deepcopy(bad), final_text] if fixed else [final(final_text)]),
    ])
    fake.queue(1)
    start = game.state.minutes
    try:
        events, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT
        assert memory(game.state).count(FIRST) == 1 and memory(game.state).count("【第 1 轮｜") == 1
        assert "两人已经消失了。" not in memory(game.state)
        assert [entry.text for entry in game.state.log if entry.kind == "keeper"] == [final_text]
        assert "".join(event["text"] for event in events if event["type"] == "keeper_delta") == final_text
        assert game.world.place("warden") == game.world.place("preacher") == "tower"
        assert game.state.investigator.inventory.count("擦手布") == 1
        assert len(game.state.checks) == 1 and not fake.values and game.state.minutes == start + 3
        assert len(role_calls("Dialogue")) == 1 and len(role_calls("HostedReply")) == 3
        assert len([event for event in events if event["type"] == "npc" and event["text"] == FIRST]) == 1
        if fixed:
            assert SEEN[-1]["schema"] is None and "【场面已经定了】" in SEEN[-1]["messages"][-1]["content"]
        else:
            assert "【上一稿的 scene 没通过核对】" in SEEN[-1]["messages"][-1]["content"]
        planned = await next_action(graph, game)
        assert FIRST in words(planned["messages"])
    finally:
        await shut([game], [cast])


async def test_final_display_disconnect_recovers_the_remembered_receipt_without_repeating_acknowledged_npcs(fake, monkeypatch):
    game, graph, cast = two_people(fake)
    script(game)
    fake.queue(1)
    games, casts, saved, emitted = [game], [cast], [], []
    start = game.state.minutes
    original_record, original_narrate = turn_graph.record_event, game._narrate

    def record(g, event):
        emitted.append(copy.deepcopy(event))
        original_record(g, event)

    monkeypatch.setattr(turn_graph, "record_event", record)

    async def narrate(report, on_delta=None, lore="", turn=None, **kwargs):
        async def shown(text):
            if on_delta:
                await on_delta(text)
            if text == "器材整理好了。" and not saved:
                receipt = game.state.pending_turn["keeper"]
                assert receipt["text"] == text and receipt["landed"] and receipt["memory_at"] == 2
                assert game.state.transcripts["keeper"][-2]["content"].startswith(
                    f"【主持记录 {receipt['memory_id']}｜配音回执至 2】")
                assert all(reply["shown"] for reply in receipt["voice_turn"]["replies"].values())
                saved.append(game.state.model_dump_json())
                raise RuntimeError("终稿已展示，交付确认尚未完成")

        return await original_narrate(report, shown, lore, turn, **kwargs)

    monkeypatch.setattr(game, "_narrate", narrate)
    try:
        with pytest.raises(RuntimeError, match="终稿已展示，交付确认尚未完成"):
            await run(graph, game, {"input": ORIGINAL})
        assert len(saved) == 1 and len(role_calls("HostedReply")) == 3
        restored = GameState.model_validate_json(saved[0])
        body_acknowledged = restored.pending_turn["keeper"].get("body_shown", False)
        game, graph, cast = two_people(fake, module=game.m.model_copy(deep=True), state=restored)
        games.append(game)
        casts.append(cast)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert_once(game.state)
        assert len(role_calls("HostedReply")) == 3  # Delivery resumes the saved final, never regenerates it.
        assert len(role_calls("Dialogue")) == 2 and len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert game.state.turn == 1 and game.state.minutes == start + 3
        assert len(game.state.checks) == 1 and not fake.values and game.state.investigator.inventory.count("擦手布") == 1
        for line in (FIRST, SECOND):
            assert len([event for event in emitted if event["type"] == "npc" and event["text"] == line]) == 1
        # The body can be redelivered only if its caller had not acknowledged it; NPC receipts already held.
        bodies = [event for event in emitted if event["type"] == "keeper_delta" and event["text"] == "器材整理好了。"]
        assert len(bodies) == (1 if body_acknowledged else 2)
        assert [entry.text for entry in game.state.log if entry.kind == "keeper"] == ["器材整理好了。"]
    finally:
        await shut(games, casts)
