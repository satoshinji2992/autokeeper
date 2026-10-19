"""Performers use the same scoped knowledge and validated world changes as other NPCs."""

from __future__ import annotations

import json
from collections import deque

import pytest

from autokeeper import config, paths
from autokeeper.agents.cast import Cast
from autokeeper.engine.game import Game
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Item, Module
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import Occur


@pytest.fixture
async def performance(monkeypatch, fake):
    script, seen = deque(), []

    class Scripted(hm.Model):
        async def _call(self, messages, **kwargs):
            seen.append([dict(message) for message in messages])
            answer = script.popleft()
            return Reply(text=json.dumps(answer, ensure_ascii=False), data=answer)

    monkeypatch.setitem(hm.MODELS, "performance-test", Scripted)
    role = config.Role("performance-test", "scripted", None)
    cfg = config.Config(roles={name: role for name in config.DEFAULT_ROLES},
                        search="none", imagegen=False, showcase=False, visual_limit=0)
    module = Module.load(paths.EXAMPLE)
    module.npc("preacher").role = "广场上的说书人"
    module.items.append(Item(id="coat", name="雨衣", description="一件油布雨衣", where="square", owner="preacher"))
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)], slot="perform-regression")
    cast = Cast(cfg, module)
    game = Game(module, state, cast, Rules(dice=fake))
    yield game, script, seen
    await game.close()
    await cast.close()


async def test_performer_does_not_receive_global_logs_clues_private_summary_or_legacy_deeds(performance):
    game, script, seen = performance
    state = game.state
    state.memory = "全局摘要哨兵：调查员暗中藏起徽章。"
    state.add("player", "角色内心计划哨兵：隐瞒书房的发现。")
    state.add("clue", "异地线索哨兵：藏在报社里的遗书。")
    game.world.apply(Occur(text="异地私语哨兵：今晚悄悄潜入钟楼。", actor="warden", participants=[state.investigator.id],
                           place="tower", visibility="private"))
    script.append({"text": "诸位，先听钟声。"})
    scoped = game._deeds("preacher")
    await game.cast.perform(state, "preacher", "讲个开场故事，最后说‘先听钟声’。", "旧调用方哨兵：全局秘密汇总")
    prompt = json.dumps(seen, ensure_ascii=False)
    for sentinel in ("全局摘要哨兵", "角色内心计划哨兵", "异地线索哨兵", "异地私语哨兵", "旧调用方哨兵"):
        assert sentinel not in prompt and sentinel not in scoped
    assert "讲个开场故事" in prompt and "先听钟声" in prompt
    assert state.npc("preacher").lines == 1


async def test_authorized_local_experience_and_script_are_available_without_becoming_global_knowledge(performance):
    game, script, seen = performance
    state = game.state
    game.world.apply(Occur(text="周衡当面告诉说书人：他刚查过旧报。", actor=state.investigator.id,
                           participants=["preacher"], place="square", visibility="public"))
    script.append({"text": "查过旧报的记者，请听这一声钟。"})
    text = await game.cast.perform(state, "preacher", "请用旧报与钟声作开场。", game._deeds("preacher"))
    assert "旧报" in game._deeds("preacher") and "当面告诉" in json.dumps(seen, ensure_ascii=False)
    assert "请用旧报与钟声作开场" in json.dumps(seen, ensure_ascii=False)
    assert text == "查过旧报的记者，请听这一声钟。"


async def test_performance_output_obeys_the_normal_secret_disclosure_guard(performance):
    game, script, _ = performance
    script.extend([{"text": game.m.truth}, {"text": "诸位，钟声会替我们作证。"}])
    text = await game.cast.perform(game.state, "preacher", "作一段简短表演。", "")
    assert text == "诸位，钟声会替我们作证。" and game.m.truth not in text
    assert game.m.truth not in json.dumps(game.state.transcripts["npc:preacher"], ensure_ascii=False)
    assert game.state.record.facts["truth"].revealed is None


async def test_performance_settles_valid_transfers_through_the_npc_path(performance):
    game, script, _ = performance
    state = game.state
    script.append({"text": "这件雨衣交给你。", "changes": [{"kind": "transfer", "key": "stage_coat",
                   "text": "这件雨衣交给你。", "npc": "preacher", "item": "coat", "owner": state.investigator.id}]})
    await game.cast.perform(state, "preacher", "照现场实际情况回应。", "")
    assert state.item_owners["coat"] == state.investigator.id
    assert "雨衣" in state.investigator.inventory
    assert state.record.entities["coat"].at == state.investigator.id


async def test_invalid_performance_actions_do_not_mutate_the_world(performance):
    game, script, _ = performance
    state = game.state
    bad = {"text": "我把金条交给你。", "changes": [{"kind": "transfer", "key": "stage_gold", "text": "我把金条交给你。",
           "npc": "preacher", "item": "nonexistent_gold", "owner": state.investigator.id}]}
    script.extend([bad, bad])
    with pytest.raises(RuntimeError, match="未通过状态检查"):
        await game.cast.perform(state, "preacher", "回应观众。", "")
    assert "stage_gold" not in state.world and "nonexistent_gold" not in state.item_owners
    assert not state.npc("preacher").met


async def test_known_unsafe_legacy_performance_history_is_removed_before_the_next_call(performance):
    game, script, seen = performance
    state = game.state
    state.transcripts["npc:preacher"] = [{"role": "user", "content": "调查员们今天做过的事如下，照实编进书里：旧污染哨兵"},
                                         {"role": "assistant", "content": "旧表演哨兵"}]
    state.notes["npc:preacher"] = "旧污染摘要哨兵"
    script.append({"text": "诸位，先听钟声。"})
    await game.cast.perform(state, "preacher", "开场。", "")
    assert "旧污染" not in json.dumps(seen, ensure_ascii=False) and "旧表演哨兵" not in json.dumps(seen, ensure_ascii=False)
    assert "npc:preacher" not in state.notes
    migrated, _ = game.debug_journal.read(kind="context-migration")
    assert "旧污染哨兵" in migrated[0]["data"]["removed_transcript"][0]["content"]
