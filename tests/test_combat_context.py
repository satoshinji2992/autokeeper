"""Narration and private clarification read the same durable combat facts, without model calls."""

from __future__ import annotations

import copy

import pytest

from autokeeper import config
from autokeeper.agents.cast import Cast
from autokeeper.engine.encounter import telling, views
from autokeeper.engine.encounter.model import Hold
from autokeeper.engine.encounter.plan import AttackPick, DeclaredAction, EncounterTurnPlan
from autokeeper.engine.game import TURN_RECORD, Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .encounter_kit import arrange, begin, fight_game
from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    m.npc("warden").stats.update({"格斗(斗殴)": 25, "敏捷": 40})
    m.npc("warden").attacks = ["格斗(斗殴) 25%，伤害 1D3+DB"]
    return m


async def settled_exchange(module, fake):
    escape = DeclaredAction(do="maneuver", maneuver="脱离", attacks=[AttackPick(target="warden")], summary="周衡尝试脱离压制")
    hold = DeclaredAction(do="maneuver", maneuver="制住", attacks=[AttackPick(target="zhouheng")], summary="守钟人再次尝试制住周衡")
    game, cast, _events = fight_game(module, fake, act=EncounterTurnPlan(action=escape))
    remote = game.state.investigator.model_copy(update={"id": "remote", "name": "异地同伴"}, deep=True)
    game.state.investigators.append(remote)
    game.state.apart[remote.id] = "office"
    game.world.sync()
    await begin(game, ["warden"], npcs=[arrange("warden", hold, react="fight_back")])
    game.asking = False
    game.state.investigator.hp = 7
    game.state.encounter.scene.holds = [Hold(held="zhouheng", by="warden", since=0)]
    game.state.pending_turn = {"input": "我尝试脱离压制"}
    action = await game.encounter_input("我尝试脱离压制")
    ruling = await game.rule(action)
    fake.queue(78, 18, 51, 90).queue_totals(3)
    turn = await game.resolve(action, ruling)
    assert game.state.investigator.hp == 4 and game.state.encounter.scene.holds
    assert not fake.values and not fake.totals
    return game, cast, turn


async def test_failed_escape_and_failed_new_hold_leave_the_original_hold_in_the_report(module, fake):
    game, _cast, turn = await settled_exchange(module, fake)
    report = game._report(turn)
    assert telling.STATE_HEAD in report
    assert "仍制住周衡" in report and "生命 4/11" in report and "仍被制住" in report
    assert "78 失败" in report and "18 常规成功" in report and "51 失败" in report
    assert "一次新的制住尝试失败不会解除" in report
    line = next(line for line in turn.fight if "78" in line)
    assert report.count(line) == 1  # The full batch of receipt lines is not appended twice.
    game.state.encounter.scene.prone.append("warden")
    assert "倒地：老守钟人（warden）" in views.settled_text(game.world, viewer="zhouheng")


async def test_a_saved_report_snapshot_does_not_change_when_the_current_hold_changes(module, fake):
    game, _cast, turn = await settled_exchange(module, fake)
    report = game._report(turn)
    restored = TURN_RECORD.validate_json(TURN_RECORD.dump_json(turn))
    release = EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="松手"), release=["zhouheng"])])
    await game._plan_unit("qa-release", release.model_dump(mode="json"), [], Turn())
    assert not game.state.encounter.scene.holds
    assert game._report(turn) == report
    assert game._report(restored) == report
    assert "压制：无" in views.settled_text(game.world, viewer="zhouheng")


async def test_a_past_report_without_a_snapshot_does_not_claim_newer_state_as_its_own(module, fake):
    game, _cast, _turn = await settled_exchange(module, fake)
    old = Turn(interval={"actor": "zhouheng", "end": game.world.now - 1, "destination": "tower"},
               fight=["【战斗 · 第 1 轮】", "本段原先保存的结算"])
    report = game._report(old)
    snapshot = report.split(telling.STATE_HEAD, 1)[1]
    assert "不能冒充本段快照" in snapshot
    assert "生命 4/11" not in snapshot and "仍制住周衡" not in snapshot


def scripted_cast(module, game):
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}, minds=0,
                             visual_limit=0, imagegen=False, search="none"), module)
    cast.world = game.world
    return cast


async def test_private_clarification_reads_formal_results_despite_incorrect_earlier_prose(module, fake):
    game, _cast, _turn = await settled_exchange(module, fake)
    own = game.state.investigator.id
    game.state.add("keeper", "你已挣脱，退到桌后。", audience=[own])
    game.state.add("keeper", "QA_OTHER_PLAYER_PRIVATE_62173", audience=["remote"])
    cast = scripted_cast(module, game)
    game.cast = cast
    before = game.state.encounter.model_dump(mode="json")
    SCRIPT.append({"text": "正式记录中你仍被制住，生命为4。刚才的挣脱描写有误。"})
    try:
        await game.private_message(own, "我现在真的脱离压制了吗？", request="combat-clarify")
        prompt = SEEN[-1]["messages"][-1]["content"]
        assert telling.STATE_HEAD in prompt and "仍制住周衡" in prompt and "生命 4/11" in prompt
        assert "【最近已结算结果】" in prompt and "78 失败" in prompt and "51 失败" in prompt
        assert "不能宣称旁白优先、面板失效" in SEEN[-1]["system"]
        assert "QA_OTHER_PLAYER_PRIVATE_62173" not in prompt
        assert game.state.encounter.model_dump(mode="json") == before and game.state.investigator.hp == 4
    finally:
        await cast.close()


async def test_remote_private_message_does_not_receive_the_other_scene_combat_snapshot(module, fake):
    game, _cast, _turn = await settled_exchange(module, fake)
    cast = scripted_cast(module, game)
    SCRIPT.append({"text": "你仍在报社，没有看到另一处的经过。"})
    try:
        await cast.private_keeper(game.state, "remote", "我在什么地方？")
        prompt = SEEN[-1]["messages"][-1]["content"]
        assert telling.STATE_HEAD not in prompt and "【最近已结算结果】" not in prompt
        assert "仍制住周衡" not in prompt
        assert "不能冒充本段快照" not in prompt
    finally:
        await cast.close()


async def test_live_keeper_fallback_adds_context_but_settled_old_report_uses_its_frozen_snapshot(module, fake):
    game, _cast, turn = await settled_exchange(module, fake)
    cast = scripted_cast(module, game)
    before = copy.deepcopy(game.state.encounter)
    SCRIPT.extend(["现场仍在扭打。", "仅补叙旧段。", "旧报告中的现场仍然成立。"])
    try:
        await cast.keeper(game.state, "本段结算")
        assert telling.STATE_HEAD in SEEN[-1]["messages"][-1]["content"]
        await cast.keeper(game.state, "过去一段的结算", settled=True)
        assert telling.STATE_HEAD not in SEEN[-1]["messages"][-1]["content"]
        frozen = game._report(turn)
        await cast.keeper(game.state, frozen, settled=True)
        assert SEEN[-1]["messages"][-1]["content"].count(telling.STATE_HEAD) == 1
        assert game.state.encounter == before
    finally:
        await cast.close()
