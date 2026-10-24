"""Versions of actually observed artwork, independent of the world's current artwork."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from . import paths

if TYPE_CHECKING:
    from .engine.state import GameState
    from .module.schema import Module


def revision(state: GameState, key: str) -> str:
    look = state.looks.get(key, {})
    return look.get("later_revision", look.get("revision", ""))


@lru_cache(maxsize=256)
def _picture_copy(path: str, folder: str, signature: tuple[int, int, int]) -> tuple[str, str]:
    """A stat change invalidates the cache, including a replacement at the same source path."""
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    target = Path(folder) / f"seen-{digest}{Path(path).suffix.lower()}"
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return str(target), digest


def _content(module: Module, state: GameState, key: str, *, source: dict[str, str] | None = None) -> dict[str, str]:
    art = source.get("art", "") if source is not None else module.art.get(key, "")
    mask = source.get("mask", "") if source is not None else module.art_colors.get(key, "")
    path = source.get("picture", "") if source is not None else module.pictures.get(key, "")
    picture, digest = "", ""
    if path:
        try:
            game = hashlib.sha256(f"{module.id}/{state.slot}".encode()).hexdigest()[:24]
            stat = Path(path).stat()
            picture, digest = _picture_copy(path, str(paths.game_art(game)),
                                            (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            if not Path(picture).is_file():
                _picture_copy.cache_clear()
                picture, digest = _picture_copy(path, str(paths.game_art(game)),
                                                (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        except OSError:
            pass  # Keep the observed text; never substitute a mutable source image after failure.
    return {"art": art, "mask": mask, "picture": picture, "digest": digest}


def remember(module: Module, state: GameState, key: str | None) -> str:
    """Freeze an authorized observation, subscribing only to its own unfinished illustration."""
    if not key or state.visuals.get(key, {}).get("source"):
        return key or ""
    content = _content(module, state, key)
    version = revision(state, key)
    ident = "seen-" + hashlib.sha256(json.dumps([key, version, content], sort_keys=True).encode()).hexdigest()[:24]
    look = state.looks.get(key, {})
    pending = bool(look.get("later_revision") or look.get("status") == "pending"
                   or (not version and (state.visuals.get(key, {}).get("status") == "pending"
                                        or (not content["picture"] and not content["art"].strip()))))
    record = state.visuals.setdefault(ident, {**content, "status": "ready", "source": key,
                                           "revision": version, "pending": "1" if pending else ""})
    attach(module, ident, record)
    return ident


def attach(module: Module, key: str, record: dict[str, str]) -> None:
    module.art[key] = record["art"]
    module.art_colors[key] = record["mask"]
    if record.get("picture"):
        module.pictures[key] = record["picture"]


def finish(module: Module, state: GameState, key: str, version: str, *, source: dict[str, str] | None = None) -> list[str]:
    """Finish only observations made for this generation; an old sighting never advances."""
    waiting = [(ident, record) for ident, record in state.visuals.items()
               if record.get("source") == key and record.get("revision") == version and record.get("pending")]
    if not waiting:
        return []
    content = _content(module, state, key, source=source)
    for ident, record in waiting:
        record.update(content, pending="")
        attach(module, ident, record)
    return [ident for ident, _ in waiting]
