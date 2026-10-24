"""A name is learned by a reader; it does not grant a remote face or whereabouts."""

from __future__ import annotations

import pytest

from autokeeper.engine import fog
from autokeeper.engine.encounter import choices, profiles, views
from autokeeper.engine.encounter.actions import Ctx
from autokeeper.engine.encounter.model import EncounterState, Participant
from autokeeper.engine.game import Game
from autokeeper.module.schema import Location, Module
from autokeeper.net.hub import table
from autokeeper.paths import EXAMPLE
from autokeeper.world.record import Sighting
from autokeeper.world.store import World

from .encounter_kit import fight_game


def split_world() -> World:
    module = Module.load(EXAMPLE)
    first = module.pregens[0].model_copy(deep=True)
    second = first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [first, second])
    state.location, state.apart = "tower", {"lin": "office"}
    world = World(module, state)
    state.npc("warden").met = True  # an aggregate marker cannot establish either reader's name
    world.observe("tower", [first.id], "seen-at-tower")
    world.rec.sightings[first.id]["warden"].name = "赵先生"
    return world


def person(world: World, viewer: str | None) -> dict | None:
    return next((npc for npc in table(world.m, world.st, viewer)["npcs"] if npc["id"] == "warden"), None)


def test_private_introduction_is_used_by_its_reader_but_not_a_remote_seat_or_the_common_view():
    world = split_world()
    own = world.st.investigator.id
    assert profiles.shown_name(world, "warden") == "赵先生"
    assert profiles.shown_name(world, "warden", viewer=own) == "赵先生"
    assert profiles.shown_name(world, "warden", viewer="lin") != "赵先生"
    assert profiles.shown_name(world, "warden", viewer=None) != "赵先生"
    assert person(world, own)["name"] == "赵先生"
    assert person(world, "lin") is None and person(world, None) is None
    assert fog.personal(world.m, world.st, own).npcs["warden"].met
    assert "warden" not in fog.personal(world.m, world.st, "lin").npcs


def test_a_telephone_introduction_only_gives_a_name_and_does_not_grant_the_authored_look_or_place():
    world = split_world()
    world.m.locations.append(Location(id="hidden", name="废弃水厂密室", read_aloud="一间秘密房间。"))
    world.m.npc("warden").look = "衣服上印着废弃水厂密室的字样。"
    world.rec.sightings["lin"] = {"warden": Sighting(place="", event="telephone", name="赵先生")}
    shown = person(world, "lin")
    assert shown["name"] == "赵先生"
    assert shown["location"] is None
    assert not any(shown[key] for key in ("look", "portrait", "status", "called"))
    projected = fog.personal(world.m, world.st, "lin")
    assert "hidden" not in fog.heard(world.m, projected)
    assert "hidden" not in fog.bearings(world.m, projected)
    common = person(world, None)
    assert common["name"] == "赵先生" and common["location"] is None and not common["portrait"]


def test_maps_keep_the_readers_introduced_alias_and_last_seen_place_after_unobserved_changes():
    world = split_world()
    own = world.st.investigator.id
    world.st.visited = ["tower"]
    world.m.npc("warden").name = "幕后真名（反派）"
    world.m.npc("warden").location = "office"
    world.m.npc("warden").portrait = "unseen-dead-face"
    world.m.npc("warden").status = "死亡"
    assert fog.met(world.m, fog.personal(world.m, world.st, own)) == {"tower": ["赵先生"]}
    shown = person(world, own)
    assert shown["name"] == "赵先生" and shown["location"] == "tower"
    assert shown["portrait"] != "unseen-dead-face" and shown["status"] != "死亡"
    assert not fog.met(world.m, fog.personal(world.m, world.st, "lin"))


def test_an_unintroduced_seen_person_has_no_secret_name_in_the_table():
    world = split_world()
    own = world.st.investigator.id
    world.rec.sightings[own]["warden"].name = ""
    world.m.npc("warden").name = "不能公开的真名"
    shown = person(world, own)
    assert shown is not None and shown["name"] != "不能公开的真名"
    assert shown["look"] == world.rec.sightings[own]["warden"].look
    assert not fog.personal(world.m, world.st, own).npcs["warden"].met


def test_an_unknown_person_keeps_the_last_seen_descriptor_after_a_private_change():
    world = split_world()
    own = world.st.investigator.id
    world.rec.sightings[own]["warden"].name = ""
    world.rec.sightings[own]["warden"].look = "瘦高的年轻人，戴着一顶灰帽子。"
    world.m.npc("warden").called = ""
    world.m.npc("warden").look = "血流满面的老者，倒在墙边。"
    assert profiles.shown_name(world, "warden", viewer=own) == "瘦高的年轻人"


@pytest.mark.asyncio
async def test_encounter_names_and_options_respect_the_viewer_even_when_the_context_cache_is_reused(fake):
    module = Module.load(EXAMPLE)
    first = module.pregens[0]
    module.pregens.append(first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True))
    game, _, _ = fight_game(module, fake)
    try:
        game.state.investigators.append(module.pregens[-1].model_copy(deep=True))
        game.world.sync()
        game.state.encounter = EncounterState(
            id="names", mode="fight", place="tower", started_turn=0,
            participants=[Participant(id=first.id, kind="investigator", side="party", controller="human"),
                          Participant(id="lin", kind="investigator", side="party", controller="human"),
                          Participant(id="warden", kind="npc", side="foe", controller="keeper")])
        game.state.record.sightings[first.id] = {"warden": Sighting(place="tower", event="a", name="赵先生")}
        game.state.record.sightings["lin"] = {"warden": Sighting(place="tower", event="b", name="李先生")}
        ctx = Ctx.of(game)
        assert ctx.name("warden", first.id) == "赵先生"
        assert ctx.name("warden", "lin") == "李先生"
        assert ctx.name("warden", "players") not in {"赵先生", "李先生"}
        assert any("赵先生" in option.label for option in choices.options(game, first.id, ctx))
        assert any("李先生" in option.label for option in choices.options(game, "lin", ctx))
        assert next(p for p in views.fight_view(game, viewer=first.id)["fighters"] if p["kind"] == "npc")["name"] == "赵先生"
        assert next(p for p in views.fight_view(game, viewer="lin")["fighters"] if p["kind"] == "npc")["name"] == "李先生"
    finally:
        await game.close()
