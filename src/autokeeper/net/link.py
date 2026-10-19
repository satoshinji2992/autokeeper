"""A player's connection to the Hub: send messages, and pump what arrives to whichever
screen is showing (the lobby first, then the game)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

Handler = Callable[[dict[str, Any]], Awaitable[None]]


def address(target: str) -> str:
    """"192.168.1.5", "192.168.1.5:8765" or a full ws:// URL, as the Hub's websocket URL."""
    target = target.strip()
    if target.startswith(("ws://", "wss://")):
        return target if target.rstrip("/").endswith("/ws") else target.rstrip("/") + "/ws"
    if ":" not in target:
        target += ":8765"
    return f"ws://{target}/ws"


class Link:
    def __init__(self, url: str, name: str, key: str = ""):
        self.url, self.name = address(url), name
        self.key = key  # the host key, when this app opened the room itself (net/room.py)
        self.handler: Handler | None = None
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.host = False  # the Hub took this player for the host (welcome)

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(self.url, heartbeat=20, max_msg_size=64 * 1024 * 1024)
        from .. import __version__
        from .hub import room_key

        await self.send({"t": "hello", "name": self.name, "key": self.key or room_key(self.url), "version": __version__})

    async def send(self, msg: dict[str, Any]) -> None:
        if self.ws is not None and not self.ws.closed:
            await self.ws.send_str(json.dumps(msg, ensure_ascii=False))

    async def pump(self) -> None:
        """Deliver every message to the current handler until the Hub goes away."""
        assert self.ws is not None
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT and self.handler is not None:
                await self.handler(json.loads(msg.data))
        if self.handler is not None:
            await self.handler({"t": "closed"})

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.session is not None:
            await self.session.close()
