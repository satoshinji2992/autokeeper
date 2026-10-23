"""Accepted typed tellings land before delivery and resume each acknowledged phase once."""

from __future__ import annotations

import copy

import pytest

from autokeeper.engine import audience
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Event
from autokeeper.world import ops as O

from .test_keeper_memory_runtime import FIRST, memory, words
from .test_keeper_tools_execution import final, role_calls, shut, table, voice
from .test_request_execution import run
from .test_scene import SCRIPT, SEEN, sandbox  # noqa: F401

BODY = "你把封签纸包搁在桌上，纸下扬起一层灰。"
TRACE = "桌面上有一片新擦开的灰痕"
ASIDE = "你听见木箱里轻轻响了一下。"
PHASES = ("final-saved", "scene-landed", "keeper-start", "keeper-delta", "keeper-end",
          "aside-delivered", "aside-delivery-failed", "media-delivered", "media-delivery-failed")


def prepared(fake):
    game, graph, cast = table(fake, party=True)
    parcel = game.world.apply(O.Bring(name="封签纸包", holder=game.state.investigator.id, actor="engine")).data
    told = final(BODY, scene={"holds": [{"item": parcel, "holder": "tower"}],
                             "traces": [{"text": TRACE, "visibility": "public"}]})
    told["telling"].update(asides=[{"to": ["lin"], "text": ASIDE}],
                           media=[{"ref": "handout:h_diary", "to": ["lin"]}])
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡小心地把封签纸包放到桌上", "target": "封签纸包"}},
        {"needs_roll": True, "skill": "敏捷", "minutes": 3, "result_scope": "scene"},
        voice("warden", "看见他放下纸包，拿一块擦手布给他。"),
        {"text": FIRST, "items": [{"name": "擦手布", "to": game.state.investigator.id}]},
        told,
    ])
    fake.queue(1)
    return game, graph, cast, parcel


def assert_scene_landed(game, parcel):
    assert game.world.entity(parcel).at == "tower"
    assert len([event for event in game.world.rec.events if event.trace == TRACE]) == 1
    assert game.state.investigator.inventory.count("擦手布") == 1


@pytest.mark.parametrize("phase", PHASES)
async def test_saved_telling_and_scene_resume_delivery_without_repeating_models_or_world(fake, monkeypatch, phase):
    game, graph, cast, parcel = prepared(fake)
    games, casts, saved, delivered = [game], [cast], [], []
    start = game.state.minutes
    original_bind = turn_graph._bind

    def cut():
        if not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError(f"交付断线：{phase}")

    def bind(current):
        original_bind(current)
        send = current.emit

        async def emit(event):
            if event["type"] in {"keeper_start", "keeper_delta", "keeper_end", "aside", "presentation"}:
                assert current.state.pending_turn["keeper"]["text"] == BODY
                assert current.state.pending_turn["keeper"]["landed"]
                assert_scene_landed(current, parcel)
            if ((phase == "aside-delivery-failed" and event["type"] == "aside")
                    or (phase == "media-delivery-failed" and event["type"] == "presentation")) and not saved:
                cut()  # The transport did not accept this item; its earlier acknowledged items still hold.
            await send(event)
            delivered.append(copy.deepcopy(event))

        current.emit = emit

    monkeypatch.setattr(turn_graph, "_bind", bind)

    def checkpoint():
        receipt = game.state.pending_turn.get("keeper", {})
        ready = {
            "final-saved": "text" in receipt and not receipt.get("landed"),
            "scene-landed": receipt.get("landed") and not receipt.get("body_started"),
            "keeper-start": receipt.get("body_started") and not receipt.get("body_shown"),
            "keeper-delta": receipt.get("body_shown") and not receipt.get("body_ended"),
            "keeper-end": receipt.get("body_ended") and not receipt.get("asides_shown"),
            "aside-delivered": 0 in receipt.get("asides_shown", []) and not receipt.get("media_shown"),
            "media-delivered": 0 in receipt.get("media_shown", []),
        }.get(phase, False)
        if ready and not saved:
            if phase == "final-saved":
                assert game.world.entity(parcel).at == game.state.investigator.id
                assert not [event for event in game.world.rec.events if event.trace == TRACE]
            else:
                assert_scene_landed(game, parcel)
            if phase in {"final-saved", "scene-landed"}:
                assert not [entry for entry in game.state.log if entry.kind == "keeper"]
            cut()

    game.checkpoint = checkpoint
    try:
        with pytest.raises(RuntimeError, match=f"交付断线：{phase}"):
            await run(graph, game, {"input": "我小心地把纸包放到桌上。"})
        assert len(saved) == 1 and not SCRIPT
        assert len(role_calls("HostedReply")) == 2 and len(role_calls("Dialogue")) == 1
        restored = GameState.model_validate_json(saved[0])
        game, graph, cast = table(fake, module=game.m.model_copy(deep=True), state=restored)
        games.append(game)
        casts.append(cast)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not game.state.pending_turn and not SCRIPT
        assert_scene_landed(game, parcel)
        assert game.state.turn == 1 and game.state.minutes == start + 3
        assert len(game.state.checks) == 1 and not fake.values
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("Dialogue")) == 1
        assert len(role_calls("HostedReply")) == 2 and memory(game.state).count(FIRST) == 1
        assert len([event for event in delivered if event["type"] == "npc" and event["text"] == FIRST]) == 1
        for kind in ("keeper_start", "keeper_delta", "keeper_end", "aside", "presentation"):
            assert len([event for event in delivered if event["type"] == kind]) == 1
        assert [entry.text for entry in game.state.log if entry.kind == "keeper" and entry.audience is None] == [BODY]
        assert [entry.text for entry in game.state.log if entry.kind == "keeper" and entry.audience == ["lin"]] == [ASIDE]
        assert len(game.state.presentations) == 1 and game.state.presentations[0]["audience"] == ["lin"]
        assert len([deed for deed in game.state.deeds if deed.kind == "handout"]) == 1
        assert not game.state.clues and not game.state.handouts
        assert ASIDE in "\n".join(entry.text for entry in audience.log(game.state, "lin"))
        assert ASIDE not in "\n".join(entry.text for entry in audience.log(game.state, "chen"))
    finally:
        await shut(games, casts)


async def test_an_ordinary_plain_keeper_answer_still_streams_before_the_model_reply_finishes(fake, monkeypatch):
    game, graph, cast = table(fake)
    text = "现在仍在钟楼。守钟人坐在原处，你们还没有开始新的行动。"
    SCRIPT.append(text)
    original_bind = turn_graph._bind
    pieces = []

    def bind(current):
        original_bind(current)
        send = current.emit

        async def emit(event):
            if event["type"] == "keeper_delta":
                assert "text" not in current.state.pending_turn.get("keeper", {})
                assert text not in words(current.state.transcripts.get("keeper", []))
                pieces.append(event["text"])
            await send(event)

        current.emit = emit

    monkeypatch.setattr(turn_graph, "_bind", bind)
    start = game.state.minutes
    try:
        _, offers = await run(graph, game, {"input": "/kp 现在大家还在这里吗？"})
        assert not offers and not SCRIPT and "".join(pieces) == text and len(pieces) > 1
        assert len(SEEN) == 1 and SEEN[0]["schema"] is None
        assert not game.state.checks and game.state.minutes == start
        assert [entry.text for entry in game.state.log if entry.kind == "keeper"] == [text]
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("partial", [False, True], ids=["nothing-landed", "partial-landed"])
@pytest.mark.parametrize("restore", [False, True], ids=["live", "reload-corrected-final"])
async def test_a_scene_that_became_stale_is_retold_from_actual_results_before_its_old_success_is_visible(
        fake, monkeypatch, partial, restore):
    game, graph, cast, parcel = prepared(fake)
    games, casts, saved, emitted = [game], [cast], [], []
    draft = SCRIPT[-1]["telling"]
    draft.update(asides=[], media=[])
    if not partial:
        draft["scene"].pop("traces")
    corrected = "守钟人已接过纸包，桌上的灰痕仍留着。" if partial else "纸包已由守钟人接住，并没有放到桌上。"
    SCRIPT.append({"scene": {}, "text": corrected})  # Correction can narrate the settled scene, not request new tools.
    changed = False
    start = game.state.minutes
    original_record = turn_graph.record_event

    def record(current, event):
        emitted.append(copy.deepcopy(event))
        original_record(current, event)

    monkeypatch.setattr(turn_graph, "record_event", record)

    def change_before_landing():
        nonlocal changed
        receipt = game.state.pending_turn.get("keeper", {})
        if "text" in receipt and not receipt.get("landed") and not changed:
            changed = True
            # A genuine intervening operation invalidates the saved Transfer's original giver.
            assert game.world.apply(O.Transfer(id="delivery-intervening-transfer", item=parcel,
                                               giver=game.state.investigator.id, to="warden", actor="engine",
                                               text="守钟人已经接过纸包。")).ok
        corrected_receipt = receipt.get("following_keeper", {})
        if restore and receipt.get("stale") and "text" in corrected_receipt and not corrected_receipt.get("landed") and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError("纠正终稿已存，尚未交付")

    game.checkpoint = change_before_landing
    try:
        if restore:
            with pytest.raises(RuntimeError, match="纠正终稿已存，尚未交付"):
                await run(graph, game, {"input": "我小心地把纸包放到桌上。"})
            assert len(saved) == 1 and not SCRIPT
            restored = GameState.model_validate_json(saved[0])
            game, graph, cast = table(fake, module=game.m.model_copy(deep=True), state=restored)
            games.append(game)
            casts.append(cast)
            _, offers = await run(graph, game, {"input": "/retry"})
        else:
            _, offers = await run(graph, game, {"input": "我小心地把纸包放到桌上。"})
        assert not offers and not SCRIPT and changed
        assert game.world.entity(parcel).at == "warden"
        assert len([event for event in game.world.rec.events if event.trace == TRACE]) == int(partial)
        assert len([event for event in game.world.rec.events if event.kind == "transfer" and parcel in event.participants]) == 1
        assert [entry.text for entry in game.state.log if entry.kind == "keeper" and entry.audience is None] == [corrected]
        assert "".join(event["text"] for event in emitted if event["type"] == "keeper_delta") == corrected
        assert BODY not in "\n".join(entry.text for entry in game.state.log)
        assert len(role_calls("HostedReply")) == 2 and len(role_calls("Telling")) == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("Dialogue")) == 1
        assert game.state.investigator.inventory.count("擦手布") == 1
        assert len(game.state.checks) == 1 and not fake.values and game.state.minutes == start + 3
        assert game.state.turn == 1 and len([entry for entry in game.state.log if entry.kind == "player"]) == 1
        assert len([event for event in emitted if event["type"] == "npc" and event["text"] == FIRST]) == 1
        assert FIRST in words(role_calls("Telling")[-1]["messages"])
    finally:
        await shut(games, casts)


@pytest.mark.parametrize("cut_stage", [None, "visit-notice", "sanity-notice", "walked-saved"],
                         ids=["live", "reload-visit-notice", "reload-sanity-notice", "reload-walked"])
async def test_an_actual_partial_walk_keeps_its_visit_event_sanity_and_notices_once_after_reload(fake, monkeypatch, cut_stage):
    game, graph, cast = table(fake)
    games, casts, saved, delivered = [game], [cast], [], []
    parcel = game.world.apply(O.Bring(name="封签纸包", holder="warden", actor="engine")).data
    await game._arrive("square", Turn(), party=True)
    game.m.flags["arrival_heard"] = "实际进入钟楼，听到了刮擦声"
    visit_text = "刚踏入钟楼，齿轮里响起刺耳的刮擦声。"
    game.m.events.append(Event(id="actual_arrival", title="进门的刮擦声", when="visit:tower", where="tower",
                               text="实际到访后才听见。", read_aloud=visit_text,
                               sanity="0/1D3", sets=["arrival_heard"]))
    draft_text = "你走进钟楼，守钟人把封签纸包放到了桌上。"
    corrected = "你已进入钟楼，纸包仍在广场布道者手里；齿轮的刮擦声让你心头一紧。"
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡走进钟楼，查看守钟人放下的纸包"}},
        {"needs_roll": True, "skill": "敏捷", "minutes": 3},
        voice("preacher", "看见他整理工具，拿一块擦手布给他。"),
        {"text": FIRST, "items": [{"name": "擦手布", "to": game.state.investigator.id}]},
        final(draft_text, scene={"room": "tower", "holds": [{"item": parcel, "holder": "tower"}]}),
        {"scene": {}, "text": corrected},
    ])
    fake.queue(1, 99).queue_totals(2)  # One primary check and one failed SAN, with a real scripted 1D3 loss.
    start, sanity = game.state.minutes, game.state.investigator.san
    changed = False
    original_bind = turn_graph._bind

    def disconnect(current):
        if not saved:
            parent = current.state.pending_turn["keeper"]
            assert parent["stale"] and parent["room"] == "tower" and current.state.location == "tower"
            assert parent["walk_journal"]["turn"]["events"][0]["id"] == "actual_arrival"
            saved.append(current.state.model_dump_json())
            raise RuntimeError(f"到访断线：{cut_stage}")

    def bind(current):
        original_bind(current)
        send = current.emit

        async def emit(event):
            cutting = ((cut_stage == "visit-notice" and event["type"] == "event" and event.get("id") == "actual_arrival")
                       or (cut_stage == "sanity-notice" and event["type"] == "sanity"))
            if cutting and not saved:
                disconnect(current)  # This notification has not been accepted by its transport yet.
            await send(event)
            delivered.append(copy.deepcopy(event))

        current.emit = emit

    monkeypatch.setattr(turn_graph, "_bind", bind)

    def checkpoint():
        nonlocal changed
        parent = game.state.pending_turn.get("keeper", {})
        if "text" in parent and not parent.get("landed") and not changed:
            changed = True
            assert game.world.apply(O.Transfer(id="partial-walk-parcel", item=parcel, giver="warden",
                                               to="preacher", actor="engine", text="布道者已经接过纸包。")).ok
        if cut_stage == "walked-saved" and parent.get("walked") and not saved:
            disconnect(game)

    game.checkpoint = checkpoint
    try:
        if cut_stage:
            with pytest.raises(RuntimeError, match=f"到访断线：{cut_stage}"):
                await run(graph, game, {"input": "我走进钟楼，看看守钟人放下的纸包。"})
            assert len(saved) == 1
            restored = GameState.model_validate_json(saved[0])
            game, graph, cast = table(fake, module=game.m.model_copy(deep=True), state=restored)
            games.append(game)
            casts.append(cast)
            _, offers = await run(graph, game, {"input": "/retry"})
        else:
            _, offers = await run(graph, game, {"input": "我走进钟楼，看看守钟人放下的纸包。"})
        assert not offers and not SCRIPT and changed
        assert game.state.location == "tower" and game.state.visited.count("tower") == 1
        assert game.state.fired.count("actual_arrival") == 1 and "arrival_heard" in game.state.flags
        assert game.state.investigator.san == sanity - 2 and not fake.values and not fake.totals
        assert game.world.entity(parcel).at == "preacher" and game.state.investigator.inventory.count("擦手布") == 1
        assert game.state.turn == 1 and game.state.minutes == start + 3 and len(game.state.checks) == 1
        assert len([entry for entry in game.state.log if entry.kind == "player"]) == 1
        san_entry = next(entry for entry in game.state.log if entry.kind == "sanity")
        assert len([entry for entry in game.state.log if entry.kind == "sanity"]) == 1
        assert len([event for event in delivered if event["type"] == "event" and event.get("id") == "actual_arrival"]) == 1
        assert len([event for event in delivered if event["type"] == "sanity"]) == 1
        assert len([event for event in delivered if event["type"] == "npc" and event["text"] == FIRST]) == 1
        assert [entry.text for entry in game.state.log if entry.kind == "keeper"] == [corrected]
        assert draft_text not in "\n".join(entry.text for entry in game.state.log)
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("Dialogue")) == 1
        assert len(role_calls("HostedReply")) == 2 and len(role_calls("Telling")) == 1
        correction_input = role_calls("Telling")[0]["messages"][-1]["content"]
        assert visit_text in correction_input and san_entry.text in correction_input
        assert "原文已展示给玩家，不要复述" in correction_input
        assert sum(event.get("text", "").count(visit_text) for event in delivered) == 1
    finally:
        await shut(games, casts)
