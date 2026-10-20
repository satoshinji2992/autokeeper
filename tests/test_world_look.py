"""Turning points land as they are decided; after a night or a journey the director looks
backstage and may adjust what it sees (Game._look)."""

from __future__ import annotations

import pytest

from autokeeper.agents.director import Review
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops as O
from autokeeper.world import scheduler
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step

from .test_engine import FakeCast, make
from .test_world import staged


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


class Looking(FakeCast):
    """A director whose look backstage answers from a list."""

    def __init__(self, answers):
        super().__init__({})
        self.answers, self.looked = list(answers), []

    async def review(self, state, material):
        self.looked.append(material)
        return self.answers.pop(0) if self.answers else None


async def test_a_turning_point_lands_as_it_is_decided():
    w = staged(location="office")
    w.reviewing = True  # a director is looking, and nothing waits for it
    w.apply(O.Move(entity="preacher", to="tower", actor="director"))
    w.agent("preacher").wake = w.now
    kill = Scene(decisions=[Decision(npc="preacher", judge="不能让他说出去", ops=[O.Condition(entity="warden", status="死亡")])])
    report = await scheduler.advance(w, Canned(lambda ask: kill), w.now, w.now + 1)
    assert not report.refused and w.entity("warden").status == "死亡"


def looking(module, fake, answers, *, reviewing: bool = True):
    game, _, _ = make(module, fake, {}, location="office")
    game.world.reviewing = reviewing
    game.world.apply(O.Enlist(npc="warden", actor="director"))
    game.cast = cast = Looking(answers)
    game.world.rec.simulated = game.state.minutes
    return game, cast


async def test_the_director_looks_backstage_after_a_long_stretch_of_time(module, fake):
    plan = O.SetPlan(npc="warden", steps=[Step(id="hide", text="把日记藏到齿轮间")])
    game, cast = looking(module, fake, [Review(backstage=[plan], reason="让守钟人先一步藏东西")])
    game.state.minutes += 30
    await game._live_later()
    assert cast.looked == []  # half an hour: no look
    game.state.minutes += 8 * 60  # a night
    await game._live_later()
    assert len(cast.looked) == 1 and "【世界】" in cast.looked[0]
    assert game.world.agent("warden").plan[-1].id == "hide"


async def test_without_a_director_looking_a_night_goes_by_unlooked_at(module, fake):
    game, cast = looking(module, fake, [Review()], reviewing=False)
    game.state.minutes += 8 * 60
    await game._live_later()
    assert cast.looked == []


async def test_no_answer_changes_nothing(module, fake):
    game, cast = looking(module, fake, [])
    plans = [s.id for s in game.world.agent("warden").plan]
    game.state.minutes += 8 * 60
    await game._live_later()
    assert len(cast.looked) == 1 and [s.id for s in game.world.agent("warden").plan] == plans
