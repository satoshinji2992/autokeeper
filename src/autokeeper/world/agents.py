"""Important NPCs as agents: what they may decide, what they may do, and who decides.

An NPC's decision (observe -> judge -> choose ops) is one structured call: a Scene with a
Decision per NPC. Several NPCs at one place offscreen are one call, one scene. The brain
only gets each NPC's own view (world/context.npc_view), and vet holds every op to what that
NPC knows and can reach: its own things, the people beside it, the places it knows.

Brains: Canned (tests, scripted playtests) and CastBrain (a stateless session on the "world"
model through agents/cast.py, counted in the save's usage as "mind").
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field

from ..agents import guard
from ..engine import world as legacy_world
from ..engine.state import hours, within
from . import knowledge
from .ops import (
    AdvancePlan,
    Break,
    Condition,
    Enlist,
    Equip,
    Follow,
    Fulfil,
    Learn,
    MakePromise,
    Move,
    Occur,
    Relate,
    Remember,
    Schedule,
    SetFlag,
    SetPlan,
    Transfer,
)
from .record import PLAYERS, Step

if TYPE_CHECKING:
    from .store import World

NpcOp = Annotated[
    Move | Follow | Relate | Transfer | Equip | Learn | MakePromise | Fulfil | Break | Occur | Remember | Condition | AdvancePlan | SetPlan | Schedule,
    Field(discriminator="kind"),
]


class Decision(BaseModel):
    npc: str = Field(description="NPC id")
    notice: str = Field("", description="他注意到了什么，一句话")
    judge: str = Field("", description="他怎么看、想要什么，一句话")
    mood: str = Field("", description="这次经历使他的心境怎样变化；无变化留空，不把害怕、愤怒或关切一律写成冷静")
    ops: list[NpcOp] = Field(default_factory=list, max_length=4, description="他这次做的事；没什么要做就留空")
    wait: int = Field(240, ge=10, le=2880, description="过多少分钟再想下一步")


class Consultation(BaseModel):
    npc: str = Field(description="本次决策里的 NPC id；工具权限绑定此人物，不能切换到导演身份")
    tool: Literal["world_query", "world_preview"]
    query: str = Field("", max_length=300, description="查询自己的已知事实、亲历或计划，不能查询未知秘密")
    ops: list[dict[str, Any]] = Field(default_factory=list, max_length=4, description="仅 world_preview：准备试算的行动，不会真正执行")


class Scene(BaseModel):
    summary: str = Field("", description="几个 NPC 同在一处时，这场幕后的戏发生了什么，一两句；只有一个人时可留空")
    decisions: list[Decision] = Field(default_factory=list)
    consultations: list[Consultation] = Field(default_factory=list, max_length=2,
                                             description="资料不足时先查询或试算，decisions暂留空；资料足够时不调用工具")


NPC_MIND_SYSTEM = """你替跑团里的 NPC 决定接下来做什么。玩家看不到决策过程，只会看到人物在场时的行动或之后的结果。

已同意同行时，位置由系统随调查员移动；不要用 move 重复赶路。确因自身目标改变主意，先用 follow（leader、destination 留空）结束同行，公开说明可感知的理由，再自行行动。

- 每个 NPC 只根据他自己的那一份资料行动：他的目标、计划、记忆、知道的事、关系和手头的东西。资料里没有的事，他不知道，也不会去用。
- 先写他注意到了什么（notice），再写他怎么看（judge），然后选最多四个操作（ops）。没什么要做的就让 ops 留空，用 wait 说过多久再想。
- 操作要具体、做得到：去他知道的地方；和同在一处的人说话、交易、答应或翻脸；推进、改变或放弃自己的计划。不替调查员做决定，不凭空造东西。
- 约定记录的是确实说过答应，通常应按动机去履行。若自己的私有记录明确表明曾故意假答应，按真实意图行动，不因台账而被迫履约；失约仍可能被发现，并产生相应关系与现实后果。不能事后拿撒谎解释系统没执行成功。
- 人物有自己的生活：不是每一步都冲着调查员，日常的事也算数。
- 调查员在场时也可以主动行动：处理眼前的问题、兑现约定、提出请求或条件、继续工作、决定离开。操作只结算自己的行为，不替调查员回答、同意、移动或交出物品。
- 目标需要调查员协助时，你也承担自己的部分。委托人会提供他已知的情况、允许合理查访、主动准备已有物品、在能力和风险可承受时协助调查；不把普通帮助都变成说服检定，也不以谨慎为由一概旁观。疏远或敌对人物仍依其自身利益行动。
- 你扮演人物，不是向玩家提供服务的助手。利益、欲望、恐惧或信念可以使他残酷、自私、偏执、报复、狂热或失控；会利用机会、离间、威胁、毁证、设局，也可能在利益一致时暂时合作。根据具体经历推演，不预设人人善良，也不按固定频率制造敌意。
- 情绪随亲历和可信消息变化。亲人失踪、新证据、受威胁、久候无果，可以引出焦急、慌乱、恼怒或如释重负；mood 记录变化，用 occur 表现旁人确实看见的反应。害怕的人仍可能求助、陪同或设法自保，不必靠拒绝行动表现害怕。
- 冲动和非理性仍属于这个人物的选择：写清他眼中的威胁、执念或误判，再在现有资源与权限内行动。恶意可以主动实施，不必等调查员先冒犯；伤害与理智损失不能借 occur 或 condition 绕过战斗、伤害和理智规则，冲突尝试与已裁定的结果分开。
- 计划与执行严格分开：说要出门不等于已到目的地。走动必须 move，交物必须 transfer，同意陪同必须 follow；先执行再用 step 标记完成。新计划为移动、交物等步骤填写 achieved 完成条件；note、notice、judge、summary 和台词都不能替代状态操作。
- 条件只用已列出的真实 id；调查员集体可用 players。& 表示同时满足，| 表示任一满足，at:players:地点可用于等调查员抵达。不要自造调查员昵称或地点 id。
- 根据已发生的变化选择下一步；没有理由时可以等待，不为显得主动而每轮插话、制造危险或重复问答。先前已经完成的对话不再演一遍。
- 在场的人只能观察到外在行为。公开事件不写人物内心、秘密、未获授权的线索或幕后计划；需要公开案情时仍遵守调查进展与线索权限。
- 人物可以有动机地欺骗、假答应或隐瞒，不限反派。公开记录写清“他说/声称了什么”，不能把不实口供直接写成世界事实；自己的私有欺骗记录与真实打算优先于表面的承诺，不因主动行动而突然无条件坦白。
- 若为谋害、操纵或报复而示好，公开行为可以温和，真实计划保留在私有记忆与计划中。不要为了让玩家知道系统正在运作而公开内心独白、泄露阴谋，或在提交时把恶意修正成体面帮助。
- 几个 NPC 同在一处时，这是一场幕后的戏：summary 里一两句写清发生了什么，每个人的操作分开写在 decisions 里。
- 引用的 id 只用资料里出现过的。写短：notice、judge 各不超过 30 字。
- 需要核对旧事或试算行动时，可以用 consultations 调用 world_query 或 world_preview。每次只查询对应 NPC 的资料，试算不会执行；最多两轮，获得结果后再给 decisions。已经能决定时直接输出行动，不为每个普通反应追加查询。
- 走动时，只有别人确实会注意到的痕迹才写 trace（翻乱的东西、撬开的门、带走的东西）；在自己家、自己常待的地方走动，不留痕迹。"""


@dataclass
class Ask:
    """One decision call: the NPCs in it, each one's own view, why they woke."""

    npcs: list[str]
    views: dict[str, str]
    known: dict[str, list[str]]  # fact ids each NPC sees, for vetting and tests
    reasons: dict[str, str]
    t: int
    place: str = ""
    consulted_reads: dict[str, dict[str, int]] = field(default_factory=dict)

    def prompt(self) -> str:
        from ..engine.state import format_time

        parts = [f"【现在】{format_time(self.t)}（第 {self.t} 分钟）" + (f"，在 {self.place}" if self.place else "")]
        for npc in self.npcs:
            parts.append(f"===== {npc} =====\n【为什么现在想】{self.reasons.get(npc, '')}\n{self.views[npc]}")
        return "\n\n".join(parts)


class Brain(Protocol):
    async def decide(self, ask: Ask) -> Scene | None:
        """The scene they play; None when no decision came (the call failed): they are asked again later."""
        ...


@dataclass
class Canned:
    """A scripted brain: fn(ask) returns a Scene, a dict or None. Records every ask."""

    fn: Callable[[Ask], Scene | dict | Awaitable[Scene | dict | None] | None]
    asks: list[Ask] = field(default_factory=list)

    async def decide(self, ask: Ask) -> Scene | None:
        self.asks.append(ask)
        out = self.fn(ask)
        if hasattr(out, "__await__"):
            out = await out  # type: ignore[misc]
        return Scene.model_validate(out) if isinstance(out, dict) else out  # type: ignore[return-value]


class CastBrain:
    """Decisions on the world model, stateless (memory is in the record), one session a group."""

    def __init__(self, cast: Any, state: Any):
        self.cast, self.state = cast, state

    async def decide(self, ask: Ask) -> Scene | None:
        from ..harness import Session
        from ..harness.replay import CassetteError

        key = "mind:" + "+".join(sorted(ask.npcs))
        if key not in self.cast.sessions:
            self.cast.sessions[key] = Session("world", self.cast.cfg.role("world"), NPC_MIND_SYSTEM, schema=Scene.model_json_schema(),
                                              memory=0, key=key, summarizer=self.cast.summarizer)  # fmt: skip
        # The read tools share the exact time slice supplied to this decision. They
        # cannot observe another group's future while that group is still thinking.
        frozen = None
        if live := getattr(self.cast, "world", None):
            from .store import World

            frozen = World(live.m.model_copy(deep=True), live.st.model_copy(deep=True))
            frozen.clock = ask.t
        prompt = ask.prompt()
        for round_ in range(3):
            try:
                reply = await self.cast._send(key, self.cast.sessions[key], self.state, prompt)
                scene = Scene.model_validate(reply.data) if isinstance(reply.data, dict) else None
            except CassetteError:
                raise
            except Exception:
                return None
            if scene is None or not scene.consultations:
                return scene
            if round_ == 2:
                if journal := getattr(self.cast, "debug_journal", None):
                    journal.append("tool", {"status": "deferred", "session": key,
                                            "reason": "达到本次世界查询轮数上限，未执行行动"}, turn=self.state.turn)
                return None
            from .tools import Gateway

            for call in scene.consultations:
                if call.npc not in ask.npcs or frozen is None:
                    result = {"ok": False, "problems": ["该人物不属于本次决策，或世界记录不可用"]}
                else:
                    gateway = Gateway(frozen, call.npc)
                    result = gateway.query(call.query, limit=12) if call.tool == "world_query" else gateway.preview(call.ops)
                    ask.consulted_reads.setdefault(call.npc, {}).update(gateway.native_reads(result.get("reads", {})))
                if journal := getattr(self.cast, "debug_journal", None):
                    journal.append("tool", {"tool": call.tool, "npc": call.npc, "args": call.model_dump(mode="json"),
                                            "result": result}, turn=self.state.turn)
                prompt += "\n【工具结果；尚未执行行动】\n" + json.dumps({"call": call.model_dump(mode="json"), "result": result},
                                                                        ensure_ascii=False)
        return None


def marked(w: World) -> list[Any]:
    """What the module sets going: the NPCs it marks important (Enlist), what they mean to do
    (its agendas: Enlist with their goals, then SetPlan), and its foreshadowing (threads).
    Fixed ids, so a loaded game repeats none of it."""
    from ..engine.state import parse_time
    from .ops import OpenThread

    ops: list[Any] = [Enlist(id=f"enlist:{n.id}", npc=n.id, actor="director") for n in w.m.npcs if n.important and n.id not in w.rec.agents]
    npcs = {n.id for n in w.m.npcs}
    for a in w.m.agendas:
        if a.npc not in npcs:
            continue
        ops.append(Enlist(id=f"agenda:{a.npc}", npc=a.npc, goals=list(a.goals), actor="director"))
        steps = []
        for move in a.steps:
            timed = re.fullmatch(r"D\d+ \d{1,2}:\d{2}", move.when.strip())
            # What the module says it brings about, done with no model call when its time or
            # condition comes; a step for "whenever" (no when) is the NPC's own to judge.
            prepared, achieved = effects(a.npc, move.does) if move.when.strip() else ([], "")
            steps.append(Step(id=move.id, text=move.text, due=parse_time(move.when) if timed else None, when="" if timed else move.when,
                              ops=prepared, achieved=achieved))
        if steps:
            ops.append(SetPlan(id=f"agenda:{a.npc}:plan", npc=a.npc, steps=steps, actor="director"))
    places = {loc.id for loc in w.m.locations}
    ops += [OpenThread(id=f"foreshadow:{f.id}", thread=f"foreshadow:{f.id}", thread_kind="foreshadow", title=f.hint[:40],
                       entities=[f.where] if f.where in places else [], actor="director") for f in w.m.foreshadow]  # fmt: skip
    return ops


def effects(npc: str, does: str) -> tuple[list[dict[str, Any]], str]:
    """An agenda step's outcome (Move.does) as the ops that bring it about (it goes somewhere,
    hands something over, sets a story flag, someone is gone), and the condition that then holds
    (Step.achieved). Only "|"-free outcomes count; the world still checks each op."""
    ops: list[dict[str, Any]] = []
    done: list[str] = []
    if "|" in does:
        return ops, ""
    for kind, rest, negate in condition_atoms(does):
        a, _, b = rest.partition(":")
        if negate:
            continue
        if kind == "at" and a == npc and b:
            ops.append({"kind": "move", "entity": npc, "to": b})
        elif kind == "owner" and a and b:
            ops.append({"kind": "transfer", "item": a, "to": b})
        elif kind == "flag" and rest:
            ops.append({"kind": "flag", "flag": rest})
        elif kind == "gone" and rest:
            ops.append({"kind": "condition", "entity": rest, "status": "失踪" if rest == npc else "死亡"})
        else:
            continue
        done.append(f"{kind}:{rest}")
    return ops, " & ".join(done)


def sanctioned(w: World, npc: str) -> set[str]:
    """Story flags the module's own agenda for this NPC brings about (Move.does): it may set those."""
    return {rest for a in w.m.agendas if a.npc == npc for move in a.steps
            for kind, rest, negate in condition_atoms(move.does) if kind == "flag" and not negate}


# -- what an NPC can reach ---------------------------------------------------------------------
def places(w: World, npc: str) -> set[str]:
    """Places it knows: the public ones, where it is and next door, its home in the module, the
    ones its facts or the director gave it."""
    out = {loc.id for loc in w.m.locations if loc.public}
    here = w.place(npc)
    if here:
        out.add(here)
        out |= set(next((loc.exits for loc in w.m.locations if loc.id == here), []))
    if (n := next((x for x in w.m.npcs if x.id == npc), None)) and n.location:
        out.add(n.location)
    if a := w.agent(npc):
        out |= set(a.places)
        out |= {rest.partition(":")[2] for s in a.plan for kind, rest, _ in condition_atoms(f"{s.when} & {s.achieved}".strip(" &"))
                if kind == "at"}  # where its plan waits for someone, or takes it
    locations = {e.id for e in w.rec.entities.values() if e.kind == "location"}
    out |= {x for f in knowledge.known(w.rec, npc) for x in f.about if x in locations}
    return out


def _beside(w: World, npc: str, other: str) -> bool:
    return other == npc or (other == PLAYERS and w.place(npc) == w.st.location) or (w.place(other) == w.place(npc) != "")


def _disclosure(w: World, npc: str, op: Any) -> list[str]:
    """An autonomous action has the same disclosure limits as an NPC's reply."""
    visible = op.visibility == "public" or (op.visibility == "auto" and w.place(npc) == w.st.location)
    if isinstance(op, Learn):
        visible = visible or PLAYERS in op.who
    if isinstance(op, Equip):
        visible = op.reveal
    if not visible:
        return []
    n = w.m.npc(npc)
    allowed = set(w.st.clues) | set(w.st.npc(npc).unlocked) | {cid for cid in n.clues if cid not in n.gates}
    protected = {f.id: f.text for f in w.rec.facts.values()
                 if not f.retracted and f.revealed is None and f.kind in {"secret", "truth", "clue", "conclusion"}
                 and not (f.id.startswith("clue:") and f.id.removeprefix("clue:") in allowed)}
    facts = [op.fact] if isinstance(op, Learn) else getattr(op, "facts", [])
    blocked = [fid for fid in facts if fid in protected]
    public = "\n".join(f.text for f in w.rec.facts.values()
                       if not f.retracted and (f.revealed is not None
                                               or (f.id.startswith("clue:") and f.id.removeprefix("clue:") in allowed)))
    text = op.evidence if isinstance(op, Equip) else getattr(op, "text", "")
    blocked += guard.leaks(text, protected, public=public)
    return [f"不能通过公开行动透露未解锁的信息：{fid}" for fid in dict.fromkeys(blocked)]


def vet(w: World, npc: str, op: Any) -> list[str]:
    """What this NPC cannot do of this op, from where it is and what it knows."""
    me, name = npc, w.name(npc)
    if problems := _disclosure(w, npc, op):
        return problems
    if isinstance(op, Move):
        if op.entity != me:
            return [f"{name}只能决定自己去哪"]
        return [] if op.to in places(w, me) else [f"{name}不知道 {op.to} 这个地方"]
    if isinstance(op, Follow):
        if op.npc != me:
            return [f"{name}只能决定自己是否同行"]
        return [] if not op.destination or op.destination in places(w, me) else [f"{name}不知道同行目的地"]
    if isinstance(op, Relate):
        return [] if op.a == me else [f"{name}只能改变自己对别人的看法"]
    if isinstance(op, Equip):
        return [] if legacy_world.owner(w.m, w.st, op.item) == me else [f"{name}只能整理自己的装备"]
    if isinstance(op, Transfer):
        holder = legacy_world.owner(w.m, w.st, op.item)
        if holder == me:
            return [] if _beside(w, me, op.to) else [f"{op.to} 不在{name}身边"]  # a place: where it is, to put it down
        if op.to == me and holder == w.place(me):
            return []
        return [f"{op.item} 不在{name}手里，也不在他身边"]
    if isinstance(op, Learn):
        return [f"{name}身边没有 {x}" for x in op.who if not _beside(w, me, x)]
    if isinstance(op, MakePromise):
        if op.by != me:
            return [f"{name}只能替自己答应"]
        return [] if _beside(w, me, op.to) else [f"{op.to} 不在{name}身边"]
    if isinstance(op, Occur):
        problems = [] if not op.place or op.place == w.place(me) else [f"{name}不在 {op.place}"]
        if not op.reactive:
            problems.append("不能替其他人物关闭对这件事的反应")
        problems += [f"{x} 不在{name}身边" for x in op.participants if not _beside(w, me, x)]
        sees = {f.id for f in knowledge.known(w.rec, me)}
        return problems + [f"{name}不知道事实 {f}" for f in op.facts if f not in sees]
    if isinstance(op, Condition):
        return [] if _beside(w, me, op.entity) else [f"{op.entity} 不在{name}身边"]
    if isinstance(op, Remember | AdvancePlan | SetPlan):
        return [] if op.npc == me else [f"{name}只能管自己的记忆和计划"]
    if isinstance(op, Schedule):
        return [] if op.participants == [] or me in op.participants else [f"{name}只能安排自己要做的事"]
    if isinstance(op, Fulfil | Break):
        return []  # the op itself checks that it is a party
    if isinstance(op, SetFlag):
        return [] if op.flag in sanctioned(w, me) else [f"{name}不能改剧情标记 {op.flag}"]
    return [f"{name}不能做 {getattr(op, 'kind', '?')}"]


# -- conditions on plan steps -------------------------------------------------------------------
def condition_atoms(when: str) -> list[tuple[str, str, bool]]:
    """The small condition language has AND precedence over OR, and no parentheses."""
    return [(part.lstrip("!").partition(":")[0], part.lstrip("!").partition(":")[2], part.startswith("!"))
            for group in when.split("|") for raw in group.split("&") if (part := raw.strip())]


def condition_problems(w: World, when: str) -> list[str]:
    if not when.strip():
        return []
    problems = []
    who = set(w.rec.entities) | {i.id for i in w.st.investigators} | {PLAYERS}
    if any(not p.strip() for group in when.split("|") for p in group.split("&")):
        problems.append("条件含有空的分支")
    for kind, rest, _ in condition_atoms(when):
        a, sep, b = rest.partition(":")
        if kind == "flag":
            valid = rest in w.m.flags or rest in w.st.flags
        elif kind == "at":
            valid = a in who and bool(sep) and (e := w.rec.entities.get(b)) is not None and e.kind == "location"
        elif kind == "promise":
            valid = a in w.rec.promises and b in {"active", "fulfilled", "broken"}
        elif kind == "owner":
            valid = (e := w.rec.entities.get(a)) is not None and e.kind == "item" and b in who
        elif kind == "knows":
            valid = a in who and b in w.rec.facts
        elif kind == "after":
            valid = rest.isdecimal()
        elif kind == "gone":
            valid = (e := w.rec.entities.get(rest)) is not None and e.kind == "npc"
        elif kind == "hour":
            valid = hours(rest) is not None
        else:
            valid = False
        if not valid or any(c in rest for c in "()!"):
            problems.append(f"条件不存在或格式不正确：{kind}:{rest}")
    return problems


def holds(w: World, when: str) -> bool:
    """Fail closed on invalid conditions, including unknown IDs in old saved plans."""
    if condition_problems(w, when):
        return False

    def atom(kind: str, rest: str, negate: bool) -> bool:
        a, _, b = rest.partition(":")
        if kind == "flag":
            ok = rest in w.st.flags
        elif kind == "at":
            ok = (w.st.location if a == PLAYERS else w.place(a)) == b
        elif kind == "promise":
            ok = w.rec.promises[a].status == b
        elif kind == "owner":
            owner = legacy_world.owner(w.m, w.st, a)
            ok = owner == b or (b == PLAYERS and any(i.id == owner for i in w.st.investigators))
        elif kind == "knows":
            ok = knowledge.sees(w.rec.facts[b], a)
        elif kind == "gone":
            ok = w.rec.entities[rest].status in ("死亡", "失踪")
        elif kind == "hour":
            ok = within(rest, w.now)
        else:  # after: validated as a nonnegative integer
            ok = w.now >= int(rest)
        return ok != negate

    return any(all(atom(*a) for a in condition_atoms(group)) for group in when.split("|"))
