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

from .. import media_snapshot
from ..engine import audience, fog
from ..engine.state import GameState
from ..module.schema import Module
from .record import DIRECTOR, PLAYERS, Entity, Event, Fact, ItemSighting, Relation, WorldRecord, relation_key

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
                                 known_by=[n.id for n in m.npcs if c.id in n.clues], origin=origin(c.id), explains=[c.id]))  # fmt: skip
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
    readers: tuple[str, ...] | None = None


def evidence(m: Module, st: GameState, rec: WorldRecord, *, include_private: bool = False) -> list[PublicEvidence]:
    """Primary public sources only. Revealed facts never prove that another fact was seen."""
    party = {i.id for i in st.investigators}

    def everyone(readers: Iterable[str]) -> bool:
        known = set(readers)
        return PLAYERS in known or (bool(party) and party <= known)

    def observed(ident: str) -> bool:
        ent = rec.entities.get(ident)
        return len(party) == 1 or (ent is not None and everyone(ent.observed_by))

    out = [PublicEvidence(f"module:{key}", getattr(m, key), 0) for key in ("title", "subtitle", "hook", "setting")]
    out += [PublicEvidence(f"arrival:{loc.id}", loc.read_aloud) for loc in m.locations if loc.id in st.visited and observed(loc.id)]
    out += [PublicEvidence(f"log:{e.turn}:{e.kind}:{st.logged - len(st.log) + i}", e.text, e.turn)
            for i, e in enumerate(st.log) if e.kind in fog.SHOWN and audience.shared(e)]
    out += [PublicEvidence(f"clue:{c.id}", c.text) for c in m.clues if c.id in st.clues
            and (len(party) == 1 or ((f := rec.facts.get(f"clue:{c.id}")) is not None and everyone(f.known_by)))]
    out += [PublicEvidence(f"handout:{h.id}", h.text) for h in m.handouts if h.id in st.handouts]
    out += [PublicEvidence(f"look:{n.id}", n.look) for n in m.npcs if n.id in st.npcs and st.npcs[n.id].met
            and (observed(n.id) or all(n.id in rec.sightings.get(who, {}) for who in party))]
    out += [PublicEvidence(f"event:{e.id}", e.text, e.turn)
            for e in rec.events if e.visibility == "public" and e.kind in _PUBLIC_EVENTS]
    out += [PublicEvidence("memory", st.memory), PublicEvidence("prologue", st.prologue, 0),
            PublicEvidence("progress", st.progress.text, st.progress.turn)]
    if include_private:
        for group, rows in (("private-log", st.log), ("private-history", st.private_history)):
            for index, entry in enumerate(rows):
                if entry.kind not in fog.SHOWN or audience.shared(entry):
                    continue
                for who in party:
                    if audience.visible(entry, who):
                        shown = entry.presented.get(who, {}).get("text", entry.text)
                        out.append(PublicEvidence(f"{group}:{entry.turn}:{entry.kind}:{index}", shown, entry.turn, (who,)))
        for event in rec.events:
            readers = tuple(who for who in party if who in (*event.witnesses, *event.participants, event.actor))
            if event.visibility != "public" and event.kind in _PUBLIC_EVENTS and readers:
                out.append(PublicEvidence(f"private-event:{event.id}", event.text, event.turn, readers))
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


def table(m: Module, st: GameState, rec: WorldRecord, *, include_private: bool = False) -> Table:
    entries = evidence(m, st, rec, include_private=include_private)
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
        if kind in ("private-log", "private-history"):
            ways.append("私下听说" if ":npc:" in source else "个人叙述里说过")
        elif kind == "log":
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
    if not ways and any(i.id in fact.known_by for i in w.st.investigators):
        ways.append("个别调查员已知")
    return "、".join(dict.fromkeys(ways))


def keep_told(w: World, fact: Fact, at: Table) -> Fact:
    """Before a fact the players met is rewritten: what they were told or shown stays theirs,
    as words that turned out untrue, with the evidence it came in; the fact itself goes back
    to unknown, holding the new truth."""
    own = _plain(fact.text)
    matches = [entry for entry, words in zip(at.entries, at.normalized, strict=True) if own and own in words]
    seen = next((entry for entry in matches if entry.readers is None), next(iter(matches), None))
    known = [*fact.known_by, *(who for entry in matches if entry.readers is not None for who in entry.readers)]
    shared = fact.revealed is not None or PLAYERS in fact.known_by or any(entry.readers is None for entry in matches)
    told = Fact(id=f"told:{fact.id}:{fact.version}", text=fact.text, kind="rumor", about=list(fact.about), origin="game",
                true=False, known_by=list(dict.fromkeys(known)), t=w.now)
    source = fact.reveal_source if fact.revealed is not None and fact.reveal_source else seen.source if seen else f"known:{fact.id}"
    excerpt = fact.reveal_excerpt if fact.revealed is not None and fact.reveal_excerpt else seen.text if seen else fact.text
    if shared:
        reveal(w.rec, told, fact.revealed if fact.revealed is not None else w.st.turn, source=source, excerpt=excerpt)
    else:
        told.reveal_source, told.reveal_excerpt = source, excerpt
    w.rec.facts[told.id] = told
    for e in w.rec.events:  # what told them now carries what they were told
        if fact.id in e.facts:
            e.facts = [told.id if x == fact.id else x for x in e.facts]
    fact.revealed, fact.reveal_source, fact.reveal_excerpt = None, "", ""
    readers = {PLAYERS, *(i.id for i in w.st.investigators)}
    fact.known_by = [x for x in fact.known_by if x not in readers]
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
    fact.known_by = list(dict.fromkeys([*fact.known_by, *(e.id for e in rec.entities.values() if e.kind == "investigator")]))
    return fresh


def sync(m: Module, st: GameState, rec: WorldRecord) -> list[str]:
    """Verify public knowledge; quarantine old unsupported markers without erasing history."""
    out = []
    at = table(m, st, rec)
    personal = table(m, st, rec, include_private=True)
    event_sources = {fid: e for e in rec.events if e.visibility == "public" for fid in e.facts}
    for fact in rec.facts.values():
        if fact.retracted or not fact.text:
            continue
        source = None
        if fact.id.startswith("clue:") and fact.id.removeprefix("clue:") in st.clues:
            source = next((e for e in at.entries if e.source == fact.id), None)
        elif event := event_sources.get(fact.id):
            source = PublicEvidence(f"event:{event.id}", event.text, event.turn)
        if source is None and (fact.revealed is None or not fact.reveal_source):
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
        own = _plain(fact.text)
        for entry, words in zip(personal.entries, personal.normalized, strict=True):
            if entry.readers is not None and own and own in words:
                fact.known_by = list(dict.fromkeys([*fact.known_by, *entry.readers]))
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


def clue_known(state: GameState, clue: str, viewer: str) -> bool:
    """Global progress is not another investigator's knowledge of a discovery."""
    fact = state.record.facts.get(f"clue:{clue}")
    if fact is not None:
        return not fact.retracted and (viewer in fact.known_by or PLAYERS in fact.known_by)
    return len(state.investigators) == 1 and clue in state.clues


def disclosable_clues(m: Module, st: GameState, npc: str, *, record: WorldRecord | None = None,
                     present: bool = True) -> set[str]:
    """One disclosure contract for NPC prompts, replies and the engine: actual current knowledge,
    live clue, prerequisites and its gate. Knowing is not revealing; only Dialogue.told records
    what was actually said. The reply receipt may be checked after its speaker walked away."""
    from ..module.graph import disclosure_gate, tellers

    if present:
        if record is not None:
            actor = record.entities.get(npc)
            if actor is None or actor.at != st.location or actor.status in {"死亡", "失踪"}:
                return set()
        elif m.npc(npc).location != st.location or m.npc(npc).status in {"死亡", "失踪"}:
            return set()
    unlocked = set(st.npc(npc).unlocked)
    return {c.id for c in m.clues if npc in tellers(m, c, record=record)
            and (record is not None or c.id in m.npc(npc).clues) and c.available(st.flags, st.clues)
            and (c.id in unlocked or disclosure_gate(m, c, npc) is None)}


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


def item_observers(w: World, viewers: Iterable[str]) -> list[str]:
    """Common item knowledge requires a nonempty party whose members actually learned it."""
    people = {entity.id for entity in w.rec.entities.values() if entity.kind in {"npc", "investigator", "creature"}}
    actual = list(dict.fromkeys(viewer for viewer in viewers if viewer in people))
    party = {investigator.id for investigator in w.st.investigators}
    if party and party <= set(actual):
        actual.append(PLAYERS)
    return actual


def remember_item(w: World, item: Entity, viewers: Iterable[str], event: str) -> None:
    """Keep what an actual transfer, discovery or inspection showed, independently of truth."""
    if item.kind != "item":
        return
    sheet = next((entry for entry in w.m.items if entry.id == item.id), None)
    seen = ItemSighting(
        event=event, at=item.at, name=item.name, equipment=item.equipment,
        quantity=item.quantity, status=item.status, hidden=item.hidden, look=item.look,
        description=sheet.description if sheet else item.look,
        art=media_snapshot.remember(w.m, w.st, sheet.art) if sheet and sheet.art else None,
        where=sheet.where if sheet else "", version=item.version, changed=item.changed)
    for viewer in item_observers(w, viewers):
        observed = seen.model_copy(deep=True)
        if viewer != item.at and (item.hidden or item.equipment == "concealed"):
            # Showing one object does not expose the rest of a concealed supply.
            observed.hidden, observed.equipment = False, "carried"
            observed.quantity = min(observed.quantity, 1)
        w.rec.item_sightings.setdefault(viewer, {})[item.id] = observed


def observe_items(w: World, place: str, witnesses: Iterable[str], event: str) -> None:
    """A room visit reveals exposed objects there, never the contents of another pocket."""
    witnesses = list(witnesses)
    for item in w.rec.entities.values():
        if item.kind != "item":
            continue
        floor = item.at == place and not item.hidden and item.equipment != "concealed"
        viewers = [viewer for viewer in witnesses if floor or item.at == viewer]
        if viewers:
            remember_item(w, item, viewers, event)


def item_sighting(w: World, item: Entity, viewer: str) -> ItemSighting | None:
    """The remembered state, unless this viewer can inspect their own current possession."""
    if viewer == PLAYERS and len(w.st.investigators) == 1:
        viewer = w.st.investigators[0].id
    if item.at == viewer:
        return None
    if seen := w.rec.item_sightings.get(viewer, {}).get(item.id):
        return seen
    investigators = {inv.id for inv in w.st.investigators}
    if viewer == PLAYERS or viewer in investigators:
        return w.rec.item_sightings.get(PLAYERS, {}).get(item.id)
    return None


def item_view(w: World, item: Entity | str, viewer: str) -> Entity | None:
    """Return an actor's current possession or their last observed copy, never unseen changes."""
    from .ops import STAFF

    item = w.rec.entities.get(item) if isinstance(item, str) else item
    if item is None or item.kind != "item":
        return None
    if viewer == PLAYERS and len(w.st.investigators) == 1:
        viewer = w.st.investigators[0].id
    if viewer in STAFF or item.at == viewer:
        return item
    if seen := item_sighting(w, item, viewer):
        return Entity(id=item.id, kind="item", name=seen.name, at=seen.at,
                      equipment=seen.equipment, quantity=seen.quantity, status=seen.status,
                      hidden=seen.hidden, look=seen.look, version=seen.version, changed=seen.changed)
    investigators = {inv.id for inv in w.st.investigators}
    # These markers describe current observation only; operations replace them when
    # truth changes and retain the previous observation above.
    if viewer in item.observed_by or (viewer in investigators and PLAYERS in item.observed_by):
        return item
    if viewer == PLAYERS:
        return None  # a multiplayer table has no physical viewpoint of its own
    place = w.rec.entities.get(item.at)
    here = w.place(viewer)
    return item if (place and place.kind == "location" and item.at == here
                    and not item.hidden and item.equipment != "concealed") else None


def item_known(w: World, item: Entity | str, viewer: str) -> bool:
    """Knowing an item exists survives later unobserved changes to its ownership or state."""
    item = w.rec.entities.get(item) if isinstance(item, str) else item
    return bool(item is not None and item.important and item_view(w, item, viewer) is not None)


def visible_items(w: World, viewer: str, holder: str = "") -> list[Entity]:
    """Important items this actor can know now; quantity 0 is known but cannot be used."""
    return [seen for item in w.rec.entities.values() if item.important
            and (seen := item_view(w, item, viewer)) is not None and (not holder or seen.at == holder)]


# -- back into this game's module ---------------------------------------------------------------
def project(m: Module, rec: WorldRecord) -> None:
    """Write the record's current truth into this game's module: where NPCs are, rewritten texts,
    what important NPCs learned. Idempotent; the module on disk never changes. Clue providers
    read this same record (module.graph.tellers); NPC.clues/gates remain the source declaration,
    not a mutable projection that would retain a former knower or lose its original gate."""
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
        if ent is not None:  # the screens leave the dead and the missing out of a scene
            # A body the world keeps (Entity.life, engine/effects) that died is dead, whatever word was set.
            n.status = "死亡" if ent.life is not None and "死亡" in ent.life.conditions else ent.status
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
