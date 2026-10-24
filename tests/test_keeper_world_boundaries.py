"""World tools share scene causality and cannot substitute for rule or clue settlement."""

from __future__ import annotations

import pytest

from autokeeper.engine.encounter.model import EncounterState, Participant
from autokeeper.engine.scenes import SceneView
from autokeeper.engine.state import SceneState
from autokeeper.world import ops as O
from autokeeper.world.scene import Context
from autokeeper.world.store import World
from autokeeper.world.tools import Gateway

from .test_world import staged


def gateway(world, elapsed=0):
    return Gateway(world, "keeper", scene_context=Context(travel=lambda _a, _b: 20, elapsed=elapsed,
                                                          interval=(world.now - elapsed, world.now)))


def commit(gate, operations, key="boundary"):
    snapshot = gate.snapshot_reads()
    reads = {ref: snapshot.get(ref, 0) for ref in gate.required_reads(operations)}
    return gate.commit(operations, reads=reads, request_id=key, compact=True)


@pytest.mark.parametrize("elapsed", [0, 19])
def test_immediate_tool_move_cannot_outrun_its_actual_scene_interval_and_leaves_no_partial_facts(elapsed):
    world = staged("tower")
    before = world.st.model_dump_json()
    result = commit(gateway(world, elapsed), [
        {"kind": "fact", "fact": "new_trace", "text": "钟楼门口留了张纸"},
        {"kind": "move", "entity": "warden", "to": "office"},
    ])
    assert not result["ok"] and "20 分钟" in "".join(result["problems"])
    assert world.st.model_dump_json() == before


def test_a_real_elapsed_journey_commits_its_departure_and_arrival_without_changing_the_clock():
    world = staged("tower")
    world.st.minutes += 20
    start = world.now - 20
    gate = gateway(world, 20)
    operations = [{"kind": "move", "entity": "warden", "to": "office"}]
    result = commit(gate, operations)
    assert result["ok"], result
    assert world.place("warden") == "office" and world.now == start + 20
    assert world.place_at("warden", start - 1) == "tower"
    assert world.place_at("warden", start + 10) == ""
    assert world.place_at("warden", start + 20) == "office"
    seq = world.rec.seq
    repeat = gate.commit(operations, reads=result["input_reads"], request_id="boundary", compact=True)
    assert repeat["repeat"] and world.rec.seq == seq


def test_rooms_in_the_same_place_do_not_need_an_artificial_minute():
    world = staged("tower")
    result = commit(gateway(world), [{"kind": "move", "entity": "warden", "to": "square"}])
    assert result["ok"], result
    assert world.place("warden") == "square"


def test_an_unrecorded_original_position_can_be_registered_without_inventing_a_journey():
    world = staged("tower")
    world.entity("preacher").at = ""
    world.touch(world.entity("preacher"))
    result = commit(gateway(world), [{"kind": "move", "entity": "preacher", "to": "office"}])
    assert result["ok"], result
    assert world.place("preacher") == "office"


def test_an_earlier_scene_cannot_move_someone_whose_later_action_already_committed():
    world = staged("tower")
    early = world.now
    with world.at(early + 30):
        assert world.apply(O.Move(entity="warden", to="office", actor="engine")).ok
    view = SceneView(world.st, SceneState(id="early", members=[world.st.investigator.id], active=0,
                                          place="tower", at=early))
    scoped = World(world.m, view, initialize=False)
    before = world.st.model_dump_json()
    result = commit(gateway(scoped, 60), [{"kind": "move", "entity": "warden", "to": "square"}])
    assert not result["ok"] and "更晚时段" in "".join(result["problems"])
    assert world.st.model_dump_json() == before
    assert world.place("warden") == world.place_at("warden", early + 30) == "office"


@pytest.mark.parametrize("other_scene", [False, True])
@pytest.mark.parametrize("mode", ["fight", "chase"])
def test_an_active_encounter_participant_cannot_leave_through_an_ordinary_world_tool(other_scene, mode):
    world = staged("tower")
    encounter = EncounterState(id="active", mode=mode, place="tower", started_turn=0, participants=[
        Participant(id="warden", kind="npc", side="foe", controller="keeper")])
    if other_scene:
        world.st.scenes["other"] = SceneState(id="other", members=[], place="tower", at=world.now,
                                               encounter=encounter)
    else:
        world.st.encounter = encounter
    result = commit(gateway(world, 120), [{"kind": "move", "entity": "warden", "to": "office"}])
    assert not result["ok"] and "遭遇回合" in "".join(result["problems"])
    assert world.place("warden") == "tower"


@pytest.mark.parametrize("wrapper", ["schedule", "plan"])
def test_future_movements_are_prepared_without_spending_the_current_zero_minute_scene(wrapper):
    world = staged("tower")
    move = {"kind": "move", "entity": "warden", "to": "office"}
    if wrapper == "schedule":
        operation = {"kind": "schedule", "event": "later_journey", "at": world.now + 20,
                     "text": "守钟人稍后去报社", "ops": [move]}
    else:
        operation = {"kind": "plan", "npc": "warden", "steps": [
            {"id": "later_journey", "text": "稍后去报社", "due": world.now + 20, "ops": [move]}]}
    result = commit(gateway(world), [operation])
    assert result["ok"], result
    assert world.place("warden") == "tower"


@pytest.mark.parametrize("change", [{"add": "死亡"}, {"remove": "昏迷"}, {"status": ""}, {"add": "风寒"}])
def test_investigator_rule_states_are_not_editable_through_generic_world_condition(change):
    world = staged("tower")
    world.st.investigator.add_condition("昏迷")
    before = world.st.model_dump_json()
    result = commit(gateway(world), [{"kind": "condition", "entity": world.st.investigator.id, **change}])
    assert not result["ok"] and "正式规则结算" in "".join(result["problems"])
    assert world.st.model_dump_json() == before and not world.st.investigator.can_act


def test_npc_nonnumeric_story_state_remains_editable():
    world = staged("tower")
    result = commit(gateway(world), [{"kind": "condition", "entity": "warden", "add": "紧张", "status": "失踪"}])
    assert result["ok"], result
    assert world.entity("warden").status == "失踪" and "紧张" in world.entity("warden").conditions


@pytest.mark.parametrize("operation", [
    {"kind": "learn", "fact": "clue:c_diary", "who": ["players"]},
    {"kind": "learn", "fact": "clue:c_diary", "who": ["zhouheng"]},
    {"kind": "occur", "place": "tower", "text": "调查员打开了日记", "facts": ["clue:c_diary"]},
    {"kind": "fact", "fact": "clue:forged_discovery", "text": "已经取得日记", "public": True},
])
def test_module_clues_cannot_be_half_distributed_without_their_rule_and_handout_receipts(operation):
    world = staged("tower")
    before = world.st.model_dump_json()
    result = commit(gateway(world), [operation])
    assert not result["ok"] and "正式线索结算" in "".join(result["problems"])
    assert world.st.model_dump_json() == before


def test_existing_engine_authorized_npc_clue_settlement_keeps_its_formal_path():
    world = staged("tower")
    world.st.npc("warden").unlocked.append("c_diary")
    result = Gateway(world, "warden").settle(
        [{"kind": "learn", "fact": "clue:c_diary", "who": ["players"]}], request_id="formal-npc-disclosure")
    assert result["ok"], result
    assert world.rec.facts["clue:c_diary"].revealed is not None


@pytest.mark.parametrize("method", ["learn", "occur"])
def test_private_npc_knowledge_does_not_require_distributing_a_clue_to_the_investigator(method):
    world = staged("tower")
    assert world.apply(O.Move(entity="preacher", to="tower", actor="engine")).ok
    operation = ({"kind": "learn", "fact": "clue:c_diary", "who": ["preacher"]} if method == "learn" else
                 {"kind": "occur", "place": "tower", "participants": ["preacher"], "facts": ["clue:c_diary"],
                  "text": "守钟人私下告诉布道者日记上的记载", "visibility": "private"})
    result = commit(gateway(world), [operation])
    assert result["ok"], result
    fact = world.rec.facts["clue:c_diary"]
    assert "preacher" in fact.known_by and world.st.investigator.id not in fact.known_by
    assert fact.revealed is None and not world.st.clues and not world.st.clue_settled


@pytest.mark.parametrize("method", ["learn", "occur"])
@pytest.mark.parametrize("partial_audience", [False, True])
def test_public_npc_knowledge_with_an_actual_investigator_listener_still_requires_clue_settlement(method, partial_audience):
    world = staged("tower")
    if partial_audience:
        world.st.investigators.append(world.st.investigator.model_copy(update={"id": "lin", "name": "林秋"}, deep=True))
        world.st.positions = {world.st.investigator.id: "tower", "lin": "office"}
        world.sync()
    operation = ({"kind": "learn", "fact": "clue:c_diary", "who": ["warden"], "visibility": "public"}
                 if method == "learn" else
                 {"kind": "occur", "place": "tower", "participants": ["warden"], "facts": ["clue:c_diary"],
                  "text": "守钟人当面读出了日记上的记载", "visibility": "public"})
    before = world.st.model_dump_json()
    result = commit(gateway(world), [operation])
    assert not result["ok"] and "正式线索结算" in "".join(result["problems"])
    assert world.st.model_dump_json() == before


def test_an_offscreen_public_npc_exchange_does_not_distribute_a_clue_to_absent_players():
    world = staged("tower")
    result = commit(gateway(world), [{"kind": "learn", "fact": "clue:c_diary", "who": ["preacher"],
                                      "visibility": "public"}])
    assert result["ok"], result
    fact = world.rec.facts["clue:c_diary"]
    assert "preacher" in fact.known_by and world.st.investigator.id not in fact.known_by
    assert fact.revealed is None and not world.st.clues


@pytest.mark.parametrize("operation", [
    {"kind": "condition", "entity": "zhouheng", "remove": "昏迷"},
    {"kind": "learn", "fact": "clue:c_diary", "who": ["players"]},
])
@pytest.mark.parametrize("wrapper", ["schedule", "plan"])
def test_future_wrappers_do_not_bypass_rule_state_and_clue_boundaries(operation, wrapper):
    world = staged("tower")
    if wrapper == "schedule":
        wrapped = {"kind": "schedule", "event": "later", "at": world.now + 20, "text": "稍后执行", "ops": [operation]}
    else:
        wrapped = {"kind": "plan", "npc": "warden", "steps": [{"id": "later", "text": "稍后执行", "ops": [operation]}]}
    before = world.st.model_dump_json()
    result = commit(gateway(world), [wrapped])
    assert not result["ok"] and world.st.model_dump_json() == before


@pytest.mark.parametrize("wrapper", ["schedule", "plan"])
@pytest.mark.parametrize("private", [False, True])
def test_future_npc_clue_knowledge_cannot_plan_an_unsettled_public_disclosure(wrapper, private):
    world = staged("tower")
    operation = {"kind": "occur", "place": "office", "participants": ["preacher"],
                 "facts": ["clue:c_diary"], "text": "布道者稍后核对日记上的记载",
                 "visibility": "private" if private else "public"}
    if wrapper == "schedule":
        wrapped = {"kind": "schedule", "event": "later_reading", "at": world.now + 20,
                   "text": "核对记载", "ops": [operation]}
    else:
        wrapped = {"kind": "plan", "npc": "preacher", "steps": [
            {"id": "later_reading", "text": "核对记载", "due": world.now + 20, "ops": [operation]}]}
    result = commit(gateway(world), [wrapped])
    assert result["ok"] is private, result
    assert not world.st.clues and not world.st.clue_settled
    if not private:
        assert "正式线索结算" in "".join(result["problems"])
