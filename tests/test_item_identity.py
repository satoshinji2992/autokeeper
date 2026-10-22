"""Display names must refer to the recorded object, including after ownership changes."""

from __future__ import annotations

from autokeeper.agents.director import Improv, NewItem
from autokeeper.engine import world as legacy_world
from autokeeper.engine.state import GameState, WorldChange
from autokeeper.world import adapters, context, maintenance, scene
from autokeeper.world import ops as O
from autokeeper.world.store import World

from .test_world import fresh


def important_tool(w: World, ident: str = "field_lamp", name: str = "便携照明灯") -> str:
    player = w.st.investigator.id
    plan = Improv(kind="展开", reason="登记调查员实际带来的工具", items=[
        NewItem(id=ident, name=name, description="黄铜外壳", where="tower", owner=player),
    ])
    result = adapters.improvise(w, plan)
    assert result.ok, result.problems
    return ident


def test_a_named_important_tool_returns_and_can_be_received_again_without_a_shadow():
    w = fresh("tower")
    me = w.st.investigator.id
    ident = important_tool(w)
    assert w.apply(O.Transfer(item="便携照明灯", to="warden", actor=me)).ok
    assert w.entity(ident).at == "warden"
    assert legacy_world.owner(w.m, w.st, "便携照明灯") == "warden"
    assert "便携照明灯" not in w.st.investigator.inventory
    assert "便携照明灯" not in w.rec.entities and "便携照明灯" not in w.st.item_owners
    assert w.apply(O.Transfer(item="便携照明灯", to=me, actor="warden")).ok
    assert w.entity(ident).at == me and w.st.investigator.inventory.count("便携照明灯") == 1
    assert [e.id for e in w.rec.entities.values() if e.kind == "item" and e.name == "便携照明灯"] == [ident]


def test_named_consumption_updates_the_existing_important_tool_and_survives_reload():
    w = fresh("tower")
    ident = important_tool(w)
    assert w.apply(O.Equip(item="便携照明灯", quantity=0, actor=w.st.investigator.id)).ok
    assert w.entity(ident).quantity == 0 and "便携照明灯" not in w.st.investigator.inventory
    assert "便携照明灯" not in w.rec.entities and "便携照明灯" not in w.st.item_owners
    loaded = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    assert loaded.entity(ident).quantity == 0
    assert not loaded.apply(O.Transfer(item="便携照明灯", to="warden")).ok


def test_explicit_equipment_promotes_a_carried_everyday_object_in_place():
    w = fresh("tower")
    me = w.st.investigator.id
    brought = w.apply(O.Bring(name="便携照明灯", holder=me, actor="keeper"))
    assert brought.ok
    ident = brought.data
    assert not w.entity(ident).important
    assert w.apply(O.Equip(item="便携照明灯", equipment="held", actor=me)).ok
    assert w.entity(ident).important and w.entity(ident).equipment == "held"
    assert "便携照明灯" not in w.rec.entities
    assert w.apply(O.Transfer(item="便携照明灯", to="warden", actor=me)).ok
    assert w.entity(ident).at == "warden" and "便携照明灯" not in w.st.investigator.inventory


def test_world_change_transfer_resolves_the_same_item_as_the_inventory_name():
    w = fresh("tower")
    ident = important_tool(w)
    change = WorldChange(kind="transfer", key="tool_returned", item="便携照明灯", owner="warden",
                         npc="warden", text="周衡把便携照明灯交还守钟人。")
    results = adapters.settle_changes(w, [change], source="arbiter")
    assert len(results) == 1 and results[0].ok
    assert w.entity(ident).at == "warden" and w.st.item_owners[ident] == "warden"
    assert "便携照明灯" not in w.st.item_owners and "便携照明灯" not in w.st.investigator.inventory


def test_legacy_world_changes_use_the_same_identity_and_keep_the_record_consistent():
    w = fresh("tower")
    ident = important_tool(w)
    change = WorldChange(kind="transfer", key="tool_returned", item="便携照明灯", owner="warden",
                         npc="warden", text="周衡把便携照明灯交还守钟人。")
    legacy_world.apply(w.m, w.st, [change], source="arbiter")
    assert w.entity(ident).at == w.st.item_owners[ident] == "warden"
    assert legacy_world.owner(w.m, w.st, "便携照明灯") == "warden"
    assert "便携照明灯" not in w.st.item_owners


def test_a_fact_about_returning_an_item_does_not_replace_its_transfer():
    w = fresh("tower")
    ident = important_tool(w)
    change = WorldChange(kind="fact", key="return_discussed", text="周衡说要交还便携照明灯。", npc="warden")
    assert adapters.settle_changes(w, [change], source="arbiter")[0].ok
    assert w.entity(ident).at == w.st.investigator.id
    assert w.apply(O.Transfer(item="便携照明灯", to="warden", actor=w.st.investigator.id)).ok
    assert w.entity(ident).at == "warden"


def test_object_handles_follow_ownership_and_do_not_include_another_holders_items():
    w = fresh("tower")
    me = w.st.investigator.id
    ident = important_tool(w)
    everyday = w.apply(O.Bring(name="封签纸包（红绳扎口）", holder=me, actor="keeper")).data
    remote = Improv(kind="展开", reason="登记布道者自己持有的东西", items=[
        NewItem(id="private_case", name="布道者的匣子", description="不应复制的匣内秘密", where="square", owner="preacher"),
    ])
    assert adapters.improvise(w, remote).ok
    before = w.st.model_dump_json()
    refs = context.item_refs(w, {me})
    assert ident in refs and everyday in refs and "封签纸包（红绳扎口）" in refs
    assert "private_case" not in refs and "不应复制的匣内秘密" not in refs
    assert w.st.model_dump_json() == before
    assert w.apply(O.Transfer(item=ident, to="warden", actor=me)).ok
    assert ident not in context.item_refs(w, {me}) and ident in context.item_refs(w, {"warden"})


def test_ambiguous_display_names_require_an_id_instead_of_creating_a_third_object():
    w = fresh("tower")
    first = important_tool(w)
    second = important_tool(w, ident="spare_lamp")
    assert not w.apply(O.Equip(item="便携照明灯", quantity=0, actor=w.st.investigator.id)).ok
    assert not w.apply(O.Transfer(item="便携照明灯", to="warden")).ok
    assert w.entity(first).quantity == w.entity(second).quantity == 1
    assert "便携照明灯" not in w.rec.entities
    assert w.apply(O.Transfer(item=second, to="warden")).ok
    assert w.apply(O.Equip(item="便携照明灯", equipment="held", actor=w.st.investigator.id)).ok
    assert w.entity(first).equipment == "held" and w.entity(second).at == "warden"


def test_a_missing_display_name_cannot_create_a_new_possession():
    w = fresh("tower")
    assert not w.apply(O.Transfer(item="臆造的工具箱", to="warden")).ok
    assert not w.apply(O.Equip(item="臆造的工具箱", quantity=0, actor=w.st.investigator.id)).ok
    assert "臆造的工具箱" not in w.rec.entities and "臆造的工具箱" not in w.st.item_owners


def test_read_only_auditing_compares_ownership_layers_without_the_canonical_resolver():
    w = fresh("tower")
    ident = important_tool(w)
    w.st.item_owners[ident] = "warden"
    next(item for item in w.m.items if item.id == ident).owner = "square"
    before = w.st.model_dump_json(), w.m.model_dump_json()
    result = maintenance.inspect(w)
    issue = next(issue for issue in result["issues"] if issue["code"] == "item_owner")
    assert issue["ref"] == f"entity:{ident}"
    assert f"世界持有者={w.st.investigator.id}" in issue["evidence"]
    assert "物品台账持有者=warden" in issue["evidence"]
    assert "模组投影持有者=square" in issue["evidence"]
    assert (w.st.model_dump_json(), w.m.model_dump_json()) == before


def test_scene_holds_rejects_an_ambiguous_display_name_without_moving_either_object():
    w = fresh("tower")
    first = important_tool(w)
    second = important_tool(w, ident="spare_lamp")
    plan = scene.plan(w, {"holds": [{"item": "便携照明灯", "holder": "warden"}]},
                      scene.Context(travel=lambda a, b: 0 if a == b else None))
    assert plan.problems
    scene.land(w, plan.held)
    assert w.entity(first).at == w.entity(second).at == w.st.investigator.id
    # Restating the same already-recorded name as a daily prop cannot turn the
    # ambiguous handover into a new-object fallback that selects the first item.
    restated = scene.plan(w, {"holds": [{"item": "便携照明灯", "holder": "warden"}],
                              "things": [{"name": "便携照明灯", "holder": w.st.investigator.id}]},
                          scene.Context(travel=lambda a, b: 0 if a == b else None))
    assert restated.problems
    scene.land(w, restated.held)
    assert w.entity(first).at == w.entity(second).at == w.st.investigator.id
    assert len([e for e in w.rec.entities.values() if e.kind == "item" and e.name == "便携照明灯"]) == 2


def test_scene_holds_uses_the_explicit_id_in_a_display_name_with_brackets():
    w = fresh("tower")
    first = important_tool(w)
    second = important_tool(w, ident="spare_lamp")
    plan = scene.plan(w, {"holds": [{"item": "便携照明灯（spare_lamp）", "holder": "warden"}]},
                      scene.Context(travel=lambda a, b: 0 if a == b else None))
    assert not plan.problems
    assert scene.land(w, plan.held)
    assert w.entity(first).at == w.st.investigator.id and w.entity(second).at == "warden"
