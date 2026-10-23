"""Temporary control of disconnected seats; character ownership stays with the human."""

from __future__ import annotations

import asyncio
from typing import Any

AI_HANDOFF = object()


class Takeovers:
    def __init__(self, hub: Any, enabled: bool, delay: float = 15.0):
        self.hub, self.enabled, self.delay = hub, enabled, delay
        self.active: set[str] = set()
        self.tasks: dict[str, asyncio.Task] = {}
        self.epoch = 0
        self.used: dict[str, int] = {}
        self.changed = asyncio.Event()
        self.closed = False

    def online(self) -> bool:
        return any(self.hub.sits.get(who) is not None for who in self.hub.state.human)

    def waiting(self, who: str) -> bool:
        return self.enabled and who in self.hub.state.human and self.hub.sits.get(who) is None and who not in self.active

    def pulse(self) -> None:
        self.epoch += 1
        previous, self.changed = self.changed, asyncio.Event()
        previous.set()
        self.hub.wake.set()
        if tables := self.hub._scene_tables:
            tables.runtime.changed.set()
            for worker in tables.workers.values():
                worker.wake.set()

    def left(self, who: str) -> None:
        if (not self.enabled or self.closed or not self.hub.started.is_set()
                or who not in self.hub.state.human or who in self.tasks or who in self.active):
            return
        self.tasks[who] = asyncio.create_task(self._after_delay(who), name=f"takeover:{who}")

    async def _after_delay(self, who: str) -> None:
        try:
            await asyncio.sleep(self.delay)
            if self.closed or self.hub.sits.get(who) is not None or self.hub.over:
                return
            self.active.add(who)
            self._controller(who, "ai")
            context = self.hub._context(who)
            if context.asking.get(who):
                context.choices[who].put_nowait(AI_HANDOFF)
            if context.retrying == who:
                context.inbox[who].put_nowait("/retry")
            self.pulse()
            self.hub._save()
            await self.hub._share_seats()
            await self.hub._broadcast({"t": "say", "from": "系统", "text": f"{self.hub.state.find(who).name}已由 AI 临时接管。"})
        finally:
            self.tasks.pop(who, None)

    def _controller(self, who: str, kind: str) -> None:
        context = self.hub._context(who)
        if encounter := context.game.world_encounter():
            if (person := encounter.get(who)) is not None:
                person.controller = kind

    async def returned(self, who: str) -> None:
        if pending := self.tasks.pop(who, None):
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        controlled = who in self.active
        self.active.discard(who)
        if who in self.hub.state.human:
            self._controller(who, "human")
        self.pulse()
        if controlled:
            self.hub._save()
            await self.hub._send(self.hub.sits[who], {"t": "notice", "text": "已收回调查员控制权，代管期间已完成的行动保留。"})

    async def pace(self, ident: str, members: list[str]) -> None:
        """An AI-only branch acts on room activity or a real outstanding time request."""
        while self.enabled and all(who in self.active for who in members):
            tables = self.hub._scene_tables
            scene = tables.runtime.scenes[ident] if tables else None
            needs_time = scene is not None and any(
                not other.closed and other.id != ident and other.reservation.get("end", other.at) > scene.at
                for other in tables.runtime.scenes.values())
            if self.online() and (needs_time or self.used.get(ident, -1) != self.epoch):
                self.used[ident] = self.epoch
                return
            signal = self.changed
            if scene is not None:
                pulse = tables.runtime._pulse
                a, b = asyncio.create_task(signal.wait()), asyncio.create_task(pulse.wait())
                try:
                    await asyncio.wait([a, b], return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in (a, b):
                        task.cancel()
                    await asyncio.gather(a, b, return_exceptions=True)
            else:
                await signal.wait()

    async def close(self) -> None:
        self.closed = True
        tasks, self.tasks = list(self.tasks.values()), {}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pulse()
