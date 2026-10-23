"""A broken socket or failed display callback must not silently swallow further player input."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import aiohttp
import pytest
from textual.widgets import Input

from autokeeper.agents.card import CardError
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net.cards import RemoteBuilder
from autokeeper.net.link import Link
from autokeeper.paths import EXAMPLE
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.net import NetGameScreen


class Socket:
    def __init__(self, failure="closed"):
        self.failure = failure
        self.closed = failure == "closed"

    async def send_str(self, text):
        raise ConnectionResetError("test connection loss")

    async def close(self):
        self.closed = True

    async def __aiter__(self):
        if self.failure == "network":
            raise ConnectionResetError("test receive interruption")
        yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT,
                              data="invalid JSON" if self.failure == "json" else json.dumps({"t": "event"}))


@pytest.mark.parametrize("failure", ["network", "json", "display"])
async def test_receive_failure_closes_the_connection_and_notifies_the_screen(failure):
    link = Link("localhost", "玩家")
    link.ws = Socket(failure)
    seen = []

    async def handle(message):
        if message["t"] == "event":
            raise ValueError("test UI failure")
        seen.append(message)

    link.handler = handle
    await link.pump()
    assert link.ws.closed
    assert seen[-1]["t"] == "closed" and "接收中断" in seen[-1]["text"]


@pytest.mark.parametrize("failure", ["closed", "write"])
async def test_sending_on_a_broken_socket_reports_the_original_unsent_message(failure):
    link = Link("localhost", "玩家")
    link.ws = Socket(failure)
    messages = []

    async def handle(message):
        messages.append(message)

    link.handler = handle
    original = {"t": "kp_private", "text": "不要公开这条消息。", "request": "private-1"}
    assert await link.send(original) is False
    assert link.ws.closed
    assert messages == [{"t": "unsent", "message": original}]


@pytest.mark.parametrize("fail_at", [1, 2])
async def test_failed_card_send_does_not_wait_forever_for_a_reply(fail_at):
    count = 0

    async def send(message):
        nonlocal count
        count += 1
        return count != fail_at

    remote = RemoteBuilder(SimpleNamespace(send=send), Module.load(EXAMPLE), save=False)
    with pytest.raises(CardError, match="未发送"):
        await asyncio.wait_for(remote._call("look_up", query="人物"), 0.5)
    assert not remote._due


@pytest.mark.parametrize("text, opened", [
    ("我查看柜台上的账本。", True),
    ("/sc 请不要公开这件事。", True),
    ("/sc 请不要公开这件事。", False),
    ("/w lin 先保管这封信。", True),
    ("/w lin 先保管这封信。", False),
])
async def test_failed_send_preserves_the_typed_text_and_its_private_channel(text, opened):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    peer = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [lead, peer], human=[lead.id, peer.id])
    link = Link("localhost", "玩家")
    link.ws = Socket()
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = NetGameScreen(module, state, link, lead.id, [])
        await app.push_screen(screen)
        failures, failed = [], asyncio.Event()

        async def handle(message):
            await screen.on_msg(message)
            if message["t"] == "unsent":
                failures.append(message["message"])
                failed.set()

        link.handler = handle
        await screen.on_msg({"t": "pool", "open": opened, "round": "test-round",
                             "phase": "collecting" if opened else "settling", "count": 0, "capacity": 2,
                             "open_to": [lead.id, peer.id] if opened else [], "waiting": [],
                             "mine": {"now": [], "next": []}})
        command = screen.query_one("#command", Input)
        command.focus()
        await pilot.pause()
        command.value = text
        await pilot.press("enter")
        await asyncio.wait_for(failed.wait(), 2)
        await pilot.pause()
        assert command.value == text
        assert len(failures) == 1
        original = failures[0]
        if text.startswith("/sc "):
            assert original["t"] == "kp_private" and original["text"] == text[4:]
            assert original["request"] and "round" not in original
        elif text.startswith("/w "):
            assert original == {"t": "speak", "text": "先保管这封信。", "to": ["lin"], "channel": "whisper"}
        else:
            assert original == {"t": "act", "text": text, "round": "test-round"}
