"""A declaration owns its operations even when the scene keeps the same displayed turn."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.encounter.plan import DeclaredAction, EncounterStart, EncounterTurnPlan
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .encounter_kit import arrange, fight_game


@pytest.fixture
async def declaring_game(fake):
    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    game, cast, events = fight_game(module, fake)
    lead = game.state.investigator
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    game.state.investigators.append(friend)
    game.state.human.append(friend.id)
    game.world.sync()
    game.table_turn_based = True
    game.state.turn = 2
    start = EncounterStart(mode="fight", foes=["warden"], reason="守钟人动手了",
                           opening=DeclaredAction(do="defend", summary="周衡举手戒备"))
    game.state.pending_turn = {"input": "守钟人动手了"}
    await game.resolve(Action(kind="other", summary="冲突开始", declared={"start": start.model_dump(mode="json")}),
                       Ruling(needs_roll=False))
    assert game.state.encounter.round == 0
    game.state.pending_turn.clear()
    yield game, cast, events
    await game.close()


def declaration(summary: str, *, say: str = "") -> Action:
    plan = EncounterTurnPlan(action=DeclaredAction(do="defend", summary=summary),
                             npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着门口"), say=say)])
    return Action(kind="other", summary=summary, declared={"plan": plan.model_dump(mode="json"), "source": "words"})


async def submit(game: Game, summary: str, *, say: str = "") -> None:
    game.state.pending_turn = {"input": summary, "active": game.state.active}
    await game.resolve(declaration(summary, say=say), Ruling(needs_roll=False))


async def test_opening_and_two_investigators_own_separate_operations_in_one_scene_turn(declaring_game):
    game, _, _ = declaring_game
    await submit(game, "周衡护住门口", say="都别过来。")
    game.state.active = 1
    await submit(game, "林秋守住窗口", say="我看见你了。")
    enc = game.state.encounter
    rows = [row for row in enc.receipts if row.source == "words"]
    assert [(row.actor, row.summary, row.phase) for row in rows] == [
        ("zhouheng", "周衡举手戒备", "cancelled"),
        ("zhouheng", "周衡护住门口", "declared"),
        ("lin", "林秋守住窗口", "declared"),
    ]
    assert len({row.id for row in rows}) == 3 and {row.round for row in rows} == {1}
    assert game.state.turn == 2 and enc.round == 0


async def test_a_new_declaration_from_the_same_investigator_replaces_only_the_previous_intention(declaring_game):
    game, _, _ = declaring_game
    await submit(game, "周衡护住门口", say="都别过来。")
    await submit(game, "周衡改守楼梯", say="楼梯上也有人。")
    rows = game.state.encounter.receipts
    assert [(row.summary, row.phase) for row in rows] == [
        ("周衡举手戒备", "cancelled"), ("周衡护住门口", "cancelled"), ("周衡改守楼梯", "declared")]
    assert len({row.id for row in rows}) == 3 and {row.round for row in rows} == {1}


async def test_a_saved_declaration_reuses_its_plan_operation_before_another_actor_declares(declaring_game):
    game, cast, events = declaring_game
    emit = game.emit

    async def broken(event):
        if event["type"] == "fight_say":
            raise ConnectionError("the plan landed before its notice was delivered")
        await emit(event)

    action = declaration("周衡护住门口", say="都别过来。")
    ruling = Ruling(needs_roll=False)
    game.state.pending_turn = {"input": action.summary, "active": 0}
    game.emit = broken
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    before = [row.id for row in game.world.rec.journal]
    restored = GameState.model_validate_json(game.state.model_dump_json())
    resumed = Game(game.m, restored, cast, game.rules, emit=emit)
    resumed.table_turn_based = True
    try:
        await resumed.resolve(action, ruling)
        await resumed.resolve(action, ruling)
        assert [row.id for row in resumed.world.rec.journal if row.id in before] == before
        assert [event["text"] for event in events if event["type"] == "fight_say"] == ["都别过来。"]
        assert [row.summary for row in restored.encounter.receipts].count(action.summary) == 1
        restored.active = 1
        await submit(resumed, "林秋守住窗口", say="我看见你了。")
        assert [row.actor for row in restored.encounter.receipts] == ["zhouheng", "zhouheng", "lin"]
    finally:
        await resumed.close()
