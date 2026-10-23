"""Declared combat intent, its actual checks and its durable encounter effects, without model calls."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import RuledEffect, Ruling
from autokeeper.engine.encounter import plan_check, views
from autokeeper.engine.encounter.model import Hold, Participant
from autokeeper.engine.encounter.plan import AttackPick, DeclaredAction, EncounterTurnPlan
from autokeeper.engine.game import Game, Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .encounter_kit import arrange, attack, begin, fight_game


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


async def settle(game, text):
    game.state.pending_turn = {"input": text}
    action = await game.encounter_input(text)
    ruling = await game.rule(action)
    return await game.resolve(action, ruling)


async def held_game(module, fake, action, **kwargs):
    npc = arrange("warden", DeclaredAction(do="defend", summary="维持压制"), react="dodge")
    game, cast, events = fight_game(module, fake, act=EncounterTurnPlan(action=action), **kwargs)
    game.asking = False
    await begin(game, ["warden"], npcs=[npc])
    game.state.encounter.scene.holds.append(Hold(held="zhouheng", by="warden", since=0))
    return game, cast, events


async def test_held_investigator_attacks_with_penalty_instead_of_automatic_strength_contest(module, fake):
    action = attack("warden", summary="周衡挥拳攻击压制者")
    game, _cast, events = await held_game(module, fake, action)
    fake.queue(20, 90).queue_totals(2)
    await settle(game, "我攻击他")
    receipt = next(r for r in game.state.encounter.receipts if r.actor == "zhouheng")
    assert receipt.phase == "committed"
    assert [(r.skill, r.bonus) for r in receipt.rolls] == [("格斗(斗殴)", -1), ("闪避", 0)]
    assert game.state.encounter.scene.holds  # An ordinary small wound does not automatically release a hold.
    assert any(e.get("type") == "fight_act" and e.get("winner") == "attacker" for e in events)
    assert not fake.values and not fake.totals


async def test_held_defender_can_dodge_with_a_penalty(module, fake):
    game, _cast, events = await held_game(module, fake, DeclaredAction(do="defend", summary="周衡防守"))
    game.state.encounter.get("warden").arrangement = arrange("warden", attack("zhouheng"))
    fake.queue(20, 90).queue_totals(2)
    await settle(game, "我闪避")
    receipt = next(r for r in game.state.encounter.receipts if r.actor == "warden")
    assert receipt.reactions[0].chosen == "dodge"
    assert receipt.rolls[-1].skill == "闪避" and receipt.rolls[-1].bonus == -1
    assert any(e.get("type") == "fight_act" and e.get("reaction") == "dodge" for e in events)


async def test_a_major_wound_forces_a_conscious_holder_to_release(module, fake):
    game, _cast, _events = await held_game(module, fake, attack("warden", summary="周衡攻击压制者"))
    fake.queue(20, 90, 10).queue_totals(6)
    await settle(game, "我攻击他")
    assert not game.state.encounter.scene.holds
    assert "昏迷" not in game.world.rec.entities["warden"].life.conditions
    assert any(c.kind == "freed" and c.who == "zhouheng" for c in game.state.encounter.changes)


@pytest.mark.parametrize("value,freed", [(10, True), (95, False)])
async def test_ruled_alternative_method_uses_its_check_and_only_success_releases(module, fake, value, freed):
    action = DeclaredAction(do="other", summary="周衡缩身滑出外衣", goal="脱离束缚")
    game, cast, _events = await held_game(module, fake, action)
    cast.ruling_for[action.summary + "；目标：" + action.goal] = Ruling(
        needs_roll=True, skill="敏捷", effects=[RuledEffect(kind="freed", target="zhouheng")])
    fake.queue(value)
    await settle(game, "我缩身滑出衣服")
    receipt = next(r for r in game.state.encounter.receipts if r.actor == "zhouheng")
    assert [r.skill for r in receipt.rolls] == ["敏捷"]
    assert bool(game.state.encounter.scene.holds) is not freed
    assert receipt.phase == "committed" and not fake.values


@pytest.mark.parametrize("value,freed", [(10, True), (95, False)])
async def test_speech_with_strategic_goal_receives_a_ruling_and_an_actual_effect(module, fake, value, freed):
    action = DeclaredAction(do="speak", target="warden", speech="先松开他，我们谈谈", summary="周衡劝对手松手", goal="让对手放人")
    game, cast, _events = await held_game(module, fake, action)
    cast.ruling_for[action.summary + "；目标：" + action.goal] = Ruling(
        needs_roll=True, skill="说服", effects=[RuledEffect(kind="freed", target="zhouheng")])
    fake.queue(value)
    await settle(game, "我劝他放人")
    receipt = next(r for r in game.state.encounter.receipts if r.actor == "zhouheng")
    assert [r.skill for r in receipt.rolls] == ["说服"] and len(cast.ruled) == 1
    assert bool(game.state.encounter.scene.holds) is not freed
    assert not fake.values


async def test_pure_speech_does_not_require_a_check_or_release_the_hold(module, fake):
    action = DeclaredAction(do="speak", speech="住手", summary="周衡喊了一声")
    game, cast, _events = await held_game(module, fake, action)
    await settle(game, "我喊住手")
    assert not cast.ruled and game.state.encounter.scene.holds
    assert not fake.values


async def test_incomplete_ruling_does_not_automatically_apply_a_release(module, fake):
    action = DeclaredAction(do="other", summary="周衡尝试脱离")
    game, cast, _events = await held_game(module, fake, action)
    cast.ruling_for[action.summary] = Ruling(needs_roll=True, effects=[RuledEffect(kind="freed", target="zhouheng")])
    result = await settle(game, "我尝试脱离")
    assert game.state.encounter.scene.holds
    assert any("没有指定技能" in line for line in result.fight)
    assert not fake.values


@pytest.mark.parametrize("value,freed", [(10, True), (95, False)])
async def test_breaking_free_is_a_declared_maneuver_contest(module, fake, value, freed):
    action = DeclaredAction(do="maneuver", maneuver="脱离", attacks=[AttackPick(target="warden")], summary="周衡挣脱压制")
    game, _cast, _events = await held_game(module, fake, action)
    fake.queue(value, 90)
    await settle(game, "我施展战技挣脱")
    receipt = next(r for r in game.state.encounter.receipts if r.actor == "zhouheng")
    assert [(r.skill, r.bonus) for r in receipt.rolls] == [("格斗(斗殴)", -1), ("闪避", 0)]
    assert bool(game.state.encounter.scene.holds) is not freed and not fake.values


async def test_help_is_consumed_once_by_the_alternative_check_and_survives_save_roundtrip(module, fake):
    from autokeeper.engine.state import GameState

    action = DeclaredAction(do="other", summary="周衡借同伴支撑缩身脱离")
    game, cast, _events = await held_game(module, fake, action)
    game.state.investigators.append(game.state.investigator.model_copy(update={"id": "helper", "name": "同伴"}, deep=True))
    game.state.investigators[-1].characteristics["敏捷"] = 100
    game.state.encounter.participants.append(Participant(id="helper", kind="investigator", side="party", controller="human", node="tower"))
    helper = DeclaredAction(do="help", target="zhouheng", summary="同伴支撑周衡")
    await game._declare(Turn(), "helper", helper, "words", None)
    saved = GameState.model_validate_json(game.state.model_dump_json())
    game = Game(module, saved, cast, game.rules, emit=game.emit)
    game.asking = False
    cast.ruling_for[action.summary] = Ruling(needs_roll=True, skill="敏捷", effects=[RuledEffect(kind="freed", target="zhouheng")])
    fake.queue(10)
    await settle(game, "我借支撑脱离")
    receipt = next(r for r in game.state.encounter.receipts if r.actor == "zhouheng")
    assert [(r.skill, r.bonus) for r in receipt.rolls] == [("敏捷", 1)]
    assert not game.state.encounter.get("zhouheng").edges and not fake.values


async def test_ruled_release_resumes_after_save_without_rerolling_or_applying_twice(module, fake, monkeypatch):
    from autokeeper.engine.state import GameState
    from autokeeper.world.store import World

    declared = DeclaredAction(do="other", summary="周衡寻找空隙脱离")
    game, cast, _events = await held_game(module, fake, declared)
    cast.ruling_for[declared.summary] = Ruling(
        needs_roll=True, skill="敏捷", effects=[RuledEffect(kind="freed", target="zhouheng")])
    game.state.pending_turn = {"input": "我寻找空隙"}
    action = await game.encounter_input("我寻找空隙")
    ruling = await game.rule(action)
    real_apply = World.apply
    broken = []

    def apply(self, op):
        if getattr(op, "id", "").endswith("/s0/act") and not broken:
            broken.append(op.id)
            raise ConnectionError("interrupted before the resolved unit landed")
        return real_apply(self, op)

    monkeypatch.setattr(World, "apply", apply)
    fake.queue(10)
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    assert game.state.encounter.scene.holds and not fake.values
    saved = GameState.model_validate_json(game.state.model_dump_json())
    game = Game(module, saved, cast, game.rules, emit=game.emit)
    game.asking = False
    await game.resolve(action, ruling)
    assert not game.state.encounter.scene.holds and not fake.values
    receipts = [r for r in game.state.encounter.receipts if r.actor == "zhouheng"]
    assert len(receipts) == 1 and len(receipts[0].rolls) == 1 and receipts[0].phase == "committed"


async def test_surprise_receipt_preserves_why_no_reaction_and_the_total_result(module, fake):
    hold = DeclaredAction(do="maneuver", maneuver="制住", attacks=[AttackPick(target="zhouheng")], summary="守钟人抓住周衡")
    game, _cast, events = fight_game(module, fake, act=EncounterTurnPlan(action=DeclaredAction(do="defend", summary="防守")))
    game.asking = False
    await begin(game, ["warden"], npcs=[arrange("warden", hold)], surprise="party")
    fake.queue(80)  # Failed roll, but a surprised target cannot answer: anything short of a fumble lands, p. 91.
    await settle(game, "我防守")
    receipt = next(r for r in game.state.encounter.receipts if r.actor == "warden")
    assert receipt.reactions[0].chosen == "none" and "突袭" in receipt.reactions[0].reason
    assert not game.state.encounter.get("zhouheng").surprised
    assert game.state.encounter.scene.holds
    event = next(e for e in events if e.get("type") == "fight_act")
    assert event["winner"] == "attacker" and event["effect"] == "制住"
    assert "已结算：制住" in event["line"] and "突袭" in event["reaction_reason"]


async def test_npc_release_changes_the_hold_and_rejects_someone_elses_hold(module, fake):
    action = DeclaredAction(do="defend", summary="周衡等待")
    game, _cast, _events = await held_game(module, fake, action)
    release = arrange("warden", DeclaredAction(do="defend", summary="停手"), release=["zhouheng"])
    plan = EncounterTurnPlan(npcs=[release])
    assert not plan_check.land(game, plan, act=False).unsettled
    await game._plan_unit("release-check", plan.model_dump(mode="json"), [], Turn())
    assert not game.state.encounter.scene.holds
    assert any("release" in p for p in plan_check.land(game, plan, act=False).unsettled)
    assert "【尚未使用" not in views.keeper_text(game)


async def test_encounter_notices_and_table_keep_the_actual_scene_when_active_seat_changes(module, fake):
    game, _cast, events = fight_game(module, fake)
    remote = game.state.investigator.model_copy(update={"id": "remote", "name": "异地同伴"}, deep=True)
    game.state.investigators.append(remote)
    game.state.apart[remote.id] = "newspaper"
    await begin(game, ["warden"], npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))])
    start = next(e for e in events if e.get("type") == "fight_start")
    assert start["audience"] == ["zhouheng"]
    assert [p.id for p in game.state.encounter.participants] == ["zhouheng", "warden"]
    assert game.state.log[-1].audience == ["zhouheng"]
    frozen_fight = start["fight"]
    game.state.active = 1
    assert game.encounter() is None and game.world_encounter() is not None
    assert not game.fought_this_turn()
    assert views.table(game, viewer="remote") is None
    assert views.table(game, viewer="zhouheng")["fight"]["place"] == "tower"
    await game._notify("frozen-replay", [start], Turn())
    assert events[-1]["audience"] == ["zhouheng"] and events[-1]["fight"] == frozen_fight
    turn = Turn()
    from autokeeper.engine.encounter.plan import EncounterStart

    assert not await game._encounter_open(EncounterStart(mode="fight", foes=["warden"], reason="另一场冲突"), turn)
    assert game.state.encounter.place == "tower" and turn.notes
