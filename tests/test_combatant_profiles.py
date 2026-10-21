"""What an NPC or a creature fights with, read once from its sheet (engine/encounter/profiles.py),
and everyone in an encounter as the engine reads them (engine/encounter/combatant.py)."""

from __future__ import annotations

import pytest

from autokeeper.engine import effects
from autokeeper.engine.encounter import combatant, profiles
from autokeeper.module.schema import Creature, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.catalog import base_value
from autokeeper.world.record import Entity

from .test_engine import make


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


def test_a_creature_sheet_is_read_with_what_it_cannot_say(module, fake):
    module.creatures.append(Creature(
        id="ghoul", name="食尸鬼", called="佝偻的黑影", description="墓园里的东西", stats={"HP": 13, "敏捷": 65, "力量": 80, "体型": 65},
        attacks=["爪击 40%，伤害 1D6+DB", "撕咬 30%，伤害 1D4", "每轮 3 次", "嚎叫吓人"],
        notes="护甲 2；枪伤减半；每轮恢复 1 点；能在墙上爬行；免疫毒素"))
    game, _, _ = make(module, fake, {}, location="tower")
    p = profiles.profile(game.world, "ghoul")
    assert (p.kind, p.hp_max, p.per_round, p.armor, p.guns_halved, p.regen, p.human) == ("creature", 13, 3, 2, True, 1, False)
    assert p.db == "+1D4" and [a.name for a in p.attacks] == ["爪击", "撕咬"] and all(a.natural and a.item is None for a in p.attacks)
    assert any("嚎叫吓人" in u for u in p.unread) and any("免疫毒素" in u for u in p.unread)
    assert not any("护甲" in u or "枪伤" in u for u in p.unread)  # what the engine reads is not listed


def test_a_damage_the_dice_cannot_roll_is_unread_not_a_crash(module, fake):
    module.creatures.append(Creature(id="thing", name="怪物", description="", stats={"HP": 10},
                                     attacks=["触手 50%，伤害 1D6"]))
    game, _, _ = make(module, fake, {}, location="tower")
    p = profiles.profile(game.world, "thing")
    p.attacks[0].damage = "1D6（酸液）"
    profiles._worldly(game.world, p)
    assert p.attacks[0].problem and any("酸液" in u for u in p.unread)


def test_an_empty_npc_sheet_is_an_ordinary_person_and_fills_fix_the_numbers(module, fake):
    module.npc("warden").stats = {}
    game, _, _ = make(module, fake, {}, location="tower")
    w = game.world
    p = profiles.profile(w, "warden")
    assert (p.hp_max, p.source, p.dodge) == (11, "ordinary", 25)
    assert p.skill("格斗(刀剑)") == base_value("格斗(刀剑)", p.chars)  # never 1% for a known weapon skill
    w.rec.entities["warden"].fills = {"生命": 16, "闪避": 40}
    p = profiles.profile(w, "warden")
    assert (p.hp_max, p.dodge, p.source) == (16, 40, "fill")
    assert profiles.life(w, p).hp == 16


def test_an_npc_fights_with_what_he_holds_and_what_his_look_says(module, fake):
    module.npc("warden").look = "腰间别着一把左轮"
    game, _, _ = make(module, fake, {}, location="tower")
    w = game.world
    w.rec.entities["poker"] = Entity(id="poker", kind="item", name="拨火棍", at="warden")
    p = profiles.profile(w, "warden")
    items = {a.name: a.item for a in p.attacks}
    assert items["棍棒"] == "poker" and items[".38 左轮手枪"] == "warden:.38 左轮手枪" and items["徒手"] is None
    gun = next(a for a in p.attacks if a.firearm)
    assert gun.capacity == 6 and gun.loaded is None  # nobody counted its rounds yet: a full load
    effects.commit(w, [effects.Ammo(item="warden:.38 左轮手枪", loaded=1)], op_id="shot", source="t")
    gun = next(a for a in profiles.profile(w, "warden").attacks if a.firearm)
    assert gun.loaded == 1 and w.rec.entities["warden:.38 左轮手枪"].at == "warden"
    effects.commit(w, [effects.Drop(item="warden:.38 左轮手枪", to="tower")], op_id="drop", source="t")
    assert not any(a.firearm for a in profiles.profile(w, "warden").attacks)  # it lies on the floor now


def test_a_creature_form_is_its_npc_one_life_one_place(module, fake):
    module.creatures.append(Creature(id="warden_beast", name="守钟人的真身", npc="warden", description="", stats={"HP": 15},
                                     attacks=["爪击 60%，伤害 1D6+DB"]))
    game, _, _ = make(module, fake, {}, location="tower")
    w = game.world
    assert profiles.combatant(w, "warden_beast") == "warden"
    p = profiles.profile(w, "warden_beast")
    assert (p.id, p.kind, p.hp_max) == ("warden", "npc", 15)
    effects.commit(w, [effects.Wound(target="warden_beast", amount=4, injury="b1")], op_id="b1", source="t")
    assert "warden_beast" not in w.rec.entities and w.rec.entities["warden"].life.hp == 11


def test_a_wound_the_record_already_had_seeds_the_body(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    game.world.rec.entities["warden"].status = "重伤"  # an older save's word for him, before bodies were kept
    p = profiles.profile(game.world, "warden")
    life = profiles.life(game.world, p)
    assert life.hp == max(1, p.hp_max // 2) and life.conditions == ["重伤"]


def test_views_read_the_sheet_the_profile_and_the_world(module, fake):
    module.creatures.append(Creature(id="ghoul", name="食尸鬼", called="佝偻的黑影", description="", stats={"HP": 12},
                                     attacks=["爪击 50%，伤害 1D6"]))
    game, _, _ = make(module, fake, {}, location="tower")
    w, inv = game.world, game.state.investigator
    inv.inventory.append("一把 .38 左轮")
    effects.commit(w, [effects.Ammo(item="一把 .38 左轮", loaded=3)], op_id="a", source="t")
    me = combatant.view(w, inv.id)
    gun = next(a for a in me.attacks() if a.firearm)
    assert (gun.item, gun.loaded, gun.capacity, gun.usable) == ("一把 .38 左轮", 3, 6, True)
    assert me.body().hp == inv.hp and me.can_act()
    beast = combatant.view(w, "ghoul")
    assert beast.public_name("players") == "佝偻的黑影" and beast.public_name("keeper") == "食尸鬼"
    game.state.identified.append("ghoul")
    assert beast.public_name("players") == "食尸鬼"
    assert beast.traits().human is False and beast.attacks()[0].natural and beast.attacks_per_round() == 1
    effects.commit(w, [effects.Wound(target="ghoul", amount=12, injury="k")], op_id="k", source="t")
    assert not beast.can_act() and beast.body().hp == 0
    assert combatant.view(w, "nobody") is None
