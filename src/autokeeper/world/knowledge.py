"""Who knows what: the module seeded as facts, what reached the table, and who may see a fact.

A module fact's id is where it lives in the module (truth, npc:warden:secrets:0,
clue:c_diary, conclusion:k_tower, location:tower:keeper, event:e_sermon), so a rewrite can
be written back into this game's copy of the module (project) and everything that still
reads the module (NPC prompts, the leak guard, the arbiter) sees the same truth.

A fact is revealed once it reached the table: a clue found, or its words in what the players
were shown (the same 5-character runs the leak guard uses). Revealed is kept for good, as the
log is folded away later, and a revealed fact is frozen.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..agents.guard import grams
from ..engine import fog
from ..engine.state import GameState
from ..module.schema import Module
from .record import DIRECTOR, PLAYERS, Entity, Event, Fact, WorldRecord

PROJECTED_KNOWS = 8  # facts an NPC learned in this game that go into its sheet's 知道的事


def improvised(st: GameState) -> set[str]:
    """Ids the director's plans brought into this game's module."""
    groups = ("npcs", "locations", "clues", "items", "events", "endings")
    return {x.get("id", "") for p in st.improv for g in groups for x in p.get("plan", {}).get(g, [])}


def seed(m: Module, st: GameState, rec: WorldRecord) -> int:
    """Bring module objects not yet in the record into it; safe to call again after an improvisation."""
    done, added = set(rec.seeded), 0
    new = improvised(st)

    def origin(ident: str) -> str:
        return "improv" if ident in new else "module"

    def put(key: str, *items: Entity | Fact) -> None:
        nonlocal added
        if key in done:
            return
        for x in items:
            (rec.entities if isinstance(x, Entity) else rec.facts).setdefault(x.id, x)
        rec.seeded.append(key)
        done.add(key)
        added += 1

    pointing: dict[str, list[str]] = {}
    for c in m.clues:
        for k in c.points_to:
            pointing.setdefault(k, []).append(c.id)
    put("truth", Fact(id="truth", text=m.truth, kind="truth", origin="module"))
    for k in m.conclusions:
        put(f"conclusion:{k.id}", Fact(id=f"conclusion:{k.id}", text=k.text, kind="conclusion", origin="module",
                                       explains=pointing.get(k.id, [])))  # fmt: skip
    for loc in m.locations:
        facts = [Fact(id=f"location:{loc.id}:keeper", text=loc.keeper, kind="setting", about=[loc.id], origin=origin(loc.id))]
        put(f"loc:{loc.id}", Entity(id=loc.id, kind="location", name=loc.name, origin=origin(loc.id)), *[f for f in facts if f.text])
    for n in m.npcs:
        o = origin(n.id)
        facts = [Fact(id=f"npc:{n.id}:goal", text=n.goal, kind="goal", about=[n.id], known_by=[n.id], origin=o)] if n.goal else []
        for field, kind in (("knows", "knows"), ("secrets", "secret"), ("lies", "lie")):
            facts += [Fact(id=f"npc:{n.id}:{field}:{i}", text=text, kind=kind, about=[n.id], known_by=[n.id], origin=o,  # type: ignore[arg-type]
                           true=kind != "lie") for i, text in enumerate(getattr(n, field))]  # fmt: skip
        put(f"npc:{n.id}", Entity(id=n.id, kind="npc", name=n.name, origin=o, at=n.location or ""), *facts)
    for item in m.items:
        put(f"item:{item.id}", Entity(id=item.id, kind="item", name=item.name, origin=origin(item.id),
                                      at=st.item_owners.get(item.id) or item.owner or item.where))  # fmt: skip
    for c in m.clues:
        put(f"clue:{c.id}", Fact(id=f"clue:{c.id}", text=c.text, kind="clue", about=[x for x in (c.where, c.npc) if x],
                                 known_by=[c.npc] if c.npc else [], origin=origin(c.id), explains=[c.id]))  # fmt: skip
    for e in m.events:
        about = [x for x in (e.move_to, e.performer) if x]
        put(f"event:{e.id}", Fact(id=f"event:{e.id}", text=e.text, kind="event", about=about, origin=origin(e.id)))
    for inv in st.investigators:
        put(f"inv:{inv.id}", Entity(id=inv.id, kind="investigator", name=inv.name, at=st.location, origin="game"))
    return added


# -- what reached the table ---------------------------------------------------------------------
def shown(m: Module, st: GameState, rec: WorldRecord) -> list[str]:
    """Everything the players were shown or lived through, as far as the save still holds it."""
    texts = [*fog.shown(m, st), st.memory, st.prologue, st.progress.text]
    texts += [e.text for e in rec.events if e.visibility == "public"]
    texts += [f.text for f in rec.facts.values() if f.revealed is not None]
    return [t for t in texts if t]


def exposed(text: str, seen: set[str], joined: str) -> bool:
    """Its words came up in front of the players: two shared 5-character runs, or the whole of a short one."""
    own = grams(text)
    if not own:
        return bool(text.strip()) and text.strip() in joined
    return len(own & seen) >= min(2, len(own))


def reveal(rec: WorldRecord, fact: Fact, turn: int) -> bool:
    if fact.revealed is not None:
        return False
    fact.revealed = turn
    if PLAYERS not in fact.known_by:
        fact.known_by.append(PLAYERS)
    return True


def sync(m: Module, st: GameState, rec: WorldRecord) -> list[str]:
    """Mark what reached the table since the last time; returns the newly revealed fact ids."""
    out = []
    for cid in st.clues:
        fact = rec.facts.get(f"clue:{cid}")
        if fact and reveal(rec, fact, st.turn):
            out.append(fact.id)
    texts = shown(m, st, rec)
    joined, seen = "\n".join(texts), set().union(*(grams(t) for t in texts)) if texts else set()
    for fact in rec.facts.values():
        if fact.revealed is None and not fact.retracted and fact.text and exposed(fact.text, seen, joined):
            reveal(rec, fact, st.turn)
            out.append(fact.id)
    return out


# -- who may see what -----------------------------------------------------------------------------
def sees(fact: Fact, viewer: str) -> bool:
    if fact.retracted:
        return False
    if viewer == DIRECTOR:
        return True
    if viewer == PLAYERS:
        return fact.revealed is not None
    return viewer in fact.known_by


def saw(event: Event, viewer: str) -> bool:
    if viewer == DIRECTOR:
        return True
    if viewer == PLAYERS:
        return event.visibility == "public"
    return viewer == event.actor or viewer in event.witnesses or viewer in event.participants


def known(rec: WorldRecord, viewer: str, kinds: Iterable[str] | None = None) -> list[Fact]:
    wanted = set(kinds) if kinds else None
    return [f for f in rec.facts.values() if sees(f, viewer) and (wanted is None or f.kind in wanted)]


# -- back into this game's module ---------------------------------------------------------------
def project(m: Module, rec: WorldRecord) -> None:
    """Write the record's current truth into this game's module: where NPCs are, rewritten texts,
    what important NPCs learned. Idempotent; the module on disk never changes."""
    facts = rec.facts

    def text(fid: str, base: str) -> str:
        f = facts.get(fid)
        return base if f is None else ("" if f.retracted else f.text)

    if "truth" in facts:
        m.truth = text("truth", m.truth)
    for k in m.conclusions:
        k.text = text(f"conclusion:{k.id}", k.text)
    for loc in m.locations:
        loc.keeper = text(f"location:{loc.id}:keeper", loc.keeper)
    for c in m.clues:
        c.text = text(f"clue:{c.id}", c.text)
    for e in m.events:
        e.text = text(f"event:{e.id}", e.text)
    for n in m.npcs:
        if (ent := rec.entities.get(n.id)) and ent.at:
            n.location = ent.at
        n.goal = text(f"npc:{n.id}:goal", n.goal)
        for field in ("knows", "secrets", "lies"):
            prefix = f"npc:{n.id}:{field}:"
            base = sorted((f for f in facts.values() if f.id.startswith(prefix) and f.origin != "game"),
                          key=lambda f: int(f.id.rsplit(":", 1)[1]))  # fmt: skip
            kept = [f.text for f in base if not f.retracted]  # every module NPC is seeded before it is projected
            if field == "knows":  # what it learned in this game, the newest few
                learned = [f.text for f in facts.values() if f.origin == "game" and n.id in f.known_by and not f.retracted
                           and f.kind not in ("secret", "lie", "goal") and f.true]  # fmt: skip
                extra = learned[-PROJECTED_KNOWS:]
            elif field == "secrets":
                extra = [f.text for f in facts.values() if f.origin == "game" and f.kind == "secret" and n.id in f.known_by
                         and n.id in f.about and not f.retracted]  # fmt: skip
            else:
                extra = []
            if not base and not extra:
                continue
            kept += extra
            setattr(n, field, list(dict.fromkeys(kept)))
