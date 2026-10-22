"""A pushed or lucky success completes the original physical action, not just its clues."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, EquipmentChange, Failure, Rejoin, RuledEffect, Ruling, Thing
from autokeeper.engine.encounter import site
from autokeeper.engine.state import GameState, WorldChange
from autokeeper.module.schema import Item, Module
from autokeeper.paths import EXAMPLE
from autokeeper.plugins.schema import Call
from autokeeper.world.ops import Equip

from .test_engine import make
from .test_resolution_recovery import reload_game, snapshots, turn_going


@pytest.fixture
def module():
    m = Module.load(EXAMPLE)
    m.items.append(Item(id="ledger", name="账本", description="一册账本", where="square"))
    return m


@pytest.mark.parametrize("mode", ["luck", {"choice": "push", "how": "拆下锁盖重新拨动锁簧"}])
async def test_second_chance_without_clues_preserves_transfers_gear_and_everyday_things(module, fake, mode):
    game, _, _ = make(module, fake, {})
    inv = game.state.investigator
    inv.skills["锁匠"], inv.luck = 50, 20
    inv.inventory.append("急救包")
    assert game.world.apply(Equip(item="急救包", equipment="carried")).ok
    action = Action(kind="use", summary="打开柜子拿出账本", target="柜门")
    ruling = Ruling(needs_roll=True, skill="锁匠", minutes=8,
                    changes=[WorldChange(kind="transfer", key="take_ledger", text="周衡打开柜子拿出了账本",
                                         item="ledger", owner=inv.id)],
                    equipment=[EquipmentChange(item="急救包", equipment="held")],
                    takes=[Thing(name="火柴")])
    fake.queue(65, 5)
    first = await game.resolve(action, ruling)
    assert first.push and first.push["clues"] == []
    assert "账本" not in inv.inventory and "火柴" not in inv.inventory
    assert game.world.rec.entities["急救包"].equipment == "carried"
    start = game.state.minutes
    second = await game.push(mode)
    assert second.action == action and second.ruling == ruling
    assert inv.inventory.count("账本") == inv.inventory.count("火柴") == 1
    assert game.world.rec.entities["急救包"].equipment == "held"
    assert game.world.rec.entities["ledger"].at == inv.id
    assert inv.luck == (5 if mode == "luck" else 20)
    assert game.state.minutes == start + (0 if mode == "luck" else 10)
    await game.close()


async def test_dropping_an_ordinary_check_applies_its_failure_without_paying_the_success(module, fake):
    game, _, _ = make(module, fake, {})
    fake.queue(65)
    game.state.investigator.skills["锁匠"] = 50
    ruling = Ruling(needs_roll=True, skill="锁匠", takes=[Thing(name="火柴")],
                    on_fail=Failure(text="锁簧断了，耽搁了一会儿", minutes=7))
    first = await game.resolve(Action(kind="use", summary="试着开锁"), ruling)
    assert first.push and "火柴" not in game.state.investigator.inventory
    before = game.state.minutes
    dropped = await game.push("drop")
    assert game.state.minutes == before + 7 and "火柴" not in game.state.investigator.inventory
    assert any("锁簧断了" in note for note in dropped.notes)
    await game.close()


async def test_lucky_success_survives_reload_after_spending_nearly_all_luck(module, fake):
    game, _, _ = make(module, fake, {})
    inv = game.state.investigator
    inv.skills["锁匠"], inv.luck = 50, 20
    action = Action(kind="use", summary="开柜子拿账本")
    ruling = Ruling(needs_roll=True, skill="锁匠", changes=[WorldChange(
        kind="transfer", key="take_ledger", text="周衡拿到账本", item="ledger", owner=inv.id)])
    fake.queue(65)
    turn_going(game, action.summary)
    saved = snapshots(game)
    await game.resolve(action, ruling)

    async def disconnected(event):
        if event["type"] == "luck":
            raise ConnectionError("lost after the luck payment")

    game.emit = disconnected
    with pytest.raises(ConnectionError):
        await game.push("luck")
    assert inv.luck == 5
    resumed, _ = reload_game(module, saved[-1], fake)
    await resumed.push("luck")
    recovered = GameState.model_validate_json(resumed.state.model_dump_json())
    assert recovered.investigator.luck == 5
    assert recovered.investigator.inventory.count("账本") == 1
    assert not fake.values
    await resumed.close()
    await game.close()


@pytest.mark.parametrize("mode", ["luck", {"choice": "push", "how": "拆开门锁，重新对齐锁簧"}])
async def test_second_chance_restores_the_route_reunites_a_companion_and_runs_the_declared_action(module, fake, mode):
    game, _, _ = make(module, fake, {}, "office")
    inv = game.state.investigator
    inv.skills["锁匠"], inv.luck = 50, 20
    game.state.investigators.append(inv.model_copy(update={"id": "li", "name": "李雁"}, deep=True))
    game.state.apart["li"] = "square"
    door = site.build(module, "office").obstacles["ob_office_door"].model_copy(update={"state": "broken"})
    game.world.rec.obstacles["leg:office_door"] = door
    action = Action(kind="use", summary="修好报社的门锁，叫同伴回来")
    ruling = Ruling(needs_roll=True, skill="锁匠", rejoin=[Rejoin(who="li", how="fetched")],
                    effects=[RuledEffect(kind="obstacle", target="leg:office_door", what="门锁修好了")],
                    plugin_calls=[Call(plugin="alarm", action="reset")])
    calls = []

    async def plugin(hook, turn, **details):
        calls.append((hook, details))

    game.plugins.run = plugin
    fake.queue(65, 5)
    await game.resolve(action, ruling)
    assert "li" in game.state.apart and game.world.rec.obstacles["leg:office_door"].state == "broken"
    assert not [c for c in calls if c[0] == "action"]
    await game.push(mode)
    assert "li" not in game.state.apart and "leg:office_door" not in game.world.rec.obstacles
    assert [c for c in calls if c[0] == "action"] == [("action", {"plugin": "alarm", "action": "reset", "arguments": {}})]
    await game.close()


async def test_luck_without_enough_points_is_not_offered_for_an_unpushable_roll(module, fake):
    game, _, _ = make(module, fake, {})
    inv = game.state.investigator
    inv.luck = 1
    fake.queue(65)
    turn = await game.resolve(Action(kind="other", summary="保持冷静"), Ruling(needs_roll=True, skill="理智"))
    assert turn.push is None and game.state.pending_push is None
    await game.close()
