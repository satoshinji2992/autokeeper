"""Experienced changes wait for the keeper; dialogue memories keep their original audience."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context, scheduler
from autokeeper.world import ops as O
from autokeeper.world.record import Step
from autokeeper.world.tools import Gateway

from .test_engine import make


@pytest.fixture
def game(fake):
    module = Module.load(EXAMPLE)
    module.npc("warden").important = True
    module.events = []
    game, _, events = make(module, fake, {}, location="tower")
    return game, events


TALK = Action(kind="talk", summary="把有人从窗边逃走的消息告诉守钟人", npc="warden", speech="刚才有人想从窗边逃走。")
QUIET = Ruling(needs_roll=False, minutes=0)


async def test_message_is_answered_now_and_kept_for_kp_when_the_party_moves_on(game):
    game, events = game
    clock = game.state.minutes
    turn = await game.resolve(TALK, QUIET)
    await game.talk(turn)
    assert game.state.minutes == clock and not game.state.checks
    assert any(event["type"] == "npc" for event in events)
    game._moved_on = True
    game.state.minutes += 5
    await game._live(Turn())
    assert any("有人想从窗边逃走" in n.text for n in scheduler.pending(game.world, ["warden"]))
    assert not hasattr(game, "brain")
    await game.close()


async def test_greeting_only_dialogue_keeps_its_memory_without_a_background_call(game):
    game, _ = game
    action = Action(kind="daily", summary="向守钟人道谢", npc="warden", speech="谢谢，辛苦了。", casual=True)
    turn = await game.resolve(action, QUIET)
    await game.talk(turn)
    await game._live(turn)
    event = next(event for event in game.world.rec.events if event.op.startswith("talk:"))
    assert not event.reactive
    assert any("谢谢" in memory.text for memory in game.world.agent("warden").memory)
    assert not [n for n in scheduler.pending(game.world, ["warden"]) if "谢谢" in n.text]
    await game.close()


async def test_fixed_work_in_front_of_the_party_executes_without_an_independent_model(game):
    game, _ = game
    assert game.world.apply(O.SetPlan(npc="warden", steps=[Step(id="walk", text="去广场", ops=[
        {"kind": "move", "entity": "warden", "to": "square"},
    ])])).ok
    await game._world(Turn())
    assert game.world.place("warden") == "square"
    assert game.world.agent("warden").plan[0].status == "done"
    assert not hasattr(game, "brain")
    await game.close()


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
