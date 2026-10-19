"""Backstage rewrites: a better story behind what the players met, never a messier record.

The director may rewrite a fact (a secret, a motive, the truth, an unfound clue, history no one
told them) or an event, for a smoother game or sharper conflict (the user's call, 2026-10-20:
even what they saw may turn out staged, as long as it holds together). What the players met
stays what they met; only the truth behind it moves:

    1. a fact the players heard, read in the narration or saw needs a reason saying how they
       came to get the old version (a lie, a mistake, a forgery, a set-up); the old words stay
       theirs as a told: fact (knowledge.keep_told) and the fact goes back to unknown;
    2. the words of a found clue, and what they were told (told: facts), never change: what
       is in their hands stays in their hands. A public event keeps its text too; a rewrite
       of it records the truth behind it as a secret, and it cannot be retracted;
    3. every found clue the old version explained is still explained, by the new version
       (op.explains) or by another standing fact;
    4. the knowledge stays sound: who knows it are real NPCs, PLAYERS cannot be added backstage;
    5. the consistency hook (World.checker; in play, the model check in engine/game._consistent)
       gets the revealed facts about the same people and places as pins, and may add problems.

A rewrite never moves anyone, never changes who is with whom or holds what: those are ops of
their own, so where people are stays exactly as play left it.

Settling updates what rests on it: the important NPCs' memories of it (rewritten or marked
stale), their goals if given, private events quoting it, the threads it is in. Plans and
director requests that read its old version turn stale by its version alone. The revision log
keeps old and new; the module projection follows on World.apply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import knowledge
from .record import PLAYERS, Fact, Memory, Revision

if TYPE_CHECKING:
    from .ops import Rewrite
    from .store import World


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
    at = knowledge.table(w.m, w.st, w.rec)
    problems: list[str] = []
    about: list[str] = []
    if kind == "fact":
        f = w.rec.facts.get(ident)
        if f is None or f.retracted:
            return [f"事实 {ident} 不存在"]
        if f.legacy_revealed is not None:
            return [f"事实 {ident} 的历史公开标记尚未核验，暂不能改；请先检查公开记录"]
        if ident.startswith("clue:") and ident.removeprefix("clue:") in w.st.clues:
            return [f"线索 {ident.removeprefix('clue:')} 已经在玩家手里，原文不能改；要改它的意思，改它背后的事"]
        if f.id.startswith("told:"):
            return [f"事实 {ident} 是玩家当初听到、看到的样子，不能改"]
        if (f.revealed is not None or knowledge.exposed(f.text, at)) and not op.reason.strip():
            return [f"玩家已经知道「{f.text[:40]}」（{knowledge.how_known(w, f, at) or '见过'}）：改写要在 reason 里写清"
                    "他们当初为什么会听到、看到那样（有人撒谎、记错、伪造、设局），其他已知的事也要说得通"]
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
        met = e.visibility == "public" or knowledge.exposed(e.text, at)
        if met and op.retract:
            return [f"事件 {ident} 玩家亲历过，不能取消；要说明它其实是另一回事，用 text 写出背后的真相"]
        if met and not op.reason.strip():
            return [f"事件 {ident} 玩家亲历过：改写要在 reason 里写清他们看到的为什么是那样（设局、假扮、伪造）"]
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
    behind: Fact | None = None  # the truth behind an event the players lived through
    if kind == "fact":
        f = rec.facts[ident]
        old, old_version = f.text, f.version
        at = knowledge.table(w.m, w.st, rec)
        if f.revealed is not None or knowledge.exposed(f.text, at):  # what they were told stays theirs
            touched.append(f"fact:{knowledge.keep_told(w, f, at).id}")
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
        if e.visibility == "public" or knowledge.exposed(e.text, knowledge.table(w.m, w.st, rec)):
            # What they lived through keeps its words; the truth behind it is a secret of those in it.
            behind = Fact(id=f"behind:{e.id}:{e.version}", text=op.text, kind="secret", origin="game", t=w.now,
                          about=[x for x in (*e.participants, e.place) if x],
                          known_by=list(dict.fromkeys(op.known_by if op.known_by is not None else
                                                      [*(x for x in e.participants if x in rec.entities and rec.entities[x].kind == "npc"),
                                                       *op.memories])))
            rec.facts[behind.id] = behind
            touched.append(f"fact:{behind.id}")
            new, new_version = behind.text, e.version
            memory_of = lambda mem: False  # noqa: E731
        else:
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
        if behind is not None and npc in op.memories:
            a.memory.append(Memory(t=w.now, turn=w.st.turn, text=op.memories[npc], fact=behind.id))
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
