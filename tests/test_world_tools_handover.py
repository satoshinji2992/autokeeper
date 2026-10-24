"""Immediate tool handovers obey the same place and time boundaries as a narrated scene."""

from __future__ import annotations

import pytest

from autokeeper.engine.scenes import SceneView
from autokeeper.engine.state import SceneState
from autokeeper.world import ops as O
from autokeeper.world.record import Entity
from autokeeper.world.scene import Context
from autokeeper.world.store import World
from autokeeper.world.tools import Gateway

from .test_world import staged


def gateway(world, elapsed=0):
    return Gateway(world, "keeper", scene_context=Context(
        travel=lambda _a, _b: 20, elapsed=elapsed, interval=(world.now - elapsed, world.now)))


def commit(gate, operations, key="handover"):
    snapshot = gate.snapshot_reads()
    reads = {ref: snapshot.get(ref, 0) for ref in gate.required_reads(operations)}
    return gate.commit(operations, reads=reads, request_id=key, compact=True)


def test_remote_handover_rejects_the_whole_batch_without_leaving_a_new_fact():
    world = staged("office")
    gate = gateway(world)
    before = world.st.model_dump_json(), world.m.model_dump_json()
    result = commit(gate, [
        {"kind": "fact", "fact": "offered_coat", "text": "守钟人提出借出雨衣"},
        {"kind": "transfer", "item": "coat", "to": world.st.investigator.id},
    ])
    assert not result["ok"] and result["index"] == 1
    assert "不在同一地点" in "".join(result["problems"])
    assert (world.st.model_dump_json(), world.m.model_dump_json()) == before


def test_handover_cannot_put_a_local_item_into_a_remote_npc_inventory():
    world = staged("tower")
    result = commit(gateway(world), [{"kind": "transfer", "item": "coat", "to": "preacher"}])
    assert not result["ok"] and world.entity("coat").at == "warden"


def test_an_immediate_handover_is_valid_when_both_offscreen_people_are_together():
    world = staged("tower")
    assert world.apply(O.Move(entity="warden", to="office")).ok
    assert world.apply(O.Move(entity="preacher", to="office")).ok
    result = commit(gateway(world), [{"kind": "transfer", "item": "coat", "to": "preacher"}])
    assert result["ok"], result
    assert world.entity("coat").at == "preacher"


def test_a_legal_move_then_handover_uses_the_updated_trial_position_and_replays_once():
    world = staged("office")
    world.st.minutes += 20
    gate = gateway(world, 20)
    operations = [{"kind": "move", "entity": "warden", "to": "office"},
                  {"kind": "transfer", "item": "雨衣", "to": world.st.investigator.id}]
    before = world.st.model_dump_json()
    preview = gate.preview(operations, compact=True)
    assert preview["ok"], preview
    assert world.st.model_dump_json() == before
    result = gate.commit(operations, reads=preview["reads"], request_id="move-hand-over", compact=True)
    assert result["ok"], result
    assert world.entity("coat").at == world.st.investigator.id
    seq = world.rec.seq
    assert gate.commit(operations, reads=preview["reads"], request_id="move-hand-over", compact=True)["repeat"]
    assert world.rec.seq == seq


def test_a_handover_before_the_necessary_move_does_not_reorder_the_operations():
    world = staged("office")
    world.st.minutes += 20
    before = world.st.model_dump_json()
    result = commit(gateway(world, 20), [
        {"kind": "transfer", "item": "coat", "to": world.st.investigator.id},
        {"kind": "move", "entity": "warden", "to": "office"},
    ])
    assert not result["ok"] and result["index"] == 0
    assert world.st.model_dump_json() == before


def test_bring_cannot_add_an_item_to_a_remote_investigators_inventory():
    world = staged("tower")
    other = world.st.investigator.model_copy(deep=True, update={"id": "guest", "name": "远处的调查员"})
    world.st.investigators.append(other)
    world.st.positions[other.id] = "office"
    world.rec.entities[other.id] = Entity(id=other.id, name=other.name, kind="investigator", at="office")
    before = world.st.model_dump_json()
    result = commit(gateway(world), [{"kind": "bring", "name": "火柴", "holder": other.id}])
    assert not result["ok"] and "不在当前场景" in "".join(result["problems"])
    assert world.st.model_dump_json() == before


def test_bring_still_registers_an_ordinary_item_for_a_local_investigator():
    world = staged("tower")
    result = commit(gateway(world), [{"kind": "bring", "name": "火柴", "holder": world.st.investigator.id}])
    assert result["ok"], result
    assert "火柴" in world.st.investigator.inventory


@pytest.mark.parametrize("subject", ["coat", "warden", "zhouheng"])
def test_earlier_scene_cannot_change_an_item_or_participant_with_a_later_physical_commitment(subject):
    world = staged("tower")
    early = world.now
    with world.at(early + 30):
        assert world.apply(O.Occur(text="参与了较晚时段的实际操作", place="tower", participants=[subject])).ok
    view = SceneView(world.st, SceneState(id="early", members=[world.st.investigator.id], place="tower", at=early))
    scoped = World(world.m, view, initialize=False)
    before = world.st.model_dump_json()
    result = commit(gateway(scoped), [{"kind": "transfer", "item": "coat", "to": world.st.investigator.id}])
    assert not result["ok"] and "更晚时段" in "".join(result["problems"])
    assert world.st.model_dump_json() == before


def test_later_readonly_knowledge_is_not_a_physical_handover_reservation():
    world = staged("tower")
    early = world.now
    with world.at(early + 30):
        assert world.apply(O.Remember(npc="warden", text="想起了钟楼的旧事")).ok
        assert world.apply(O.AddFact(fact="old_clock", text="钟楼建于多年前", about=["warden"])).ok
    view = SceneView(world.st, SceneState(id="early", members=[world.st.investigator.id], place="tower", at=early))
    scoped = World(world.m, view, initialize=False)
    result = commit(gateway(scoped), [{"kind": "transfer", "item": "coat", "to": world.st.investigator.id}])
    assert result["ok"], result


@pytest.mark.parametrize("wrapper", ["schedule", "plan"])
def test_future_handover_is_not_rejected_using_this_segments_current_positions(wrapper):
    world = staged("office")
    transfer = {"kind": "transfer", "item": "coat", "to": world.st.investigator.id}
    if wrapper == "schedule":
        operation = {"kind": "schedule", "event": "later_handover", "at": world.now + 20,
                     "text": "守钟人到达后交出雨衣", "ops": [transfer]}
    else:
        operation = {"kind": "plan", "npc": "warden", "steps": [
            {"id": "later_handover", "text": "到达后交出雨衣", "due": world.now + 20, "ops": [transfer]}]}
    result = commit(gateway(world), [operation])
    assert result["ok"], result
    assert world.entity("coat").at == "warden"


def test_gateway_without_a_scene_context_keeps_the_existing_backstage_interface():
    world = staged("office")
    result = commit(Gateway(world, "keeper"), [{"kind": "transfer", "item": "coat", "to": world.st.investigator.id}])
    assert result["ok"], result


def test_bring_cannot_insert_an_earlier_item_after_the_holders_later_physical_action():
    world = staged("tower")
    early = world.now
    with world.at(early + 30):
        assert world.apply(O.Occur(text="调查员在较晚时段实际翻查了背包", place="tower",
                                  participants=[world.st.investigator.id])).ok
    view = SceneView(world.st, SceneState(id="early", members=[world.st.investigator.id], place="tower", at=early))
    scoped = World(world.m, view, initialize=False)
    before = world.st.model_dump_json()
    result = commit(gateway(scoped), [{"kind": "bring", "name": "火柴", "holder": world.st.investigator.id}])
    assert not result["ok"] and "更晚时段" in "".join(result["problems"])
    assert world.st.model_dump_json() == before


def test_immediate_handover_uses_the_explicit_segment_end_without_moving_the_root_clock():
    world = staged("tower")
    end = world.now
    world.st.minutes += 30
    gate = Gateway(world, "keeper", scene_context=Context(travel=lambda _a, _b: 20, interval=(end, end)))
    result = commit(gate, [{"kind": "transfer", "item": "coat", "to": world.st.investigator.id}])
    assert result["ok"], result
    assert next(event for event in reversed(world.rec.events) if event.kind == "transfer").t == end
    assert world.now == end + 30
