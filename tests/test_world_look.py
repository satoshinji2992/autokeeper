"""World changes are keeper notes; scene creation remains an explicit director capability."""

from __future__ import annotations

from autokeeper.agents.cast import Action, Ruling
from autokeeper.agents.director import Improv
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops as O
from autokeeper.world import scheduler
from autokeeper.world.record import Step

from .test_engine import Improvising, make
from .test_world import staged


async def test_a_prepared_turning_point_lands_without_a_director_review():
    w = staged(location="office")
    assert w.apply(O.Move(entity="preacher", to="tower", actor="keeper")).ok
    report = scheduler.Report(tick="turning-point")
    assert scheduler._land(w, [{"kind": "condition", "entity": "warden", "status": "死亡"}],
                           actor="preacher", prefix="turning-point", report=report)
    assert not report.refused and w.entity("warden").status == "死亡"
    assert not hasattr(w, "reviewing")


async def test_a_night_collects_open_judgment_for_kp_without_a_separate_review(fake):
    module = Module.load(EXAMPLE)
    module.npc("warden").important = True
    game, cast, _ = make(module, fake, {}, location="office")
    try:
        start = game.state.minutes
        assert game.world.apply(O.SetPlan(npc="warden", steps=[
            Step(id="hide", text="考虑把日记藏到何处", due=start + 30, meet=False),
        ])).ok
        turn = await game.resolve(Action(kind="wait", summary="在报社过夜"),
                                  Ruling(needs_roll=False, minutes=8 * 60, rest=True))
        needs = scheduler.pending(game.world, ["warden"])
        assert any(n.kind == "due" and "日记" in n.text for n in needs)
        assert game.world.agent("warden").plan[-1].status == "pending"
        assert game.world.place("warden") == "tower"
        assert not hasattr(game, "_look") and not hasattr(game, "brain")
        notes = game._keeper_backstage({}, turn)
        assert "考虑把日记藏到何处" in notes
        assert not cast.reports  # collecting a note itself makes no model call
    finally:
        await game.close()


async def test_director_is_called_explicitly_to_prepare_and_apply_a_backstage_arrangement(fake):
    module = Module.load(EXAMPLE)
    module.npc("warden").important = True
    game, _, _ = make(module, fake, {}, location="office")
    plan = Improv(kind="展开", reason="守钟人决定先把日记藏好", backstage=[
        O.SetPlan(npc="warden", steps=[Step(id="hide", text="把日记藏到齿轮间", meet=False)]),
    ])
    game.cast = cast = Improvising({}, [plan])
    game.state.pending_turn = {"input": "筹备幕后"}
    try:
        game.state.minutes += 8 * 60
        await game._live(Turn())
        assert cast.asked == []
        assert await game._improvise("展开", required=True) is not None
        assert len(cast.asked) == 1 and game.world.agent("warden").plan[-1].id == "hide"
        assert await game._improvise("展开", required=True) is not None
        assert len(cast.asked) == 1  # recovery uses the applied scene receipt
    finally:
        await game.close()
