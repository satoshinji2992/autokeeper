"""Edit a module's YAML as text: a field into one entry (or one agenda step), items onto a list,
a key into a map.

A hand-written module keeps its layout and comments; a new field goes in as new lines, in that
file's own indentation, so the diff is the new lines alone (module/adapt.py writes through this).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import package


# -- the YAML, as text ---------------------------------------------------------------------
def _block(value: dict[str, Any], indent: int) -> list[str]:
    text = yaml.dump(value, Dumper=package._Dumper, allow_unicode=True, sort_keys=False, width=100)
    return [(" " * indent + line) if line else line for line in text.rstrip("\n").split("\n")]


def put(text: str, group: str, ident: str, key: str, value: Any) -> str:
    """`text` with `key: value` added to the entry `- id: <ident>` under the top-level `group:`,
    or put in place of that entry's own `key:` line when it is a one-line field."""
    lines = text.split("\n")
    top = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(group)}:\s*$", line)), None)
    if top is None:
        raise KeyError(f"没有 {group}")
    start = next((i for i in range(top + 1, len(lines)) if re.match(rf"(\s*)- id: ['\"]?{re.escape(ident)}['\"]?\s*$", lines[i])), None)
    if start is None or any(re.match(r"[^\s-]", line) for line in lines[top + 1 : start]):  # another top-level key came first
        raise KeyError(f"{group} 里没有 {ident}")
    dash = len(lines[start]) - len(lines[start].lstrip(" "))
    end = start + 1
    while end < len(lines) and not re.match(rf"([^\s-]| {{0,{dash}}}- |-)", lines[end]):  # the next entry, or the next top-level key
        end += 1
    while end > start + 1 and not lines[end - 1].strip():  # the blank lines between entries stay after it
        end -= 1
    inner = dash + 2
    new = _block({key: value}, inner)
    own = next((i for i in range(start, end) if re.match(rf"\s{{{inner}}}{re.escape(key)}:", lines[i]) or (i == start and lines[i][dash + 2 :].startswith(f"{key}:"))), None)
    if own is not None:
        if lines[own].rstrip().endswith(":") or (own + 1 < end and len(lines[own + 1]) - len(lines[own + 1].lstrip(" ")) > inner):
            raise ValueError(f"{ident} 的 {key} 已经有内容")
        return "\n".join([*lines[:own], *new, *lines[own + 1 :]])
    return "\n".join([*lines[:end], *new, *lines[end:]])


def _entry_end(lines: list[str], start: int, stop: int) -> int:
    """Where the list entry starting at `start` ends: the next entry at its level, or `stop`;
    the blank lines between entries stay after it."""
    dash = len(lines[start]) - len(lines[start].lstrip(" "))
    end = start + 1
    while end < stop and not re.match(rf"([^\s-]| {{0,{dash}}}- |-)", lines[end]):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def put_step(text: str, group: str, owner: str, ident: str, key: str, value: Any) -> str:
    """`text` with `key: value` added to the step `- id: <ident>` of the entry `- npc: <owner>`
    under the top-level `group:` (an agenda's steps), or put in place of that step's own one-line
    `key:`."""
    lines = text.split("\n")
    top = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(group)}:\s*$", line)), None)
    if top is None:
        raise KeyError(f"没有 {group}")
    bottom = next((i for i in range(top + 1, len(lines)) if re.match(r"[^\s-]", lines[i])), len(lines))
    entry = next((i for i in range(top + 1, bottom) if re.match(rf"\s*- npc: ['\"]?{re.escape(owner)}['\"]?\s*$", lines[i])), None)
    if entry is None:
        raise KeyError(f"{group} 里没有 {owner}")
    stop = _entry_end(lines, entry, bottom)
    start = next((i for i in range(entry + 1, stop) if re.match(rf"\s*- id: ['\"]?{re.escape(ident)}['\"]?\s*$", lines[i])), None)
    if start is None:
        raise KeyError(f"{owner} 的打算里没有 {ident}")
    end = _entry_end(lines, start, stop)
    inner = len(lines[start]) - len(lines[start].lstrip(" ")) + 2
    new = _block({key: value}, inner)
    own = next((i for i in range(start + 1, end) if re.match(rf" {{{inner}}}{re.escape(key)}:", lines[i])), None)
    if own is not None:
        return "\n".join([*lines[:own], *new, *lines[own + 1 :]])
    return "\n".join([*lines[:end], *new, *lines[end:]])


def append(text: str, group: str, items: list[Any]) -> str:
    """`text` with `items` added at the end of the top-level list `group:` (in that list's own
    indentation), or the list started at the end of the file."""
    lines = text.rstrip("\n").split("\n") if text.strip() else []
    top = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(group)}:", line)), None)
    if top is None or re.match(rf"{re.escape(group)}:\s*\[\]\s*$", lines[top]):
        if top is not None:
            del lines[top]
        return "\n".join([*lines, *_block({group: items}, 0)]) + "\n"
    end = top + 1
    while end < len(lines) and not re.match(r"[^\s-]", lines[end]):
        end += 1
    while end > top + 1 and not lines[end - 1].strip():
        end -= 1
    first = next((line for line in lines[top + 1 : end] if line.lstrip().startswith("- ")), "- ")
    dash = len(first) - len(first.lstrip(" "))
    new = _block({group: items}, dash)[1:]  # the items in the list's own indentation, not the key
    return "\n".join([*lines[:end], *new, *lines[end:]]) + "\n"


def put_key(text: str, group: str, key: str, value: Any) -> str:
    """`text` with `key: value` added to the top-level mapping `group:` (flags), unless it has the key."""
    lines = text.rstrip("\n").split("\n")
    top = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(group)}:", line)), None)
    if top is None or re.match(rf"{re.escape(group)}:\s*\{{\}}\s*$", lines[top]):
        if top is not None:
            del lines[top]
        return "\n".join([*lines, *_block({group: {key: value}}, 0)]) + "\n"
    end = top + 1
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith(" ")):
        if re.match(rf"\s+{re.escape(key)}:", lines[end]):
            return text
        end += 1
    while end > top + 1 and not lines[end - 1].strip():
        end -= 1
    inner = next((len(x) - len(x.lstrip(" ")) for x in lines[top + 1 : end] if x.strip()), 2)
    return "\n".join([*lines[:end], *_block({key: value}, inner), *lines[end:]]) + "\n"


def where(folder: Path, group: str) -> Path:
    """The file of a module's package that holds a top-level list."""
    for name, keys in package.PARTS.items():
        if group in keys and (folder / name).is_file():
            return folder / name
    return folder / package.MAIN
