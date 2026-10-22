"""What a line typed at the table is, before anyone reads it: out-of-character chat, words an
investigator says to the party, or an action for the keeper (anything else).

The rule is fixed and needs no model:

- Out of character: a line starting with `//`, or wholly wrapped in double brackets, `((…))` or
  `（（…））`. Shown to the table, never recorded, never read by the keeper.
- Said to the party: a line starting with a quotation mark (「 “ " 『), with a stage direction
  （…） first or not; or one starting with 「对<调查员名>说」 or 「对大家说」. It is passed on and
  recorded (Game.speak); the keeper is not asked and no action is spent.
- Everything else is an action, a single leading （…） included: 「（压低声音）别进那扇门。」 is the
  investigator acting, for the keeper, as it always was.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

OOC = "//"
QUOTES = ("「", "“", '"', "『")
EVERYONE = "大家"
_WRAPPED = re.compile(r"[（(]{2}(.*?)[）)]{2}", re.S)
_STAGE = re.compile(r"[（(][^（()）]*[）)]\s*")
_TO = re.compile(r"对(.{1,16}?)说[：:，,]?\s*")


@dataclass(frozen=True)
class Said:
    line: str  # as typed, its stage direction and quotation marks kept
    to: str = ""  # a teammate's full name, 大家, or "" (whoever of the party is there)


def ooc(text: str) -> str | None:
    """The chat in an out-of-character line (it may be empty), or None for any other line."""
    s = text.strip()
    if s.startswith(OOC):
        return s[len(OOC):].strip()
    if found := _WRAPPED.fullmatch(s):
        return found.group(1).strip()
    return None


def _named(word: str, names: Iterable[str]) -> str:
    """The teammate `word` names: the full name, or one part of a name written with 「·」."""
    return next((n for n in names if word == n or word in n.split("·")), "")


def said(text: str, names: Iterable[str]) -> Said | None:
    """The words, if the line is said to the party; `names`, the teammates who could hear it.
    None for out-of-character chat and for anything that is an action."""
    s = text.strip()
    names = [n for n in names if n]
    if not names or ooc(s) is not None:
        return None
    if found := _TO.match(s):
        to = EVERYONE if found.group(1) == EVERYONE else _named(found.group(1), names)
        rest = s[found.end():].strip()
        return Said(rest, to) if to and rest else None
    body = s[found.end():] if (found := _STAGE.match(s)) else s
    return Said(s) if body.startswith(QUOTES) and len(body) > 1 else None
