"""Backstage equipment affects play without publishing NPC inventories."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, EquipmentChange, Ruling
from autokeeper.engine import fog
from autokeeper.module.schema import Item, Module
from autokeeper.net.hub import table
from autokeeper.paths import EXAMPLE
from autokeeper.world import knowledge
from autokeeper.world.ops import Equip
from autokeeper.world.record import PLAYERS

from .test_engine import make


@pytest.fixture
def module():
    m = Module.load(EXAMPLE)
    m.items.append(Item(id="hidden_key", name="私藏的铁钥匙", description="一把锈铁钥匙", where="tower", owner="warden",
                        equipment="concealed", hidden=True, quantity=2))
    return m


async def test_a_players_important_equipment_updates_from_a_successful_action(module, fake):
    game, _, _ = make(module, fake, {})
    game.state.investigator.inventory.append("手电筒")
    ruling = Ruling(needs_roll=False, equipment=[EquipmentChange(item="手电筒", equipment="held")])
    await game.resolve(Action(kind="use", summary="拿出手电筒", target="手电筒"), ruling)
    gear = next(e for e in game.state.record.entities.values() if e.kind == "item" and e.name == "手电筒")
    assert gear.at == game.state.investigator.id and gear.equipment == "held"
    assert game.state.investigator.inventory.count("手电筒") == 1


async def test_failed_action_does_not_consume_an_important_item(module, fake):
    game, _, _ = make(module, fake, {})
    game.state.investigator.inventory.append("急救包")
    assert game.world.apply(Equip(item="急救包", equipment="carried")).ok
    fake.queue(99)
    ruling = Ruling(needs_roll=True, skill="急救", equipment=[EquipmentChange(item="急救包", quantity=0)])
    await game.resolve(Action(kind="use", summary="尝试急救", target="急救包"), ruling)
    assert game.state.record.entities["急救包"].quantity == 1
    assert "急救包" in game.state.investigator.inventory


def test_public_module_and_map_do_not_show_hidden_npc_equipment(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    before = game.state.model_dump_json()
    assert "hidden_key" not in {i["id"] for i in table(module, game.state)["items"]}
    assert "hidden_key" not in fog.things(module, game.state)
    assert not game._items_for(Action(kind="investigate", summary="搜钥匙", target="私藏的铁钥匙"))
    assert game.state.model_dump_json() == before  # publishing a view is read-only


async def test_a_successful_search_reveals_an_item_without_taking_it(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    ruling = Ruling(needs_roll=False, equipment=[EquipmentChange(item="hidden_key", reveal=True,
                                                                 evidence="守钟人打开内袋，取出一把锈铁钥匙。")])
    await game.resolve(Action(kind="investigate", summary="请守钟人出示钥匙", npc="warden"), ruling)
    assert knowledge.item_known(game.world, "hidden_key", PLAYERS)
    assert game.state.record.entities["hidden_key"].at == "warden"
    assert "私藏的铁钥匙" not in game.state.investigator.inventory
    shown = next(i for i in table(module, game.state)["items"] if i["id"] == "hidden_key")
    assert not shown["hidden"] and shown["quantity"] == 1  # no private carrying state or exact remaining quantity


async def test_player_cannot_rearrange_someone_elses_hidden_equipment(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    ruling = Ruling(needs_roll=False, equipment=[EquipmentChange(item="hidden_key", equipment="held")])
    await game.resolve(Action(kind="use", summary="让守钟人把钥匙拿在手里"), ruling)
    assert game.state.record.entities["hidden_key"].equipment == "concealed"
    assert not knowledge.item_known(game.world, "hidden_key", PLAYERS)
