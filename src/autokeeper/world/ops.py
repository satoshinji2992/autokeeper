"""The world operations: every change to this game's world is one of these.

Each op checks itself against the world (check -> problems in plain words) and settles into
it (settle -> the event it logged). store.World.apply adds idempotence, the journal, undo
and the projection into the module. An op's `actor` is who does it: an NPC id, an
investigator id, director, keeper or engine; NPC agents are held to what they know by
world/agents.vet before their ops get here.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.json_schema import SkipJsonSchema

from ..engine import world as legacy_world
from ..engine.effects import Effect
from ..engine.encounter.model import Archive, Begin, EncounterChange, SetParticipant
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

MAX_BRING = 10  # everyday things registered in one turn, by everyone together: a scene's worth, not a shop's stock
STAFF = (ENGINE, DIRECTOR, KEEPER, "arbiter")
# Settled by the game engine alone, from dice it rolled: never by a model, the director, an NPC or
# the keeper's world tools (world/tools.Gateway, world/desk), nor scheduled or planned for later.
ENGINE_ONLY = ("encounter", "effects")
Visibility = Literal["auto", "public", "private"]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    undoable: ClassVar[bool] = True

    # Filled in by whoever lands it (the scheduler, the desk, the game), so a model is not asked for them.
    id: SkipJsonSchema[str] = Field("", description="同一件事重试时用同一个 id，留空则按内容生成")
    actor: SkipJsonSchema[str] = Field(ENGINE, description="谁做的：NPC id、调查员 id，或 director / keeper / engine")
    reason: str = Field("", description="为什么，玩家看不到")
    visibility: Visibility = Field("auto", description="auto：调查员在场就公开；public：当地可见，调查员不在场仍不向玩家展示；private 暗中进行")

    def check(self, w: World) -> list[str]:
        return []

    def settle(self, w: World, oid: str) -> tuple[Event | None, Any]:
        raise NotImplementedError


def _who(w: World, ident: str, *, players: bool = True) -> bool:
    return (players and ident == PLAYERS) or ident in w.rec.entities or any(i.id == ident for i in w.st.investigators)


def _npc(w: World, ident: str) -> Entity | None:
    e = w.rec.entities.get(ident)
    return e if e is not None and e.kind == "npc" else None


# -- names of things -----------------------------------------------------------------------------
_MARKS = re.compile(r"[\s，。、；：？！“”‘’「」『』（）()《》〈〉【】…—·\-,.;:?!\"'`]+")
_COUNT = re.compile(r"^(?:[一二两三四五六七八九十几数半]|\d+)[把根支枝盏个本封张件条块只副瓶袋包盒枚串顶双套份册片罐杯碗捆叠]")


def plain(name: str) -> str:
    """A thing's name as names are compared: no marks or spaces, no count in front (一把、两根)."""
    text = _MARKS.sub("", name).casefold()
    return _COUNT.sub("", text) or text


def overlap(a: str, b: str, *, least: int = 2) -> bool:
    """One name inside the other, compared plain; shorter than `least` characters, only the same name."""
    a, b = plain(a), plain(b)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= least and (a in b or b in a)))


def names(name: str, title: str) -> bool:
    """Whether a thing's name stands for what a title names: the same name, the title inside it, or
    it inside the thing the title is about (after its last 的: 外套口袋里的酒瓶 is about a flask, not a coat)."""
    a, t = plain(name), plain(title)
    return bool(a and t) and (a == t or (len(t) >= 2 and t in a) or overlap(name, title.rsplit("的", 1)[-1]))


def reserved(w: World, name: str) -> str:
    """What of the module's own a name stands for, in words: a clue, a handout or an important item.
    Those come from the module, a found clue or a ruling, never as an everyday thing (Bring)."""
    kept = [(c.title, "线索") for c in w.m.clues] + [(h.title, "手稿") for h in w.m.handouts]
    kept += [(i.name, "重要物件") for i in w.m.items if i.important]
    kept += [(e.name, "重要物件") for e in w.rec.entities.values() if e.kind == "item" and e.important]
    return next((f"{what}「{title}」" for title, what in kept if names(name, title)), "")


# Never anyone's everyday thing, whoever brings it out: arms come from the module, the investigator's
# own gear or a ruling, so a revolver is not had for the asking (Bring).
ARMS = re.compile(r"枪|左轮|子弹|弹药|炸药|火药|雷管|手榴弹|炸弹")


def lacking(w: World, name: str) -> str:
    """What this turn's action lacks (Ruling.missing, kept in the record) that a name stands for."""
    missing = w.rec.missing if w.rec.missing_turn == w.st.turn else []
    return next((x for x in missing if overlap(name, x, least=1)), "")


def holding(w: World, where: str, name: str) -> str:
    """The thing of this name already at `where` (a person or a place): its record id, or the line
    of an investigator's inventory; empty when there is none."""
    hit = next((e.id for e in w.rec.entities.values() if e.kind == "item" and e.at == where and overlap(name, e.name)), "")
    hit = hit or next((i.id for i in w.m.items if overlap(name, i.name) and legacy_world.owner(w.m, w.st, i.id) == where), "")
    if hit:
        return hit
    inv = next((i for i in w.st.investigators if i.id == where), None)
    return next((line for line in inv.inventory if overlap(name, line)), "") if inv is not None else ""


def bring_count(w: World) -> int:
    """Everyday things registered this turn."""
    return sum(1 for e in w.rec.events if e.kind == "bring" and e.turn == w.st.turn)


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
        if e.following and self.actor != ENGINE:  # only the party's own travel takes a companion along
            return [f"{e.name}正与{w.name(e.following)}同行：先用 follow 当面结束同行，再离开"]
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
        actor = self.actor if self.actor not in STAFF else e.id
        place = self.to
        text = f"{e.name}从{w.name(src)}去了{w.name(self.to)}"
        if vis != "private" and w.st.location == src:
            place, text = src, f"{e.name}离开了{w.name(src)}"
        elif vis != "private" and w.st.location == self.to:
            text = f"{e.name}来到{w.name(self.to)}"
        moved = w.emit("move", text, actor=actor, participants=[e.id], place=place,
                       visibility=vis, witnesses=seeing, op=oid)  # fmt: skip
        if self.visibility != "private":
            departing, arriving = w.observers(src), w.observers(self.to)
            moved.witnesses = list(dict.fromkeys([*moved.witnesses, *departing, *arriving]))
            w.observe(src, departing, moved.id, leaving=e.id)
            w.observe(self.to, arriving, moved.id)
        if self.trace and src:  # what it left behind stays where it was, for whoever comes there next
            w.emit("trace", self.trace, actor=actor, participants=[e.id], place=src, visibility="private", witnesses=[],
                   trace=self.trace, op=oid)  # fmt: skip
        return moved, None


class Follow(Base):
    kind: Literal["follow"] = "follow"
    npc: str = Field(description="同意同行的 NPC id，只能替自己同意")
    leader: str = Field("", description="同行的调查员 id；结束同行时留空")
    destination: str = Field("", description="同意同行的目的地 id；结束同行时留空")

    def check(self, w: World) -> list[str]:
        e = _npc(w, self.npc)
        if e is None:
            return [f"{self.npc} 不是 NPC"]
        if self.actor not in (*STAFF, self.npc):
            return ["不能替其他 NPC 同意同行"]
        if not self.leader and not self.destination:
            return []
        if self.leader not in {i.id for i in w.st.investigators}:
            return ["同行者不是调查员"]
        if e.at != w.st.location or e.status in ("死亡", "失踪"):
            return ["NPC 不在调查员身边，不能开始同行"]
        if not self.destination and self.actor in STAFF:  # the keeper's scene: along wherever they go, until it ends
            return []
        dest = w.rec.entities.get(self.destination)
        return [] if dest and dest.kind == "location" else ["同行目的地不存在"]

    def settle(self, w: World, oid: str):
        e = w.rec.entities[self.npc]
        e.following, e.follow_to = self.leader, self.destination
        w.touch(e)
        text = (f"{e.name}同意与{w.name(self.leader)}同行至{w.name(self.destination)}" if self.leader and self.destination
                else f"{e.name}跟着{w.name(self.leader)}" if self.leader else f"{e.name}结束了同行")
        return w.emit("follow", text, actor=self.npc, place=e.at, visibility=self.visibility, op=oid), None


class Relate(Base):
    kind: Literal["relate"] = "relate"
    a: str = Field(description="谁的看法")
    b: str = Field(description="对谁")
    rel: str = Field(description="关系，比如 信任、恐惧、欠情、敌对、盟友")
    value: int | None = Field(None, ge=-100, le=100, description="直接设成多少，-100..100")
    delta: int | None = Field(None, ge=-100, le=100, description="增减多少")
    note: str = Field("", description="一句话：因为什么")
    known_by: list[str] | None = Field(None, description="谁知道这层关系；默认只有持有看法的人，公开表态时才列入对方")
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
        informed = self.known_by if self.known_by is not None else [self.a]
        r.known_by = list(dict.fromkeys([*r.known_by, *informed]))
        text = f"{w.name(self.a)}对{w.name(self.b)}的{self.rel}：{old}→{r.value}" + (f"（{self.note}）" if self.note else "")
        e = w.emit("relate", text, actor=self.actor, participants=informed, place=w.place(self.a),
                   visibility="private" if self.visibility == "auto" else self.visibility, op=oid)  # fmt: skip
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

    def _item_id(self, w: World) -> str | None:
        return legacy_world.item_id(w.m, w.st, self.item,
                                    holder=self.giver or (self.actor if self.actor not in STAFF else ""))

    def check(self, w: World) -> list[str]:
        ident = self._item_id(w)
        if ident is None:
            return [f"物件名称 {self.item} 不能唯一确定，请使用物件 id"]
        before = legacy_world.owner(w.m, w.st, ident)
        if not before:
            return [f"物件 {self.item} 不存在或不知道在谁手里"]
        if self.giver and self.giver != before:
            return [f"{self.item} 现在不在 {self.giver} 手里"]
        if not _who(w, self.to, players=False):
            return [f"{self.to} 不存在"]
        if (item := w.rec.entities.get(ident)) is not None and item.quantity <= 0:
            return [f"物件 {self.item} 已耗尽，不能转交"]
        return [f"{self.item} 已经在 {self.to} 手里"] if before == self.to else []

    def settle(self, w: World, oid: str):
        st, m = w.st, w.m
        key = self._item_id(w)
        assert key is not None  # checked against the same record, with no intervening await
        before = legacy_world.owner(m, st, key)
        item = next((x for x in m.items if x.id == key), None)
        ent = w.rec.entities.get(key)
        name = ent.name if ent is not None else item.name if item else key
        for inv in st.investigators:  # the same bookkeeping as engine/world.apply
            if inv.id == before and name in inv.inventory:
                inv.inventory.remove(name)
            if inv.id == self.to and name not in inv.inventory:
                inv.inventory.append(name)
        st.item_owners[key] = self.to
        if ent is None and item is not None and item.important:
            ent = w.rec.entities[key] = Entity(id=key, kind="item", name=name, origin="game", changed=w.rec.seq)
        elif ent is not None:
            w.touch(ent)
        if ent is not None:
            ent.at, ent.equipment = self.to, "carried"
            destination = w.rec.entities.get(self.to)
            ent.hidden = self.visibility == "private" and destination is not None and destination.kind == "location"
        text = self.text or f"{name}从{w.name(before)}到了{w.name(self.to)}手里"
        w.legacy(self.key, "transfer", text, self.tag or next((x for x in (before, self.to) if _npc(w, x)), ""), self.actor)
        event = w.emit("transfer", text, actor=self.actor, participants=[before, self.to, key], place=w.place(before),
                       visibility=self.visibility, op=oid)
        if ent is not None:
            ent.observed_by = list(dict.fromkeys([before, self.to, *event.witnesses,
                                                  *([PLAYERS] if event.visibility == "public" else [])]))
        return event, None


class Bring(Base):
    """An everyday thing registered as it comes up where the investigators are: a torch the keeper
    hands over, a poker an NPC takes from the fireplace, a cane picked up in the hall. Not an
    important item; never one with the name of a clue, a handout or an important item, nor what
    this turn's action lacks (Ruling.missing): those come from the module, a found clue or a ruling."""

    kind: Literal["bring"] = "bring"
    name: str = Field(description="东西的名称，如 手电筒、拨火棍")
    look: str = Field("", description="一句外观")
    holder: str = Field("", description="拿着它的人：调查员或 NPC id；放在那里没人拿就留空，填 place")
    place: str = Field("", description="它放在哪个地点；有人拿着就留空")
    item: str = Field("", description="新物件的 id，留空由系统生成")
    source: SkipJsonSchema[str] = Field("", description="已实际取得的手稿线索；只由引擎落实守秘人明确写出的持有场面")

    def check(self, w: World) -> list[str]:
        name, here = self.name.strip(), w.st.location
        if not name or len(name) > 16:
            return ["东西的名称要写，且不超过 16 个字"]
        if bool(self.holder) == bool(self.place):
            return ["holder 和 place 填一个：有人拿着填 holder，放在那里填 place"]
        problems = [] if len(self.look) <= 80 else ["外观写一句，不超过 80 字"]
        document = next((c for c in w.m.clues if c.id == self.source and c.id in w.st.clues and c.handout
                         and c.handout in w.st.handouts), None)
        sourced = document is not None and self.actor in STAFF and any(
            names(name, label) for label in (document.title, w.m.handout(document.handout).title))
        if self.source and not sourced:
            problems.append("材料来源尚未实际取得，不能登记成已经拿到的证物")
        where = self.holder or self.place
        if self.holder and not (any(i.id == self.holder for i in w.st.investigators)
                                or ((e := _npc(w, self.holder)) is not None and e.at == here and e.status not in ("死亡", "失踪"))):
            problems.append(f"{w.name(self.holder)}不在调查员所在的{w.name(here)}")
        if self.place and self.place != here:
            problems.append(f"{w.name(self.place)}不是调查员此刻所在的地方")
        if self.item and self.item in w.rec.entities:
            problems.append(f"id {self.item} 已经用过")
        if not sourced and (clash := reserved(w, name)):  # an NPC never hears of the module's hidden things
            problems.append(f"「{name}」和{clash}重名：这样的东西只由模组、线索或裁定给出" if self.actor in STAFF
                            else f"「{name}」不是随手就有的日常物件，不能当场拿出来")
        if not sourced and ARMS.search(name):
            problems.append(f"「{name}」不是随手就有的日常物件：枪支、弹药和炸药只由模组、调查员的装备或裁定给出")
        if not sourced and (gap := lacking(w, name)):
            problems.append(f"这一轮的行动正缺{gap}，不能当场拿出来")
        if have := holding(w, where, name):
            problems.append(f"{w.name(where)}已经有{w.name(have)}了")
        if bring_count(w) >= MAX_BRING:
            problems.append(f"这一轮已经登记了 {MAX_BRING} 件东西")
        return problems

    def settle(self, w: World, oid: str):
        name, look = self.name.strip(), self.look.strip()
        where = self.holder or self.place
        ident = self.item or f"thing:{hashlib.sha256(oid.encode()).hexdigest()[:10]}"
        ent = w.rec.entities[ident] = Entity(id=ident, kind="item", name=name, origin="game", at=where,
                                             important=bool(self.source),
                                             look=look, changed=w.rec.seq)
        w.st.item_owners[ident] = where
        for inv in w.st.investigators:
            if inv.id == self.holder and name not in inv.inventory:
                inv.inventory.append(name)
        seen = f"（{look}）" if look else ""
        if self.place:
            text = f"{w.name(self.place)}有{name}{seen}"
        else:
            text = f"{w.name(self.holder)}{'拿出了' if self.actor == self.holder else '拿着'}{name}{seen}"
        actor = self.actor if self.actor not in STAFF else self.holder or ENGINE
        # Seen, not news: nobody is woken to think again over a torch (scheduler._affects).
        event = w.emit("bring", text, actor=actor, participants=[x for x in (self.holder, ident) if x], place=w.st.location,
                       visibility=self.visibility, op=oid, reactive=False)
        ent.observed_by = list(dict.fromkeys([*([self.holder] if self.holder else []), *event.witnesses,
                                              *([PLAYERS] if event.visibility == "public" else [])]))
        return event, ident


class Equip(Base):
    """Important equipment stays backstage until a witnessed action explicitly reveals it."""

    kind: Literal["equip"] = "equip"
    item: str = Field(description="已登记的重要物品 id，不登记普通生活杂物")
    equipment: Literal["carried", "worn", "held", "concealed"] | None = Field(None, description="携带、穿戴、手持或藏匿")
    quantity: int | None = Field(None, ge=0, description="重要物品余量；NPC只能减少自己已有的数量")
    status: str | None = Field(None, max_length=100, description="物件的实际状况，不凭空增加能力")
    reveal: bool = Field(False, description="明确发现、搜身或主动出示时才公开，不因NPC在场就揭示装备")
    evidence: str = Field("", max_length=200, description="公开时必须写实际可见的发现或出示经过")

    def _item(self, w: World) -> Entity | None:
        ident = legacy_world.item_id(w.m, w.st, self.item, holder=self.actor if self.actor not in STAFF else "")
        if ident is None:
            return None
        if existing := w.rec.entities.get(ident):
            # Explicit equipment maintenance promotes the actual everyday object,
            # never a second object under the inventory's display name.
            return existing if existing.important else existing.model_copy(update={"important": True})
        holder = legacy_world.owner(w.m, w.st, ident)
        if holder and _who(w, holder, players=False):
            # Explicit promotion adds metadata for an actual carried object, never a
            # new possession. Using its original name preserves old inventory keys.
            template = next((i for i in w.m.items if i.id == ident), None)
            return Entity(id=ident, kind="item", name=template.name if template else ident, origin="game", at=holder)
        return None

    def check(self, w: World) -> list[str]:
        item = self._item(w)
        if item is None or item.kind != "item" or not item.important:
            return [f"重要物品 {self.item} 未登记"]
        if self.equipment is None and self.quantity is None and self.status is None and not self.reveal:
            return ["装备操作必须改变携带方式、数量、状况，或明确公开"]
        if self.actor not in STAFF and item.at != self.actor:
            return ["只能整理自己实际持有的装备"]
        if self.actor not in STAFF and self.quantity is not None and self.quantity > item.quantity:
            return ["不能凭空增加装备数量"]
        if self.reveal and (not self.evidence.strip() or w.place(item.at) != w.st.location):
            return ["公开装备需要当前场景中实际发生的发现或出示记录"]
        holder = w.rec.entities.get(item.at)
        if self.equipment in {"held", "worn"} and (holder is None or holder.kind not in {"npc", "investigator"}):
            return ["手持或穿戴的物品必须实际由人物持有"]
        if self.equipment in {"held", "worn"} and (self.quantity if self.quantity is not None else item.quantity) <= 0:
            return ["耗尽的物品不能作为可用装备"]
        return []

    def settle(self, w: World, oid: str):
        item = self._item(w)
        assert item is not None  # check verified the original inventory or ownership record
        w.rec.entities[item.id] = item
        w.st.item_owners.setdefault(item.id, item.at)
        changed = self.equipment is not None or self.quantity is not None or self.status is not None
        if self.equipment is not None:
            item.equipment = self.equipment
            item.hidden = self.equipment == "concealed"
        if self.quantity is not None:
            item.quantity = self.quantity
            if not item.quantity:
                item.equipment = "carried"
            for inv in w.st.investigators:
                if inv.id == item.at:
                    if not item.quantity and item.name in inv.inventory:
                        inv.inventory.remove(item.name)
                    elif item.quantity and item.name not in inv.inventory:
                        inv.inventory.append(item.name)
        if self.status is not None:
            item.status = self.status
        if changed:
            item.observed_by = [item.at]  # previous observers do not magically learn a private change
        vis = ("private" if self.visibility == "private" else "public") if self.reveal else "private"
        text = self.evidence if self.reveal else f"整理重要装备：{item.name}（{item.equipment}，数量{item.quantity}）"
        event = w.emit("equip", text, actor=self.actor, participants=[item.at], place=w.place(item.id), visibility=vis, op=oid)
        if self.reveal:
            item.observed_by = list(dict.fromkeys([*item.observed_by, *event.witnesses, PLAYERS]))
        w.touch(item)
        return event, None


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


# -- bodies, things and encounters (the engine's) ------------------------------------------------------
def _engine_only(op: Base) -> list[str]:
    return [] if op.actor == ENGINE else ["伤害、效果与遭遇只由游戏引擎按掷骰结算"]


class Effects(Base):
    """What happened to people and things outside an encounter, already rolled (engine/effects.py):
    a trap's or a fall's wound, an event's, a plugin's. All land or none."""

    kind: Literal["effects"] = "effects"
    effects: list[Effect] = Field(default_factory=list)
    source: str = ""  # what rolled them: event:<id>, fail:<turn>, plugin:<id>, fight:<…>

    def check(self, w: World) -> list[str]:
        from ..engine import effects

        if problems := _engine_only(self):
            return problems
        return effects.problems(w, self.effects)

    def settle(self, w: World, oid: str):
        from ..engine import effects

        done = effects.settle(w, self.effects, oid=oid)
        return _last(w, oid), done


class EncounterStep(Base):
    """One unit of a fight or a chase (engine/encounter): the encounter's changes and their effects
    on people and things, together, so a unit lands whole or not at all and its id lands it once.
    Everyone who joins it (Begin, SetParticipant) has their body kept by the world from then on."""

    kind: Literal["encounter"] = "encounter"
    encounter: str
    unit: str = ""
    effects: list[Effect] = Field(default_factory=list)
    changes: list[EncounterChange] = Field(default_factory=list)

    def check(self, w: World) -> list[str]:
        from ..engine import effects
        from ..engine.encounter.model import apply

        if problems := _engine_only(self):
            return problems
        enc = w.st.encounter
        begins = any(isinstance(c, Begin) for c in self.changes)
        if not begins and (enc is None or enc.id != self.encounter):
            return [f"遭遇 {self.encounter} 不在进行中"]
        if begins and enc is not None and enc.id != self.encounter:
            return [f"遭遇 {enc.id} 还没有结束，不能开始 {self.encounter}"]
        try:
            apply(enc, self.changes)
        except ValueError as exc:
            return [str(exc)]
        return effects.problems(w, self.effects)

    def settle(self, w: World, oid: str):
        from ..engine import effects
        from ..engine.encounter.model import KEEP_ENCOUNTERS, apply

        for c in self.changes:  # whoever joins: the world keeps their body from now on
            joined = c.state.participants if isinstance(c, Begin) else [c.participant] if isinstance(c, SetParticipant) else []
            for p in joined:
                if p.kind != "investigator":
                    effects.seed(w, p.id)
        done = effects.settle(w, self.effects, oid=oid)
        w.st.encounter = apply(w.st.encounter, self.changes)
        for c in self.changes:
            if isinstance(c, Archive):
                w.st.encounter_log = [*w.st.encounter_log, c.summary.model_copy(deep=True)][-KEEP_ENCOUNTERS:]
        return _last(w, oid), done


def _last(w: World, oid: str) -> Event | None:
    """The last event this op emitted, for its journal entry."""
    return next((e for e in reversed(w.rec.events[-16:]) if e.op == oid), None)


# -- facts and knowledge -----------------------------------------------------------------------
class AddFact(Base):
    kind: Literal["fact"] = "fact"
    fact: str = Field(description="新事实的 id，英文小写加下划线")
    text: str = Field(description="一句话，已经发生或确实如此的事")
    fact_kind: Literal["state", "secret", "knows", "lie", "rumor", "relation", "event", "setting"] = "state"
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
        # Being known to the investigators does not make a private conversation audible
        # to every NPC in the room. Its event keeps only the actual participants.
        vis = "private" if self.visibility == "private" else ("public" if public else ("private" if self.visibility == "auto" else self.visibility))
        e = w.emit("fact", self.text, actor=self.actor, participants=list(self.about), place=w.place(self.actor) or w.st.location,
                   visibility=vis, witnesses=f.known_by, facts=[f.id], op=oid)  # fmt: skip
        if public and vis == "private" and e.place == w.st.location:
            from .knowledge import reveal

            reveal(w.rec, f, w.st.turn, source=f"private-event:{e.id}", excerpt=e.text)
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
        private = self.visibility == "private"
        vis = "private" if private else ("public" if PLAYERS in self.who else ("private" if self.visibility == "auto" else self.visibility))
        place = w.place(self.actor) or w.place(next(iter(tell), "")) or (w.st.location if PLAYERS in self.who else "")
        e = w.emit("learn", text, actor=self.actor, participants=tell, place=place,
                   visibility=vis, witnesses=tell, facts=[f.id], op=oid)  # fmt: skip
        if PLAYERS in self.who and private and e.place == w.st.location:
            from .knowledge import reveal

            reveal(w.rec, f, w.st.turn, source=f"private-event:{e.id}", excerpt=e.text)
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
    reactive: SkipJsonSchema[bool] = True  # engine classification of greeting-only dialogue, not an NPC discretion
    transcript: SkipJsonSchema[bool] = False  # complete investigator/NPC exchange, supplied only by the engine
    witnesses: SkipJsonSchema[list[str]] = Field(default_factory=list)  # who else knows of it: the module's past (agents.marked)

    def check(self, w: World) -> list[str]:
        problems = [] if self.text.strip() and (self.transcript or len(self.text) <= 300) else ["要写发生了什么，不超过 300 字"]
        if self.transcript and self.actor not in {i.id for i in w.st.investigators}:
            problems.append("完整对话记录只能由系统登记调查员实际参与的交流")
        if self.witnesses and self.actor not in STAFF:
            problems.append("另外还有谁知道，只能由系统登记")
        if self.place and self.place not in w.rec.entities:
            problems.append(f"地点 {self.place} 不存在")
        problems += [f"{x} 不存在" for x in self.participants if not _who(w, x)]
        problems += [f"事实 {f} 不存在" for f in self.facts if f not in w.rec.facts]
        return problems

    def settle(self, w: World, oid: str):
        return w.emit("occur", self.text, actor=self.actor, participants=self.participants, place=self.place,
                      visibility=self.visibility, facts=self.facts, trace=self.trace, op=oid, witnesses=self.witnesses,
                      reactive=self.reactive, verbatim=self.transcript), None  # fmt: skip


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
    text: str = Field(description="到时发生什么，一两句")
    place: str = Field("", description="在哪个地点")
    participants: list[str] = Field(default_factory=list, description="谁参与")
    ops: list[dict[str, Any]] = Field(default_factory=list, description="到时结算的操作")
    reads: SkipJsonSchema[dict[str, int]] = Field(default_factory=dict)

    def check(self, w: World) -> list[str]:
        problems = [f"安排 {self.event} 已经存在"] if self.event in w.rec.scheduled else []
        if self.at < w.now:
            problems.append("安排的时间已经过了")
        for i, raw in enumerate(self.ops):
            try:
                parse(raw)
            except ValueError:
                problems.append(f"第 {i + 1} 个操作格式不对")
            if isinstance(raw, dict) and raw.get("kind") in ENGINE_ONLY:
                problems.append(f"第 {i + 1} 个操作只能由游戏引擎当场结算，不能安排到以后")
        return problems

    def settle(self, w: World, oid: str):
        w.rec.scheduled[self.event] = Scheduled(id=self.event, at=self.at, text=self.text, actor=self.actor, place=self.place,
                                                participants=list(self.participants), ops=list(self.ops), reads=dict(self.reads))  # fmt: skip
        # Bookkeeping, not something that happened: the people it names learn of it when it comes about.
        return w.emit("schedule", f"安排：{self.text}", actor=self.actor, place=self.place, visibility="private", witnesses=[], op=oid), None


class Enlist(Base):
    """Make an NPC an important one, with goals of its own (or update them)."""

    kind: Literal["enlist"] = "enlist"
    npc: str = Field(description="NPC id")
    goals: list[str] = Field(default_factory=list, description="他想要的，一两条")
    resources: list[str] = Field(default_factory=list, description="他能动用的人和东西")
    places: list[str] = Field(default_factory=list, description="除了公开的地方，他还知道哪些地点 id")
    mood: str = Field("", description="此刻的心境")
    passive: SkipJsonSchema[bool] = False  # only its fixed steps run (record.Agent.passive); set by the engine alone

    def check(self, w: World) -> list[str]:
        if _npc(w, self.npc) is None:
            return [f"{self.npc} 不是 NPC"]
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
        if self.goals:
            a.goal_reason, a.goal_source = self.reason, oid
        a.resources = self.resources or a.resources
        a.places = list(dict.fromkeys([*a.places, *self.places]))
        a.mood = self.mood or a.mood
        a.passive = self.passive
        return None, None


class SetPlan(Base):
    kind: Literal["plan"] = "plan"
    npc: str = Field(description="重要 NPC 的 id")
    steps: list[Step] | None = Field(None, max_length=6, description="替换接下来未完成的几步，按先后排；空列表撤销旧待办，不填保留")
    goals: list[str] | None = Field(None, description="目标也变了才写")
    mood: str = Field("", description="此刻的心境")
    reads: SkipJsonSchema[dict[str, int]] = Field(default_factory=dict)  # versions the plan was made from (scheduler fills them)

    def check(self, w: World) -> list[str]:
        from .agents import condition_problems

        if self.npc not in w.rec.agents:
            return [f"{self.npc} 不是重要 NPC"]
        ids = [s.id for s in self.steps or []]
        problems = ["步骤 id 重复"] if len(set(ids)) != len(ids) else []
        for s in self.steps or []:
            if s.status != "pending":
                problems.append(f"步骤 {s.id} 是新计划，不能预先标记为 {s.status}")
            if s.completion == "report" and (s.achieved.strip() or s.ops):
                problems.append(f"步骤 {s.id} 的 report 只记录本人私有工作报告，不能同时填写 achieved 或 ops；"
                                "有移动、交物等实际结果的步骤请用 completion=state")
            problems += [f"步骤 {s.id}：{p}" for p in condition_problems(w, s.when)]
            problems += [f"步骤 {s.id} 完成条件：{p}" for p in condition_problems(w, s.achieved)]
            for raw in s.ops:
                try:
                    parse(raw)
                except ValueError:
                    problems.append(f"步骤 {s.id} 里有格式不对的操作")
                if isinstance(raw, dict) and raw.get("kind") in ENGINE_ONLY:
                    problems.append(f"步骤 {s.id} 里的伤害与遭遇只能由游戏引擎当场结算")
        return problems

    def settle(self, w: World, oid: str):
        a = w.rec.agents[self.npc]
        incoming = {s.id for s in self.steps or []}
        retired = [s.model_copy(deep=True, update={"status": "dropped", "note": self.reason or "已由新计划替换"})
                   if s.status in {"pending", "blocked"} else s for s in a.plan if s.id not in incoming]
        steps = []
        for s in self.steps or []:
            s = s.model_copy(deep=True, update={"started": w.rec.seq})
            if not s.achieved:
                final = {}
                for raw in s.ops:
                    op = parse(raw)
                    if isinstance(op, Move):
                        final[f"at:{op.entity}"] = op.to
                    elif isinstance(op, Transfer):
                        final[f"owner:{op.item}"] = op.to
                s.achieved = " & ".join(f"{key}:{value}" for key, value in final.items())
            steps.append(s)
        if self.steps is not None:
            a.plan = [*retired[-6:], *steps]
        # Its agent version validates the decision's input, but writing this very
        # plan (and its subsequent mood/seen bookkeeping) changes that version.
        # Keep actual external dependencies for future execution, not this self-write.
        if self.steps is not None:
            a.reads = {ref: version for ref, version in self.reads.items() if ref != f"agent:{self.npc}"}
        if self.goals is not None:
            a.goals = list(self.goals)
            a.goal_reason, a.goal_source = self.reason, oid
        a.mood = self.mood or a.mood
        w.touch(a)
        text = f"{w.name(self.npc)}打算：" + "；".join(s.text for s in (a.plan if self.steps is None else self.steps) if s.status == "pending")
        return w.emit("plan", text, actor=self.npc, participants=[self.npc], visibility="private", witnesses=[], op=oid), None


class AdvancePlan(Base):
    kind: Literal["step"] = "step"
    npc: str
    step: str
    status: Literal["done", "blocked", "dropped"]
    note: str = Field("", description="完成 report 步骤时必填：具体说明该人物的私有工作及判断；NPC仅能报告自己的工作，主持可代记；不是外部事实、交付或线索发现")

    def check(self, w: World) -> list[str]:
        a = w.rec.agents.get(self.npc)
        if a is None:
            return [f"{self.npc} 不是重要 NPC"]
        s = next((s for s in reversed(a.plan) if s.id == self.step), None)
        if s is None:
            return [f"{self.npc} 的计划里没有步骤 {self.step}"]
        if s.status not in ("pending", "blocked"):
            return [f"步骤 {self.step} 已经{s.status}"]
        if self.status != "done":
            return []
        from .agents import holds, meeting

        if s.completion == "report":
            if s.achieved.strip() or s.ops:
                return [f"步骤 {self.step} 的 report 不能同时填写 achieved 或 ops；实际结果请用 completion=state"]
            if self.actor != self.npc and self.actor not in STAFF:
                return ["NPC只能提交自己的 report 工作完成报告，主持角色可代记"]
            return [] if self.note.strip() else [f"步骤 {self.step} 的 report 完成须填写具体 note，说明本人做了哪些工作、形成什么判断；"
                                                "不以报告代替外部状态变化"]

        if meeting(w, self.npc, s):  # what it does on meeting them is its own to play: played, it is done
            return []

        if not s.started and not s.achieved and not s.ops:
            return [f"步骤 {self.step} 是缺少完成依据的旧计划；先重新规划并明确 achieved 条件或执行操作；"
                    "仅本人定性工作可在新计划中显式选择 completion=report，完成时提交具体 note"]
        if s.achieved:
            return [] if holds(w, s.achieved) else [f"步骤 {self.step} 的实际完成条件尚未满足：{s.achieved}"]
        # Old plans have no declared outcome. Require an actual action, not another plan,
        # memory, private note, or the model merely saying that it was done.
        actions = {"move", "follow", "transfer", "occur", "learn", "promise", "fulfil", "break", "condition"}
        if any(e.seq > s.started and e.actor == self.npc and e.kind in actions and e.t == w.now for e in w.rec.events):
            return []
        return [f"步骤 {self.step} 尚无实际执行记录；先执行行动，或为计划填写可验证的 achieved 条件；"
                "仅本人定性工作可在新计划中显式选择 completion=report，不能用报告代替移动或交物"]

    def settle(self, w: World, oid: str):
        a = w.rec.agents[self.npc]
        s = next(s for s in reversed(a.plan) if s.id == self.step)
        s.status, s.note = self.status, self.note or s.note
        w.touch(a)
        word = {"done": "做成了", "blocked": "受阻", "dropped": "放弃了"}[self.status]
        if self.status == "done" and s.completion == "report":
            word = "报告完成"
        return w.emit("step", f"{w.name(self.npc)}{word}：{s.text}" + (f"（{self.note}）" if self.note else ""), actor=self.npc,
                      participants=[self.npc], visibility="private", witnesses=[], op=oid), None  # fmt: skip


def execution_order[T](ops: list[T]) -> list[tuple[int, T]]:
    """Effects retain their order; completion receipts check their plan version last.

    Keep input indices for operation identities and error reporting. This does not make a
    receipt true: the normal actor, achieved-condition and execution-evidence checks still run.
    """
    def completion(item: tuple[int, T]) -> bool:
        op = item[1]
        return ((isinstance(op, AdvancePlan) and op.status == "done")
                or (isinstance(op, dict) and op.get("kind") == "step" and op.get("status") == "done"))

    ordered: list[tuple[int, T]] = []
    segment: list[tuple[int, T]] = []
    for item in enumerate(ops):
        op = item[1]
        replaces = ((isinstance(op, SetPlan) and op.steps is not None)
                    or (isinstance(op, dict) and op.get("kind") == "plan" and op.get("steps") is not None))
        if replaces:
            ordered.extend(sorted(segment, key=completion))
            segment.clear()
            ordered.append(item)
        else:
            segment.append(item)
    return [*ordered, *sorted(segment, key=completion)]


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
    # Host-only: registering a scene's person does not by itself open a mystery. Ordinary director
    # packages retain their existing question thread; this is not another model-facing option.
    thread: SkipJsonSchema[bool] = True

    def check(self, w: World) -> list[str]:
        from ..agents.director import Improv
        from ..module import improv

        try:
            plan = Improv.model_validate(self.plan)
        except ValueError as exc:
            return [f"方案格式不对：{str(exc)[:200]}"]
        return improv.check(w.m, plan, at=self.at or w.st.location, actors={inv.id for inv in w.st.investigators},
                            player_names={inv.name for inv in w.st.investigators})

    def settle(self, w: World, oid: str):
        from ..agents.director import Improv
        from ..module import improv

        plan = Improv.model_validate(self.plan)
        applied = improv.apply(w.m, plan)
        w.st.improv.append({"turn": w.st.turn, "kind": plan.kind, "plan": plan.model_dump()})
        w.sync()
        new = [x.id for g in (plan.npcs, plan.locations, plan.items) for x in g]
        facts = [f"clue:{c.id}" for c in plan.clues]
        if self.thread and plan.kind in ("展开", "后果") and (new or facts):
            tid = f"improv:{oid}"
            w.rec.threads[tid] = Thread(id=tid, kind="question", title=plan.reason[:40] or plan.kind, opened=w.now, opened_turn=w.st.turn,
                                        last=w.now, last_turn=w.st.turn, entities=new, facts=facts)  # fmt: skip
        e = w.emit("improvise", f"即兴（{plan.kind}）：{plan.reason}", actor=DIRECTOR, participants=new, place=self.at or w.st.location,
                   visibility="private", witnesses=[], op=oid)  # fmt: skip
        return e, applied


Op = Annotated[
    Move | Follow | Relate | Transfer | Bring | Equip | Condition | SetFlag | AddFact | Learn | Remember | Occur | MakePromise | Fulfil
    | Break | OpenThread | AdvanceThread | CloseThread | Schedule | Enlist | SetPlan | AdvancePlan | Rewrite | Improvise
    | Effects | EncounterStep,
    Field(discriminator="kind"),
]
OPS: TypeAdapter[Op] = TypeAdapter(Op)
KINDS = [m.model_fields["kind"].default for m in (Move, Follow, Relate, Transfer, Bring, Equip, Condition, SetFlag, AddFact, Learn, Remember,
                                                  Occur, MakePromise, Fulfil, Break, OpenThread, AdvanceThread, CloseThread, Schedule,
                                                  Enlist, SetPlan, AdvancePlan, Rewrite, Improvise, Effects, EncounterStep)]  # fmt: skip


def parse(data: dict[str, Any]) -> Op:
    return OPS.validate_python(data)
