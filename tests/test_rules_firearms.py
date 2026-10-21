"""Shooting as the Keeper Rulebook settles it (rules/firearms.py, ch. 6 pp. 95–98)."""

from __future__ import annotations

from autokeeper.rules.bcdice import Level
from autokeeper.rules.firearms import (
    Shot,
    dive_for_cover,
    fire,
    fumble_hits,
    jam_cleared,
    load_one_and_fire,
    malfunction,
    point_blank,
    range_band,
    readied_bonus,
    reload,
    second_barrel_penalty,
    shot_difficulty,
    shot_modifiers,
    shots_allowed,
)
from autokeeper.rules.weapons import WEAPONS, weapon_in


def test_range_bands_set_the_difficulty_and_only_a_critical_impales_at_extreme_range():
    """p. 95: base range regular, twice it hard, four times extreme."""
    assert [range_band(d, 15) for d in (15, 16, 30, 60, 61)] == [0, 1, 1, 2, None]
    assert [shot_difficulty(b) for b in (0, 1, 2)] == [Level.REGULAR, Level.HARD, Level.EXTREME]
    assert shot_modifiers(Shot(band=2)).impale_needs == Level.CRITICAL  # Harvey's 01 at 40 yards, p. 97
    assert shot_modifiers(Shot(band=1)).impale_needs == Level.EXTREME


def test_a_shotgun_keeps_its_chance_at_every_band():
    """p. 54: its spread loses damage, not accuracy; the 12-gauge's bands are 10/20/50 yards (p. 373)."""
    assert [range_band(d, 10, shotgun=True) for d in (10, 20, 50, 51)] == [0, 1, 2, None]
    assert shot_modifiers(Shot(band=2, shotgun=True)).difficulty == Level.REGULAR


def test_the_dice_of_harveys_shots():
    """p. 97: three shots at point blank, the bonus and the penalty cancel; aiming against partial cover
    cancels too."""
    three = shot_modifiers(Shot(point_blank=True, volley=3))
    assert three.dice == 0 and len(three.reasons) == 2
    assert shot_modifiers(Shot(aimed=True, partial_cover=True)).dice == 0
    assert shot_modifiers(Shot(band=2, loaded_one=True)).dice == -1
    assert shot_modifiers(Shot(point_blank=True)).dice == 1
    assert shot_modifiers(Shot(dived=True, partial_cover=True, fast_target=True)).dice == -2  # two at most
    assert shot_modifiers(Shot(target_build=4)).dice == 1 and shot_modifiers(Shot(target_build=-2)).dice == -1
    assert shot_modifiers(Shot(into_melee=True)).dice == -1 and shot_modifiers(Shot(moving=True)).dice == -1
    assert shot_modifiers(Shot(prone_target=True, point_blank=True)).dice == 1


def test_point_blank_and_volleys():
    assert point_blank(12, 60) and not point_blank(13, 60)  # DEX/5 feet
    assert shots_allowed(1, volley=3) == 3 and shots_allowed(1) == 1 and shots_allowed(2) == 2
    assert second_barrel_penalty(True) == 0 and second_barrel_penalty(False) == -1  # p. 375


def test_a_fumble_into_a_melee_hits_the_ally_with_the_lowest_luck():
    assert fumble_hits({"roger": 55, "harvey": 40, "cissy": 70}) == "harvey"  # p. 96
    assert fumble_hits({}) == ""


def test_diving_for_cover():
    """p. 95: a Dodge; success gives the shooter a penalty die; the next attack is lost either way."""
    assert dive_for_cover(Level.REGULAR).penalty == -1
    failed = dive_for_cover(Level.FAILURE)
    assert failed.penalty == 0 and failed.lose_next and not failed.all_attacks
    assert dive_for_cover(Level.HARD, attacks_per_round=3).all_attacks


def test_malfunction_is_a_dud_for_revolvers_and_a_jam_for_a_lever_action():
    """p. 98."""
    assert malfunction(99, 100, "revolver") == "" and malfunction(100, 100, "revolver") == "dud"
    assert malfunction(98, 98, "lever") == "jam" and malfunction(99, 99, "bolt") == "dud"
    assert malfunction(97, 96, "auto") == "jam"
    assert not jam_cleared(1, 3, True) and not jam_cleared(3, 3, False) and jam_cleared(3, 3, True)


def test_reloading_and_ammunition():
    """p. 96: two cartridges a round; a magazine in one round; a belt in two."""
    assert fire(6, 3) == (3, 3) and fire(1, 3) == (1, 0)
    r = reload(0, 6, 10)
    assert (r.loaded, r.spare) == (2, 8)
    assert reload(0, 6, 10, rounds=3).loaded == 6 and reload(0, 6, 1).loaded == 1
    assert reload(2, 8, None, loading="magazine").loaded == 8 and reload(0, 8, None, loading="magazine").spare is None
    assert reload(0, 250, None, loading="belt").loaded == 0 and reload(0, 250, None, loading="belt", rounds=2).loaded == 250
    one = load_one_and_fire(0, 4)
    assert one is not None and (one.loaded, one.spare) == (1, 3) and load_one_and_fire(0, 0) is None


def test_a_readied_gun_goes_first_only_when_it_is_fired_and_can_fire():
    """p. 95: +50 DEX for a readied firearm the character means to fire."""
    assert readied_bonus(firing=True, loaded=6, jammed=False) == 50
    assert readied_bonus(firing=False, loaded=6, jammed=False) == 0  # punching with a gun in the other hand
    assert readied_bonus(firing=True, loaded=0, jammed=False) == 0
    assert readied_bonus(firing=True, loaded=6, jammed=True) == 0
    assert readied_bonus(firing=True, loaded=None, jammed=False) == 50


def test_the_catalog_follows_the_weapon_table():
    """Table XVII, pp. 371–375."""
    by = {w.name: w for w in WEAPONS}
    assert (by[".38 左轮手枪"].damage, by[".38 左轮手枪"].range, by[".38 左轮手枪"].ammo) == ("1D10", 15, 6)
    assert by[".38 左轮手枪"].volley == 3 and by[".38 左轮手枪"].mechanism == "revolver"
    assert (by[".32 自动手枪"].ammo, by[".32 自动手枪"].malfunction) == (8, 99)
    assert by["杠杆步枪"].mechanism == "lever" and by["杠杆步枪"].malfunction == 98
    assert by["霰弹枪"].shots == 2 and by["汤普森冲锋枪"].malfunction == 96
    assert by["手斧"].damage == "1D6+1+DB" and by["斧头"].damage == "1D8+2+DB"
    assert by["警棍"].damage == "1D6+DB" and by["马刀"].skill == "格斗(剑)"
    assert weapon_in("温彻斯特").name == "杠杆步枪" and weapon_in("一把剑杖").name == "剑杖"
    assert by["投掷石块"].kind == "thrown" and by["投掷石块"].base_range(60) == 12
    assert by["徒手"].natural and not by["小刀"].natural
