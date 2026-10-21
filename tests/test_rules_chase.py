"""Chases as the Keeper Rulebook runs them (rules/chase.py, ch. 7 pp. 111–128): the book's own worked
examples (the angry farmer, the three cultists) and every number of ChaseRules with its page."""

from __future__ import annotations

from dataclasses import fields

from autokeeper.rules import chase
from autokeeper.rules.bcdice import Level
from autokeeper.rules.chase import RULES, ChaseRules


def test_every_number_cites_its_page_and_the_unverified_ones_are_listed():
    for f in fields(ChaseRules):
        assert f.metadata["page"].isdigit() and 111 <= int(f.metadata["page"]) <= 128, f.name
    # what the book leaves to the keeper and the project fixed (docs/rules-reference.md lists them)
    assert ChaseRules.unverified() == ["speed_floor", "barrier_cost", "act_cost", "sight_gap", "hide_far"]
    assert ChaseRules.cite("start_gap") == "p. 112" and "未核验" in ChaseRules.cite("sight_gap")
    assert (RULES.start_gap, RULES.hazard_loss, RULES.careful_max, RULES.attack_cost) == (2, "1D3", 2, 1)
    assert RULES.delay_carries and RULES.hazard_moves


def test_the_speed_roll_and_escaping_at_once():
    """p. 112: Harvey's extreme CON takes him from 6 to 7, the farmer's failure from 7 to 6."""
    harvey, farmer = chase.speed_after(6, Level.EXTREME), chase.speed_after(7, Level.FAILURE)
    assert (harvey, farmer) == (7, 6) and chase.escapes_at_once(harvey, [farmer])
    assert not chase.escapes_at_once(5, [6]) and not chase.escapes_at_once(6, [6])
    assert chase.speed_after(8, Level.REGULAR) == 8 and chase.speed_after(1, Level.FAILURE) == 1


def test_placing_three_cultists_and_three_investigators():
    """p. 125: Scott (10) is away; cultists A, B (7) at the rear, C (9) two on; Harvey (5) two ahead of
    C, Brian (8) three ahead of Harvey."""
    lineup = chase.placement({"harvey": 5, "brian": 8, "scott": 10}, {"a": 7, "b": 7, "c": 9})
    assert lineup.escaped == ["scott"] and lineup.left_behind == []
    assert lineup.positions == {"a": 0, "b": 0, "c": 2, "harvey": 4, "brian": 7}
    assert chase.placement({"q": 8}, {"slow": 5, "p": 8}).left_behind == ["slow"]
    assert chase.placement({"q": 8}, {"p": 8}, gap=5).positions == {"p": 0, "q": 2}  # never more than two
    assert chase.placement({"q": 8}, {"p": 8}, gap=1).positions["q"] == 1
    assert not chase.placement({"q": 9}, {"p": 8}).on


def test_action_points_and_the_delay_that_carries_over():
    """pp. 113–115: Harvey (5) has 1, the farmer (6) 2, Brian 3 over the slowest 4; the farmer slips
    in the mud with no points left and pays 1 of next round's 2."""
    assert chase.action_points(5, 5) == 1 and chase.action_points(6, 5) == 2 and chase.action_points(8, 5) == 4
    left, owed = chase.spend(0, 1)
    assert (left, owed) == (0, 1)
    assert chase.start_round(2, owed) == (1, 0)
    assert chase.start_round(1, 3) == (0, 2)
    assert chase.spend(3, 1) == (2, 0)
    assert chase.start_round(2, 1, ChaseRules(delay_carries=False)) == (2, 0)


def test_hazards_and_barriers():
    """pp. 114–116: careful crossing buys up to two bonus dice; a failed hazard costs 1D3 points and
    harm and still moves on; a failed barrier holds; the 5-point fence takes Harvey's 3 and stands."""
    assert chase.careful(1) == 1 and chase.careful(3) == 2 and chase.careful(-1) == 0
    slip = chase.hazard(False, 1)
    assert slip.moved and slip.lost == 1 and slip.harmed
    assert chase.hazard(True) == chase.Crossed(True)
    assert not chase.hazard(False, 2, harm=False).harmed
    assert not chase.barrier(False).moved and chase.barrier(True, opens=True).broken
    assert chase.barrier_hit(chase.BARRIER_HP["薄围栏"], 3) == (2, False)
    assert chase.barrier_hit(2, 4) == (0, True)
    assert chase.BARRIER_HP["外门"] == 15 and chase.BARRIER_HP["大树"] == 50


def test_attacks_and_shots_in_a_chase():
    """p. 116: same location (a gun excepted), one point, no more than one's attacks a round; p. 120:
    stopping to shoot costs a point, shooting on the move a penalty die."""
    assert chase.attack_problems(same_location=True, firearm=False, attacks_made=0, attacks_per_round=1, points=1) == []
    far = chase.attack_problems(same_location=False, firearm=False, attacks_made=1, attacks_per_round=1, points=0)
    assert len(far) == 3
    assert chase.attack_problems(same_location=False, firearm=True, attacks_made=0, attacks_per_round=1, points=1) == []
    assert chase.shot_cost(moving=False) == (1, 0) and chase.shot_cost(moving=True) == (0, 1)


def test_hiding_and_losing_track():
    """p. 119: Stealth against the pursuers' Spot (under 50 regular), bonus dice far ahead; out of
    sight, a pursuer's failed Track loses the quarry."""
    assert chase.hide_difficulty([40]) == Level.REGULAR and chase.hide_difficulty([40, 60]) == Level.HARD
    assert chase.hide_bonus(1) == 0 and chase.hide_bonus(2) == 1 and chase.hide_bonus(4) == 2
    assert chase.hidden(Level.REGULAR, Level.REGULAR) and not chase.hidden(Level.REGULAR, Level.HARD)
    assert chase.out_of_sight(3) and not chase.out_of_sight(2) and chase.out_of_sight(0, blocked=True)  # the start's two: in sight
    assert chase.lost_track(Level.FAILURE) and not chase.lost_track(Level.REGULAR)


def test_monsters_joiners_and_the_rest():
    """p. 121: a gnoph-keh swims at its DEX, a ghoul at a fifth; p. 120: a pursuer slower than every
    quarry is ignored; swimming at half MOV; p. 117: random hazards."""
    assert chase.monster_skill(60, "good") == 60 and chase.monster_skill(60) == 30 and chase.monster_skill(60, "poor") == 12
    assert not chase.joins("pursuer", 5, [6, 8], [7]) and chase.joins("pursuer", 6, [6, 8], [7])
    assert chase.joins("quarry", 3, [6], [7])
    assert chase.other_movement(8) == 4
    assert [chase.random_hazard(r) for r in (59, 60, 85, 96)] == [None, Level.REGULAR, Level.HARD, Level.EXTREME]
    assert chase.chase_order({"a": 50, "b": 60, "c": 50}, {"c": 3, "a": 2}) == ["b", "c", "a"]
    assert chase.CREATED["bolt"][1] == 1 and chase.CREATED["lock"][1] == "round"
