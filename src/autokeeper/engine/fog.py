"""What the investigators know of the map: where they have been, and what they have only heard of.

A place is heard of once its name comes up in something the players were shown: the
module's title, hook, setting and opening, what they saw on arriving somewhere, the
keeper's narration and the NPCs' lines, who the people they met turned out to be, the
clues and handouts they found, the new places the director announced. Their own words do
not count, or typing a guess would put a secret on the map.

A name counts in full or by its head (阿诺兹堡公共图书馆 as 图书馆, 食尸鬼隧道网 as 隧道网),
never by a word so plain it could mean anything (大街, 中心), nor by one that is part of
another place's name. jieba finds where the words of a name begin.

The log is folded into a summary as the game goes on, so the engine keeps what was heard
in GameState.heard (place id -> the name it went by): nothing heard of is forgotten, and
the players' screens at a shared table, which never get the log, know it too.
"""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Iterable
from functools import lru_cache

from ..module.schema import Module
from .state import GameState

with warnings.catch_warnings():  # jieba 0.42 predates Python 3.12's stricter string escapes
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

jieba.setLogLevel(logging.WARNING)  # it reports loading its dictionary on stderr, under the TUI

SHOWN = ("opening", "keeper", "npc", "event", "clue", "handout", "ending")  # log kinds the players read
# Heads too plain to stand for one place: the keeper says them about anywhere.
GENERIC = frozenset(
    "大街 街道 街上 小路 道路 马路 路口 路上 中心 地方 房间 屋子 房子 小屋 屋里 门口 门外 外面 里面 附近 周围 一带 "
    "楼上 楼下 一楼 二楼 三楼 楼梯 地下 地上 深处 角落 旁边 对面 后面 前面 入口 出口 大门 号房 镇上 城里 家里 村里 "
    "远处 这里 那里".split()
)
_MARKS = str.maketrans("", "", "《》「」『』“”‘’\"' \u3000\t\r\n")
_JOINS = frozenset("的之·・")  # a head starts after these, never with them


def plain(text: str) -> str:
    """Text as names are matched: no book-title marks, quotes or spaces."""
    return text.translate(_MARKS)


@lru_cache(maxsize=16)
def _aliases(names: tuple[tuple[str, str], ...]) -> dict[str, tuple[str, ...]]:
    flat = {pid: plain(name) for pid, name in names}
    out: dict[str, tuple[str, ...]] = {}
    for pid, name in names:
        words = [w for w in jieba.lcut(name) if plain(w)]
        heads = {plain("".join(words[i:])) for i in range(1, len(words)) if words[i] not in _JOINS}
        heads = {
            h for h in heads
            if len(h) >= 2 and h not in GENERIC and not any(h in other for oid, other in flat.items() if oid != pid)
        }  # fmt: skip
        out[pid] = tuple(sorted({flat[pid], *heads}, key=len, reverse=True))
    return out


def aliases(m: Module) -> dict[str, tuple[str, ...]]:
    """Every place's names as they can come up in text, longest first: the full name, then its
    heads that name nothing else in the module."""
    return _aliases(tuple((loc.id, loc.name) for loc in m.locations))


@lru_cache(maxsize=16)
def _pattern(names: tuple[str, ...]) -> re.Pattern[str]:
    # Longest first: where 圣玛丽教堂的钟楼 is named, 钟楼 is not named separately.
    return re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))


def mentions(m: Module, texts: Iterable[str]) -> dict[str, str]:
    """The places these texts name: id -> the name used (the longest, if it came up several ways)."""
    owners: dict[str, list[str]] = {}
    for pid, names in aliases(m).items():
        for n in names:
            owners.setdefault(n, []).append(pid)
    if not owners:
        return {}
    full = {plain(loc.name): loc.name for loc in m.locations}
    pattern = _pattern(tuple(owners))
    found: dict[str, str] = {}
    for text in texts:
        for hit in pattern.finditer(plain(text)):
            said = full.get(hit.group(), hit.group())
            for pid in owners[hit.group()]:
                if len(plain(said)) > len(plain(found.get(pid, ""))):
                    found[pid] = said
    return found


def shown(m: Module, st: GameState) -> list[str]:
    """Everything the players have been shown that can name a place."""
    places = {loc.id: loc for loc in m.locations}
    clues, handouts = {c.id: c for c in m.clues}, {h.id: h for h in m.handouts}
    texts = [m.title, m.subtitle, m.hook, m.setting, m.opening]
    texts += [places[v].read_aloud for v in st.visited if v in places]  # what they saw on arriving
    texts += [e.text for e in st.log if e.kind in SHOWN]
    texts += [f"{clues[c].title}\n{clues[c].text}" for c in st.clues if c in clues]
    texts += [f"{handouts[h].title}\n{handouts[h].text}" for h in st.handouts if h in handouts]
    texts += [n.role for n in m.npcs if n.id in st.npcs and st.npcs[n.id].met]  # shown beside their first line
    # The director's new places are announced by name when they open a new line (tui: "improv").
    texts += [loc.get("name", "") for p in st.improv if p.get("kind") == "展开" for loc in p.get("plan", {}).get("locations", [])]
    return texts


def heard(m: Module, st: GameState) -> dict[str, str]:
    """Places the investigators have heard of, visited or not (id -> the name they heard it by):
    what the engine kept in st.heard, and anything the players were shown since."""
    ids = {loc.id for loc in m.locations}
    out = {pid: name for pid, name in st.heard.items() if pid in ids}
    for pid, name in mentions(m, shown(m, st)).items():
        if len(plain(name)) > len(plain(out.get(pid, ""))):
            out[pid] = name
    return out
