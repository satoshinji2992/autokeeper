"""What the screens share: the save index, writing a save, loading a module (its skill
package compiled when stale) and a module's picture as text."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from rich.text import Text
from textual.theme import Theme

from .. import artkit, imagery, paths, pictures
from ..engine.state import GameState
from ..module.compiler import ensure_compiled
from ..module.schema import Module

THEMES = [
    Theme("amber", primary="#ffb000", secondary="#d98e04", accent="#ff6b35", foreground="#f3d7a6", background="#0c0a06",
          surface="#15110a", panel="#211a0f", error="#e5383b", success="#9bd770", warning="#ffd166", dark=True),
    Theme("phosphor", primary="#33ff66", secondary="#1fbf4c", accent="#b3ff66", foreground="#b8ffc8", background="#040a05",
          surface="#08140a", panel="#0d1f10", error="#ff5555", success="#33ff66", warning="#e6ff66", dark=True),
    Theme("lavender", primary="#9a7fc4", secondary="#c3b1e1", accent="#ff8fab", foreground="#ece6f6", background="#0f0c14",
          surface="#17121f", panel="#221a2e", error="#ff6b6b", success="#a0e8af", warning="#ffd6a5", dark=True),
    Theme("blood", primary="#c1121f", secondary="#e85d04", accent="#ffba08", foreground="#f1e3e3", background="#0b0505",
          surface="#160909", panel="#220d0d", error="#ff4d4d", success="#90be6d", warning="#ffba08", dark=True),
]


def save_index() -> dict:
    f = paths.save_index()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def write_save(state: GameState, module: Module) -> None:
    paths.games().mkdir(parents=True, exist_ok=True)
    target = paths.save(state.slot)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(state.model_dump_json(indent=1), encoding="utf-8")
    temporary.replace(target)
    idx = save_index()
    idx[state.slot] = {
        "label": state.label,
        "module": module.id,
        "title": module.title,
        "party": "、".join(i.name for i in state.investigators),
        "clock": state.clock,
        "location": module.location(state.location).name,
        "ending": state.ending,
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "path": str(module_dir(module.id)),
    }
    paths.save_index().write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    paths.remember_checkout()  # a later installed copy finds these saves


def module_dir(module_id: str) -> Path:
    for p in [*paths.module_folders(), paths.EXAMPLE]:  # the format sample is not offered, but its saves still load
        if p.name == module_id:
            return p
    raise FileNotFoundError(module_id)


def load_module(path: Path) -> Module:
    m = Module.load(path)
    ensure_compiled(m, path)
    pictures.adopt(m)  # what was found or drawn for it on this machine (pictures.py)
    return m


FACE = (30, 14)  # an investigator's portrait on the sheet (F4, the room, the cards)


def face_text(m: Module, key: str | None, style: str = "photo") -> Text:
    """An investigator's portrait at sheet size: the photo when there is one, else the art."""
    return art_text(m, key, FACE if key in m.pictures else None, style, face=True)


def art_text(m: Module, name: str | None, size: tuple[int, int] | None = None, style: str = "photo", *, face: bool = False) -> Text:
    """A picture (chafa) when the module has one and a size is given, else the hand-drawn art.
    `face`: a portrait, cropped round the face to fill `size`."""
    if name and size and name in m.pictures and imagery.available():
        try:
            return Text.from_ansi(imagery.render(m.pictures[name], *size, style, face=face))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass  # a missing or unreadable picture must not break selection or hide its ASCII fallback
    if not name or name not in m.art:
        return Text("")
    return artkit.colorize(m.art[name], m.art_colors.get(name))


# -- title -------------------------------------------------------------------------------
