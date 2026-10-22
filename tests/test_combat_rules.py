"""The shared numbers of a fight (rules/combat.py, rules/weapons.py)."""

from __future__ import annotations

from autokeeper.rules import combat
from autokeeper.rules.weapons import carried, parse_attack, weapon_in


def test_a_creatures_notes_and_attacks_as_the_modules_write_them():
    ghoul = "伤害加值 0，体格 0。枪械伤害减半（向下取整）。HP 降到 0 死亡；只被拳头打时损失 6 点即被击晕。"
    assert combat.traits(ghoul) == {"guns_halved": True, "knockout": 6, "db": "0", "build": 0}
    erie = "体格 1，移动 10，伤害加成 +1D4。护甲：无，但每轮恢复 1 点 HP。"
    assert combat.traits(erie) == {"regen": 1, "db": "+1D4", "build": 1, "move": 10}
    claw = parse_attack("爪击 50% (25/10)，伤害 1D6")
    assert (claw.name, claw.value, claw.damage, claw.firearm) == ("爪击", 50, "1D6", False)
    rifle = parse_attack("射击（步枪）30%，春田 M1903 栓式猎枪，1 发/轮，弹夹 5，射程 110 码，2D6+4；弹夹里只有 1 发")
    assert (rifle.skill, rifle.damage, rifle.ammo, rifle.range) == ("射击(步枪/霰弹枪)", "2D6+4", 1, 110)
    assert parse_attack("每回合 1 次（入门模组简化）") is None
    assert combat.stats_of({"STR": 90, "HP": 18, "DEX": 80})["力量"] == 90


def test_the_rulebooks_own_wording_of_a_stat_block_reads_too():
    """As the Chinese 7th edition prints a monster, and as a model copies it: the attacks a round,
    halved firearms, armour and healing put other ways round, a bonus written DB."""
    from autokeeper.rules.weapons import attacks_per_round, unread

    assert attacks_per_round(["每回合攻击次数：3"]) == 3 and attacks_per_round(["爪击 50%"], "攻击次数：2") == 2
    assert attacks_per_round(["每轮攻击 2 次"]) == 2 and attacks_per_round(["每轮 2 点"]) == 1  # points are no attacks
    named = parse_attack("攻击：爪击 50%，伤害 1D6+伤害加值")
    assert (named.name, named.damage) == ("爪击", "1D6+DB")
    assert parse_attack("闪避 40%（20/8）") is None  # listed under the attacks, but no attack
    assert unread("闪避 40%（20/8）").startswith("闪避不是攻击") and unread("爪击 50，伤害 1D6").startswith("读不出命中率")
    assert unread("触手 45%") == "没写伤害，按 1D3 算" and unread("每回合 1 次") == "" and unread("撕咬 40%(20/8)，1D4+DB") == ""
    assert combat.traits("护甲：火器和穿刺武器造成的伤害减半，向下取整") == {"guns_halved": True}
    assert combat.traits("护甲：厚皮 4 点")["armor"] == 4 and combat.traits("2 点护甲")["armor"] == 2
    assert "armor" not in combat.traits("护甲：无，但每轮回复 2 点生命") and combat.traits("每轮回复 2 点生命")["regen"] == 2
    assert combat.traits("DB +1D4，体格 1，移动力 8") == {"db": "+1D4", "build": 1, "move": 8}


def test_an_investigator_fights_with_what_they_carry():
    assert [w.name for w in carried(["手电筒", "一把 .38 左轮手枪", "折刀"])] == ["徒手", ".38 左轮手枪", "小刀"]
    assert weapon_in("一根拨火棍").name == "棍棒" and weapon_in("自动手枪").name == ".32 自动手枪"


def test_a_dex_tie_goes_to_the_better_fighter():
    """p. 86: a DEX tie goes to the higher combat skill; p. 95: +50 to a readied firearm about to be fired."""
    slots = [combat.Slot("a", 55, 25), combat.Slot("b", 55, 70), combat.Slot("c", 55, 70, first_on_tie=True)]
    assert combat.turn_order(slots) == ["c", "b", "a"]  # the cultist's 70% before Harvey's 25%, p. 86
    assert combat.turn_order([combat.Slot("quick", 70), combat.Slot("gunman", 40, readied=50)]) == ["gunman", "quick"]


def test_weapon_skills_start_at_the_books_base_and_claws_are_no_weapon_to_take():
    """pp. 46, 53–54: an untrained sword is 20%, an axe 15%, a submachine gun 15% — never 1%."""
    from autokeeper.rules.catalog import base_value, canonical

    bases = {"格斗(斧)": 15, "格斗(刀剑)": 20, "格斗(链枷)": 10, "格斗(矛)": 20, "格斗(鞭)": 5, "格斗(绞索)": 15,
             "射击(冲锋枪)": 15, "射击(弓)": 15, "射击(重武器)": 10, "射击(机枪)": 10, "射击(火焰喷射器)": 10}
    assert {k: base_value(k, {}) for k in bases} == bases
    assert canonical("格斗（刀剑）") == "格斗(剑)" and canonical("射击(霰弹枪)") == "射击(步枪/霰弹枪)"
    claw = parse_attack("格斗（剥皮爪击）60%，伤害 1D4+2+DB")
    knife = parse_attack("攻击：小刀 40%，伤害 1D4+DB")
    rifle = parse_attack("射击（步枪）30%，2D6+4")
    assert claw.natural and not knife.natural and not rifle.natural
    assert weapon_in("拳头").natural
