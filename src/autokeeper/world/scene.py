"""The keeper's scene: how things stand as a telling ends, and what of it goes into the record.

The keeper writes it before its words (agents/cast.Telling): who is with the investigators, who
walks with them, what changed hands, everyday things brought out, traces left behind, what is going
on as it ends. Nothing here reads the words. The scene is compared with the record, and what differs
becomes world operations, grouped by the people and things they touch (Unit): a person's coming,
going and company together; the investigators' walk between the rooms of one place
(world/context.rooms) with whoever goes along; a handover that needs both hands in one place with the
moves of the people in it; an everyday thing brought out together with its handing on. A unit lands
whole or not at all.

Explicit observations keep what named NPCs actually saw or heard, including the keeper's own
performance of their speech. They are private witnessed events, not physical traces or new facts;
arriving later does not disclose them. No observation is extracted from the telling's words.

A scene does not move the clock: someone comes or goes only as far as the minutes this turn already
took (Context.elapsed, measured with Game._travel). The investigators go somewhere else only on their
own action, ruled and timed by the engine. Someone gone from the scene has left, and went somewhere.
What the scene cannot say: anyone's state or wounds (rules settle those), and nothing for the record
that is not in it.

plan() tries every unit on a copy, people first, then things on a copy that already holds the
people's moves, and says in words the keeper can act on what does not hold. land() lands the units
that hold; their op ids are keeper:{turn}:{i}.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..engine import world as legacy_world
from .context import rooms
from .ops import Bring, Follow, Improvise, Move, Occur, Transfer, names
from .record import ENGINE, KEEPER

if TYPE_CHECKING:
    from .store import World


class Left(BaseModel):
    who: str = Field(description="离开的人")
    to: str = Field(description="去了哪个地点")


class Holding(BaseModel):
    item: str = Field(description="物件")
    holder: str = Field(description="讲完时它在谁手里，或放在哪个地点")


class Thing(BaseModel):
    name: str = Field(description="日常小物件，比如 火柴、一杯热茶")
    holder: str = Field(description="谁拿出来的")


class Trace(BaseModel):
    text: str = Field(description="留下的痕迹，一句话")
    # A plain string rather than an enum: a word off here is read as public, not sent back for repair.
    visibility: str = Field("public", description="public：调查员看得见；private：他们没注意到")

    @property
    def private(self) -> bool:
        return "priv" in self.visibility.lower() or self.visibility.strip() in ("私下", "暗中", "没注意到")


class Observation(BaseModel):
    witnesses: list[str] = Field(description="本段实际在场看见或听见内容的 NPC id 或姓名；只写确实知情者")
    text: str = Field(max_length=300, description="当场获知的重要事实或承诺，一两句；含KP替NPC说过且影响后续回答的话。不是痕迹、推测或尚未完成的结果")


class Person(BaseModel):
    """A lasting person improvised here, without a full character sheet or a scene package."""

    id: str = Field(description="新人物的 id，英文小写加下划线；已有的人只写进 here")
    name: str = Field(description="姓名或稳定称呼")
    role: str = Field("路人", description="身份，一句话")
    look: str = Field("", description="看得见的外貌、衣着")
    personality: str = Field("", description="性格和处事方式")
    goal: str = Field("", description="眼下想做什么")
    knows: list[str] = Field(default_factory=list, description="亲身知道的事；不能把待调查的结果提前算作他知道")


# How things stand as the telling ends; a field left out is as the record has it. (No docstring: the
# model reads a schema's descriptions, and these are written for it.)
class Scene(BaseModel):
    room: str = Field("", description="讲完时调查员在哪；只在同一处的房间之间走动时写，没走动留空")
    people: list[Person] = Field(default_factory=list, description="现场新出现、后续还会参与的人；只登记必要资料，普通背景人群不用写")
    here: list[str] = Field(default_factory=list, description="讲完时和调查员在一起的人；没变也照抄")
    follow: list[str] = Field(default_factory=list, description="here 里跟着调查员走的人")
    left: list[Left] = Field(default_factory=list, description="这段里离开的人和去向")
    holds: list[Holding] = Field(default_factory=list, description="这段里换了手的物件")
    things: list[Thing] = Field(default_factory=list, description="这段里拿出来的日常小物件；递给了别人就再写进 holds")
    traces: list[Trace] = Field(default_factory=list, description="这段里留下的新痕迹")
    observations: list[Observation] = Field(default_factory=list,
                                           description="本段NPC实际看见或听见的重要事实与承诺，明确witnesses；只记影响后续应答的内容，不写逐句流水账")
    flags: list[str] = Field(default_factory=list, description=(
        "本段已经发生、满足完整含义的剧情标记 id；人物本次真正做成的事也在这里登记。"
        "只从已声明标记中选，不将尝试、提议、口头谎言或尚未完成的计划当成事实，不改写骰点和取得条件"))
    now: str = Field("", description="讲完时眼前还在进行的事，一句话，只写调查员看得到的；没有就留空")


@dataclass
class Context:
    """What a scene is checked with, besides the record."""

    travel: Callable[[str, str], int | None]  # minutes between two places, None when there is no way (Game._travel)
    elapsed: int = 0  # minutes this turn already took: how far anyone could have walked
    missing: list[str] = field(default_factory=list)  # what this action lacks (Ruling.missing): not brought out here
    walk: bool = True  # the investigators acted this turn, so they may walk between the rooms of the place
    ids: str = "keeper:0"  # op ids are f"{ids}:{i}"
    start: int = 0  # the first i


@dataclass
class Unit:
    """Changes that land whole or not at all."""

    key: str
    steps: list[dict[str, Any]] = field(default_factory=list)  # world ops; walk, meet and witness are scene checks
    said: list[str] = field(default_factory=list)  # what it changes, in words
    problems: list[str] = field(default_factory=list)  # why it does not hold

    def dump(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls, data: dict[str, Any]) -> Unit:
        return cls(**data)

    @property
    def room(self) -> str:
        return next((s["to"] for s in self.steps if s.get("kind") == "walk"), "")


@dataclass
class Plan:
    units: list[Unit] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # everything that does not hold, the units' and the scene's own
    now: str = ""
    end: str = ""  # how it stands once the units that hold have landed, in words
    flags: list[str] = field(default_factory=list)  # completed story facts; only a whole accepted scene may commit these

    @property
    def held(self) -> list[Unit]:
        return [u for u in self.units if not u.problems]

    @property
    def room(self) -> str:
        return next((u.room for u in self.held if u.room), "")


# -- reading the scene --------------------------------------------------------------------------
def _bare(text: str) -> list[str]:
    """What a reference may be written as: an id, a name, either with the other in brackets."""
    text = text.strip()
    inner = re.findall(r"[（(]([^）)]*)[）)]", text)
    outer = re.sub(r"[（(][^）)]*[）)]", "", text).strip()
    return [x.strip() for x in [text, outer, *inner] if x.strip()]


def _find(w: World, text: str, kinds: tuple[str, ...]) -> str | None:
    """An id, a name, an alias; failing those, the one name inside what was written (钟楼底层: 钟楼)
    or the one name what was written is part of (布道者: 街头布道者)."""
    if not text.strip() or any(x in (i.id, i.name) for i in w.st.investigators for x in _bare(text)):
        return None  # the investigators are not the scene's to place
    known = [(e.id, e.name) for e in w.rec.entities.values() if e.kind in kinds]
    if "location" in kinds:
        known += [(loc.id, a) for loc in w.m.locations for a in loc.aliases]
    for x in _bare(text):
        if (e := w.rec.entities.get(x)) is not None and e.kind in kinds:
            return x
        if found := next((ident for ident, name in known if name == x), None):
            return found
    for x in _bare(text):
        inside = [(ident, name) for ident, name in known if len(name) >= 2 and name in x]
        longest = max((len(name) for _, name in inside), default=0)
        if len(best := {ident for ident, name in inside if len(name) == longest}) == 1:
            return best.pop()
        around = {ident for ident, name in known if len(x) >= 2 and x in name}
        if len(around) == 1:
            return around.pop()
    return None


def _npc(w: World, text: str) -> str | None:
    return _find(w, text, ("npc",))


def _place(w: World, text: str) -> str | None:
    return _find(w, text, ("location",))


def _person(w: World, text: str) -> str | None:
    """An investigator or an NPC."""
    for x in _bare(text):
        if inv := next((i.id for i in w.st.investigators if x in (i.id, i.name)), None):
            return inv
    return _npc(w, text)


def _item(w: World, text: str) -> tuple[str, str] | None:
    """An item the record knows of, and who or where has it: an important one, a module one, or a
    name in an investigator's things."""
    candidates = _bare(text)
    # An explicit id, including one in brackets, is stronger than an outer display
    # name shared by multiple objects. Never pick the first same-named entity.
    for x in candidates:
        e = w.rec.entities.get(x)
        if e is not None and e.kind == "item":
            return x, legacy_world.owner(w.m, w.st, x) or e.at
        if any(item.id == x for item in w.m.items):
            return x, legacy_world.owner(w.m, w.st, x)
    unresolved = False
    for x in candidates:
        found = legacy_world.item_id(w.m, w.st, x)
        if found is None:
            unresolved = True
            continue
        if found not in w.rec.entities and not any(item.id == found for item in w.m.items):
            found = None
        if found is not None:
            return found, legacy_world.owner(w.m, w.st, found)
        holder = next((i.id for i in w.st.investigators if x in i.inventory), None) or w.st.item_owners.get(x)
        if holder:
            return x, holder
    if unresolved:
        raise ValueError(f"「{text}」无法唯一定位已有物件，请使用实际物件的 id。")
    if source := _document(w, text):
        if (item := w.rec.entities.get(f"document:{source}")) is not None:
            return item.id, item.at
    return None


def _document(w: World, text: str) -> str:
    """An acquired material, by its clue or handout name. Knowing an unrelated fact creates no object."""
    return next((c.id for c in w.m.clues if c.id in w.st.clues and c.handout in w.st.handouts
                 and c.handout and (text in (c.id, c.handout)
                                    or any(names(text, label) for label in (c.title, w.m.handout(c.handout).title)))), "")


def _blank(text: str) -> bool:
    """Nothing written: empty, or the placeholder of the format's example (harness/models.example)."""
    return _named(text).strip("…") in ("", "无", "没有", "none", "null", "None")


def _named(text: str) -> str:
    return re.sub(r"[\s，。、；：！？“”‘’「」（）()·,.;:!?\"']", "", text)


def _clip(text: str, n: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


# -- everyday things (Bring) -------------------------------------------------------------------
def bring(w: World, step: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """An everyday thing a scene brings out, registered as it is given (world/adapters.bring_ops,
    ops.Bring): read off the record as the unit finds it (whoever brings or takes it may have just
    come in), so the step keeps what was said rather than the op. Its op takes the step's id; what
    this action lacks (Ruling.missing) is not brought out."""
    from .adapters import bring_ops

    if source := _document(w, step["name"]):
        op = Bring(id=step["id"], item=f"document:{source}", name=step["name"], source=source,
                   holder=step.get("holder") or "", place=step.get("place") or "", actor=KEEPER)
        bad = op.check(w)
        return ([] if bad else [op.model_dump(mode="json")]), bad
    found = bring_ops(w, [step["name"]], actor=step["actor"], holder=step.get("holder") or None,
                      place=step.get("place") or None, missing=step.get("missing", ()))
    ops = [op.model_copy(update={"id": step["id"]}).model_dump(mode="json") for op in found.ops]
    return ops, list(found.problems)


# -- the scene against the record -----------------------------------------------------------------
def plan(w: World, raw: Any, ctx: Context) -> Plan:
    """What the scene changes in the record, grouped into units, each tried on a copy in order (a unit
    that does not hold is taken back before the next is tried), and what does not hold, in words."""
    try:
        scene = raw if isinstance(raw, Scene) else Scene.model_validate(raw)
    except ValueError as exc:
        return Plan(problems=[f"scene 的格式不对，按字段说明重写：{str(exc).splitlines()[0][:120]}"])
    original = w
    given = scene.model_fields_set
    st, rec = w.st, w.rec
    party = {i.id for i in st.investigators}
    lead = st.investigator.id
    src = st.location
    at_start = set(w.present(src))
    problems: list[str] = []
    units: dict[str, Unit] = {}
    count = itertools.count(ctx.start)
    movers: dict[str, str] = {}  # person -> the unit that moves them

    def name(ident: str) -> str:
        return w.name(ident) if ident else ""

    def op(model: Any, **kw: Any) -> dict[str, Any]:
        return model(id=f"{ctx.ids}:{next(count)}", **kw).model_dump(mode="json")

    def unit(key: str) -> Unit:
        return units.setdefault(key, Unit(key))

    # Where the investigators are as it ends: a room of the place they are in, or where they were.
    room = src
    if "room" in given and not _blank(scene.room):
        where = _place(w, scene.room)
        if where is None:  # a room's own name (客厅) for where they already are: they stay, nothing to tell again
            pass
        elif where != src and not ctx.walk:
            problems.append(f"这一段调查员自己没有行动，不能替他们走到{name(where)}：room 留空。")
        elif where != src and where not in rooms(w.m, src):
            problems.append(f"调查员这一轮在{name(src)}，{name(where)}不在同一处，要去得玩家下一轮自己走："
                            f"可以写他们打算去，人还在{name(src)}。")
        else:
            room = where

    # Who is with them: the people there now, and as it ends.
    if scene.people:
        from .store import World

        w = World(w.m.model_copy(deep=True), w.st.model_copy(deep=True))
        rec = w.rec
        for p in scene.people:
            if not p.id.strip() or not p.name.strip():
                problems.append("新人物的 id 和姓名不能为空。")
                continue
            if p.id in w.rec.entities:
                problems.append(f"人物 id {p.id} 已经存在：已有的人只写进 here，不要重新登记。")
                continue
            raw = op(Improvise, at=room, actor=KEEPER, thread=False,
                     plan={"kind": "展开", "reason": f"现场登记：{p.name}", "npcs": [{**p.model_dump(), "location": room}]})
            result = w.apply(raw)
            if not result.ok:
                problems += [f"登记{p.name}：{why}" for why in result.problems]
                continue
            # Improvise changes the module and cannot be undone. Keep registration separate from
            # later handovers/company, which may fail without making the person cease to exist.
            u = unit(f"introduce:{p.id}")
            u.steps.append(raw)
            u.said.append(f"登记{p.name}，在{name(room)}")
    here_now = w.present(src)
    there_now = w.present(room) if room != src else []
    seen = list(dict.fromkeys([*here_now, *there_now]))
    following = {n for n in seen if rec.entities[n].following in party}
    if "here" in given:  # an id or a name; passers-by and the investigators themselves are not the scene's to place
        end = list(dict.fromkeys(n for x in scene.here if (n := _npc(w, x)) is not None))
    else:  # as it was; walking on, those who follow go along
        end = there_now + [n for n in here_now if room == src or n in following]
    leaving: dict[str, str] = {}
    for gone in scene.left:
        n = _npc(w, gone.who)
        if n is not None and n in seen:  # only people in the scene leave it
            leaving[n] = gone.to
    walks = room != src
    party_unit = unit("party") if walks else Unit("party")
    if walks:
        party_unit.said.append(f"调查员走到{name(room)}")
        for p in party:
            movers[p] = "party"

    for n in end:
        e = rec.entities[n]
        if n in leaving:
            problems.append(f"{e.name}同时写在 here 和 left 里，只能写一处。")
            leaving.pop(n)
        if e.status == "死亡":
            if e.at != room:
                problems.append(f"{e.name}已经死了，不能写成在场或走动。")
            continue
        if e.status == "失踪":
            problems.append(f"{e.name}下落不明，不能写成在场。")
            continue
        if e.at == room:
            continue
        if walks and e.at == src:  # goes along with them
            party_unit.steps.append(op(Move, entity=n, to=room, actor=ENGINE))
            party_unit.said.append(f"{e.name}一起过去")
            movers[n] = "party"
            continue
        if not e.at:
            u = unit(f"npc:{n}")
            u.steps.append(op(Move, entity=n, to=room, actor=KEEPER, reason="补记已在眼前的人，原位置未登记"))
            u.said.append(f"补记{e.name}在{name(room)}")
            movers[n] = u.key
            continue
        minutes = _way(w, ctx, e.at, room)
        if minutes is None:
            problems.append(f"{e.name}在{name(e.at) or '别处'}，从那里到不了{name(room)}：他不在这里，可以写他没来。")
        elif minutes > ctx.elapsed:
            problems.append(f"{e.name}在{name(e.at)}，到{name(room)}要 {minutes} 分钟，这一轮只过了 {ctx.elapsed} 分钟，赶不到："
                            "可以写他动身、在路上，还没到。")
        else:
            u = unit(f"npc:{n}")
            u.steps.append(op(Move, entity=n, to=room, actor=KEEPER))
            u.said.append(f"{e.name}来到{name(room)}")
            movers[n] = u.key

    for n in seen:
        if n in end:
            continue
        e = rec.entities[n]
        if n in leaving:
            to = _place(w, leaving[n])
            minutes = _way(w, ctx, e.at, to) if to else None
            if to is None or to == room:
                problems.append(f"{e.name}的去向「{leaving[n]}」不是能离开去的地点：写一个别的地点。")
            elif minutes is None:
                problems.append(f"{e.name}从{name(e.at)}去不了{name(to)}：写一个走得到的地点。")
            elif minutes > ctx.elapsed:
                problems.append(f"{e.name}从{name(e.at)}到{name(to)}要 {minutes} 分钟，这一轮只过了 {ctx.elapsed} 分钟，到不了："
                                "可以写他起身要走，人还写在 here 里。")
            else:
                u = unit(f"npc:{n}")
                if n in following:
                    u.steps.append(op(Follow, npc=n, actor=KEEPER))
                u.steps.append(op(Move, entity=n, to=to, actor=KEEPER))
                u.said.append(f"{e.name}去了{name(to)}")
                movers[n] = u.key
        elif walks and e.at == src:  # stays behind as they walk on
            if n in following:
                party_unit.steps.insert(0, op(Follow, npc=n, actor=KEEPER))
            party_unit.said.append(f"{e.name}留在{name(src)}")
        else:
            problems.append(f"{e.name}原来在{name(e.at)}，scene 里没有他，也没写去向：还在就写进 here，走了就写进 left 和去处。")

    if walks:  # the walk itself comes after the farewells and before the company arrives
        stays = sum(1 for s in party_unit.steps if s.get("kind") == "follow")
        party_unit.steps.insert(stays, {"kind": "walk", "to": room})

    if "follow" in given:
        company = list(dict.fromkeys(n for x in scene.follow if (n := _npc(w, x)) is not None))
        for n in company:
            e = rec.entities[n]
            if n not in end:
                problems.append(f"{e.name}写在 follow 里，就得写进 here。")
            elif e.following not in party or (walks and e.follow_to == room):
                # Company from here on, wherever they go next (the end of a walk to its agreed place would end it).
                unit(f"npc:{n}").steps.append(op(Follow, npc=n, leader=lead, actor=KEEPER))
                unit(f"npc:{n}").said.append(f"{e.name}跟着调查员")
        for n in end:
            e = rec.entities[n]
            if n not in company and e.following in party and e.status not in ("死亡", "失踪"):
                unit(f"npc:{n}").steps.append(op(Follow, npc=n, actor=KEEPER))
                unit(f"npc:{n}").said.append(f"{e.name}不再跟着调查员")

    # Things: what changed hands, then everyday things brought out, and handed on. A clue or handout the
    # party holds is in hand already (the Ratgers' map of Esnel, 2026-10-22: written in as a new thing,
    # it was refused as one not had for the asking, and the whole telling was asked for again).
    held = [c.title for c in w.m.clues if c.id in w.st.clues] + [h.title for h in w.m.handouts if h.id in w.st.handouts]
    things = {_named(t.name): t for t in scene.things if not _blank(t.name)
              and (_document(w, t.name) or not any(names(t.name, x) for x in held))}
    handed: dict[str, str] = {}
    for h in scene.holds:
        if _blank(h.item):
            continue
        try:
            found = _item(w, h.item)
        except ValueError as exc:
            problems.append(str(exc))
            things.pop(_named(h.item), None)
            continue
        if found is None and _document(w, h.item) and _named(h.item) not in things:
            things[_named(h.item)] = Thing(name=h.item, holder=lead)
        if found is None and _named(h.item) in things:
            handed[_named(h.item)] = h.holder
            continue
        if found is None:
            problems.append(f"「{h.item}」不是登记过的物件：日常小东西写进 things。")
            continue
        item, before = found
        to = _person(w, h.holder) or _place(w, h.holder)
        if to is None:
            problems.append(f"「{h.holder}」不是人也不是地点，{h.item}到不了那里。")
            continue
        if to == before:
            continue
        label = w.name(item) if item in rec.entities else item
        u = unit(f"item:{item}")
        u.steps += [_meet(before, to, f"{name(before)}和{name(to)}讲完时不在一处，{label}递不过去。"),
                    op(Transfer, item=item, to=to, giver=before, actor=KEEPER)]
        u.said.append(f"{label}到了{name(to)}" + ("" if rec.entities.get(to) and rec.entities[to].kind == "location" else "手里"))
    for i, (key, t) in enumerate(things.items()):
        actor = _person(w, t.holder)
        if actor is None:
            problems.append(f"「{t.holder}」不是在场的人，{t.name}没人拿得出来。")
            continue
        final = handed.get(key, "")
        holder = _person(w, final) if final else actor
        place = _place(w, final) if final and holder is None else None
        if holder is None and place is None:
            problems.append(f"「{final}」不是人也不是地点，{t.name}到不了那里。")
            continue
        given_to = holder or place or actor
        u = unit(f"thing:{i}")
        # Brought out in front of the investigators and handed over where both stand: one step, so
        # registering it and its handing on land together.
        u.steps += [_meet(actor, lead, f"{name(actor)}讲完时不在调查员身边，拿不出{t.name}。"),
                    {"kind": "bring", "id": f"{ctx.ids}:{next(count)}", "name": t.name.strip(), "actor": actor,
                     "holder": holder or "", "place": place or "", "missing": list(ctx.missing)}]
        u.said.append(f"{name(actor)}拿出{t.name}" + (f"，给了{name(given_to)}" if given_to != actor else ""))
    for i, t in enumerate(scene.traces):
        if not _blank(t.text):
            u = unit(f"trace:{i}")
            u.steps += [_meet(lead, room, f"调查员讲完时不在{name(room)}，痕迹留不在那里。"),  # where they stand as it ends
                        op(Occur, text=_clip(t.text), trace=_clip(t.text), place=room,
                           visibility="private" if t.private else "public", actor=KEEPER)]
            u.said.append(f"留下痕迹：{_clip(t.text, 60)}")

    for i, observed in enumerate(scene.observations):
        if _blank(observed.text):
            continue
        witnesses = list(dict.fromkeys(_npc(w, x) for x in observed.witnesses))
        if not witnesses or None in witnesses:
            problems.append("observations.witnesses 要写本段实际在场的 NPC id 或姓名，不能留空或写未登记的人。")
            continue
        u = unit(f"observation:{i}")
        for who in witnesses:
            u.steps.append({"kind": "witness", "who": who, "lead": lead, "room": room,
                            "at_start": who in at_start,
                            "why": f"{name(who)}本段起点、结尾都未实际在场，不能记下这段见闻。"})
        u.steps.append(op(Occur, text=observed.text.strip(), participants=witnesses,
                          place=room, visibility="private", actor=KEEPER, reactive=False))
        u.said.append(f"{'、'.join(name(n) for n in witnesses)}当场获知：{observed.text.strip()}")

    merged = _merge(units, movers)
    end_words = _try(original, merged)
    for u in merged:
        problems += u.problems
    now = "" if _blank(scene.now) else _clip(scene.now)
    flags = list(dict.fromkeys(scene.flags))
    problems += [f"剧情标记 {flag} 没有声明：只能从模组标记中选择。" for flag in flags if flag not in w.m.flags]
    return Plan(units=merged, problems=list(dict.fromkeys(problems)), now=now, end=end_words,
                flags=[] if problems else flags)


def _meet(a: str, b: str, why: str) -> dict[str, Any]:
    """A step of this module's: `a` and `b` (people or places) where the other is, or `why` not."""
    return {"kind": "meet", "a": a, "b": b, "why": why}


def _merge(units: dict[str, Unit], movers: dict[str, str]) -> list[Unit]:
    """A handover that needs both hands in one place lands with the moves of the people in it."""
    root = {k: k for k in units}

    def find(k: str) -> str:
        while root[k] != k:
            root[k] = root[root[k]]
            k = root[k]
        return k

    for key, u in units.items():
        for step in u.steps:
            kind = step.get("kind")
            if kind not in ("meet", "witness"):
                continue
            people = (step["a"], step["b"]) if kind == "meet" else (step["who"], step["lead"])
            for who in people:
                if (other := movers.get(who)) and other in units and other != key:
                    a, b = find(key), find(other)
                    if a != b:
                        first, second = sorted((a, b), key=list(units).index)
                        root[second] = first
    out: dict[str, Unit] = {}
    for key, u in units.items():  # in order: people first, then things, then traces
        top = find(key)
        if top not in out:
            out[top] = Unit(top)
        out[top].steps += u.steps
        out[top].said += u.said
    return [u for u in out.values() if u.steps]


def run(w: World, u: Unit) -> list[str]:
    """Land one unit whole, or take it back and say why not."""
    applied, walked, problems = 0, None, []
    for step in u.steps:
        kind = step.get("kind")
        if kind == "walk":
            walked = (w.st.location, dict(w.rec.left))
            w.rec.left[w.st.location] = w.rec.seq
            w.st.location = step["to"]
            continue
        if kind == "meet":
            if w.place(step["a"]) != w.place(step["b"]):
                problems = [step["why"]]
                break
            continue
        if kind == "witness":
            person = w.rec.entities.get(step["who"])
            if person is None or person.kind != "npc" or (not step["at_start"] and (
                    person.status in ("死亡", "失踪") or w.place(person.id) != step["room"]
                    or w.st.location != step["room"])):
                problems = [step["why"]]
                break
            continue
        ops, refused = bring(w, step) if kind == "bring" else ([step], [])
        if refused:
            problems = refused
            break
        for raw in ops:
            result = w.apply(raw)
            if not result.ok:
                problems = [f"{'；'.join(u.said) or u.key}：{p}" for p in result.problems]
                break
            applied += 0 if result.repeat else 1
        if problems:
            break
    else:
        return []
    if applied:
        w.undo(applied)
    if walked is not None:
        w.st.location, w.rec.left = walked
    return problems


def _try(w: World, units: list[Unit]) -> str:
    """Every unit tried in order on a copy; what holds stays for the next. How it then stands."""
    from .store import World

    trial = World(w.m.model_copy(deep=True), w.st.model_copy(deep=True))
    for u in units:
        u.problems = run(trial, u)
    return standing(trial)


def standing(w: World) -> str:
    """Where the investigators are, who is with them and who walks with them, in words."""
    party = {i.id for i in w.st.investigators}
    here = w.present(w.st.location)
    people = "、".join(w.name(n) for n in here) or "没有别人"
    along = "、".join(w.name(n) for n in here if w.rec.entities[n].following in party) or "没有人"
    return f"调查员在{w.name(w.st.location)}；在场：{people}；跟着调查员：{along}"


def land(w: World, units: list[Unit]) -> list[Unit]:
    """Land what holds, now: tried on a copy first, then the same on the record (no await between)."""
    from .store import World

    trial = World(w.m.model_copy(deep=True), w.st.model_copy(deep=True))
    landed = []
    for u in units:
        if run(trial, u):
            continue
        if run(w, u):  # the same state both times: it holds here too
            continue
        landed.append(u)
    return landed


# -- in words, for the keeper --------------------------------------------------------------------
def _way(w: World, ctx: Context, a: str, b: str) -> int | None:
    """Minutes from one place to another as a scene counts them: a room of the same building is
    a few steps away (Thomas comes over from the study while they talk, as they walk into it
    themselves); between places, the way there takes its time."""
    return 0 if b in rooms(w.m, a) else ctx.travel(a, b)


MISSING = "\n\n【上一稿没有 scene】先写 scene（这段讲完时的场面，没变的照抄），再照 scene 写 text。"


def feedback(p: Plan) -> str:
    """What to tell the keeper when its scene did not hold: what, and what it could write instead."""
    return ("\n\n【上一稿的 scene 没通过核对】\n" + "\n".join(f"- {x}" for x in p.problems)
            + "\n重写这一段：先照记录改好 scene，再照 scene 写 text。")


def settled(p: Plan) -> str:
    """The scene the program put together of what holds, for a telling that may only follow it."""
    lines = ["\n\n【场面已经定了】scene 两次没通过核对，系统只记下了能成立的部分。这一段照它写，只写叙述正文：",
             f"- 讲完时：{p.end}"]
    lines += [f"- {x}" for u in p.held for x in u.said]
    if p.problems:
        lines.append("没能成立的，只能写成尝试或打算，不能写成已经发生：")
        lines += [f"- {x}" for x in p.problems]
    return "\n".join(lines)


def kept(p: Plan) -> str:
    """For telling it again: the scene as it was recorded, which another telling does not change."""
    done = [x for u in p.held for x in u.said]
    if not done and not p.problems:
        return ""
    words = "；".join(done) or "场面没有变"
    failed = f"；没能成立、只能写成尝试的：{'；'.join(p.problems)}" if p.problems else ""
    return f"\n\n（这一段讲完时的场面已经记下，重讲不能改：{words}{failed}）"
