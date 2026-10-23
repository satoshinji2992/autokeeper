"""A scene's saved body, live events and retelling keep the actual readers."""

from __future__ import annotations

import pytest

from autokeeper.engine import audience, progress
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.state import GameState
from autokeeper.tui.screens.net import history_text

from .test_keeper_tools_execution import final, shut, table
from .test_net_private import private_hub as _private_hub
from .test_scene import SCRIPT, SEEN, sandbox  # noqa: F401

private_hub = _private_hub
BODY = "桌沿的灰里有一道刚留下的手印，窗外的钟声仍在响。"
CONFIG = {"configurable": {"thread_id": "multiplayer-visibility"}}


def split(game):
    game.state.apart["chen"] = "square"
    game.world.rec.entities["chen"].at = "square"


async def play(graph, game, hub, request):
    async for mode, event in turn_graph.stream(graph, game, request, CONFIG, stream_mode=["custom", "updates"]):
        if mode == "custom":
            await hub._event(event)


@pytest.mark.parametrize("restore", [False, True])
async def test_live_saved_and_reconnected_scene_body_does_not_reach_remote_seat(fake, private_hub, restore):
    hub, messages = private_hub
    old = hub.game
    game, graph, cast = table(fake, party=True)
    games, casts = [old, game], [cast]
    split(game)
    SCRIPT.extend([{"action": {"kind": "use", "summary": "周衡检查桌沿"}},
                   {"needs_roll": False, "minutes": 1}, final(BODY)])
    saved = []

    def checkpoint():
        receipt = game.state.pending_turn.get("keeper", {})
        if restore and receipt.get("landed") and not saved:
            saved.append(game.state.model_dump_json())
            raise ConnectionError("正文尚未交付")

    def attach():
        hub.game, hub.cast, hub.m, hub.state = game, cast, game.m, game.state

    attach()
    game.checkpoint = checkpoint
    try:
        if restore:
            with pytest.raises(ConnectionError, match="正文尚未交付"):
                await play(graph, game, hub, {"input": "我检查桌沿。"})
            game, graph, cast = table(fake, module=game.m.model_copy(deep=True),
                                       state=GameState.model_validate_json(saved[0]))
            games.append(game)
            casts.append(cast)
            attach()
            await play(graph, game, hub, {"input": "/retry"})
        else:
            await play(graph, game, hub, {"input": "我检查桌沿。"})
        body = next(entry for entry in game.state.log if entry.text == BODY)
        assert body.audience == ["zhouheng", "lin"] and body.delivery
        shown = [(cid, msg["ev"]) for cid, msg in messages
                 if msg["t"] == "event" and msg["ev"]["type"].startswith("keeper_")]
        assert {cid for cid, _ in shown} == {"a", "b"}
        assert all(event["audience"] == body.audience and event["delivery"] == body.delivery for _, event in shown)
        assert BODY in str(hub._welcome("a")["log"])
        assert BODY not in str(hub._welcome("c")["log"])
        assert BODY not in str(hub._welcome("watch")["log"])
        assert BODY in str(audience.log(game.state, "lin"))
        assert BODY not in str(audience.log(game.state))  # not input to the common recap
        assert "私密" not in history_text(body).plain  # ordinary scene prose retains its presentation
    finally:
        await shut(games, casts)


async def test_retelling_keeps_original_observers_even_after_location_record_changes(fake, private_hub):
    hub, messages = private_hub
    old = hub.game
    game, graph, cast = table(fake, party=True)
    split(game)
    SCRIPT.extend([{"action": {"kind": "use", "summary": "周衡检查桌沿"}},
                   {"needs_roll": False, "minutes": 1}, final(BODY)])
    hub.game, hub.cast, hub.m, hub.state = game, cast, game.m, game.state
    try:
        await play(graph, game, hub, {"input": "我检查桌沿。"})
        entry = game.told.entry
        messages.clear()
        # A later location record must not change who this original observation belonged to.
        game.state.apart = {"lin": "square"}
        game.world.rec.entities["lin"].at = "square"
        game.world.rec.entities["chen"].at = "tower"
        SCRIPT.append("桌边的手印很新，远处钟声还没有停。")
        game.emit = hub._event
        await game.retell()
        assert {cid for cid, msg in messages if msg["t"] == "event" and msg["ev"]["type"].startswith("keeper_")} == {"a", "b"}
        assert entry.audience == ["zhouheng", "lin"]
        assert all(msg["ev"]["audience"] == entry.audience for _, msg in messages if msg["t"] == "event"
                   and msg["ev"]["type"].startswith("keeper_"))
    finally:
        await shut([old, game], [cast])


async def test_common_progress_does_not_name_the_last_actors_private_location(fake):
    game, _, cast = table(fake, party=True)
    split(game)
    game.m.location("tower").name = "PRIVATE_LOCATION_MARKER"
    game.state.log.clear()
    game.state.add("keeper", "PRIVATE_RESULT_MARKER", audience=["zhouheng", "lin"])
    try:
        material = progress.material(game.m, game.state)
        assert "PRIVATE_LOCATION_MARKER" not in material and "PRIVATE_RESULT_MARKER" not in material
        assert "分头行动" in material
    finally:
        await shut([game], [cast])


async def test_completed_scene_recount_never_executes_the_current_requests_continuation(fake):
    game, _, cast = table(fake, party=True)
    game.state.ongoing, game.state.sky = "异地的谈话尚未结束", "rain"
    request = {"actor": game.state.investigator.id, "original": "前往教堂问候", "remaining": "前往教堂", "completed": []}
    game.state.pending_turn = {"input": "前往教堂问候", "request": request.copy()}
    before = game.world.rec.seq
    SCRIPT.append("先前那场谈话结束时，天色已经放晴。")
    events = []

    async def emit(event):
        events.append(event)

    game.emit = emit
    try:
        await game._narrate("仅补叙前一处已经结束的交谈", _receipt={"audience": ["lin"]}, _settled_only=True)
        assert len(SEEN) == 1 and SEEN[0]["schema"] is None
        assert game.state.pending_turn["request"] == request
        assert game.world.rec.seq == before and game.state.ongoing == "异地的谈话尚未结束"
        assert game.state.sky == "rain" and not game.state.perceptions
        assert all(event["audience"] == ["lin"] for event in events if event["type"].startswith("keeper_"))
    finally:
        await shut([game], [cast])
