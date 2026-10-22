"""The one way damage and its kin reach the game (engine/effects.py): rolled once, landed in one
world op (world/ops.Effects, EncounterStep), never twice, never by a model."""

from __future__ import annotations

import pytest

from autokeeper.engine import effects
from autokeeper.engine.encounter.model import (
    Archive,
    Begin,
    EncounterState,
    EncounterSummary,
    Participant,
    SetCursor,
)
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Creature, Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.plugins.schema import Case, Command, Plugin
from autokeeper.rules.bcdice import Level
from autokeeper.world import desk
from autokeeper.world.ops import EncounterStep, Schedule
from autokeeper.world.record import DIRECTOR, KEEPER
from autokeeper.world.tools import Gateway

from .test_engine import make


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    m.creatures.append(Creature(id="ghoul", name="食尸鬼", description="墓园里的东西", stats={"HP": 12, "体质": 60},
                                attacks=["爪击 50%，伤害 1D6+DB"], notes="护甲 1"))
    return m


def test_a_wound_is_rolled_with_its_con_roll_and_lands_once(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator  # 11 HP: 6 is a major wound
    fake.queue(90).queue_totals(6)  # 6 damage; the CON roll fails
    wound = effects.harm(game, inv.id, "1D6", injury="trap:1")
    assert (wound.amount, wound.con) == (6, Level.FAILURE)
    done = effects.commit(game.world, [wound], op_id="effects:trap:1", source="trap")
    assert done.ok and inv.hp == 5 and {"重伤", "昏迷"} <= set(inv.conditions) and inv.injury.injuries == ["trap:1"]
    assert done.told("trap:1", inv.name) == "受到 6 点伤害（HP 11 → 5），重伤，昏迷"
    again = effects.commit(game.world, [wound], op_id="effects:trap:1", source="trap")  # a retried turn: nothing twice
    assert again.ok and again.repeat and inv.hp == 5 and again.told("trap:1", inv.name) == done.told("trap:1", inv.name)
    twice = effects.commit(game.world, [wound.model_copy(update={"amount": 2})], op_id="effects:other", source="plugin")
    assert not twice.ok and "已经结算过" in twice.problems[0] and inv.hp == 5  # the same injury under another op


def test_a_blow_over_the_maximum_kills_and_exactly_the_maximum_is_a_major_wound(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    fake.queue(10)  # CON passes
    effects.commit(game.world, [effects.harm(game, inv.id, 11, injury="a")], op_id="a", source="t")
    assert inv.hp == 0 and "死亡" not in inv.conditions and "濒死" in inv.conditions  # 11 of 11: major, then dying
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    effects.commit(game.world, [effects.harm(game, inv.id, 12, injury="b")], op_id="b", source="t")  # no CON roll at all
    assert "死亡" in inv.conditions and not fake.values


def test_damage_text_that_is_no_expression_is_refused_with_a_reason(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    for bad in ("1D3点", "无", "1D6+伤害加值"):
        with pytest.raises(ValueError):
            effects.harm(game, game.state.investigator.id, bad, injury="x")
    assert effects.harm(game, game.state.investigator.id, "2", injury="x").amount == 2  # a constant: nothing for BCDice


def test_creatures_keep_their_bodies_armour_and_death(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    w = game.world
    assert "ghoul" not in w.rec.entities  # a creature is nobody's until it is hurt or fights
    effects.commit(w, [effects.Wound(target="ghoul", amount=5, means="blow", injury="g1")], op_id="g1", source="t")
    ghoul = w.rec.entities["ghoul"]
    assert ghoul.kind == "creature" and ghoul.at == "tower" and ghoul.life.hp == 8  # armour 1 first
    effects.commit(w, [effects.Wound(target="ghoul", amount=20, injury="g2")], op_id="g2", source="t")
    assert ghoul.life.hp == 0 and "死亡" in ghoul.life.conditions and ghoul.status == "死亡"  # not dying: dead


def test_heal_stabilize_wake_die_and_mark(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    fake.queue(10)
    effects.commit(game.world, [effects.harm(game, inv.id, 11, injury="w")], op_id="w", source="t")
    effects.commit(game.world, [effects.Heal(target=inv.id, amount=1, means="first_aid", injury="w"),
                                effects.Stabilize(target=inv.id), effects.Mark(investigator=inv.id, skill="急救")],
                   op_id="aid", source="t")
    # Stabilized, not healed (p. 101): still dying and out cold until Medicine, no CON every round
    assert inv.hp == 1 and "濒死" in inv.conditions and inv.injury.aided == ["w"] and inv.injury.stabilized_at == game.state.minutes
    assert "急救" in inv.marks
    effects.commit(game.world, [effects.Wake(target=inv.id)], op_id="wake", source="t")
    assert "昏迷" in inv.conditions  # the dying do not wake
    other, _, _ = make(module, fake, {}, location="tower")
    me = other.state.investigator
    me.hp = 3
    effects.commit(other.world, [effects.Wound(target=me.id, amount=3, injury="k")], op_id="k", source="t")  # a minor wound to 0
    effects.commit(other.world, [effects.Wake(target=me.id)], op_id="wake", source="t")
    assert "昏迷" not in me.conditions
    effects.commit(game.world, [effects.Die(target="warden", why="测试")], op_id="die", source="t")
    assert game.world.rec.entities["warden"].status == "死亡" and game.m.npc("warden").status == "死亡"


def test_a_dropped_weapon_leaves_the_inventory_and_lies_where_it_fell(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    inv.inventory.append(".38 左轮手枪")
    done = effects.commit(game.world, [effects.Ammo(item=".38 左轮手枪", loaded=2), effects.Jam(item=".38 左轮手枪", jammed=True),
                                       effects.Drop(item=".38 左轮手枪", to="tower")], op_id="drop", source="t")
    assert done.ok and ".38 左轮手枪" not in inv.inventory
    gun = game.world.rec.entities[".38 左轮手枪"]
    assert gun.at == "tower" and gun.weapon.loaded == 2 and gun.weapon.jammed and gun.weapon.capacity == 6


def test_an_effects_op_is_atomic(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    before = inv.hp
    done = effects.commit(game.world, [effects.Wound(target=inv.id, amount=3, injury="ok"),
                                       effects.Relocate(who="players", place="tower")], op_id="half", source="t")
    inv = game.state.investigator  # a refused op puts the whole domain back as it was
    assert not done.ok and inv.hp == before and not inv.injury.injuries  # the party moves by arriving: nothing landed


def test_models_and_the_director_cannot_settle_effects_or_encounters(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    w = game.world
    wound = {"kind": "effects", "effects": [{"kind": "wound", "target": game.state.investigator.id, "amount": 5, "injury": "x"}]}
    for actor in (KEEPER, DIRECTOR):
        result = Gateway(w, actor).commit([wound], reads={}, request_id=f"r-{actor}")
        assert not result["ok"]
        schema = Gateway(w, actor).schema()["operations"]
        assert "effects" not in str(schema.get("discriminator", {})) and "EncounterStep" not in schema.get("$defs", {})
    assert not w.apply({**wound, "actor": DIRECTOR}).ok  # an op written by anyone but the engine is refused
    with pytest.raises(ValueError):
        desk.submit(w, "plan", [wound], reads={})
    later = Schedule(event="later", at=game.state.minutes + 10, text="以后", ops=[wound], actor=DIRECTOR)
    assert any("引擎" in p for p in later.check(w))
    assert game.state.investigator.hp == game.state.investigator.hp_max


def _encounter(game) -> EncounterState:
    inv = game.state.investigator
    return EncounterState(id="enc1", mode="fight", place="tower", started_turn=game.state.turn, participants=[
        Participant(id=inv.id, kind="investigator", side="party", controller="human"),
        Participant(id="ghoul", kind="creature", side="foe", controller="keeper")])


def test_an_encounter_step_lands_its_changes_and_effects_together_and_is_undoable(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    w, st = game.world, game.state
    begun = w.apply(EncounterStep(id="enc/enc1/begin", encounter="enc1", changes=[Begin(state=_encounter(game))]))
    assert begun.ok and st.encounter.id == "enc1" and w.rec.entities["ghoul"].life.hp == 12  # seeded on joining
    hit = EncounterStep(id="enc/enc1/r1/s0", encounter="enc1", changes=[SetCursor(cursor=1)],
                        effects=[effects.Wound(target="ghoul", amount=4, means="blow", injury="enc1:r1:a")])
    assert w.apply(hit).ok and st.encounter.cursor == 1 and w.rec.entities["ghoul"].life.hp == 9
    assert w.apply(hit).repeat and w.rec.entities["ghoul"].life.hp == 9
    bad = EncounterStep(id="enc/enc1/r1/s1", encounter="enc1", changes=[SetCursor(cursor=2)],
                        effects=[effects.Wound(target="ghoul", amount=4, injury="enc1:r1:a")])
    assert not w.apply(bad).ok and st.encounter.cursor == 1  # refused whole
    assert w.undo() == [] and st.encounter.cursor == 0 and w.rec.entities["ghoul"].life.hp == 12
    summary = EncounterSummary(id="enc1", mode="fight", place="tower", started_turn=st.turn, rounds=1)
    assert w.apply(EncounterStep(id="enc/enc1/archive", encounter="enc1", changes=[Archive(summary=summary)])).ok
    assert st.encounter is None and [x.id for x in st.encounter_log] == ["enc1"]


async def test_event_and_failure_damage_go_through_effects_and_bad_text_is_a_note(module, fake):
    game, _, events = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    hurt = Event(id="beam", title="横梁", when="now", text="一根横梁砸下来", damage="1D3")
    fake.queue_totals(2)
    turn = Turn()
    await game._fire(hurt, turn)
    assert inv.hp == inv.hp_max - 2 and inv.injury.injuries == [f"event:beam:{game.state.turn}"]
    notice = next(e for e in events if e["type"] == "damage")
    assert notice["injury"] == f"event:beam:{game.state.turn}" and notice["text"].startswith("受到 2 点伤害")
    odd = Event(id="odd", title="怪事", when="now", text="", damage="1D3点")  # an import's words, not an expression
    turn = Turn()
    await game._fire(odd, turn)
    assert inv.hp == inv.hp_max - 2 and any("伤害式无法使用" in n for n in turn.notes)


DAMAGE = '''def handle(event, ctx, api):
    return {"state": {}, "effects": [{"kind": "damage", "who": ctx["acting"], "amount": 3, "key": event["text"]}]}
'''


async def test_a_plugin_cannot_take_off_a_blow_the_engine_already_took(module, fake):
    """The blow an engine notice names (its injury id) is not taken off again by a plugin."""
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    effects.commit(game.world, [effects.Wound(target=inv.id, amount=2, injury="event:beam:1")], op_id="e", source="event")
    plugin = Plugin(id="trap", name="陷阱", description="再扣一次", code=DAMAGE, hooks=["command"], capabilities=["damage"],
                    commands=[Command(name="踩", description="踩陷阱")],
                    tests=[Case(name="踩", event={"hook": "command", "command": "踩", "text": "x"}, expected_state={})])
    game.m.extensions[plugin.id] = plugin
    game.m.plugins.append(plugin.id)
    game.plugins.verified.add(plugin.fingerprint)
    assert await game.plugins.command("踩", "event:beam:1") == ""
    inv = game.state.investigator
    assert inv.hp == inv.hp_max - 2 and inv.injury.injuries == ["event:beam:1"]  # the same blow: not again
    assert await game.plugins.command("踩", "plugin:new") == "", game.state.plugin_errors
    inv = game.state.investigator
    assert inv.hp == inv.hp_max - 5 and inv.injury.injuries == ["event:beam:1", "plugin:new"]
