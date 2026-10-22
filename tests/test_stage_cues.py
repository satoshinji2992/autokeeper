"""The ordinary keeper plan controls an interruption without a separate approval call."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, BeatChoice, Plan, Ruling
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context, stage
from autokeeper.world import ops as O

from .test_keeper_plan import PlanCast, make


@pytest.fixture
def module():
    return Module.load(EXAMPLE)


def queued(game):
    return stage.queue(game.world, "warden:leave", "warden", [{"kind": "move", "entity": "warden", "to": "square"}],
                       ["warden"], ["warden"], text="守钟人准备下楼去广场")


def planned(module, fake, choice):
    action = Action(kind="talk", summary="周衡继续问守钟人", npc="warden", speech="钟是什么时候坏的？")
    cast = PlanCast({"继续问": (action, Ruling(needs_roll=False))},
                    {"继续问": Plan(action=action, beats=[BeatChoice(id="warden:leave", action=choice, reason="按眼前局势安排")])})
    game = make(module, fake, cast)
    return game, queued(game), cast


async def test_the_keeper_can_interrupt_a_conversation_and_the_operation_is_still_checked(module, fake):
    game, beat, cast = planned(module, fake, "play")
    action = await game.parse("继续问")
    turn = await game.resolve(action, Ruling(needs_roll=False))
    assert beat.status == "played" and game.world.entity("warden").at == "square"
    assert beat.note == "按眼前局势安排" and any("离开了钟楼" in text for text in turn.world)
    assert cast.planned == ["继续问"]  # the same plan, no stage-specific model
    await game.close()


async def test_an_explicit_departure_is_visible_to_the_rules_assistant_before_it_requests_dice(module, fake):
    game, beat, cast = planned(module, fake, "play")
    seen = []

    async def arbiter(state, action, at=None):
        seen.append(game.world.entity("warden").at)
        return Ruling(needs_roll=False, missing=["守钟人已离开，无法继续当面交谈"])

    cast.arbiter = arbiter
    game.state.pending_turn = {"input": "继续问"}
    action = await game.parse("继续问")
    assert beat.status == "played" and game.world.entity("warden").at == "square"
    ruling = await game.rule(action)
    turn = await game.resolve(action, ruling)
    assert seen == ["square"] and not turn.checks
    assert any("离开了钟楼" in text for text in turn.world)
    await game.close()


async def test_a_cue_saved_before_ruling_is_not_committed_again_after_reload(module, fake):
    game, _, cast = planned(module, fake, "play")
    st = game.state
    st.pending_turn = {"input": "继续问"}
    await game.parse("继续问")
    saved = GameState.model_validate_json(st.model_dump_json())
    operations = len(saved.record.journal)
    from autokeeper.engine.game import Game
    from autokeeper.rules.coc import Rules

    resumed = Game(module.model_copy(deep=True), saved, cast, Rules(dice=fake))
    action = await resumed.parse("继续问")
    turn = await resumed.resolve(action, Ruling(needs_roll=False))
    assert len(saved.record.journal) == operations and cast.planned == ["继续问"]
    assert sum("离开了钟楼" in text for text in turn.world) == 1
    await resumed.close()
    await game.close()


async def test_cue_flag_consequences_are_visible_before_ruling_and_not_repeated(module, fake):
    game, beat, _ = planned(module, fake, "play")
    game.m.flags["opened_gate"] = "守钟人打开了侧门"
    game.m.events.append(Event(id="door_opens", title="侧门", when="flag:opened_gate", text="侧门打开，传来风声。",
                               read_aloud="一阵冷风从侧门吹进来。"))
    beat.actor = "director"
    beat.ops = [{"kind": "flag", "flag": "opened_gate"}]
    game.state.pending_turn = {"input": "继续问"}
    action = await game.parse("继续问")
    assert "opened_gate" in game.state.flags and "door_opens" in game.state.fired
    turn = await game.resolve(action, Ruling(needs_roll=False))
    assert [event.id for event in turn.events].count("door_opens") == 1
    await game.close()


async def test_explicit_hold_survives_other_actions_and_save_reload_until_the_keeper_releases_it(module, fake):
    game, beat, _ = planned(module, fake, "hold")
    await game.parse("继续问")
    for _ in range(6):
        game.state.turn += 1
        await game.resolve(Action(kind="investigate", summary="周衡查看齿轮"), Ruling(needs_roll=False))
    assert beat.status == "waiting" and beat.cue == "hold" and game.world.entity("warden").at == "tower"
    restored = GameState.model_validate_json(game.state.model_dump_json())
    assert restored.record.beats[0].cue == "hold"
    stage.cue(game.world, beat.id, "play", "此刻离开符合当前场面")
    await game.resolve(Action(kind="talk", summary="周衡追问", npc="warden"), Ruling(needs_roll=False))
    assert beat.status == "played" and game.world.entity("warden").at == "square"
    await game.close()


async def test_drop_is_final_and_cannot_be_replayed_from_a_saved_choice(module, fake):
    game, beat, _ = planned(module, fake, "drop")
    await game.parse("继续问")
    assert beat.status == "dropped"
    stage.cue(game.world, beat.id, "play", "过时的重试")
    await game.resolve(Action(kind="wait", summary="周衡等着"), Ruling(needs_roll=False))
    assert beat.status == "dropped" and game.world.entity("warden").at == "tower"
    await game.close()


async def test_a_cued_beat_cannot_bypass_position_or_ownership_validation(module, fake):
    game, beat, _ = planned(module, fake, "play")
    beat.ops = [{"kind": "transfer", "item": "unknown_key", "to": game.state.investigator.id}]
    await game.parse("继续问")
    await game.resolve(Action(kind="talk", summary="周衡继续问", npc="warden"), Ruling(needs_roll=False))
    assert beat.status == "dropped" and "unknown_key" not in game.state.investigator.inventory
    await game.close()


def test_the_keeper_receives_the_waiting_operations_and_their_timing(module, fake):
    game, beat, _ = planned(module, fake, "hold")
    stage.cue(game.world, beat.id, "hold", "先让调查员把问题问完")
    brief = context.keeper_brief(game.world)
    assert beat.id in brief and "先让调查员把问题问完" in brief and '"to": "square"' in brief
    assert "不是已发生的事" in brief


def test_old_beats_default_to_automatic_timing(module, fake):
    game, beat, _ = planned(module, fake, "hold")
    data = beat.model_dump(exclude={"cue"})
    assert type(beat).model_validate(data).cue == "auto"
    assert game.world.apply(O.Condition(entity="warden", add="正在交谈")).ok
