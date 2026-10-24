"""Current scenes must be ready and recoverable before a player action is resolved."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling, _recent
from autokeeper.agents.director import Improv, NewNPC
from autokeeper.config import Config
from autokeeper.engine.visuals import Visuals
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world.ops import AddFact, Move, Rewrite

from .test_engine import improvised, make


@pytest.fixture
def module():
    return Module.load(EXAMPLE)


async def _quiet(event):
    pass


def scene(*ops):
    return Improv(kind="展开", reason="街角来了报童", npcs=[NewNPC(id="newsboy", name="报童", role="卖报", location="square")],
                  backstage=list(ops))


async def test_invalid_world_operation_cannot_leave_a_new_character_in_the_scene(module, fake):
    game, _, _ = make(module, fake, {})
    before = game.state.model_dump(mode="json")
    applied = await game._land("展开", scene(Move(entity="warden", to="square"),
                                          Rewrite(target="fact:absent", text="不存在的真相")))
    assert applied is None and not any(n.id == "newsboy" for n in module.npcs)
    assert game.state.model_dump(mode="json") == before


async def test_a_world_change_while_drawing_cannot_be_overwritten_by_the_old_scene(module, fake, monkeypatch):
    game, _, _ = make(module, fake, {})

    async def draw(visuals, keys):
        assert game.world.apply(AddFact(fact="new_testimony", text="另一个角色刚刚提供了新证言")).ok
        return []

    monkeypatch.setattr(Visuals, "prepare", draw)
    assert await game._land("展开", scene()) is None
    assert "new_testimony" in game.state.record.facts and not game.state.improv
    assert not any(n.id == "newsboy" for n in module.npcs)


async def test_background_redraw_survives_committing_an_unrelated_scene(module, fake, monkeypatch):
    game, _, _ = make(module, fake, {})

    async def draw(visuals, keys):
        game.m.art["tower"] = "重新绘制的钟楼"
        return []

    monkeypatch.setattr(Visuals, "prepare", draw)
    assert await game._land("展开", scene()) is not None
    assert game.m.art["tower"] == "重新绘制的钟楼"


async def test_a_first_illustration_that_keeps_failing_lets_the_scene_go_on_without_it(module, fake, monkeypatch):
    ask = Action(kind="investigate", summary="看看报童卖什么")
    ruling = Ruling(needs_roll=False, scope="可即兴", minutes=10)
    game, cast, _ = improvised(module, fake, {"x": (ask, ruling)}, [scene()])
    cast.cfg = Config(imagegen=False, search="none")
    tries = []

    async def fail(visuals, keys):
        tries.append(set(keys))
        return list(keys)

    monkeypatch.setattr(Visuals, "prepare", fail)
    before = game.state.minutes
    assert await game.rule(ask) is not None
    assert [len(t) for t in tries if t] == [1, 1]  # drawn twice, then no longer waited for
    assert any(n.id == "newsboy" for n in module.npcs) and len(game.state.improv) == 1
    assert game.state.minutes == before and not game.state.checks


async def test_a_game_with_no_pictures_left_does_not_wait_for_one(module, fake):
    game, _, _ = make(module, fake, {})
    visuals = Visuals(module, game.state, Config(imagegen=False, search="none", visual_limit=1), _quiet)
    game.state.visuals["improv_scene_aaaaaaaaaaaaaaaa"] = {"art": "", "mask": "", "status": "failed", "attempts": "1"}
    assert await visuals.prepare({"improv_portrait_bbbbbbbbbbbbbbbb"}) == []


async def test_a_committed_scene_can_resume_after_its_notification_fails(module, fake):
    ask = Action(kind="investigate", summary="查看报童的报纸")
    ruling = Ruling(needs_roll=True, skill="侦查", scope="可即兴", minutes=10)
    game, cast, events = improvised(module, fake, {"x": (ask, ruling)}, [scene()])
    game.state.pending_turn = {"input": "x"}
    deliver = game.emit

    async def interrupted(event):
        if event["type"] == "improv":
            raise OSError("display disconnected")

    game.emit = interrupted
    before = game.state.minutes
    with pytest.raises(OSError):
        await game.rule(ask)
    assert game.state.pending_turn["scene_package"]["applied"]
    assert game.state.pending_turn["scene_package"]["reads"]
    assert game.state.minutes == before and len(game.state.improv) == 1
    game.emit = deliver  # the replacement display connection can receive its refresh
    fake.queue(5)
    steered = await game.rule(ask)
    turn = await game.resolve(ask, steered)
    assert len(turn.checks) == 1 and len(cast.asked) == 1 and len(cast.ruled) == 1
    assert len(game.state.improv) == 1 and game.state.minutes == before + 10
    assert sum(event["type"] == "improv" for event in events) == 1


async def test_opening_retry_keeps_its_committed_adaptation(module, fake):
    plan = scene()
    game, cast, events = improvised(module, fake, {}, [plan])
    game.state.investigator.backstory["重要之人"] = "镇上的报童"
    deliver = game.emit

    async def interrupted(event):
        if event["type"] == "improv":
            raise OSError("display disconnected")

    game.emit = interrupted
    with pytest.raises(OSError):
        await game.start()
    saved = game.state.pending_turn["scene_package"]["applied"]
    assert saved and len(game.state.improv) == 1
    game.emit = deliver
    await game.start()
    assert len(cast.asked) == 1 and not game.state.pending_turn
    assert len(game.state.improv) == 1
    assert sum(event["type"] == "improv" for event in events) == 1


def test_recent_context_preserves_a_destination_at_the_start_of_a_long_reply(module, fake):
    game, _, _ = make(module, fake, {})
    game.state.add("npc", "我们先去书房找一下那本书。" + "这是一段关于路上见闻的交谈。" * 30, who="托马斯")
    game.state.add("player", "去看看")
    context = _recent(game.state)
    assert "先去书房找一下那本书" in context and "去看看" in context
    assert "npc] 托马斯" in context
