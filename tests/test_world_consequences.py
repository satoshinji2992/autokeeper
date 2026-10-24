"""Lost investigation routes are consequences, not reasons to reject world changes."""

from __future__ import annotations

import pytest

from autokeeper.agents.director import Improv, NewClue, NewLocation
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops as O
from autokeeper.world import scheduler, stage

from .test_engine import Improvising, make
from .test_world import staged


def directed(module, fake, cast):
    game, _, _ = make(module, fake, {}, location="square")
    game.cast = cast
    game.state.pending_turn = {"input": "筹备新的调查方向"}
    return game


@pytest.mark.parametrize("location", ["office", "tower"])
async def test_an_npc_can_silence_the_last_witness(location):
    w = staged(location=location)
    w.m.clue("c_notice").points_to = []  # only the warden's diary supports k_motive
    assert w.apply(O.Move(entity="preacher", to="tower", actor="director")).ok
    report = scheduler.Report(tick="silence-witness")
    assert scheduler._land(w, [{"kind": "condition", "entity": "warden", "status": "死亡"}],
                           actor="preacher", prefix="silence-witness", report=report)
    assert not report.refused
    if location == "tower":  # in front of the party: it waits for the keeper, then lands whole
        [beat] = stage.waiting(w)
        assert w.entity("warden").status != "死亡"
        assert stage.play(w, beat) == []
        assert beat.status == "played"
    assert w.entity("warden").status == "死亡"  # offscreen a turning point lands directly
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
    cast = Improvising({}, [Improv(kind="展开", reason="证人遭到灭口", backstage=[O.Condition(entity="warden", status="死亡")])])
    game = directed(module, fake, cast)
    try:
        assert await game._improvise("展开", at="square", required=True) is not None
        assert game.world.entity("warden").status == "死亡"
        assert "c_motive_report" not in {c.id for c in module.clues}
        cast.plans.append(later)
        game.state.pending_turn = {"input": "再次筹备新的方向"}
        assert await game._improvise("展开", at="square", required=True) is not None
        assert len(cast.asked) == 2 and all(not asked[2] for asked in cast.asked)  # neither plan needs to repair the original route
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
    cast = Improvising({}, [Improv(kind="展开", reason="证人遭到灭口", backstage=[O.Condition(entity="warden", status="死亡")])])
    game = directed(module, fake, cast)
    try:
        assert await game._improvise("展开", required=True) is not None
        assert len(cast.asked) == 1
        assert game.world.entity("warden").status == "死亡"
        assert "c_diary" not in game.state.clues
    finally:
        await game.close()
