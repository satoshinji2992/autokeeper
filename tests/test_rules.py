from __future__ import annotations

import pytest

from autokeeper.rules import commands
from autokeeper.rules.bcdice import Level, Roll
from autokeeper.rules.coc import Rules, max_of

from .conftest import needs_node


def test_roll_parses_bcdice_text():
    r = Roll("CC(1)<=60", "(1D100<=60) 獎勵・懲罰骰[1] ＞ 53, 23 ＞ 23 ＞ 困難成功")
    assert r.level == Level.HARD and r.value == 23 and r.candidates == [53, 23]
    assert Roll("3D6*5", "(3D6*5) ＞ 7[3,2,2]*5 ＞ 35").total == 35
    assert Roll("CC<=50", "(1D100<=50) ボーナス・ペナルティダイス[0] ＞ 36 ＞ 36 ＞ レギュラー成功").level == Level.REGULAR


def test_derived_values(inv):
    assert (inv.hp_max, inv.mp_max, inv.san) == (11, 12, 60)
    assert inv.damage_bonus == "0" and inv.build == 0
    assert inv.move == 8
    assert inv.value("闪避") == 32  # 敏捷/2
    assert inv.value("母语") == 70  # 教育
    assert inv.value("灵感") == 75
    assert inv.value("侦察") == 60  # alias
    assert inv.value("外语") == 40  # general skill falls back to the specialisation


def test_check_difficulty_and_marks(rules, fake, inv):
    fake.queue(25, 36)
    hard = rules.check(inv, "侦查", Level.HARD)
    assert hard.level == Level.HARD and hard.passed
    assert "侦查" in inv.marks
    miss = rules.check(inv, "图书馆使用", Level.HARD)
    assert miss.level == Level.REGULAR and not miss.passed and miss.can_push


def test_push_and_luck(rules, fake, inv):
    fake.queue(63)
    failed = rules.check(inv, "侦查")
    assert failed.luck_needed() == 3
    assert rules.spend_luck(inv, failed) and failed.passed and inv.luck == 52
    fake.queue(90, 40)
    failed = rules.check(inv, "心理学")
    pushed = rules.push(inv, failed)
    assert pushed.pushed and pushed.passed and not pushed.can_push
    fake.queue(100)
    with pytest.raises(ValueError):
        rules.push(inv, rules.check(inv, "侦查"))  # fumbles cannot be pushed


def test_sanity_temporary_insanity(rules, fake, inv):
    fake.queue(80, 30).queue_totals(6)
    s = rules.sanity(inv, "1", "1D6")
    assert s.loss == 6 and inv.san == 54
    assert s.temporary and s.int_check and s.int_check.passed
    assert "临时疯狂" in inv.conditions and fake.tables == ["BMR"]


def test_sanity_fumble_takes_maximum_and_daily_limit(rules, fake, inv):
    fake.queue(100, 90)
    s = rules.sanity(inv, "1", "1D10+2")
    assert s.loss == 12 and s.indefinite and not s.temporary
    assert max_of("2D6+1") == 13 and max_of("3") == 3


def test_damage(rules, fake, inv):
    fake.queue(90)
    d = rules.damage(inv, 6)
    assert d.major_wound and d.unconscious and inv.hp == 5
    fake.queue(10)
    d = rules.damage(inv, 5)
    assert d.dying and inv.hp == 0
    assert rules.damage(inv, 11).dead


def test_opposed(rules, fake, inv):
    fake.queue(20, 40)
    a = rules.check(inv, "侦查")
    b = rules.check(inv, "潜行", target=50)
    assert rules.opposed(a, b).winner == "a"


def test_commands(rules, fake, inv):
    fake.queue(12)
    r = commands.run(".rab2 困难侦查", inv, rules)
    assert r.kind == "check" and r.data["check"].bonus == 2 and r.data["check"].difficulty == Level.HARD
    fake.queue(70)
    r = commands.run(".ra 侦查50", None, rules)
    assert r.data["check"].target == 50 and not r.data["check"].passed
    r = commands.run(".st 侦查65 hp-3 理智+2", inv, rules)
    assert inv.skills["侦查"] == 65 and inv.hp == 8 and inv.san == 62
    fake.queue(10)
    r = commands.run(".sc 1/1d6", inv, rules)
    assert r.kind == "sanity" and inv.san == 61
    fake.queue_totals(3)
    assert commands.run(".r 1d6", inv, rules).text.endswith("3")
    assert commands.run(".xyz", inv, rules).kind == "error"
    assert commands.run(".ra", inv, rules).kind == "error"


@needs_node
def test_live_bcdice(bcdice, inv):
    rules = Rules(dice=bcdice)
    c = rules.check(inv, "侦查", bonus=1)
    assert c.level is not None and 1 <= c.value <= 100 and len(c.roll.candidates) == 2
    stats = rules.roll_characteristics()
    assert all(15 <= v <= 90 for v in stats.values())
    assert "BMR" not in bcdice.table("BMR")
