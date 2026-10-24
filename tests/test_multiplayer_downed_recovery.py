"""A settled wound cannot prevent recovery of the telling that follows it."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.harness.spend import BudgetExceeded
from autokeeper.module.schema import Event, Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast


class InterruptedTelling(TableCast):
    def __init__(self, *, fail=False):
        super().__init__()
        self.fail = fail
        self.intents = self.rulings = self.tellings = 0

    async def intent(self, state, text):
        self.intents += 1
        return Action(kind="investigate", summary="检查门框")

    async def arbiter(self, state, action, at=None, **kwargs):
        self.rulings += 1
        return Ruling(needs_roll=True, skill="侦查", minutes=1)

    async def keeper(self, state, report, on_delta=None, lore=""):
        self.tellings += 1
        if self.fail:
            raise BudgetExceeded("offline interruption after injury")
        return await super().keeper(state, report, on_delta, lore)


def downed_hub(fake, cast, damage, state=None):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = state or Game.new(module, [lead, friend], human=[lead.id, friend.id])
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    hub.m.events = [Event(id="fallen-beam", title="门框坠落", when=hub.m.start_time, text="门框砸向调查员。",
                          damage=f"{damage // 6}D6")]
    hub.m.endings, hub.m.foreshadow, hub.m.agendas = [], [], []
    return hub


@pytest.mark.parametrize("damage,condition", [(6, "昏迷"), (24, "死亡")])
@pytest.mark.parametrize("ai", [False, True])
async def test_downed_investigators_saved_turn_finishes_after_restart_without_new_action_or_dice(fake,
                                                                                            damage, condition, ai):
    cast = InterruptedTelling(fail=True)
    hub = downed_hub(fake, cast, damage)
    who = hub.state.investigator.id
    resumed = None
    fake.queue(1, *([90] if condition == "昏迷" else []))
    fake.queue_totals(damage)
    order = [(who, None if ai else "检查门框")]
    try:
        await hub.game.arrange_party(order)
        with pytest.raises(BudgetExceeded):
            await hub._run("检查门框", who, ai=True)
        assert condition in hub.state.find(who).conditions and not hub.state.find(who).can_act
        assert hub.state.pending_turn["stage"] == "narrate" and len(hub.state.checks) == 1
        saved = GameState.model_validate_json(hub.state.model_dump_json())
        before = saved.turn, saved.minutes, saved.find(who).hp, len(saved.checks)
        second = InterruptedTelling()
        resumed = downed_hub(fake, second, damage, saved)
        resumed.sits[who] = "player"
        await resumed._settle_round(order)
        assert not saved.pending_turn and not resumed.game.pending_party()
        assert (saved.turn, saved.minutes, saved.find(who).hp, len(saved.checks)) == before
        assert (second.intents, second.rulings, second.tellings) == (0, 0, 1)
        assert not second.ai_turns and not fake.values and not fake.totals
    finally:
        await hub.game.close()
        if resumed is not None:
            await resumed.game.close()


@pytest.mark.parametrize("condition", ["昏迷", "死亡"])
async def test_downed_investigator_without_a_saved_turn_cannot_start_a_new_action(fake, condition):
    cast = InterruptedTelling()
    hub = downed_hub(fake, cast, 6)
    who = hub.state.investigator.id
    hub.state.find(who).conditions.append(condition)
    try:
        await hub.game.arrange_party([(who, "重新查看门框")])
        await hub._settle_round([(who, "重新查看门框")])
        assert not hub.state.pending_turn and not hub.game.pending_party()
        assert (cast.intents, cast.rulings, cast.tellings) == (0, 0, 0)
        assert not hub.state.checks
    finally:
        await hub.game.close()
