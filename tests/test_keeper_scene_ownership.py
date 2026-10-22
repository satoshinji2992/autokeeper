"""A keeper's accepted plan owns the actual focus; schedules and unread memories survive."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Plan, Ruling
from autokeeper.engine.game import Game, Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import context, scheduler, stage
from autokeeper.world import ops as O
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step

from .test_engine import FakeCast
from .test_keeper_plan import PlanCast

WAIT = Action(kind="wait", summary="周衡稍等片刻")
TEXT = "我稍等片刻"


@pytest.fixture
def module() -> Module:
    module = Module.load(EXAMPLE)
    module.events, module.endings, module.agendas, module.foreshadow = [], [], [], []
    for npc in module.npcs:
        npc.important = True
    return module


def make(module, fake, *, planned=True, failed=False):
    script = {TEXT: (WAIT, Ruling(needs_roll=False, minutes=0))}
    cast = (PlanCast(script, {TEXT: RuntimeError("plan unavailable") if failed else Plan(action=WAIT)})
            if planned else FakeCast(script))
    st = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    st.location = "tower"
    game = Game(module, st, cast, Rules(dice=fake))  # type: ignore[arg-type]
    game.brain = Canned(lambda ask: Scene(decisions=[Decision(npc=n, wait=600) for n in ask.npcs]))
    game.minds = scheduler.Budget(turn=4, skip=4)
    for agent in game.world.rec.agents.values():
        agent.seen = game.world.rec.seq
    return game, cast


def witnessed(game, npc="warden", place="tower", *, ident="news"):
    return game.world.apply(O.Occur(id=ident, actor=game.state.investigator.id, place=place,
                                    participants=[npc], text=f"{npc}亲眼看见调查员留下了一张便条"))


async def test_an_accepted_plan_holds_silent_bystanders_but_not_people_elsewhere(module, fake):
    game, _ = make(module, fake)
    await game.parse(TEXT)
    assert not game.initiative
    witnessed(game)
    witnessed(game, "preacher", "square", ident="elsewhere")
    seen = game.world.agent("warden").seen
    await game._live(Turn())
    assert [list(ask.npcs) for ask in game.brain.asks] == [["preacher"]]
    assert game.world.agent("warden").seen == seen
    assert game.world.agent("preacher").seen > seen
    await game.close()


@pytest.mark.parametrize("planned,failed", [(False, False), (True, True)])
async def test_without_an_effective_keeper_plan_the_same_scene_can_react(module, fake, planned, failed):
    game, cast = make(module, fake, planned=planned, failed=failed)
    await game.parse(TEXT)
    witnessed(game)
    await game._live(Turn())
    assert [list(ask.npcs) for ask in game.brain.asks] == [["warden"]]
    assert cast.heard == [TEXT]
    await game.close()


async def test_scene_ownership_preserves_fixed_work_and_its_conditions(module, fake):
    game, _ = make(module, fake)
    await game.parse(TEXT)
    w = game.world
    assert w.apply(O.SetPlan(npc="warden", steps=[
        Step(id="oil", text="给齿轮上油", due=w.now + 1,
             ops=[{"kind": "occur", "text": "守钟人给齿轮上了油", "place": "tower"}]),
        Step(id="wait_outside", text="等调查员回广场再关门", due=w.now + 1, when="at:zhouheng:square",
             ops=[{"kind": "occur", "text": "守钟人关上门", "place": "tower"}]),
    ])).ok
    game.state.minutes += 2
    turn = Turn()
    await game._live(turn)
    assert not game.brain.asks and len(stage.waiting(w)) == 1
    game._play(turn, set())
    assert w.agent("warden").plan[0].status == "done"
    assert w.agent("warden").plan[1].status == "pending"
    assert sum(e.text == "守钟人给齿轮上了油" for e in w.rec.events) == 1
    assert not any(e.text == "守钟人关上门" for e in w.rec.events)
    await game._live(Turn())
    game._play(Turn(), set())
    assert sum(e.text == "守钟人给齿轮上了油" for e in w.rec.events) == 1
    await game.close()


async def test_unread_events_are_not_acknowledged_and_the_origin_reacts_after_the_party_leaves(module, fake):
    game, _ = make(module, fake)
    game.state.pending_turn = {"input": TEXT}
    await game.parse(TEXT)
    w = game.world
    seen = w.agent("warden").seen
    event_id = witnessed(game).event
    event = next(e for e in w.rec.events if e.id == event_id)
    game.state.pending_turn = {}  # remember clears the action receipt before its background world work
    await game._live_later()
    assert not game.brain.asks and w.agent("warden").seen == seen
    assert event in w.rec.events
    assert any("留下了一张便条" in m.text for m in w.agent("warden").memory)
    await game._arrive("square", Turn(), alone=True)
    game.state.minutes += 1
    await game._live(Turn())
    assert [list(ask.npcs) for ask in game.brain.asks] == [["warden"]]
    assert "留下了一张便条" in game.brain.asks[0].reasons["warden"]
    assert w.agent("warden").seen >= event.seq
    assert game._held() == {"preacher"}
    await game.close()


async def test_scene_ownership_tracks_arrivals_and_releases_even_a_departed_initiative_npc(module, fake):
    game, _ = make(module, fake)
    await game.parse(TEXT)
    game._directed = {"warden"}
    w = game.world
    assert w.apply(O.Move(entity="warden", to="square", actor="director")).ok
    assert w.apply(O.Move(entity="preacher", to="tower", actor="director")).ok
    assert game._held() == {"preacher"}
    await game._live(Turn())
    assert [list(ask.npcs) for ask in game.brain.asks] == [["warden"]]
    await game.close()


@pytest.mark.parametrize("continuation", [False, True])
async def test_scene_ownership_resumes_from_the_existing_arrangement_receipt(module, fake, continuation):
    game, cast = make(module, fake)
    game.state.pending_turn = {"input": TEXT}
    await game.parse(TEXT)
    st = game.state.model_copy(deep=True)
    if continuation:
        saved = st.pending_turn.pop("arrangement")
        st.pending_turn["arrangement"] = {**saved, "at": st.turn - 1}
        st.pending_turn["prepared_action"] = {"arrangement": saved}
    await game.close()
    restored = Game(module, type(st).model_validate_json(st.model_dump_json()), cast, Rules(dice=fake))
    restored.brain = Canned(lambda ask: Scene(decisions=[Decision(npc=n, wait=600) for n in ask.npcs]))
    witnessed(restored)
    seen = restored.world.agent("warden").seen
    await restored._live(Turn())
    assert not restored.brain.asks and restored.world.agent("warden").seen == seen
    restored.state.pending_turn = {}
    await restored._live_later()
    assert not restored.brain.asks
    await restored.close()


@pytest.mark.parametrize("receipt", [
    {"at": 0, "plan": {"action": WAIT.model_dump()}, "landed": True},
    {"at": 1, "plan": {"action": WAIT.model_dump()}, "landed": False},
    {"at": 1, "landed": True},
])
async def test_an_old_or_incomplete_receipt_does_not_claim_the_scene(module, fake, receipt):
    game, _ = make(module, fake, planned=False)
    game.state.turn = 1
    game.state.pending_turn = {"arrangement": receipt}
    witnessed(game)
    await game._live(Turn())
    assert [list(ask.npcs) for ask in game.brain.asks] == [["warden"]]
    await game.close()


async def test_the_keeper_gets_all_unfinished_work_and_the_actual_completion_conditions(module, fake):
    game, _ = make(module, fake)
    a = game.world.agent("warden")
    tail = "先检查所有传动齿轮。" * 12 + "最后须当面交还钥匙，不能只说已经还了。"
    a.plan = [Step(id=f"pending_{n}", text=f"待办第{n}项") for n in range(4)] + [
        Step(id="blocked_key", text=tail, status="blocked", note="门锁卡住了，尚未取得钥匙",
             due=game.world.now + 60, when="at:warden:tower", achieved="at:warden:square"),
        Step(id="already_done", text="旧的已完成事项", status="done"),
        Step(id="cancelled", text="旧的已撤回事项", status="dropped"),
    ]
    a.refused = "本次未能交还钥匙：调查员已经离开；钥匙仍在守钟人身上。"
    game.world.agent("preacher").plan = [Step(id="far_work", text="异地人物的未完成私事")]
    assert game.world.apply(O.Move(entity="preacher", to="office", actor="director")).ok
    seen = a.seen
    brief = context.keeper_brief(game.world)
    assert all(f"pending_{n}：待办第{n}项" in brief for n in range(4))
    assert tail in brief and "受阻" in brief and "最早 " in brief
    assert "前置 at:warden:tower" in brief and "完成须 at:warden:square" in brief
    assert "经过 门锁卡住了，尚未取得钥匙" in brief and a.refused in brief
    assert "旧的已完成事项" not in brief and "旧的已撤回事项" not in brief
    assert "异地人物的未完成私事" not in brief
    assert tail not in context.npc_brief(game.world, "preacher") and a.refused not in context.npc_brief(game.world, "preacher")
    assert a.seen == seen
    await game.close()
