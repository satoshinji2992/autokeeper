"""Backstage rewrites: change what the players have not met, keep everything they have.

The director may rewrite a fact (a secret, a motive, the truth, an unfound clue, history no one
told them) or a private event, for a better story. What reached the table is frozen:

    1. the target is not revealed, and its words never came up in front of the players
       (knowledge.exposed over everything the save still holds that they were shown);
    2. a private event can be rewritten, a public one cannot; nor can an event that carried a
       revealed fact be retracted;
    3. every found clue the old version explained is still explained, by the new version
       (op.explains) or by another standing fact;
    4. the knowledge stays sound: who knows it are real NPCs, PLAYERS cannot be added backstage;
    5. the consistency hook (World.checker, an LLM in phase 2) gets the revealed facts about the
       same people and places as pins, and may add problems.

Settling updates what rests on it: the important NPCs' memories of it (rewritten or marked
stale), their goals if given, private events quoting it, the threads it is in. Plans and
director requests that read its old version turn stale by its version alone. The revision log
keeps old and new; the module projection follows on World.apply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import knowledge
from .record import PLAYERS, Fact, Revision

if TYPE_CHECKING:
    from .ops import Rewrite
    from .store import World


def _seen(w: World) -> tuple[set[str], str]:
    from ..agents.guard import grams

    texts = knowledge.shown(w.m, w.st, w.rec)
    return set().union(*(grams(t) for t in texts)) if texts else set(), "\n".join(texts)


def pins(w: World, about: list[str]) -> list[Fact]:
    """Revealed facts about the same people and places: what a rewrite must not contradict."""
    near = set(about)
    return [f for f in w.rec.facts.values() if f.revealed is not None and not f.retracted and (not near or near & set(f.about))]


def check(w: World, op: Rewrite) -> list[str]:
    kind, _, ident = op.target.partition(":")
    if not op.retract and not op.text.strip():
        return ["改写要写新的内容；要取消这件事用 retract"]
    if len(op.text) > 600:
        return ["改写的内容不超过 600 字"]
    seen, joined = _seen(w)
    problems: list[str] = []
    about: list[str] = []
    if kind == "fact":
        f = w.rec.facts.get(ident)
        if f is None or f.retracted:
            return [f"事实 {ident} 不存在"]
        if f.revealed is not None:
            return [f"事实 {ident} 玩家已经知道了，不能改"]
        if knowledge.exposed(f.text, seen, joined):
            return [f"事实 {ident} 的内容已经在玩家面前出现过，不能改"]
        about = f.about
        found = set(w.st.clues)
        keeps = [] if op.retract else (op.explains if op.explains is not None else f.explains)
        for c in f.explains:
            if c in found and c not in keeps:
                others = [g for g in w.rec.facts.values() if g.id not in (f.id, f"clue:{c}") and not g.retracted and c in g.explains]
                if not others:
                    problems.append(f"已经找到的线索 {c} 会失去解释：新版本要能解释它（写进 explains）")
        problems += [f"线索 {c} 不存在" for c in (op.explains or []) if f"clue:{c}" not in w.rec.facts]
    elif kind == "event":
        e = w.rec.event(ident)
        if e is None:
            return [f"事件 {ident} 不存在"]
        if e.visibility == "public":
            return [f"事件 {ident} 玩家亲历过，不能改"]
        if knowledge.exposed(e.text, seen, joined):
            return [f"事件 {ident} 的内容已经在玩家面前出现过，不能改"]
        if op.retract and any((g := w.rec.facts.get(fid)) and g.revealed is not None for fid in e.facts):
            problems.append(f"事件 {ident} 带出的事实玩家已经知道，不能取消")
        about = [*e.participants, e.place]
    else:
        return ["target 要写成 fact:<id> 或 event:<id>"]
    if op.known_by is not None:
        if PLAYERS in op.known_by:
            problems.append("改写不能直接让调查员知道；要透露给玩家用 learn")
        problems += [f"{x} 不是 NPC" for x in op.known_by if x != PLAYERS and (x not in w.rec.entities or w.rec.entities[x].kind != "npc")]
    problems += [f"{x} 不是重要 NPC" for x in [*op.goals, *op.memories] if x not in w.rec.agents]
    if not problems and w.checker is not None:
        problems += list(w.checker(w, op, pins(w, about)))
    return problems


def settle(w: World, op: Rewrite, oid: str):
    rec = w.rec
    kind, _, ident = op.target.partition(":")
    touched: list[str] = []
    retold: dict[str, str] = {}  # event id -> its new text
    if kind == "fact":
        f = rec.facts[ident]
        old, old_version = f.text, f.version
        f.text = op.text or f.text
        f.retracted = op.retract
        if op.known_by is not None:
            f.known_by = list(dict.fromkeys(op.known_by))
        if op.explains is not None:
            f.explains = list(op.explains)
        w.touch(f)
        quoting = {e.id for e in rec.events if ident in e.facts}
        for e in rec.events:  # private history that quoted the old words now says the new ones
            if e.visibility == "private" and old and old in e.text:
                e.text = e.text.replace(old, f.text if not op.retract else "（并无此事）")
                e.version += 1
                retold[e.id] = e.text
                touched.append(f"event:{e.id}")
        new, new_version = ("" if op.retract else f.text), f.version
        memory_of = lambda mem: mem.fact == ident or mem.event in quoting or mem.event in retold  # noqa: E731
    else:
        e = rec.event(ident)
        assert e is not None
        old, old_version = e.text, e.version
        e.text = "（并无此事）" if op.retract else op.text
        e.version += 1
        new, new_version = e.text, e.version
        retold[e.id] = e.text
        memory_of = lambda mem: mem.event == ident  # noqa: E731
    for npc, a in rec.agents.items():
        hit = False
        for mem in a.memory:
            if memory_of(mem):
                if npc in op.memories:
                    mem.text, mem.stale = op.memories[npc], False
                elif mem.event in retold and kind == "fact":  # it remembers the event as it now stands
                    mem.text = retold[mem.event]
                else:
                    mem.stale = True
                hit = True
        if npc in op.goals:
            a.goals = list(op.goals[npc])
            hit = True
        if hit:
            w.touch(a)
            touched.append(f"agent:{npc}")
    for th in rec.threads.values():
        if ident in th.facts and th.status == "open":
            w.touch(th)
            touched.append(f"thread:{th.id}")
    rec.revisions.append(Revision(id=f"r{len(rec.revisions) + 1}", seq=rec.seq, t=w.now, turn=w.st.turn, target=op.target, old=old,
                                  new=new, old_version=old_version, new_version=new_version, reason=op.reason, by=op.actor,
                                  touched=touched))  # fmt: skip
    event = w.emit("rewrite", f"改写 {op.target}：{op.reason or '为了故事'}", actor=op.actor, visibility="private", witnesses=[], op=oid)
    return event, rec.revisions[-1]
