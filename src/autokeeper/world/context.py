"""What each role is given of the world, and nothing more.

    director   the whole slice: important NPCs and their plans, the tension ledger with its
               hints, promises, relations, unrevealed facts with their versions (for the
               reads of a backstage plan), pending schedules and requests, recent revisions
    keeper     the scene as the players can see it: who is here now, traces of what happened
               here while they were away, what the table knows, public promises and events;
               and behind the screen (keeper_brief), what this place hides, what the people
               here and next door hide and mean to do, what is coming, what was rewritten
    npc        its own sheet, goals, plan, memories, what it knows, its relations, promises,
               things and places; never a fact it does not know
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..engine.state import format_time
from . import agents, knowledge, threads
from .record import PLAYERS, Event, Fact, Memory, WorldRecord

if TYPE_CHECKING:
    from ..engine.state import GameState
    from ..module.schema import Module
    from .store import World


@dataclass
class View:
    text: str
    facts: list[str] = field(default_factory=list)  # fact ids it shows
    reads: dict[str, int] = field(default_factory=dict)  # versions it was made from
    seen: int = 0  # event boundary actually available when this view was captured


def _when(t: int | None) -> str:
    return "不限时" if t is None else format_time(t)


def _company(w: World, ent) -> str:
    """Whose company an NPC is in: to an agreed place, or along wherever they go (world/scene.py)."""
    return f"与{w.name(ent.following)}去{w.name(ent.follow_to)}" if ent.follow_to else f"跟着{w.name(ent.following)}走"


def _terms(text: str) -> set[str]:
    """Small, deterministic lexical recall; never expands a query into new information."""
    terms = set(re.findall(r"[a-z0-9_]{2,}", text.casefold()))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(run[i:i + width] for width in (2, 3) for i in range(len(run) - width + 1))
    return terms - {"调查", "查员", "调查员", "自己", "刚才", "这个", "那个", "什么", "一个", "没有", "知道"}


def _recall_indices(texts: list[str], query: str, concerns: str, *, recent: int, related: int) -> list[int]:
    if len(texts) <= recent + related:
        return list(range(len(texts)))
    focus, background = _terms(query), _terms(concerns)
    terms = [_terms(text) for text in texts]
    counts = Counter(term for row in terms for term in row)
    selected = set(range(max(0, len(texts) - recent), len(texts)))
    scores = []
    for index, row in enumerate(terms):
        if index in selected:
            continue
        # Common names and phrases cannot crowd out a specific question. The current
        # perceived message has priority over broad goals shared by many old records.
        score = sum(6 / counts[term] for term in row & focus) + sum(1 / counts[term] for term in row & background)
        if score:
            scores.append((score, index))
    selected.update(index for _, index in sorted(scores, reverse=True)[:related])
    return sorted(selected)


def _concerns(w: World, npc: str) -> str:
    a = w.agent(npc)
    parts = [w.name(x) for x in w.present(w.place(npc)) if x != npc]
    if w.place(npc) == w.st.location:
        parts += [i.name for i in w.st.investigators]
    if a:
        parts += [*a.goals, *(s.text for s in a.plan if s.status in {"pending", "blocked"})]
    parts += [p.text for p in w.rec.promises.values() if npc in {p.by, p.to} and p.status == "active"]
    return "\n".join(parts)


def _npc_facts(w: World, npc: str, query: str, concerns: str) -> list[Fact]:
    facts = sorted(knowledge.known(w.rec, npc), key=lambda f: f.t)
    if len(facts) <= 30:
        return facts
    keep = set(_recall_indices([f.text for f in facts], query, concerns, recent=12, related=12))
    # Its original identity, motives and secrets are the basis of roleplay, rather than
    # disposable recent notes. Permission filtering already happened before selection.
    keep.update(i for i, fact in enumerate(facts) if fact.id.startswith(f"npc:{npc}:"))
    return [fact for i, fact in enumerate(facts) if i in keep]


def _fact_line(npc: str, fact: Fact, sources: dict[str, Event], w: World) -> str:
    if fact.kind == "lie" and npc in fact.about:
        label = "这是你编的"
    elif fact.kind == "rumor":
        label = "传闻，尚未证实"
    elif (event := sources.get(fact.id)) is not None:
        label = (f"听说，来源：{w.name(event.actor)}" if event.kind == "learn" and event.actor not in {npc, "engine", "director", "keeper"}
                 else "来自亲历或当面获知的记录")
    else:
        label = "原有知识" if fact.origin == "module" else "已登记知识，来源未标注"
    return f"- {knowledge.public_ref(fact, npc)}：{fact.text}（{label}）"


def _npc_memories(w: World, npc: str, query: str, concerns: str, *, recent: int) -> tuple[list[str], dict[str, Event]]:
    by_event = {e.id: e for e in w.rec.events}
    events = [e for e in w.rec.events if knowledge.saw(e, npc) and e.kind not in {"plan", "step", "remember"}]
    sources = {fid: event for event in events for fid in event.facts}
    entries = {f"event:{event.id}": Memory(t=event.t, turn=event.turn, text=event.text, event=event.id) for event in events}
    a = w.agent(npc)
    if a:
        for index, memory in enumerate(a.memory):
            if memory.stale or (memory.fact and (memory.fact not in w.rec.facts or not knowledge.sees(w.rec.facts[memory.fact], npc))):
                continue
            if memory.event and memory.event in by_event:
                continue  # the authoritative visible event is already present, or was not available to this NPC
            entries[f"memory:{index}"] = memory
    memories = sorted(entries.values(), key=lambda m: (m.t, m.turn))
    selected = _recall_indices([m.text for m in memories], query, concerns, recent=recent, related=6)
    lines = []
    for index in selected:
        memory = memories[index]
        event = by_event.get(memory.event)
        if event is not None:
            if event.kind == "relate":
                label = "个人态度记录"
            else:
                label = (f"听说，来源：{w.name(event.actor)}" if event.kind == "learn" and event.actor not in {npc, "engine", "director", "keeper"}
                         else f"亲历或当面获知，记录者：{w.name(event.actor)}")
        elif re.search(r"怀疑|猜测|推测|可能|似乎|认为|觉得", memory.text):
            label = "个人判断，尚未证实"
        else:
            label = "个人记录，来源未标注"
        lines.append(f"- {format_time(memory.t)} [{label}] {memory.text}")
    return lines, sources


def npc_view(w: World, npc: str, query: str = "") -> View:
    rec = w.rec
    n = next((x for x in w.m.npcs if x.id == npc), None)
    ent = rec.entities[npc]
    a = rec.agents.get(npc)
    here = w.place(npc)
    lines = [f"【你是】{ent.name}（{npc}）" + (f"，{n.role}" if n else "") + (f"。性格：{n.personality}" if n and n.personality else "")]
    if ent.status or ent.conditions:
        lines.append(f"【你的状况】{'、'.join(x for x in [ent.status, *ent.conditions] if x)}")
    if ent.following:
        lines.append(f"【已同意同行】{_company(w, ent)}；改变主意须明确结束同行，不忽略刚才的约定。")
    others = [f"{w.name(x)}（{x}）" for x in w.present(here) if x != npc]
    if here == w.st.location:
        others += [f"{i.name}（{i.id}，调查员）" for i in w.st.investigators]
    lines.append(f"【你在】{w.name(here)}（{here}）；身边：{'、'.join(others) or '没有人'}")
    if a:
        lines.append("【你的目标】" + ("；".join(a.goals) or "无"))
        if a.mood:
            lines.append(f"【你的心境】{a.mood}")
        # What it does on meeting the investigators is its voice's (npc_brief), not a backstage step.
        steps = [f"- {s.id}：{s.text}（{_when(s.due)}{'，条件 ' + s.when if s.when else ''}，{s.status}）"
                 for s in a.plan if s.status != "dropped" and not agents.meeting(w, npc, s)]
        lines.append("【你的计划】\n" + ("\n".join(steps) or "- 还没有"))
    concerns = _concerns(w, npc)
    facts = _npc_facts(w, npc, query, concerns)
    memories, sources = _npc_memories(w, npc, query, concerns, recent=12)
    lines.append("【你知道的事】\n" + ("\n".join(_fact_line(npc, f, sources, w) for f in facts) or "- 无"))
    lines.append("【你的记忆（近期与当前相关旧事）】\n" + ("\n".join(memories) or "- 无"))
    witnessed = [e for e in rec.events if (npc in e.participants or npc in e.witnesses)
                 and e.kind not in {"plan", "step", "remember"}][-6:]
    if witnessed:
        lines.append("【近期亲历或当面获知】\n" + "\n".join(
            f"- {format_time(e.t)} {w.name(e.actor)}（{e.actor}）：{e.text}" for e in witnessed))
    if here == w.st.location:
        lines.append("【眼前的交流】调查员在场。可回应刚听到的话、提出与自身目标有关的帮助或请求；其未说出的意图不在你的信息内。")
    rels = [r for r in rec.relations.values() if npc in (r.a, r.b) and npc in r.known_by]
    lines.append("【你和别人】\n" + ("\n".join(f"- {w.name(r.a)}对{w.name(r.b)}：{r.kind} {r.value}" + (f"（{r.note}）" if r.note else "")
                                            for r in rels) or "- 无"))  # fmt: skip
    proms = [p for p in rec.promises.values() if npc in (p.by, p.to)]
    lines.append("【约定】\n" + ("\n".join(f"- {p.id}：{w.name(p.by)}答应{w.name(p.to)}：{p.text}（{_when(p.due)}，{p.status}）"
                                          for p in proms) or "- 无"))  # fmt: skip
    equipment_names = {"carried": "携带", "worn": "穿戴", "held": "手持", "concealed": "藏匿"}
    held = [f"{e.name}（{e.id}，{equipment_names[e.equipment]}，数量{e.quantity}"
            + (f"，{e.status}" if e.status else "") + "）" for e in knowledge.visible_items(w, npc, holder=npc)]
    lines.append(f"【自己的重要装备】{'、'.join(held) or '未登记'}")
    lines.append("【你知道的地方】" + "、".join(f"{w.name(p)}（{p}）" for p in sorted(agents.places(w, npc))))
    reads = ({f"fact:{f.id}": f.version for f in facts} | {f"promise:{p.id}": p.version for p in proms}
             | {f"entity:{npc}": ent.version} | {f"relation:{r.key}": r.version for r in rels}
             | {f"entity:{e.id}": e.version for e in rec.entities.values() if e.kind == "item" and e.at == npc}
             | {f"entity:{other}": rec.entities[other].version for other in w.present(here)}
             | ({f"agent:{npc}": a.version} if a else {}))
    return View(knowledge.public_text(w, npc, "\n".join(lines)), [f.id for f in facts], reads, seen=w.rec.seq)


# Events a player can see happen. A relation's numbers, a flag's meaning, plans and threads are
# the backstage's own bookkeeping, even when the party stood there.
SHOWN = {"occur", "move", "transfer", "equip", "condition", "promise", "fulfil", "break", "fact", "learn"}


# How a meeting reaction stands (Step.once): done once, or the NPC's way that comes up now and then.
ONCE = {True: "，只做一次，做过填进 played", False: "，一贯如此，碰上了才带出，不必每次都演"}


def npc_brief(w: World, npc: str, query: str = "") -> str:
    """What an NPC brings to a conversation of its own life in this game: how it is, what it is
    after and up to, what it remembers, how it stands with people, what it promised. The rest of
    its sheet (what it knows, its secrets) is in its system prompt, projected from the record."""
    rec = w.rec
    ent, a = rec.entities.get(npc), rec.agents.get(npc)
    if ent is None:
        return ""
    lines = []
    if ent.status or ent.conditions:
        lines.append(f"【你的状况】{'、'.join(x for x in [ent.status, *ent.conditions] if x)}")
    if ent.following:
        lines.append(f"【已同意同行】{_company(w, ent)}。")
    if a:
        if a.goals:
            lines.append("【你眼下想要的】" + "；".join(a.goals))
        if a.mood:
            lines.append(f"【你的心境】{a.mood}")
        pending = [s for s in a.plan if s.status == "pending"]
        steps = [s.text + (f"（完成须满足 {s.achieved}）" if s.achieved else "") for s in pending if not agents.meeting(w, npc, s)][:3]
        if steps:
            lines.append("【你正打算】" + "；".join(steps))
        meet = [f"{s.text}（{s.id}{ONCE.get(s.once, '')}）" for s in pending if agents.meets(w, npc, s)][:4]
        if meet and ent.at == w.st.location:  # what it does on meeting them: its own to play now
            lines.append("【见到调查员时，你会】" + "；".join(meet))
    concerns = _concerns(w, npc)
    memories, sources = _npc_memories(w, npc, query, concerns, recent=6)
    if memories:
        lines.append("【你记得（近期与当前相关旧事）】\n" + "\n".join(memories))
    facts = [f for f in _npc_facts(w, npc, query, concerns) if not f.id.startswith(f"npc:{npc}:") or f.origin != "module"]
    if facts:
        lines.append("【当前相关的已知资料】\n" + "\n".join(_fact_line(npc, fact, sources, w) for fact in facts))
    rels = sorted((r for r in rec.relations.values() if r.a == npc and npc in r.known_by), key=lambda r: -abs(r.value))[:5]
    if rels:
        lines.append("【你对别人（-100 到 100）】" + "；".join(f"{w.name(r.b)}：{r.kind} {r.value:+d}" + (f"（{r.note}）" if r.note else "")
                                                         for r in rels))  # fmt: skip
    # Its promises to and from the investigators are in the game's ledger already (engine/world.context).
    proms = [p for p in rec.promises.values() if npc in (p.by, p.to) and p.status == "active" and p.id not in w.st.world]
    if proms:
        lines.append("【还没了结的约定】" + "；".join(f"{w.name(p.by)}答应{w.name(p.to)}：{p.text}（{_when(p.due)}）" for p in proms))
    # The conversation session may be compacted; the current record remains the source
    # for what the character actually witnessed, and what happened after its last reply.
    recent = [e for e in rec.events if npc in e.participants or npc in e.witnesses][-5:]
    if recent:
        lines.append("【近期亲历或当面获知】\n" + "\n".join(
            f"- {format_time(e.t)} {w.name(e.actor)}：{e.text}" for e in recent
            if e.kind not in {"plan", "step", "remember"}))
    if ent.at == w.st.location:
        lines.append("【当前交流】调查员就在身边；按本轮实际听见的话回应，可以提出自己的帮助或请求，不能知道其未说出的打算。")
    return knowledge.public_text(w, npc, "\n".join(lines))


def rooms(m: Module, place: str) -> set[str]:
    """The other rooms of the building the party is in: places inside the same private place (a
    house and its study, a cemetery and its crypt), the building itself among them. A town's
    public quarter is no building: the library is not near the main street."""
    locs = {loc.id: loc for loc in m.locations}
    inside = {x: loc.within for x, loc in locs.items() if loc.within and loc.within in locs}
    top = inside.get(place) or place
    if top not in locs or locs[top].public:
        return set()
    return ({top} | {x for x, outer in inside.items() if outer == top}) - {place}


def nearby(w: World, place: str) -> dict[str, str]:
    """NPC id -> the room of this building they are in: not in sight, not gone either."""
    near = rooms(w.m, place)
    return {e.id: e.at for e in w.rec.entities.values() if e.kind == "npc" and e.at in near and e.status not in ("死亡", "失踪")}


def traces(w: World, at: str) -> list[str]:
    """What can still be noticed at a place of what went on there since the party last left it."""
    since = w.rec.left.get(at, -1)
    found = [e.trace for e in w.rec.events if e.place == at and e.trace and e.visibility == "private" and e.seq > since]
    return list(dict.fromkeys(found))[-5:]


def arrival(w: World, at: str) -> list[str]:
    """What the keeper is told when the party comes to a place: who is there now, and what can
    still be noticed of what went on there while they were away (the traces left since)."""
    rec = w.rec
    people = [w.name(x) + (f"（{rec.entities[x].status}）" if rec.entities[x].status else "") for x in w.present(at)]
    lines = [f"此刻在这里的人（以这一条为准）：{'、'.join(people) or '没有别人'}"]
    if left := traces(w, at):
        lines.append("这里看得出来的变化（他们不在时留下的）：" + "；".join(left))
    return lines


def happened(w: World, since: int) -> list[str]:
    """What the world did in front of the party since a record seq: an NPC who walked in, a
    scheduled thing that came about where they stand. Told by the keeper as it happens."""
    return [e.text for e in w.rec.events if e.seq > since and e.visibility == "public" and e.kind in SHOWN
            and e.actor not in {i.id for i in w.st.investigators}]  # fmt: skip


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
        at_table = knowledge.table(w.m, st, rec) if any(f.id.startswith("told:") for f in told) else None
        how = {f.id: knowledge.how_known(w, f, at_table) for f in told if at_table and f.id.startswith("told:")}
        lines.append("【调查员已经知道】\n" + "\n".join(f"- {f.text}" + (f"（{how[f.id]}）" if how.get(f.id) else "") for f in told))
    party = {PLAYERS, *(i.id for i in st.investigators)}
    proms = [p for p in rec.promises.values() if p.public and p.status == "active" and party & {p.by, p.to}]
    if proms:
        lines.append("【还没兑现的约定】\n" + "\n".join(f"- {w.name(p.by)}答应{w.name(p.to)}：{p.text}（{_when(p.due)}）" for p in proms))
    seen = [e for e in rec.events if e.visibility == "public"][-8:]
    if seen:
        lines.append("【调查员亲历的近事】\n" + "\n".join(f"- {format_time(e.t)} {e.text}" for e in seen))
    return "\n".join(lines)


BRIEF_ITEM = 160  # characters of one secret, plan step or note in the keeper's brief
BRIEF_SOON = 4  # coming things listed


def _clip(text: str, n: int = BRIEF_ITEM) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def rewrites(rec: WorldRecord) -> list[str]:
    """Every fact or event rewritten in this game, once each, however long ago and however many
    rewrites came after: what it first said, and the whole of what it says now."""
    rows = []
    for target in dict.fromkeys(r.target for r in rec.revisions):
        first = next(r for r in rec.revisions if r.target == target)
        kind, _, ident = target.partition(":")
        if kind == "fact":
            f = rec.facts.get(ident)
            now = "（已撤回，没有这回事）" if f is None or f.retracted or not f.text.strip() else f.text.strip()
        else:
            e = rec.event(ident)
            now = "（已撤回，没有这回事）" if e is None or not e.text.strip() else e.text.strip()
        rows.append(f"- 原来：{_clip(first.old, 60)} → 现在：{now}")
    return rows


def _ready(w: World, when: str) -> bool:
    """Whether a module step's time has come and its condition holds."""
    from ..engine.state import parse_time

    if re.fullmatch(r"D\d+ \d{1,2}:\d{2}", when.strip()):
        return parse_time(when.strip()) <= w.now
    return agents.holds(w, when)


def apart(st: GameState, name: Callable[[str], str]) -> str:
    """The investigators a chase left elsewhere than the party (GameState.apart): who, where (`name` of
    the place), and how they are when they cannot get up; "" for none."""
    rows = []
    for inv_id, place in st.apart.items():
        inv = next((i for i in st.investigators if i.id == inv_id), None)
        if inv is None:
            continue
        how = "、".join(c for c in inv.conditions if c in ("死亡", "濒死", "昏迷", "重伤"))
        rows.append(f"{inv.name}在{name(place)}" + (f"（{how}）" if how else ""))
    return "；".join(rows)


def keeper_brief(w: World) -> str:
    """What the keeper keeps behind the screen for the telling it is about to give (the user's
    call, 2026-10-20: the keeper sees everything): what this place hides, what the people here and
    in the other rooms of the building hide and mean to do, what is coming, what was rewritten.
    Read from the record, so a rewrite shows; sent with each telling and never kept in the
    keeper's memory. What of it reaches the players stays the keeper's call under its rules."""
    rec, st, m = w.rec, w.st, w.m
    here = st.location
    lines: list[str] = []
    note = rec.facts.get(f"location:{here}:keeper")
    if note is not None and not note.retracted and note.text.strip():
        lines.append(f"此地（{w.name(here)}）的守秘人须知：{' '.join(note.text.split())}")  # whole: what it says last counts too

    def person(npc: str) -> str:
        own = [f for f in rec.facts.values() if f.id.startswith(f"npc:{npc}:") and not f.retracted and f.text.strip()]
        goal = next((f.text for f in own if f.kind == "goal"), "")
        secrets = [_clip(f.text) for f in own if f.kind == "secret" and f.revealed is None]
        lies = [_clip(f.text) for f in own if f.kind == "lie"]
        parts = [f"想要：{_clip(goal)}"] if goal else []
        parts += [f"秘密：{'；'.join(secrets)}"] if secrets else []
        parts += [f"会说的谎：{'；'.join(lies)}"] if lies else []
        things = [e.name for e in rec.entities.values() if e.kind == "item" and e.at == npc]  # what is on them, everyday or not
        parts += [f"身上有：{'、'.join(things)}"] if things else []
        if (a := rec.agents.get(npc)) is not None:
            steps = [f"{_clip(s.text, 80)}（{_when(s.due) if s.due is not None else s.when or '想到就做'}）"
                     for s in a.plan if s.status == "pending"][:3]
            parts += [f"打算：{'；'.join(steps)}"] if steps else []
            parts += [f"心境：{_clip(a.mood, 60)}"] if a.mood else []
        # What may come of a ruling of the keeper's (Move.ruling): not the NPC's to do, the keeper's to decide.
        ruled = [_clip(s.text, 100) + (f"（只在{s.only_if}时）" if s.only_if else "") for g in m.agendas if g.npc == npc
                 for s in g.steps if s.ruling and _ready(w, s.when)]
        parts += [f"要你裁定以后才可能的：{'；'.join(ruled)}"] if ruled else []
        return "；".join(parts) or "没有特别的事"

    present = w.present(here)
    if present:
        lines.append("在场的人：\n" + "\n".join(f"- {w.name(x)}：{person(x)}" for x in present))
    if left := apart(st, w.name):
        lines.append(f"和大家分开的调查员（追逐后落在别处，不在这里；下次移动时归队）：{left}")
    near = nearby(w, here)
    if near:
        lines.append("同一处别的房间里：\n" + "\n".join(f"- {w.name(x)}（在{w.name(room)}）：{person(x)}" for x, room in near.items()))
    hidden = [c for c in m.clues if c.where == here and c.id not in st.clues]
    if hidden:
        lines.append("这里还没被找到的东西（别先写出来，也别把它所在的地方写成空的）：" + "；".join(
            f"{c.title}（{c.feature or '此处'}）" for c in hidden))
    soon = [(s.at, _clip(s.text)) for s in rec.scheduled.values() if s.status == "pending" and s.at >= w.now]
    soon += [(s.due, f"{w.name(npc)}：{_clip(s.text)}") for npc, a in rec.agents.items()
             for s in a.plan if s.status == "pending" and s.due is not None and s.due >= w.now]
    if soon:
        lines.append("接下来会发生的事（还没发生，别提前写成发生了）：\n" + "\n".join(
            f"- {format_time(t)} {text}" for t, text in sorted(soon)[:BRIEF_SOON]))
    # The keeper's book (agents/cast._book) is written once, when its session opens: whatever of it
    # was rewritten since stands here whole, as it is now, however many other rewrites came after.
    book = [t for t in dict.fromkeys(r.target for r in rec.revisions)
            if t in ("fact:truth",) or t.startswith(("fact:conclusion:", "event:past:"))]
    if book:
        rows = []
        for target in book:
            kind, _, ident = target.partition(":")
            if kind == "fact":
                f = rec.facts.get(ident)
                label = "真相" if ident == "truth" else f"结论 {ident.removeprefix('conclusion:')}"
                now = "（已撤回，没有这回事）" if f is None or f.retracted or not f.text.strip() else f.text.strip()
            else:
                e = rec.event(ident)
                label, now = "前史", "（已撤回，没有这回事）" if e is None or not e.text.strip() else e.text.strip()
            rows.append(f"- {label}：{now}")
        lines.append("底牌里改过的（以这里为准，代替开头资料里的同一条）：\n" + "\n".join(rows))
    if rec.revisions:
        lines.append("改写过的设定（以改写后的为准）：\n" + "\n".join(
            f"- {_clip(r.old, 60)} → {_clip(r.new, 120)}" for r in rec.revisions[-5:]))
    if not lines:
        return ""
    return ("【幕后（只给你看）】这些用来让描写和真相一致、让在场的人照自己的打算行事；对玩家说什么、露出多少，照硬规则。\n"
            + "\n".join(lines))


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
    hidden = [f for f in rec.facts.values() if f.revealed is None and f.legacy_revealed is None and not f.retracted and f.text][-30:]
    lines.append("【玩家还不知道的（可以改写）】\n" + "\n".join(f"- fact:{f.id}（v{f.version}）{f.text[:80]}" for f in hidden))
    known = [f for f in rec.facts.values() if f.revealed is not None and not f.retracted]
    held = [f for f in known if f.id.startswith("told:") or (f.id.startswith("clue:") and f.id.removeprefix("clue:") in w.st.clues)]
    met = [f for f in known if f not in held][-15:]
    at = knowledge.table(w.m, w.st, rec)
    lines.append("【玩家已经知道的（可以为阴谋或剧情改写，但要自圆其说）】\n" + ("\n".join(
        f"- fact:{f.id}（v{f.version}，{knowledge.how_known(w, f, at) or '见过'}）{f.text[:80]}" for f in met) or "- 无"))
    pins = held[-15:]
    lines.append("【玩家手里的线索原文、当初听到看到的样子（不能改）】\n" + ("\n".join(f"- {f.text[:80]}" for f in pins) or "- 无"))
    uncertain = [f for f in rec.facts.values() if f.legacy_revealed is not None and not f.retracted]
    if uncertain:
        lines.append("【历史公开待核验（不能向玩家补述，暂不能改写）】\n" + "\n".join(
            f"- fact:{f.id}：旧记录标为第 {f.legacy_revealed} 回合公开，但没有可追溯来源。" for f in uncertain))
    later = [s for s in rec.scheduled.values() if s.status == "pending"]
    if later:
        lines.append("【已经安排的事】\n" + "\n".join(f"- {s.id} {_when(s.at)}：{s.text}" for s in later))
    recent = rec.events[-12:]
    lines.append("【最近发生的事】\n" + ("\n".join(f"- {format_time(e.t)} event:{e.id}{'（玩家亲历）' if e.visibility == 'public' else ''} {e.text}"
                                              for e in recent) or "- 无"))
    if rec.revisions:
        lines.append("【最近的改写】\n" + "\n".join(f"- {r.target}：{r.old[:40]} → {r.new[:40]}" for r in rec.revisions[-5:]))
    refs = {f"fact:{f.id}" for f in [*hidden, *met, *pins, *uncertain]}
    refs |= {f"agent:{npc}" for npc in rec.agents}
    refs |= {f"promise:{p.id}" for p in rec.promises.values()}
    refs |= {f"relation:{r.key}" for r in rels}
    refs |= {f"thread:{th.id}" for th, _, _ in rows}
    refs |= {f"event:{e.id}" for e in recent}
    refs |= {f"scheduled:{s.id}" for s in later}
    named = set(rec.agents)
    named |= {w.place(npc) for npc in rec.agents}
    named |= {x for p in rec.promises.values() for x in (p.by, p.to)}
    named |= {x for r in rels for x in (r.a, r.b)}
    refs |= {f"entity:{x}" for x in named if x in rec.entities}
    reads = {ref: w.version(ref) for ref in sorted(refs)}
    return View("\n".join(lines), [f.id for f in hidden], reads)

