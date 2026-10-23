"""Only important items have durable equipment state; possession never reveals an NPC's inventory."""

from autokeeper.agents.director import Improv, NewItem
from autokeeper.engine.state import GameState
from autokeeper.module import improv
from autokeeper.module.schema import Item
from autokeeper.world import adapters, context, knowledge
from autokeeper.world import ops as O
from autokeeper.world.agents import vet
from autokeeper.world.record import PLAYERS
from autokeeper.world.store import World

from .test_world import fresh


def armed(location="tower"):
    w = fresh(location)
    w.m.items.extend([
        Item(id="pistol", name="旧手枪", description="一把保养过的手枪", where="tower", owner="warden", equipment="concealed", hidden=True),
        Item(id="rounds", name="备用弹药", description="一盒备用弹药", where="tower", owner="warden", quantity=2),
        Item(id="tea_cup", name="普通茶杯", description="日常茶杯", where="tower", important=False),
    ])
    w.sync()
    return w


def test_old_item_defaults_remain_valid_and_ordinary_props_are_not_inventory_entities():
    item = Item(id="key", name="铜钥匙", description="铜制", where="tower")
    assert item.important and item.quantity == 1 and item.equipment == "carried" and not item.hidden
    w = armed()
    assert "tea_cup" not in w.rec.entities
    assert all(item.important for item in knowledge.visible_items(w, "warden"))


def test_an_npc_knows_their_equipment_but_nearby_people_do_not_automatically_see_it():
    w = armed()
    assert w.apply(O.Move(entity="preacher", to="tower")).ok
    for mode in ("carried", "worn", "held", "concealed"):
        assert w.apply(O.Equip(item="pistol", equipment=mode, actor="warden")).ok
        assert knowledge.item_known(w, "pistol", "warden")
        assert not knowledge.item_known(w, "pistol", PLAYERS)
        assert not knowledge.item_known(w, "pistol", "preacher")
        assert w.rec.events[-1].visibility == "private"
        assert "旧手枪" in context.npc_view(w, "warden").text
        assert "旧手枪" not in context.npc_view(w, "preacher").text


def test_player_important_equipment_is_private_and_survives_a_save():
    w = armed()
    player = w.st.investigator
    assert w.apply(O.Transfer(item="pistol", to=player.id, actor="warden", visibility="private")).ok
    assert w.apply(O.Equip(item="pistol", equipment="concealed", actor=player.id)).ok
    assert knowledge.item_known(w, "pistol", player.id) and knowledge.item_known(w, "pistol", PLAYERS)
    assert not knowledge.item_known(w, "pistol", "preacher")
    copy = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    assert copy.entity("pistol").at == player.id and copy.entity("pistol").equipment == "concealed"
    assert copy.entity("pistol").hidden
    assert "旧手枪" in player.inventory and player.inventory.count("旧手枪") == 1


def test_equipping_cannot_change_someone_elses_property_or_invent_ammunition():
    w = armed()
    assert not w.apply(O.Equip(item="pistol", equipment="held", actor="preacher")).ok
    assert vet(w, "preacher", O.Equip(item="pistol", equipment="held"))
    assert not w.apply(O.Equip(item="rounds", quantity=3, actor="warden")).ok
    assert not w.apply(O.Equip(item="not_registered", equipment="held", actor="warden")).ok
    assert w.apply(O.Equip(item="rounds", quantity=0, actor="warden")).ok
    assert not w.apply(O.Equip(item="rounds", equipment="held", actor="warden")).ok
    assert not w.apply(O.Transfer(item="rounds", to=w.st.investigator.id)).ok
    assert w.entity("rounds").quantity == 0


def test_discovery_requires_evidence_and_reveals_only_to_actual_participants():
    w = armed()
    assert w.apply(O.Move(entity="preacher", to="tower")).ok
    assert not w.apply(O.Equip(item="pistol", reveal=True)).ok
    assert w.apply(O.Equip(item="pistol", reveal=True, evidence="调查员搜查衣物夹层后发现一把旧手枪。",
                           visibility="private")).ok
    assert knowledge.item_known(w, "pistol", "warden")
    assert not knowledge.item_known(w, "pistol", PLAYERS)
    assert not knowledge.item_known(w, "pistol", "preacher")
    assert w.rec.events[-1].visibility == "private"
    assert w.entity("pistol").equipment == "concealed"  # discovery does not alter physical possession
    remote = w.st.investigator.model_copy(update={"id": "remote", "name": "异地同伴"}, deep=True)
    w.st.investigators.append(remote)
    w.st.apart[remote.id] = "office"
    w.sync()
    # Public disclosure permits actual scene observers; it does not notify the distant party member.
    assert w.apply(O.Equip(item="pistol", reveal=True, evidence="夹层中的旧手枪已向现场展示。", visibility="public")).ok
    assert knowledge.item_known(w, "pistol", w.st.investigator.id)
    assert not knowledge.item_known(w, "pistol", remote.id) and not knowledge.item_known(w, "pistol", PLAYERS)
    w.st.location = "office"
    assert not w.apply(O.Equip(item="pistol", reveal=True, evidence="远处出现了一把手枪。")).ok


def test_public_presentation_informs_present_witnesses_and_private_changes_do_not_telepathically_update_them():
    w = armed()
    assert w.apply(O.Move(entity="preacher", to="tower")).ok
    assert w.apply(O.Equip(item="pistol", equipment="held", actor="warden", reveal=True,
                           evidence="守钟人把旧手枪放在掌中，展示给在场的人。")).ok
    assert knowledge.item_known(w, "pistol", PLAYERS) and knowledge.item_known(w, "pistol", "preacher")
    assert w.rec.events[-1].text in context.happened(w, w.rec.seq - 1)
    assert w.apply(O.Equip(item="pistol", equipment="concealed", actor="warden")).ok
    assert not knowledge.item_known(w, "pistol", PLAYERS) and not knowledge.item_known(w, "pistol", "preacher")
    assert any("展示给在场的人" in event.text for event in w.rec.events)  # prior observation remains in history


def test_important_items_hidden_at_a_place_do_not_appear_just_because_someone_arrives():
    w = armed()
    assert w.apply(O.Transfer(item="pistol", to="tower", actor="warden", visibility="private")).ok
    assert w.entity("pistol").hidden and not knowledge.item_known(w, "pistol", PLAYERS)
    assert w.apply(O.Equip(item="pistol", reveal=True, evidence="在墙角暗格内找到那把旧手枪。")).ok
    assert knowledge.item_known(w, "pistol", PLAYERS)


def test_director_hidden_items_do_not_become_public_features_and_runtime_players_can_own_them():
    w = fresh()
    player = w.st.investigator.model_copy(update={"id": "custom_investigator"}, deep=True)
    w.st.investigators = [player]
    w.sync()
    plan = Improv(kind="展开", reason="登记已有的关键工具", items=[
        NewItem(id="key", name="私藏钥匙", description="一把铜钥匙", where="square", owner=player.id,
                equipment="concealed", hidden=True),
    ])
    assert not improv.check(w.m, plan, at="square", actors={player.id})
    result = adapters.improvise(w, plan)
    assert result.ok and w.entity("key").at == player.id
    assert "私藏钥匙" not in w.m.location("square").features
    assert knowledge.item_known(w, "key", player.id) and not knowledge.item_known(w, "key", "preacher")


def test_explicit_promotion_uses_only_an_existing_inventory_item_and_adds_no_new_possessions():
    w = fresh()
    player = w.st.investigator
    player.inventory.append("便携手电")
    before = list(player.inventory)
    assert "便携手电" not in w.rec.entities
    assert w.apply(O.Equip(item="便携手电", equipment="held", actor=player.id)).ok
    assert player.inventory == before
    assert w.entity("便携手电").at == player.id and w.entity("便携手电").important
    assert not w.apply(O.Equip(item="臆造的自动步枪", equipment="held", actor=player.id)).ok
    assert "臆造的自动步枪" not in player.inventory and "臆造的自动步枪" not in w.rec.entities
    assert w.apply(O.Transfer(item="便携手电", to="warden", actor=player.id, visibility="private")).ok
    assert "便携手电" not in player.inventory and w.entity("便携手电").at == "warden"
    assert w.apply(O.Transfer(item="便携手电", to=player.id, actor="warden", visibility="private")).ok
    assert player.inventory.count("便携手电") == 1


def test_an_important_consumable_is_removed_from_usable_inventory_when_exhausted():
    w = fresh()
    player = w.st.investigator
    player.inventory.append("应急照明棒")
    assert w.apply(O.Equip(item="应急照明棒", quantity=0, actor=player.id)).ok
    assert "应急照明棒" not in player.inventory
    assert w.entity("应急照明棒").quantity == 0
    assert not w.apply(O.Transfer(item="应急照明棒", to="warden")).ok
