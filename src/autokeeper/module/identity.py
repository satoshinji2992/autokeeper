"""Exact identity labels shared by compiled data, improvisation and scene references.

Only typography is normalized. Prefixes, nicknames and two similar names never establish
that two people are the same person.
"""

from __future__ import annotations

import unicodedata


def identity(name: str) -> str:
    return "".join(unicodedata.normalize("NFKC", name).split()).translate(str.maketrans("", "", "·・.．•")).casefold()
