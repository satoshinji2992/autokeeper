"""Recovery across a changed ruling and a partially committed encounter answer."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import graph
from autokeeper.engine.encounter.plan import EncounterTurnPlan
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.rules.coc import Rules

from .encounter_kit import Keeper, arrange, attack, begin, fight_game
from .test_engine import FakeCast, make
from .test_execution_journal import ACTION, RULING, drive, module


async def test_replanned_manual_check_replaces_the_unanswered_old_offer(fake):
    m = module()
    game, _, _ = make(m, fake.queue(25), {"调查": (ACTION, RULING)})
    resumed = None
    try:
        _, old_offers = await drive(graph.build(game, InMemorySaver()), {"input": "调查", "by_hand": True})
        state = GameState.model_validate_json(game.state.model_dump_json())
        resumed = Game(m.model_copy(deep=True), state, FakeCast({}), Rules(dice=fake))
        action = Action(kind="investigate", summary="改为辨听门后的声音", target="门后")
        ruling = Ruling(needs_roll=True, skill="聆听", difficulty="困难", minutes=1)

        async def changed_shared_resource(end):
            # SceneRuntime._prepare_joint rewrites these after its shared-resource handoff.
            state.pending_turn["action"] = action.model_dump(mode="json")
            state.pending_turn["ruling"] = ruling.model_dump(mode="json")
            state.pending_turn.pop("roll_confirmed", None)

        resumed.time_wait = changed_shared_resource
        _, new_offers = await drive(graph.build(resumed, InMemorySaver()), {"input": "/retry"})
        assert new_offers != old_offers
        assert new_offers == [{"kind": "roll", **resumed.roll_prompt(action, ruling)}]
        assert list(fake.values) == [25] and not state.checks
    finally:
        await game.close()
        if resumed is not None:
            await resumed.close()


async def test_restart_finishes_an_encounter_answer_after_it_consumed_the_pending_choice(fake):
    m = module()

    def warden_attacks(state, text, brief):
        return EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng", summary="守钟人挥拳"))])

    game, _, _ = fight_game(m, fake, arrange=warden_attacks)
    resumed = None
    saved = []
    try:
        await begin(game, ["warden"])
        app = graph.build(game, InMemorySaver())
        fake.queue(10, 60, 20, 50).queue_totals(2, 3)
        _, offers = await drive(app, {"input": "攻击 楼梯口打盹的老人（徒手，1D3）"})
        assert [offer["kind"] for offer in offers] == ["reaction"]

        def disconnect_after_answer():
            if (not saved and game.state.encounter.pending is None
                    and any(key.startswith("respond:") for key in game.state.pending_turn)):
                saved.append(game.state.model_dump_json())
                raise ConnectionError("answer committed, response unfinished")

        game.checkpoint = disconnect_after_answer
        with pytest.raises(ConnectionError, match="response unfinished"):
            await drive(app, Command(resume="dodge"))
        state = GameState.model_validate_json(saved[-1])
        resumed = Game(m.model_copy(deep=True), state, Keeper(arrange=warden_attacks), Rules(dice=fake))
        _, remaining = await drive(graph.build(resumed, InMemorySaver()), {"input": "/retry"})
        assert not remaining and not state.pending_turn
        assert not fake.values and not fake.totals
        assert state.investigator.hp == 8
    finally:
        await game.close()
        if resumed is not None:
            await resumed.close()
