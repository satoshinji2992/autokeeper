"""What compile time added, held to the module's own conditions (原作条件复核), with no model call.

The situation layer (module/adapt.py) gives the people what they know and what they do on meeting
the investigators. Written loosely it hands over what the module keeps behind a roll: a meeting
reaction that points at the diary a 侦查 roll finds, a step that waits on what the investigators
found as if the NPC knew it too. The structural checks (graph.audit) cannot see that; these rules
can, from the module alone:

    提前兑现    an addition the NPC plays or the keeper shows (a meeting reaction that hands something
                over, a sign) names a clue that takes a check, a prerequisite or its giver's social
                gate, with nothing in it waiting for the clue to be found; or a fact compile time gave
                an NPC says what such a clue says (knowing the diary is in the study is no finding it)
    条件太宽    a step waits on a flag that says what the investigators learned or got (has_liquor,
                learned_telescope), with no word on how the NPC comes to know it or what must be
                done in front of him (Move.only_if)

Knowing a thing exists, knowing where, finding it, taking it and reading it are five things: the
keeper may rule a new way in play, the compiler never decides one for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schema import Clue, Module

# A flag that says what the investigators came to know or to have, not what happened to someone.
PROGRESS = re.compile(r"(调查员|玩家).{0,8}?(得知|知道|获知|知晓|查到|查清|查出|发现|弄到|买到|拿到|找到|获得|得到|搞到|看见|看到|听到|听说|读到|认出)")
PROGRESS_ID = re.compile(r"^(learned|found|knows?|has|got|saw|heard|read)_")


@dataclass
class Piece:
    """One thing compile time added that an NPC plays or knows, or the keeper shows the players."""

    ref: str  # agendas:<npc>:<step> / knows:<npc>:<i> / foreshadow:<id> / foreshadow:<id>:<stage, from 1>
    kind: str  # meet / backstage / knows / foreshadow
    text: str
    npc: str = ""
    when: str = ""
    only_if: str = ""
    ruling: bool = False
    does: str = ""


@dataclass
class Finding:
    ref: str
    level: str  # error: hands over what a condition keeps; warning: a condition too wide; note: a fact that
    # says what a clue behind a roll says (the NPC may well have lived it: asked again, kept)
    text: str

    def __str__(self) -> str:
        return f"[{'错误' if self.level == 'error' else '提醒'}] {self.text}"


def pieces(m: Module) -> list[Piece]:
    """The situation layer as pieces: every agenda step and sign (compile time's or the author's
    additions; the module's own clues, events and endings are not here). The past is left to the
    review that reads the original (module/adapt.verify): who knew what before play is no way to a
    clue by itself."""
    out = []
    for a in m.agendas:
        for s in a.steps:
            kind = "meet" if s.meet else "backstage"
            out.append(Piece(f"agendas:{a.npc}:{s.id}", kind, s.text, a.npc, s.when, s.only_if, s.ruling, s.does))
    for f in m.foreshadow:  # a sign, and each later look at it (Foreshadow.stages)
        out.append(Piece(f"foreshadow:{f.id}", "foreshadow", f.hint))
        out += [Piece(f"foreshadow:{f.id}:{n}", "foreshadow", s.hint, when=s.when) for n, s in enumerate(f.stages, 1)]
    return out


def knows(m: Module, npc: str, start: int = 0) -> list[Piece]:
    """An NPC's facts from `start` on, as pieces: the ones compile time filled in."""
    n = m.npc(npc)
    return [Piece(f"knows:{npc}:{i}", "knows", text, npc) for i, text in enumerate(n.knows) if i >= start]


def gate(m: Module, c: Clue) -> str:
    """What it takes to get a clue, in words; empty when it is had for the looking or the asking."""
    parts = []
    if c.check:
        parts.append(f"{c.check.skill}检定")
    if c.npc and (giver := next((n for n in m.npcs if n.id == c.npc), None)) and c.id in giver.gates:
        parts.append(f"对{giver.name}的{giver.gates[c.id].skill}")
    if c.requires:
        parts.append("先有 " + "、".join(c.requires))
    return "、".join(parts)


COMMON = 8  # a word the module's own text uses this often names no one thing (病人 in a clinic)


def visible(m: Module) -> str:
    """What the players can see or are told without any roll: the hook, the places as they come
    to them (names, what is read aloud, what can be looked at), the people as they meet them."""
    rows = [m.title, m.subtitle, m.hook, m.setting, m.opening]
    rows += [f"{x.name} {x.read_aloud} {' '.join(x.features)}" for x in m.locations]
    rows += [f"{n.name} {n.role} {n.look}" for n in m.npcs]
    rows += [f"{i.name} {i.description}" for i in m.items if not i.hidden]
    return " ".join(rows)


def corpus(m: Module) -> str:
    """The module's own keeper text, to tell a thing's name from a common word."""
    return " ".join([m.truth, m.keeper_notes, *(x.read_aloud + x.keeper for x in m.locations),
                     *(n.role + " ".join(n.knows + n.secrets) for n in m.npcs), *(e.text for e in m.events), *(c.text for c in m.clues)])


def titles(m: Module, c: Clue) -> list[str]:
    """A clue's title and its handout's."""
    h = next((x for x in m.handouts if x.id == c.handout), None) if c.handout else None
    return [c.title] + ([h.title] if h else [])


def marks(m: Module, c: Clue, seen: str | None = None, words: str | None = None) -> list[str]:
    """The words a text names a clue by: its title, its handout's title, and the thing the title
    is about (日记 of 道格拉斯的日记) when that is not something the players see anyway (墓碑 in a
    cemetery) or a word the module uses all over (病人 of 检查活着的病人)."""
    seen = visible(m) if seen is None else seen
    words = corpus(m) if words is None else words
    found = titles(m, c)
    head = re.split(r"[的之：:]", c.title)[-1].strip()
    heads = [head] + ([head[1:]] if len(head) >= 3 and head[0] in "怪旧老新小大破" else [])
    found += [x for x in heads if x != c.title and x not in seen and words.count(x) < COMMON]
    return [x for x in dict.fromkeys(found) if len(x) >= 2]


# A meeting reaction that hands a thing over or out, not one that only keeps quiet about it.
DELIVERS = re.compile(r"指出|指给|指着|交给|交出|递给|递上|给他们|给调查员|拿给|拿出|翻出|找出|找到|翻到|说出|讲出|讲清|说清|透露|承认|告诉|解释|"
                      r"领.{0,8}看|带.{0,8}看|让.{0,6}(看|读|翻)|借给|送给|念给")


def _flags(when: str) -> list[str]:
    return [x.strip()[5:] for group in when.split("|") for x in group.split("&") if x.strip().startswith("flag:")]


def _waits_for(m: Module, piece: Piece, c: Clue) -> bool:
    """Whether the piece holds off until the clue is had: found (knows:players:clue:<id>), or the
    keeper's to rule. Its prerequisites met is enough only for a clue that takes nothing else: one
    that takes a roll or a social gate is not had because what comes before it is."""
    if piece.ruling or f"knows:players:clue:{c.id}" in piece.when.replace(" ", ""):
        return True
    rolled = bool(c.check) or bool(c.npc and any(n.id == c.npc and c.id in n.gates for n in m.npcs))
    needs = {r[5:] for r in c.requires if r.startswith("flag:")}
    return not rolled and bool(needs) and needs <= set(_flags(piece.when))


def progress(m: Module, flag: str) -> bool:
    """A flag that says what the investigators learned or got, not what anyone else knows."""
    return bool(PROGRESS.search(m.flags.get(flag, "")) or PROGRESS_ID.match(flag))


def findings(m: Module, chosen: list[Piece] | None = None) -> list[Finding]:
    """What of the added pieces (all the situation layer when none are given) gets round the
    module's conditions."""
    items = pieces(m) if chosen is None else chosen
    seen, words = visible(m), corpus(m)
    gated = [(c, why, marks(m, c, seen, words)) for c in m.clues if (why := gate(m, c))]
    own = {a.npc: {f for s in a.steps for f in _flags(s.does)} for a in m.agendas}
    out: list[Finding] = []
    for p in items:
        if p.kind == "knows":  # knowing it is there is not having found it: what it may not know is what the clue says
            from ..agents import guard

            said = next((c for c, why, _ in gated if c.npc != p.npc and guard.leaks(p.text, {c.id: c.text}, public=seen)), None)
            if said is not None:
                why = gate(m, said)
                out.append(Finding(p.ref, "note", f"{p.ref} 写出了线索 {said.id}「{said.title}」里的内容，那要{why}才拿得到；"
                                                  "他知道有这样东西、知道在哪可以写，知道里面写了什么要原作写明他读过或亲历过"))
        elif p.kind == "foreshadow" or (p.kind == "meet" and DELIVERS.search(p.text)):
            for c, why, words in gated:
                # A sign names the thing by its title; its words against the clue's are guard.leaks' (adapt.edits).
                names = [w for w in words if w in titles(m, c)] if p.kind == "foreshadow" else words
                hit = next((w for w in names if w in p.text), None)
                if hit is None or _waits_for(m, p, c):
                    continue
                if p.npc and p.npc == c.npc and (p.kind == "knows" or p.only_if.strip()):
                    continue  # its giver knows its own clue; telling it under a stated condition is the module's way
                # Found by words (「不指出日记」 names it too): a hint for the call that reads the original, never a verdict.
                out.append(Finding(p.ref, "warning", f"{p.ref} 提到「{hit}」，线索 {c.id}「{c.title}」要{why}才拿得到；"
                                                   f"写成{_as(p)}可能等于不用{why}就交了出去。若是这样，要么删掉，要么写成要守秘人裁定的结果"
                                                   f"（ruling: true），要么等线索找到以后（when 写 knows:players:clue:{c.id}）"))
                break
        if p.kind in ("meet", "backstage") and not p.only_if.strip() and not p.ruling:
            for flag in _flags(p.when):
                if progress(m, flag) and flag not in own.get(p.npc, set()):
                    out.append(Finding(p.ref, "warning", f"{p.ref} 等的标记 {flag}（{m.flags.get(flag, '')}）说的是调查员那边的进展："
                                                         "调查员有了、知道了，不等于东西到了他手里、他也知道了。"
                                                         "在 only_if 里写清实际依据：谁当面做了什么，他通过什么途径得知"))
                    break
    return out


def _as(p: Piece) -> str:
    return {"meet": "见面就做的事", "knows": "他已经知道的事", "foreshadow": "一眼能看到的端倪"}.get(p.kind, "安排")
