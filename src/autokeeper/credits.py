"""Where pictures came from: one credits.yaml per folder of pictures, keyed by picture name.

    note: a line about the whole folder (licences, how the pictures were changed)
    pictures:
      thomas: {title, page, thumb, license, artist, width, height, kind}
      square_fire: {title, license, artist, prompt, based_on: {...}}   # made by Codex

A module's photos sit in art/places/, art/people/ and art/items/ and share art/credits.yaml
(module/package.py); any other folder of pictures (your cards, a game's pictures, the
big-scene cache) keeps its own credits.yaml next to them.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

FILE = "credits.yaml"
KINDS = ("places", "people", "items")  # a module's picture folders, under art/
_lock = threading.Lock()


def file_for(picture: Path) -> Path:
    folder = Path(picture).parent
    if folder.name in KINDS and folder.parent.name == "art":
        return folder.parent / FILE
    return folder / FILE


def read_file(file: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(Path(file).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(file: Path, data: dict[str, Any]) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + ".tmp")
    temporary.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    temporary.replace(file)


def get(picture: Path) -> dict[str, Any] | None:
    record = read_file(file_for(picture)).get("pictures", {}).get(Path(picture).stem)
    return dict(record) if isinstance(record, dict) else None


def put(picture: Path, record: dict[str, Any] | None) -> None:
    """Record (or with None, forget) where `picture` came from."""
    file = file_for(picture)
    with _lock:
        data = read_file(file)
        pictures = data.setdefault("pictures", {})
        if record is None:
            if pictures.pop(Path(picture).stem, None) is None:
                return
        else:
            pictures[Path(picture).stem] = record
        _write_file(file, data)


def listing(folder: Path) -> list[dict[str, Any]]:
    """Every credit in a folder's credits.yaml (a module's art/), with its picture's name."""
    pictures = read_file(Path(folder) / FILE).get("pictures", {})
    return [dict(record) | {"name": name} for name, record in sorted(pictures.items()) if isinstance(record, dict)]
