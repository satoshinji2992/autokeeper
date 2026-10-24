"""Item truth changes cannot rewrite another seat's recorded observations."""

from __future__ import annotations

import pytest

from autokeeper.engine import fog
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Item, Module
from autokeeper.net.hub import Hub, known_items, public_card, table
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import knowledge
from autokeeper.world.ops import Bring, Equip, Occur, Transfer
from autokeeper.world.record import PLAYERS
from autokeeper.world.store import World

from .test_net import TableCast
from .test_personal_map_projection import split_world


@pytest.fixture
async def item_hub(fake):
    module = Module.load(EXAMPLE)
    first = module.pregens[0].model_copy(deep=True)
    second = first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [first, second], human=[first.id, second.id])
    state.location, state.visited = "tower", ["tower"]
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    hub.m.items.append(Item(id="case", name="旧皮箱", description="皮面磨损的棕色箱子", where="tower", owner="warden",
                            art="case-old", quantity=3))
    hub.m.art["case-old"] = "[ OLD LEATHER CASE ]"
    hub.game.world.sync()
    try:
        yield hub
    finally:
        await hub.game.close()


def operate(world: World, operation):
    result = world.apply(operation)
    assert result.ok, result.problems
    return result


def shown_item(hub: Hub, viewer: str) -> dict:
    return next(item for item in table(hub.m, hub.state, viewer)["items"] if item["id"] == "case")


def lend_and_return(hub: Hub) -> None:
    world = hub.game.world
    operate(world, Transfer(id="show-case", item="case", giver="warden", to="zhouheng", actor="warden"))
    operate(world, Transfer(id="return-case", item="case", giver="zhouheng", to="warden", actor="zhouheng"))


async def test_unseen_transfer_keeps_item_knowledge_and_last_owner_in_every_network_view(item_hub):
    hub, world = item_hub, item_hub.game.world
    lend_and_return(hub)
    before = shown_item(hub, "zhouheng")
    operate(world, Transfer(id="secret-case", item="case", giver="warden", to="lin", actor="warden", visibility="private"))
    assert "case" in known_items(hub.m, hub.state, "zhouheng")
    assert shown_item(hub, "zhouheng") == before
    assert hub._view("zhouheng")["item_owners"]["case"] == "warden"
    assert hub._view("lin")["item_owners"]["case"] == "lin"
    assert shown_item(hub, "lin")["owner"] == "lin"
    assert not hub.state.record.entities["case"].observed_by.count("zhouheng")
    # Public knowledge is also the last public observation, not this private transfer.
    assert hub._view()["item_owners"]["case"] == "warden"


async def test_secret_equipment_and_module_changes_do_not_replace_previous_observation(item_hub):
    hub, world = item_hub, item_hub.game.world
    lend_and_return(hub)
    before = shown_item(hub, "zhouheng")
    operate(world, Equip(id="hidden-use", item="case", quantity=0, status="已烧毁", equipment="concealed", actor="warden"))
    sheet = next(item for item in hub.m.items if item.id == "case")
    sheet.name, sheet.description, sheet.art, sheet.where = "烧毁的箱子", "只剩灰烬", "case-burned", "office"
    world.entity("case").name = sheet.name
    assert shown_item(hub, "zhouheng") == before
    remembered = knowledge.item_view(world, "case", "zhouheng")
    assert (remembered.quantity, remembered.status, remembered.hidden) == (3, "", False)
    assert remembered.version < world.entity("case").version
    operate(world, Equip(id="show-remains", item="case", reveal=True, evidence="守钟人掀开布，露出烧毁的皮箱。", actor="warden"))
    after = shown_item(hub, "zhouheng")
    assert after["name"] == "烧毁的箱子" and after["quantity"] == 0 and after["status"] == "已烧毁"
    assert after["description"] == "只剩灰烬" and hub.state.visuals[after["art"]]["source"] == "case-burned"


async def test_private_transfer_does_not_clear_another_seats_last_known_teammate_inventory(item_hub):
    hub, world = item_hub, item_hub.game.world
    operate(world, Transfer(id="public-gift", item="case", giver="warden", to="lin", actor="warden"))
    before = public_card(hub.state.investigators[1].model_dump(mode="json"), "zhouheng", hub.m, hub.state)["inventory"]
    assert "旧皮箱" in before
    operate(world, Transfer(id="private-return", item="case", giver="lin", to="warden", actor="lin", visibility="private"))
    assert "旧皮箱" not in hub.state.investigators[1].inventory
    after = public_card(hub.state.investigators[1].model_dump(mode="json"), "zhouheng", hub.m, hub.state)["inventory"]
    assert after == before
    assert shown_item(hub, "zhouheng")["owner"] == "lin"
    assert hub._view("zhouheng")["item_owners"]["case"] == "lin"


async def test_private_inventory_removal_cannot_reorder_the_observers_remaining_items(item_hub):
    hub, world = item_hub, item_hub.game.world
    hub.m.items.append(Item(id="letter", name="信封", description="一个信封", where="tower", owner="warden"))
    world.sync()
    operate(world, Transfer(id="gift-case", item="case", giver="warden", to="lin", actor="warden"))
    operate(world, Transfer(id="gift-letter", item="letter", giver="warden", to="lin", actor="warden"))
    before = public_card(hub.state.investigators[1].model_dump(mode="json"), "zhouheng", hub.m, hub.state)["inventory"]
    operate(world, Transfer(id="secret-return-case", item="case", giver="lin", to="warden", actor="lin", visibility="private"))
    assert public_card(hub.state.investigators[1].model_dump(mode="json"), "zhouheng", hub.m, hub.state)["inventory"] == before


async def test_observed_floor_item_remains_at_last_known_place_after_private_removal(item_hub):
    hub, world = item_hub, item_hub.game.world
    operate(world, Transfer(id="case-on-floor", item="case", giver="warden", to="tower", actor="warden"))
    operate(world, Occur(id="floor-look", text="调查员查看地上的皮箱。", place="tower"))
    before = shown_item(hub, "zhouheng")
    operate(world, Transfer(id="hidden-pickup", item="case", giver="tower", to="lin", actor="lin", visibility="private"))
    assert shown_item(hub, "zhouheng") == before
    assert before["where"] == "tower" and not before["owner"]
    assert hub._view("zhouheng")["item_owners"]["case"] == "tower"
    assert fog.things(hub.m, fog.personal(hub.m, hub.state, "zhouheng"))["case"] == "tower"
    assert "case" not in fog.things(hub.m, fog.personal(hub.m, hub.state, "lin"))


async def test_current_owner_can_inspect_private_equipment_and_observations_survive_save_load(item_hub):
    hub, world = item_hub, item_hub.game.world
    lend_and_return(hub)
    before = shown_item(hub, "zhouheng")
    operate(world, Transfer(id="private-gift", item="case", giver="warden", to="lin", actor="warden", visibility="private"))
    operate(world, Equip(id="private-supply", item="case", quantity=2, equipment="concealed", status="锁好", actor="lin"))
    own = shown_item(hub, "lin")
    assert (own["owner"], own["quantity"], own["equipment"], own["status"]) == ("lin", 2, "concealed", "锁好")
    restored = World(hub.m.model_copy(deep=True), GameState.model_validate_json(hub.state.model_dump_json()))
    assert next(item for item in table(restored.m, restored.st, "zhouheng")["items"] if item["id"] == "case") == before
    assert restored.rec.item_sightings["zhouheng"]["case"].event == "ev:return-case"
    assert knowledge.item_view(restored, "case", "lin").quantity == 2


async def test_merely_entering_a_room_does_not_reveal_an_npcs_important_inventory(item_hub):
    hub, world = item_hub, item_hub.game.world
    operate(world, Occur(id="enter-room", text="调查员环顾钟楼。", place="tower"))
    assert "case" not in known_items(hub.m, hub.state, "zhouheng")
    assert "case" not in world.rec.item_sightings.get("zhouheng", {})
    assert knowledge.item_view(world, "case", "warden").quantity == 3


async def test_replacing_an_item_image_does_not_repaint_a_previous_private_observation(item_hub):
    hub, world = item_hub, item_hub.game.world
    lend_and_return(hub)
    previous = shown_item(hub, "zhouheng")["art"]
    hub.m.art["case-old"] = "[ CHARRED REMAINS ]"
    operate(world, Equip(id="burn-case-secretly", item="case", status="烧毁", actor="warden"))
    assert shown_item(hub, "zhouheng")["art"] == previous
    assert hub.m.art[previous] == "[ OLD LEATHER CASE ]"
    operate(world, Equip(id="show-case-damage", item="case", reveal=True, evidence="守钟人展示皮箱的焦痕。", actor="warden"))
    current = shown_item(hub, "zhouheng")["art"]
    assert current != previous and hub.m.art[current] == "[ CHARRED REMAINS ]"


def test_floor_maps_use_each_viewers_observation_and_do_not_invent_common_knowledge():
    world = split_world()
    world.m.items.append(Item(id="case", name="旧皮箱", description="皮面磨损的箱子", where="tower"))
    world.sync()
    operate(world, Occur(id="zhou-sees-case", text="钟楼角落放着旧皮箱。", place="tower"))
    zhou = fog.personal(world.m, world.st, "zhouheng")
    lin = fog.personal(world.m, world.st, "lin")
    assert lin.investigator.id == "lin" and world.st.investigator.id == "zhouheng"
    assert fog.things(world.m, zhou)["case"] == "tower"
    assert "case" not in fog.things(world.m, lin)
    assert "case" not in fog.things(world.m, fog.personal(world.m, world.st, None), viewer=PLAYERS)
    assert "case" not in known_items(world.m, world.st, "lin")
    assert "case" not in known_items(world.m, world.st, None)
    operate(world, Transfer(id="private-case-to-office", item="case", giver="tower", to="office",
                            actor="keeper", visibility="private"))
    # A private move reveals neither its destination nor a new disappearance to Zhou.
    assert fog.things(world.m, fog.personal(world.m, world.st, "zhouheng"))["case"] == "tower"
    assert "case" not in fog.things(world.m, fog.personal(world.m, world.st, "lin"))
    operate(world, Equip(id="uncover-office-case", item="case", equipment="carried", actor="keeper"))
    operate(world, Occur(id="lin-sees-case", text="林秋揭开布，看到一只旧皮箱。", place="office"))
    assert fog.things(world.m, fog.personal(world.m, world.st, "lin"))["case"] == "office"
    assert fog.things(world.m, fog.personal(world.m, world.st, "zhouheng"))["case"] == "tower"
    assert "case" not in fog.things(world.m, fog.personal(world.m, world.st, None), viewer=PLAYERS)


@pytest.mark.parametrize("kind", ["transfer", "bring"])
def test_a_locally_public_item_action_does_not_inform_a_distant_character(kind):
    world = split_world()
    if kind == "transfer":
        world.m.items.append(Item(id="case", name="旧皮箱", description="一个皮箱", where="tower", owner="warden"))
        world.sync()
        result = operate(world, Transfer(id="local-handover", item="case", giver="warden", to="zhouheng",
                                         actor="warden", visibility="public"))
        ident = "case"
    else:
        result = operate(world, Bring(id="local-object", name="黄铜杯", holder="warden", actor="warden", visibility="public"))
        ident = result.data
    assert "zhouheng" in world.rec.event(result.event).witnesses
    assert "lin" not in world.rec.event(result.event).witnesses
    assert knowledge.item_view(world, ident, "zhouheng") is not None
    assert knowledge.item_view(world, ident, "lin") is None
    assert PLAYERS not in world.entity(ident).observed_by
    assert ident not in world.rec.item_sightings.get(PLAYERS, {})


async def test_local_reveal_does_not_refresh_distant_past_observers_or_common_snapshot(item_hub):
    hub, world = item_hub, item_hub.game.world
    lend_and_return(hub)
    old = shown_item(hub, "lin")
    common = world.rec.item_sightings[PLAYERS]["case"].model_dump()
    hub.state.apart["lin"] = "office"
    next(item for item in hub.m.items if item.id == "case").description = "皮箱底下出现了一个焦黑洞口。"
    # No Equip state field changes: a former observer must still not learn newly visible details remotely.
    operate(world, Equip(id="local-inspection", item="case", reveal=True, evidence="守钟人抬起皮箱，露出底下的洞口。", actor="warden"))
    assert shown_item(hub, "lin") == old
    assert world.rec.item_sightings[PLAYERS]["case"].model_dump() == common
    assert "lin" not in world.entity("case").observed_by and PLAYERS not in world.entity("case").observed_by
    assert shown_item(hub, "zhouheng")["description"] == "皮箱底下出现了一个焦黑洞口。"
    hub.state.apart.clear()
    operate(world, Equip(id="share-inspection", item="case", reveal=True, evidence="守钟人给两人展示皮箱的洞口。", actor="warden"))
    assert PLAYERS in world.entity("case").observed_by
    assert shown_item(hub, "lin")["description"] == "皮箱底下出现了一个焦黑洞口。"


def test_an_empty_party_does_not_vacuously_learn_an_item_handover():
    module = Module.load(EXAMPLE)
    module.items.append(Item(id="case", name="旧皮箱", description="一个皮箱", where="tower", owner="warden"))
    world = World(module, Game.new(module, [], human=[]))
    operate(world, Transfer(id="unattended-handover", item="case", giver="warden", to="preacher", actor="warden",
                            visibility="public"))
    assert PLAYERS not in world.entity("case").observed_by
    assert not world.rec.item_sightings.get(PLAYERS)
