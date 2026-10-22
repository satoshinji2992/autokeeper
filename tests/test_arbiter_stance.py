"""Rulings use the current agreement and willingness, not a character's initial goal."""

import pytest

from autokeeper import config
from autokeeper.agents.cast import Action, Cast
from autokeeper.engine.game import Game
from autokeeper.harness import Reply
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import ops as O


def game_at_tower(fake, monkeypatch):
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    cast = Cast(config.Config(imagegen=False, showcase=False), module)
    game = Game(module, state, cast, Rules(dice=fake))
    game.write_progress = False
    prompts = []

    async def send(key, session, state, prompt, **kwargs):
        assert key == "arbiter"
        prompts.append(prompt)
        return Reply(data={"needs_roll": False, "minutes": 1})

    monkeypatch.setattr(cast, "_send", send)
    return game, cast, prompts


def local_stance(prompt):
    return prompt.split("【本地人物的处境】\n", 1)[1].split("【调查员重要装备状态】", 1)[0]


async def test_the_arbiter_gets_current_goals_and_agreed_work_after_the_exchange_leaves_recent_history(fake, monkeypatch):
    game, cast, prompts = game_at_tower(fake, monkeypatch)
    w, me = game.world, game.state.investigator.id
    assert w.apply(O.Enlist(npc="warden")).ok
    assert w.apply(O.SetPlan(npc="warden", goals=["陪调查员修好停转装置"], reason="调查员已经救出他的家人")).ok
    assert w.apply(O.MakePromise(promise="repair", by="warden", to=me, text="借出工具并带路到钟机旁",
                                due=w.now + 120)).ok
    assert w.apply(O.Follow(npc="warden", leader=me, destination="square")).ok
    for index in range(30):
        game.state.add("keeper", f"记录无关的天气变化第{index}次。")
    action = Action(kind="talk", npc="warden", summary="请他按刚才约定拿工具")
    ruled = await cast.arbiter(game.state, action)
    situation = local_stance(prompts[0])
    assert "陪调查员修好停转装置" in situation and "调查员已经救出他的家人" in situation
    assert "借出工具并带路到钟机旁" in situation and "已同意同行" in situation
    assert w.m.npc("warden").goal not in situation
    assert len(prompts) == 1 and not ruled.needs_roll
    await game.close()


@pytest.mark.parametrize("settlement", [O.Break, O.Fulfil])
async def test_the_same_arbiter_session_does_not_revive_cleared_goals_or_cancelled_agreements(fake, monkeypatch, settlement):
    game, cast, prompts = game_at_tower(fake, monkeypatch)
    w, me = game.world, game.state.investigator.id
    assert w.apply(O.Enlist(npc="warden", goals=["先送调查员回去"])).ok
    assert w.apply(O.MakePromise(promise="old", by="warden", to=me, text="今天陪同去报社")).ok
    assert w.apply(O.Follow(npc="warden", leader=me)).ok
    action = Action(kind="talk", npc="warden", summary="问现在是否同行")
    await cast.arbiter(game.state, action)
    assert "今天陪同去报社" in local_stance(prompts[0])
    assert w.apply(O.SetPlan(npc="warden", goals=[], reason="已经送达，他现在要独自休息")).ok
    assert w.apply(settlement(promise="old", actor="warden")).ok
    assert w.apply(O.Follow(npc="warden", leader="")).ok
    await cast.arbiter(game.state, action)
    situation = local_stance(prompts[1])
    assert "当前目标（以此为准）：无" in situation
    assert "已经送达，他现在要独自休息" in situation
    assert "今天陪同去报社" not in situation and "已同意同行" not in situation
    assert w.m.npc("warden").goal not in situation
    assert len(prompts) == 2
    await game.close()


async def test_the_local_ruling_does_not_pull_every_npcs_memories_and_promises_into_the_scene(fake, monkeypatch):
    game, cast, prompts = game_at_tower(fake, monkeypatch)
    w, me = game.world, game.state.investigator.id
    assert w.apply(O.Enlist(npc="warden", goals=["守着现场"])).ok
    assert w.apply(O.Enlist(npc="preacher", goals=["远处人物的目标不挤入本地处境"])).ok
    assert w.apply(O.MakePromise(promise="offsite", by="preacher", to=me, text="外地会面约定")).ok
    assert w.apply(O.Remember(npc="warden", text="整段旧记忆不应为本地意愿摘要重发")).ok
    await cast.arbiter(game.state, Action(kind="talk", npc="warden", summary="询问当前安排"))
    situation = local_stance(prompts[0])
    assert "守着现场" in situation
    assert "远处人物的目标不挤入本地处境" not in situation and "外地会面约定" not in situation
    assert "整段旧记忆不应为本地意愿摘要重发" not in situation
    await game.close()


async def test_a_cast_without_a_world_still_uses_the_module_goal(fake, monkeypatch):
    game, cast, prompts = game_at_tower(fake, monkeypatch)
    cast.world = None
    await cast.arbiter(game.state, Action(kind="talk", npc="warden", summary="询问钟楼开放情况"))
    assert game.m.npc("warden").goal in local_stance(prompts[0])
    await game.close()
