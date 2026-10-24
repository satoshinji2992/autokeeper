"""Tool observations and event consequences retain their own durable boundaries."""

from __future__ import annotations

import copy

import pytest

from autokeeper.engine import graph as turn_graph
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Event
from autokeeper.world import ops as O

from .test_keeper_memory_runtime import memory, words
from .test_keeper_tools_execution import final, role_calls, shut
from .test_keeper_world_tools import BODY, NEW_SECRET, OLD_SECRET, ORIGINAL, begin, prepare, tool
from .test_request_execution import run
from .test_scene import SCRIPT, Writer, sandbox  # noqa: F401

OTHER_CHANGE = "另一个场景已把办公室封锁，布道者现在准备留守教堂。"


async def test_saved_query_observation_is_not_silently_requeried_after_the_world_changes(fake):
    game, graph, cast = prepare(fake)
    games, casts, saved = [game], [cast], []
    SCRIPT.extend([*begin(), tool("world_query", scope=["fact:keeper_secret"]), final(BODY)])

    def disconnect():
        calls = game.state.pending_turn.get("keeper", {}).get("calls", [])
        if any(row.get("tool") == "world_query" and row.get("done") for row in calls) and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError("查询回执已存后断线")

    game.checkpoint = disconnect
    try:
        with pytest.raises(RuntimeError, match="查询回执已存后断线"):
            await run(graph, game, {"input": ORIGINAL})
        restored = GameState.model_validate_json(saved[0])
        game, graph, cast = prepare(fake, module=game.m.model_copy(deep=True), state=restored)
        games.append(game)
        casts.append(cast)
        assert game.world.apply(O.Rewrite(target="fact:keeper_secret", text=OTHER_CHANGE,
                                          reason="其他场景已经采取行动", actor="engine")).ok
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert len(role_calls("HostedReply")) == 2
        current = words(role_calls("HostedReply")[-1]["messages"])
        # The saved observation still means what the KP actually read then;
        # current private context separately supplies the later world state.
        assert OLD_SECRET in current and OTHER_CHANGE in current
        assert OLD_SECRET in memory(game.state)
        assert game.world.rec.facts["keeper_secret"].text == OTHER_CHANGE
        assert len(game.world.rec.revisions) == 1
    finally:
        await shut(games, casts)


async def test_a_lore_result_survives_a_later_model_failure_without_retrieving_it_again(fake, monkeypatch):
    game, graph, cast = prepare(fake)
    games, casts, retrieved = [game], [cast], []
    source = "原文里的墨迹需要在侧光下才能看清；普通寒暄不会发现。"

    async def lore(state, query, keywords=None, max_tokens=1500):
        if query != "墨迹的发现条件":
            return ""
        retrieved.append(query)
        return source

    monkeypatch.setattr(cast, "lore", lore)
    SCRIPT.extend([*begin(), tool("lore", query="墨迹的发现条件"), RuntimeError("后续主持模型离线"), final(BODY)])
    try:
        with pytest.raises(RuntimeError, match="后续主持模型离线"):
            await run(graph, game, {"input": ORIGINAL})
        assert retrieved == ["墨迹的发现条件"]
        restored = GameState.model_validate_json(game.state.model_dump_json())
        game, graph, cast = prepare(fake, module=game.m.model_copy(deep=True), state=restored)
        games.append(game)
        casts.append(cast)

        async def unexpected(*args, **kwargs):
            raise AssertionError("a saved lore observation was retrieved a second time")

        monkeypatch.setattr(cast, "lore", unexpected)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert source in words(role_calls("HostedReply")[-1]["messages"])
        assert memory(game.state).count(source) == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert retrieved == ["墨迹的发现条件"]
    finally:
        await shut(games, casts)


async def test_a_world_change_during_the_keeper_call_rejects_only_the_stale_commit(fake, monkeypatch):
    game, graph, cast = prepare(fake)
    SCRIPT.extend([
        *begin(),
        tool("world_query", scope=["fact:keeper_secret"]),
        tool("world_commit", operations=[{"kind": "rewrite", "target": "fact:keeper_secret", "text": NEW_SECRET,
                                           "reason": "准备安排新的幕后行动"}]),
        final("老人仍等着你，办公室的情况需要重新核实。"),
    ])
    original_call = Writer._call
    hosted = 0

    async def call(self, messages, *, schema, images, on_delta):
        nonlocal hosted
        if schema and schema.get("title") == "HostedReply":
            hosted += 1
        answer = await original_call(self, messages, schema=schema, images=images, on_delta=on_delta)
        if schema and schema.get("title") == "HostedReply" and hosted == 2:
            # Another scene commits after this KP started deciding, before its
            # returned command may land. It is not a replay or malformed reply.
            assert game.world.apply(O.Rewrite(target="fact:keeper_secret", text=OTHER_CHANGE,
                                              reason="另一场景已经封锁办公室", actor="engine")).ok
        return answer

    monkeypatch.setattr(Writer, "_call", call)
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert game.world.rec.facts["keeper_secret"].text == OTHER_CHANGE
        assert len(game.world.rec.revisions) == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert len(role_calls("HostedReply")) == 3
        result_context = words(role_calls("HostedReply")[-1]["messages"])
        assert '"stale"' in result_context and OTHER_CHANGE in result_context
        assert not game.state.checks and game.state.turn == 1
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("cut_stage", ["event-settled", "final-saved"])
async def test_flag_tool_consequences_keep_their_sanity_roll_and_final_telling_after_reload(fake, monkeypatch, cut_stage):
    game, graph, cast = prepare(fake)
    games, casts, saved, shown = [game], [cast], [], []
    game.m.flags["seal_opened"] = "封印已经打开"
    game.m.events = [Event(id="inside_seal", title="封印中的东西", when="flag:seal_opened", where="tower",
                           text="封印打开后才看见箱中的眼睛。", read_aloud="箱中有一只睁开的眼睛。", sanity="0/1D3")]
    SCRIPT.extend([
        *begin(),
        tool("world_commit", operations=[{"kind": "flag", "flag": "seal_opened"}]),
        final("箱中的眼睛仍睁着，你刚才所见并未消失。"),
    ])
    fake.queue(99).queue_totals(2)
    sanity = game.state.investigator.san
    original_record = turn_graph.record_event

    def record(current, event):
        shown.append(copy.deepcopy(event))
        original_record(current, event)

    monkeypatch.setattr(turn_graph, "record_event", record)

    def disconnect():
        keeper = game.state.pending_turn.get("keeper", {})
        ready = {
            "event-settled": "inside_seal" in game.state.fired and game.state.investigator.san == sanity - 2,
            "final-saved": "text" in keeper and not keeper.get("landed"),
        }[cut_stage]
        if ready and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError(f"封印后果断线：{cut_stage}")

    game.checkpoint = disconnect
    try:
        with pytest.raises(RuntimeError, match=f"封印后果断线：{cut_stage}"):
            await run(graph, game, {"input": ORIGINAL})
        restored = GameState.model_validate_json(saved[0])
        game, graph, cast = prepare(fake, module=game.m.model_copy(deep=True), state=restored)
        games.append(game)
        casts.append(cast)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert game.state.investigator.san == sanity - 2
        assert game.state.fired.count("inside_seal") == 1
        assert not fake.values and not fake.totals
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert len(role_calls("HostedReply")) == 2
        assert "箱中有一只睁开的眼睛。" in words(role_calls("HostedReply")[-1]["messages"])
        assert game.state.turn == 1
        assert len([event for event in shown if event["type"] == "sanity"]) == 1
        assert len([entry for entry in game.state.log if entry.kind == "keeper"]) == 1
    finally:
        await shut(games, casts)


@pytest.mark.parametrize("restore", [False, True], ids=["live", "reload-preview"])
async def test_preview_then_commit_uses_the_live_input_versions_without_landing_the_preview(fake, monkeypatch, restore):
    game, graph, cast = prepare(fake)
    games, casts, saved = [game], [cast], []
    moves = [{"kind": "move", "entity": "preacher", "to": "office", "visibility": "private"}]
    SCRIPT.extend([*begin(minutes=30), tool("world_preview", operations=moves),
                   tool("world_commit", operations=moves), final(BODY)])
    original_call = Writer._call
    saw_preview = []

    async def call(self, messages, *, schema, images, on_delta):
        if schema and schema.get("title") == "HostedReply" and len(role_calls("HostedReply")) == 1:
            # This is the commit's model call: its preceding preview has reported
            # the predicted destination but has not moved the real person.
            assert game.world.place("preacher") == "square"
            assert not [event for event in game.world.rec.events if event.kind == "move"]
            saw_preview.append(True)
        return await original_call(self, messages, schema=schema, images=images, on_delta=on_delta)

    monkeypatch.setattr(Writer, "_call", call)

    def disconnect():
        calls = game.state.pending_turn.get("keeper", {}).get("calls", [])
        if any(row.get("tool") == "world_preview" and row.get("done") for row in calls) and not saved:
            assert game.world.place("preacher") == "square"
            saved.append(game.state.model_dump_json())
            raise RuntimeError("预演已存，尚未提交")

    if restore:
        game.checkpoint = disconnect
    try:
        if restore:
            with pytest.raises(RuntimeError, match="预演已存，尚未提交"):
                await run(graph, game, {"input": ORIGINAL})
            restored = GameState.model_validate_json(saved[0])
            game, graph, cast = prepare(fake, module=game.m.model_copy(deep=True), state=restored)
            games.append(game)
            casts.append(cast)
            _, offers = await run(graph, game, {"input": "/retry"})
        else:
            _, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert saw_preview == [True]
        assert game.world.place("preacher") == "office"
        assert len([event for event in game.world.rec.events if event.kind == "move"]) == 1
        assert len(role_calls("HostedReply")) == 3
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert memory(game.state).count('"status": "preview"') == 1
        assert memory(game.state).count('"status": "committed"') == 1
    finally:
        await shut(games, casts)


@pytest.mark.parametrize("cut_stage", [None, "event-settled", "corrected-final"],
                         ids=["live", "reload-event", "reload-correction"])
async def test_committed_tool_flag_consequences_survive_a_stale_final_scene_without_another_telling(fake, monkeypatch, cut_stage):
    game, graph, cast = prepare(fake)
    games, casts, saved, shown = [game], [cast], [], []
    parcel = game.world.apply(O.Bring(name="封签纸包", holder=game.state.investigator.id, actor="engine")).data
    game.m.flags["bell_signaled"] = "约定的铃声已经发出"
    game.m.events = [Event(id="answering_knock", title="楼下的回应", when="flag:bell_signaled", where="tower",
                           text="听见约定的铃声，楼下的人敲了敲扶手。", read_aloud="楼下传来三声敲击。")]
    draft = "你把封签纸包放在桌上，约定的铃声响后，楼下传来三声敲击。"
    corrected = "封签纸包已经由老人接住；楼下传来三声敲击，回应刚才约定的铃声。"
    SCRIPT.extend([
        *begin(),
        tool("world_commit", operations=[{"kind": "flag", "flag": "bell_signaled"}]),
        final(draft, scene={"holds": [{"item": parcel, "holder": "tower"}]}),
        {"scene": {}, "text": corrected},  # A correction cannot request another tool or repeat the action.
    ])
    original_record = turn_graph.record_event
    changed = False

    def record(current, event):
        shown.append(copy.deepcopy(event))
        original_record(current, event)

    monkeypatch.setattr(turn_graph, "record_event", record)

    def checkpoint():
        nonlocal changed
        keeper = game.state.pending_turn.get("keeper", {})
        if "text" in keeper and not keeper.get("landed") and not changed:
            changed = True
            # A valid intervening transfer invalidates only the saved final scene;
            # the earlier committed bell signal is already a real world fact.
            assert game.world.apply(O.Transfer(id="other-scene-catches-parcel", item=parcel,
                                               giver=game.state.investigator.id, to="warden", actor="engine")).ok
        correction = keeper.get("following_keeper", {})
        ready = {
            "event-settled": "answering_knock" in game.state.fired,
            "corrected-final": "text" in correction and not correction.get("landed"),
        }.get(cut_stage, False)
        if ready and not saved:
            if cut_stage == "corrected-final":
                assert correction["text"] == corrected
            saved.append(game.state.model_dump_json())
            raise RuntimeError(f"纠正与后果断线：{cut_stage}")

    game.checkpoint = checkpoint
    try:
        if cut_stage:
            with pytest.raises(RuntimeError, match=f"纠正与后果断线：{cut_stage}"):
                await run(graph, game, {"input": ORIGINAL})
            restored = GameState.model_validate_json(saved[0])
            game, graph, cast = prepare(fake, module=game.m.model_copy(deep=True), state=restored)
            games.append(game)
            casts.append(cast)
            game.checkpoint = checkpoint  # An early event cut still needs the later intervening transfer.
            _, offers = await run(graph, game, {"input": "/retry"})
        else:
            _, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not SCRIPT and not game.state.pending_turn and changed
        assert game.world.entity(parcel).at == "warden"
        assert "bell_signaled" in game.state.flags and game.state.fired.count("answering_knock") == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert len(role_calls("HostedReply")) == 2 and len(role_calls("Telling")) == 1
        bodies = [entry for entry in game.state.log if entry.kind == "keeper"]
        assert [entry.text for entry in bodies] == [corrected]
        assert [event["text"] for event in shown if event["type"] == "keeper_delta"] == [corrected]
        assert len([event for event in shown if event["type"] == "event" and event["id"] == "answering_knock"]) == 1
        assert memory(game.state).count('"status": "committed"') == 1
        assert not game.state.checks and game.state.turn == 1
    finally:
        await shut(games, casts)


async def test_a_preview_keeps_its_input_versions_when_another_scene_changes_the_fact_before_commit(fake):
    game, graph, cast = prepare(fake)
    changes = [{"kind": "rewrite", "target": "fact:keeper_secret", "text": NEW_SECRET,
                "reason": "预演后准备采用新的幕后安排"}]
    SCRIPT.extend([
        *begin(), tool("world_preview", operations=changes), tool("world_commit", operations=changes),
        final("办公室的情况已经改变，原来的安排没有落实。"),
    ])
    changed = False

    def after_preview():
        nonlocal changed
        calls = game.state.pending_turn.get("keeper", {}).get("calls", [])
        if not changed and any(row.get("tool") == "world_preview" and row.get("done") for row in calls):
            changed = True
            # The preview reads V1 and predicts V2 on its detached copy. Another
            # scene now really creates V2: preview post_reads must not authorize
            # overwriting it, nor may a fresh snapshot erase the original V1 read.
            assert game.world.apply(O.Rewrite(target="fact:keeper_secret", text=OTHER_CHANGE,
                                              reason="另一场景在预演后采取了行动", actor="engine")).ok

    game.checkpoint = after_preview
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        assert changed and not offers and not SCRIPT and not game.state.pending_turn
        assert game.world.rec.facts["keeper_secret"].text == OTHER_CHANGE
        assert len(game.world.rec.revisions) == 1
        assert len(role_calls("HostedReply")) == 3
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert '"stale"' in words(role_calls("HostedReply")[-1]["messages"])
        assert OTHER_CHANGE in words(role_calls("HostedReply")[-1]["messages"])
        assert memory(game.state).count('"status": "preview"') == 1
        assert '"status": "committed"' not in memory(game.state)
    finally:
        await shut([game], [cast])
