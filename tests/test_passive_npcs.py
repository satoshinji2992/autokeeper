"""NPCs with an agenda the module does not mark important (world/agents.marked, Agent.passive): only
their fixed steps run, nothing wakes them to decide. Scripted brains, no model calls."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import game as game_module
from autokeeper.engine.game import Game
from autokeeper.module.schema import Agenda, Module, Move
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import ops as O
from autokeeper.world import scheduler
from autokeeper.world.agents import Canned, Scene, marked
from autokeeper.world.record import Agent

from .test_engine import FakeCast, make

WAIT = Action(kind="wait", summary="周衡在报社等着")


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.agendas = [Agenda(npc="preacher", goals=["把信众带到钟楼下"], steps=[
        Move(id="tower", text="去钟楼底下布道", when="D1 14:30", does="at:preacher:tower"),
        Move(id="think", text="想想要不要去报社找记者", when="D1 14:30"),  # no outcome: a judgement, not a fixed step
        Move(id="never", text="等钟停了就收摊", when="D1 14:30 & flag:bell_stopped", does="at:preacher:square"),
    ])]
    m.flags.setdefault("bell_stopped", "钟停了")
    return m


def play(module, fake):
    game, _, _ = make(module, fake, {}, location="office")  # away from the party: his steps land offscreen
    asked: list = []
    game.brain = Canned(lambda ask: asked.append(ask) or Scene(decisions=[]))  # type: ignore[assignment]
    game.minds = scheduler.Budget(turn=2, skip=4)
    return game, asked


async def test_a_passive_npc_keeps_his_schedule_and_is_never_woken_to_decide(module, fake):
    game, asked = play(module, fake)
    w = game.world
    assert w.agent("preacher").passive and "warden" not in w.rec.agents
    await game.resolve(WAIT, Ruling(needs_roll=False, minutes=240, reason="", rest=True))  # to 18:10, past every grace
    plan = {s.id: s.status for s in w.agent("preacher").plan}
    assert w.place("preacher") == "tower" and plan["tower"] == "done"  # on time, with no model
    assert plan["think"] == "pending" and plan["never"] == "blocked"  # not due-woken, not woken when blocked
    assert asked == []
    await game.close()


async def test_the_same_npc_marked_important_is_woken_as_before(module, fake):
    module.npc("preacher").important = True
    game, asked = play(module, fake)
    assert not game.world.agent("preacher").passive
    await game.resolve(WAIT, Ruling(needs_roll=False, minutes=240, reason="", rest=True))
    assert game.world.place("preacher") == "tower"
    reasons = [ask.reasons["preacher"] for ask in asked]
    assert reasons and all("受阻" in r or "该做了" in r for r in reasons)  # one call a slice, the weightiest reason
    await game.close()


async def test_nothing_that_happens_to_a_passive_npc_wakes_him_and_the_director_can_enlist_him(module, fake):
    game, asked = play(module, fake)
    w = game.world
    w.apply(O.Occur(id="shout", actor="warden", text="守钟人冲他吼了一声", place="square", participants=["preacher"]))
    w.agent("preacher").wake = w.now
    await game.resolve(WAIT, Ruling(needs_roll=False, minutes=5, reason=""))
    assert asked == []
    w.apply(O.Enlist(id="enlist:preacher:later", actor="director", npc="preacher", goals=["阻止钟声"]))
    assert not w.agent("preacher").passive  # the director's enlisting makes him one who decides
    w.apply(O.Occur(id="shout2", actor="warden", text="守钟人又吼了一声", place="square", participants=["preacher"]))
    await game.resolve(WAIT, Ruling(needs_roll=False, minutes=5, reason=""))
    assert [ask.npcs for ask in asked] == [["preacher"]]
    await game.close()


async def test_a_save_from_before_gets_its_passive_npcs_and_an_old_record_still_loads(module, fake, monkeypatch):
    assert Agent.model_validate({"npc": "x"}).passive is False
    real = marked
    monkeypatch.setattr(game_module, "marked", lambda w: [op for op in real(w) if not op.id.endswith(":passive")])
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    old = Game(module, state, FakeCast({}), Rules(dice=fake))  # type: ignore[arg-type]
    assert not old.world.agent("preacher").passive
    await old.close()
    monkeypatch.setattr(game_module, "marked", real)
    again = Game(module, state, FakeCast({}), Rules(dice=fake))  # type: ignore[arg-type]
    assert again.world.agent("preacher").passive
    await again.close()
