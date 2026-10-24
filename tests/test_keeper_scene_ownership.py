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
    quiet = scheduler.collect(game.world, moved_on=True)
    scheduler.acknowledge(game.world, {note.id: note.version for note in quiet})
    return game, cast


def witnessed(game, npc="warden", place="tower", *, ident="news"):
    return game.world.apply(O.Occur(id=ident, actor=game.state.investigator.id, place=place,
                                    participants=[npc], text=f"{npc}亲眼看见调查员留下了一张便条"))


async def test_an_accepted_plan_preserves_observations_as_notes_instead_of_running_npc_workers(module, fake):
    game, _ = make(module, fake)
    await game.parse(TEXT)
    assert not game.initiative
    witnessed(game)
    witnessed(game, "preacher", "square", ident="elsewhere")
    previously_considered = {note.id for note in game.world.rec.backstage.values() if note.status == "considered"}
    await game._live(Turn())
    notes = scheduler.pending(game.world)
    assert {note.npc for note in notes if "留下了一张便条" in note.text} == {"warden", "preacher"}
    assert all(game.world.rec.backstage[ident].status == "considered" for ident in previously_considered)
    assert game.world.place("warden") == "tower" and game.world.place("preacher") == "square"
    await game.close()


@pytest.mark.parametrize("planned,failed", [(False, False), (True, True)])
async def test_without_an_effective_keeper_plan_the_same_scene_can_react(module, fake, planned, failed):
    game, cast = make(module, fake, planned=planned, failed=failed)
    await game.parse(TEXT)
    witnessed(game)
    await game._live(Turn())
    assert any(note.npc == "warden" and "留下了一张便条" in note.text for note in scheduler.pending(game.world))
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
    assert len(stage.waiting(w)) == 1
    game._play(turn, set())
    assert w.agent("warden").plan[0].status == "done"
    assert w.agent("warden").plan[1].status == "pending"
    assert sum(e.text == "守钟人给齿轮上了油" for e in w.rec.events) == 1
    assert not any(e.text == "守钟人关上门" for e in w.rec.events)
    await game._live(Turn())
    game._play(Turn(), set())
    assert sum(e.text == "守钟人给齿轮上了油" for e in w.rec.events) == 1
    await game.close()


async def test_reading_unfinished_notes_does_not_acknowledge_them_when_the_party_leaves(module, fake):
    game, _ = make(module, fake)
    game.state.pending_turn = {"input": TEXT}
    await game.parse(TEXT)
    w = game.world
    event_id = witnessed(game).event
    event = next(e for e in w.rec.events if e.id == event_id)
    game.state.pending_turn = {}
    await game._live(Turn())
    receipt = {}
    assert "留下了一张便条" in game._keeper_backstage(receipt, Turn())
    note, = (note for note in scheduler.pending(w) if note.source == f"event:warden:{event_id}")
    original_ticket = (note.id, note.version)
    captured = receipt["backstage"]["tickets"]
    assert captured and all(w.rec.backstage[key].status == "pending" for key in captured)
    assert event in w.rec.events
    assert any("留下了一张便条" in m.text for m in w.agent("warden").memory)
    await game._arrive("square", Turn(), alone=True)
    game.state.minutes += 1
    await game._live(Turn())
    assert any((note.id, note.version) == original_ticket for note in scheduler.pending(w))
    await game.close()


async def test_departed_initiative_does_not_hide_changed_scene_membership_from_the_keeper(module, fake):
    game, _ = make(module, fake)
    await game.parse(TEXT)
    w = game.world
    assert w.apply(O.Move(entity="warden", to="square", actor="director")).ok
    assert w.apply(O.Move(entity="preacher", to="tower", actor="director")).ok
    await game._live(Turn())
    assert game._present() == ("preacher",)
    assert w.place("warden") == "square"
    assert any(note.npc == "warden" for note in scheduler.pending(w))
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
    event_id = witnessed(restored).event
    await restored._live(Turn())
    note, = (note for note in scheduler.pending(restored.world) if note.source == f"event:warden:{event_id}")
    original_ticket = (note.id, note.version)
    restored.state.pending_turn = {}
    await restored._live(Turn())
    assert any((note.id, note.version) == original_ticket for note in scheduler.pending(restored.world))
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
    assert any(note.npc == "warden" and "留下了一张便条" in note.text for note in scheduler.pending(game.world))
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
    tickets = {note.id: (note.version, note.status) for note in game.world.rec.backstage.values()}
    brief = context.keeper_brief(game.world)
    assert all(f"pending_{n}：待办第{n}项" in brief for n in range(4))
    assert tail in brief and "受阻" in brief and "最早 " in brief
    assert "前置 at:warden:tower" in brief and "完成须 at:warden:square" in brief
    assert "经过 门锁卡住了，尚未取得钥匙" in brief and a.refused in brief
    assert "旧的已完成事项" not in brief and "旧的已撤回事项" not in brief
    assert "异地人物的未完成私事" not in brief
    assert tail not in context.npc_brief(game.world, "preacher") and a.refused not in context.npc_brief(game.world, "preacher")
    assert {note.id: (note.version, note.status) for note in game.world.rec.backstage.values()} == tickets
    await game.close()
