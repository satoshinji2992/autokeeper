"""Independent socket checks for storage contention and reserved draft reconnects."""

from __future__ import annotations

import asyncio

from filelock import FileLock

from autokeeper import paths
from autokeeper.tui import library

from .test_multiplayer_transport import sit, sockets
from .test_multiplayer_transport import transport_hub as _transport_hub
from .test_net import pool_open, until

transport_hub = _transport_hub


async def reached(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_a_busy_save_index_reports_failure_without_disconnecting_the_player(transport_hub, monkeypatch):
    hub = transport_hub
    paths.games().mkdir(parents=True, exist_ok=True)
    holder = FileLock(paths.save_lock())
    monkeypatch.setattr(library, "save_index_lock", lambda: FileLock(paths.save_lock(), timeout=0.01))
    try:
        async with sockets(hub) as (http, url):
            player = await sit(http, url, "zhouheng")
            with holder:
                await player.send_json({"t": "save"})
                failed = await until(player, lambda msg: msg["t"] in ("error", "saved"))
                assert failed["t"] == "error" and "存档未完成" in failed["text"]
                assert not player.closed and hub.sits["zhouheng"] is not None
            await player.send_json({"t": "save"})
            assert (await until(player, lambda msg: msg["t"] == "saved"))["text"]
            assert paths.save(hub.state.slot).is_file()
    finally:
        await hub.game.close()


async def test_ready_draft_reservation_can_disconnect_rejoin_and_submit_a_new_action(transport_hub, monkeypatch):
    hub = transport_hub
    original = hub.game.prepare_party
    release = asyncio.Event()
    calls = []

    async def prepare(order, **kwargs):
        calls.append(list(order))
        if len(calls) > 1:
            await release.wait()
        return await original(order, **kwargs)

    monkeypatch.setattr(hub.game, "prepare_party", prepare)
    collecting = None
    try:
        async with sockets(hub) as (http, url):
            first = await sit(http, url, "zhouheng")
            returning = await sit(http, url, "lin")
            collecting = asyncio.create_task(hub._collect())
            await until(first, pool_open)
            await returning.send_json({"t": "join", "round": hub.round_id})
            await until(returning, lambda msg: pool_open(msg) and msg.get("joining") == ["lin"])
            await first.send_json({"t": "act", "text": "留意门口", "round": hub.round_id})
            await reached(lambda: hub.phase == "ready")
            assert hub.preparing is None and hub.joining == {"lin"} and hub.open
            await returning.close()
            await reached(lambda: hub.sits["lin"] is None and len(calls) >= 2)
            assert not hub.joining
            returning = await sit(http, url, "lin")
            await returning.send_json({"t": "join", "round": hub.round_id})
            await until(returning, lambda msg: pool_open(msg) and msg.get("joining") == ["lin"])
            release.set()
            await reached(lambda: hub.phase == "ready")
            assert hub.open and not hub.state.round_plan
            await returning.send_json({"t": "act", "text": "检查窗户", "round": hub.round_id})
            await asyncio.wait_for(collecting, 3)
            assert hub.state.round_plan["input"] == [["zhouheng", "留意门口"], ["lin", "检查窗户"]]
            assert not hub.open and not hub.joining and not hub.early
    finally:
        release.set()
        if collecting is not None:
            collecting.cancel()
            await asyncio.gather(collecting, return_exceptions=True)
        await hub.game.close()
