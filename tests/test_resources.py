"""A 0 in an investigator's resources is a number, not a blank to fill (rules/coc.Investigator): at SAN
0 they are permanently insane, at HP 0 down, out of luck or magic at 0, and so they stay through every
save and load: the sheet, the whole game, the world's own copy, a kept card, a sheet brought in. What a
sheet leaves out is still worked out from its characteristics. The review of 2026-10-21: a save at
SAN 0 came back at POW."""

from __future__ import annotations

import pytest

from autokeeper import config, paths, roster
from autokeeper.agents.card import Builder, parse_st
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net import cards as netcards
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Investigator
from autokeeper.rules.creation import accept
from autokeeper.tui.library import write_save
from autokeeper.world.store import World

STATS = {"力量": 50, "体质": 60, "体型": 55, "敏捷": 65, "外貌": 60, "智力": 75, "意志": 60, "教育": 70}


def spent(inv: Investigator) -> Investigator:
    """At the end of everything: no sanity, no hit points, no magic and no luck left."""
    inv.san = inv.hp = inv.mp = inv.luck = inv.san_start_of_day = 0
    inv.add_condition("永久疯狂")
    inv.add_condition("濒死")
    return inv


def pools(inv: Investigator) -> tuple[int, ...]:
    return inv.san, inv.hp, inv.mp, inv.luck, inv.san_start_of_day


def test_a_zero_survives_the_character_card_round_trip(inv):
    spent(inv)
    for back in (Investigator.model_validate_json(inv.model_dump_json()), Investigator.model_validate(inv.model_dump(mode="json"))):
        assert pools(back) == (0, 0, 0, 0, 0) and back.conditions == ["永久疯狂", "濒死"]
        assert (back.hp_max, back.mp_max) == (inv.hp_max, inv.mp_max) and not back.can_act


def test_what_a_sheet_leaves_out_is_still_worked_out():
    fresh = Investigator(id="a", name="甲", characteristics=STATS, luck=50)
    assert (fresh.hp_max, fresh.hp, fresh.mp_max, fresh.mp, fresh.san, fresh.san_start_of_day) == (11, 11, 12, 12, 60, 60)
    wounded = Investigator(id="b", name="乙", characteristics=STATS, luck=50, hp=4, san=40)
    assert (wounded.hp, wounded.hp_max, wounded.san, wounded.san_start_of_day) == (4, 11, 40, 40)
    capped = Investigator(id="c", name="丙", characteristics=STATS, luck=50, hp_max=9, mp_max=10)  # maxima, no current values
    assert (capped.hp, capped.mp) == (9, 10)


async def test_a_zero_survives_the_whole_game_save():
    m = Module.load(EXAMPLE)
    state = Game.new(m, [spent(m.pregens[0].model_copy(deep=True))])
    write_save(state, m)
    back = GameState.model_validate_json(paths.save(state.slot).read_text(encoding="utf-8"))  # as a save is loaded
    assert pools(back.investigator) == (0, 0, 0, 0, 0) and "永久疯狂" in back.investigator.conditions
    w = World(m, back)
    w.load(w.domain())  # what an op that fails puts back (World.apply)
    assert pools(back.investigator) == (0, 0, 0, 0, 0)


def test_a_zero_survives_a_kept_card(inv):
    roster.keep(spent(inv), {"copied": "测试"})
    [back] = roster.saved()
    assert pools(back) == (0, 0, 0, 0, 0)


def test_a_sheet_brought_in_takes_a_zero_as_written_and_works_out_what_it_leaves_out():
    inv, fixes = accept("c", name="沈芸", characteristics=STATS, skills={}, luck=0, san=0, hp=0, mp=0)
    assert pools(inv) == (0, 0, 0, 0, 0) and fixes == []
    blank, fixes = accept("d", name="沈芸", characteristics=STATS, skills={})
    assert (blank.san, blank.hp, blank.mp, blank.luck) == (60, 11, 12, 50) and fixes == ["原卡没写幸运，按 50 算"]


async def test_a_dice_bot_line_with_zeros_is_read_as_written():
    line = ".st 力量50体质60体型55敏捷65外貌60智力75意志60教育70理智0幸运0hp0mp0侦查60"
    found = parse_st(line)
    assert found is not None and (found["san"], found["luck"], found["hp"], found["mp"]) == (0, 0, 0, 0)
    b = Builder(config.Config(), Module.load(EXAMPLE))
    assert await b.read(line) == "sheet"  # a bare line: no model asked
    assert b.card is not None and (b.card.san, b.card.hp, b.card.mp, b.card.luck) == (0, 0, 0, 0)


def test_a_card_brought_to_a_table_keeps_its_zeros_and_one_that_cannot_act_is_refused():
    card = {"name": "沈芸", "characteristics": STATS, "luck": 60, "skills": {}}
    assert netcards.vet(card | {"mp": 0}, "x").mp == 0  # magic spent, not refilled
    for pool in ("san", "hp"):  # permanently insane, down: not healed into a seat
        with pytest.raises(netcards.Refused, match="无法参加调查"):
            netcards.vet(card | {pool: 0}, "x")
