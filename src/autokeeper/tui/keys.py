"""Alt+空格 as a key of its own. Terminals send Alt+<key> as Esc then the key; Textual names that
alt+<key> only for one-letter names, so Alt+空格 came in as a plain space. The input box's mode
switch is on it (the user, 2026-10-21), so the parser is taught this one."""

from __future__ import annotations

from textual import events
from textual._xterm_parser import XTermParser

_plain = XTermParser._sequence_to_key_events


def _with_alt_space(self, sequence: str, alt: bool = False):
    if alt and sequence == " ":
        yield events.Key("alt+space", None)
        return
    yield from _plain(self, sequence, alt)


def install() -> None:
    XTermParser._sequence_to_key_events = _with_alt_space  # type: ignore[method-assign]
