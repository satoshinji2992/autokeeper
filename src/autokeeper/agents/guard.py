"""Leak guard for NPC replies.

An NPC must not say what it does not know or has not been talked into revealing.
We compare its reply with every protected text it is not allowed to use; sharing
two or more 5-character runs with one of them counts as a leak. Runs that also occur
in what the speaker may say anyway (its own sheet, the places everyone knows) do not
count: a street name shared with a secret is not a secret.

It also notices an NPC falling into its habits: a module sheet says what a character
keeps doing and saying (托马斯 "说话时爱搓手", "爱说'说实在的'"), and a model shown that
does it in every line, each one opening with a bracketed action (docs/eval.md).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..module.schema import NPC

_STRIP = re.compile(r"[\s，。、；：？！“”‘’「」『』（）()《》〈〉…—\-,.;:?!\"'`]+")


def grams(text: str, n: int = 5) -> set[str]:
    s = _STRIP.sub("", text)
    return {s[i : i + n] for i in range(max(0, len(s) - n + 1))}


def leaks(reply: str, protected: dict[str, str], *, n: int = 5, hits: int = 2, public: str = "") -> list[str]:
    said = grams(reply, n) - grams(public, n)
    return [key for key, text in protected.items() if len(said & grams(text, n)) >= hits]


_SENTENCE = re.compile(r"(?<=[。！？!?…])")


def redact(reply: str, protected: dict[str, str], *, n: int = 5, public: str = "") -> str:
    """The reply without any sentence that shares a run with a protected text: the last
    resort when asking again still leaks. Empty if nothing safe is left."""
    kept = [s for s in _SENTENCE.split(reply) if s.strip() and not leaks(s, protected, n=n, hits=1, public=public)]
    return "".join(kept).strip()


# -- habits --------------------------------------------------------------------------------
_BEAT = re.compile(r"[（(]([^（）()]{1,60})[）)]")
_LITTLE = str.maketrans("", "", "着了过的地得一又再把将被往向在朝他她你我它们您这那个")
Habits = tuple[frozenset[str], tuple[str, ...]]


def opens_with_action(line: str) -> bool:
    return line.lstrip().startswith(("（", "("))


def pairs(text: str) -> set[str]:
    """Two-character runs within each clause, little words left out: 又搓了搓手 -> 搓搓, 搓手."""
    out: set[str] = set()
    for clause in _STRIP.split(text):
        c = clause.translate(_LITTLE)
        out |= {c[i : i + 2] for i in range(len(c) - 1)}
    return out


def habits(npc: NPC) -> Habits:
    """What the module has an NPC keep doing and saying: the two-character runs of its look and
    voice (搓手, 铁铲, 藤篮), and its catchphrases, the quoted words."""
    told = f"{npc.look}\n{npc.voice}"
    phrases = tuple(p for p in re.findall(r"[“「]([^”」]{2,16})[”」]", told) if len(_STRIP.sub("", p)) >= 2)
    return frozenset(pairs(re.sub(r"[“「][^”」]*[”」]", "", told))), phrases


def repeats(line: str, before: Iterable[str], habit: Habits = (frozenset(), ())) -> list[str]:
    """The NPC's habits a line repeats from the lines before it: one of its actions done again (a
    run of its look or voice in this line's bracketed actions and in one of theirs: 搓着手, 又搓了
    搓手), or a catchphrase said again."""
    before = list(before)
    runs, phrases = habit

    def acts(texts: list[str]) -> set[str]:
        return {p for t in texts for b in _BEAT.findall(t) for p in pairs(b)}

    flat = [_STRIP.sub("", t) for t in before]
    said = [p for p in phrases if (q := _STRIP.sub("", p)) in _STRIP.sub("", line) and any(q in t for t in flat)]
    return sorted(acts([line]) & acts(before) & runs) + said


def slips(line: str, before: list[str], npc: NPC) -> list[str]:
    """What a later line does that a first meeting may: open with an action, or repeat a habit
    from the NPC's two lines before. In words for asking again; empty when the line is fine."""
    out = ["开头又先写了动作"] if opens_with_action(line) else []
    if again := repeats(line, before[-2:], habits(npc)):
        out.append(f"又用了刚用过的“{again[0]}”")
    return out
