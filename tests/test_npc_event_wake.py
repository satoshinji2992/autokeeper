"""Autonomous reactions follow experienced events, without advancing time or resetting allowances."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context, scheduler
from autokeeper.world import ops as O
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step, WorldRecord
from autokeeper.world.tools import Gateway

from .test_engine import make
from .test_world import staged


@pytest.fixture
def game(fake):
    module = Module.load(EXAMPLE)
    module.npc("warden").important = True
    module.events = []
    game, _, events = make(module, fake, {}, location="tower")
    game.minds = scheduler.Budget(turn=2)
    game.brain = Canned(lambda ask: Scene(decisions=[Decision(npc=npc, ops=[
        O.Occur(text="守钟人放下工具，检查窗边的门闩", place="tower"),
    ]) for npc in ask.npcs]))
    return game, events


TALK = Action(kind="talk", summary="把有人从窗边逃走的消息告诉守钟人", npc="warden", speech="刚才有人想从窗边逃走。")
QUIET = Ruling(needs_roll=False, minutes=0)


def moved_on(game, minutes: int = 5) -> None:
    """The party has gone somewhere else, and some minutes passed on the way."""
    game._moved_on = True
    game.state.minutes += minutes


async def test_a_message_is_answered_now_and_thought_over_once_the_party_moves_on(game):
    game, events = game
    clock = game.state.minutes
    turn = await game.resolve(TALK, QUIET)
    await game.talk(turn)
    assert not game.brain.asks and game.world.rec.npc_budget_calls == 0  # its voice answered it
    assert game.state.minutes == clock and not game.state.checks
    assert any(event["type"] == "npc" for event in events)
    moved_on(game)
    await game._live(Turn())
    assert len(game.brain.asks) == 1
    assert "有人想从窗边逃走" in game.brain.asks[0].reasons["warden"]
    assert "有人想从窗边逃走" in game.brain.asks[0].views["warden"]


async def test_greeting_only_dialogue_keeps_its_memory_without_a_background_call(game):
    game, _ = game
    action = Action(kind="daily", summary="向守钟人道谢", npc="warden", speech="谢谢，辛苦了。", casual=True)
    turn = await game.resolve(action, QUIET)
    await game.talk(turn)
    await game._live(turn)
    assert not game.brain.asks
    event = next(event for event in game.world.rec.events if event.op.startswith("talk:"))
    assert not event.reactive
    assert any("谢谢" in memory.text for memory in game.world.agent("warden").memory)


async def test_a_conversation_spends_none_of_the_turns_calls(game):
    game, _ = game
    game.minds = scheduler.Budget(turn=1)
    game.world.agent("warden").wake = game.state.minutes + 1
    wait = Action(kind="wait", summary="等候一分钟")
    await game.resolve(wait, Ruling(needs_roll=False, minutes=1))
    assert len(game.brain.asks) == 1
    turn = await game.resolve(TALK, QUIET)
    await game.talk(turn)
    assert len(game.brain.asks) == 1 and game.world.rec.npc_budget_calls == 1
    game.state.turn += 1
    moved_on(game)
    await game._live(Turn())
    assert len(game.brain.asks) == 2 and game.world.rec.npc_budget_calls == 1  # a new turn, its own call


async def test_repeated_live_calls_do_not_repeat_a_completed_reaction(game):
    game, _ = game
    turn = await game.resolve(TALK, QUIET)
    await game.talk(turn)
    moved_on(game)
    await game._world(turn)  # the world's turn: the reaction is decided and played
    seq, output = game.world.rec.seq, list(turn.world)
    for _ in range(3):
        await game._live(turn)
    await game.talk(turn)  # a resumed dialogue does not replay the answer or its reaction
    assert len(game.brain.asks) == 1
    assert game.world.rec.seq == seq and turn.world == output
    assert len(game.cast.npc_calls) == 1


async def test_no_decision_result_does_not_grant_a_free_retry_in_the_same_tick(game):
    game, _ = game
    game.brain = Canned(lambda ask: None)
    turn = await game.resolve(TALK, QUIET)
    await game.talk(turn)
    moved_on(game)
    await game._live(turn)
    await game._live(turn)
    assert len(game.brain.asks) == 1
    game.state.turn += 1
    moved_on(game)
    await game._live(turn)
    assert len(game.brain.asks) == 2


async def test_something_done_in_front_of_it_wakes_it_at_once(game):
    game, _ = game
    turn = await game.resolve(TALK, QUIET)
    await game.talk(turn)
    assert game.world.apply(O.Occur(text="调查员当着他的面拿走了门边的靴子。", actor=game.state.investigator.id,
                                   participants=["warden"], place="tower")).ok
    await game._live(turn)
    assert len(game.brain.asks) == 1 and game.world.rec.npc_budget_calls == 1
    assert "拿走了门边的靴子" in game.brain.asks[-1].reasons["warden"]


async def test_zero_budget_executes_an_existing_prepared_plan_without_thinking(game):
    game, _ = game
    game.minds = scheduler.Budget(turn=0, skip=0)
    assert game.world.apply(O.SetPlan(npc="warden", steps=[Step(id="walk", text="去广场", ops=[
        {"kind": "move", "entity": "warden", "to": "square"},
    ])])).ok
    await game._world(Turn())  # leaving the party's scene: played as the world's turn ends
    assert game.world.place("warden") == "square" and not game.brain.asks
    assert game.world.agent("warden").plan[0].status == "done"


async def test_a_cancelled_call_keeps_its_allowance_and_is_asked_again_on_resume():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="think", text="检查门锁")])).ok
    asked = asyncio.Event()
    hanging = asyncio.Event()
    calls = 0

    async def think(ask):
        nonlocal calls
        calls += 1
        if calls == 1:
            asked.set()
            await hanging.wait()
        return Scene(decisions=[Decision(npc=npc, ops=[
            O.Occur(text=f"{w.name(npc)}整理了随身的东西", place=w.place(npc)),
        ]) for npc in ask.npcs])

    brain = Canned(think)
    start = w.now
    task = asyncio.create_task(scheduler.advance(w, brain, start, start + 1, tick="cancelled",
                               budget=scheduler.Budget(turn=3), shared=True))
    await asyncio.wait_for(asked.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert w.rec.npc_budget_calls == 1 and w.clock is None  # the call it reserved stays spent
    assert not any("整理了随身" in event.text for event in w.rec.events)
    w.st.record = WorldRecord.model_validate_json(w.rec.model_dump_json())  # resumed from the save
    report = await scheduler.advance(w, brain, start, start + 1, tick="cancelled",
                                     budget=scheduler.Budget(turn=3), shared=True)
    assert calls == 2 and report.calls == w.rec.npc_budget_calls == 2
    assert len([event for event in w.rec.events if "整理了随身" in event.text]) == 2
    repeated = await scheduler.advance(w, brain, start, start + 1, tick="cancelled", shared=True)
    assert repeated.repeat and calls == 2 and not w.rec.npc_work


async def test_an_answer_that_came_back_lands_on_resume_without_asking_again():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="think", text="检查门锁")])).ok
    brain = Canned(lambda ask: Scene(decisions=[Decision(npc="warden", ops=[O.Occur(text="守钟人关好窗户", place="tower")])]))
    interrupted = False

    def checkpoint():
        nonlocal interrupted
        if not interrupted and any(job.get("status") == "ready" for work in w.rec.npc_work.values()
                                   for batch in work["batches"].values() for job in batch):
            interrupted = True
            raise ConnectionError("save notification interrupted")

    with pytest.raises(ConnectionError):
        await scheduler.advance(w, brain, w.now, w.now + 1, tick="ready", shared=True, checkpoint=checkpoint)
    assert not any(event.text == "守钟人关好窗户" for event in w.rec.events)
    w.st.record = WorldRecord.model_validate_json(w.rec.model_dump_json())
    w.rec.npc_budget_calls = 99  # no allowance left, and none needed
    await scheduler.advance(w, brain, w.now, w.now + 1, tick="ready", shared=True)
    assert len(brain.asks) == 1
    assert len([event for event in w.rec.events if event.text == "守钟人关好窗户"]) == 1


async def test_a_landed_answer_and_its_receipt_survive_a_checkpoint_interruption():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="think", text="检查门锁")])).ok
    brain = Canned(lambda ask: Scene(decisions=[Decision(npc=npc, ops=[
        O.Occur(text=f"{w.name(npc)}合上随身的本子", place=w.place(npc)),
    ]) for npc in ask.npcs]))
    interrupted = False

    def checkpoint():
        nonlocal interrupted
        if (not interrupted and any(job.get("status") == "done" for work in w.rec.npc_work.values()
                                    for batch in work["batches"].values() for job in batch)):
            interrupted = True
            raise ConnectionError("save notification interrupted")

    start = w.now
    with pytest.raises(ConnectionError):
        await scheduler.advance(w, brain, start, start + 1, tick="landed", shared=True, checkpoint=checkpoint)
    assert len([event for event in w.rec.events if "合上随身" in event.text]) == 2
    w.st.record = WorldRecord.model_validate_json(w.rec.model_dump_json())
    await scheduler.advance(w, brain, start, start + 1, tick="landed", shared=True)
    assert len(brain.asks) == w.rec.npc_budget_calls == 1  # nothing is asked or landed twice
    assert len([event for event in w.rec.events if "合上随身" in event.text]) == 2


def test_scheduler_receipts_do_not_invalidate_operation_undo(game):
    game, _ = game
    w = game.world
    assert w.apply(O.Occur(text="门外传来脚步声", place="tower")).ok
    w.rec.npc_budget_turn, w.rec.npc_budget_calls = w.st.turn, 1
    w.rec.npc_work["pending"] = {"turn": w.st.turn, "batches": {}}
    assert not w.undo()
    assert w.rec.npc_budget_calls == 1 and "pending" in w.rec.npc_work


def test_long_private_dialogue_preserves_late_commitments_for_authorized_recall(game):
    game, _ = game
    w = game.world
    assert w.apply(O.Enlist(npc="preacher")).ok
    assert w.apply(O.Move(entity="preacher", to="tower")).ok
    speech = "我把一路看到的情况告诉他。" * 50 + "请替我保管紫铜匣，傍晚凭银叶徽章取回。"
    answer = "守钟人详细核对了窗户与门上的痕迹。" * 50 + "我答应保管紫铜匣，只有拿银叶徽章的人才能领走。"
    action = TALK.model_copy(update={"speech": speech, "summary": "私下打算日后骗走钟楼钥匙"})
    game._exchanged("warden", action, answer, private=True)
    source = next(event for event in w.rec.events if event.op.startswith("talk:"))
    assert speech in source.text and answer in source.text and len(source.text) > 1000
    assert "私下打算" not in source.text
    for index in range(55):
        assert w.apply(O.Occur(id=f"quiet:{index}", text=f"广场上的孩子第{index}次数石阶", place="tower")).ok
    recalled = context.npc_brief(w, "warden", query="银叶徽章取回紫铜匣")
    assert "只有拿银叶徽章的人才能领走" in recalled
    lookup = Gateway(w, "warden").inspect("紫铜匣", limit=3)
    assert any(answer in event["text"] for event in lookup["events"])
    outsider = Gateway(w, "preacher").inspect("紫铜匣", limit=3)
    assert "紫铜匣" not in outsider["context"] and outsider["events"] == []
    assert "紫铜匣" not in context.npc_brief(w, "preacher", query="银叶徽章")
