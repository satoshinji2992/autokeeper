"""Damage dice as written and as BCDice rolls them (rules/dice_expr.py)."""

from __future__ import annotations

import re

import pytest
from pydantic import BaseModel, ValidationError

from autokeeper.harness.models import problems
from autokeeper.rules.dice_expr import (
    PATTERN,
    DamageExpr,
    DiceText,
    StoredDiceText,
    bonus_terms,
    canonical,
    problem,
    valid,
)

from .conftest import FakeDice, needs_node

VALID = {
    "": "",
    "0": "0",
    "2": "2",
    "1D6": "1D6",
    "1d6": "1D6",
    "D6": "1D6",
    "1D6+DB": "1D6+DB",
    "1d4 + db": "1D4+DB",
    "1D4+2+DB": "1D4+2+DB",
    "1D4+半DB": "1D4+半DB",
    "2D6+4": "2D6+4",
    "1D10+2-1D4": "1D10+2-1D4",
    "+1D3": "1D3",
    "4D6/2D6/1D6": "4D6/2D6/1D6",
    "１Ｄ６＋２": "1D6+2",
    " 1D8 ": "1D8",
}

# What the review found models writing, and what BCDice refuses outright.
INVALID = {
    "无": "留空",
    "没有": "留空",
    "1D6+伤害": "DB",
    "1D6+伤害加值": "DB",
    "1D3点": "单位",
    "1D6（火焰）": "括号",
    "1D6(火)": "括号",
    "1D6×2": "乘除",
    "1D6*2": "乘除",
    "1D6/2": "分档",
    "1D6+": "末尾",
    "-2": "负数",
    "1D6+DB+DB": "不止一次",
    "1D6-DB": "减号",
    "1D0": "面数",
    "0D6": "个数",
    "1D6/1D6/1D6/1D6": "三档",
    "abc": "不是骰子式",
}


@pytest.mark.parametrize(("text", "canon"), VALID.items())
def test_valid_expressions_read_in_one_canonical_spelling(text, canon):
    assert canonical(text) == canon
    assert valid(text) and problem(text) == ""
    assert canonical(canon) == canon  # the canonical spelling reads as itself


@pytest.mark.parametrize(("text", "says"), INVALID.items())
def test_what_is_no_expression_is_refused_with_a_chinese_reason(text, says):
    with pytest.raises(ValueError, match=says):
        DamageExpr.parse(text)
    assert not valid(text) and says in problem(text)


@pytest.mark.parametrize("text", [t for t in INVALID if re.search(r"[一-鿿×*（(]", t)])
def test_the_schema_pattern_refuses_the_chinese_and_the_words(text):
    assert re.fullmatch(PATTERN, text) is None


@pytest.mark.parametrize("text", VALID)
def test_the_schema_pattern_admits_every_valid_expression_written_in_half_width(text):
    if not re.search(r"[１Ｄ６＋]", text):
        assert re.search(PATTERN, text) is not None  # jsonschema searches; the pattern is anchored


def test_the_harness_repair_pass_sees_a_bad_dice_string():
    class Hit(BaseModel):
        damage: DiceText = ""

    schema = Hit.model_json_schema()
    assert schema["properties"]["damage"]["pattern"] == PATTERN
    assert problems({"damage": "1D6"}, schema) == []
    for bad in ("无", "1D3点", "1D6（火焰）", "1D6+伤害"):
        assert problems({"damage": bad}, schema), bad


def test_dicetext_canonicalizes_and_says_why():
    class Hit(BaseModel):
        damage: DiceText = ""

    assert Hit(damage="1d6 + db").damage == "1D6+DB"
    assert Hit.model_validate_json('{"damage": "0"}').damage == "0"
    with pytest.raises(ValidationError, match="括号里的说明"):
        Hit(damage="1D6（火焰）")


def test_stored_dice_text_still_loads_what_was_never_an_expression():
    class Kept(BaseModel):
        harm: StoredDiceText = ""

    assert Kept(harm="1d3").harm == "1D3"
    assert Kept(harm="1D3点").harm == "1D3点"  # an old module's or save's: refused where it is rolled
    assert Kept.model_json_schema()["properties"]["harm"]["pattern"] == PATTERN


# -- what BCDice is given ---------------------------------------------------------------------
DBS = ("0", "", "-2", "-1", "+1D4", "+1D6", "+2D6", "1D4", "+2")


@pytest.mark.parametrize("text", [t for t in VALID if t])
def test_rollable_never_holds_db_chinese_spaces_or_a_slash(text):
    expr = DamageExpr.parse(text)
    for db in DBS:
        for band in range(3):
            command = expr.rollable(db, band)
            assert not re.search(r"DB|db|[一-鿿]|\s|/", command), (text, db, command)
            assert command == "" or re.fullmatch(r"-?\d+(D\d+)?([+-]\d+(D\d+)?)*", command), command
            assert (command == "") == (expr.fixed(db, band) is not None)


@pytest.mark.parametrize(("text", "db", "command", "most"), [
    ("1D6+DB", "+1D4", "1D6+1D4", 10),
    ("1D6+DB", "-2", "1D6-2", 4),
    ("1D6+DB", "0", "1D6", 6),
    ("1D6+DB", "", "1D6", 6),
    ("1D3+DB", "+2D6", "1D3+2D6", 15),
    ("1D4+半DB", "+1D6", "1D4+1D3", 7),
    ("1D4+半DB", "+1D4", "1D4+1D2", 6),
    ("1D4+半DB", "-2", "1D4-1", 3),
    ("1D4+半DB", "-1", "1D4", 4),
    ("1D10+2-1D4", "0", "1D10+2-1D4", 11),
])
def test_the_damage_bonus_is_put_in_for_db(text, db, command, most):
    expr = DamageExpr.parse(text)
    assert expr.has_db == ("DB" in text)
    assert expr.rollable(db) == command
    assert expr.maximum(db) == most
    assert expr.fixed(db) is None


def test_nothing_to_roll():
    for text in ("", "0"):
        expr = DamageExpr.parse(text)
        assert expr.none and expr.rollable("+1D4") == "" and expr.fixed() == 0 and expr.maximum() == 0
    two = DamageExpr.parse("2")
    assert not two.none and two.rollable() == "" and two.fixed() == 2 and two.maximum() == 2
    assert DamageExpr.parse("1+DB").fixed("-2") == 0  # never below nothing
    assert DamageExpr.parse("2+DB").fixed("+2") == 4
    assert not DamageExpr.parse("DB").none and DamageExpr.parse("DB").rollable("+1D4") == "1D4"


def test_a_shotgun_rolls_the_band_of_its_range():
    gun = DamageExpr.parse("4D6/2D6/1D6")
    assert [gun.rollable(band=b) for b in range(4)] == ["4D6", "2D6", "1D6", "1D6"]  # past long range: the last
    assert gun.maximum(band=0) == 24 and gun.maximum(band=2) == 6
    assert DamageExpr.parse("1D10").rollable(band=2) == "1D10"


def test_total_rolls_only_when_there_are_dice():
    dice = FakeDice().queue_totals(3, -1)
    assert DamageExpr.parse("1D6+DB").total(dice, "+1D4") == 3
    assert DamageExpr.parse("1D6-2").total(dice) == 0  # a roll below nothing is nothing
    assert DamageExpr.parse("2").total(dice) == 2  # no dice thrown: FakeDice would have run dry


def test_damage_bonus_spellings():
    assert bonus_terms("0") == () and bonus_terms("") == () and bonus_terms("+0") == ()
    assert [t.text() for t in bonus_terms("+1d4")] == ["1D4"]
    assert [(t.sign, t.text()) for t in bonus_terms("-2")] == [(-1, "2")]
    with pytest.raises(ValueError, match="伤害加值"):
        bonus_terms("一点")
    with pytest.raises(ValueError, match="伤害加值"):
        DamageExpr.parse("1D6+DB").rollable("+DB")


@needs_node
def test_bcdice_rolls_every_rollable_within_its_bounds(bcdice):
    for text in [t for t in VALID if t] + ["1D6+DB", "1D4+半DB", "1D3-1"]:
        expr = DamageExpr.parse(text)
        for db in DBS:
            if command := expr.rollable(db):
                total = bcdice.total(command)  # BCDiceError if it refused the command
                assert total <= expr.maximum(db) or total < 0, (command, total)
