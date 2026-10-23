"""Offline audit reproductions: expected failures document unresolved UI/persistence defects."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from aiohttp import web

from autokeeper import paths
from autokeeper.tui import library
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens import lobby as lobby_module
from autokeeper.tui.screens.lobby import LobbyScreen

from .test_multiplayer_transport import sockets
from .test_multiplayer_transport import transport_hub as _transport_hub

transport_hub = _transport_hub


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="P2 audit: switching to watching leaves the human seat occupied")
async def test_watching_releases_the_players_former_seat(transport_hub):
    hub = transport_hub
    app = AutoKeeperApp(theme="amber")
    try:
        async with sockets(hub) as (_, url), app.run_test(size=(120, 40)) as pilot:
            screen = LobbyScreen(url, "临时入座的人")
            await app.push_screen(screen)
            async with asyncio.timeout(3):
                while screen.welcome is None:
                    await pilot.pause(0.02)
            await pilot.press("enter")
            async with asyncio.timeout(3):
                while screen.chosen is None:
                    await pilot.pause(0.02)
            seat = screen.chosen
            assert hub.sits[seat] is not None
            await screen.action_watch()
            await pilot.pause()
            assert screen.chosen == ""
            assert hub.sits[seat] is None
    finally:
        await hub.game.close()


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="P1 audit: concurrent room saves overwrite each others index entries")
def test_two_rooms_saving_together_keep_both_index_entries(transport_hub, monkeypatch):
    original_index = library.save_index
    barrier = Barrier(2)

    def concurrent_index():
        result = original_index()
        barrier.wait(timeout=3)
        return result

    monkeypatch.setattr(library, "save_index", concurrent_index)
    first = transport_hub.state.model_copy(update={"slot": "review-room-a"}, deep=True)
    second = transport_hub.state.model_copy(update={"slot": "review-room-b"}, deep=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(library.write_save, state, transport_hub.m) for state in (first, second)]
        for job in jobs:
            job.result(timeout=5)
    assert paths.save(first.slot).is_file() and paths.save(second.slot).is_file()
    assert {first.slot, second.slot} <= set(original_index())


@pytest.mark.xfail(strict=True, raises=json.JSONDecodeError,
                   reason="P1 audit: interrupted index write destroys the previously valid save listing")
def test_interrupted_index_write_keeps_the_previous_index_readable(transport_hub, monkeypatch):
    from pathlib import Path

    state, module = transport_hub.state, transport_hub.m
    library.write_save(state, module)
    original_write = Path.write_text
    target = paths.save_index()

    def interrupted(path, text, *args, **kwargs):
        if path == target:
            original_write(path, "{", encoding="utf-8")
            raise OSError("offline injected interruption")
        return original_write(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", interrupted)
    with pytest.raises(OSError, match="offline injected"):
        library.write_save(state, module)
    assert state.slot in json.loads(target.read_text())


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="P2 audit: connect timeout ends before the required room welcome arrives")
async def test_room_confirmation_is_covered_by_the_connection_timeout(monkeypatch):
    accepted = asyncio.Event()

    async def silent(request):
        socket = web.WebSocketResponse(protocols=("autokeeper",))
        await socket.prepare(request)
        accepted.set()
        async for _ in socket:
            pass
        return socket

    server = web.Application()
    server.router.add_get("/ws", silent)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(lobby_module, "CONNECT_SECONDS", 1)
    app = AutoKeeperApp(theme="amber")
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            screen = LobbyScreen(f"127.0.0.1:{port}", "玩家")
            await app.push_screen(screen)
            await asyncio.wait_for(accepted.wait(), 3)
            await pilot.pause(0.05)
            if app.screen is not screen or screen.link.ws is None or screen.link.ws.closed:
                raise RuntimeError("Audit setup did not establish the silent room connection")
            await pilot.pause(1.1)
            assert not isinstance(app.screen, LobbyScreen)
    finally:
        await runner.cleanup()
