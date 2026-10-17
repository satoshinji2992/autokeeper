"""Leak guard for NPC replies.

An NPC must not say what it does not know or has not been talked into revealing.
We compare its reply with every protected text it is not allowed to use; sharing
two or more 5-character runs with one of them counts as a leak.
"""

from __future__ import annotations

import re

_STRIP = re.compile(r"[\s，。、；：？！“”‘’「」『』（）()《》〈〉…—\-,.;:?!\"'`]+")


def grams(text: str, n: int = 5) -> set[str]:
    s = _STRIP.sub("", text)
    return {s[i : i + n] for i in range(max(0, len(s) - n + 1))}


def leaks(reply: str, protected: dict[str, str], *, n: int = 5, hits: int = 2) -> list[str]:
    said = grams(reply, n)
    return [key for key, text in protected.items() if len(said & grams(text, n)) >= hits]
