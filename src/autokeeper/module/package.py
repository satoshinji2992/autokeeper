"""How a module is laid out on disk.

    modules/<id>/
      module.yaml        title, era, setting, hook, truth, keeper notes, opening, flags, start, plugin list
      places.yaml        locations
      people.yaml        npcs, creatures, pregens
      clues.yaml         clues, items, handouts, sketches
      story.yaml         events, conclusions, endings, showcases
      situation.yaml     the situation (局面): relations, agendas, foreshadowing, ties, mechanics to plug in
      plugins/<id>/      tested rule plugins
      art/
        places/          scene photos and pictures, <key>.jpg (or .png, .webp)
        people/          portraits
        items/           handouts, maps, covers and the rest
        ascii/           <key>.txt hand-drawn art and <key>.color, its colour mask
        credits.yaml     where every picture came from (credits.py)
      source/            an import's original text, page images and the importer's notes (local only)

Everything derived lives outside: the compiled skill in skills/module-<id>/, the knowledge
base and the big-scene pictures under data/cache/, the renderer's prepared copy of each
photo in data/cache/pictures/.

A module.yaml holding everything loads too, as does a flat art/ folder (<key>.txt, <key>.jpg):
that is how the importer's agent writes its draft, and normalize() files it into the layout
above once the draft passes its checks.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from .. import credits

MAIN = "module.yaml"
PARTS: dict[str, tuple[str, ...]] = {
    "places.yaml": ("locations",),
    "people.yaml": ("npcs", "creatures", "pregens"),
    "clues.yaml": ("clues", "items", "handouts", "sketches"),
    "story.yaml": ("events", "conclusions", "endings", "showcases", "prelude"),
    "situation.yaml": ("relations", "agendas", "foreshadow", "ties", "mechanics"),
}
IMAGES = (".jpg", ".jpeg", ".png", ".webp")
DRAFT_LEFTOVERS = ("_example", "module.yaml.bak")  # the importer's reference copy and backups


# -- the YAML ------------------------------------------------------------------------------
def read(folder: Path) -> dict[str, Any]:
    """module.yaml with the part files merged in."""
    folder = Path(folder)
    data = yaml.safe_load((folder / MAIN).read_text(encoding="utf-8")) or {}
    for name in PARTS:
        if (folder / name).is_file():
            part = yaml.safe_load((folder / name).read_text(encoding="utf-8")) or {}
            if not isinstance(part, dict):
                raise ValueError(f"{name} 应该是一个映射（字段名: 内容）")
            data.update(part)
    return data


class _Dumper(yaml.SafeDumper):
    """Multi-line text as a | block, the way the bundled modules are written by hand."""


def _text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    if "\n" in value.strip("\n") and not any(line != line.rstrip() for line in value.splitlines()):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_Dumper.add_representer(str, _text)


def _dump(data: dict[str, Any]) -> str:
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=100)


TOP_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(\s|$)")


def split_text(text: str) -> dict[str, str]:
    """A whole-module module.yaml cut into the package's files as text, so the comments and
    the way it was written stay: each top-level key with the comment lines just above it."""
    owner = {key: name for name, keys in PARTS.items() for key in keys}
    out: dict[str, list[str]] = {MAIN: []}
    pending: list[str] = []
    target = MAIN
    for line in text.splitlines(keepends=True):
        if line.startswith("#") or not line.strip():
            pending.append(line)
            continue
        if found := TOP_KEY.match(line):
            target = owner.get(found.group(1), MAIN)
        out.setdefault(target, []).extend(pending)
        pending = []
        out[target].append(line)
    out[target].extend(pending)
    return {name: "".join(lines).strip("\n") + "\n" for name, lines in out.items() if "".join(lines).strip()}


def write(folder: Path, data: dict[str, Any]) -> Path:
    """Split `data` (Module.model_dump) into module.yaml and the part files; a part with
    nothing in it has no file."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    split = {key for keys in PARTS.values() for key in keys}
    (folder / MAIN).write_text(_dump({k: v for k, v in data.items() if k not in split}), encoding="utf-8")
    for name, keys in PARTS.items():
        part = {k: data[k] for k in keys if data.get(k)}
        if part:
            (folder / name).write_text(_dump(part), encoding="utf-8")
        else:
            (folder / name).unlink(missing_ok=True)
    return folder / MAIN


def joined_text(folder: Path) -> str:
    """The package's YAML files as one whole-module module.yaml, comments kept (the importer's
    agent works on one file)."""
    folder = Path(folder)
    texts = [(folder / name).read_text(encoding="utf-8") for name in (MAIN, *PARTS) if (folder / name).is_file()]
    return "\n".join(text.rstrip("\n") + "\n" for text in texts)


# -- the art -------------------------------------------------------------------------------
def ascii_art(art_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(art, colour masks) by key, from art/ascii/ and from a flat draft (which wins: it is newer)."""
    art: dict[str, str] = {}
    colors: dict[str, str] = {}
    for folder in (art_dir / "ascii", art_dir):
        if folder.is_dir():
            art |= {p.stem: p.read_text(encoding="utf-8").rstrip("\n") for p in sorted(folder.glob("*.txt"))}
            colors |= {p.stem: p.read_text(encoding="utf-8").rstrip("\n") for p in sorted(folder.glob("*.color"))}
    return art, colors


def _photos(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.iterdir()) if p.suffix.lower() in IMAGES and "." not in p.stem]


def pictures(art_dir: Path) -> dict[str, str]:
    """Picture key -> file, from art/places, art/people, art/items and a flat draft."""
    out: dict[str, str] = {}
    for folder in (*(art_dir / kind for kind in credits.KINDS), art_dir):
        out |= {p.stem: str(p) for p in _photos(folder)}
    return out


def find_picture(art_dir: Path, key: str) -> Path | None:
    found = pictures(Path(art_dir)).get(key)
    return Path(found) if found else None


def picture_target(art_dir: Path, key: str, kind: str) -> Path:
    """Where a new photo for `key` goes: over the one it has, else by kind ('portrait' to
    people/, 'scene' to places/, anything else to items/)."""
    if (found := find_picture(art_dir, key)) is not None:
        return found.parent / f"{key}.jpg"
    folder = {"portrait": "people", "scene": "places"}.get(kind, "items")
    return Path(art_dir) / folder / f"{key}.jpg"


def art_kind(m, key: str) -> str:
    """places, people or items: what the module uses the picture `key` for."""
    places = {loc.art or loc.id for loc in m.locations} | {loc.id for loc in m.locations}
    places |= {look.key(loc.art or loc.id) for loc in m.locations for look in loc.looks}
    people = {n.portrait or n.id for n in m.npcs} | {n.id for n in m.npcs} | {c.id for c in m.creatures}
    people |= {p.portrait for p in m.pregens if p.portrait}
    people |= {look.key(n.portrait or n.id) for n in m.npcs for look in n.looks}
    if key in places:
        return "places"
    if key in people:
        return "people"
    return "items"


# -- filing a draft ------------------------------------------------------------------------
def start_draft(folder: Path) -> None:
    """Before the importer's agent writes a module.yaml again: part files from an earlier
    import would be merged over its draft."""
    for name in PARTS:
        (Path(folder) / name).unlink(missing_ok=True)


def normalize(folder: Path) -> None:
    """File a module written in one module.yaml with a flat art/ folder into the layout
    above. The content stays the same; the prepared copies (.prep.png) and CREDITS.md go,
    since the cache and credits.yaml replace them."""
    from .schema import Module

    folder = Path(folder)
    art_dir = folder / "art"
    m = Module.load(folder)
    if not any((folder / name).exists() for name in PARTS):
        for name, text in split_text((folder / MAIN).read_text(encoding="utf-8")).items():
            (folder / name).write_text(text, encoding="utf-8")
    if m.art:
        (art_dir / "ascii").mkdir(parents=True, exist_ok=True)
        for p in sorted(art_dir.iterdir()):
            if p.is_file() and p.suffix in (".txt", ".color"):
                p.replace(art_dir / "ascii" / p.name)
    note = ""
    if art_dir.is_dir():
        for p in _photos(art_dir):
            target = art_dir / art_kind(m, p.stem) / p.name
            target.parent.mkdir(exist_ok=True)
            p.replace(target)
            record: dict[str, Any] = {}
            for suffix in (".credit.json", ".source.json"):
                sidecar = p.with_name(p.stem + suffix)
                if sidecar.is_file():
                    try:
                        record |= json.loads(sidecar.read_text(encoding="utf-8"))
                    except ValueError:
                        pass
                    sidecar.unlink()
            if record:
                credits.put(target, record)
        for p in sorted(art_dir.iterdir()):
            if p.is_file() and (p.name.endswith((".prep.png", ".credit.json", ".source.json")) or ".glitch." in p.name):
                p.unlink()
        if (old := art_dir / "CREDITS.md").is_file():
            note = _credits_note(old.read_text(encoding="utf-8"))
            old.unlink()
    if note:
        file = art_dir / credits.FILE
        data = credits.read_file(file)
        file.write_text(yaml.safe_dump({"note": note, **data}, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    if (notes := folder / "notes.md").is_file():
        (folder / "source").mkdir(exist_ok=True)
        notes.replace(folder / "source" / "notes.md")
    for name in DRAFT_LEFTOVERS:
        p = folder / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.is_file():
            p.unlink()


def _credits_note(markdown: str) -> str:
    """The paragraph of a CREDITS.md above its table (the licence note)."""
    lines = [line.strip() for line in markdown.splitlines()]
    text = [line for line in lines if line and not line.startswith(("#", "|"))]
    return " ".join(text)
