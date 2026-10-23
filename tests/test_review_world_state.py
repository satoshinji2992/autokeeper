"""Shared identities and explicit item resolution across multiplayer world operations."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, PushRuling, Ruling
from autokeeper.engine.game import Game
from autokeeper.engine.scenes import SceneView
from autokeeper.engine.state import SceneState, WorldChange
from autokeeper.rules.coc import Rules
from autokeeper.world import adapters
from autokeeper.world.ops import Bring
from autokeeper.world.record import Entity
from autokeeper.world.store import World
from autokeeper.world.tools import Gateway

from .test_engine import FakeCast
from .test_harness import SCRIPT, SEEN, harness_sandbox  # noqa: F401
from .test_npc_acts import setup as npc_setup
from .test_world import fresh


async def test_parallel_npc_handover_preserves_pushed_success_growth_mark(fake):
    w = fresh("office")
    root = w.st
    lead = root.investigator
    lead.skills["侦查"] = 60
    friend = lead.model_copy(update={"id": "friend", "name": "另一名调查员"}, deep=True)
    root.investigators.append(friend)
    root.positions = {lead.id: "office", friend.id: "square"}
    root.scenes = {
        "inside": SceneState(id="inside", members=[lead.id], place="office", at=root.minutes, active=0),
        "outside": SceneState(id="outside", members=[friend.id], place="square", at=root.minutes, active=1),
    }
    entered, resume = asyncio.Event(), asyncio.Event()

    class PausingCast(FakeCast):
        async def repush(self, state, pending, how):
            entered.set()
            await resume.wait()
            return PushRuling(achievable=True, minutes=1)

    game = Game(w.m, SceneView(root, root.scenes["inside"]), PausingCast({}), Rules(dice=fake), restore=False)
    outside = World(w.m, SceneView(root, root.scenes["outside"]))
    fake.queue(90, 5)
    pending = None
    try:
        first = await game.resolve(Action(kind="investigate", summary="查阅书架", target="书架"),
                                   Ruling(needs_roll=True, skill="侦查"))
        assert first.push
        pending = asyncio.create_task(game.push({"choice": "push", "how": "移开书架重新找"}))
        await asyncio.wait_for(entered.wait(), 2)
        result = Gateway(outside, "preacher").settle(
            [{"kind": "bring", "name": "纸杯", "holder": "preacher"},
             {"kind": "transfer", "item": "纸杯", "to": friend.id, "giver": "preacher"}],
            request_id="parallel-cup",
        )
        assert result["ok"], result
        resume.set()
        pushed = await pending
        assert pushed.checks[0].passed
        assert "纸杯" in root.find(friend.id).inventory
        assert "侦查" in root.find(lead.id).marks
    finally:
        resume.set()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await game.close()


def test_serving_soup_does_not_transfer_the_whole_pot():
    w = fresh("tower")
    me = w.st.investigator.id
    assert w.apply(Bring(name="一锅热汤", holder="warden", actor="warden")).ok
    groups, problems = adapters.reply_ops(w, "warden", items=[{"name": "热汤", "to": me}])
    for index, group in enumerate(groups):
        result = Gateway(w, "warden").settle(group, request_id=f"serve-one-bowl:{index}")
        assert result["ok"], result
    # An unresolved portion may be refused or represented separately, never the whole pot.
    assert "一锅热汤" not in w.st.investigator.inventory, problems
    assert any(e.kind == "item" and e.name == "一锅热汤" and e.at == "warden" for e in w.rec.entities.values())


@pytest.mark.parametrize("reverse", [False, True])
def test_ambiguous_important_item_name_requires_disambiguation(reverse):
    w = fresh("tower")
    me = w.st.investigator.id
    objects = [Entity(id="steel-key", name="钢钥匙", kind="item", important=True, at="warden"),
               Entity(id="brass-key", name="黄铜钥匙", kind="item", important=True, at="warden")]
    for item in reversed(objects) if reverse else objects:
        w.rec.entities[item.id] = item
    groups, problems = adapters.reply_ops(w, "warden", items=[{"name": "钥匙", "to": me}])
    assert problems and not groups


def test_transferring_one_of_two_same_named_items_keeps_the_other_visible():
    w = fresh("tower")
    me = w.st.investigator.id
    for ident in ("key-a", "key-b"):
        w.rec.entities[ident] = Entity(id=ident, name="钥匙", kind="item", important=True, at=me)
        w.st.item_owners[ident] = me
    w.st.investigator.inventory.append("钥匙")
    result = Gateway(w, "keeper").settle(
        [{"kind": "transfer", "item": "key-a", "to": "warden", "giver": me}], request_id="one-of-two")
    assert result["ok"], result
    assert w.rec.entities["key-a"].at == "warden"
    assert w.rec.entities["key-b"].at == me
    assert "钥匙" in w.st.investigator.inventory


async def test_npc_reply_rejects_an_item_as_its_own_holder(fake):
    game, cast = npc_setup(fake)
    game.world.rec.entities["key-a"] = Entity(id="key-a", name="钥匙", kind="item", important=True, at="warden")
    reply = {"text": "钥匙给您。", "items": [{"name": "钥匙", "to": "key-a"}]}
    SCRIPT.extend([reply, reply, reply])  # enough attempts if a repair asks for a valid recipient
    try:
        assert await cast.npc(game.state, "warden", "把钥匙给我。") == "钥匙给您。"
        assert game.world.rec.entities["key-a"].at == "warden"
        assert game.world.place("key-a") == "tower"
    finally:
        await game.close()


def test_world_gateway_preserves_live_record_and_module_entity_references():
    w = fresh("tower")
    record, entities, npc, sheet = w.rec, w.rec.entities, w.entity("warden"), w.m.npc("warden")
    result = Gateway(w, "warden").settle(
        [{"kind": "move", "entity": "warden", "to": "square"}], request_id="walk-out")
    assert result["ok"], result
    assert w.rec is record and w.rec.entities is entities
    assert w.entity("warden") is npc and npc.at == "square"
    assert w.m.npc("warden") is sheet and sheet.location == "square"


async def test_npc_can_clarify_which_important_key_it_hands_over(fake):
    game, cast = npc_setup(fake)
    me = game.state.investigator.id
    game.world.rec.entities.update({
        "steel-key": Entity(id="steel-key", name="钢钥匙", kind="item", at="warden"),
        "brass-key": Entity(id="brass-key", name="黄铜钥匙", kind="item", at="warden"),
    })
    SCRIPT.extend([
        {"text": "钥匙给您。", "items": [{"name": "钥匙", "to": me}]},
        {"text": "黄铜的这把给您。", "items": [{"name": "brass-key", "to": me}]},
    ])
    try:
        assert await cast.npc(game.state, "warden", "把钥匙给我。") == "黄铜的这把给您。"
        assert len(SEEN) == 2 and "steel-key" in SEEN[-1]["messages"][-1]["content"]
        assert game.world.rec.entities["brass-key"].at == me
        assert game.world.rec.entities["steel-key"].at == "warden"
        assert not cast.unsettled
    finally:
        await game.close()


async def test_npc_can_register_a_serving_without_moving_its_container(fake):
    game, cast = npc_setup(fake)
    me = game.state.investigator.id
    assert game.world.apply(Bring(name="一锅热汤", holder="warden", actor="warden")).ok
    SCRIPT.extend([
        {"text": "给您盛一碗。", "items": [{"name": "热汤", "to": me}]},
        {"text": "给您盛一碗。", "items": [{"name": "一碗热汤", "to": me}]},
    ])
    try:
        assert await cast.npc(game.state, "warden", "给我一碗汤。") == "给您盛一碗。"
        assert len(SEEN) == 2 and not cast.unsettled
        assert game.state.investigator.inventory == ["一碗热汤"]
        assert any(e.name == "一锅热汤" and e.at == "warden" for e in game.world.rec.entities.values())
    finally:
        await game.close()


def test_separately_named_portions_do_not_collapse_to_one_object():
    w = fresh("tower")
    assert w.apply(Bring(name="一杯热茶", holder="warden", actor="warden")).ok
    second = w.apply(Bring(name="一碗热茶", holder="warden", actor="warden"))
    assert second.ok, second.problems
    assert {e.name for e in w.rec.entities.values() if e.kind == "item" and e.at == "warden"} >= {"一杯热茶", "一碗热茶"}


def test_location_lookup_does_not_recurse_forever_on_cyclic_record():
    w = fresh("tower")
    w.rec.entities.update({
        "a": Entity(id="a", name="物件甲", kind="item", at="b"),
        "b": Entity(id="b", name="物件乙", kind="item", at="a"),
    })
    assert w.place("a") == w.place("b") == ""


def test_legacy_transfer_keeps_a_still_owned_same_named_item_visible():
    from autokeeper.engine import world

    w = fresh("tower")
    me = w.st.investigator.id
    for ident in ("key-a", "key-b"):
        w.rec.entities[ident] = Entity(id=ident, name="钥匙", kind="item", at=me)
        w.st.item_owners[ident] = me
    w.st.investigator.inventory.append("钥匙")
    world.apply(w.m, w.st, [WorldChange(kind="transfer", key="give-key", item="key-a", owner="warden",
                                      text="把其中一把钥匙交给守钟人。")], source="arbiter")
    assert w.rec.entities["key-a"].at == "warden"
    assert w.rec.entities["key-b"].at == me
    assert "钥匙" in w.st.investigator.inventory
