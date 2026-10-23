"""Location visits and map disclosures are sourced to their actual observers."""

from __future__ import annotations

import pytest

from autokeeper.engine import fog, presentation
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Location, Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context
from autokeeper.world.ops import Bring, Occur, Transfer
from autokeeper.world.record import PLAYERS
from autokeeper.world.store import World


def split_world() -> World:
    m = Module.load(EXAMPLE)
    m.locations.append(Location(id="crypt", name="旧城地下密室", read_aloud="密室里有一扇黑色铁门。"))
    m.location("office").read_aloud = "报社墙上的箭头指向旧城地下密室。"
    first = m.pregens[0].model_copy(deep=True)
    second = first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    st = Game.new(m, [first, second])
    st.location, st.apart, st.visited = "tower", {"lin": "office"}, ["tower", "office", "crypt"]
    st.heard = {"crypt": "旧城地下密室"}
    return World(m, st)


def test_an_empty_lobby_has_no_vacuously_shared_visits_or_hidden_location_descriptions():
    from autokeeper.net.hub import table

    m = Module.load(EXAMPLE)
    st = Game.new(m, [], human=[])
    World(m, st)
    before = st.model_dump_json()
    assert not fog.personal(m, st, None).visited
    book = table(m, st)
    assert all(not place["read_aloud"] and not place["art"] and not place["views"] for place in book["locations"])
    assert st.model_dump_json() == before


def test_initialization_records_only_each_investigators_actual_place_and_reload_does_not_backfill():
    w = split_world()
    assert w.entity("tower").observed_by == ["zhouheng"]
    assert w.entity("office").observed_by == ["lin"]
    assert not w.entity("crypt").observed_by
    restored = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    assert restored.entity("tower").observed_by == ["zhouheng"]
    assert restored.entity("office").observed_by == ["lin"]
    assert not restored.entity("crypt").observed_by


def test_a_remote_global_visit_read_aloud_or_clue_does_not_reveal_a_place_to_another_investigator():
    w = split_world()
    w.st.clues.append("c_diary")
    w.m.clue("c_diary").text = "旧城地下密室藏着失踪者的笔记。"
    before = w.st.model_dump_json()
    own = fog.personal(w.m, w.st, "zhouheng")
    other = fog.personal(w.m, w.st, "lin")
    assert own.location == "tower" and own.visited == ["tower"]
    assert other.location == "office" and other.visited == ["office"]
    assert "crypt" not in fog.heard(w.m, own) and "crypt" in fog.heard(w.m, other)
    assert "office" not in own.visited and "crypt" not in own.visited
    assert not own.clues and not other.clues and own.record is w.st.record
    assert own.apart == {"lin": ""} and own.journey == own.ongoing == ""
    assert w.st.model_dump_json() == before


def test_private_readable_history_and_common_memory_are_evidence_but_player_guesses_are_not():
    w = split_world()
    # Remove the local read-aloud's independent disclosure for this source-specific example.
    w.m.location("office").read_aloud = "报社里堆着旧报纸。"
    w.st.add("player", "我猜旧城地下密室里有东西。", who="周衡", audience=["zhouheng"])
    w.st.add("npc", "旧城地下密室在河边。", who="线人", audience=["lin"])
    w.st.private_history.extend(w.st.log)
    w.st.log.clear()
    assert "crypt" not in fog.personal(w.m, w.st, "zhouheng").heard
    assert "crypt" in fog.personal(w.m, w.st, "lin").heard
    assert "crypt" not in fog.personal(w.m, w.st, None).heard
    w.st.memory = "调查员共同听说了旧城地下密室。"
    assert all("crypt" in fog.personal(w.m, w.st, who).heard for who in ("zhouheng", "lin", None))


def test_only_the_delivered_papers_owner_gets_its_map_disclosure():
    w = split_world()
    w.m.location("office").read_aloud = "报社里堆着旧报纸。"
    paper = w.m.handouts[0]
    paper.text = "小地图标注着旧城地下密室。"
    w.st.handouts.append(paper.id)
    delivery = presentation.prepare(w.m, w.st, {"ref": f"handout:{paper.id}", "to": ["lin"]}, "private-map")
    w.st.presentations.append(delivery)
    assert paper.id not in fog.personal(w.m, w.st, "zhouheng").handouts
    assert "crypt" not in fog.personal(w.m, w.st, "zhouheng").heard
    own = fog.personal(w.m, w.st, "lin")
    assert own.handouts == [paper.id] and "crypt" in own.heard
    assert paper.id not in fog.personal(w.m, w.st, None).handouts


def test_observe_requires_actual_presence_and_preserves_personal_visits():
    w = split_world()
    w.observe("office", ["zhouheng", "lin"], "office-source")
    assert w.entity("office").observed_by == ["lin"]
    w.st.apart["lin"] = "crypt"
    w.observe("crypt", ["lin"], "real-crypt-arrival")
    assert w.entity("crypt").observed_by == ["lin"]
    assert "crypt" not in fog.personal(w.m, w.st, "zhouheng").visited
    assert "crypt" in fog.personal(w.m, w.st, "lin").visited
    assert not fog.personal(w.m, w.st, None).visited  # start remains a required schema placeholder


def test_split_scene_item_observation_is_personal_and_the_keeper_still_gets_the_local_receipt():
    w = split_world()
    before = w.rec.seq
    w.apply(Occur(id="local-change", text="钟楼窗扇被推开了。", place="tower", visibility="public"))
    assert context.happened(w, before) == ["钟楼窗扇被推开了。"]
    result = w.apply(Bring(id="local-cup", name="黄铜杯", holder="warden", actor="warden"))
    item = w.entity(result.data)
    event = w.rec.event(result.event)
    assert event.visibility == "private" and "zhouheng" in event.witnesses and "lin" not in event.witnesses
    assert "zhouheng" in item.observed_by and PLAYERS not in item.observed_by
    moved = w.apply(Transfer(id="local-gift", item=item.id, to="zhouheng", giver="warden", actor="warden"))
    assert w.rec.event(moved.event).visibility == "private" and PLAYERS not in item.observed_by
    w.st.apart.clear()
    assert w.apply(Occur(id="shared-change", text="两位调查员一同听见钟响。", place="tower")).ok
    assert w.rec.events[-1].visibility == "public"


def test_single_investigator_keeps_their_own_stops_without_borrowing_another_seats_history():
    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.location, st.visited = "tower", ["square", "tower", "office"]
    w = World(m, st)
    projected = fog.personal(w.m, w.st, None)
    assert set(projected.visited) == set(w.st.visited) and projected.location == "tower"


def test_removing_another_seat_does_not_turn_their_old_visits_into_single_player_knowledge():
    w = split_world()
    w.st.investigators.pop()
    w.st.apart.clear()
    assert "crypt" not in fog.personal(w.m, w.st, None).visited


def test_only_the_investigator_with_formal_clue_evidence_gets_the_clue_identifier():
    w = split_world()
    clue = w.m.clue("c_diary")
    w.st.add("clue", f"〔{clue.title}〕{clue.text}", audience=["lin"])
    assert fog.personal(w.m, w.st, "lin").clues == [clue.id]
    assert not fog.personal(w.m, w.st, "zhouheng").clues
    assert not fog.personal(w.m, w.st, None).clues
    w.st.log.clear()
    w.rec.facts[f"clue:{clue.id}"].known_by.append("lin")
    assert fog.personal(w.m, w.st, "lin").clues == [clue.id]
    assert not fog.personal(w.m, w.st, "zhouheng").clues


def test_an_invalid_viewer_is_not_allowed_to_select_another_seats_map():
    w = split_world()
    with pytest.raises(ValueError, match="调查员不在本局"):
        fog.personal(w.m, w.st, "missing")
