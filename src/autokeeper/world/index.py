"""Retrieval over the record: structured queries, the relation graph, and fuzzy recall.

Structured queries read the record directly. The relation graph is a networkx MultiDiGraph
built from relations and the events that joined people (an event is a hyperedge: every pair of
its participants meets there). Fuzzy recall embeds event and fact texts with the same local
model the module knowledge base uses (fastembed bge-small-zh, knowledge.embed), no LLM. The
vectors are cached by text and rebuilt from the record, so they are never saved and a save
never sees another's. Every query is scoped to a viewer first: an NPC recalls only what it
knows or saw.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import networkx as nx

from . import knowledge
from .record import DIRECTOR, Event, Fact, Promise, Relation

with warnings.catch_warnings():  # jieba 0.42 predates Python 3.12's stricter string escapes
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

jieba.setLogLevel(logging.WARNING)

if TYPE_CHECKING:
    import numpy as np

    from .store import World

Embed = Callable[[list[str]], Awaitable["np.ndarray"]]


# -- structured -----------------------------------------------------------------------------------
def knows(w: World, npc: str) -> list[Fact]:
    return knowledge.known(w.rec, npc)


def happened_at(w: World, place: str, since: int = 0, *, viewer: str = DIRECTOR) -> list[Event]:
    return [e for e in w.rec.events if e.place == place and e.t >= since and knowledge.saw(e, viewer)]


def promises(w: World, involving: str = "", status: str = "active") -> list[Promise]:
    return [p for p in w.rec.promises.values() if (not involving or involving in (p.by, p.to)) and (not status or p.status == status)]


def relations(w: World, entity: str, *, viewer: str = DIRECTOR) -> list[Relation]:
    return [r for r in w.rec.relations.values() if entity in (r.a, r.b) and (viewer == DIRECTOR or viewer in r.known_by)]


def graph(w: World, *, viewer: str = DIRECTOR) -> nx.MultiDiGraph:
    """People, places and things, joined by relations (a -> b) and by the events they shared."""
    g = nx.MultiDiGraph()
    for e in w.rec.entities.values():
        g.add_node(e.id, kind=e.kind, name=e.name)
    for r in w.rec.relations.values():
        if viewer == DIRECTOR or viewer in r.known_by:
            g.add_edge(r.a, r.b, key=f"rel:{r.kind}", kind=r.kind, value=r.value, note=r.note)
    for ev in w.rec.events:
        if not knowledge.saw(ev, viewer):
            continue
        people = [p for p in ev.participants if p in g]
        for i, a in enumerate(people):
            for b in people[i + 1 :]:
                g.add_edge(a, b, key=f"ev:{ev.id}", kind="event", event=ev.id, text=ev.text)
    return g


def neighbours(w: World, entity: str, depth: int = 1, *, viewer: str = DIRECTOR) -> dict[str, int]:
    """Entities within `depth` steps of this one, either way along an edge: id -> distance."""
    g = graph(w, viewer=viewer).to_undirected(as_view=True)
    if entity not in g:
        return {}
    near = nx.single_source_shortest_path_length(g, entity, cutoff=depth)
    return {k: v for k, v in near.items() if k != entity}


# -- fuzzy recall -----------------------------------------------------------------------------------
@dataclass
class Hit:
    ref: str  # event:<id> or fact:<id>
    text: str
    score: float


def _items(w: World, viewer: str) -> list[tuple[str, str]]:
    out = [(f"fact:{f.id}", f.text) for f in w.rec.facts.values() if f.text and knowledge.sees(f, viewer)]
    out += [(f"event:{e.id}", e.text) for e in w.rec.events if knowledge.saw(e, viewer)]
    return out


def _words(text: str) -> set[str]:
    return {t.strip() for t in jieba.lcut(text) if len(t.strip()) > 1}


class Recall:
    """Fuzzy recall for one save. Without an embedder it ranks by shared words (jieba), which
    needs nothing installed; with one, by cosine similarity of the embeddings."""

    def __init__(self, embed: Embed | None = None):
        self.embed = embed
        self.vectors: dict[str, np.ndarray] = {}  # text -> vector

    @classmethod
    def local(cls) -> Recall:
        """The module knowledge base's local embedder, when fastembed is installed."""
        try:
            import fastembed  # noqa: F401
        except ImportError:
            return cls()
        from .. import knowledge as kb

        return cls(kb.embed)

    async def search(self, w: World, query: str, *, viewer: str = DIRECTOR, k: int = 5) -> list[Hit]:
        items = _items(w, viewer)
        if not items or not query.strip():
            return []
        if self.embed is None:
            want = _words(query)
            scored = [(len(want & _words(t)) / (len(want) or 1), ref, t) for ref, t in items]
        else:
            import numpy as np

            missing = list(dict.fromkeys(t for _, t in items if t not in self.vectors))
            if missing:
                for t, v in zip(missing, await self.embed(missing), strict=True):
                    self.vectors[t] = np.asarray(v, dtype=float)
            q = np.asarray((await self.embed([query]))[0], dtype=float)
            qn = np.linalg.norm(q) or 1.0
            scored = [(float(self.vectors[t] @ q / ((np.linalg.norm(self.vectors[t]) or 1.0) * qn)), ref, t) for ref, t in items]
        scored.sort(key=lambda s: -s[0])
        return [Hit(ref, t, round(s, 3)) for s, ref, t in scored[:k] if s > 0]
