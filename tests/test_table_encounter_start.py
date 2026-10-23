"""The host admits fresh fight/chase rounds; a started round resumes its actual pending choice."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.encounter.plan import DeclaredAction, EncounterStart, EncounterTurnPlan
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .encounter_kit import arrange, attack, fight_game


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


async def opening(module, fake, mode: str, *, hosted: bool = True, hostile: bool = False,
                  human: bool = True, by_hand: bool = False):
    npc = (attack("zhouheng") if mode == "fight" else DeclaredAction(
        do="pursue", target="zhouheng", summary="守钟人追上去")) if hostile else DeclaredAction(do="defend", summary="守着")
    arrangement = arrange("warden", npc, react="dodge")
    game, cast, events = fight_game(module, fake, location="office" if mode == "chase" else "tower",
                                    arrange=lambda *a: EncounterTurnPlan(npcs=[arrangement]))
    game.table_turn_based = hosted
    if not human:
        game.state.human = []
    if hosted:
        game.begin_round()
    if by_hand:
        game.by_hand_seats = frozenset({"zhouheng"})
    start = EncounterStart(mode=mode, foes=["warden"], quarry="party" if mode == "chase" else "", pace=6,
                           reason="冲突开始", surprise="party" if hostile else "", npcs=[arrangement],
                           opening=attack("warden") if by_hand else DeclaredAction(do="defend", summary="戒备"))
    action = Action(kind="other", summary="冲突开始", declared={"start": start.model_dump(mode="json", by_alias=True)})
    game.state.pending_turn = {"input": "冲突开始"}
    if mode == "chase":
        fake.queue(40, 40)  # only the speed rolls belong to opening the chase
    await game.resolve(action, Ruling(needs_roll=False))
    return game, cast, events


@pytest.mark.parametrize("mode", ["fight", "chase"])
async def test_host_opening_does_not_run_an_npc_before_fresh_declarations(module, fake, mode):
    game, _cast, events = await opening(module, fake, mode, hostile=True)
    enc = game.state.encounter
    assert enc.mode == mode and enc.round == enc.completed_rounds == 0 and enc.pending is None
    assert not any(r.actor == "warden" for r in enc.receipts)
    assert not any(e["type"] in ("fight_round", "chase_round", "fight_act") for e in events)
    assert enc.get("zhouheng").surprised  # collection does not revoke a rule-defined surprise
    assert not fake.values


@pytest.mark.parametrize("mode", ["fight", "chase"])
async def test_a_solo_opening_still_runs_its_first_round(module, fake, mode):
    game, _cast, events = await opening(module, fake, mode, hosted=False)
    enc = game.state.encounter
    assert enc.round == enc.completed_rounds == 1
    assert any(e["type"] == ("fight_round" if mode == "fight" else "chase_round") for e in events)


async def test_only_the_saved_matching_round_batch_starts_a_new_host_round(module, fake):
    game, _cast, _events = await opening(module, fake, "fight")
    enc = game.state.encounter
    for context in (["different", "fight", 1], [enc.id, "chase", 1], [enc.id, "fight", 2]):
        game.state.round_plan["encounter"] = context
        await game._beat(Turn(), settle=True)
        assert enc.round == enc.completed_rounds == 0
    game.state.round_plan["encounter"] = [enc.id, "fight", 1]
    await game._beat(Turn(), settle=True)
    enc = game.state.encounter
    assert enc.round == enc.completed_rounds == 1
    await game._beat(Turn(), settle=True)
    assert game.state.encounter.round == game.state.encounter.completed_rounds == 1
    game.state.round_plan["encounter"] = [enc.id, "fight", 2]
    await game._beat(Turn(), settle=True)
    assert game.state.encounter.round == game.state.encounter.completed_rounds == 2


async def test_a_pending_roll_resumes_after_reload_without_recollecting_the_started_round(module, fake):
    game, cast, events = await opening(module, fake, "fight", by_hand=True)
    game.state.round_plan["encounter"] = [game.state.encounter.id, "fight", 1]
    await game._beat(Turn(), settle=True)
    pending = game.state.encounter.pending
    assert pending is not None and pending.kind == "roll" and game.state.encounter.round == 1
    # The batch may be over; an existing pending choice still belongs to its already-started round.
    game.state.round_plan.pop("encounter")
    game.state.pending_turn.clear()
    restored = GameState.model_validate_json(game.state.model_dump_json())
    resumed = Game(module, restored, cast, game.rules, emit=game.emit)
    resumed.table_turn_based = True
    resumed.by_hand_seats = game.by_hand_seats
    fake.queue(10, 90).queue_totals(1)
    await resumed.respond(pending.id, "roll", who="zhouheng")
    assert resumed.state.encounter.pending is None
    assert resumed.state.encounter.round == resumed.state.encounter.completed_rounds == 1
    assert sum(e["type"] == "fight_act" for e in events) == 1 and not fake.values and not fake.totals


async def test_an_ai_only_host_does_not_wait_for_a_human_batch(module, fake):
    game, _cast, _events = await opening(module, fake, "fight", human=False)
    assert game.state.encounter.round == game.state.encounter.completed_rounds == 1
