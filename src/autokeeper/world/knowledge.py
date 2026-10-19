"""Who knows what: the module seeded as facts, what reached the table, and who may see a fact.

A module fact's id is where it lives in the module (truth, npc:warden:secrets:0,
clue:c_diary, conclusion:k_tower, location:tower:keeper, event:e_sermon), so a rewrite can
be written back into this game's copy of the module (project) and everything that still
reads the module (NPC prompts, the leak guard, the arbiter) sees the same truth.

A fact is revealed by a found clue, an explicit public event, or its complete words in a
public source. The source and displayed excerpt are retained when the log is folded.
Shared names, addresses and fragments never make the rest of a hidden fact public.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..engine import fog
from ..engine.state import GameState
from ..module.schema import Module
from .record import DIRECTOR, PLAYERS, Entity, Event, Fact, Relation, WorldRecord, relation_key

if TYPE_CHECKING:
    from .store import World

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
        if not item.important:
            continue
        put(f"item:{item.id}", Entity(id=item.id, kind="item", name=item.name, origin=origin(item.id),
                                      at=st.item_owners.get(item.id) or item.owner or item.where,
                                      important=True, equipment=item.equipment, quantity=item.quantity,
                                      status=item.status, hidden=item.hidden or item.equipment == "concealed"))  # fmt: skip
    for c in m.clues:
        put(f"clue:{c.id}", Fact(id=f"clue:{c.id}", text=c.text, kind="clue", about=[x for x in (c.where, c.npc) if x],
                                 known_by=[c.npc] if c.npc else [], origin=origin(c.id), explains=[c.id]))  # fmt: skip
    for e in m.events:
        about = [x for x in (e.move_to, e.performer) if x]
        put(f"event:{e.id}", Fact(id=f"event:{e.id}", text=e.text, kind="event", about=about, origin=origin(e.id)))
    for inv in st.investigators:
        put(f"inv:{inv.id}", Entity(id=inv.id, kind="investigator", name=inv.name, at=st.location, origin="game"))
    npcs = {n.id for n in m.npcs}
    for bond in m.relations:  # the conflict graph (module.schema.Bond): how they see each other from the start
        key = relation_key(bond.a, bond.b, bond.kind)
        if f"bond:{key}" not in done and bond.a in npcs:
            rec.relations.setdefault(key, Relation(a=bond.a, b=bond.b, kind=bond.kind, value=bond.value, note=bond.why,
                                                   known_by=[x for x in (bond.a, bond.b) if x in npcs]))  # fmt: skip
            rec.seeded.append(f"bond:{key}")
            done.add(f"bond:{key}")
            added += 1
    return added


# -- what reached the table ---------------------------------------------------------------------
# This is an evidence check, not the NPC leak detector. A few shared runs cannot establish
# that every claim in a secret (often several paragraphs) was actually disclosed.
_MARKS = re.compile(r"[\s，。、；：？！“”‘’「」『』（）()《》〈〉…—\-,.;:?!\"'`]+")
_PUBLIC_EVENTS = {"occur", "move", "transfer", "condition", "promise", "fulfil", "break", "fact", "learn"}


@dataclass(frozen=True)
class PublicEvidence:
    source: str
    text: str
    turn: int | None = None


def evidence(m: Module, st: GameState, rec: WorldRecord) -> list[PublicEvidence]:
    """Primary public sources only. Revealed facts never prove that another fact was seen."""
    out = [PublicEvidence(f"module:{key}", getattr(m, key), 0) for key in ("title", "subtitle", "hook", "setting")]
    out += [PublicEvidence(f"arrival:{loc.id}", loc.read_aloud) for loc in m.locations if loc.id in st.visited]
    out += [PublicEvidence(f"log:{e.turn}:{e.kind}:{st.logged - len(st.log) + i}", e.text, e.turn)
            for i, e in enumerate(st.log) if e.kind in fog.SHOWN]
    out += [PublicEvidence(f"clue:{c.id}", c.text) for c in m.clues if c.id in st.clues]
    out += [PublicEvidence(f"handout:{h.id}", h.text) for h in m.handouts if h.id in st.handouts]
    out += [PublicEvidence(f"look:{n.id}", n.look) for n in m.npcs if n.id in st.npcs and st.npcs[n.id].met]
    out += [PublicEvidence(f"event:{e.id}", e.text, e.turn)
            for e in rec.events if e.visibility == "public" and e.kind in _PUBLIC_EVENTS]
    out += [PublicEvidence("memory", st.memory), PublicEvidence("prologue", st.prologue, 0),
            PublicEvidence("progress", st.progress.text, st.progress.turn)]
    return [e for e in out if e.text]


def shown(m: Module, st: GameState, rec: WorldRecord) -> list[str]:
    """Everything still recorded as shown, without treating a reveal marker as new evidence."""
    return [e.text for e in evidence(m, st, rec)]


@dataclass
class Table:
    entries: list[PublicEvidence]
    normalized: list[str]


def _plain(text: str) -> str:
    return _MARKS.sub("", text).casefold()


def table(m: Module, st: GameState, rec: WorldRecord) -> Table:
    entries = evidence(m, st, rec)
    return Table(entries, [_plain(e.text) for e in entries])


def _matching(text: str, at: Table) -> PublicEvidence | None:
    """A complete statement within one source; fragments from unrelated sources cannot join."""
    own = _plain(text)
    if not own:
        return None
    return next((e for e, words in zip(at.entries, at.normalized, strict=True) if own in words), None)


def exposed(text: str, at: Table) -> bool:
    """Whether the full statement was displayed (formatting differences are harmless)."""
    return _matching(text, at) is not None


def how_known(w: World, fact: Fact, at: Table) -> str:
    """How the players have this fact, for a rewrite to account for (world/rewrite.py):
    "听守钟人说的", "叙述里说过", "亲眼见到", several joined by 、; empty when it never reached them."""
    own = _plain(fact.text)
    sources = [e.source for e, words in zip(at.entries, at.normalized, strict=True) if own and own in words]
    if fact.revealed is not None and fact.reveal_source:
        sources.append(fact.reveal_source)
    first = w.st.logged - len(w.st.log)  # absolute index of the oldest log entry still kept
    ways = []
    for source in dict.fromkeys(sources):
        kind, _, rest = source.partition(":")
        if kind == "log":
            said, _, index = rest.partition(":")[2].partition(":")
            if said == "npc":
                at_index = int(index) - first if index.isdigit() else -1
                who = w.st.log[at_index].who if 0 <= at_index < len(w.st.log) else ""
                ways.append(f"听{who or '人'}说的")
            else:
                ways.append("叙述里说过" if said in ("keeper", "opening", "ending") else "亲眼见到")
        elif kind in ("event", "private-event") and (e := w.rec.event(rest)) is not None and e.kind in ("learn", "fact"):
            npc = w.rec.entities.get(e.actor)
            ways.append(f"听{npc.name}说的" if npc is not None and npc.kind == "npc" else "叙述里说过")
        elif kind in ("memory", "progress", "prologue"):
            ways.append("叙述里说过")
        else:
            ways.append("亲眼见到")
    return "、".join(dict.fromkeys(ways))


def keep_told(w: World, fact: Fact, at: Table) -> Fact:
    """Before a fact the players met is rewritten: what they were told or shown stays theirs,
    as words that turned out untrue, with the evidence it came in; the fact itself goes back
    to unknown, holding the new truth."""
    seen = _matching(fact.text, at)
    told = Fact(id=f"told:{fact.id}:{fact.version}", text=fact.text, kind="rumor", about=list(fact.about), origin="game",
                true=False, known_by=list(dict.fromkeys([*fact.known_by, PLAYERS])), t=w.now)
    source = fact.reveal_source if fact.revealed is not None and fact.reveal_source else seen.source if seen else ""
    excerpt = fact.reveal_excerpt if fact.revealed is not None and fact.reveal_excerpt else seen.text if seen else fact.text
    reveal(w.rec, told, fact.revealed if fact.revealed is not None else w.st.turn, source=source, excerpt=excerpt)
    w.rec.facts[told.id] = told
    for e in w.rec.events:  # what told them now carries what they were told
        if fact.id in e.facts:
            e.facts = [told.id if x == fact.id else x for x in e.facts]
    fact.revealed, fact.reveal_source, fact.reveal_excerpt = None, "", ""
    fact.known_by = [x for x in fact.known_by if x != PLAYERS]
    return told


def reveal(rec: WorldRecord, fact: Fact, turn: int, *, source: str, excerpt: str) -> bool:
    """Record one explicit disclosure with durable evidence, never a similarity score."""
    if not source or not excerpt.strip():
        raise ValueError("公开事实必须保留来源与公开内容")
    fresh = fact.revealed is None
    # An explicit disclosure can rehabilitate an uncertain historical marker. Keep its
    # original turn when available, while retaining the actual evidence that verified it.
    fact.revealed = fact.revealed if fact.revealed is not None else turn
    fact.reveal_source, fact.reveal_excerpt = source, excerpt
    if fact.legacy_revealed is not None:
        fact.reveal_note = "旧公开标记已由可追溯的公开来源核验。"
        fact.legacy_revealed = None
    if PLAYERS not in fact.known_by:
        fact.known_by.append(PLAYERS)
    return fresh


def sync(m: Module, st: GameState, rec: WorldRecord) -> list[str]:
    """Verify public knowledge; quarantine old unsupported markers without erasing history."""
    out = []
    at = table(m, st, rec)
    event_sources = {fid: e for e in rec.events if e.visibility == "public" for fid in e.facts}
    for fact in rec.facts.values():
        if fact.retracted or not fact.text:
            continue
        source = None
        if fact.id.startswith("clue:") and fact.id.removeprefix("clue:") in st.clues:
            source = PublicEvidence(fact.id, fact.text)
        elif event := event_sources.get(fact.id):
            source = PublicEvidence(f"event:{event.id}", event.text, event.turn)
        elif not fact.reveal_source:
            source = _matching(fact.text, at)
        if source:
            turn = fact.revealed if fact.revealed is not None else source.turn if source.turn is not None else st.turn
            if reveal(rec, fact, turn, source=source.source, excerpt=source.text):
                out.append(fact.id)
        elif fact.revealed is not None and not fact.reveal_source:
            # Old versions used two matching five-character runs (an address alone was
            # enough to mark the whole module truth). The save may have folded its log,
            # so absence of evidence cannot prove this was never spoken either.
            fact.legacy_revealed, fact.revealed = fact.revealed, None
            fact.known_by = [x for x in fact.known_by if x != PLAYERS]
            fact.reveal_note = "旧版本公开标记缺少来源，现存公开记录无法核验；暂停公开全文并保留历史标记，暂不允许改写。"
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


def public_ref(fact: Fact, npc: str) -> str:
    """Stable actor-scoped reference without a module path that classifies its truthfulness."""
    from .ops import STAFF

    if npc in STAFF:
        return fact.id
    digest = hashlib.sha256(f"{npc}/{fact.id}".encode()).hexdigest()[:20]
    return f"claim:{digest}"


def resolve_ref(w: World, npc: str, ref: str) -> str | None:
    """Decode only known facts; knowing or guessing a handle never grants access to a fact."""
    from .ops import STAFF

    facts = w.rec.facts.values() if npc in STAFF else known(w.rec, npc)
    for fact in facts:
        if not fact.retracted and ref in (fact.id, public_ref(fact, npc)):
            return fact.id
    return None


def public_text(w: World, npc: str, text: str) -> str:
    """Project references in an NPC's own plan/memory text without changing saved records."""
    for fact in sorted(known(w.rec, npc), key=lambda f: -len(f.id)):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(fact.id)}(?![A-Za-z0-9_])"
        text = re.sub(pattern, lambda _match, fact=fact: public_ref(fact, npc), text)
    return text


def item_known(w: World, item: Entity | str, viewer: str) -> bool:
    """Current item knowledge, distinct from being in the same room as its holder."""
    from .ops import STAFF

    item = w.rec.entities.get(item) if isinstance(item, str) else item
    if item is None or item.kind != "item" or not item.important:
        return False
    if viewer in STAFF or item.at == viewer:
        return True
    investigators = {inv.id for inv in w.st.investigators}
    if viewer == PLAYERS and item.at in investigators:
        return True
    if viewer in item.observed_by or (viewer in investigators and PLAYERS in item.observed_by):
        return True
    place = w.rec.entities.get(item.at)
    here = w.st.location if viewer == PLAYERS or viewer in investigators else w.place(viewer)
    return bool(place and place.kind == "location" and item.at == here
                and not item.hidden and item.equipment != "concealed")


def visible_items(w: World, viewer: str, holder: str = "") -> list[Entity]:
    """Important items this actor can know now; quantity 0 is known but cannot be used."""
    return [item for item in w.rec.entities.values()
            if (not holder or item.at == holder) and item_known(w, item, viewer)]


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
        if ent is not None:
            n.status = ent.status  # the screens leave the dead and the missing out of a scene
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
