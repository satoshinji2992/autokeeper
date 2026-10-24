"""Small world-tool payloads retain permissions, relevant versions and scene ownership."""

from __future__ import annotations

import json

import pytest

from autokeeper.engine.scenes import SceneView
from autokeeper.engine.state import SceneState, format_time
from autokeeper.world.ops import AddFact, Move, SetPlan
from autokeeper.world.store import World
from autokeeper.world.tools import Gateway

from .test_world import staged


def test_selected_schema_keeps_only_reachable_operation_definitions():
    gateway = Gateway(staged("tower"), "keeper")
    schema = gateway.schema(["move"])["operations"]
    assert set(schema["discriminator"]["mapping"]) == {"move"}
    assert set(schema["$defs"]) == {"Move"}
    assert len(json.dumps(schema)) < len(json.dumps(gateway.schema()["operations"])) / 4
    with pytest.raises(ValueError, match="不可用"):
        gateway.schema(["effects"])
    with pytest.raises(ValueError, match="不可用"):
        Gateway(gateway.world, "warden").schema(["rewrite"])
    with pytest.raises(ValueError, match="不可用"):
        gateway.schema([])


def test_scoped_inspection_returns_records_once_without_global_context_or_plans():
    world = staged("tower")
    gateway = Gateway(world, "keeper")
    before = world.st.model_dump_json(), world.m.model_dump_json()
    result = gateway.query(compact=True, scope=["entity:warden", "fact:f_key"])
    assert "context" not in result
    assert {row["id"] for row in result["entities"]} == {"warden"}
    assert {row["id"] for row in result["facts"]} == {"f_key"}
    assert result["plans"] == {} and not result["events"]
    assert set(result["reads"]) == {"entity:warden", "fact:f_key"}
    assert "preacher" not in json.dumps(result)
    assert world.st.model_dump_json() == before[0] and world.m.model_dump_json() == before[1]


def test_scoped_npc_query_preserves_knowledge_permissions_and_opaque_refs():
    world = staged("tower")
    gateway = Gateway(world, "warden")
    result = gateway.query(compact=True, scope=["entity:warden", "fact:truth", "agent:preacher"])
    assert not result["facts"] and not result["plans"]
    assert set(result["reads"]) == {"entity:warden", "agent:warden"}
    assert "truth" not in json.dumps(result)


def test_host_selects_pre_call_snapshot_without_unrelated_remote_dependencies():
    world = staged("tower")
    gateway = Gateway(world, "keeper")
    ops = [{"kind": "move", "entity": "warden", "to": "square"}]
    before = world.st.model_dump_json()
    snapshot = gateway.snapshot_reads()
    refs = gateway.required_reads(ops)
    assert world.st.model_dump_json() == before
    assert refs == {"entity:warden", "entity:square"}
    reads = {ref: snapshot.get(ref, 0) for ref in refs}
    assert world.apply(SetPlan(npc="preacher", goals=["去报社"])).ok
    result = gateway.commit(ops, reads=reads, request_id="local-move", compact=True)
    assert result["ok"], result
    assert set(result["positions"]) == {"warden"}
    assert "agent:preacher" not in result["reads"]
    assert "operation" not in result["ops"][0]
    seq = world.rec.seq
    repeat = gateway.commit(ops, reads=reads, request_id="local-move", compact=True)
    assert repeat["repeat"] and world.rec.seq == seq
    assert set(repeat["positions"]) == {"warden"}


@pytest.mark.parametrize("changed", ["entity", "fact", "plan"])
def test_direct_dependencies_still_reject_stale_scoped_decisions(changed):
    world = staged("tower")
    gateway = Gateway(world, "keeper")
    if changed == "entity":
        ops = [{"kind": "move", "entity": "warden", "to": "square"}]
    elif changed == "fact":
        ops = [{"kind": "remember", "npc": "warden", "fact": "f_key", "text": "记住钥匙"}]
    else:
        ops = [{"kind": "plan", "npc": "warden", "goals": ["守楼"]}]
    snapshot = gateway.snapshot_reads()
    reads = {ref: snapshot.get(ref, 0) for ref in gateway.required_reads(ops)}
    if changed == "entity":
        world.apply(Move(entity="warden", to="office"))
    elif changed == "fact":
        world.touch(world.rec.facts["f_key"])
    else:
        world.apply(SetPlan(npc="warden", goals=["关门"]))
    before = world.st.model_dump_json()
    result = gateway.commit(ops, reads=reads, request_id="stale-" + changed, compact=True)
    assert result["status"] == "stale", result
    assert world.st.model_dump_json() == before


def test_scoped_commit_explains_missing_targets_without_mutating():
    world = staged("tower")
    gateway = Gateway(world, "keeper")
    reads = gateway.query(compact=True, scope=["entity:square"])["reads"]
    before = world.st.model_dump_json()
    result = gateway.commit([{"kind": "move", "entity": "warden", "to": "square"}],
                            reads=reads, request_id="missing-target", compact=True)
    assert result["status"] == "rejected" and result["missing_reads"] == ["entity:warden"]
    assert world.st.model_dump_json() == before


def test_staff_can_capture_absence_create_fact_and_replay_without_duplicate():
    world = staged("tower")
    gateway = Gateway(world, "keeper")
    ops = [{"kind": "fact", "fact": "new_trace", "text": "门外留下了脚印", "about": ["warden"]}]
    snapshot = gateway.snapshot_reads()
    reads = {ref: snapshot.get(ref, 0) for ref in gateway.required_reads(ops)}
    assert reads["fact:new_trace"] == 0
    result = gateway.commit(ops, reads=reads, request_id="new-trace", compact=True)
    assert result["ok"], result
    assert result["reads"]["fact:new_trace"] > 0
    seq = world.rec.seq
    assert gateway.commit(ops, reads=reads, request_id="new-trace", compact=True)["repeat"]
    assert world.rec.seq == seq


def test_captured_absence_is_stale_if_another_writer_created_the_record():
    world = staged("tower")
    gateway = Gateway(world, "keeper")
    ops = [{"kind": "fact", "fact": "new_trace", "text": "门外留下了脚印"}]
    reads = {ref: gateway.snapshot_reads().get(ref, 0) for ref in gateway.required_reads(ops)}
    assert world.apply(AddFact(fact="new_trace", text="门外没有脚印")).ok
    result = gateway.commit(ops, reads=reads, request_id="colliding-trace", compact=True)
    assert result["status"] == "stale" and "fact:new_trace" in result["stale"]
    assert world.rec.facts["new_trace"].text == "门外没有脚印"


@pytest.mark.parametrize("op", [
    {"kind": "effects", "effects": []}, {"kind": "clock", "minutes": 60},
    {"kind": "improvise", "plan": {}}, {"kind": "move", "entity": "warden", "to": "square", "minutes": 1},
    {"kind": "schedule", "event": "forced", "at": 2000, "text": "伤害", "ops": [{"kind": "effects", "effects": []}]},
])
def test_compact_preview_does_not_bypass_engine_rules_or_change_world(op):
    world = staged("tower")
    before = world.st.model_dump_json(), world.m.model_dump_json()
    result = Gateway(world, "keeper").preview([op], compact=True)
    assert not result["ok"]
    assert (world.st.model_dump_json(), world.m.model_dump_json()) == before


def test_compact_preview_and_commit_keep_scene_clock_and_shared_record_objects():
    world = staged("tower")
    first = SceneState(id="a", members=[world.st.investigator.id], place="tower", at=600)
    second = SceneState(id="b", members=[], place="office", at=900)
    world.st.scenes = {"a": first, "b": second}
    view = SceneView(world.st, first)
    scoped = World(world.m, view, initialize=False)
    scoped.clock = 600
    gateway = Gateway(scoped, "keeper")
    original_record = world.st.record
    original_npc = world.rec.entities["warden"]
    ops = [{"kind": "move", "entity": "warden", "to": "square"}]
    before = world.st.model_dump_json()
    preview = gateway.preview(ops, compact=True)
    assert preview["ok"] and preview["clock"] == format_time(600)
    assert world.st.model_dump_json() == before
    result = gateway.commit(ops, reads=preview["reads"], request_id="scene-move", compact=True)
    assert result["ok"], result
    assert world.st.record is view.record is original_record
    assert world.rec.entities["warden"] is original_npc and original_npc.at == "square"
    assert first.at == 600 and second.at == 900 and world.st.scenes["b"] is second


def test_plan_dependencies_include_nested_targets_and_fact_conditions():
    world = staged("tower")
    gateway = Gateway(world, "keeper")
    ops = [{"kind": "plan", "npc": "warden", "steps": [{"id": "leave", "text": "交出雨衣后去广场",
            "when": "knows:warden:f_key & owner:coat:warden", "achieved": "at:warden:square",
            "ops": [{"kind": "move", "entity": "warden", "to": "square"}]}]}]
    required = gateway.required_reads(ops)
    assert {"agent:warden", "entity:warden", "entity:square", "entity:coat", "fact:f_key"} <= required
    reads = {ref: gateway.snapshot_reads().get(ref, 0) for ref in required}
    result = gateway.commit(ops, reads=reads, request_id="nested-plan", compact=True)
    assert result["ok"], result
    assert "fact:f_key" in world.rec.agents["warden"].reads


def test_item_name_resolves_to_actual_item_dependency_not_an_absent_name():
    world = staged("tower")
    gateway = Gateway(world, "keeper")
    ops = [{"kind": "transfer", "item": "雨衣", "to": world.st.investigator.id, "giver": "warden"}]
    required = gateway.required_reads(ops)
    assert "entity:coat" in required and "entity:雨衣" not in required
    reads = {ref: gateway.snapshot_reads().get(ref, 0) for ref in required}
    world.touch(world.rec.entities["coat"])
    result = gateway.commit(ops, reads=reads, request_id="item-alias", compact=True)
    assert result["status"] == "stale" and "entity:coat" in result["stale"]


def test_declared_flags_are_read_without_clock_or_global_event_dependencies():
    world = staged("tower")
    gateway = Gateway(world, "keeper")
    flag = next(iter(world.m.flags))
    result = gateway.query(compact=True, scope=["flag:" + flag])
    assert result["flags"] == {flag: flag in world.st.flags}
    ops = [{"kind": "flag", "flag": flag, "on": True}]
    assert gateway.required_reads(ops) == {"flag:" + flag}
    before = world.now
    committed = gateway.commit(ops, reads=result["reads"], request_id="set-flag", compact=True)
    assert committed["ok"] and world.now == before


@pytest.mark.parametrize("actor, anchor", [("keeper", "tower"), ("warden", "warden")])
def test_plain_occurrence_preview_has_the_same_usable_dependencies_as_commit(actor, anchor):
    world = staged("tower")
    gateway = Gateway(world, actor)
    ops = [{"kind": "occur", "text": "门外传来脚步声"}]
    before = world.st.model_dump_json()
    preview = gateway.preview(ops, compact=True)
    assert preview["ok"] and set(preview["reads"]) == gateway.required_reads(ops) == {f"entity:{anchor}"}
    assert world.st.model_dump_json() == before
    result = gateway.commit(ops, reads=preview["reads"], request_id="plain-event", compact=True)
    assert result["ok"], result
    seq = world.rec.seq
    assert gateway.commit(ops, reads=preview["reads"], request_id="plain-event", compact=True)["repeat"]
    assert world.rec.seq == seq
