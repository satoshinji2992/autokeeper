"""Lost investigation routes are consequences, not reasons to reject world changes."""

from __future__ import annotations

import pytest

from autokeeper.agents.director import Improv, NewClue, NewLocation
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops as O
from autokeeper.world import review, scheduler, stage
from autokeeper.world.agents import Canned, Decision, Scene

from .test_world import staged
from .test_world_play import Directing, directed, plan_with


@pytest.mark.parametrize("location,reviewing", [("office", False), ("office", True), ("tower", False)])
async def test_an_npc_can_silence_the_last_witness(location, reviewing):
    w = staged(location=location)
    w.m.clue("c_notice").points_to = []  # only the warden's diary supports k_motive
    w.reviewing = reviewing
    assert w.apply(O.Move(entity="preacher", to="tower", actor="director")).ok
    kill = Scene(decisions=[Decision(npc="preacher", ops=[O.Condition(entity="warden", status="死亡")])])
    w.agent("preacher").wake = w.now
    report = await scheduler.advance(w, Canned(lambda ask: kill), w.now, w.now + 1)
    assert not report.refused
    if reviewing:
        [proposal] = review.waiting(w)
        assert w.entity("warden").status != "死亡"
        assert review.let_through(w, proposal) == []
        assert proposal.status == "landed"
    elif location == "tower":
        [beat] = stage.waiting(w)
        assert w.entity("warden").status != "死亡"
        assert stage.play(w, beat) == []
        assert beat.status == "played"
    assert w.entity("warden").status == "死亡"
    assert "c_diary" not in w.st.clues  # no automatic discovery to compensate for the loss


async def test_a_director_can_open_a_route_later_without_undoing_the_loss(fake):
    module = Module.load(EXAMPLE)
    module.clue("c_notice").points_to = []
    original = module.clue("c_diary").text
    later = Improv(
        kind="展开", reason="旧案留下的报纸提供新的调查方向",
        locations=[NewLocation(id="archive", name="报纸档案室", read_aloud="旧报纸按年份装订。", exits=["square"])],
        clues=[NewClue(id="c_motive_report", title="旧案报道", text="报道记下守钟人丧子的经过。",
                       where="archive", points_to=["k_motive"])],
    )
    cast = Directing([plan_with(O.Condition(entity="warden", status="死亡"))])
    game = directed(module, fake, cast)
    try:
        assert await game._improvise("展开", at="square") is not None
        assert game.world.entity("warden").status == "死亡"
        assert "c_motive_report" not in {c.id for c in module.clues}
        cast.plans.append(later)
        assert await game._improvise("展开", at="square") is not None
        assert cast.asked == [[], []]  # neither plan needs to repair the original route
        assert "archive" in module.location("square").exits
        assert module.clue("c_motive_report").points_to == ["k_motive"]
        assert "c_motive_report" not in game.state.clues  # a new source still needs investigation
        assert game.world.entity("warden").status == "死亡"
        assert module.clue("c_diary").text == original
    finally:
        await game.close()


async def test_a_director_backstage_change_need_not_preserve_the_last_source(fake):
    module = Module.load(EXAMPLE)
    module.clue("c_notice").points_to = []
    game = directed(module, fake, Directing([]))
    try:
        assert await game._backstage([O.Condition(entity="warden", status="死亡")], "证人遭到灭口") == []
        assert game.world.entity("warden").status == "死亡"
        assert "c_diary" not in game.state.clues
    finally:
        await game.close()
