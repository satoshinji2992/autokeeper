"""Bodies and weapons live in the world: the encounter engine reads them from the record and writes
every wound, death, round and jam through engine/effects, so the next fight finds them as the last
one left them. These are the review probes of 2026-10-21 turned around
(dev/output/review/test_battle_review_20261021.py): they assert what should happen."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import RuledEffect, Ruling
from autokeeper.engine import combat as battle
from autokeeper.engine.encounter.actions import Ctx
from autokeeper.engine.encounter.plan import DeclaredAction, EncounterStart, EncounterTurnPlan
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Creature, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.chase import Leg, Obstacle, Track

from .encounter_kit import arrange, begin, fight_game

RIFLE = "杠杆步枪"
GUN = ".38 左轮手枪"
DEFEND = DeclaredAction(do="defend", summary="守着")


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


def ghoul(module: Module, hp: int = 12) -> None:
    module.creatures.append(Creature(id="ghoul", name="食尸鬼", description="墓园中的食尸鬼", sanity="0/1", stats={"HP": hp},
                                     attacks=["爪击 50%，伤害 1D6"]))


def table(*npcs):
    return lambda *a: EncounterTurnPlan(npcs=list(npcs))


async def act(game, text: str) -> Turn:
    """One round, as the player's choice in the screen's words; nobody stops to ask."""
    game.asking = False
    game.state.turn += 1  # a new turn, as Game.parse makes it
    action = await game.encounter_input(text)
    turn = await game.resolve(action, await game.rule(action))
    return turn


async def over(game) -> None:
    await game._encounter_end("dispersed", Turn())
    await game.archive_encounter()


def option(game, who: str, name: str):
    return next(a for a in Ctx.of(game).view(who).attacks() if a.name == name)


async def test_rounds_fired_and_a_jam_carry_into_the_next_fight(module, fake):
    game, _cast, _ = fight_game(module, fake, arrange=table(arrange("warden", DEFEND)))
    inv = game.state.investigator
    inv.inventory.append(RIFLE)
    await begin(game, ["warden"])
    shoot = f"射击 楼梯口打盹的老人（{RIFLE}）｜被攻击时闪避"
    fake.queue(90)  # a miss: one round gone
    await act(game, shoot)
    gun = game.world.rec.entities[RIFLE]
    assert gun.at == inv.id and gun.weapon.loaded == 5 and not gun.weapon.jammed  # promoted on first use, its rounds kept
    fake.queue(99)  # at its malfunction number: a lever action jams (Keeper Rulebook p. 98)
    await act(game, shoot)
    assert gun.weapon.jammed and gun.weapon.loaded == 5
    await over(game)
    await begin(game, ["warden"])
    again = option(game, inv.id, RIFLE)
    assert again.loaded == 5 and again.jammed  # no fight reloads or clears a gun by itself


async def test_a_revolver_that_misfires_only_had_a_dud(module, fake):
    game, _cast, _ = fight_game(module, fake, arrange=table(arrange("warden", DEFEND)))
    game.state.investigator.inventory.append(GUN)
    await begin(game, ["warden"])
    fake.queue(100)
    await act(game, f"射击 楼梯口打盹的老人（{GUN}）｜被攻击时闪避")
    gun = game.world.rec.entities[GUN]
    assert gun.weapon.loaded == 5 and not gun.weapon.jammed


async def test_a_disarmed_weapon_leaves_the_inventory_and_lies_on_the_floor(module, fake):
    grab = DeclaredAction(do="maneuver", maneuver="缴械", attacks=[{"target": "zhouheng"}], summary="守钟人去夺枪")
    game, _cast, _ = fight_game(module, fake, arrange=table(arrange("warden", grab)))
    inv = game.state.investigator
    inv.inventory.append(GUN)
    await begin(game, ["warden"])
    fake.queue(10, 90)  # the warden's grab succeeds; 周衡's dodge fails
    await act(game, "防守｜被攻击时闪避")
    assert GUN not in inv.inventory and game.world.rec.entities[GUN].at == "tower"
    assert not any(a.firearm for a in Ctx.of(game).view(inv.id).attacks())


async def test_claws_cannot_be_disarmed(module, fake):
    ghoul(module)
    game, _cast, _ = fight_game(module, fake, arrange=table(arrange("ghoul", DEFEND)))
    fake.queue(30)
    await begin(game, ["ghoul"])
    fake.queue(10, 90)
    turn = await act(game, "缴械 食尸鬼｜被攻击时闪避")
    assert [a.name for a in Ctx.of(game).view("ghoul").attacks()] == ["爪击"] and any("夺不走" in line for line in turn.fight)


async def test_a_dead_creature_stays_dead(module, fake):
    ghoul(module, hp=3)
    game, _cast, _ = fight_game(module, fake, arrange=table(arrange("ghoul", DEFEND)))
    fake.queue(30)
    await begin(game, ["ghoul"])
    assert game.world.rec.entities["ghoul"].kind == "creature"  # a world entity from the moment it fights
    fake.queue(10, 90).queue_totals(3)  # 周衡 hits; its answer fails; 3 damage
    await act(game, "攻击 食尸鬼（徒手）｜被攻击时闪避")
    body = game.world.rec.entities["ghoul"].life
    assert body.hp == 0 and "死亡" in body.conditions
    assert game.state.encounter is None and game.state.encounter_log[-1].ended.how == "won"  # into the log as it ended
    assert not await game._encounter_open(EncounterStart(mode="fight", foes=["ghoul"], pace=6, reason="又来"), Turn())


async def test_a_hurt_npc_comes_back_hurt(module, fake):
    game, _cast, _ = fight_game(module, fake, arrange=table(arrange("warden", DEFEND)))
    await begin(game, ["warden"])
    full = Ctx.of(game).view("warden").body().hp_max
    fake.queue(10, 90).queue_totals(4)
    await act(game, "攻击 楼梯口打盹的老人（徒手）｜被攻击时闪避")
    await over(game)
    await begin(game, ["warden"])
    assert Ctx.of(game).view("warden").body().hp == full - 4 and game.world.rec.entities["warden"].life.hp == full - 4


async def test_a_ruled_damage_that_is_no_expression_is_a_note_not_a_locked_save(module, fake):
    lamp = DeclaredAction(do="other", summary="周衡抡起油灯砸过去")
    game, cast, _ = fight_game(module, fake, act=EncounterTurnPlan(action=lamp), arrange=table(arrange("warden", DEFEND)))
    bad = RuledEffect.model_construct(kind="damage", target="warden", dice="1D6点", bonus=0, what="")  # an old journal's
    cast.ruling_for[lamp.summary] = Ruling.model_construct(**{**Ruling(needs_roll=True, skill="投掷").__dict__, "effects": [bad]})
    await begin(game, ["warden"])
    fake.queue(10)
    turn = await act(game, "抡起油灯砸他")
    assert any("伤害式无法使用" in line for line in turn.fight) and game.world.rec.entities["warden"].life.hp == 11


async def test_a_chase_hazard_with_unreadable_harm_still_delays_and_is_noted(module, fake):
    game, _cast, _ = fight_game(module, fake)
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
