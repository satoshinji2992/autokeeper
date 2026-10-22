"""A melee exchange as the Keeper Rulebook settles it (rules/melee.py, ch. 6 pp. 86–91)."""

from __future__ import annotations

import itertools

import pytest

from autokeeper.rules.bcdice import Level
from autokeeper.rules.melee import (
    clamp,
    damage_mode,
    damage_plan,
    disadvantage_bonus,
    keeper_default,
    maneuver_feasible,
    melee_outcome,
    opposed_difficulty,
    outnumbered_bonus,
    reactions_against,
    surprise_attack,
    surprise_bonus,
)

from .conftest import FakeDice

LEVELS = list(Level)


@pytest.mark.parametrize(("att", "dfn"), list(itertools.product(LEVELS, LEVELS)))
def test_every_pair_of_levels_against_a_dodge_and_against_fighting_back(att, dfn):
    """p. 87: against a dodge the attacker needs the better success, a tie the dodger's; against
    fighting back a tie of successes is the attacker's and a better success hurts the attacker; both
    failing, nobody is hurt (p. 86)."""
    dodge = melee_outcome(att, dfn, "dodge")
    if att.ok and att > dfn:
        assert dodge.winner == "attacker"
    else:
        assert dodge.winner == "none" and dodge.mode == "none"
    back = melee_outcome(att, dfn, "fight_back")
    if att.ok and att >= dfn:
        assert back.winner == "attacker"
    elif dfn.ok and dfn > att:
        assert back.winner == "defender" and back.mode == "normal"
    else:
        assert back.winner == "none"
    if not att.ok and not dfn.ok:
        assert dodge.winner == back.winner == "none"


def test_melee_both_fail_nobody_hits():
    for att, dfn in itertools.product((Level.FUMBLE, Level.FAILURE), repeat=2):
        assert melee_outcome(att, dfn, "fight_back").winner == "none"
        assert melee_outcome(att, dfn, "dodge").winner == "none"


def test_a_tie_of_successes_goes_to_the_dodger_and_to_the_attacker_against_fighting_back():
    assert melee_outcome(Level.REGULAR, Level.REGULAR, "dodge").winner == "none"
    assert melee_outcome(Level.REGULAR, Level.REGULAR, "fight_back").winner == "attacker"  # Harvey's punch, p. 88


def test_extreme_damage_only_for_the_attacker_on_its_own_turn():
    """p. 87: never on fighting back; the ruffian's extreme knife on Harvey's turn did normal damage
    (p. 90)."""
    assert melee_outcome(Level.EXTREME, Level.FAILURE, "dodge").mode == "extreme"
    assert melee_outcome(Level.CRITICAL, Level.HARD, "fight_back").mode == "extreme"
    assert melee_outcome(Level.REGULAR, Level.EXTREME, "fight_back").mode == "normal"  # the defender's blow
    assert melee_outcome(Level.EXTREME, Level.FAILURE, "dodge", own_turn=False).mode == "normal"
    assert damage_mode(Level.EXTREME) == "extreme" and damage_mode(Level.HARD) == "normal"
    assert damage_mode(Level.FAILURE) == "none"


def test_an_attack_nobody_answers_lands_unless_it_fumbles():
    """p. 91: against a target that cannot dodge or fight back the attack fails only on a fumble."""
    assert melee_outcome(Level.FAILURE, None, "none").winner == "attacker"
    assert melee_outcome(Level.FAILURE, None, "none").mode == "normal"  # it lands, and hurts as a blow does
    assert melee_outcome(Level.FUMBLE, None, "none").winner == "none"
    assert melee_outcome(Level.EXTREME, None, "none").mode == "extreme"
    with pytest.raises(ValueError):
        melee_outcome(Level.REGULAR, Level.REGULAR, "cover")


def test_a_manoeuvre_takes_effect_and_does_no_damage():
    won = melee_outcome(Level.REGULAR, Level.REGULAR, "fight_back", maneuver=True)  # Roger's hold, p. 93
    assert won.winner == "attacker" and won.effect and won.mode == "none"
    lost = melee_outcome(Level.REGULAR, Level.EXTREME, "fight_back", maneuver=True)  # the ruffian's knife, p. 90
    assert lost.winner == "defender" and lost.mode == "normal"
    countered = melee_outcome(Level.REGULAR, Level.HARD, "maneuver")
    assert countered.winner == "defender" and countered.effect and countered.mode == "none"


def test_damage_of_a_blunt_and_an_impaling_extreme_success():
    """p. 87: +1D4 and a flick knife at extreme is 1D4+8; p. 88: old Birch's extreme fist is 7;
    p. 99: the .45's impale is 12+1D10+2."""
    knife = damage_plan("1D4+DB", "+1D4", "extreme", impale=True)
    assert (knife.fixed, knife.roll) == (8, "1D4") and knife.total(FakeDice().queue_totals(3)) == 11
    fist = damage_plan("1D3+DB", "+1D4", "extreme")
    assert (fist.fixed, fist.roll) == (7, "") and fist.text == "最大 7"
    pistol = damage_plan("1D10+2", "0", "extreme", impale=True)
    assert (pistol.fixed, pistol.roll) == (12, "1D10+2")
    normal = damage_plan("1D6+DB", "+1D4", "normal")
    assert (normal.fixed, normal.roll) == (0, "1D6+1D4")
    assert damage_plan("1D4+半DB", "+1D6", "normal").roll == "1D4+1D3"  # a throw: half the bonus
    assert damage_plan("4D6/2D6/1D6", "0", "normal", band=1).roll == "2D6"
    assert damage_plan("1D3+DB", "-2", "normal").roll == "1D3-2"
    assert damage_plan("2", "0", "normal").fixed == 2 and damage_plan("0").total(FakeDice()) == 0
    assert damage_plan("1D6", "0", "none").fixed == 0
    with pytest.raises(ValueError):
        damage_plan("1D6点")


def test_a_manoeuvre_against_a_bigger_target():
    """p. 89: a penalty die per point of build over the attacker, two at most; three and it fails."""
    assert maneuver_feasible(0, 1) == (True, 1)  # Harvey against the ruffian, p. 90
    assert maneuver_feasible(0, 2) == (True, 2)
    assert maneuver_feasible(0, 3) == (False, 0)
    assert maneuver_feasible(2, 0) == (True, 0)


def test_outnumbered_after_as_many_answers_as_attacks_and_never_against_ranged_attacks():
    """p. 91: a bonus die once the defender has answered; a ghoul with three attacks only after three."""
    assert outnumbered_bonus(0) == 0 and outnumbered_bonus(1) == 1
    assert outnumbered_bonus(2, 3) == 0 and outnumbered_bonus(3, 3) == 1
    assert outnumbered_bonus(5, ranged=True) == 0 and outnumbered_bonus(5, immune=True) == 0


def test_what_a_throw_a_missile_and_a_shot_can_be_answered_with():
    """p. 91: a throw dodged, a missile met with cover; neither fought back beyond DEX/5 feet."""
    assert reactions_against("thrown", distance_ft=30, dex=60) == ("dodge", "none")
    assert "fight_back" in reactions_against("thrown", distance_ft=10, dex=60)  # 12 feet
    assert reactions_against("missile", distance_ft=30, dex=60) == ("cover", "none")
    assert reactions_against("firearm", distance_ft=3, dex=90) == ("cover", "none")
    assert "dodge" in reactions_against("melee") and "fight_back" in reactions_against("melee")
    assert reactions_against("melee", ready=False) == ("none",)


def test_keepers_fight_back_unless_withdrawing_and_the_difficulty_of_opposing_a_skill():
    assert keeper_default() == "fight_back" and keeper_default(withdrawing=True) == "dodge"  # p. 87
    assert opposed_difficulty(49) == Level.REGULAR and opposed_difficulty(50) == Level.HARD  # p. 72
    assert opposed_difficulty(90) == Level.EXTREME


def test_surprise_and_disadvantage():
    """pp. 90–91: an unready target is hit unless the attack fumbles, or the attacker takes a bonus
    die; a ranged attack is always rolled."""
    assert surprise_attack(Level.FAILURE, ranged=False)
    assert not surprise_attack(Level.FUMBLE, ranged=False)
    assert not surprise_attack(Level.FAILURE, ranged=True) and surprise_attack(Level.REGULAR, ranged=True)
    assert not surprise_attack(Level.FAILURE, ranged=False, rule="bonus")
    assert surprise_bonus(ranged=False, rule="bonus") == 1 and surprise_bonus(ranged=False) == 0
    assert disadvantage_bonus(prone=True, held=True) == 1 and disadvantage_bonus() == 0
    assert clamp(3) == 2 and clamp(-5) == -2 and clamp(0) == 0
