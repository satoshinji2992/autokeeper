"""What each role is given of the world, and nothing more.

    director   the whole slice: important NPCs and their plans, the tension ledger with its
               hints, promises, relations, unrevealed facts with their versions (for the
               reads of a backstage plan), pending schedules and requests, recent revisions
    keeper     the scene as the players can see it: who is here now, traces of what happened
               here while they were away, what the table knows, public promises and events
    npc        its own sheet, goals, plan, memories, what it knows, its relations, promises,
               things and places; never a fact it does not know
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..engine.state import format_time
from . import agents, knowledge, threads
from .record import PLAYERS

if TYPE_CHECKING:
    from .store import World


@dataclass
class View:
    text: str
    facts: list[str] = field(default_factory=list)  # fact ids it shows
    reads: dict[str, int] = field(default_factory=dict)  # versions it was made from


def _when(t: int | None) -> str:
    return "不限时" if t is None else format_time(t)


def npc_view(w: World, npc: str) -> View:
    rec = w.rec
    n = next((x for x in w.m.npcs if x.id == npc), None)
    ent = rec.entities[npc]
    a = rec.agents.get(npc)
    here = w.place(npc)
    lines = [f"【你是】{ent.name}（{npc}）" + (f"，{n.role}" if n else "") + (f"。性格：{n.personality}" if n and n.personality else "")]
    if ent.status or ent.conditions:
        lines.append(f"【你的状况】{'、'.join(x for x in [ent.status, *ent.conditions] if x)}")
    others = [f"{w.name(x)}（{x}）" for x in w.present(here) if x != npc]
    if here == w.st.location:
        others += [f"{i.name}（{i.id}，调查员）" for i in w.st.investigators]
    lines.append(f"【你在】{w.name(here)}（{here}）；身边：{'、'.join(others) or '没有人'}")
    if a:
        lines.append("【你的目标】" + ("；".join(a.goals) or "无"))
        if a.mood:
            lines.append(f"【你的心境】{a.mood}")
        steps = [f"- {s.id}：{s.text}（{_when(s.due)}{'，条件 ' + s.when if s.when else ''}，{s.status}）" for s in a.plan if s.status != "dropped"]
        lines.append("【你的计划】\n" + ("\n".join(steps) or "- 还没有"))
    facts = sorted(knowledge.known(rec, npc), key=lambda f: f.t)[-30:]
    lines.append("【你知道的事】\n" + ("\n".join(f"- {f.id}：{f.text}" + ("（这是你编的）" if f.kind == "lie" else "") for f in facts) or "- 无"))
    if a:
        mem = [m for m in a.memory if not m.stale][-12:]
        lines.append("【你的记忆】\n" + ("\n".join(f"- {format_time(m.t)} {m.text}" for m in mem) or "- 无"))
    rels = [r for r in rec.relations.values() if npc in (r.a, r.b) and npc in r.known_by]
    lines.append("【你和别人】\n" + ("\n".join(f"- {w.name(r.a)}对{w.name(r.b)}：{r.kind} {r.value}" + (f"（{r.note}）" if r.note else "")
                                            for r in rels) or "- 无"))  # fmt: skip
    proms = [p for p in rec.promises.values() if npc in (p.by, p.to)]
    lines.append("【约定】\n" + ("\n".join(f"- {p.id}：{w.name(p.by)}答应{w.name(p.to)}：{p.text}（{_when(p.due)}，{p.status}）"
                                          for p in proms) or "- 无"))  # fmt: skip
    held = [f"{e.name}（{e.id}）" for e in rec.entities.values() if e.kind == "item" and e.at == npc]
    lines.append(f"【你手里】{'、'.join(held) or '没有登记的东西'}")
    lines.append("【你知道的地方】" + "、".join(f"{w.name(p)}（{p}）" for p in sorted(agents.places(w, npc))))
    reads = {f"fact:{f.id}": f.version for f in facts} | {f"promise:{p.id}": p.version for p in proms}
    return View("\n".join(lines), [f.id for f in facts], reads)


def keeper_view(w: World, at: str | None = None) -> str:
    """The scene and what the table knows; no secret, no plan."""
    rec, st = w.rec, w.st
    at = at or st.location
    people = [f"{w.name(x)}" + (f"（{rec.entities[x].status}）" if rec.entities[x].status else "") for x in w.present(at)]
    traces = [e.trace for e in rec.events if e.place == at and e.trace and e.visibility == "private"][-5:]
    lines = [f"【此刻在{w.name(at)}的人】{'、'.join(people) or '没有别人'}"]
    if traces:
        lines.append("【这里看得出来的变化】" + "；".join(traces))
    told = [f for f in rec.facts.values() if f.revealed is not None and f.origin != "module" and not f.retracted][-15:]
    if told:
        lines.append("【调查员已经知道】\n" + "\n".join(f"- {f.text}" for f in told))
    party = {PLAYERS, *(i.id for i in st.investigators)}
    proms = [p for p in rec.promises.values() if p.public and p.status == "active" and party & {p.by, p.to}]
    if proms:
        lines.append("【还没兑现的约定】\n" + "\n".join(f"- {w.name(p.by)}答应{w.name(p.to)}：{p.text}（{_when(p.due)}）" for p in proms))
    seen = [e for e in rec.events if e.visibility == "public"][-8:]
    if seen:
        lines.append("【调查员亲历的近事】\n" + "\n".join(f"- {format_time(e.t)} {e.text}" for e in seen))
    return "\n".join(lines)


def director_view(w: World) -> View:
    rec = w.rec
    lines = []
    for npc, a in rec.agents.items():
        steps = "；".join(f"{s.id} {s.text}（{_when(s.due)}，{s.status}）" for s in a.plan[-4:]) or "无"
        lines.append(f"【{w.name(npc)}（{npc}，v{a.version}）】在 {w.name(w.place(npc))}；目标：{'；'.join(a.goals) or '无'}；计划：{steps}"
                     + (f"；心境：{a.mood}" if a.mood else ""))  # fmt: skip
    rows = threads.ranked(w)
    lines.append("【线头】\n" + ("\n".join(f"- {th.id}（{th.kind}，热度 {th.heat}，{score}）{th.title}：{hint}" for th, score, hint in rows) or "- 无"))
    lines.append("【约定】\n" + ("\n".join(f"- {p.id}（v{p.version}）{w.name(p.by)}→{w.name(p.to)}：{p.text}（{_when(p.due)}，{p.status}）"
                                          for p in rec.promises.values()) or "- 无"))  # fmt: skip
    rels = sorted(rec.relations.values(), key=lambda r: -abs(r.value))[:12]
    lines.append("【关系】\n" + ("\n".join(f"- {w.name(r.a)}→{w.name(r.b)} {r.kind} {r.value}" for r in rels) or "- 无"))
    hidden = [f for f in rec.facts.values() if f.revealed is None and not f.retracted and f.text][-30:]
    lines.append("【玩家还不知道的（可以改写）】\n" + "\n".join(f"- fact:{f.id}（v{f.version}）{f.text[:80]}" for f in hidden))
    pins = [f for f in rec.facts.values() if f.revealed is not None and not f.retracted][-15:]
    lines.append("【玩家已经知道的（不能动）】\n" + ("\n".join(f"- {f.text[:80]}" for f in pins) or "- 无"))
    later = [s for s in rec.scheduled.values() if s.status == "pending"]
    if later:
        lines.append("【已经安排的事】\n" + "\n".join(f"- {s.id} {_when(s.at)}：{s.text}" for s in later))
    lines.append("【最近发生的事】\n" + ("\n".join(f"- {format_time(e.t)} {e.text}" for e in rec.events[-12:]) or "- 无"))
    if rec.revisions:
        lines.append("【最近的改写】\n" + "\n".join(f"- {r.target}：{r.old[:40]} → {r.new[:40]}" for r in rec.revisions[-5:]))
    reads = {f"fact:{f.id}": f.version for f in hidden} | {f"agent:{n}": a.version for n, a in rec.agents.items()}
    return View("\n".join(lines), [f.id for f in hidden], reads)

