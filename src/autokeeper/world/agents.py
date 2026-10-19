"""Important NPCs as agents: what they may decide, what they may do, and who decides.

An NPC's decision (observe -> judge -> choose ops) is one structured call: a Scene with a
Decision per NPC. Several NPCs at one place offscreen are one call, one scene. The brain
only gets each NPC's own view (world/context.npc_view), and vet holds every op to what that
NPC knows and can reach: its own things, the people beside it, the places it knows.

Brains: Canned (tests, scripted playtests) and CastBrain (phase 2: a stateless session on
the NPC model through agents/cast.py, counted in the save's usage as "mind").
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Protocol

from pydantic import BaseModel, Field

from ..engine import world as legacy_world
from . import knowledge
from .ops import (
    MAX_AGENTS,
    AdvancePlan,
    Break,
    Condition,
    Enlist,
    Fulfil,
    Learn,
    MakePromise,
    Move,
    Occur,
    Relate,
    Remember,
    Schedule,
    SetPlan,
    Transfer,
)
from .record import PLAYERS

if TYPE_CHECKING:
    from .store import World

NpcOp = Annotated[
    Move | Relate | Transfer | Learn | MakePromise | Fulfil | Break | Occur | Remember | Condition | AdvancePlan | SetPlan | Schedule,
    Field(discriminator="kind"),
]


class Decision(BaseModel):
    npc: str = Field(description="NPC id")
    notice: str = Field("", description="他注意到了什么，一句话")
    judge: str = Field("", description="他怎么看、想要什么，一句话")
    ops: list[NpcOp] = Field(default_factory=list, max_length=4, description="他这次做的事；没什么要做就留空")
    wait: int = Field(240, ge=10, le=2880, description="过多少分钟再想下一步")


class Scene(BaseModel):
    summary: str = Field("", description="几个 NPC 同在一处时，这场幕后的戏发生了什么，一两句；只有一个人时可留空")
    decisions: list[Decision] = Field(default_factory=list)


NPC_MIND_SYSTEM = """你在幕后替跑团里的 NPC 决定接下来做什么。玩家看不到这一步，只会在之后看到结果。

- 每个 NPC 只根据他自己的那一份资料行动：他的目标、计划、记忆、知道的事、关系和手头的东西。资料里没有的事，他不知道，也不会去用。
- 先写他注意到了什么（notice），再写他怎么看（judge），然后选最多四个操作（ops）。没什么要做的就让 ops 留空，用 wait 说过多久再想。
- 操作要具体、做得到：去他知道的地方；和同在一处的人说话、交易、答应或翻脸；推进、改变或放弃自己的计划。不替调查员做决定，不凭空造东西。
- 约定要当真：答应过的事到时去做；对方失约，他会记住，关系随之变化。
- 人物有自己的生活：不是每一步都冲着调查员，日常的事也算数。
- 几个 NPC 同在一处时，这是一场幕后的戏：summary 里一两句写清发生了什么，每个人的操作分开写在 decisions 里。
- 引用的 id 只用资料里出现过的。写短：notice、judge 各不超过 30 字。"""


@dataclass
class Ask:
    """One decision call: the NPCs in it, each one's own view, why they woke."""

    npcs: list[str]
    views: dict[str, str]
    known: dict[str, list[str]]  # fact ids each NPC sees, for vetting and tests
    reasons: dict[str, str]
    t: int
    place: str = ""

    def prompt(self) -> str:
        parts = [f"【现在】第 {self.t} 分钟" + (f"，在 {self.place}" if self.place else "")]
        for npc in self.npcs:
            parts.append(f"===== {npc} =====\n【为什么现在想】{self.reasons.get(npc, '')}\n{self.views[npc]}")
        return "\n\n".join(parts)


class Brain(Protocol):
    async def decide(self, ask: Ask) -> Scene | None: ...


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
    """Decisions on the NPC model, stateless (the memory is in the record), one session a group."""

    def __init__(self, cast: Any, state: Any):
        self.cast, self.state = cast, state

    async def decide(self, ask: Ask) -> Scene | None:
        from ..harness import Session

        key = "mind:" + "+".join(sorted(ask.npcs))
        if key not in self.cast.sessions:
            self.cast.sessions[key] = Session("npc", self.cast.cfg.role("npc"), NPC_MIND_SYSTEM, schema=Scene.model_json_schema(),
                                              memory=0, key=key, summarizer=self.cast.summarizer)  # fmt: skip
        reply = await self.cast._send(key, self.cast.sessions[key], self.state, ask.prompt())
        try:
            return Scene.model_validate(reply.data) if isinstance(reply.data, dict) else None
        except ValueError:
            return None


def marked(w: World) -> list[Enlist]:
    """Enlist ops for the NPCs the module marks important and the record does not have yet."""
    return [Enlist(id=f"enlist:{n.id}", npc=n.id, actor="director") for n in w.m.npcs if n.important and n.id not in w.rec.agents][:MAX_AGENTS]


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
        out |= {s.when.split(":")[2] for s in a.plan if s.when.startswith("at:") and s.when.count(":") == 2}
    locations = {e.id for e in w.rec.entities.values() if e.kind == "location"}
    out |= {x for f in knowledge.known(w.rec, npc) for x in f.about if x in locations}
    return out


def _beside(w: World, npc: str, other: str) -> bool:
    return other == npc or (other == PLAYERS and w.place(npc) == w.st.location) or (w.place(other) == w.place(npc) != "")


def vet(w: World, npc: str, op: Any) -> list[str]:
    """What this NPC cannot do of this op, from where it is and what it knows."""
    me, name = npc, w.name(npc)
    if isinstance(op, Move):
        if op.entity != me:
            return [f"{name}只能决定自己去哪"]
        return [] if op.to in places(w, me) else [f"{name}不知道 {op.to} 这个地方"]
    if isinstance(op, Relate):
        return [] if op.a == me else [f"{name}只能改变自己对别人的看法"]
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
    return [f"{name}不能做 {getattr(op, 'kind', '?')}"]


# -- conditions on plan steps -------------------------------------------------------------------
def holds(w: World, when: str) -> bool:
    """A step's condition, parts joined by &: flag:x, !flag:x, at:<who>:<place>,
    promise:<id>:<active|fulfilled|broken>, owner:<item>:<holder>, knows:<npc>:<fact>, after:<minute>."""
    for part in (p.strip() for p in when.split("&")):
        if not part:
            continue
        negate = part.startswith("!")
        kind, _, rest = part.lstrip("!").partition(":")
        a, _, b = rest.partition(":")
        if kind == "flag":
            ok = rest in w.st.flags
        elif kind == "at":
            ok = w.place(a) == b
        elif kind == "promise":
            ok = (p := w.rec.promises.get(a)) is not None and p.status == b
        elif kind == "owner":
            ok = legacy_world.owner(w.m, w.st, a) == b
        elif kind == "knows":
            ok = (f := w.rec.facts.get(b)) is not None and knowledge.sees(f, a)
        elif kind == "after":
            ok = w.now >= int(rest)
        else:
            ok = False
        if ok == negate:
            return False
    return True
