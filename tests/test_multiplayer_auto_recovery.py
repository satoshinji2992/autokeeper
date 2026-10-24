"""Bounded model recovery resumes the durable stage, never a roll or a player's choice."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Game, PreparationError
from autokeeper.engine.state import GameState
from autokeeper.harness.models import ContextBudgetExceeded, StructuredOutputError
from autokeeper.harness.spend import BudgetExceeded
from autokeeper.module.schema import Module
from autokeeper.net import recovery
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_interruptible_round import collect, wait_for
from .test_multiplayer_transport import transport_hub as _transport_hub
from .test_net import TableCast

transport_hub = _transport_hub


class Narrator(TableCast):
    def __init__(self, failures):
        super().__init__()
        self.failures = list(failures)
        self.intents = self.rulings = self.tellings = 0

    async def intent(self, state, text):
        self.intents += 1
        return Action(kind="investigate", summary="查看告示栏", target="告示栏")

    async def arbiter(self, state, action, at=None, **kwargs):
        self.rulings += 1
        return Ruling(needs_roll=True, skill="侦查", minutes=1)

    async def keeper(self, state, report, on_delta=None, lore=""):
        self.tellings += 1
        if self.failures:
            raise self.failures.pop(0)
        return await super().keeper(state, report, on_delta, lore)


def made(fake, cast, state=None):
    module = Module.load(EXAMPLE)
    module.events, module.endings, module.foreshadow, module.agendas = [], [], [], []
    if state is None:
        state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    hub.m.events, hub.m.endings, hub.m.foreshadow, hub.m.agendas = [], [], [], []
    return hub


@pytest.fixture(autouse=True)
def no_recovery_delay(monkeypatch):
    monkeypatch.setattr(recovery, "DELAY", 0)


@pytest.mark.parametrize("value,kinds", [(1, ["roll"]), (88, ["roll", "push"])])
async def test_a_transient_narration_failure_resumes_after_the_same_roll_and_choice(fake, monkeypatch, value, kinds):
    cast = Narrator([ConnectionError("temporary service disconnect")])
    hub = made(fake, cast)
    who = hub.state.investigator.id
    hub.sits[who] = "player"
    messages, offers = [], []

    async def event(message):
        messages.append(message)

    async def ask(seat, cid, offer, **kwargs):
        offers.append(offer["kind"])
        return "roll" if offer["kind"] == "roll" else "drop"

    monkeypatch.setattr(hub, "_event", event)
    monkeypatch.setattr(hub, "_ask", ask)
    fake.queue(value)
    start = hub.state.minutes
    try:
        await hub._run("查看告示栏", who, ai=False)
        assert (cast.intents, cast.rulings, cast.tellings) == (1, 1, 2)
        assert offers == kinds and len(hub.state.checks) == 1 and not fake.values
        assert hub.state.turn == 1 and hub.state.minutes == start + 1 and not hub.state.pending_turn
        assert sum(message["type"] == "keeper_end" for message in messages) == 1
        assert any("自动恢复" in message.get("text", "") for message in messages)
    finally:
        await hub.game.close()


async def test_failed_format_recovery_remains_bounded_after_save_and_reload(fake):
    broken = StructuredOutputError(["not JSON"], ["still not JSON"])
    cast = Narrator([broken, broken])
    hub = made(fake, cast)
    fake.queue(1)
    resumed = None
    try:
        with pytest.raises(StructuredOutputError):
            await hub._run("查看告示栏", hub.state.investigator.id, ai=True)
        assert cast.tellings == 2 and hub.state.pending_turn["auto_recovery"] == {"narrate": 1}
        saved = GameState.model_validate_json(hub.state.model_dump_json())
        second = Narrator([broken, broken])
        resumed = made(fake, second, saved)
        with pytest.raises(StructuredOutputError):
            await resumed._run("/retry", saved.investigator.id, ai=True)
        assert second.tellings == 1 and second.intents == second.rulings == 0
        assert len(saved.checks) == 1 and saved.turn == 1 and not fake.values
    finally:
        await hub.game.close()
        if resumed:
            await resumed.game.close()


async def test_a_partial_plain_telling_is_not_automatically_replayed(fake, monkeypatch):
    cast = Narrator([])
    hub = made(fake, cast)
    messages = []

    async def event(message):
        messages.append(message)

    async def partial(state, report, on_delta=None, lore=""):
        cast.tellings += 1
        await on_delta("尚未讲完的半句")
        await asyncio.sleep(0)
        raise ConnectionError("plain stream lost")

    monkeypatch.setattr(hub, "_event", event)
    monkeypatch.setattr(cast, "keeper", partial)
    fake.queue(1)
    try:
        with pytest.raises(ConnectionError):
            await hub._run("查看告示栏", hub.state.investigator.id, ai=True)
        assert cast.tellings == 1 and "auto_recovery" not in hub.state.pending_turn
        assert sum(message.get("text") == "尚未讲完的半句" for message in messages) == 1
    finally:
        await hub.game.close()


@pytest.mark.parametrize("failure", [BudgetExceeded("configured spend ceiling"),
                                    ContextBudgetExceeded({"total_chars": 20, "max_input_chars": 10})])
async def test_permanent_limits_never_repeat_a_model_stage(fake, failure):
    cast = Narrator([failure, failure])
    hub = made(fake, cast)
    fake.queue(1)
    try:
        with pytest.raises(type(failure)):
            await hub._run("查看告示栏", hub.state.investigator.id, ai=True)
        assert cast.tellings == 1 and "auto_recovery" not in hub.state.pending_turn
        assert len(hub.state.checks) == 1
    finally:
        await hub.game.close()


async def test_budget_failure_explains_the_block_and_keeps_the_journal_for_manual_configuration(fake, monkeypatch):
    cast = Narrator([BudgetExceeded("spending stopped")])
    hub = made(fake, cast)
    who = hub.state.investigator.id
    hub.sits[who] = "player"
    messages = []

    async def send(cid, message):
        messages.append((cid, message))

    async def ask(*args, **kwargs):
        return "roll"

    monkeypatch.setattr(hub, "_send", send)
    monkeypatch.setattr(hub, "_ask", ask)
    fake.queue(1)
    await hub.game.arrange_party([(who, "查看告示栏")])
    task = asyncio.create_task(hub._settle_round([(who, "查看告示栏")]))
    try:
        await wait_for(lambda: hub.retrying == who)
        error, = (message for cid, message in messages if cid == "player" and message["t"] == "error")
        assert "费用上限" in error["text"] and "重复重试无法恢复" in error["text"]
        assert "费用上限" in hub.state.pending_turn["failure"] and cast.tellings == 1
        assert hub.state.pending_turn and len(hub.state.checks) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()


async def test_preparation_retries_a_wrapped_service_error_without_executing_the_draft(transport_hub, monkeypatch):
    hub = transport_hub
    hub.sits.update(zhouheng="a", lin="b")
    original, calls = hub.game.prepare_party, []

    async def fail_once(order, **kwargs):
        calls.append(list(order))
        if len(calls) == 1:
            try:
                raise TimeoutError("temporary planning service")
            except TimeoutError as exc:
                raise PreparationError("preparation incomplete") from exc
        return await original(order, **kwargs)

    monkeypatch.setattr(hub.game, "prepare_party", fail_once)
    task = await collect(hub)
    try:
        await hub._on("a", {"t": "act", "text": "检查门锁"})
        await asyncio.wait_for(task, 3)
        assert calls == [[("zhouheng", "检查门锁")]] * 2
        assert hub.state.round_plan["input"] == [["zhouheng", "检查门锁"]]
        assert not hub.state.log and not hub.state.checks and not hub.state.pending_turn
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()


async def test_preparation_recovery_yields_to_a_new_player_declaration(transport_hub, monkeypatch):
    hub = transport_hub
    hub.sits.update(zhouheng="a", lin="b")
    original, calls = hub.game.prepare_party, []
    monkeypatch.setattr(recovery, "DELAY", 10)

    async def prepare(order, **kwargs):
        calls.append(list(order))
        if len(calls) == 1:
            raise ConnectionError("first draft unavailable")
        return await original(order, **kwargs)

    monkeypatch.setattr(hub.game, "prepare_party", prepare)
    task = await collect(hub)
    try:
        await hub._on("a", {"t": "act", "text": "检查门锁"})
        await wait_for(lambda: len(calls) == 1)
        await hub._on("a", {"t": "act", "text": "改为观察楼梯"})
        await asyncio.wait_for(task, 3)
        assert calls == [[("zhouheng", "检查门锁")], [("zhouheng", "改为观察楼梯")]]
        assert hub.state.round_plan["input"] == [["zhouheng", "改为观察楼梯"]]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()


def test_a_wrapped_budget_failure_is_not_mistaken_for_a_transient_outer_error():
    try:
        raise BudgetExceeded("limit")
    except BudgetExceeded as exc:
        wrapped = ConnectionError("outer transport wrapper")
        wrapped.__cause__ = exc
    assert not recovery.recoverable(wrapped) and "费用上限" in recovery.explanation(wrapped)
