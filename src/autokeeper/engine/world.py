"""Durable public consequences, separate from lossy conversation summaries."""

from __future__ import annotations

from ..module.schema import Module
from .state import GameState, WorldChange, WorldFact


def owner(m: Module, st: GameState, item: str) -> str:
    if item in st.item_owners:
        return st.item_owners[item]
    for inv in st.investigators:
        if item in inv.inventory:
            return inv.id
    return next((x.owner or x.where for x in m.items if x.id == item), "")


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
        before = owner(m, st, c.item)
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
            before = owner(m, st, c.item)
            item = next((x for x in m.items if x.id == c.item), None)
            name = item.name if item else c.item
            for inv in st.investigators:
                if inv.id == before and name in inv.inventory:
                    inv.inventory.remove(name)
                if inv.id == c.owner and name not in inv.inventory:
                    inv.inventory.append(name)
            st.item_owners[c.item] = c.owner
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
