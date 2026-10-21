"""Foreshadowing as the keeper is handed it (Module.foreshadow, Game._foreshadow).

A sign's first hint comes the first time the party arrives where it shows. Its stages
(Foreshadow.stages) come after, one on each later arrival where the next one shows, or on
waking there after a rest, once its time or flag has come: the painting in the inn looks a
little different each time they pass it. Only the hint goes to the keeper, never the clue it
points to. GameState.hinted keeps which signs gave their first hint (as saves always have);
GameState.staged how many of their stages followed.
"""

from __future__ import annotations

import re

from ..module.schema import Foreshadow, Module, Stage
from .state import GameState, parse_time

TIMED = re.compile(r"D\d+ \d{1,2}:\d{2}")


def step(st: GameState, f: Foreshadow) -> int:
    """Which of its signs comes next: 0 its first hint, n its n-th stage."""
    return 0 if f.id not in st.hinted else 1 + st.staged.get(f.id, 0)


def sign(f: Foreshadow, n: int) -> Stage | None:
    """Its n-th sign (0: the first hint) with the place it shows at; None past the last."""
    if n == 0:
        return Stage(hint=f.hint, where=f.where)
    if n > len(f.stages):
        return None
    s = f.stages[n - 1]
    return s if s.where else s.model_copy(update={"where": f.where})


def ready(st: GameState, s: Stage) -> bool:
    """Its time has come, or its flag is set; one with neither comes on the next visit."""
    when = s.when.strip()
    if not when:
        return True
    if TIMED.fullmatch(when):
        return st.minutes >= parse_time(when)
    return when.startswith("flag:") and when[5:] in st.flags


def at(m: Module, st: GameState, place: str) -> list[str]:
    """The signs whose next one shows at `place`, ready or not yet."""
    return [f.id for f in m.foreshadow if (s := sign(f, step(st, f))) is not None and s.where == place]


def hand(m: Module, st: GameState, ids: list[str]) -> list[tuple[str, Stage]]:
    """The signs of `ids` that come now, where the party is, each moved on by one: the keeper's
    note for each, and its sign (whose flag the game sets)."""
    out = []
    for fid in dict.fromkeys(ids):
        f = next((x for x in m.foreshadow if x.id == fid), None)
        if f is None:
            continue
        n = step(st, f)
        s = sign(f, n)
        if s is None or s.where != st.location or not ready(st, s):
            continue
        if n == 0:
            st.hinted.append(f.id)
            out.append((f"这里可以顺手露出一点端倪，一笔带过，不点破、不解释：{s.hint}", s))
        else:
            st.staged[f.id] = n
            before = sign(f, n - 1)
            out.append((f"先前露出过的端倪起了变化，可以顺手带出，一笔带过，不点破、不解释。先前：{before.hint if before else f.hint}；"
                        f"这次：{s.hint}", s))
    return out
