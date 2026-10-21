"""Wounds and healing as the Keeper Rulebook settles them (rules/wounds.py, ch. 6 pp. 91, 100–103)."""

from __future__ import annotations

import pytest

from autokeeper.rules import wounds
from autokeeper.rules.bcdice import Level
from autokeeper.rules.coc import Investigator
from autokeeper.rules.wounds import Body, apply_wound, assess

HARVEY = Body(hp=15, hp_max=15)  # the rulebook's Harvey Walters


def test_a_minor_wound_and_a_major_one():
    """p. 100: under half the maximum minor; half or more major, prone at once, CON or unconscious."""
    minor = apply_wound(HARVEY, 3, "blow")
    assert minor.body.hp == 12 and not minor.major and not minor.prone
    found = assess(minor.body, 8, "blow")
    assert found.major and found.con and not found.dead
    major = apply_wound(minor.body, 8, "blow", con=Level.REGULAR)
    assert major.body.hp == 4 and major.major and major.prone and not major.unconscious and major.body.major
    out = apply_wound(minor.body, 8, "blow", con=Level.FAILURE)
    assert out.unconscious and not out.body.conscious


def test_exactly_the_maximum_is_a_major_wound_and_more_kills():
    """p. 100."""
    same = apply_wound(HARVEY, 15, "blow", con=Level.REGULAR)
    assert not same.dead and same.major and same.dying and same.body.hp == 0
    over = apply_wound(HARVEY, 16, "blow")
    assert over.dead and over.body.dead
    assert apply_wound(Body(hp=0, hp_max=15, conditions=("昏迷",)), 16).dead  # whatever was left


def test_at_zero_hit_points_unconscious_with_minor_wounds_dying_with_a_major_one():
    """pp. 100–101: 4, 6 and 7 leave Harvey at 0 and unconscious; 8 then 5 leave him dying."""
    b = HARVEY
    for n in (4, 6, 7):
        b = apply_wound(b, n, "blow").body
    assert b.hp == 0 and b.has("昏迷") and not b.dying
    b = apply_wound(apply_wound(HARVEY, 3).body, 8, con=Level.REGULAR).body
    dying = apply_wound(b, 5, "blow", round=4)
    assert dying.dying and dying.body.dying and dying.body.has("昏迷") and dying.body.dying_since == 4


def test_damage_at_zero_is_ignored_unless_meant_to_kill():
    down = apply_wound(HARVEY, 15, con=Level.REGULAR).body
    assert apply_wound(down, 3).body == down
    assert apply_wound(down, 3, "blow", finish=True).dead


def test_armour_first_and_not_against_poison_or_drowning():
    """p. 91: the deep one's 1 point of armour takes 1 off Harvey's 5; it does nothing against poison,
    drowning or magic."""
    deep_one = Body(hp=10, hp_max=10, human=False, armor=1)
    assert apply_wound(deep_one, 5, "blow").taken == 4
    assert apply_wound(deep_one, 5, "poison").taken == 5 and apply_wound(deep_one, 5, "asphyxia").taken == 5
    assert apply_wound(deep_one, 5, "blow", magic=True).taken == 5
    shell = Body(hp=10, hp_max=10, armor=6)
    stopped = apply_wound(shell, 5, "blow")
    assert stopped.taken == 0 and stopped.absorbed == 5 and stopped.notes == ["没能伤到它"]


def test_guns_halved_before_the_armour():
    """The star vampire (4 armour, bullets half): 11 rolled is 5, then 1 (pp. 256, 258, 274)."""
    vampire = Body(hp=20, hp_max=20, human=False, armor=4, guns_halved=True)
    assert apply_wound(vampire, 11, "firearm").taken == 1
    assert apply_wound(vampire, 11, "blow").taken == 7
    ghoul = Body(hp=13, hp_max=13, human=False, guns_halved=True)
    assert apply_wound(ghoul, 9, "firearm").taken == 4


def test_creatures_die_at_zero_unless_they_heal_themselves():
    beast = apply_wound(Body(hp=8, hp_max=8, human=False), 9)
    assert beast.dead and beast.down
    healer = apply_wound(Body(hp=8, hp_max=8, human=False, regen=1), 9)
    assert healer.down and not healer.dead and healer.body.regenerating and not healer.body.conscious
    up, rises = wounds.regen_tick(healer.body)
    assert rises and up.hp == 1 and not up.regenerating
    assert apply_wound(healer.body, 2, finish=True).dead  # finished off while down


def test_a_knockout_from_fists():
    ghoul = Body(hp=12, hp_max=12, human=False, knockout=6)
    once = apply_wound(ghoul, 3, "unarmed").body
    twice = apply_wound(once, 3, "unarmed")
    assert twice.unconscious and twice.body.punched == 6
    knocked = wounds.knockout_blow(HARVEY)  # the optional rule, p. 105
    assert knocked.body.hp == 14 and knocked.unconscious


def test_the_same_injury_lands_once():
    first = apply_wound(HARVEY, 3, "blow", injury="enc1:r1:a")
    again = apply_wound(first.body, 3, "blow", injury="enc1:r1:a")
    assert again.refused and again.body.hp == 12 and first.body.injuries == ("enc1:r1:a",)


def test_suffocating_to_zero_kills_and_poison_is_halved_by_an_extreme_con():
    """p. 103."""
    hurt = apply_wound(Body(hp=3, hp_max=12), 4, "asphyxia")
    assert hurt.dead
    assert wounds.asphyxia_difficulty(True) == Level.HARD
    assert wounds.asphyxia_round(Level.FAILURE, failed_before=False)
    assert not wounds.asphyxia_round(Level.REGULAR, failed_before=False)
    assert wounds.asphyxia_round(Level.CRITICAL, failed_before=True)  # once failed, every round
    assert wounds.asphyxia_round(Level.REGULAR, failed_before=False, exerting=True)  # hard while exerting
    assert wounds.poison_damage(15, Level.EXTREME) == 7 and wounds.poison_damage(15, Level.HARD) == 15
    assert wounds.harm_dice("重度") == "1D10" and [h.dice for h in wounds.OTHER_DAMAGE][-1] == "8D10"
    with pytest.raises(ValueError):
        wounds.harm_dice("很重")


def test_dying_rolls_con_from_the_end_of_the_next_round():
    """p. 101: dying in round 3, the first CON at the end of round 4."""
    dying = apply_wound(apply_wound(HARVEY, 8, con=Level.REGULAR).body, 7, round=3).body
    assert not wounds.dying_roll_due(dying, 3) and wounds.dying_roll_due(dying, 4)
    assert wounds.dying_roll(dying, Level.REGULAR) == dying
    assert wounds.dying_roll(dying, Level.FAILURE).dead


def test_first_aid_stabilizes_the_dying_and_medicine_follows():
    """p. 101: Harvey's friend fails, he survives his CON, the second try works: 1 temporary HP,
    hourly CON; in hospital Medicine clears the dying mark and gives back 2 (1D3)."""
    dying = apply_wound(apply_wound(HARVEY, 8, con=Level.REGULAR).body, 7, round=3).body
    assert wounds.medicine(dying, Level.HARD, 2).allowed is False  # First Aid first
    tried = wounds.first_aid(dying, False)
    assert tried.allowed and tried.body == dying
    saved = wounds.first_aid(tried.body, True, minutes=100)
    assert saved.stabilized and saved.body.hp == 1 and saved.body.stabilized and not wounds.dying_roll_due(saved.body, 9)
    assert not wounds.first_aid_allowed(saved.body)[0]
    relapse = wounds.stabilized_roll(saved.body, Level.FAILURE, round=None)
    assert relapse.dying and relapse.hp == 0 and not relapse.stabilized
    treated = wounds.medicine(saved.body, Level.REGULAR, 2)
    assert treated.healed == 2 and treated.body.hp == 3 and not treated.body.dying


def test_first_aid_once_per_injury_wakes_and_heals_one():
    """pp. 54, 100: within the hour, +1 HP, once; it wakes the unconscious."""
    b = HARVEY
    for i, n in enumerate((4, 6, 5)):
        b = apply_wound(b, n, injury=f"w{i}").body
    assert b.hp == 0 and b.has("昏迷")
    aid = wounds.first_aid(b, True)
    assert aid.woke and aid.body.hp == 1 and aid.body.conscious and aid.body.aided == ("w2",)
    again = wounds.first_aid(aid.body, True, "w2")
    assert not again.allowed and "孤注一掷" in again.why
    assert not wounds.first_aid(aid.body, True, "w1", within_hour=False).allowed
    failed = wounds.first_aid(aid.body, False, "w1")
    assert failed.body.aided == ("w2", "w1") and failed.body.hp == 1  # the one try is spent


def test_medicine_takes_a_hard_success_after_the_day_and_once_per_injury():
    """p. 101."""
    b = apply_wound(HARVEY, 5, injury="w").body
    assert wounds.medicine_difficulty(False) == Level.HARD
    assert wounds.medicine(b, Level.REGULAR, 3, same_day=False).healed == 0
    done = wounds.medicine(b, Level.REGULAR, 3)
    assert done.healed == 3 and done.body.hp == 13
    assert not wounds.medicine(done.body, Level.REGULAR, 3).allowed


def test_natural_healing():
    """pp. 101–102: a point a day without a major wound; a weekly CON with one."""
    minor = apply_wound(HARVEY, 5).body
    assert wounds.heal_day(minor).hp == 11
    major = apply_wound(HARVEY, 12, con=Level.REGULAR).body
    assert wounds.heal_day(major) == major
    assert wounds.week_dice(Level.FAILURE) == "" and wounds.week_dice(Level.HARD) == "1D3"
    assert wounds.week_dice(Level.EXTREME) == "2D3"
    assert wounds.week_bonus(rested=True, cared=True) == 2 and wounds.week_bonus(poor=True) == -1
    week = wounds.heal_week(major, Level.HARD, 3)
    assert week.hp == 6 and week.major
    assert wounds.heal_week(major, Level.FAILURE, 3).hp == 3
    assert not wounds.heal_week(week, Level.REGULAR, 2).major  # back at half (8 of 15)
    assert not wounds.heal_week(major, Level.EXTREME, 5).major


def test_a_body_comes_from_and_goes_back_onto_an_investigator():
    inv = Investigator(id="h", name="哈维", characteristics=dict.fromkeys(
        ("力量", "体质", "体型", "敏捷", "外貌", "智力", "意志", "教育"), 50), luck=50, hp=15, hp_max=15)
    hurt = apply_wound(Body.of(inv), 8, con=Level.FAILURE, injury="x", minutes=30)
    wounds.onto(inv, hurt.body)
    assert inv.hp == 7 and set(inv.conditions) == {"重伤", "昏迷"} and inv.injury.injuries == ["x"]
    assert inv.injury.unconscious_at == 30 and Body.of(inv) == hurt.body
    assert wounds.wake(hurt.body).conscious
