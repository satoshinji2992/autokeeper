"""The saved execution journal can resume without a previous graph cursor or Turn holder."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import graph
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, make

THREAD = {"configurable": {"thread_id": "execution-journal"}}
ACTION = Action(kind="investigate", summary="检查石阶", target="石阶")
RULING = Ruling(needs_roll=True, skill="侦查", minutes=1)


def module():
    result = Module.load(EXAMPLE)
    result.events, result.endings, result.agendas, result.foreshadow = [], [], [], []
    return result


async def drive(app, value):
    events, offers = [], []
    async for mode, chunk in app.astream(value, THREAD, stream_mode=["custom", "updates"]):
        if mode == "custom":
            events.append(chunk)
        elif "__interrupt__" in chunk:
            offers.extend(item.value for item in chunk["__interrupt__"])
    return events, offers


@pytest.mark.parametrize("stage", ["resolve", "offer", "narrate", "remember", "finish"])
async def test_restart_uses_the_saved_stage_without_replanning_or_repeating_a_roll(fake, stage):
    m = module()
    game, _, _ = make(m, fake.queue(25), {"调查": (ACTION, RULING)})
    saved = []

    def disconnect():
        if game.state.pending_turn.get("stage") == stage:
            saved.append(game.state.model_dump_json())
            raise ConnectionError("saved stage, lost transport")

    game.checkpoint = disconnect
    try:
        with pytest.raises(ConnectionError, match="lost transport"):
            await drive(graph.build(game, InMemorySaver()), {"input": "调查"})
        state = GameState.model_validate_json(saved[-1])
        ident = state.pending_turn["id"]
        assert ident
        cast = FakeCast({})
        resumed = Game(m.model_copy(deep=True), state, cast, Rules(dice=fake))
        try:
            _, offers = await drive(graph.build(resumed, InMemorySaver()), {"input": "/retry"})
            assert not offers and not resumed.state.pending_turn
            assert len(resumed.state.checks) == 1 and not fake.values and not cast.ruled
            if stage in {"remember", "finish"}:
                assert not cast.reports
            assert sum(entry.kind == "player" for entry in resumed.state.log) == 1
        finally:
            await resumed.close()
    finally:
        await game.close()


async def test_reload_keeps_manual_roll_and_push_as_distinct_frozen_choices(fake):
    m = module()
    game, _, _ = make(m, fake.queue(80), {"调查": (ACTION, RULING)})
    try:
        _, offers = await drive(graph.build(game, InMemorySaver()), {"input": "调查", "by_hand": True})
        assert [offer["kind"] for offer in offers] == ["roll"]
        state = GameState.model_validate_json(game.state.model_dump_json())
        assert state.pending_turn["awaiting"]["key"] == "roll" and list(fake.values) == [80]
        resumed = Game(m.model_copy(deep=True), state, FakeCast({}), Rules(dice=fake))
        try:
            app = graph.build(resumed, InMemorySaver())
            _, repeated = await drive(app, {"input": "/retry"})
            assert repeated == offers
            _, pushes = await drive(app, Command(resume="roll"))
            assert [offer["kind"] for offer in pushes] == ["push"]
            assert resumed.state.pending_turn["awaiting"]["key"] == "push"
            assert len(resumed.state.checks) == 1 and not fake.values
            _, remaining = await drive(app, Command(resume="drop"))
            assert not remaining and not resumed.state.pending_turn
            assert len(resumed.state.checks) == 1
        finally:
            await resumed.close()
    finally:
        await game.close()
