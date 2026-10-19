"""大场景: one big picture over the whole screen at a turning point of the story: the opening,
something sudden, a crisis, an important place, a monster, the final boss, an ending.

    Module.showcases, and up to IMPROV_MAX the director adds in a game
    ->  pictures prepared ahead in the background, one at a time, the opening first
    ->  at its moment, shown if its picture is ready: never waited for, never shown twice

A picture comes, in this order, from the module itself (`art`), from Codex redrawing a
reference (`base`, the place its trigger names, or a photo imagery's search finds), or from
Codex and the words alone. Without Codex nothing is generated: the module's own picture of
that place, or a found photo when picture search is on, is shown as it is; else the big scene
is skipped. A module's big-scene pictures are kept in data/cache/showcases/<module>/ (never in the
repository) for every game of it; the director's are kept with their save.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from .. import config, credits, imagegen, imagery, paths
from ..module.schema import Module, Showcase
from .state import GameState

IMPROV_MAX = 3  # big scenes the director may add in one game
MOST = 15  # big scenes one module may have


def cache(module_id: str) -> Path:
    """Where a module's big-scene pictures are kept: outside its folder, for all its games."""
    return paths.showcases(module_id)


def name(sc: Showcase) -> str:
    """The picture's file name; a big scene written differently gets a new picture."""
    digest = hashlib.sha256(f"{sc.kind}|{sc.when}|{sc.prompt}|{sc.base}".encode()).hexdigest()[:8]
    return f"{sc.id}-{digest}.jpg"


def own(m: Module, sc: Showcase) -> str:
    """The module's own picture for it, when it names one that is there."""
    path = m.pictures.get(sc.art, "") if sc.art else ""
    return path if path and Path(path).is_file() else ""


def reference(m: Module, sc: Showcase) -> Path | None:
    """The module's picture to redraw: `base`, else that of the place its trigger names (the
    opening's first place, an event's place) or of the creature it names."""
    side, _, ident = sc.when.partition(":")
    keys = [sc.base] if sc.base else []
    place = m.start if sc.when == "opening" else ident if side == "visit" else ""
    if side == "event":
        ev = next((e for e in m.events if e.id == ident), None)
        place = (ev.move_to or (ev.when[6:] if ev.when.startswith("visit:") else "")) if ev else ""
    if side == "creature":
        keys.append(ident)
    loc = next((x for x in m.locations if x.id == place), None)
    if loc is not None and loc.art:
        keys.append(loc.art)
    for key in keys:
        path = m.pictures.get(key, "")
        if path and Path(path).is_file():
            return Path(path)
    return None


def photo(target: Path) -> Path:
    return target.with_name(target.stem + ".photo.jpg")


def ready(cfg: config.Config | None, m: Module, sc: Showcase, target: Path) -> str:
    """The picture a big scene would show now ("" when it has none yet). With Codex, only its
    own or a drawn one; without, the module's picture of its place or a found photo as is."""
    if path := own(m, sc):
        return path
    if target.is_file():
        return str(target)
    if imagegen.enabled(cfg):
        return ""  # still to be drawn: until then, nothing
    if photo(target).is_file():
        return str(photo(target))
    ref = reference(m, sc)
    return str(ref) if ref else ""


async def _search(cfg: config.Config, sc: Showcase, target: Path, on_reply=None) -> Path | None:
    """A photo for it through imagery's search, kept beside the picture (and a mark when there
    is none, so the next game does not look again)."""
    import httpx

    found_file, none = photo(target), target.with_name(target.stem + ".none")
    if found_file.is_file():
        return found_file
    if none.exists() or cfg.search == "none":
        return None
    try:
        async with httpx.AsyncClient() as client:
            found = await imagery.find_picture(cfg, client, f"{sc.title}：{sc.prompt}", "scene", web=imagery.private(target.parent),
                                               **({"on_reply": on_reply} if on_reply else {}))
    except Exception:  # no photo then; it is only a reference
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    if not found:
        none.touch()
        return None
    im, c = found
    im.save(found_file, quality=92)
    credits.put(found_file, asdict(c) | {"kind": "scene"})
    return found_file


async def prepare(cfg: config.Config | None, m: Module, sc: Showcase, target: Path, *, where: str = "import",
                  on_reply: Callable | None = None, on_search: Callable | None = None) -> str:
    """Make one big scene's picture into `target`, if it has none. How it came ("" for not at
    all): 模组原图, 已经画好, AI 照原图画, AI 照描述画, 原图照用, 找到的照片."""
    if own(m, sc):
        return "模组原图"
    if target.is_file():
        return "已经画好"
    if cfg is None:
        return ""
    ref = reference(m, sc)
    found = await _search(cfg, sc, target, on_search) if ref is None else None
    if imagegen.enabled(cfg):
        source = ref or found
        world = {"era": m.era, "setting": m.setting}
        try:
            await imagegen.make(imagegen.staged(sc.prompt, reference=source is not None, **world), target, kind="scene",
                                module=m.id, original=source, title=sc.title, where=where, on_reply=on_reply)
            return "AI 照原图画" if source else "AI 照描述画"
        except (imagegen.Unavailable, imagegen.OverBudget, imagegen.Failed, OSError, ValueError):
            return ""  # the moment is skipped; nothing was counted
    return "原图照用" if ref else "找到的照片" if found else ""


class Showcases:
    """A game's big scenes: pictures prepared in the background, shown at their moment."""

    def __init__(self, module: Module, state: GameState, cfg: config.Config | None, emit: Callable[[dict], Awaitable[None]],
                 folder: Path, idle: asyncio.Event, limit: asyncio.Semaphore):
        self.module, self.state, self.cfg, self.emit = module, state, cfg, emit
        self.folder, self.idle, self.limit = folder, idle, limit  # this save's own pictures; shared with Visuals
        self.task: asyncio.Task | None = None
        self.tried: set[str] = set()  # prepared in this session, or found impossible
        self.waiting = ""  # the opening, while its picture is still on its way and nobody has acted

    def on(self) -> bool:
        return self.cfg is not None and getattr(self.cfg, "showcase", True)

    def target(self, sc: Showcase) -> Path:
        return (self.folder if sc.improvised else cache(self.module.id)) / name(sc)

    def picture(self, sc: Showcase) -> str:
        return ready(self.cfg, self.module, sc, self.target(sc))

    def order(self) -> list[Showcase]:
        """The opening first, then as the module lists them, the director's last."""
        return sorted(self.module.showcases, key=lambda sc: (sc.when != "opening", sc.improvised))

    def prepare(self) -> None:
        """Prepare the pictures still missing, one at a time, unless that is going on already."""
        if not self.on() or (self.task is not None and not self.task.done()):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.task = asyncio.create_task(self._prepare(), name="showcases")

    async def _prepare(self) -> None:
        while True:
            due = [sc for sc in self.order()
                   if sc.id not in self.state.shown and sc.id not in self.tried and not self.picture(sc)]
            if not due:
                return
            sc = due[0]
            self.tried.add(sc.id)
            async with self.limit:
                await self.idle.wait()
                await prepare(self.cfg, self.module, sc, self.target(sc), where="game",
                              on_reply=lambda reply: self.state.record_usage("visual:imagegen", reply),
                              on_search=lambda role, reply: self.state.record_usage(f"visual:{role}", reply))
            if self.waiting == sc.id and self.state.turn == 0 and (path := self.picture(sc)):
                self.waiting = ""
                self.state.shown.append(sc.id)
                await self._show(sc, path)  # the opening, still before anyone has acted

    async def fire(self, trigger: str) -> None:
        """The moment of the big scenes `trigger` names: shown if the picture is ready, and
        over either way. The opening waits for its picture until someone acts."""
        for sc in self.module.showcases:
            if sc.when != trigger or sc.id in self.state.shown:
                continue
            path = self.picture(sc) if self.on() else ""
            if not path and trigger == "opening" and self.on():
                self.waiting = sc.id
                continue
            self.state.shown.append(sc.id)
            if path:
                await self._show(sc, path)

    async def named(self, text: str) -> None:
        """A creature whose name first comes up in what the players read."""
        for sc in self.module.showcases:
            side, _, ident = sc.when.partition(":")
            if side != "creature" or sc.id in self.state.shown:
                continue
            creature = next((c for c in self.module.creatures if c.id == ident), None)
            if creature is not None and creature.name and creature.name in text:
                await self.fire(sc.when)

    def settle(self) -> None:
        """Someone has acted: the opening's moment is over, picture or not."""
        if self.waiting and self.waiting not in self.state.shown:
            self.state.shown.append(self.waiting)
        self.waiting = ""

    async def _show(self, sc: Showcase, path: str) -> None:
        await self.emit({"type": "showcase", "id": sc.id, "kind": sc.kind, "title": sc.title, "picture": path})

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def prepare_module(cfg: config.Config, m: Module, *, log=print) -> list[str]:
    """Every big scene's picture for a module, one at a time, the opening first (the import's
    last step and `autokeeper showcase`); what is there already stays."""
    done = []
    for sc in sorted(m.showcases, key=lambda sc: sc.when != "opening")[:MOST]:
        target = cache(m.id) / name(sc)
        how = await prepare(cfg, m, sc, target)
        if how and ready(cfg, m, sc, target):
            done.append(sc.id)
            log(f"  ✓ {sc.id}（{sc.kind}）：{how}")
        else:
            log(f"  · {sc.id}（{sc.kind}）：没有图，到时跳过")
    return done
