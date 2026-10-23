"""Other scenes cannot turn a collected fight/chase declaration into a default action."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.encounter.model import EncounterState
from autokeeper.engine.encounter.plan import DeclaredAction, EncounterStart, EncounterTurnPlan
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .encounter_kit import Keeper, arrange, attack


@pytest.fixture(params=["fight", "chase"])
async def mixed_hub(fake, request):
    mode = request.param
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    friend.characteristics["敏捷"] = 60  # No unrelated tie-break roll in the chase order.
    party = [lead, friend, lead.model_copy(update={"id": "remote", "name": "异地同伴"}, deep=True)]
    state = Game.new(module, party, human=[p.id for p in party])
    state.location, state.apart = (("tower", {"remote": "office"}) if mode == "fight"
                                   else ("office", {"remote": "square"}))
    remote = Action(kind="investigate", summary="异地阅读")

    def plan(state, text, brief):
        if text == "A守住门口":
            action = DeclaredAction(do="defend", summary=text)
        elif mode == "fight":
            action = attack("warden", summary="B挥拳攻击守钟人")
        else:
            action = DeclaredAction(do="speak", summary="B停下呼喊", speech="等一等")
        return EncounterTurnPlan(action=action)

    cast = Keeper({"异地阅读": (remote, Ruling(needs_roll=False, minutes=1))}, act=plan)
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    hub.m.events, hub.m.endings = [], []
    hub.sits = {"zhouheng": "a", "lin": "b", "remote": "c"}
    hub.started.set()
    arrangement = arrange("warden", DeclaredAction(do="defend", summary="只防守"), react="dodge")
    if mode == "chase":
        fake.queue(40, 40, 40)
    await hub.game._encounter_open(EncounterStart(mode=mode, foes=["warden"], npcs=[arrangement],
                                                 quarry="party" if mode == "chase" else "",
                                                 reason="离线审阅"), Turn())
    yield hub
    await hub.game.close()


@pytest.mark.parametrize("order", [("zhouheng", "remote", "lin"),
                                   ("remote", "zhouheng", "lin"),
                                   ("zhouheng", "lin", "remote")])
async def test_other_scenes_never_replace_a_submitted_encounter_action_with_a_default(mixed_hub, fake, monkeypatch, order):
    hub, state = mixed_hub, mixed_hub.state
    mode, eid = state.encounter.mode, state.encounter.id
    state.round_plan["encounter"] = [eid, mode, 1]
    declarations = {"zhouheng": "A守住门口", "lin": "B主动行动", "remote": "异地阅读"}
    hub.pool = [(seat, declarations[seat]) for seat in order]
    if mode == "fight":
        fake.queue(10, 90).queue_totals(1)
    offered = []

    async def confirm(seat, who, offer, *, timed):
        offered.append((seat, offer["kind"]))
        return "roll" if offer["kind"] == "roll" else offer.get("default")

    monkeypatch.setattr(hub, "_ask", confirm)
    await asyncio.wait_for(hub._settle(), 3)
    enc = state.encounter
    assert enc.round == enc.completed_rounds == 1
    rows = [r for r in enc.receipts if r.actor == "lin"]
    assert len(rows) == 1 and rows[0].round == 1
    assert rows[0].declared.do == ("attack" if mode == "fight" else "speak")
    assert rows[0].source == "words" and rows[0].phase == "committed"
    assert not [r for r in enc.receipts if r.round > 1]
    assert not fake.values
    assert offered == ([("lin", "roll")] if mode == "fight" else [])


def test_tactical_collection_ignores_current_done_and_nonparticipant_tokens_after_reload(mixed_hub):
    hub, state = mixed_hub, mixed_hub.state
    enc = state.encounter
    state.round_spent = 0
    state.apart.pop("remote", None)  # Same room, but no encounter membership/consent yet.
    assert hub.game.world.place("remote") == state.location
    state.round_plan = {"input": [["zhouheng", "已结算"], ["lin", "当前行动"], ["remote", "普通场面"]],
                        "steps": [{"token": "0"}, {"token": "1"}, {"token": "2"}], "done": ["0"],
                        "encounter": [enc.id, enc.mode, 1]}
    state.pending_turn = {"party_token": "1"}
    hub.game.holding = True  # Prose aggregation cannot hold a tactical round open.
    assert not hub.game._awaiting_encounter_declarations(enc)
    restored = GameState.model_validate_json(state.model_dump_json())
    original = hub.game.state
    try:
        hub.game.state = restored
        assert not hub.game._awaiting_encounter_declarations(EncounterState.model_validate(enc.model_dump()))
        restored.round_plan["done"] = []
        assert hub.game._awaiting_encounter_declarations(enc)
        enc.get("zhouheng").presence = "left"
        assert not hub.game._awaiting_encounter_declarations(enc)
    finally:
        hub.game.state = original
