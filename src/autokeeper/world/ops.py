"""The world operations: every change to this game's world is one of these.

Each op checks itself against the world (check -> problems in plain words) and settles into
it (settle -> the event it logged). store.World.apply adds idempotence, the journal, undo
and the projection into the module. An op's `actor` is who does it: an NPC id, an
investigator id, director, keeper or engine; NPC agents are held to what they know by
world/agents.vet before their ops get here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.json_schema import SkipJsonSchema

from ..engine import world as legacy_world
from .record import (
    DIRECTOR,
    ENGINE,
    KEEPER,
    PLAYERS,
    Agent,
    Entity,
    Fact,
    Promise,
    Relation,
    Scheduled,
    Step,
    Thread,
    relation_key,
)

if TYPE_CHECKING:
    from .record import Event
    from .store import World

MAX_AGENTS = 6
STAFF = (ENGINE, DIRECTOR, KEEPER, "arbiter")
Visibility = Literal["auto", "public", "private"]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    undoable: ClassVar[bool] = True

    id: str = Field("", description="同一件事重试时用同一个 id，留空则按内容生成")
    actor: str = Field(ENGINE, description="谁做的：NPC id、调查员 id，或 director / keeper / engine")
    reason: str = Field("", description="为什么，玩家看不到")
    visibility: Visibility = Field("auto", description="auto：调查员在场就公开；public 公开；private 暗中进行")

    def check(self, w: World) -> list[str]:
        return []

    def settle(self, w: World, oid: str) -> tuple[Event | None, Any]:
        raise NotImplementedError


def _who(w: World, ident: str, *, players: bool = True) -> bool:
    return (players and ident == PLAYERS) or ident in w.rec.entities or any(i.id == ident for i in w.st.investigators)


def _npc(w: World, ident: str) -> Entity | None:
    e = w.rec.entities.get(ident)
    return e if e is not None and e.kind == "npc" else None


# -- people and things ------------------------------------------------------------------------
class Move(Base):
    kind: Literal["move"] = "move"
    entity: str = Field(description="NPC id")
    to: str = Field(description="地点 id")
    trace: str = Field("", description="之后在原地还看得出来的痕迹，没有就留空")

    def check(self, w: World) -> list[str]:
        e = _npc(w, self.entity)
        if e is None:
            return [f"{self.entity} 不是 NPC"]
        dest = w.rec.entities.get(self.to)
        if dest is None or dest.kind != "location":
            return [f"地点 {self.to} 不存在"]
        if e.status == "死亡":
            return [f"{e.name} 已经死了"]
        return [f"{e.name} 已经在 {dest.name}"] if e.at == self.to else []

    def settle(self, w: World, oid: str):
        e = w.rec.entities[self.entity]
        src = e.at
        seeing = [] if self.visibility == "private" else [*w.present(src), *w.present(self.to)]  # who saw it go and arrive
        e.at = self.to
        w.touch(e)
        vis = self.visibility
        if vis == "auto":
            vis = "public" if w.st.location in (src, self.to) else "private"
        return w.emit("move", f"{e.name}从{w.name(src)}去了{w.name(self.to)}", actor=self.actor if self.actor not in STAFF else e.id,
                      participants=[e.id], place=self.to, visibility=vis, witnesses=seeing, trace=self.trace, op=oid), None  # fmt: skip


class Relate(Base):
    kind: Literal["relate"] = "relate"
    a: str = Field(description="谁的看法")
    b: str = Field(description="对谁")
    rel: str = Field(description="关系，比如 信任、恐惧、欠情、敌对、盟友")
    value: int | None = Field(None, ge=-100, le=100, description="直接设成多少，-100..100")
    delta: int | None = Field(None, ge=-100, le=100, description="增减多少")
    note: str = Field("", description="一句话：因为什么")
    known_by: list[str] | None = Field(None, description="谁知道这层关系；默认双方")
    key: SkipJsonSchema[str] = Field("", exclude=True)  # its GameState.world key and NPC (adapters.py)
    tag: SkipJsonSchema[str] = Field("", exclude=True)

    def check(self, w: World) -> list[str]:
        problems = [f"{x} 不存在" for x in (self.a, self.b) if not _who(w, x)]
        if self.a == self.b:
            problems.append("关系的两方是同一个人")
        if not self.rel.strip() or len(self.rel) > 12:
            problems.append("关系名要写，且不超过 12 个字")
        if self.value is None and self.delta is None and not self.note.strip():
            problems.append("value、delta、note 至少写一个")
        return problems

    def settle(self, w: World, oid: str):
        k = relation_key(self.a, self.b, self.rel)
        r = w.rec.relations.get(k)
        old = r.value if r else 0
        if r is None:
            r = w.rec.relations[k] = Relation(a=self.a, b=self.b, kind=self.rel, since=w.now, changed=w.rec.seq)
        else:
            w.touch(r)
        r.value = max(-100, min(100, self.value if self.value is not None else old + (self.delta or 0)))
        r.note = self.note or r.note
        people = [x for x in (self.a, self.b) if _npc(w, x)]
        r.known_by = list(dict.fromkeys([*r.known_by, *(self.known_by if self.known_by is not None else people)]))
        text = f"{w.name(self.a)}对{w.name(self.b)}的{self.rel}：{old}→{r.value}" + (f"（{self.note}）" if self.note else "")
        e = w.emit("relate", text, actor=self.actor, participants=[self.a, self.b], place=w.place(self.a),
                   visibility=self.visibility, op=oid)  # fmt: skip
        if e.visibility == "public" and PLAYERS not in r.known_by:
            r.known_by.append(PLAYERS)
        w.legacy(self.key, "relationship", self.note or text, self.tag or next(iter(people), ""), self.actor)
        return e, None


class Transfer(Base):
    kind: Literal["transfer"] = "transfer"
    item: str = Field(description="物件 id，或调查员背包里的完整名称")
    to: str = Field(description="新持有者：调查员、NPC 或地点 id")
    giver: str = Field("", description="原来的持有者；写了就必须对得上")
    text: str = ""
    key: SkipJsonSchema[str] = Field("", exclude=True)
    tag: SkipJsonSchema[str] = Field("", exclude=True)

    def check(self, w: World) -> list[str]:
        before = legacy_world.owner(w.m, w.st, self.item)
        if not before:
            return [f"物件 {self.item} 不存在或不知道在谁手里"]
        if self.giver and self.giver != before:
            return [f"{self.item} 现在不在 {self.giver} 手里"]
        if not _who(w, self.to, players=False):
            return [f"{self.to} 不存在"]
        return [f"{self.item} 已经在 {self.to} 手里"] if before == self.to else []

    def settle(self, w: World, oid: str):
        st, m = w.st, w.m
        before = legacy_world.owner(m, st, self.item)
        item = next((x for x in m.items if x.id == self.item), None)
        name = item.name if item else self.item
        for inv in st.investigators:  # the same bookkeeping as engine/world.apply
            if inv.id == before and name in inv.inventory:
                inv.inventory.remove(name)
            if inv.id == self.to and name not in inv.inventory:
                inv.inventory.append(name)
        st.item_owners[self.item] = self.to
        ent = w.rec.entities.get(self.item)
        if ent is None:
            ent = w.rec.entities[self.item] = Entity(id=self.item, kind="item", name=name, origin="game", changed=w.rec.seq)
        else:
            w.touch(ent)
        ent.at = self.to
        text = self.text or f"{name}从{w.name(before)}到了{w.name(self.to)}手里"
        w.legacy(self.key, "transfer", text, self.tag or next((x for x in (before, self.to) if _npc(w, x)), ""), self.actor)
        return w.emit("transfer", text, actor=self.actor, participants=[before, self.to, self.item], place=w.place(before),
                      visibility=self.visibility, op=oid), None  # fmt: skip


class Condition(Base):
    kind: Literal["condition"] = "condition"
    entity: str = Field(description="调查员或 NPC id")
    add: str = Field("", description="加上的状态，比如 受伤、昏迷")
    remove: str = Field("", description="去掉的状态")
    status: str | None = Field(None, description="NPC 的总体状况：受伤、失踪、死亡；空字符串表示恢复正常")

    def check(self, w: World) -> list[str]:
        if not (self.add or self.remove or self.status is not None):
            return ["add、remove、status 至少写一个"]
        if any(i.id == self.entity for i in w.st.investigators):
            return [] if self.status is None else ["调查员的状况由规则结算，这里只改状态"]
        return [] if _npc(w, self.entity) else [f"{self.entity} 不是调查员或 NPC"]

    def settle(self, w: World, oid: str):
        inv = next((i for i in w.st.investigators if i.id == self.entity), None)
        if inv is not None:
            if self.add:
                inv.add_condition(self.add)
            if self.remove in inv.conditions:
                inv.conditions.remove(self.remove)
        e = w.rec.entities[self.entity]
        if self.add and self.add not in e.conditions:
            e.conditions.append(self.add)
        if self.remove in e.conditions:
            e.conditions.remove(self.remove)
        if self.status is not None:
            e.status = self.status
        w.touch(e)
        said = "、".join(x for x in (f"添了{self.add}" if self.add else "", f"不再{self.remove}" if self.remove else "",
                                     f"现在{self.status or '无恙'}" if self.status is not None else "") if x)  # fmt: skip
        return w.emit("condition", f"{e.name}{said}", actor=self.actor, participants=[self.entity], place=w.place(self.entity),
                      visibility=self.visibility, op=oid), None  # fmt: skip


class SetFlag(Base):
    kind: Literal["flag"] = "flag"
    flag: str
    on: bool = True

    def check(self, w: World) -> list[str]:
        return [] if self.flag in w.m.flags else [f"标记 {self.flag} 没有声明"]

    def settle(self, w: World, oid: str):
        if self.on and self.flag not in w.st.flags:
            w.st.flags.append(self.flag)
        elif not self.on and self.flag in w.st.flags:
            w.st.flags.remove(self.flag)
        meaning = w.m.flags[self.flag]
        return w.emit("flag", meaning if self.on else f"不再是：{meaning}", actor=self.actor, place=w.place(self.actor) or w.st.location,
                      visibility="private" if self.visibility == "auto" else self.visibility, op=oid), None  # fmt: skip


# -- facts and knowledge -----------------------------------------------------------------------
class AddFact(Base):
    kind: Literal["fact"] = "fact"
    fact: str = Field(description="新事实的 id，英文小写加下划线")
    text: str = Field(description="一句话，已经发生或确实如此的事")
    fact_kind: Literal["state", "secret", "knows", "rumor", "relation", "event", "setting"] = "state"
    about: list[str] = Field(default_factory=list)
    known_by: list[str] = Field(default_factory=list, description="哪些 NPC 知道")
    true: bool = True
    explains: list[str] = Field(default_factory=list, description="能解释哪些线索 id")
    public: bool = Field(False, description="调查员们是否已经知道")
    key: SkipJsonSchema[str] = Field("", exclude=True)
    tag: SkipJsonSchema[str] = Field("", exclude=True)

    def check(self, w: World) -> list[str]:
        problems = []
        if self.fact in w.rec.facts:
            problems.append(f"事实 {self.fact} 已经存在；要改写用 rewrite")
        if not self.text.strip() or len(self.text) > 300:
            problems.append("事实要写，且不超过 300 字")
        problems += [f"{x} 不存在" for x in [*self.about, *self.known_by] if not _who(w, x)]
        problems += [f"线索 {c} 不存在" for c in self.explains if f"clue:{c}" not in w.rec.facts]
        return problems

    def settle(self, w: World, oid: str):
        f = w.rec.facts[self.fact] = Fact(id=self.fact, text=self.text, kind=self.fact_kind, about=list(self.about), origin="game",
                                          true=self.true, known_by=[x for x in self.known_by if x != PLAYERS], explains=list(self.explains),
                                          t=w.now, key=self.key, changed=w.rec.seq)  # fmt: skip
        public = self.public or PLAYERS in self.known_by
        vis = "public" if public else ("private" if self.visibility == "auto" else self.visibility)
        e = w.emit("fact", self.text, actor=self.actor, participants=list(self.about), place=w.place(self.actor) or w.st.location,
                   visibility=vis, witnesses=f.known_by, facts=[f.id], op=oid)  # fmt: skip
        w.legacy(self.key, "relationship" if self.fact_kind == "relation" else "fact", self.text,
                 self.tag or next((x for x in self.known_by if _npc(w, x)), ""), self.actor)
        return e, None


class Learn(Base):
    kind: Literal["learn"] = "learn"
    fact: str = Field(description="事实 id")
    who: list[str] = Field(description="谁知道了：NPC id，或 players 表示告诉调查员们")
    how: str = Field("", description="怎么知道的，一句话")

    def check(self, w: World) -> list[str]:
        f = w.rec.facts.get(self.fact)
        if f is None or f.retracted:
            return [f"事实 {self.fact} 不存在"]
        problems = [f"{x} 不存在" for x in self.who if not _who(w, x)]
        if self.actor not in STAFF and self.actor not in f.known_by and not any(i.id == self.actor for i in w.st.investigators):
            problems.append(f"{w.name(self.actor)} 自己不知道这件事，说不出来")
        return problems

    def settle(self, w: World, oid: str):
        f = w.rec.facts[self.fact]  # who knows it is not what it says: its version stays
        tell = [x for x in self.who if x != PLAYERS]
        for x in tell:
            if x not in f.known_by:
                f.known_by.append(x)
        names = "、".join(w.name(x) for x in self.who)
        teller = "" if self.actor in STAFF else w.name(self.actor)
        text = f"{teller + '告诉' if teller else ''}{names}{'' if teller else '得知'}：{f.text}" + (f"（{self.how}）" if self.how else "")
        vis = "public" if PLAYERS in self.who else ("private" if self.visibility == "auto" else self.visibility)
        e = w.emit("learn", text, actor=self.actor, participants=tell, place=w.place(self.actor) or w.place(next(iter(tell), "")),
                   visibility=vis, witnesses=tell, facts=[f.id], op=oid)  # fmt: skip
        return e, None


class Remember(Base):
    kind: Literal["remember"] = "remember"
    npc: str
    text: str = Field(description="这个 NPC 记住的事，一句话")
    fact: str = ""
    event: str = ""

    def check(self, w: World) -> list[str]:
        if self.npc not in w.rec.agents:
            return [f"{self.npc} 不是重要 NPC，没有记忆"]
        return [] if self.text.strip() and len(self.text) <= 200 else ["记忆要写，且不超过 200 字"]

    def settle(self, w: World, oid: str):
        from .record import Memory
        from .store import MEMORY_KEEP

        a = w.rec.agents[self.npc]
        a.memory.append(Memory(t=w.now, turn=w.st.turn, text=self.text, fact=self.fact, event=self.event))
        del a.memory[:-MEMORY_KEEP]
        w.touch(a)
        return None, None


class Occur(Base):
    """Something happens: an offscreen scene, a consequence, a rumour spreading. Several
    participants make it one hyperedge."""

    kind: Literal["occur"] = "occur"
    text: str = Field(description="发生了什么，一两句")
    place: str = Field("", description="在哪个地点")
    participants: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list, description="这件事让在场的人知道了哪些事实 id")
    trace: str = Field("", description="之后在这里还看得出来的痕迹")

    def check(self, w: World) -> list[str]:
        problems = [] if self.text.strip() and len(self.text) <= 300 else ["要写发生了什么，不超过 300 字"]
        if self.place and self.place not in w.rec.entities:
            problems.append(f"地点 {self.place} 不存在")
        problems += [f"{x} 不存在" for x in self.participants if not _who(w, x)]
        problems += [f"事实 {f} 不存在" for f in self.facts if f not in w.rec.facts]
        return problems

    def settle(self, w: World, oid: str):
        return w.emit("occur", self.text, actor=self.actor, participants=self.participants, place=self.place,
                      visibility=self.visibility, facts=self.facts, trace=self.trace, op=oid), None  # fmt: skip


# -- promises and threads ----------------------------------------------------------------------
class MakePromise(Base):
    kind: Literal["promise"] = "promise"
    promise: str = Field(description="约定 id")
    by: str = Field(description="谁答应的")
    to: str = Field(description="答应谁")
    text: str
    due: int | None = Field(None, description="到期的游戏分钟数；没有期限留空")
    public: bool = True
    key: SkipJsonSchema[str] = Field("", exclude=True)
    tag: SkipJsonSchema[str] = Field("", exclude=True)

    def check(self, w: World) -> list[str]:
        problems = [f"约定 {self.promise} 已经存在"] if self.promise in w.rec.promises else []
        problems += [f"{x} 不存在" for x in (self.by, self.to) if not _who(w, x)]
        if not self.text.strip() or len(self.text) > 300:
            problems.append("约定要写，且不超过 300 字")
        if self.due is not None and self.due < w.now:
            problems.append("约定的期限已经过了")
        return problems

    def settle(self, w: World, oid: str):
        p = w.rec.promises[self.promise] = Promise(id=self.promise, by=self.by, to=self.to, text=self.text, made=w.now, turn=w.st.turn,
                                                   due=self.due, public=self.public, changed=w.rec.seq)  # fmt: skip
        tid = f"promise:{p.id}"
        w.rec.threads.setdefault(tid, Thread(id=tid, kind="promise", title=self.text[:40], opened=w.now, opened_turn=w.st.turn,
                                             last=w.now, last_turn=w.st.turn, entities=[self.by, self.to]))  # fmt: skip
        npc = self.tag or next((x for x in (self.by, self.to) if _npc(w, x)), "")
        w.legacy(self.key, "promise", self.text, npc, self.actor)
        vis = self.visibility if self.public else "private"  # auto: public where the party is
        return w.emit("promise", f"{w.name(self.by)}答应{w.name(self.to)}：{self.text}", actor=self.actor, participants=[self.by, self.to],
                      place=w.place(self.by) or w.place(self.to), visibility=vis, op=oid), None  # fmt: skip


class _Settle(Base):
    promise: str
    text: str = ""
    status: ClassVar[str] = ""

    def check(self, w: World) -> list[str]:
        p = w.rec.promises.get(self.promise)
        if p is None:
            return [f"约定 {self.promise} 不存在"]
        if p.status != "active":
            return [f"约定 {self.promise} 已经{'履行' if p.status == 'fulfilled' else '违背'}了"]
        if self.actor not in (*STAFF, p.by, p.to) and not any(i.id == self.actor for i in w.st.investigators):
            return [f"{w.name(self.actor)} 不是这个约定的一方"]
        return []

    def settle(self, w: World, oid: str):
        p = w.rec.promises[self.promise]
        p.status, p.settled = self.status, w.now  # type: ignore[assignment]
        w.touch(p)
        word = "履行了" if self.status == "fulfilled" else "没有履行"
        text = f"{w.name(p.by)}{word}对{w.name(p.to)}的约定：{p.text}" + (f"（{self.text}）" if self.text else "")
        if th := w.rec.threads.get(f"promise:{p.id}"):
            th.status, th.resolution, th.last, th.last_turn = "closed", text, w.now, w.st.turn
            th.heat += 0 if self.status == "fulfilled" else 2
            w.touch(th)
        for key, fact in w.st.world.items():  # the mirrored GameState.world promise, if any
            if fact.kind == "promise" and key == p.id:
                fact.status, fact.turn = self.status, w.st.turn  # type: ignore[assignment]
        vis = self.visibility if p.public else "private"  # auto: public where the party is
        where = w.place(self.actor) or w.place(p.by) or w.place(p.to)  # where whoever settled it is
        return w.emit(self.kind, text, actor=self.actor, participants=[p.by, p.to], place=where,  # type: ignore[attr-defined]
                      visibility=vis, op=oid), None  # fmt: skip


class Fulfil(_Settle):
    kind: Literal["fulfil"] = "fulfil"
    status: ClassVar[str] = "fulfilled"


class Break(_Settle):
    kind: Literal["break"] = "break"
    status: ClassVar[str] = "broken"


class OpenThread(Base):
    kind: Literal["thread_open"] = "thread_open"
    thread: str
    thread_kind: Literal["question", "foreshadow", "conflict", "plan"] = "question"
    title: str
    entities: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)

    def check(self, w: World) -> list[str]:
        problems = [f"线头 {self.thread} 已经存在"] if self.thread in w.rec.threads else []
        problems += [f"{x} 不存在" for x in self.entities if not _who(w, x)]
        return problems + [f"事实 {f} 不存在" for f in self.facts if f not in w.rec.facts]

    def settle(self, w: World, oid: str):
        w.rec.threads[self.thread] = Thread(id=self.thread, kind=self.thread_kind, title=self.title, opened=w.now, opened_turn=w.st.turn,
                                            last=w.now, last_turn=w.st.turn, entities=list(self.entities), facts=list(self.facts),
                                            changed=w.rec.seq)  # fmt: skip
        return w.emit("thread", f"新线头：{self.title}", actor=self.actor, participants=self.entities, visibility="private", op=oid), None


class AdvanceThread(Base):
    kind: Literal["thread_advance"] = "thread_advance"
    thread: str
    beat: str = Field(description="这条线这次往前走了哪一步")
    heat: int = Field(1, ge=-5, le=5)
    facts: list[str] = Field(default_factory=list)

    def check(self, w: World) -> list[str]:
        th = w.rec.threads.get(self.thread)
        if th is None:
            return [f"线头 {self.thread} 不存在"]
        return [f"线头 {self.thread} 已经收了"] if th.status == "closed" else []

    def settle(self, w: World, oid: str):
        th = w.rec.threads[self.thread]
        th.beats.append(self.beat[:120])
        th.heat = max(0, th.heat + self.heat)
        th.facts = list(dict.fromkeys([*th.facts, *self.facts]))
        th.last, th.last_turn = w.now, w.st.turn
        w.touch(th)
        return w.emit("thread", f"{th.title}：{self.beat}", actor=self.actor, participants=th.entities, visibility="private", op=oid), None


class CloseThread(Base):
    kind: Literal["thread_close"] = "thread_close"
    thread: str
    resolution: str

    check = AdvanceThread.check

    def settle(self, w: World, oid: str):
        th = w.rec.threads[self.thread]
        th.status, th.resolution, th.last, th.last_turn = "closed", self.resolution[:200], w.now, w.st.turn
        w.touch(th)
        return w.emit("thread", f"{th.title}：{self.resolution}", actor=self.actor, participants=th.entities, visibility="private", op=oid), None


# -- time and plans ----------------------------------------------------------------------------
class Schedule(Base):
    kind: Literal["schedule"] = "schedule"
    event: str = Field(description="安排的 id")
    at: int = Field(description="在第几分钟发生（游戏时间）")
    text: str
    place: str = ""
    participants: list[str] = Field(default_factory=list)
    ops: list[dict[str, Any]] = Field(default_factory=list, description="到时结算的操作")
    reads: dict[str, int] = Field(default_factory=dict)

    def check(self, w: World) -> list[str]:
        problems = [f"安排 {self.event} 已经存在"] if self.event in w.rec.scheduled else []
        if self.at < w.now:
            problems.append("安排的时间已经过了")
        for i, raw in enumerate(self.ops):
            try:
                parse(raw)
            except ValueError:
                problems.append(f"第 {i + 1} 个操作格式不对")
        return problems

    def settle(self, w: World, oid: str):
        w.rec.scheduled[self.event] = Scheduled(id=self.event, at=self.at, text=self.text, actor=self.actor, place=self.place,
                                                participants=list(self.participants), ops=list(self.ops), reads=dict(self.reads))  # fmt: skip
        return w.emit("schedule", f"安排：{self.text}", actor=self.actor, participants=self.participants, place=self.place,
                      visibility="private", op=oid), None  # fmt: skip


class Enlist(Base):
    """Make an NPC an important one, with goals of its own (or update them)."""

    kind: Literal["enlist"] = "enlist"
    npc: str
    goals: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)
    mood: str = ""

    def check(self, w: World) -> list[str]:
        if _npc(w, self.npc) is None:
            return [f"{self.npc} 不是 NPC"]
        if self.npc not in w.rec.agents and len(w.rec.agents) >= MAX_AGENTS:
            return [f"重要 NPC 最多 {MAX_AGENTS} 个"]
        return [f"地点 {p} 不存在" for p in self.places if p not in w.rec.entities]

    def settle(self, w: World, oid: str):
        a = w.rec.agents.get(self.npc)
        if a is None:
            n = next((x for x in w.m.npcs if x.id == self.npc), None)
            a = w.rec.agents[self.npc] = Agent(npc=self.npc, goals=self.goals or ([n.goal] if n and n.goal else []), changed=w.rec.seq,
                                               seen=w.rec.seq, acted=w.now)  # fmt: skip
        else:
            w.touch(a)
            a.goals = self.goals or a.goals
        a.resources = self.resources or a.resources
        a.places = list(dict.fromkeys([*a.places, *self.places]))
        a.mood = self.mood or a.mood
        return None, None


class SetPlan(Base):
    kind: Literal["plan"] = "plan"
    npc: str
    steps: list[Step] = Field(max_length=6)
    goals: list[str] | None = None
    mood: str = ""
    reads: dict[str, int] = Field(default_factory=dict, description="这个计划依据的事实和人物的版本")

    def check(self, w: World) -> list[str]:
        if self.npc not in w.rec.agents:
            return [f"{self.npc} 不是重要 NPC"]
        ids = [s.id for s in self.steps]
        problems = ["步骤 id 重复"] if len(set(ids)) != len(ids) else []
        for s in self.steps:
            for raw in s.ops:
                try:
                    parse(raw)
                except ValueError:
                    problems.append(f"步骤 {s.id} 里有格式不对的操作")
        return problems

    def settle(self, w: World, oid: str):
        a = w.rec.agents[self.npc]
        done = [s for s in a.plan if s.status != "pending"]
        a.plan = [*done[-6:], *(s.model_copy(deep=True) for s in self.steps)]
        a.reads = dict(self.reads)
        if self.goals is not None:
            a.goals = list(self.goals)
        a.mood = self.mood or a.mood
        w.touch(a)
        text = f"{w.name(self.npc)}打算：" + "；".join(s.text for s in self.steps)
        return w.emit("plan", text, actor=self.npc, participants=[self.npc], visibility="private", witnesses=[], op=oid), None


class AdvancePlan(Base):
    kind: Literal["step"] = "step"
    npc: str
    step: str
    status: Literal["done", "blocked", "dropped"]
    note: str = ""

    def check(self, w: World) -> list[str]:
        a = w.rec.agents.get(self.npc)
        if a is None:
            return [f"{self.npc} 不是重要 NPC"]
        s = next((s for s in a.plan if s.id == self.step), None)
        if s is None:
            return [f"{self.npc} 的计划里没有步骤 {self.step}"]
        return [] if s.status in ("pending", "blocked") else [f"步骤 {self.step} 已经{s.status}"]

    def settle(self, w: World, oid: str):
        a = w.rec.agents[self.npc]
        s = next(s for s in a.plan if s.id == self.step)
        s.status, s.note = self.status, self.note or s.note
        w.touch(a)
        word = {"done": "做成了", "blocked": "受阻", "dropped": "放弃了"}[self.status]
        return w.emit("step", f"{w.name(self.npc)}{word}：{s.text}" + (f"（{self.note}）" if self.note else ""), actor=self.npc,
                      participants=[self.npc], visibility="private", witnesses=[], op=oid), None  # fmt: skip


# -- backstage ---------------------------------------------------------------------------------
class Rewrite(Base):
    """Rewrite a fact or a private event the players have not met (world/rewrite.py)."""

    kind: Literal["rewrite"] = "rewrite"
    target: str = Field(description="fact:<id> 或 event:<id>")
    text: str = Field("", description="新的内容；retract 时留空")
    retract: bool = Field(False, description="这件事其实不存在")
    known_by: list[str] | None = Field(None, description="改写后谁知道；不写就沿用")
    explains: list[str] | None = Field(None, description="改写后能解释哪些线索；已找到的线索必须仍有解释")
    goals: dict[str, list[str]] = Field(default_factory=dict, description="随之改变的重要 NPC 目标")
    memories: dict[str, str] = Field(default_factory=dict, description="NPC id -> 改写后他记得的样子")

    def check(self, w: World) -> list[str]:
        from . import rewrite

        return rewrite.check(w, self)

    def settle(self, w: World, oid: str):
        from . import rewrite

        return rewrite.settle(w, self, oid)


class Improvise(Base):
    """The director's improvisation (agents/director.Improv), checked and merged into this game's
    module by module/improv.py, then seeded into the record. It changes the module in place,
    so it cannot be undone."""

    kind: Literal["improvise"] = "improvise"
    undoable: ClassVar[bool] = False
    plan: dict[str, Any]
    at: str = ""

    def check(self, w: World) -> list[str]:
        from ..agents.director import Improv
        from ..module import improv

        try:
            plan = Improv.model_validate(self.plan)
        except ValueError as exc:
            return [f"方案格式不对：{str(exc)[:200]}"]
        return improv.check(w.m, plan, at=self.at or w.st.location)

    def settle(self, w: World, oid: str):
        from ..agents.director import Improv
        from ..module import improv

        plan = Improv.model_validate(self.plan)
        applied = improv.apply(w.m, plan)
        w.st.improv.append({"turn": w.st.turn, "kind": plan.kind, "plan": plan.model_dump()})
        w.sync()
        new = [x.id for g in (plan.npcs, plan.locations, plan.items) for x in g]
        facts = [f"clue:{c.id}" for c in plan.clues]
        if plan.kind in ("展开", "后果") and (new or facts):
            tid = f"improv:{oid}"
            w.rec.threads[tid] = Thread(id=tid, kind="question", title=plan.reason[:40] or plan.kind, opened=w.now, opened_turn=w.st.turn,
                                        last=w.now, last_turn=w.st.turn, entities=new, facts=facts)  # fmt: skip
        e = w.emit("improvise", f"即兴（{plan.kind}）：{plan.reason}", actor=DIRECTOR, participants=new, place=self.at or w.st.location,
                   visibility="private", witnesses=[], op=oid)  # fmt: skip
        return e, applied


Op = Annotated[
    Move | Relate | Transfer | Condition | SetFlag | AddFact | Learn | Remember | Occur | MakePromise | Fulfil | Break
    | OpenThread | AdvanceThread | CloseThread | Schedule | Enlist | SetPlan | AdvancePlan | Rewrite | Improvise,
    Field(discriminator="kind"),
]
OPS: TypeAdapter[Op] = TypeAdapter(Op)
KINDS = [m.model_fields["kind"].default for m in (Move, Relate, Transfer, Condition, SetFlag, AddFact, Learn, Remember, Occur,
                                                  MakePromise, Fulfil, Break, OpenThread, AdvanceThread, CloseThread, Schedule,
                                                  Enlist, SetPlan, AdvancePlan, Rewrite, Improvise)]  # fmt: skip


def parse(data: dict[str, Any]) -> Op:
    return OPS.validate_python(data)
