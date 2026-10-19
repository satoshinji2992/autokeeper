"""World planning consults real, scoped tools only when a decision needs more evidence."""

from __future__ import annotations

import json
from collections import deque

import pytest

from autokeeper import config, paths
from autokeeper.agents.cast import Cast
from autokeeper.engine.game import Game
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules
from autokeeper.world import context, scheduler
from autokeeper.world.agents import Ask, CastBrain
from autokeeper.world.ops import AddFact


@pytest.fixture
async def planning(monkeypatch, fake):
    script, seen = deque(), []

    class Scripted(hm.Model):
        mode = "json"

        async def _call(self, messages, *, schema, images, on_delta):
            seen.append(messages[-1]["content"])
            result = script.popleft()
            return Reply(text=json.dumps(result, ensure_ascii=False), data=result)

    monkeypatch.setitem(hm.MODELS, "world_tools_test", Scripted)
    cfg = config.Config(roles={r: config.Role("world_tools_test", "scripted", None) for r in config.DEFAULT_ROLES},
                        imagegen=False, search="none", visual_limit=0, showcase=False)
    module = Module.load(paths.EXAMPLE)
    module.npc("warden").important = True
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    cast = Cast(cfg, module)
    game = Game(module, state, cast, Rules(dice=fake))
    yield game, script, seen
    await game.close()
    await cast.close()


def asking(game):
    view = context.npc_view(game.world, "warden")
    return Ask(npcs=["warden"], views={"warden": view.text}, known={"warden": view.facts}, reasons={},
               t=game.state.minutes, place=game.world.place("warden"))


async def test_a_direct_decision_still_uses_only_one_model_call(planning):
    game, script, seen = planning
    script.append({"decisions": [{"npc": "warden", "judge": "先把钟修好"}]})
    decision = await CastBrain(game.cast, game.state).decide(asking(game))
    assert decision and decision.decisions[0].judge == "先把钟修好" and len(seen) == 1


async def test_world_query_returns_known_evidence_without_exposing_another_npcs_secret(planning):
    game, script, seen = planning
    assert game.world.apply(AddFact(fact="old_arrangement", text="记者会带来一封关于怀表的信", known_by=["warden"])).ok
    assert game.world.apply(AddFact(fact="another_secret", text="绝密哨兵词不应被检索到", known_by=["preacher"])).ok
    script.extend([{"consultations": [{"npc": "warden", "tool": "world_query", "query": "怀表的信"}]},
                   {"decisions": [{"npc": "warden", "judge": "先等记者带信过来"}]}])
    ask = asking(game)
    decision = await CastBrain(game.cast, game.state).decide(ask)
    assert decision and len(seen) == 2
    assert "记者会带来一封关于怀表的信" in seen[-1] and "绝密哨兵词" not in seen[-1]
    assert "fact:old_arrangement" in ask.consulted_reads["warden"]


async def test_preview_is_read_only_and_tracks_the_original_read_versions(planning):
    game, script, seen = planning
    before = game.world.place("warden")
    op = {"kind": "move", "entity": "warden", "to": "square"}
    script.extend([{"consultations": [{"npc": "warden", "tool": "world_preview", "ops": [op]}]},
                   {"decisions": [{"npc": "warden", "ops": [op]}]}])
    ask = asking(game)
    decision = await CastBrain(game.cast, game.state).decide(ask)
    assert decision and len(seen) == 2 and game.world.place("warden") == before
    assert not scheduler.fresh(game.world, ask.consulted_reads["warden"])
    assert "尚未执行行动" in seen[-1] and '"status": "preview"' in seen[-1]


async def test_model_cannot_change_its_tool_identity_to_director(planning):
    game, script, seen = planning
    script.extend([{"consultations": [{"npc": "director", "tool": "world_query", "query": "truth"}]},
                   {"decisions": []}])
    decision = await CastBrain(game.cast, game.state).decide(asking(game))
    assert decision is not None and len(seen) == 2
    assert "不属于本次决策" in seen[-1] and game.m.truth not in seen[-1]


async def test_tool_round_limit_defers_without_executing_a_partial_decision(planning):
    game, script, seen = planning
    query = {"consultations": [{"npc": "warden", "tool": "world_query", "query": "怀表"}],
             "decisions": [{"npc": "warden", "ops": [{"kind": "move", "entity": "warden", "to": "square"}]}]}
    before = game.world.place("warden")
    script.extend([query, query, query])
    assert await CastBrain(game.cast, game.state).decide(asking(game)) is None
    assert len(seen) == 3 and game.world.place("warden") == before
