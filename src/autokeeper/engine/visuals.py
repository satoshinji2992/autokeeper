"""Illustrate improvised people, places and props without holding up a turn."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from .. import artkit, config, imagery
from ..agents import artist
from ..module.schema import Module
from .state import GameState

FALLBACKS = {
    "portrait": "    ___\n   /   \\\n  | o o |\n   \\_~_/\n  __| |__\n /       \\\n/_________\\",
    "scene": "       /\\\n  ____/  \\____\n /            \\\n/______________\\\n|  []  __  []  |\n|     |  |     |\n|_____|__|_____|",
    "item": "     ______\n    /_____/|\n   |     | |\n   |     | /\n   |_____|/",
}


class Visuals:
    def __init__(self, module: Module, state: GameState, cfg: config.Config | None, emit: Callable[[dict], Awaitable[None]]):
        self.module, self.state, self.cfg, self.emit = module, state, cfg, emit
        self.tasks: dict[str, asyncio.Task] = {}
        self.limit = asyncio.Semaphore(2)
        self.idle = asyncio.Event()
        self.idle.set()
        digest = hashlib.sha256(f"{module.id}/{state.slot}".encode()).hexdigest()[:24]
        self.folder = config.SAVES / "art" / digest
        for key, record in state.visuals.items():
            self._attach(key, record)

    def _attach(self, key: str, record: dict[str, str]) -> None:
        self.module.art[key] = record["art"]
        self.module.art_colors[key] = record["mask"]
        if record.get("picture") and Path(record["picture"]).is_file():
            self.module.pictures[key] = record["picture"]

    def schedule(self) -> None:
        targets = [(x.art, x.read_aloud, "scene") for x in self.module.locations]
        targets += [(n.portrait, f"{n.name}，{n.look or '衣着朴素的当地成年人'}", "portrait") for n in self.module.npcs]
        targets += [(i.art, f"{i.name}，{i.description}", "item") for i in self.module.items]
        for key, subject, kind in targets:
            if not key or not re.fullmatch(r"improv_(?:scene|portrait|item)_[0-9a-f]{16}", key):
                continue
            if key in self.tasks:
                if not self.tasks[key].done():
                    continue
                del self.tasks[key]
            if key not in self.state.visuals:
                art = FALLBACKS[kind]
                self.state.visuals[key] = {"art": art, "mask": artkit.auto_mask(art), "status": "pending", "brief": subject}
                self._attach(key, self.state.visuals[key])
            # Artwork can finish during a turn. Its own cache survives without saving a
            # half-resolved game state over the last completed turn.
            try:
                cached = json.loads((self.folder / f"{key}.json").read_text(encoding="utf-8"))
                valid = isinstance(cached, dict) and all(isinstance(value, str) for value in cached.values())
                if (
                    valid and cached.get("status") in ("ready", "failed") and cached.get("art", "").strip()
                    and int(cached.get("attempts", "0")) >= int(self.state.visuals[key].get("attempts", "0"))
                    and not artkit.check(key, cached["art"], cached["mask"], portrait=kind == "portrait")
                ):
                    self.state.visuals[key] = cached
                    self._attach(key, cached)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            record = self.state.visuals[key]
            retry = record["status"] == "failed" and int(record.get("attempts", "0")) < 2
            photo = (record["status"] == "ready" and record.get("photo_status") == "pending"
                     and int(record.get("photo_attempts", "0")) < 2)
            if self.cfg is not None and (record["status"] == "pending" or retry or photo):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    continue
                self.tasks[key] = asyncio.create_task(self._make(key, subject, kind), name=f"illustrate:{key}")

    async def _make(self, key: str, subject: str, kind: str) -> None:
        assert self.cfg is not None
        record = self.state.visuals[key]
        try:
            async with self.limit:
                await self.idle.wait()
                spent = sum(v.get("cost_usd", 0) for k, v in self.state.usage.items() if k.startswith("visual:"))
                attempts = sum(int(r.get("attempts", "0")) for r in self.state.visuals.values())
                if ((record["status"] != "ready" and attempts >= self.cfg.visual_limit)
                        or self.cfg.visual_limit == 0 or spent >= self.cfg.visual_budget_usd):
                    return  # keep the placeholder; foreground narration owns the remaining budget
                async with asyncio.timeout(90):
                    if record["status"] != "ready":
                        record["status"] = "pending"
                        record["attempts"] = str(int(record.get("attempts", "0")) + 1)
                        text, mask = await artist.draw(
                            self.cfg, f"统一美术：简洁黑白钢笔插画，固定外貌与衣着，不改人物身份。{self.module.era}，"
                            f"{self.module.setting}。{record.get('brief', subject)}", portrait=kind == "portrait",
                            on_reply=lambda reply: self.state.record_usage("visual:artist", reply),
                        )
                        if not text.strip() or artkit.check(key, text, mask, portrait=kind == "portrait"):
                            raise ValueError("Invalid illustration")
                        record.update(art=text, mask=mask, status="ready", photo_status="pending")
                        self._attach(key, record)
                        await self._publish(key)
                    await self.idle.wait()
                    spent = sum(v.get("cost_usd", 0) for k, v in self.state.usage.items() if k.startswith("visual:"))
                    if spent >= self.cfg.visual_budget_usd:
                        return
                    if self.cfg.search != "none":
                        record["photo_attempts"] = str(int(record.get("photo_attempts", "0")) + 1)
                        await self._photo(key, record, subject, kind)
                    record["photo_status"] = "done"
                    record.pop("error", None)
                    await self._cache(key)
        except asyncio.CancelledError:
            raise  # still pending: resume the illustration when the save is loaded
        except Exception as exc:
            # Keep the illustration already on screen, and make the failure inspectable
            # in the save instead of breaking play or leaking an unobserved task error.
            record.update(status="ready" if record["status"] == "ready" else "failed", error=type(exc).__name__)
            await self._cache(key)

    async def _photo(self, key, record, subject, kind) -> None:
        await imagery.fetch_folder(
            self.cfg, self.folder, [(key, f"{self.module.era}，{subject}", kind)], log=lambda _: None,
            on_reply=lambda role, reply: self.state.record_usage(f"visual:{role}", reply),
        )
        picture = self.folder / f"{key}.jpg"
        if picture.is_file():
            record["picture"] = str(picture)
            self._attach(key, record)
            await self._publish(key)

    def pause(self) -> None:
        self.idle.clear()

    def resume(self) -> None:
        self.idle.set()
        self.schedule()

    async def _publish(self, key: str) -> None:
        if self.cfg is not None:
            self._attach(key, self.state.visuals[key])
        await self._cache(key)
        await self.emit({"type": "art", "key": key, **self.state.visuals[key]})

    async def _cache(self, key: str) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / f"{key}.json").write_text(json.dumps(self.state.visuals[key], ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # a cache failure must not hide an illustration that is already ready

    async def close(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
