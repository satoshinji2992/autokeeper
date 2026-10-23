"""A player's connection to the Hub: send messages, and pump what arrives to whichever
screen is showing (the lobby first, then the game)."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

Handler = Callable[[dict[str, Any]], Awaitable[None]]
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


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
        self.closing = False

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(self.url, heartbeat=20, max_msg_size=64 * 1024 * 1024)
        from .. import __version__
        from .hub import room_key

        if await self.send({"t": "hello", "name": self.name, "key": self.key or room_key(self.url), "version": __version__}) is False:
            raise ConnectionError("连接已断开，未完成入房确认。")

    async def send(self, msg: dict[str, Any]) -> bool:
        try:
            if self.ws is None or self.ws.closed:
                raise ConnectionError("连接已断开")
            await self.ws.send_str(json.dumps(msg, ensure_ascii=False))
            return True
        except (ConnectionError, aiohttp.ClientError, RuntimeError):
            if self.ws is not None and not self.ws.closed:
                try:
                    await self.ws.close()
                except (ConnectionError, aiohttp.ClientError):
                    log.debug("关闭已中断的发送连接失败", exc_info=True)
            if self.handler is not None:
                await self.handler({"t": "unsent", "message": msg})
            return False

    async def pump(self) -> None:
        """Deliver every message to the current handler until the Hub goes away."""
        assert self.ws is not None
        reason = "与房主的连接已断开。"
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT and self.handler is not None:
                    await self.handler(json.loads(msg.data))
        except Exception:
            log.exception("联机消息接收或处理失败")
            reason = "联机消息接收中断，请重新加入房间。"
        finally:
            if not self.closing:
                try:
                    await self.ws.close()
                except (ConnectionError, aiohttp.ClientError):
                    log.debug("关闭已中断的接收连接失败", exc_info=True)
                if self.handler is not None:
                    await self.handler({"t": "closed", "text": reason})

    async def close(self) -> None:
        self.closing = True
        if self.ws is not None:
            await self.ws.close()
        if self.session is not None:
            await self.session.close()
