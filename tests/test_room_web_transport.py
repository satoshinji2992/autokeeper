"""A room URL carries browser terminals and native multiplayer without mixing protocols."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import aiohttp
import pytest
from aiohttp import web
from textual.widgets import Button, Input
from textual_serve.server import Server

from autokeeper.net import room as lan
from autokeeper.net.link import Link, address
from autokeeper.net.web import Page
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.lobby import LobbyScreen
from autokeeper.tui.screens.net import JoinScreen, NetGameScreen

from .test_disconnect_takeover import make_hub
from .test_multiplayer_transport import sit, sockets
from .test_net import until


@asynccontextmanager
async def room_page(hub_port=None):
    page = Page("unused", host="127.0.0.1", hub_port=hub_port)
    runner = web.AppRunner(await page._make_app(), shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize(("target", "expected"), [
    ("192.168.1.5", "ws://192.168.1.5:8765/ws"),
    ("192.168.1.5:8000", "ws://192.168.1.5:8000/ws"),
    ("http://127.0.0.1:8000/", "ws://127.0.0.1:8000/ws"),
    ("https://room.example/#join", "wss://room.example/ws"),
    ("https://room.example/table/?width=80", "wss://room.example/table/ws"),
])
def test_room_address_accepts_the_link_the_host_shares(target, expected):
    assert address(target) == expected


async def test_browser_websocket_keeps_its_original_handler(monkeypatch):
    handled = []

    async def browser(self, request):
        handled.append(request.headers.get("Sec-WebSocket-Protocol"))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json(["browser"])
        await ws.close()
        return ws

    monkeypatch.setattr(Server, "handle_websocket", browser)
    async with room_page() as url, aiohttp.ClientSession() as http:
        async with http.ws_connect(url + "/ws") as browser:
            assert await browser.receive_json() == ["browser"]
    assert handled == [None]


async def test_native_client_on_a_single_player_page_gets_an_explanation():
    async with room_page() as url, aiohttp.ClientSession() as http:
        async with http.ws_connect(url + "/ws", protocols=("autokeeper",)) as socket:
            reply = await socket.receive_json()
            assert reply["t"] == "error" and "单人游戏网页" in reply["text"]


@pytest.mark.parametrize("guest", [False, True])
async def test_http_room_url_reclaims_ai_control_in_the_real_lobby(fake, monkeypatch, guest):
    if guest:
        monkeypatch.setenv("AUTOKEEPER_GUEST", "1")
    else:
        monkeypatch.delenv("AUTOKEEPER_GUEST", raising=False)
    lan.remember(address="old-host:8765", name="旧名字")
    hub = make_hub(fake)
    hub.takeovers.active.add("lin")
    app = AutoKeeperApp(theme="amber")
    try:
        async with sockets(hub) as (http, url), room_page(urlsplit(url).port) as page_url:
            keeper = await sit(http, url, "zhouheng")
            async with app.run_test(size=(120, 40)) as pilot:
                lobby = LobbyScreen(page_url, "回来的人")
                await app.push_screen(lobby)
                async with asyncio.timeout(5):
                    while lobby.welcome is None:
                        await pilot.pause(0.05)
                assert lobby.link.ws.protocol == "autokeeper"
                if guest:
                    assert lan.recall()["address"] == "old-host:8765"
                else:
                    # Direct CLI entry bypasses JoinScreen.go; successful admission must persist it.
                    again = JoinScreen()
                    assert address(again.address) == address(page_url)
                    assert again.name_ == "回来的人"
                assert str(lobby.query_one("#sit", Button).label) == "收回控制"
                await pilot.press("enter")
                async with asyncio.timeout(5):
                    while not isinstance(app.screen, NetGameScreen):
                        await pilot.pause(0.05)
                assert app.screen.seat == "lin" and "lin" not in hub.takeovers.active
                assert hub.sits["lin"] is not None
            await until(keeper, lambda msg: msg["t"] == "seats" and not msg["seats"][1]["online"])
            assert hub.sits["lin"] is None
            link = Link(page_url, "再次连接")
            try:
                await link.connect()
                await until(link.ws, lambda msg: msg["t"] == "welcome")
                await link.send({"t": "sit", "investigator": "lin"})
                reply = await until(link.ws, lambda msg: msg["t"] == "seated")
                assert reply["investigator"] == "lin"
            finally:
                await link.close()
            await keeper.close()
    finally:
        await hub.takeovers.close()
        await hub.game.close()


@pytest.mark.parametrize("target", [
    "http://room.example", "https://room.example", "http://room.example:8123/table/",
    "192.168.1.5:9000",
])
async def test_retry_preserves_endpoint_scheme_port_and_proxy_path(monkeypatch, target):
    attempted = []

    async def unavailable(self):
        attempted.append(self.url)
        raise ConnectionError("test disconnect")

    monkeypatch.setattr(Link, "connect", unavailable)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        await app.push_screen(LobbyScreen(target, "玩家"))
        for attempt in range(2):
            async with asyncio.timeout(5):
                while not isinstance(app.screen, JoinScreen) or len(attempted) != attempt + 1:
                    await pilot.pause(0.05)
            assert app.screen.query_one("#addr", Input).value == address(target)
            assert attempted[-1] == address(target)
            if attempt == 0:
                app.screen.go()
    assert attempted == [address(target), address(target)]


@pytest.mark.parametrize("target", ["http://room.example", "https://room.example/table/", "host:9000"])
def test_room_without_advertised_addresses_shares_actual_endpoint(target):
    lobby = LobbyScreen(target, "玩家")
    assert lobby._room_text({}).plain == "客户端加入地址：" + address(target)
