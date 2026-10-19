"""A playthrough with no model: what a module lets a table reach, worked out from its structure.

Instead of AI investigators playing it (slow, paid, and not how people play), every way the
module opens up is followed at once. Everything in it only ever grows: places become known,
clues found, flags set, events fired. So "what can a table reach" is a fixpoint, reached in a
few rounds, with no search over orders:

    places    the start, every exit of a place reached, where a fired event takes the party
    clues     at a place reached (or with an NPC who is there), every requirement met
    flags     set by an event that fires; any other declared flag only when the investigators
              bring it about, as the arbiter rules (counted, but marked: it depends on them)
    events    their moment comes: a place reached (visit:), a flag set (flag:), an hour (D1 21:00)
    endings   their flag set, or an event that ends the game fires

Then, per conclusion: which places and which people it cannot do without (take one away and
no clue for it is left). The three-clue rule asks for none: any one clue can be missed. A public
place does not count (anyone can ask the way there: a town's main street is on every road but
cannot be missed); a hidden room, a locked crypt or one person who has to talk can.

What it does not see: the order things happen in (a deadline that passes before a clue is
found), and what the director adds in play. It sees what the module itself promises.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .graph import supporting_clues
from .schema import Module

TIMED = re.compile(r"D\d+ \d{1,2}:\d{2}")


@dataclass
class Reach:
    places: set[str] = field(default_factory=set)
    clues: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)  # set by the module's own events
    events: set[str] = field(default_factory=set)
    endings: dict[str, str] = field(default_factory=dict)  # ending id -> "事件" (it comes) or "行动" (they bring it about)


def reach(m: Module, *, without_places: frozenset[str] = frozenset(), without_npcs: frozenset[str] = frozenset()) -> Reach:
    r = Reach()
    places = {x.id: x for x in m.locations}
    if m.start in places and m.start not in without_places:
        r.places.add(m.start)
    acted = set(m.flags)  # what the arbiter may set when the investigators do what it means
    while True:
        before = (len(r.places), len(r.clues), len(r.flags), len(r.events))
        for pid in list(r.places):
            r.places |= {x for x in places[pid].exits if x in places and x not in without_places}
        for e in m.events:
            if e.id in r.events:
                continue
            when = e.when.strip()
            kind, _, what = when.partition(":")
            if (kind == "visit" and what in r.places) or (kind == "flag" and what in r.flags | acted) or TIMED.fullmatch(when):
                r.events.add(e.id)
                r.flags |= set(e.sets)
                if e.move_to and e.move_to in places and e.move_to not in without_places:
                    r.places.add(e.move_to)
        holders = {n.id: n.location for n in m.npcs if n.id not in without_npcs}
        for c in m.clues:
            if c.id in r.clues:
                continue
            # As Game._valid_clues has it: a clue is to be had where it lies, or wherever the one
            # who tells it is. Taking that person away takes away only the second way.
            here = c.where in r.places or (bool(c.npc) and holders.get(c.npc) in r.places)
            if c.npc and c.npc in without_npcs:
                here = c.where in r.places and not any(n.id == c.npc and n.location == c.where for n in m.npcs)
            met = all(x[5:] in r.flags | acted if x.startswith("flag:") else x in r.clues for x in c.requires)
            if here and met:
                r.clues.add(c.id)
        if (len(r.places), len(r.clues), len(r.flags), len(r.events)) == before:
            break
    for end in m.endings:
        flag = end.condition[5:] if end.condition.startswith("flag:") else ""
        if flag in r.flags:
            r.endings[end.id] = "事件"
        elif flag in acted:
            r.endings[end.id] = "行动"
    for e in m.events:
        if e.ending and e.id in r.events:
            r.endings[e.ending] = "事件"
    return r


@dataclass
class Report:
    reach: Reach
    unreachable: dict[str, list[str]]  # kind -> ids nothing in the module leads to
    fragile: dict[str, list[str]]  # conclusion -> the places and people it cannot do without
    lean: dict[str, int]  # conclusion -> clues for it a table can reach, when fewer than three
    issues: list[str]
    size: int = 0  # places, clues, events and endings in the module
    conclusions: int = 0

    def share(self) -> float:
        """How sound the module is, 0..1: nothing out of reach, no conclusion hanging on one thread."""
        out = sum(len(v) for v in self.unreachable.values()) / max(1, self.size)
        hanging = sum(bool(v) for v in self.fragile.values()) / max(1, self.conclusions)
        return max(0.0, 1 - out) * (1 - 0.5 * hanging)


def playthrough(m: Module) -> Report:
    r = reach(m)
    unreachable = {
        "地点": [x.id for x in m.locations if x.id not in r.places],
        "线索": [c.id for c in m.clues if c.id not in r.clues],
        "事件": [e.id for e in m.events if e.id not in r.events],
        "结局": [e.id for e in m.endings if e.id not in r.endings],
    }
    support = supporting_clues(m)
    lean = {k: n for k, v in support.items() if (n := len([c for c in v if c in r.clues])) < 3}
    fragile: dict[str, list[str]] = {}
    for k, clues in support.items():
        got = [c for c in clues if c in r.clues]
        if not got:
            continue
        hangs = []
        for loc in m.locations:
            if loc.id != m.start and not loc.public and not any(c in reach(m, without_places=frozenset({loc.id})).clues for c in got):
                hangs.append(f"地点 {loc.id}")
        for n in m.npcs:
            if any(m.clue(c).npc == n.id for c in got) and not any(c in reach(m, without_npcs=frozenset({n.id})).clues for c in got):
                hangs.append(f"NPC {n.id}")
        fragile[k] = hangs
    issues = []
    names = {"地点": "走不到的地点", "线索": "拿不到的线索", "事件": "不会发生的事件", "结局": "到不了的结局"}
    for kind, ids in unreachable.items():
        if ids:
            issues.append(f"{names[kind]}：{'、'.join(ids)}")
    for e in m.endings:
        if e.id in unreachable["结局"] and not e.condition.startswith("flag:"):
            issues.append(f"结局 {e.id} 的条件不是 flag:标记（{e.condition[:30]}），游戏里不会触发")

    for k, n in lean.items():
        issues.append(f"结论 {k} 能拿到的线索只有 {n} 条（三线索原则要三条）")
    for k, hangs in fragile.items():
        if hangs:
            issues.append(f"结论 {k} 离不开{'、'.join(hangs)}：去不了那里、那个人不开口，就推不出来")
    by_action = [e for e, how in r.endings.items() if how == "行动"]
    if r.endings and len(by_action) == len(r.endings):
        issues.append("所有结局都要靠调查员自己做到（裁定员认定），没有一个会随时间或事件自己到来")
    return Report(r, unreachable, fragile, lean, issues, size=len(m.locations) + len(m.clues) + len(m.events) + len(m.endings),
                  conclusions=len(m.conclusions))  # fmt: skip
