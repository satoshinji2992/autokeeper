"""A delivered introduction is personal knowledge, independent of cast registration."""

from __future__ import annotations

import pytest

from autokeeper.engine.state import GameState
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops, scene

from .test_scene import SCRIPT, SEEN, Writer, ctx, made


@pytest.fixture(autouse=True)
def writer(monkeypatch):
    monkeypatch.setitem(hm.MODELS, "writer", Writer)
    SCRIPT.clear()
    SEEN.clear()


def party(game):
    first = game.state.investigator.id
    for ident, name in (("friend", "林秋"), ("elsewhere", "许远")):
        game.state.investigators.append(game.state.investigator.model_copy(update={"id": ident, "name": name}, deep=True))
    game.state.apart["elsewhere"] = "office"
    game.world.sync()
    return first, "friend", "elsewhere"


def test_a_cast_registration_or_presence_alone_does_not_introduce_anyone(fake):
    game, _ = made("tower")
    prepared = scene.plan(game.world, {"here": ["warden"]}, ctx(game))
    assert not prepared.problems
    scene.land(game.world, prepared.held)
    assert not any("warden" in sightings for sightings in game.world.rec.sightings.values())


def test_introduction_prepares_without_changes_then_records_only_the_actual_body_readers(fake):
    game, _ = made("tower")
    first, friend, elsewhere = party(game)
    before = game.state.model_dump_json()
    prepared = scene.plan(game.world, {"introductions": [{"npc": "warden"}]}, ctx(game, audience=[first]))
    assert not prepared.problems and game.state.model_dump_json() == before
    assert scene.land(game.world, prepared.held)
    seen = game.world.rec.sightings[first]["warden"]
    assert seen.name == "老守钟人" and seen.place == "tower" and seen.look == game.m.npc("warden").look
    assert friend not in game.world.rec.sightings and elsewhere not in game.world.rec.sightings
    event = game.world.rec.event(seen.event)
    assert event is not None and event.visibility == "private" and event.witnesses == [first]


def test_a_same_room_aside_can_introduce_someone_to_other_actual_listeners(fake):
    game, _ = made("tower")
    first, friend, _ = party(game)
    prepared = scene.plan(game.world, {"introductions": [{"npc": "warden", "to": [friend]}]},
                          ctx(game, audience=[first]))
    assert not prepared.problems and scene.land(game.world, prepared.held)
    assert game.world.rec.sightings[friend]["warden"].name == "老守钟人"
    assert "warden" not in game.world.rec.sightings.get(first, {})


def test_a_later_shared_sighting_preserves_private_names_without_teaching_everyone(fake):
    game, _ = made("tower")
    first, friend, _ = party(game)
    prepared = scene.plan(game.world, {"introductions": [{"npc": "warden", "name": "陈先生", "to": [first]}]},
                          ctx(game, audience=[first]))
    assert not prepared.problems and scene.land(game.world, prepared.held)
    game.state.npc("warden").met = True  # the aggregate is not the other investigator's knowledge
    assert game.world.apply(ops.Occur(text="门口有人敲门。", place="tower")).ok
    assert game.world.rec.sightings[first]["warden"].name == "陈先生"
    assert game.world.rec.sightings[friend]["warden"].name == ""


@pytest.mark.parametrize("recipients", [["elsewhere"], [], ["not_a_player"]])
def test_an_introduction_cannot_be_registered_to_absent_or_empty_listeners(fake, recipients):
    game, _ = made("tower")
    first, _, _ = party(game)
    prepared = scene.plan(game.world, {"introductions": [{"npc": "warden", "to": recipients}]},
                          ctx(game, audience=[first]))
    assert prepared.problems and not prepared.held


def test_an_offscreen_or_unregistered_person_cannot_be_made_familiar(fake):
    game, _ = made("tower")
    for npc in ("preacher", "unknown_npc"):
        prepared = scene.plan(game.world, {"introductions": [{"npc": npc}]}, ctx(game))
        assert prepared.problems and not prepared.held
    assert not game.world.rec.sightings


def test_a_person_whose_arrival_cannot_land_is_not_introduced(fake):
    game, _ = made("tower")
    game.world.rec.entities["preacher"].at = "office"
    game.m.npc("preacher").location = "office"
    prepared = scene.plan(game.world, {"here": ["warden", "preacher"], "introductions": [{"npc": "preacher"}]},
                          ctx(game, elapsed=0))
    assert prepared.problems and not prepared.held
    assert not game.world.rec.sightings


def test_a_new_person_can_be_registered_and_introduced_in_one_accepted_scene(fake):
    game, _ = made("tower")
    prepared = scene.plan(game.world, {
        "people": [{"id": "messenger", "name": "阿尔伯特", "role": "邮差"}],
        "here": ["warden", "messenger"], "introductions": [{"npc": "messenger"}],
    }, ctx(game))
    assert not prepared.problems and scene.land(game.world, prepared.held)
    assert game.world.rec.sightings[game.state.investigator.id]["messenger"].name == "阿尔伯特"
    assert sum(npc.id == "messenger" for npc in game.m.npcs) == 1


def test_a_person_can_introduce_himself_then_leave_without_exposing_his_destination(fake):
    game, _ = made("tower")
    prepared = scene.plan(game.world, {
        "here": [], "left": [{"who": "warden", "to": "square"}], "introductions": [{"npc": "warden"}],
    }, ctx(game))
    assert not prepared.problems and scene.land(game.world, prepared.held)
    seen = game.world.rec.sightings[game.state.investigator.id]["warden"]
    assert seen.name == "老守钟人" and seen.place == "tower" and seen.left
    assert game.world.place("warden") == "square"


def test_an_origin_rooms_introduction_stays_there_when_the_investigator_walks_on(fake):
    game, _ = made("square")
    first, friend, _ = party(game)
    game.state.apart[friend] = "tower"
    game.world.sync()
    begin = game.state.minutes
    game.state.minutes += 1
    context = ctx(game, elapsed=1, interval=(begin, begin + 1), participants=[first], audience=[first])
    raw = {"room": "tower", "here": ["warden"], "introductions": [{"npc": "preacher"}]}
    denied = scene.plan(game.world, raw | {"introductions": [{"npc": "preacher", "to": [first, friend]}]}, context)
    assert denied.problems  # waiting inside the tower does not overhear a greeting out on the square
    prepared = scene.plan(game.world, raw, context)
    assert not prepared.problems and scene.land(game.world, prepared.held)
    seen = game.world.rec.sightings[first]["preacher"]
    event = game.world.rec.event(seen.event)
    assert seen.name == "街头布道者" and seen.place == "square" and not seen.left
    assert event is not None and event.place == "square" and event.t == begin
    assert game.world.place("preacher") == "square" and game.state.location == "tower"
    assert "preacher" not in game.world.rec.sightings.get(friend, {})


def test_a_destination_rooms_introduction_reaches_arrivals_but_not_people_left_behind(fake):
    game, _ = made("square")
    first, friend, _ = party(game)
    begin = game.state.minutes
    game.state.minutes += 1
    context = ctx(game, elapsed=1, interval=(begin, begin + 1), participants=[first], audience=[first])
    raw = {"room": "tower", "here": ["warden"], "introductions": [{"npc": "warden"}]}
    denied = scene.plan(game.world, raw | {"introductions": [{"npc": "warden", "to": [first, friend]}]}, context)
    assert denied.problems  # the other investigator stays outside while the first meets the warden
    prepared = scene.plan(game.world, raw, context)
    assert not prepared.problems and scene.land(game.world, prepared.held)
    seen = game.world.rec.sightings[first]["warden"]
    event = game.world.rec.event(seen.event)
    assert seen.name == "老守钟人" and seen.place == "tower"
    assert event is not None and event.place == "tower" and event.t == begin + 1
    assert game.state.location == "tower" and game.world.place(friend) == "square"
    assert "warden" not in game.world.rec.sightings.get(friend, {})


def test_a_remote_introduction_keeps_its_name_without_exposing_a_face_or_position(fake):
    game, _ = made("tower")
    first, friend, _ = party(game)
    prepared = scene.plan(game.world, {"introductions": [{"npc": "preacher", "name": "张先生"}]},
                          ctx(game, audience=[first], contacts=["preacher"]))
    assert not prepared.problems and scene.land(game.world, prepared.held)
    seen = game.world.rec.sightings[first]["preacher"]
    assert seen.name == "张先生" and seen.place == "" and seen.look == "" and seen.portrait == "" and seen.status == ""
    assert "preacher" not in game.world.rec.sightings.get(friend, {})
    denied = scene.plan(game.world, {"introductions": [{"npc": "preacher", "to": [friend]}]},
                        ctx(game, audience=[first], contacts=["preacher"]))
    assert denied.problems and not denied.held


def test_retry_and_reload_keep_the_same_sourced_introduction(fake):
    game, _ = made("tower")
    prepared = scene.plan(game.world, {"introductions": [{"npc": "warden"}]}, ctx(game))
    assert not prepared.problems and scene.land(game.world, prepared.held)
    before = game.state.model_dump_json()
    assert scene.land(game.world, prepared.held)
    assert game.state.model_dump_json() == before
    saved = GameState.model_validate_json(before)
    loaded, _ = made(state=saved, module=Module.load(EXAMPLE))
    seen = loaded.world.rec.sightings[loaded.state.investigator.id]["warden"]
    assert seen.name == "老守钟人" and loaded.world.rec.event(seen.event) is not None


def test_an_introduced_alias_does_not_publish_the_keepers_real_identity(fake):
    game, _ = made("tower")
    game.m.npc("warden").name = "李文（邪教头目）"
    prepared = scene.plan(game.world, {"introductions": [{"npc": "warden", "name": "陈先生"}]}, ctx(game))
    assert not prepared.problems and scene.land(game.world, prepared.held)
    sighting = game.world.rec.sightings[game.state.investigator.id]["warden"]
    assert sighting.name == "陈先生"
    event = game.world.rec.event(sighting.event)
    assert event is not None and "陈先生" in event.text and "李文" not in event.text and "邪教" not in event.text
    assert game.m.npc("warden").name == "李文（邪教头目）"
    prepared = scene.plan(game.world, {"introductions": [{"npc": "warden"}]}, ctx(game, start=1))
    assert not prepared.problems and scene.land(game.world, prepared.held)
    assert game.world.rec.sightings[game.state.investigator.id]["warden"].name == "李文"


def test_npc_tools_cannot_manufacture_an_investigators_introduction_record(fake):
    game, _ = made("tower")
    before = game.state.model_dump_json()
    result = game.world.apply(ops.Occur(text="调查员已经认识我。", actor="warden", introduced={"warden": "老守钟人"}))
    assert not result.ok and game.state.model_dump_json() == before


async def test_a_rejected_draft_does_not_introduce_its_abandoned_visitor(fake):
    game, _ = made("tower")
    game.world.rec.entities["preacher"].at = "office"
    game.m.npc("preacher").location = "office"
    game.state.pending_turn.update(input="/opening", opening=True)
    SCRIPT.extend([
        {"scene": {"here": ["warden", "preacher"], "introductions": [{"npc": "preacher"}]},
         "text": "布道者走进来，自报姓名。"},
        {"scene": {"here": ["warden"], "introductions": [{"npc": "warden"}]},
         "text": "老人报了姓名，推开门请你避雨。"},
    ])
    try:
        text = await game._narrate("守钟人正在接待。", lore=game._brief())
        assert text == "老人报了姓名，推开门请你避雨。" and len(SEEN) == 2
        seen = game.world.rec.sightings[game.state.investigator.id]
        assert seen["warden"].name == "老守钟人" and "preacher" not in seen
    finally:
        await game.close()
