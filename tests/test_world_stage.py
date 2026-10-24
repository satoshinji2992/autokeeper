"""The scene in front of the investigators belongs to the keeper (world/stage.py): backstage work
lands as it happens; what would change the scene waits as a beat and is played, then told."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops as O
from autokeeper.world import scheduler, stage
from autokeeper.world.record import Step

from .test_engine import make
from .test_world import staged


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


async def test_backstage_work_lands_at_once_and_work_in_front_of_the_party_waits_for_the_keeper():
    w = staged(location="square")
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="oil", text="给齿轮上油", due=w.now + 1,
                                                ops=[{"kind": "occur", "text": "守钟人在齿轮间里上油", "place": "tower"}])]))
    w.apply(O.SetPlan(npc="preacher", steps=[Step(id="preach", text="在广场布道", due=w.now + 1,
                                                  ops=[{"kind": "occur", "text": "布道者站上木箱开始布道", "place": "square"}])]))
    w.apply(O.Move(entity="preacher", to="square", actor="director"))
    report = await scheduler.advance(w, w.now, w.now + 5)
    texts = [e.text for e in w.rec.events]
    assert "守钟人在齿轮间里上油" in texts  # up in the tower, away from them: it simply happened
    assert "布道者站上木箱开始布道" not in texts and [b.actor for b in stage.waiting(w)] == ["preacher"]
    assert report.staged == [stage.waiting(w)[0].id]
    again = await scheduler.advance(w, w.now + 5, w.now + 10)
    assert not again.staged and len(stage.waiting(w)) == 1  # the same step is not queued twice
    assert stage.play(w, stage.waiting(w)[0]) == []
    assert any(e.text == "布道者站上木箱开始布道" and e.visibility == "public" for e in w.rec.events)


async def test_a_beat_that_no_longer_fits_is_dropped_and_its_author_thinks_again():
    w = staged(location="tower")
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="go", text="下楼去广场", due=w.now + 1,
                                                ops=[{"kind": "move", "entity": "warden", "to": "square"}])]))
    await scheduler.advance(w, w.now, w.now + 2)
    beat = stage.waiting(w)[0]
    assert beat.leaving == ["warden"]
    w.apply(O.Condition(entity="warden", status="死亡", actor="director"))
    assert stage.play(w, beat) and beat.status == "dropped"
    assert w.entity("warden").at == "tower" and w.agent("warden").refused


async def test_a_held_step_whose_condition_no_longer_holds_is_thought_again():
    w = staged(location="tower")
    show = Step(id="show", text="把日记摊给调查员看", due=w.now + 1, when="at:players:tower",
                ops=[{"kind": "occur", "text": "守钟人把日记摊开在桌上", "place": "tower"}])
    w.apply(O.SetPlan(npc="warden", steps=[show]))
    await scheduler.advance(w, w.now, w.now + 2)
    [beat] = stage.waiting(w)
    w.st.location = "square"  # the party went down before it played
    assert any("条件不再成立" in p for p in stage.play(w, beat)) and beat.status == "dropped"
    assert not any(e.text == "守钟人把日记摊开在桌上" for e in w.rec.events) and w.agent("warden").refused


def test_only_the_partys_own_travel_takes_a_companion_along():
    w = staged(location="tower")
    me = w.st.investigator.id
    assert w.apply(O.Follow(npc="warden", actor="warden", leader=me, destination="square")).ok
    for actor in ("director", "warden"):
        refused = w.apply(O.Move(entity="warden", to="office", actor=actor))
        assert not refused.ok and "结束同行" in refused.problems[0]
    assert w.apply(O.Follow(npc="warden", actor="warden")).ok  # ended in front of them first
    assert w.apply(O.Move(entity="warden", to="square", actor="warden")).ok


async def test_someone_walking_in_is_told_when_it_plays(module, fake):
    module.npc("preacher").important = True
    game, cast, _ = make(module, fake, {}, location="square")
    w = game.world
    w.apply(O.Enlist(npc="preacher", places=["square", "office"]))
    w.apply(O.Move(entity="preacher", to="office", actor="director"))
    w.apply(O.SetPlan(npc="preacher", steps=[Step(id="come", text="去广场", due=w.now + 5,
                                                  ops=[{"kind": "move", "entity": "preacher", "to": "square"}])]))
    wait = Action(kind="wait", summary="周衡在广场等了一会儿")
    turn = await game.resolve(wait, Ruling(needs_roll=False, minutes=10))
    assert w.entity("preacher").at == "square" and any("来到广场" in t for t in turn.world)
    await game.narrate(turn)
    assert "来到广场" in cast.reports[-1]
    await game.close()
