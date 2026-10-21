"""What a module's places, people, things and creatures are shown as where the module has no
picture of its own: a photo found for it and prepared like the module's own (any licence will do:
it stays on this machine); failing that, one an image model draws (imagegen); and only where
neither can be had (no network, no Codex, the module's budget spent) one painted by program
(scenery), which is quick and costs nothing but is no match for either and looks the same for
every module. Passers-by get no portrait at all.

They are found or drawn once per module, for all its games, into paths.module_art (the module's
own folder is left alone: a bundled module's is the repository), ahead of play (`autokeeper art
prepare`, and an import) or, for what is still missing, in the background of a game
(engine/visuals.py). adopt() puts them in front of the painted ones when a game opens the module.
What could not be had is noted beside it (<key>.tried.json): the search is not run again, nor a
drawing that failed twice, for RETRY days.

    gaps(m)   what has no picture of its own (a painted one counts as none)
    fill(...) a photo for each, else a drawing; returns the keys that got one
    adopt(m)  the ones found or drawn before, under their keys
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import threading
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any, NamedTuple

from . import config, credits, imagegen, imagery, paths, scenery
from .module import package
from .module.schema import NPC, Module

FOLDERS = {"scene": "places", "portrait": "people", "item": "items", "creature": "people"}
RETRY = 7  # days before what could not be found, or drawn twice over, is tried again


class Gap(NamedTuple):
    key: str  # the picture key it is shown under
    subject: str  # what it is, for the search and the image model: only what can be seen
    kind: str  # scene, portrait, item or creature


def passerby(n: NPC) -> bool:
    """Someone with no part to play beyond being there: no aims, nothing they know, hide or give
    away. They get no portrait."""
    return not (n.important or n.goal or n.knows or n.secrets or n.lies or n.clues or n.gates)


def folder(m: Module) -> Path:
    return paths.module_art(m.id)


def _real(m: Module, key: str) -> bool:
    """A picture of its own, found or drawn for `key`: not one painted by program."""
    path = m.pictures.get(key)
    return bool(path) and not scenery.painted(path)


def gaps(m: Module) -> list[Gap]:
    """Every place, person (not a passer-by), thing worth registering and creature of the module
    with no picture of its own, under the key it has or the one scenery would paint it under."""
    out = [Gap(loc.art or f"scene-{loc.id}", f"{loc.name}：{loc.read_aloud.strip()[:160]}", "scene") for loc in m.locations]
    out += [Gap(n.portrait or f"face-{n.id}", f"{n.name}，{n.role}；{n.look}", "portrait") for n in m.npcs if not passerby(n)]
    out += [Gap(p.portrait or f"face-{p.id}", f"{p.name}，{p.occupation}，{p.age} 岁", "portrait") for p in m.pregens]
    out += [Gap(i.art or f"item-{i.id}", f"{i.name}：{i.description}", "item") for i in m.items if i.important]
    out += [Gap(f"creature-{c.id}", f"{c.name}：{c.description}", "creature") for c in m.creatures]
    return [g for g in out if not _real(m, g.key)]


def _register(m: Module, key: str, path: Path) -> None:
    """The picture under its key, and the place, person or thing with no key of its own given it."""
    m.pictures[key] = str(path)
    for loc in m.locations:
        if not loc.art and key == f"scene-{loc.id}":
            loc.art = key
    for who in [*m.npcs, *m.pregens]:
        if not who.portrait and key == f"face-{who.id}":
            who.portrait = key
    for i in m.items:
        if not i.art and key == f"item-{i.id}":
            i.art = key


def adopt(m: Module) -> list[str]:
    """The pictures found or drawn for the module on this machine, in front of anything painted.
    Returns their keys."""
    found = package.pictures(folder(m))
    keys = [key for key in found if not _real(m, key)]
    for key in keys:
        _register(m, key, Path(found[key]))
    return keys


async def fill(
    cfg: config.Config, m: Module, *, log: Callable[[str], Any] = print, where: str = "import", force: bool = False,
    limit: AbstractAsyncContextManager | None = None, ready: Callable[[], Awaitable[Any]] | None = None,
    budget: Callable[[], bool] | None = None, on_search: Callable[[str, Any], None] | None = None,
    on_draw: Callable[[Any], None] | None = None, done: Callable[[str, Path], Awaitable[None]] | None = None,
) -> list[str]:
    """A picture for every gap, one at a time: a photo (the vision model writes the searches and
    picks one), else a drawing by Codex; nothing is painted here. What was tried in vain lately is
    not tried again (RETRY) unless `force`. `limit` and `ready` hold a game's turn, `budget` says
    whether its pictures may cost more (engine/visuals.py); `done(key, path)` hears of each one as it
    lands. Returns the keys."""
    import httpx

    era = "，".join(x for x in (m.era, m.setting) if x)
    search, draw = cfg.search != "none", imagegen.enabled(cfg)
    made: list[str] = []
    if not (search or draw):
        return made
    async with httpx.AsyncClient() as client:
        for gap in gaps(m):
            if budget is not None and not budget():
                break
            target = folder(m) / FOLDERS[gap.kind] / f"{gap.key}.jpg"
            tried = {} if force else _tried(target)
            path: Path | None = target if target.is_file() else None
            searched, failed = bool(tried.get("searched")), int(tried.get("failed", 0))
            async with limit or nullcontext():
                if ready is not None:
                    await ready()
                if path is None and search and gap.kind != "creature" and not searched:  # nobody has photographed a ghoul
                    try:
                        path = await _photo(cfg, client, gap, f"{gap.subject}（{era}）", target, on_search, log)
                        searched = path is None
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # offline, a search that broke: not held against it, and on to the drawing
                        log(f"  · {gap.key}：找照片出错（{type(exc).__name__}）")
                if path is None and draw and imagegen.left(m.id) > 0 and failed < 2:
                    try:
                        path = await imagegen.make(imagegen.prompt(gap.subject, gap.kind, era=m.era, setting=m.setting), target,
                                                   kind=gap.kind, module=m.id, title=gap.subject[:80], where=where, on_reply=on_draw)
                        log(f"  ✓ {gap.key}：没找到合适的照片，AI 画了一张")
                    except (imagegen.Unavailable, imagegen.OverBudget) as exc:
                        draw = False
                        log(f"  · AI 画图先停在这里：{exc}")
                    except (imagegen.Failed, OSError, ValueError) as exc:
                        failed += 1
                        log(f"  · {gap.key}：AI 没画成（{type(exc).__name__}）")
            if path is None:
                _note(target, searched, failed)
                log(f"  · {gap.key}：没有找到照片，也没能画，暂用程序画的图")
                continue
            target.with_suffix(".tried.json").unlink(missing_ok=True)
            _register(m, gap.key, path)
            made.append(gap.key)
            if done is not None:
                await done(gap.key, path)
    return made


def _tried(target: Path) -> dict[str, Any]:
    """What was tried for this picture lately and came to nothing ({} once RETRY days have passed)."""
    try:
        note = json.loads(target.with_suffix(".tried.json").read_text(encoding="utf-8"))
        when = dt.datetime.fromisoformat(note["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return note if dt.datetime.now() - when < dt.timedelta(days=RETRY) else {}


def _note(target: Path, searched: bool, failed: int) -> None:
    if not (searched or failed):  # nothing was tried (offline, no Codex): try again next time
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    note = {"at": dt.datetime.now().isoformat(timespec="seconds"), "searched": searched, "failed": failed}
    target.with_suffix(".tried.json").write_text(json.dumps(note), encoding="utf-8")


async def _photo(cfg, client, gap: Gap, subject: str, target: Path, on_search, log) -> Path | None:
    """A photo for it saved as `target`, credited and prepared; None when none fits."""
    async with asyncio.timeout(120):
        if on_search is None:
            found = await imagery.find_picture(cfg, client, subject, gap.kind, web=True)
        else:
            found = await imagery.find_picture(cfg, client, subject, gap.kind, web=True, on_reply=on_search)
    if not found:
        return None
    im, c = found
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.stem}.{os.getpid()}-{threading.get_ident()}.part.jpg")
    await asyncio.to_thread(im.save, part, quality=92)
    os.replace(part, target)  # whole or not at all: another game may be reading the folder
    credits.put(target, asdict(c) | {"kind": gap.kind})
    await asyncio.to_thread(imagery.preprocess, target, gap.kind if gap.kind in imagery.PIPELINES else "scene")
    log(f"  ✓ {gap.key}：{c.title}（{c.license}）")
    return target


__all__ = ["Gap", "adopt", "fill", "gaps", "passerby"]
