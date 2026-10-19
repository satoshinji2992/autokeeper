"""Investigators made outside a module (public-figure cards), kept between games.

    data/cards/<id>.json         the sheet, and how it was made
    data/cards/<id>.jpg          its photo; where it came from is in data/cards/credits.yaml
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from pathlib import Path

from . import credits, paths
from .module.schema import Module
from .rules.coc import Investigator


def folder() -> Path:
    return paths.cards()


def keep(inv: Investigator, made: dict, photo: Path | None = None) -> None:
    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    if photo is not None and photo.exists():
        target = d / (inv.portrait + photo.suffix)
        shutil.copy2(photo, target)
        credits.put(target, credits.get(photo))
    record = {"investigator": inv.model_dump(mode="json"), **made}
    (d / f"{inv.id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")


def saved() -> list[Investigator]:
    """Every kept card, newest first."""
    out = []
    files = list(folder().glob("*.json")) if folder().is_dir() else []
    for f in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            out.append(Investigator.model_validate(json.loads(f.read_text(encoding="utf-8"))["investigator"]))
        except (ValueError, KeyError):  # a hand-edited or half-written file is skipped, not fatal
            continue
    return out


def attach(m: Module, investigators: Iterable[Investigator]) -> None:
    """Their photos into the module's pictures, so the sheet can show them."""
    for inv in investigators:
        photo = folder() / f"{inv.portrait}.jpg"
        if inv.portrait and inv.portrait not in m.pictures and photo.exists():
            m.pictures[inv.portrait] = str(photo)
