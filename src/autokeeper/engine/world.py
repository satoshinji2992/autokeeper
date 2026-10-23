"""Durable public consequences, separate from lossy conversation summaries."""

from __future__ import annotations

from ..module.schema import Module
from .state import GameState, WorldChange, WorldFact


def item_id(m: Module, st: GameState, item: str, *, holder: str = "") -> str | None:
    """Resolve an existing item id or its unique display name, without inventing an alias.

    A holder can distinguish otherwise identical objects. An ambiguous name has no
    identity; a name with no record remains usable for an old inventory-only object.
    """
    item = item.strip()
    if ent := st.record.entities.get(item):
        return item if ent.kind == "item" else None
    if any(i.id == item for i in m.items):
        return item
    named = {e.id: e.at for e in st.record.entities.values() if e.kind == "item" and e.name == item}
    for entry in m.items:
        if entry.name == item and entry.id not in named:
            named[entry.id] = st.item_owners.get(entry.id) or entry.owner or entry.where
    if holder:
        owned = [ident for ident, at in named.items() if at == holder]
        if owned:
            return owned[0] if len(owned) == 1 else None
    return next(iter(named)) if len(named) == 1 else (None if named else item)


def owner(m: Module, st: GameState, item: str, *, holder: str = "") -> str:
    item = item_id(m, st, item, holder=holder)
    if item is None:
        return ""
    if ent := st.record.entities.get(item):
        return ent.at
    if item in st.item_owners:
        return st.item_owners[item]
    for inv in st.investigators:
        if item in inv.inventory:
            return inv.id
    return next((x.owner or x.where for x in m.items if x.id == item), "")


def other_named_item(m: Module, st: GameState, holder: str, name: str, *, excluding: str) -> bool:
    """Another actual possession still represented by this inventory display line."""
    return (any(e.id != excluding and e.kind == "item" and e.at == holder and e.name == name and e.quantity > 0
                for e in st.record.entities.values())
            or any(i.id != excluding and i.id not in st.record.entities and i.name == name and i.quantity > 0
                   and owner(m, st, i.id) == holder for i in m.items))


def actors(m: Module, st: GameState) -> set[str]:
    return {i.id for i in st.investigators} | {n.id for n in m.npcs_at(st.location)} | {st.location}


def admissible(m: Module, st: GameState, c: WorldChange, *, source: str, spoken: str = "", present: set[str] | None = None) -> bool:
    """Whether apply would take this change now (world/adapters.py settles the same ones as ops)."""
    present = actors(m, st) if present is None else present
    if not c.key.strip() or not c.text.strip() or len(c.text) > 500 or (c.npc and c.npc not in present):
        return False
    if spoken and (c.text not in spoken or c.npc != source):
        return False
    old = st.world.get(c.key)
    if old and (old.who != st.investigator.id or old.npc != c.npc or (old.kind != c.kind and c.kind != "fulfill")):
        return False
    if c.kind == "fulfill":
        fact = st.world.get(c.key)
        return bool(fact and fact.kind == "promise" and fact.who == st.investigator.id and fact.npc == c.npc)
    if c.kind == "transfer":
        before = owner(m, st, c.item, holder=source if spoken else st.investigator.id)
        allowed = {source} if spoken else {st.investigator.id, st.location}
        return bool(before) and before in allowed and c.owner in present and before != c.owner
    return True


def apply(m: Module, st: GameState, changes: list[WorldChange], *, source: str, spoken: str = "") -> None:
    """Only the acting person's reachable possessions can move. NPC memories must quote
    their actual reply; private lore and player assertions are never extracted as facts."""
    present = actors(m, st)
    for c in changes[:8]:
        if not admissible(m, st, c, source=source, spoken=spoken, present=present):
            continue
        if c.kind == "fulfill":
            fact = st.world[c.key]
            fact.status, fact.turn = "fulfilled", st.turn
            continue
        if c.kind == "transfer":
            ident = item_id(m, st, c.item, holder=source if spoken else st.investigator.id)
            assert ident is not None  # admissible checked an unambiguous existing possession
            before = owner(m, st, ident)
            item = next((x for x in m.items if x.id == ident), None)
            entity = st.record.entities.get(ident)
            name = entity.name if entity else item.name if item else ident
            for inv in st.investigators:
                if (inv.id == before and name in inv.inventory
                        and not other_named_item(m, st, before, name, excluding=ident)):
                    inv.inventory.remove(name)
                if inv.id == c.owner and name not in inv.inventory:
                    inv.inventory.append(name)
            st.item_owners[ident] = c.owner
            if entity is not None:
                entity.at, entity.equipment = c.owner, "carried"
        st.world[c.key] = WorldFact(kind=c.kind, text=c.text, who=st.investigator.id, npc=c.npc,
                                   turn=st.turn, source=source)


STATUS = {"active": "有效", "fulfilled": "已履行", "broken": "未履行"}


def context(st: GameState, *, npc: str | None = None) -> str:
    selected = [(key, value) for key, value in st.world.items() if npc is None or value.npc == npc]
    recent = [(key, v) for key, v in selected if v.kind != "promise" or v.status != "active"][-20:]
    pending = [(key, v) for key, v in selected if v.kind == "promise" and v.status == "active"]
    facts = [f"[{key}] {v.text}（{STATUS[v.status]}）"
             for key, v in [*recent, *pending]]
    return "【本局已发生的事实和约定】\n" + ("\n".join(facts) or "无")
