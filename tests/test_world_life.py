"""Bodies and weapons live in the world (0.0.4 M1): the old fight and chase engine reads them from
the record and writes every wound, death, round and jam through engine/effects, so the next fight
finds them as the last one left them. These are the review probes of 2026-10-21 turned around
(dev/output/review/test_battle_review_20261021.py): they assert what should happen."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import combat as battle
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Creature, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.chase import Leg, Obstacle, Track
from autokeeper.world.record import Life

from .test_engine import make

GUN = ".38 左轮手枪"


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


def fight(game, *foes: str, orders: dict | None = None):
    """A fight at the tower, built from the world as the engine builds one."""
    st = game.state
    me = battle.from_investigator(st.investigator, game._carried_by(st.investigator.id), world=game.world)
    others = game._others(list(foes))
    st.fight = battle.Fight(fighters=[me, *others], place="tower", started=st.turn, round=1,
                            orders={k: battle.Order.model_validate(v) for k, v in (orders or {}).items()})
    return st.fight, me, others


def ghoul(module: Module, hp: int = 12) -> None:
    module.creatures.append(Creature(id="ghoul", name="食尸鬼", description="墓园中的食尸鬼", stats={"HP": hp},
                                     attacks=["爪击 50%，伤害 1D6"]))


async def test_rounds_fired_and_a_jam_carry_into_the_next_fight(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    inv.inventory.append(GUN)
    f, me, _ = fight(game, "warden", orders={inv.id: {"do": "shoot", "target": "warden"}})
    fake.queue(90)  # a miss: one round gone
    game._fight_act(inv.id, Turn(), None)
    assert next(a for a in me.attacks if a.firearm).ammo == 5
    gun = game.world.rec.entities[GUN]
    assert gun.at == inv.id and gun.weapon.loaded == 5 and not gun.weapon.jammed  # promoted on first use, its rounds kept
    f.round += 1
    fake.queue(100)  # the gun jams
    game._fight_act(inv.id, Turn(), None)
    await game._fight_end(Turn(), "won", [])
    again = battle.from_investigator(inv, game._carried_by(inv.id), world=game.world)
    rebuilt = next(a for a in again.attacks if a.firearm)
    assert rebuilt.ammo == 5 and rebuilt.jammed  # no fight reloads or clears a gun by itself


async def test_a_disarmed_weapon_leaves_the_inventory_and_lies_on_the_floor(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    inv.inventory.append(GUN)
    _, me, _ = fight(game, "warden", orders={"warden": {"do": "maneuver", "target": inv.id, "maneuver": "缴械"}})
    assert me.wielding.name == GUN
    fake.queue(10, 90)  # the warden's grab succeeds; 周衡's dodge fails
    game._fight_act("warden", Turn(), None)
    assert not any(a.firearm for a in me.attacks)
    assert GUN not in inv.inventory and game.world.rec.entities[GUN].at == "tower"
    assert not any(a.firearm for a in battle.from_investigator(inv, game._carried_by(inv.id), world=game.world).attacks)


async def test_claws_cannot_be_disarmed(module, fake):
    ghoul(module)
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    _, _, (beast,) = fight(game, "ghoul", orders={inv.id: {"do": "maneuver", "target": "ghoul", "maneuver": "缴械"}})
    fake.queue(10, 90)
    out = game._fight_act(inv.id, Turn(), None)
    assert [a.name for a in beast.attacks] == ["爪击"] and "夺不走" in out["events"][-1]["lines"][-1]


async def test_a_dead_creature_stays_dead(module, fake):
    ghoul(module, hp=3)
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    _, _, (beast,) = fight(game, "ghoul", orders={inv.id: {"do": "attack", "target": "ghoul", "weapon": "徒手"}})
    assert game.world.rec.entities["ghoul"].kind == "creature"  # a world entity from the moment it fights
    fake.queue(10, 90).queue_totals(3)  # 周衡 hits; its dodge fails; 3 damage
    game._fight_act(inv.id, Turn(), None)
    assert beast.status == "死亡"
    await game._fight_end(Turn(), "won", [])
    body = game.world.rec.entities["ghoul"].life
    assert body.hp == 0 and "死亡" in body.conditions
    assert game._fight_begin(Ruling(needs_roll=False, combat=["ghoul"]), Action(kind="attack", summary="攻击食尸鬼")) == []


async def test_a_hurt_npc_comes_back_hurt(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    _, _, (warden,) = fight(game, "warden", orders={inv.id: {"do": "attack", "target": "warden", "weapon": "徒手"}})
    full = warden.hp_max
    fake.queue(10, 90).queue_totals(4)
    game._fight_act(inv.id, Turn(), None)
    await game._fight_end(Turn(), "won", [])
    _, _, (again,) = fight(game, "warden")
    assert again.hp == full - 4 and game.world.rec.entities["warden"].life.hp == full - 4


async def test_a_model_written_damage_that_is_no_expression_is_a_note_not_a_locked_save(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    fight(game, "warden", orders={inv.id: {"do": "other", "text": "抡起油灯砸过去"}})
    ruling = Ruling(needs_roll=True, skill="投掷", harm="1D6点", harm_to="warden")
    fake.queue(10)
    out = game._fight_act(inv.id, Turn(), ruling)
    assert "伤害式无法使用" in out["events"][-1]["lines"][-1]
    fake.queue(10).queue_totals(3)
    game.state.fight.round += 1
    out = game._fight_act(inv.id, Turn(), ruling.model_copy(update={"harm": "1D6+DB"}))  # the bonus put in by the engine
    assert "受到 1D6" in out["events"][-1]["lines"][-1]


async def test_a_chase_hazard_with_unreadable_harm_still_delays_and_is_noted(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    me = battle.from_investigator(inv, world=game.world)
    (warden,) = game._others(["warden"])
    track = Track(legs=[Leg(name=f"道路{i}") for i in range(6)])
    track.legs[1].obstacle = Obstacle(kind="hazard", what="结冰的台阶", skill="敏捷", harm="1D3（滑倒）")
    me.pos, me.actions = 0, 2
    game.state.chase = battle.Chase(runners=[me, warden], track=track, quarry="party")
    fake.queue(99).queue_totals(1)  # slips: one movement action lost
    turn = Turn()
    game._chase_act(inv.id, turn)
    assert inv.hp == inv.hp_max and any("伤害式无法使用" in line for line in turn.fight)


async def test_an_act_cut_short_keeps_nothing_and_its_retry_lands_once(module, fake, monkeypatch):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    _, _, (warden,) = fight(game, "warden", orders={inv.id: {"do": "attack", "target": "warden", "weapon": "徒手"}})
    full = warden.hp
    real = battle.view
    calls = {"n": 0}

    def broken(fight):
        calls["n"] += 1
        raise RuntimeError("连接断了")

    monkeypatch.setattr(battle, "view", broken)
    fake.queue(10, 90).queue_totals(4)
    with pytest.raises(RuntimeError):
        game._fight_act(inv.id, Turn(), None)
    assert warden.hp == full and game.world.rec.entities["warden"].life.hp == full  # nothing of it kept
    monkeypatch.setattr(battle, "view", real)
    fake.queue(10, 90).queue_totals(4)  # the journal would replay the same dice
    game._fight_act(inv.id, Turn(), None)
    assert warden.hp == full - 4 and game.world.rec.entities["warden"].life.hp == full - 4 and calls["n"] == 1


async def test_a_fighter_the_world_had_no_body_for_is_adopted_as_it_stands(module, fake):
    """A fight built without the world (an old save's fighters) hands its numbers to the world."""
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    me, foe = battle.from_investigator(inv), battle.from_npc(game.m.npc("warden"))
    foe.hp = 3
    game.state.fight = battle.Fight(fighters=[me, foe], place="tower", started=1, round=1,
                                    orders={inv.id: battle.Order(do="attack", target="warden", weapon="徒手")})
    fake.queue(10, 90).queue_totals(1)
    game._fight_act(inv.id, Turn(), None)
    life = game.world.rec.entities["warden"].life
    assert isinstance(life, Life) and (life.hp, life.hp_max) == (2, foe.hp_max) and foe.hp == 2
