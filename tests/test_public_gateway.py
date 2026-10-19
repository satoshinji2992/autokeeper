"""Public browser access requires a password, including WebSocket upgrades."""

from __future__ import annotations

import pytest
from aiohttp import ClientSession, CookieJar, WSServerHandshakeError, web

from dev.public_gateway import COOKIE, PublicServer

PASSWORD = "test-only-access-password"


@pytest.fixture
async def gateway():
    server = PublicServer("unused", password=PASSWORD, public_url="http://127.0.0.1:8090")

    async def websocket(request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        await socket.send_str("game connected")
        await socket.close()
        return socket

    server.handle_websocket = websocket
    app = await server._make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server.public_url = url
    try:
        yield server, url
    finally:
        await runner.cleanup()


async def test_anonymous_visitors_cannot_open_the_game_or_websocket(gateway):
    _, url = gateway
    async with ClientSession() as client:
        async with client.get(url, allow_redirects=False) as response:
            assert response.status == 303 and response.headers["Location"] == "/login"
        async with client.get(url + "/healthz") as response:
            assert response.status == 200
        with pytest.raises(WSServerHandshakeError) as failure:
            await client.ws_connect(url + "/ws")
        assert failure.value.status == 401
        async with client.post(url + "/login", data={"password": "wrong"}) as response:
            assert response.status == 401 and COOKIE not in response.cookies


async def test_login_opens_the_game_and_rejects_cross_site_or_forged_sessions(gateway):
    server, url = gateway
    async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
        async with client.post(url + "/login", data={"password": PASSWORD}, allow_redirects=False) as response:
            assert response.status == 303 and response.cookies[COOKIE]["httponly"]
            assert response.cookies[COOKIE]["samesite"] == "Strict"
        async with client.get(url) as response:
            html = await response.text()
            assert response.status == 200 and "AutoKeeper" in html and url.replace("http:", "ws:") in html
            assert PASSWORD not in html
        async with client.ws_connect(url + "/ws", origin=url) as ws:
            assert (await ws.receive()).data == "game connected"
        with pytest.raises(WSServerHandshakeError) as failure:
            await client.ws_connect(url + "/ws", origin="https://unrelated.example")
        assert failure.value.status == 403
    async with ClientSession(cookies={COOKIE: server.token() + "x"}) as client:
        async with client.get(url, allow_redirects=False) as response:
            assert response.status == 303


def test_public_access_rejects_plain_http_and_weak_passwords():
    with pytest.raises(ValueError, match="HTTPS"):
        PublicServer("unused", password=PASSWORD, public_url="http://example.com")
    with pytest.raises(ValueError, match="16"):
        PublicServer("unused", password="short", public_url="https://example.com")
