"""Edit a module's YAML as text: a field into one entry (or one agenda step), items onto a list
(or onto one entry's list), a key into a map; and taking back one of them (an entry, a step, a
field, a field's whole value, items of an entry's list, a key).

A hand-written module keeps its layout and comments; a new field goes in as new lines, in that
file's own indentation, so the diff is the new lines alone (module/adapt.py writes through this).
"""

from __future__ import annotations

import re
from collections.abc import Callable
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


def _top(lines: list[str], group: str) -> tuple[int, int]:
    """The lines of the top-level `group:` (from its key to the next top-level key)."""
    top = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(group)}:\s*$", line)), None)
    if top is None:
        raise KeyError(f"没有 {group}")
    return top, next((i for i in range(top + 1, len(lines)) if re.match(r"[^\s-]", lines[i])), len(lines))


def _entries(lines: list[str], top: int, bottom: int) -> list[int]:
    """Where each entry of the list under lines[top] starts."""
    first = next((i for i in range(top + 1, bottom) if lines[i].lstrip().startswith("- ")), None)
    if first is None:
        return []
    dash = len(lines[first]) - len(lines[first].lstrip(" "))
    return [i for i in range(first, bottom) if lines[i].startswith(" " * dash + "- ") and len(lines[i]) - len(lines[i].lstrip(" ")) == dash]


def drop(text: str, group: str, index: int) -> str:
    """`text` without the `index`-th entry of the top-level list `group:`."""
    lines = text.split("\n")
    top, bottom = _top(lines, group)
    starts = _entries(lines, top, bottom)
    if not 0 <= index < len(starts):
        raise KeyError(f"{group} 没有第 {index + 1} 条")
    start = starts[index]
    end = _entry_end(lines, start, bottom)
    out = [*lines[:start], *lines[end:]]
    if not _entries(out, top, bottom - (end - start)):  # the list is empty now
        out[top] = f"{group}: []"
    return "\n".join(out)


def drop_entry(text: str, group: str, ident: str) -> str:
    """`text` without the entry `- id: <ident>` of the top-level list `group:`."""
    lines = text.split("\n")
    top, bottom = _top(lines, group)
    starts = _entries(lines, top, bottom)
    index = next((k for k, i in enumerate(starts) if re.match(rf"\s*- id: ['\"]?{re.escape(ident)}['\"]?\s*$", lines[i])), None)
    if index is None:
        raise KeyError(f"{group} 里没有 {ident}")
    return drop(text, group, index)


def drop_step(text: str, group: str, owner: str, ident: str) -> str:
    """`text` without the step `- id: <ident>` of the entry `- npc: <owner>` (an agenda's steps)."""
    lines = text.split("\n")
    top, bottom = _top(lines, group)
    entry = next((i for i in range(top + 1, bottom) if re.match(rf"\s*- npc: ['\"]?{re.escape(owner)}['\"]?\s*$", lines[i])), None)
    if entry is None:
        raise KeyError(f"{group} 里没有 {owner}")
    stop = _entry_end(lines, entry, bottom)
    start = next((i for i in range(entry + 1, stop) if re.match(rf"\s*- id: ['\"]?{re.escape(ident)}['\"]?\s*$", lines[i])), None)
    if start is None:
        raise KeyError(f"{owner} 的打算里没有 {ident}")
    return "\n".join([*lines[:start], *lines[_entry_end(lines, start, stop):]])


def _field(lines: list[str], group: str, ident: str, key: str) -> tuple[int, int | None, int]:
    """The field `key:` of the entry `- id: <ident>` under `group:`: the entry's own indentation,
    and the lines the field takes (from, to); from is None when the entry has no such field."""
    top, bottom = _top(lines, group)
    start = next((i for i in range(top + 1, bottom) if re.match(rf"(\s*)- id: ['\"]?{re.escape(ident)}['\"]?\s*$", lines[i])), None)
    if start is None:
        raise KeyError(f"{group} 里没有 {ident}")
    end = _entry_end(lines, start, bottom)
    inner = len(lines[start]) - len(lines[start].lstrip(" ")) + 2
    own = next((i for i in range(start + 1, end) if re.match(rf" {{{inner}}}{re.escape(key)}:", lines[i])), None)
    if own is None:
        return inner, None, 0
    stop = own + 1
    while stop < end and (not lines[stop].strip() or len(lines[stop]) - len(lines[stop].lstrip(" ")) > inner
                          or lines[stop].startswith(" " * inner + "- ")):
        stop += 1
    return inner, own, stop


def replace(text: str, group: str, ident: str, key: str, value: Any) -> str:
    """`text` with the whole of the field `key:` of the entry `- id: <ident>` under `group:`
    (however many lines it has) given as `value`."""
    lines = text.split("\n")
    inner, own, stop = _field(lines, group, ident, key)
    if own is None:
        return put(text, group, ident, key, value)
    return "\n".join([*lines[:own], *_block({key: value}, inner), *lines[stop:]])


def drop_field(text: str, group: str, ident: str, key: str) -> str:
    """`text` without the field `key:` of the entry `- id: <ident>` under `group:`, however many
    lines it has (unchanged when the entry has none)."""
    lines = text.split("\n")
    _, own, stop = _field(lines, group, ident, key)
    return text if own is None else "\n".join([*lines[:own], *lines[stop:]])


def drop_items(text: str, group: str, ident: str, key: str, gone: Callable[[Any], bool]) -> str:
    """`text` without the items `gone` picks of the list field `key:` of the entry `- id: <ident>` under
    `group:` (the looks on a flag): their own lines and no others; the field goes once nothing is left."""
    lines = text.split("\n")
    _, own, stop = _field(lines, group, ident, key)
    if own is None:
        return text
    while stop > own + 1 and not lines[stop - 1].strip():
        stop -= 1
    first = next((i for i in range(own + 1, stop) if lines[i].lstrip().startswith("- ")), None)
    if first is None:  # written on one line: written again whole
        entries = (yaml.safe_load(text) or {}).get(group) or []
        entry = next(x for x in entries if isinstance(x, dict) and x.get("id") == ident)
        kept = [x for x in entry.get(key) or [] if not gone(x)]
        return replace(text, group, ident, key, kept) if kept else drop_field(text, group, ident, key)
    dash = len(lines[first]) - len(lines[first].lstrip(" "))
    starts = [i for i in range(first, stop) if lines[i].startswith(" " * dash + "- ")]
    spans = list(zip(starts, [*starts[1:], stop], strict=True))
    out = [(a, b) for a, b in spans if gone((yaml.safe_load("\n".join(line[dash:] for line in lines[a:b])) or [None])[0])]
    if len(out) == len(spans):
        return drop_field(text, group, ident, key)
    dropped = {i for a, b in out for i in range(a, b)}
    return "\n".join(line for i, line in enumerate(lines) if i not in dropped)


def extend(text: str, group: str, ident: str, key: str, items: list[Any]) -> str:
    """`text` with `items` added at the end of the list field `key:` of the entry `- id: <ident>` under
    `group:` (a place's looks), in that list's own indentation; the field put in when the entry has
    none, written whole again when it is written on one line (`looks: []`)."""
    lines = text.split("\n")
    _, own, stop = _field(lines, group, ident, key)
    if own is None:
        return put(text, group, ident, key, items)
    first = next((line for line in lines[own + 1 : stop] if line.lstrip().startswith("- ")), None)
    if first is None:
        entries = (yaml.safe_load(text) or {}).get(group) or []
        entry = next(x for x in entries if isinstance(x, dict) and x.get("id") == ident)
        return replace(text, group, ident, key, [*(entry.get(key) or []), *items])
    while stop > own + 1 and not lines[stop - 1].strip():  # the blank lines after it stay after it
        stop -= 1
    dash = len(first) - len(first.lstrip(" "))
    return "\n".join([*lines[:stop], *_block({key: items}, dash)[1:], *lines[stop:]])  # the items, not the key


def drop_key(text: str, group: str, key: str) -> str:
    """`text` without the key `key:` of the top-level mapping `group:` (a flag), however many lines it has."""
    lines = text.split("\n")
    top = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(group)}:\s*$", line)), None)
    if top is None:
        raise KeyError(f"没有 {group}")
    bottom = next((i for i in range(top + 1, len(lines)) if lines[i].strip() and not lines[i].startswith(" ")), len(lines))
    start = next((i for i in range(top + 1, bottom) if re.match(rf"\s+['\"]?{re.escape(key)}['\"]?:", lines[i])), None)
    if start is None:
        raise KeyError(f"{group} 里没有 {key}")
    inner = len(lines[start]) - len(lines[start].lstrip(" "))
    end = start + 1
    while end < bottom and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip(" ")) > inner):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    out = [*lines[:start], *lines[end:]]
    if not any(line.strip() and not line.lstrip().startswith("#") for line in out[top + 1 : bottom - (end - start)]):
        out[top] = f"{group}: {{}}"  # nothing left in it
    return "\n".join(out)


def where(folder: Path, group: str) -> Path:
    """The file of a module's package that holds a top-level list."""
    for name, keys in package.PARTS.items():
        if group in keys and (folder / name).is_file():
            return folder / name
    return folder / package.MAIN
