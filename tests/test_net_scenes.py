"""Real sockets exercise independently hosted scenes; no model or dice service."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import aiohttp

from autokeeper.agents.cast import Ruling
from autokeeper.engine.game import Game
from autokeeper.module.schema import Handout, Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast, opened, pool_open, until


class SceneCast(TableCast):
    async def arbiter(self, state, action, at=None, **kwargs):
        return Ruling(needs_roll="检定" in action.summary, skill="侦查", minutes=0)

    async def keeper(self, state, report, on_delta=None, lore=""):
        return f"{state.investigator.name}所在的{state.location}。"


async def joined(http, hub, who):
    ws = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
    await ws.send_json({"t": "hello", "name": who})
    await ws.send_json({"t": "sit", "investigator": who})
    await until(ws, lambda msg: msg["t"] == "seated")
    return ws


async def test_pending_roll_in_another_scene_does_not_block_conversation_or_leak_it(fake):
    m = Module.load(EXAMPLE)
    a = m.pregens[0].model_copy(deep=True)
    b = a.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [a, b], human=[a.id, b.id], slot="scene-sockets")
    state.apart = {b.id: "office"}
    casts = {}

    def factory(ident):
        casts[ident] = SceneCast()
        return casts[ident]

    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake),
              scene_cast_factory=factory, scene_rules_factory=lambda _: Rules(dice=fake))
    hub.window = 0.01
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            x, y = await joined(http, hub, a.id), await joined(http, hub, b.id)
            await until(x, pool_open)
            await until(y, pool_open)
            assert len(casts) == 2
            hub.m.handouts.append(Handout(id="sealed_letter", title="私人信件", text="零点到东门。"))
            await hub._context(a.id).game.present([{"ref": "handout:sealed_letter", "to": [b.id]}], key="remote-letter")
            letter = await until(y, lambda msg: msg.get("ev", {}).get("type") == "presentation")
            assert letter["ev"]["payload"]["handout"]["text"] == "零点到东门。"
            assert not hub._media_view(a.id)["papers"]
            assert hub._media_view(b.id)["papers"]
            await y.send_json({"t": "act", "text": "我要检定侦查"})
            prompt = await until(y, lambda msg: msg["t"] == "offer")
            assert prompt["offer"]["kind"] == "roll"
            await x.send_json({"t": "act", "text": "我看看广场"})
            await until(x, lambda msg: msg["t"] == "event" and msg.get("ev", {}).get("type") == "keeper_end")
            assert hub._view(a.id)["location"] == "square"
            assert hub._view(b.id)["location"] == "office"
            assert hub._view(a.id)["encounter_public"] is None
            assert not any(entry.who == b.name for entry in hub._context(a.id).state.log)
            assert hub._context(b.id).state.pending_turn
            # The original unanswered offer is restored to this seat after reconnect.
            await y.close()
            y = await joined(http, hub, b.id)
            restored = await until(y, lambda msg: msg["t"] == "offer")
            assert restored["offer"]["respond_id"] == prompt["offer"]["respond_id"]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
