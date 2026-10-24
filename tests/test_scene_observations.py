"""Explicit, witnessed scene memories travel back to NPCs without becoming public traces."""

from __future__ import annotations

import pytest

from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context, scene
from autokeeper.world.ops import Move

from .test_engine import make


@pytest.fixture
def game(fake):
    return make(Module.load(EXAMPLE), fake, {}, location="square")[0]


def scene_context(game, *, elapsed=0, ids="keeper:1"):
    return scene.Context(travel=game._travel, elapsed=elapsed, ids=ids)


async def test_a_new_npc_without_an_agent_remembers_the_keepers_performance_only_for_named_witnesses(game):
    text = "站务员亲口告诉调查员：金博尔今早把一封便条留在这里，自己已将它交给调查员。"
    planned = scene.plan(game.world, {
        "people": [{"id": "clerk", "name": "值班站务员", "role": "站务员"}],
        "here": ["preacher", "clerk"],
        "observations": [{"witnesses": ["值班站务员"], "text": text}],
    }, scene_context(game))
    assert not planned.problems and scene.land(game.world, planned.held)
    assert game.world.agent("clerk") is None  # ordinary NPCs still query the witnessed event
    assert text in context.npc_brief(game.world, "clerk", "便条是谁交来的？")
    assert text not in context.npc_brief(game.world, "preacher")  # at the same place is not enough
    event = next(e for e in game.world.rec.events if e.text == text)
    assert event.visibility == "private" and not event.trace and not event.facts and not event.reactive
    assert event.participants == ["keeper"] and event.witnesses == ["clerk"]
    # Arriving later does not acquire an old private observation as a physical trace.
    assert game.world.apply(Move(id="later", entity="warden", to="square")).ok
    assert text not in context.npc_brief(game.world, "warden")
    # A persisted scene unit can be replayed without repeating its event or any NPC memory.
    assert scene.land(game.world, planned.held)
    assert len([e for e in game.world.rec.events if e.text == text]) == 1
    await game.close()


async def test_a_remote_npc_cannot_be_named_as_a_witness_and_a_failed_arrival_grants_no_memory(game):
    text = "调查员答应次日回来报告。"
    assert game.world.apply(Move(id="away", entity="warden", to="office")).ok
    raw = {"observations": [{"witnesses": ["warden"], "text": text}]}
    distant = scene.plan(game.world, raw, scene_context(game))
    assert distant.problems and not scene.land(game.world, distant.held)
    assert text not in context.npc_brief(game.world, "warden")
    # Declaring an arrival does not make an impossible trip witnessed.
    raw["here"] = ["preacher", "warden"]
    too_soon = scene.plan(game.world, raw, scene_context(game))
    assert too_soon.problems and not scene.land(game.world, too_soon.held)
    assert text not in context.npc_brief(game.world, "warden")
    await game.close()


async def test_a_witness_arrives_with_the_memory_atomically_and_a_refused_move_lands_neither(game):
    text = "调查员当面解释：警局已经接手，自己明天再来。"
    assert game.world.apply(Move(id="away", entity="warden", to="office")).ok
    # The claimed travel interval must actually pass after the recorded move;
    # elapsed=30 alone cannot send him back before his latest commitment.
    game.state.minutes += 30
    raw = {"here": ["preacher", "warden"], "observations": [{"witnesses": ["warden"], "text": text}]}
    planned = scene.plan(game.world, raw, scene_context(game, elapsed=30))
    assert not planned.problems and len(planned.held) == 1
    # An intervening world change can make a previously valid movement impossible.
    game.world.rec.entities["warden"].status = "死亡"
    assert not scene.land(game.world, planned.held)
    assert not any(e.text == text for e in game.world.rec.events)
    game.world.rec.entities["warden"].status = ""
    assert scene.land(game.world, planned.held)
    assert game.world.place("warden") == "square" and text in context.npc_brief(game.world, "warden")
    await game.close()


async def test_a_witness_present_at_the_start_remembers_even_after_leaving_the_scene(game):
    text = "布道者在离开前听见调查员答应晚间到报社见面。"
    planned = scene.plan(game.world, {
        "here": [], "left": [{"who": "preacher", "to": "office"}],
        "observations": [{"witnesses": ["preacher"], "text": text}],
    }, scene_context(game, elapsed=30))
    assert not planned.problems and scene.land(game.world, planned.held)
    assert game.world.place("preacher") == "office" and text in context.npc_brief(game.world, "preacher")
    assert text not in context.npc_brief(game.world, "warden")
    await game.close()
