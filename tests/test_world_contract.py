"""The module stays solvable, whoever writes to the world (world/contract.py)."""

from __future__ import annotations

from autokeeper.world import contract, scheduler
from autokeeper.world import ops as O
from autokeeper.world.agents import Canned, Decision, Scene

from .test_world import staged


def lonely_motive(w):
    """The motive now rests on the warden's diary alone."""
    w.m.clue("c_notice").points_to = []
    return w


async def test_an_npc_cannot_silence_the_only_one_who_can_tell_it():
    w = lonely_motive(staged(location="office"))
    w.apply(O.Move(entity="preacher", to="tower", actor="director"))
    kill = Scene(decisions=[Decision(npc="preacher", ops=[O.Condition(entity="warden", status="死亡")])])
    w.agent("preacher").wake = w.now
    report = await scheduler.advance(w, Canned(lambda ask: kill), w.now, w.now + 1)
    assert any("k_motive" in r for r in report.refused) and w.entity("warden").status != "死亡"


async def test_once_they_hold_the_clue_the_same_change_may_land():
    w = lonely_motive(staged(location="office"))
    w.st.clues.append("c_diary")
    w.apply(O.Move(entity="preacher", to="tower", actor="director"))
    kill = Scene(decisions=[Decision(npc="preacher", ops=[O.Condition(entity="warden", status="死亡")])])
    w.agent("preacher").wake = w.now
    report = await scheduler.advance(w, Canned(lambda ask: kill), w.now, w.now + 1)
    assert not report.refused and w.entity("warden").status == "死亡"


def test_ways_follow_the_world_as_it_stands():
    w = lonely_motive(staged(location="office"))
    assert contract.ways(w) == {"k_tower": True, "k_motive": True}
    w.apply(O.Condition(entity="warden", status="失踪", actor="director"))
    assert contract.ways(w)["k_motive"] is False and contract.ways(w)["k_tower"] is True


async def test_a_director_plan_that_would_cut_a_conclusion_goes_back_with_why(fake):
    from autokeeper.module.schema import Module
    from autokeeper.paths import EXAMPLE

    from .test_world_play import Directing, directed, plan_with

    module = Module.load(EXAMPLE)
    module.clue("c_notice").points_to = []
    cast = Directing([plan_with(O.Condition(entity="warden", status="死亡")), plan_with()])
    game = directed(module, fake, cast)
    assert await game._improvise("展开", at="square") is not None
    assert any("k_motive" in p and "补一条路" in p for p in cast.asked[1])
    assert game.world.entity("warden").status != "死亡"
