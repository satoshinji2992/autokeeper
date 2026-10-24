"""The real keeper uses world tools without a second narrator or premature delivery.

Only the scripted writer and temporary game state are used. These are execution
and recovery checks, not an assessment of a live model's narrative quality.
"""

from __future__ import annotations

import copy
import json

import pytest

from autokeeper.engine import graph as turn_graph
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Event
from autokeeper.world import ops as O

from .test_keeper_memory_runtime import memory, words
from .test_keeper_tools_execution import final, role_calls, shut, table
from .test_request_execution import run
from .test_scene import SCRIPT, SEEN, sandbox  # noqa: F401

ORIGINAL = "我把清点册合上，等老人继续说。"
BODY = "老人把清点册收好，没有催你离开。"
OLD_SECRET = "教堂仓库暂时封闭，布道者准备继续留在广场。"
NEW_SECRET = "教堂仓库藏着来信，布道者改为先去办公室保护证人。"
PRIVATE_GOAL = "先保护办公室里的证人，不向楼上的来访者透露。"


def tool(name: str, **arguments) -> dict:
    return {"kind": "tool", "tool": {"tool": name, **arguments}}


def begin(*, minutes: int = 0) -> list[dict]:
    return [
        {"action": {"kind": "wait" if minutes else "use",
                    "summary": "周衡原地清点器材并等候三十分钟" if minutes else "周衡合上清点册"}},
        {"needs_roll": False, "minutes": minutes},
    ]


def prepare(fake, *, state=None, module=None):
    game, graph, cast = table(fake, state=state, module=module)
    if state is None:
        assert game.world.apply(O.AddFact(fact="keeper_secret", text=OLD_SECRET, fact_kind="secret",
                                          about=["preacher"], known_by=["preacher"],
                                          actor="engine", visibility="private")).ok
    return game, graph, cast


def operations(game, *, at: int | None = None) -> list[dict]:
    now = game.state.minutes if at is None else at
    return [
        {"kind": "move", "entity": "preacher", "to": "office", "visibility": "private"},
        {"kind": "enlist", "npc": "preacher", "goals": [PRIVATE_GOAL], "mood": "紧张但已有主意"},
        {"kind": "plan", "npc": "preacher", "steps": [
            {"id": "protect_witness", "text": "把证人的口信记到自己的纸上", "due": now + 90,
             "completion": "report"},
        ]},
        {"kind": "schedule", "event": "preacher_return", "at": now + 30, "place": "office",
         "text": "布道者准备返回广场", "participants": ["preacher"],
         "ops": [{"kind": "move", "entity": "preacher", "to": "square", "visibility": "private"}]},
        {"kind": "rewrite", "target": "fact:keeper_secret", "text": NEW_SECRET,
         "reason": "幕后准备随当前局势调整", "known_by": ["preacher"]},
    ]


def scripted_world_change(game) -> None:
    game.m.events = [Event(id="office_after_arrival", title="办公室的回应", when="at:preacher:office", where="office",
                           text="布道者抵达办公室后，屋里的人才把百叶窗合上。", read_aloud="办公室的百叶窗被合上了。")]
    SCRIPT.extend([
        *begin(minutes=30),
        tool("world_query", scope=["fact:keeper_secret", "entity:preacher", "agent:preacher"]),
        tool("world_commit", operations=operations(game, at=game.state.minutes + 30)),
        tool("world_query", scope=["fact:keeper_secret", "entity:preacher", "agent:preacher",
                                   "scheduled:preacher_return"]),
        final(BODY),
    ])


def assert_world_change(game) -> None:
    rec = game.world.rec
    assert game.world.place("preacher") == "office"
    assert rec.agents["preacher"].goals == [PRIVATE_GOAL]
    assert any(step.id == "protect_witness" and step.status == "pending" for step in rec.agents["preacher"].plan)
    assert rec.scheduled["preacher_return"].at == game.state.minutes + 30
    assert rec.scheduled["preacher_return"].ops[0]["actor"] == "keeper"
    assert rec.facts["keeper_secret"].text == NEW_SECRET
    assert game.state.fired.count("office_after_arrival") == 1
    assert len([revision for revision in rec.revisions if revision.target == "fact:keeper_secret"]) == 1
    assert rec.facts["keeper_secret"].revealed is None
    assert all(person.id not in rec.facts["keeper_secret"].known_by for person in game.state.investigators)


async def test_one_keeper_queries_changes_backstage_and_uses_the_actual_result_before_telling(fake, monkeypatch):
    game, graph, cast = prepare(fake)
    scripted_world_change(game)
    exposed = []
    original_bind = turn_graph._bind

    def bind(current):
        original_bind(current)
        send = current.emit

        async def emit(event):
            if event["type"] in {"keeper_start", "keeper_delta", "keeper_end", "npc", "aside", "presentation"}:
                assert_world_change(current)
                exposed.append(copy.deepcopy(event))
            await send(event)

        current.emit = emit

    monkeypatch.setattr(turn_graph, "_bind", bind)
    start = game.state.minutes
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert_world_change(game)
        assert game.state.minutes == start + 30 and game.state.turn == 1 and not game.state.checks
        hosted = role_calls("HostedReply")
        assert len(hosted) == 4 and not role_calls("Dialogue")
        final_context = words(hosted[-1]["messages"])
        assert NEW_SECRET in final_context and PRIVATE_GOAL in final_context
        assert '"office"' in final_context and "preacher_return" in final_context
        assert [event["text"] for event in exposed if event["type"] == "keeper_delta"] == [BODY]
        assert NEW_SECRET not in json.dumps(exposed, ensure_ascii=False)
        assert PRIVATE_GOAL not in json.dumps(exposed, ensure_ascii=False)
        assert "办公室的百叶窗被合上了。" not in json.dumps(exposed, ensure_ascii=False)
        remembered = memory(game.state)
        assert '"status": "committed"' in remembered and NEW_SECRET in remembered
        # The tool exchange remains in the same KP history on the next action;
        # no separate director or NPC model reinterprets what was committed.
        SCRIPT.extend([*begin(), final("你把手从册子上收回来。")])
        await run(graph, game, {"input": "我把手收回来。"})
        assert not SCRIPT and NEW_SECRET in words(role_calls("Plan")[-1]["messages"])
        assert len(role_calls("Dialogue")) == 0
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("stage", ["request-saved", "commit-saved", "final-saved"])
async def test_world_tool_save_reload_does_not_repeat_commits_or_model_requests(fake, monkeypatch, stage):
    game, graph, cast = prepare(fake)
    scripted_world_change(game)
    games, casts, saved, shown = [game], [cast], [], []
    original_record = turn_graph.record_event

    def record(current, event):
        shown.append(copy.deepcopy(event))
        original_record(current, event)

    monkeypatch.setattr(turn_graph, "record_event", record)

    def disconnect():
        receipt = game.state.pending_turn.get("keeper", {})
        commits = [row for row in receipt.get("calls", []) if row.get("tool") == "world_commit"]
        committed = bool(game.world.rec.revisions)
        ready = {
            "request-saved": bool(commits) and not committed,
            "commit-saved": bool(commits) and committed and commits[-1].get("done"),
            "final-saved": "text" in receipt and not receipt.get("landed"),
        }[stage]
        if ready and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError(f"世界工具断线：{stage}")

    game.checkpoint = disconnect
    try:
        with pytest.raises(RuntimeError, match=f"世界工具断线：{stage}"):
            await run(graph, game, {"input": ORIGINAL})
        assert len(saved) == 1
        restored = GameState.model_validate_json(saved[0])
        game, graph, cast = prepare(fake, module=game.m.model_copy(deep=True), state=restored)
        games.append(game)
        casts.append(cast)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert_world_change(game)
        assert game.state.turn == 1 and not game.state.checks
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert len(role_calls("HostedReply")) == 4 and not role_calls("Dialogue")
        assert [entry.text for entry in game.state.log if entry.kind == "keeper"] == [BODY]
        assert len([event for event in shown if event["type"] == "keeper_delta"]) == 1
        assert memory(game.state).count('"status": "committed"') == 1
    finally:
        await shut(games, casts)


async def test_lore_result_and_selected_world_schema_are_returned_to_the_same_keeper_history(fake, monkeypatch):
    game, graph, cast = prepare(fake)
    original = "原文条件：只有调查员打开门后的抽屉，才能看见来信。"
    asked = []

    async def lore(state, query, keywords=None, max_tokens=1500):
        if query != "来信的发现条件":
            return ""  # The arbiter's existing optional prefetch is unrelated to this tool request.
        asked.append((query, keywords, max_tokens))
        return original

    monkeypatch.setattr(cast, "lore", lore)
    SCRIPT.extend([
        *begin(),
        tool("world_schema", kinds=["move", "plan", "schedule", "rewrite"]),
        tool("lore", query="来信的发现条件", keywords=["来信", "抽屉"], max_tokens=700),
        final(BODY),
    ])
    try:
        events, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT
        assert asked == [("来信的发现条件", ["来信", "抽屉"], 700)]
        assert len(role_calls("HostedReply")) == 3
        assert original in words(role_calls("HostedReply")[-1]["messages"])
        assert original in memory(game.state)
        assert original not in json.dumps(events, ensure_ascii=False)
        assert not game.state.clues and not game.state.handouts
        assert not game.world.rec.revisions and game.world.place("preacher") == "square"
    finally:
        await shut([game], [cast])


async def test_world_tool_flag_runs_its_existing_event_consequence_without_repeating_the_original_action(fake):
    game, graph, cast = prepare(fake)
    game.m.flags["letter_set_down"] = "信件已经放在桌上"
    game.m.events = [Event(id="reply_knock", title="敲门", where="tower", when="flag:letter_set_down",
                           text="门外的信使听见放信声后敲门。", read_aloud="门外响起两声敲门声。")]
    SCRIPT.extend([
        *begin(),
        tool("world_commit", operations=[{"kind": "flag", "flag": "letter_set_down"}]),
        final("门外传来了敲门声，你把清点册放下。"),
    ])
    try:
        events, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert game.state.fired.count("reply_knock") == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert len(role_calls("HostedReply")) == 2
        assert "门外响起两声敲门声。" in words(role_calls("HostedReply")[-1]["messages"])
        assert game.state.turn == 1 and not game.state.checks
        assert len([event for event in events if event["type"] == "event" and event["id"] == "reply_knock"]) == 1
        assert any("敲门声" in entry.text for entry in game.state.log if entry.kind == "keeper")
    finally:
        await shut([game], [cast])


async def test_only_observed_world_flags_trigger_pictures_without_broadcasting_hidden_flag_meanings(fake, monkeypatch):
    game, graph, cast = prepare(fake)
    hidden = "幕后布道者已经收到密信"
    game.m.flags.update(private_letter=hidden, window_opened="窗帘已经拉开")
    pictures = []

    async def show(trigger, *, audience=None):
        pictures.append((trigger, audience))

    monkeypatch.setattr(game.showcases, "fire", show)
    SCRIPT.extend([
        *begin(),
        tool("world_commit", operations=[
            {"kind": "flag", "flag": "private_letter"},
            {"kind": "flag", "flag": "window_opened", "visibility": "public"},
        ]),
        final("窗帘拉开了一道缝。"),
    ])
    try:
        events, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT
        assert ("flag:window_opened", [game.state.investigator.id]) in pictures
        assert not any(trigger == "flag:private_letter" for trigger, _ in pictures)
        assert hidden not in json.dumps(events, ensure_ascii=False)
        assert not any(event["type"] == "flag" for event in events)
    finally:
        await shut([game], [cast])
