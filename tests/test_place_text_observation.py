"""A remembered location does not disclose later descriptions written behind the scenes."""

from autokeeper.engine import fog
from autokeeper.engine.state import GameState
from autokeeper.net.hub import table
from autokeeper.world.store import World

from .test_personal_map_projection import split_world


def location(world, viewer, ident="tower"):
    return next(place for place in table(world.m, world.st, viewer)["locations"] if place["id"] == ident)


def test_remote_rewrite_keeps_the_seen_description_and_does_not_unlock_its_new_landmark():
    world = split_world()
    old = location(world, "zhouheng")["read_aloud"]
    world.st.location = "square"
    world.m.location("tower").read_aloud = "楼梯口出现了血字，指向旧城地下密室。"
    for reader in (world, World(world.m.model_copy(deep=True), GameState.model_validate_json(world.st.model_dump_json()))):
        view = fog.personal(reader.m, reader.st, "zhouheng")
        assert location(reader, "zhouheng")["read_aloud"] == old
        assert "crypt" not in fog.heard(reader.m, view)
        assert "crypt" not in fog.landmarks(reader.m, view)
        assert "crypt" not in fog.chart(reader.m, view).names
        assert "血字" not in "\n".join(fog.kept(reader.m, view, viewer="zhouheng"))
    assert "血字" in world.m.location("tower").read_aloud  # the omniscient keeper retains the actual new scene


def test_only_the_observer_gets_the_new_place_description_and_later_visits_refresh_it():
    world = split_world()
    old = location(world, "zhouheng")["read_aloud"]
    world.st.location = "square"
    world.st.apart["lin"] = "tower"
    new = world.m.location("tower").read_aloud = "楼梯口出现了血字，指向旧城地下密室。"
    world.observe("tower", ["lin"], "lin-sees-writing")
    assert location(world, "lin")["read_aloud"] == new
    assert location(world, "zhouheng")["read_aloud"] == old
    assert "crypt" in fog.heard(world.m, fog.personal(world.m, world.st, "lin"))
    assert "crypt" not in fog.heard(world.m, fog.personal(world.m, world.st, "zhouheng"))
    world.st.location = "tower"
    world.observe("tower", ["zhouheng"], "zhou-returns")
    assert location(world, "zhouheng")["read_aloud"] == new
    assert location(world, None)["read_aloud"] == new


def test_unseated_view_does_not_borrow_one_players_new_observation():
    world = split_world()
    world.st.apart["lin"] = "tower"
    world.observe("tower", ["zhouheng", "lin"], "both-visit")
    old = location(world, None)["read_aloud"]
    assert old
    world.st.apart["lin"] = "office"
    world.m.location("tower").read_aloud = "楼梯口出现了血字，指向旧城地下密室。"
    world.observe("tower", ["zhouheng"], "zhou-sees-writing")
    common = location(world, None)
    assert "血字" not in common["read_aloud"]
    assert all(place["id"] != "crypt" for place in table(world.m, world.st)["locations"])
    assert location(world, "lin")["read_aloud"] == old


def test_a_player_client_without_world_record_uses_its_already_filtered_module():
    world = split_world()
    book = table(world.m, world.st, "zhouheng")
    client_module = type(world.m).model_validate(book)
    client_state = GameState(module=world.m.id, investigators=world.st.investigators, location="tower", visited=["tower"])
    assert not client_state.record.seeded
    assert fog.read_aloud(client_module, client_state, "tower") == location(world, "zhouheng")["read_aloud"]
