"""Leak guard for NPC replies.

An NPC must not say what it does not know or has not been talked into revealing.
We compare its reply with every protected text it is not allowed to use; sharing
two or more 5-character runs with one of them counts as a leak. Runs that also occur
in what the speaker may say anyway (its own sheet, the places everyone knows) do not
count: a street name shared with a secret is not a secret.
"""

from __future__ import annotations

import re

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
