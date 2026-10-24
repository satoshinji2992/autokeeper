"""Visited places and delivered papers retain the artwork actually seen."""

from pathlib import Path

from PIL import Image

from autokeeper.engine import presentation
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module, View
from autokeeper.net.hub import table
from autokeeper.paths import EXAMPLE
from autokeeper.world.ops import Move

from .test_engine import make
from .test_personal_map_projection import split_world
from .test_scene_readiness import scene


def test_a_remote_redraw_does_not_replace_the_last_seen_place_or_view():
    world = split_world()
    key = world.m.location("tower").art = "tower-picture"
    world.m.art[key] = "[ ORIGINAL TOWER ]"
    world.m.location("tower").views = [View(key="tower-detail", label="齿轮间")]
    world.m.art["tower-detail"] = "[ ORIGINAL GEARS ]"
    world.observe("tower", ["zhouheng"], "zhou-sees-tower")
    old = world.m.art[key]
    before = next(place for place in table(world.m, world.st, "zhouheng")["locations"] if place["id"] == "tower")
    assert before["art"] != key and world.m.art[before["art"]] == old
    world.m.art[key] = "[ CHANGED TOWER ]"  # completed background redraw, without a model call
    world.m.art["tower-detail"] = "[ BROKEN GEARS ]"
    after = next(place for place in table(world.m, world.st, "zhouheng")["locations"] if place["id"] == "tower")
    assert after == before and world.m.art[after["art"]] == old
    world.st.apart["lin"] = "tower"
    world.observe("tower", ["lin"], "lin-arrives-after-change")
    current = next(place for place in table(world.m, world.st, "lin")["locations"] if place["id"] == "tower")
    assert current["art"] != before["art"] and world.m.art[current["art"]] == "[ CHANGED TOWER ]"
    assert world.rec.place_sightings["zhouheng"]["tower"].art == before["art"]


def test_handout_delivery_keeps_its_original_image_after_source_replacement_and_save_load(tmp_path):
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    key, source = "paper-photo", tmp_path / "paper.png"
    module.handouts[0].images = [key]
    Image.new("RGB", (16, 16), "red").save(source)
    old = source.read_bytes()
    module.pictures[key] = str(source)
    delivery = presentation.prepare(module, state, {"ref": f"handout:{module.handouts[0].id}"}, "paper-delivery")
    state.presentations.append(delivery)
    Image.new("RGB", (16, 16), "blue").save(source)
    restored = GameState.model_validate_json(state.model_dump_json())
    saved = restored.presentations[0]["payload"]["pictures"][key]
    assert saved != str(source) and Path(saved).read_bytes() == old


async def test_staged_world_observations_commit_their_image_versions_even_without_new_artwork(fake):
    module = Module.load(EXAMPLE)
    module.npc("warden").portrait = "warden"
    module.art["warden"] = "[ THE WARDEN ]"
    game, _, _ = make(module, fake, {})
    game.state.npc("warden").met = True
    try:
        assert await game._land("展开", scene(Move(entity="warden", to="square")), draw=0) is not None
        key = game.state.record.sightings[game.state.investigator.id]["warden"].portrait
        assert game.state.visuals[key]["art"] == "[ THE WARDEN ]"
        assert game.m.art[key] == "[ THE WARDEN ]"
        restored = GameState.model_validate_json(game.state.model_dump_json())
        assert restored.visuals[key]["source"] == "warden"
    finally:
        await game.close()
