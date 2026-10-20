"""Turning points wait for the director's look between turns (world/review.py, Game._review)."""

from __future__ import annotations

import pytest

from autokeeper.agents.director import Review, Verdict
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops as O
from autokeeper.world import review, scheduler
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step

from .test_engine import FakeCast, make
from .test_world import staged


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


async def held_kill(w):
    """The preacher means to do the warden in, up in the tower, while the party is at the office."""
    w.reviewing = True
    if w.agent("preacher") is None:
        assert w.apply(O.Enlist(npc="preacher", actor="director")).ok
    w.apply(O.Move(entity="preacher", to="tower", actor="director"))
    w.agent("preacher").wake = w.now
    scene = Scene(decisions=[Decision(npc="preacher", judge="不能让他说出去", ops=[O.Condition(entity="warden", status="死亡")])])
    return await scheduler.advance(w, Canned(lambda ask: scene), w.now, w.now + 1)


async def test_a_turning_point_waits_for_the_director_while_one_is_looking():
    w = staged(location="office")
    report = await held_kill(w)
    assert report.held and w.entity("warden").status != "死亡"
    [proposal] = review.waiting(w)
    assert proposal.actor == "preacher" and "老守钟人死了" in proposal.why and proposal.text == "不能让他说出去"
    asked = []
    await scheduler.advance(w, Canned(lambda ask: asked.append(ask) or Scene(decisions=[])), w.now + 1, w.now + 300)
    assert not any("preacher" in ask.npcs for ask in asked)  # the preacher does not act again while it waits


async def test_without_a_director_looking_nothing_is_held():
    w = staged(location="office")
    w.reviewing = False
    w.apply(O.Move(entity="preacher", to="tower", actor="director"))
    w.agent("preacher").wake = w.now
    scene = Scene(decisions=[Decision(npc="preacher", ops=[O.Condition(entity="warden", status="死亡")])])
    report = await scheduler.advance(w, Canned(lambda ask: scene), w.now, w.now + 1)
    assert not report.held and w.entity("warden").status == "死亡"


class Reviewing(FakeCast):
    """A director whose look between turns answers from a list."""

    def __init__(self, answers):
        super().__init__({})
        self.answers, self.looked = list(answers), []

    async def review(self, state, material):
        self.looked.append(material)
        return self.answers.pop(0) if self.answers else None


async def looked_at(module, fake, answer):
    game, _, _ = make(module, fake, {}, location="office")
    game.cast = cast = Reviewing([answer])
    await held_kill(game.world)
    await game._review()
    return game, cast


async def test_the_director_lets_it_through(module, fake):
    game, cast = await looked_at(module, fake, Review(verdicts=[]))
    assert "老守钟人死了" in cast.looked[0] and "【世界】" in cast.looked[0]
    assert game.world.entity("warden").status == "死亡" and not review.waiting(game.world)


async def test_the_director_turns_it_down_and_says_why(module, fake):
    proposal_id = None

    def answer(game):
        return Review(verdicts=[Verdict(id=review.waiting(game.world)[0].id, choice="否决", why="镇上人多眼杂，现在动手太冒险")])

    game, _, _ = make(module, fake, {}, location="office")
    await held_kill(game.world)
    proposal_id = review.waiting(game.world)[0].id
    game.cast = Reviewing([answer(game)])
    await game._review()
    assert game.world.entity("warden").status != "死亡"
    assert next(p for p in game.world.rec.proposals if p.id == proposal_id).status == "dropped"
    assert any("太冒险" in m.text for m in game.world.agent("preacher").memory)


async def test_put_off_it_goes_ahead_when_its_time_comes(module, fake):
    game, _, _ = make(module, fake, {}, location="office")
    await held_kill(game.world)
    game.cast = Reviewing([Review(verdicts=[Verdict(id=review.waiting(game.world)[0].id, choice="推迟", minutes=120)])])
    await game._review()
    assert game.world.entity("warden").status != "死亡" and review.waiting(game.world)[0].status == "deferred"
    game.state.minutes += 130
    await game._review()
    assert game.world.entity("warden").status == "死亡"


async def test_no_answer_lets_everything_through(module, fake):
    game, _ = await looked_at(module, fake, None)
    assert game.world.entity("warden").status == "死亡"


async def test_the_look_is_also_a_chance_for_a_backstage_adjustment(module, fake):
    game, _, _ = make(module, fake, {}, location="office")
    game.world.reviewing = True
    game.state.turn, game.state.progress_at = 5, 5  # the story has moved since the last look
    plan = O.SetPlan(npc="warden", steps=[Step(id="hide", text="把日记藏到齿轮间")])
    game.world.apply(O.Enlist(npc="warden", actor="director"))
    game.cast = Reviewing([Review(backstage=[plan], reason="让守钟人先一步藏东西")])
    await game._review()
    assert game.world.agent("warden").plan[-1].id == "hide"
