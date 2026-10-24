"""Private keeper mail recovers the saved request without spending another action."""

from __future__ import annotations

import copy

import pytest

from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.harness.models import StructuredOutputError
from autokeeper.harness.spend import BudgetExceeded
from autokeeper.module.schema import Module
from autokeeper.net import recovery
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast


class MailCast(TableCast):
    def __init__(self, failures=()):
        super().__init__()
        self.failures = list(failures)
        self.requests = []

    async def private_keeper(self, state, who, text, *, context=""):
        self.requests.append((who, text, context))
        if self.failures:
            raise self.failures.pop(0)
        return {"text": "你刚才看到的是车辙，还没有检查驾驶室。", "media": []}


def made(fake, monkeypatch, cast, state=None):
    if state is None:
        module = Module.load(EXAMPLE)
        lead = module.pregens[0].model_copy(deep=True)
        other = lead.model_copy(deep=True, update={"id": "other", "name": "林秋"})
        state = Game.new(module, [lead, other], slot="private-recovery", human=[lead.id, other.id])
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    hub.sits = {state.investigators[0].id: "sender", state.investigators[1].id: "other-player"}
    events, messages = [], []

    async def event(message):
        events.append(copy.deepcopy(message))

    async def send(cid, message):
        messages.append((cid, copy.deepcopy(message)))

    monkeypatch.setattr(hub.game, "listener", event)
    monkeypatch.setattr(hub, "_send", send)
    monkeypatch.setattr(hub, "_save", lambda: None)
    monkeypatch.setattr(recovery, "DELAY", 0)
    return hub, events, messages


def queue(hub):
    who = hub.state.investigators[0].id
    ident, receipt = hub.game.receive_private_message(who, "我刚才看见什么？", request="same-question")
    hub.private_pending.add(ident)
    hub.private_requests.put_nowait((who, receipt["text"], "same-question"))
    return ident, receipt


@pytest.mark.parametrize("failure", [TimeoutError("temporary timeout"),
                                     StructuredOutputError(["invalid"], ["invalid again"])])
async def test_private_model_failure_resumes_the_same_saved_mail_once(fake, monkeypatch, failure):
    cast = MailCast([failure])
    hub, events, messages = made(fake, monkeypatch, cast)
    ident, receipt = queue(hub)
    before = (hub.state.turn, hub.state.minutes)
    try:
        await hub._privates()
        assert len(cast.requests) == 2 and cast.requests[0] == cast.requests[1]
        assert receipt["delivered"] and receipt["auto_recovery"] == 1 and "failure" not in receipt
        assert len(hub.state.private_messages) == 1 and not hub.private_pending
        assert [event["type"] for event in events] == ["private_input", "aside"]
        assert all(event["audience"] == [ident.split(":", 1)[0]] for event in events)
        assert [entry.kind for entry in hub.state.log] == ["system", "keeper"]
        assert messages == [("sender", {"t": "notice", "text": "私信暂未完成，正在自动恢复。"})]
        assert (hub.state.turn, hub.state.minutes) == before and not hub.pool and not hub.state.checks
    finally:
        await hub.game.close()


async def test_private_recovery_is_bounded_across_later_wakes_and_save_reload(fake, monkeypatch):
    cast = MailCast([TimeoutError("first"), TimeoutError("second")])
    hub, _, messages = made(fake, monkeypatch, cast)
    ident, receipt = queue(hub)
    restored_hub = None
    try:
        await hub._privates()
        assert len(cast.requests) == 2 and receipt["auto_recovery"] == 1
        assert "failure" in receipt and not receipt.get("delivered")
        hub._start_privates()
        assert hub.private_requests.empty() and hub.private_task is None
        assert messages[-1][0] == "sender" and messages[-1][1]["t"] == "error"
        saved = GameState.model_validate_json(hub.state.model_dump_json())
        next_cast = MailCast([TimeoutError("after reload"), TimeoutError("must remain unused")])
        restored_hub, events, _ = made(fake, monkeypatch, next_cast, saved)
        await restored_hub._privates()
        assert len(next_cast.requests) == 1 and saved.private_messages[ident]["auto_recovery"] == 1
        assert events == [] and len(saved.log) == 1  # input was already acknowledged
    finally:
        await hub.game.close()
        if restored_hub is not None:
            await restored_hub.game.close()


async def test_private_budget_failure_does_not_retry_or_hide_the_reason(fake, monkeypatch):
    cast = MailCast([BudgetExceeded("configured cap")])
    hub, _, messages = made(fake, monkeypatch, cast)
    _, receipt = queue(hub)
    try:
        await hub._privates()
        assert len(cast.requests) == 1 and "auto_recovery" not in receipt
        assert "费用上限" in receipt["failure"] and "费用上限" in messages[-1][1]["text"]
        hub._start_privates()
        assert hub.private_requests.empty() and hub.private_task is None
    finally:
        await hub.game.close()


async def test_private_reply_delivery_retry_keeps_the_generated_reply(fake, monkeypatch):
    cast = MailCast()
    hub, events, _ = made(fake, monkeypatch, cast)
    _, receipt = queue(hub)
    failed = False

    async def listener(event):
        nonlocal failed
        if event["type"] == "aside" and not failed:
            failed = True
            raise ConnectionError("delivery temporarily interrupted")
        events.append(copy.deepcopy(event))

    monkeypatch.setattr(hub.game, "listener", listener)
    try:
        await hub._privates()
        assert len(cast.requests) == 1 and receipt["delivered"]
        assert [event["type"] for event in events] == ["private_input", "aside"]
        assert [entry.kind for entry in hub.state.log] == ["system", "keeper"]
    finally:
        await hub.game.close()
