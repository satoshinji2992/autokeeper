"""Small backend boundaries discovered while following the personal-observation paths."""

from dataclasses import replace

import pytest

from autokeeper.engine import fog
from autokeeper.engine.game import Game
from autokeeper.harness import Session
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.net.hub import table
from autokeeper.paths import EXAMPLE

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401


async def test_a_locally_blocked_request_cannot_discard_the_previous_accepted_exchange():
    session = Session("keeper", SCRIPTED, "role", memory=10000)
    blocked = hm.make_model(replace(SCRIPTED, max_input_chars=1), "role")
    SCRIPT.append("晚上八点关门。")
    try:
        await session.send("几点关门？")
        before = [dict(message) for message in session.transcript]
        with pytest.raises(hm.ContextBudgetExceeded):
            await session.send("再问一遍。", model=blocked)
        session.discard_reply()
        assert session.transcript == before and len(SEEN) == 1
        assert not session.reply_kept
    finally:
        await session.close()
        await blocked.close()


def test_a_known_persons_private_attack_sheet_is_not_in_the_player_module():
    module = Module.load(EXAMPLE)
    module.npc("warden").attacks = ["射击(手枪) 75%，伤害 1D10"]
    module.npc("warden").stats = {"生命": 18}
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.npc("warden").met = True
    npc = next(person for person in table(module, state)["npcs"] if person["id"] == "warden")
    assert not npc["stats"] and not npc["attacks"]


def test_a_map_without_investigators_has_no_personal_sightings():
    module = Module.load(EXAMPLE)
    state = Game.new(module, [], human=[])
    assert fog.met(module, state) == {}
