"""Private unfinished intent stays on the host and is consumed only by an adopted plan."""

from __future__ import annotations

import copy
import json

import pytest

from autokeeper import config
from autokeeper.agents.cast import Cast
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net.hub import PRIVATE
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401
from .test_net_private import private_hub as _private_hub

private_hub = _private_hub


def oldnotes(state):
    notes = {
        inv.id: {
            "original": f"{inv.id}：不让同伴知道，我先藏起欠条，再核对私账。",
            "completed": [{"turn": 7, "actual": f"{inv.id}：已把欠条藏进私人信封。"}],
            "remaining": f"{inv.id}：独自核对私账，别让其他人看见。",
        }
        for inv in state.investigators
    }
    state.unfinished = copy.deepcopy(notes)
    state.last_actions = {inv.id: 700 + i for i, inv in enumerate(state.investigators)}
    return notes


def assert_private_absent(payload, notes):
    encoded = json.dumps(payload, ensure_ascii=False)
    assert '"unfinished"' not in encoded and '"last_actions"' not in encoded
    for note in notes.values():
        assert note["original"] not in encoded
        assert note["remaining"] not in encoded
        assert note["completed"][0]["actual"] not in encoded


def test_request_notes_are_excluded_from_real_state_and_welcome_projections(private_hub):
    hub, _ = private_hub
    notes = oldnotes(hub.state)
    last_actions = hub.state.last_actions.copy()
    raw = hub.state.model_dump(mode="json")
    assert {"unfinished", "last_actions"} <= PRIVATE
    assert raw["unfinished"] == notes and raw["last_actions"] == last_actions

    # Include each owner as well as the other seats and an unseated spectator.
    # These are keeper notes, not a client-side automatic action queue.
    for cid in ("a", "b", "c", "watch"):
        view = hub._view(hub._seat_of(cid))
        assert view["location"] == hub.state.location
        assert_private_absent(view, notes)
        welcome = hub._welcome(cid)
        assert welcome["you"] == cid and welcome["started"]
        assert_private_absent(welcome, notes)

    assert hub.state.unfinished == notes and hub.state.last_actions == last_actions


@pytest.mark.parametrize("message_type", ["sync", "turn", "over"])
async def test_state_broadcast_reprojects_raw_host_notes_for_each_seat(private_hub, message_type):
    hub, messages = private_hub
    notes = oldnotes(hub.state)
    raw = hub.state.model_dump(mode="json")
    assert raw["unfinished"]  # a genuinely private host payload enters the production sender

    await hub._broadcast({"t": message_type, "state": raw})

    assert {cid for cid, _ in messages} == {"a", "b", "c", "watch"}
    for cid, payload in messages:
        assert payload["t"] == message_type
        assert payload["state"] == hub._view(hub._seat_of(cid))
        assert_private_absent(payload, notes)
    assert hub.state.unfinished == notes and raw["unfinished"] == notes


def planning_game(fake, active):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    other = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [lead, other], human=[lead.id, other.id])
    state.active, state.location = active, "tower"
    state.pending_turn = {"input": "取消核账，先在这里休息。"}
    cast = Cast(config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}), module)
    game = Game(module, state, cast, Rules(dice=fake))
    return game, oldnotes(state)


@pytest.mark.parametrize("active", [0, 1])
@pytest.mark.parametrize("remaining", ["", "休息以后再决定是否核账。"])
async def test_new_valid_plan_consumes_only_its_actors_oldnote(fake, active, remaining):
    game, notes = planning_game(fake, active)
    try:
        state, text = game.state, game.state.pending_turn["input"]
        actor = state.investigator.id
        last_actions = state.last_actions.copy()
        SCRIPT.append({"action": {"kind": "daily", "summary": f"{state.investigator.name}先原地休息"},
                       "remaining": remaining})

        action = await game.parse(text)

        assert action.kind == "daily" and len(SEEN) == 1 and not SCRIPT
        assert state.unfinished == {who: note for who, note in notes.items() if who != actor}
        assert state.last_actions == last_actions  # planning has not performed another action
        if remaining:
            request = state.pending_turn["request"]
            assert request["actor"] == actor and request["original"] == text
            assert request["remaining"] == remaining and request["completed"] == []
        else:
            assert "request" not in state.pending_turn
    finally:
        await game.close()


@pytest.mark.parametrize("active", [0, 1])
async def test_malformed_plan_falls_back_to_intent_without_consuming_any_oldnote(fake, active):
    game, notes = planning_game(fake, active)
    try:
        state, text = game.state, game.state.pending_turn["input"]
        last_actions = state.last_actions.copy()
        # The actual harness format retry is exhausted before Game uses intent.
        SCRIPT.extend([{"initiative": []}, {"initiative": []},
                       {"kind": "daily", "summary": f"{state.investigator.name}先原地休息"}])

        action = await game.parse(text)

        assert action.kind == "daily" and len(SEEN) == 3 and not SCRIPT
        assert {"plan", "intent"} <= game.cast.sessions.keys()
        assert state.unfinished == notes and state.last_actions == last_actions
        assert "request" not in state.pending_turn
    finally:
        await game.close()
