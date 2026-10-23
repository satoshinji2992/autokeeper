"""Confirmed audit findings, pending fixes; every model and die is local and scripted."""

from __future__ import annotations

import pytest

from autokeeper import paths
from autokeeper.agents.cast import ContinuedTelling
from autokeeper.engine import presentation
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module

from .test_engine import make
from .test_keeper_tools_execution import final, role_calls, shut, table
from .test_request_execution import run
from .test_scene import SCRIPT, sandbox  # noqa: F401


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="Game.remember commits an empty chronicler reply and deletes the public source log")
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


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="The scene fallback takes only settled.text and drops valid private text and media")
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
