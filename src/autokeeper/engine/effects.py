"""What happens to people and things, already rolled: the one way damage and its kin reach the game.

A wound used to be applied wherever it was rolled: by a fight to its own copies of the fighters,
by an event to the sheet, by a plugin to whatever it was given. Some of them never reached the
world (an NPC came back standing in the next fight), and some reached it twice. These models are
the results instead. Whoever rolls the dice (an encounter's unit, an event, a failed check, a
plugin) turns what they rolled into a list of `Effect`s; applying them is then
deterministic, in one op (world/ops.EncounterStep inside an encounter, world/ops.Effects outside
one), so they land together or not at all, and the op's id makes a retry land nothing twice.

Rolling them is `harm` (the damage and the CON roll a major wound calls for, through the game's
current dice, so a journaled stage replays them); applying them is `settle`, which the two world
ops call, and `commit` lands a list outside an encounter. `Body` and `wound` are the bookkeeping a
wound does to a body are rules/wounds.py's (`wound`, `heal`, `stabilize`, `die` wrap them), pure, so an
engine can see what a blow will do before the op lands it.

Every amount is absolute where it can be: `Ammo` says what is in the gun now, not how many were
fired, so applying it again changes nothing. A `Wound` carries the id of the injury it is; a second
wound with the same id is refused, so a plugin and the engine cannot both deduct the same blow.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from ..rules import wounds
from ..rules.bcdice import Level
from ..rules.coc import Damage
from ..rules.wounds import Body

if TYPE_CHECKING:
    from ..world.record import Entity
    from ..world.store import World

Means = wounds.Means


class Wound(BaseModel):
    """`amount` HP off `target` (an investigator, NPC or creature id), before armour."""

    kind: Literal["wound"] = "wound"
    target: str
    amount: int = Field(ge=0)
    means: Means = "other"  # unarmed counts towards a knockout, firearm is halved for some creatures
    con: Level | None = None  # the CON roll a major wound called for, already thrown; None when none was
    injury: str  # stable id of this injury: no second wound with it lands; First Aid is once per injury
    round: int | None = None  # the encounter round it was dealt in: dying from it starts rolling CON after the next


class Heal(BaseModel):
    kind: Literal["heal"] = "heal"
    target: str
    amount: int = Field(ge=0)
    means: Literal["first_aid", "medicine", "natural", "regen", "other"]
    injury: str = ""  # the injury First Aid was given for (InjuryState.aided)


class Wake(BaseModel):
    """Out cold no longer (First Aid, or the keeper's ruling)."""

    kind: Literal["wake"] = "wake"
    target: str


class Stabilize(BaseModel):
    """Dying no longer gets worse until Medicine can be used."""

    kind: Literal["stabilize"] = "stabilize"
    target: str


class Die(BaseModel):
    kind: Literal["die"] = "die"
    target: str
    why: str  # 濒死时体质检定失败、一击超过生命上限……


class Ammo(BaseModel):
    """What is in the gun `item` and carried for it now, absolute (WeaponState)."""

    kind: Literal["ammo"] = "ammo"
    item: str
    loaded: int = Field(ge=0)
    spare: int | None = None  # None: unknown, unchanged


class Jam(BaseModel):
    kind: Literal["jam"] = "jam"
    item: str
    jammed: bool


class Ready(BaseModel):
    """`who` has `item` in hand (equipment "held"), as world/ops.Equip makes it."""

    kind: Literal["ready"] = "ready"
    who: str
    item: str


class Drop(BaseModel):
    """`item` leaves its holder for `to`: a place (dropped, knocked away) or someone (taken). The
    inventory follows, as world/ops.Transfer moves it. A creature's own claws are never an item."""

    kind: Literal["drop"] = "drop"
    item: str
    to: str


class Relocate(BaseModel):
    """`who` (an NPC or a creature) is now at `place`. Never the party: it moves only by arriving
    (Game._arrive), since arriving fires events."""

    kind: Literal["relocate"] = "relocate"
    who: str
    place: str


class Mark(BaseModel):
    """A skill an investigator used successfully ticked for improvement."""

    kind: Literal["mark"] = "mark"
    investigator: str
    skill: str


class Luck(BaseModel):
    """An important NPC's Luck pool after it spent some on its own roll (Keeper Rulebook p. 168):
    what is left, absolute. It does not come back."""

    kind: Literal["luck"] = "luck"
    who: str
    left: int = Field(ge=0)


class Fills(BaseModel):
    """Numbers the keeper fixed for an NPC's or a creature's sheet that lacked them (Entity.fills):
    the profile reads them from now on; a body already kept takes a new maximum HP."""

    kind: Literal["fills"] = "fills"
    who: str
    values: dict[str, int]


Effect = Annotated[
    Wound | Heal | Wake | Stabilize | Die | Ammo | Jam | Ready | Drop | Relocate | Mark | Luck | Fills,
    Field(discriminator="kind"),
]

EFFECTS: TypeAdapter[list[Effect]] = TypeAdapter(list[Effect])


# -- what a wound does to a body: rules/wounds.py's, the same for everyone -------------------------
@dataclass
class Hurt:
    """What one wound did (rules/wounds.WoundResult), in the investigators' words (rules/coc.Damage)
    and the account's (`notes`)."""

    amount: int  # as rolled, before armour
    before: int
    result: wounds.WoundResult

    @property
    def body(self) -> Body:
        return self.result.body

    @property
    def notes(self) -> list[str]:
        return list(self.result.notes)

    def describe(self) -> str:
        """As Rules.damage describes a wound, so the screens and the log read as they did."""
        r = self.result
        return Damage(self.amount, self.before, r.body.hp, major_wound=r.major, unconscious=r.unconscious,
                      dying=r.dying, dead=r.dead).describe()


def wound(body: Body, w: Wound, *, minutes: int | None = None, round: int | None = None) -> Hurt:
    """`body` after the wound `w` (rules/wounds.apply_wound: armour, guns halved, major wounds, the CON
    roll `w.con`, dying, death, a creature's knockout and healing); the new body is `.body`."""
    return Hurt(w.amount, body.hp, wounds.apply_wound(body, w.amount, w.means, con=w.con, injury=w.injury,
                                                       minutes=minutes, round=round))


def heal(body: Body, h: Heal) -> Body:
    """HP back (never over the maximum; nothing for the dead). First Aid is kept against its injury,
    and a creature that heals itself is up again once it has any."""
    if body.dead:
        return body
    aided = body.aided + ((h.injury,) if h.means == "first_aid" and h.injury and h.injury not in body.aided else ())
    out = replace(body, hp=min(body.hp_max, body.hp + h.amount), aided=aided)
    if out.regenerating and out.hp > 0:
        out = replace(out, regenerating=False, conditions=tuple(c for c in out.conditions if c != wounds.UNCONSCIOUS))
    return out


def stabilize(body: Body, now: int) -> Body:
    """The dying, stabilized by First Aid: still dying (and out cold) until Medicine, no longer
    rolling CON every round but every hour (rules/wounds.first_aid, p. 101)."""
    if not body.dying or body.stabilized:
        return body
    return replace(body, hp=max(1, body.hp), stabilized_at=now)


def die(body: Body) -> Body:
    if body.dead:
        return body
    return replace(body, hp=0, regenerating=False, conditions=(*body.conditions, wounds.DEAD))


def major(body: Body, w: Wound) -> bool:
    """Whether the wound calls for a CON roll: a major wound that does not kill."""
    return wounds.assess(body, w.amount, w.means).con


# -- whose body ------------------------------------------------------------------------------------
def status_of(conditions, hp: int, hp_max: int, regenerating: bool = False) -> str:
    """An NPC's word for the scene (Entity.status, NPC.status): 死亡, 重伤, 受伤 or nothing."""
    if "死亡" in conditions:
        return "死亡"
    if {"濒死", "重伤"} & set(conditions):
        return "重伤"
    if "昏迷" in conditions or regenerating or hp < hp_max:
        return "受伤"
    return ""


def body_of(w: World, ident: str) -> Body | None:
    """Someone's body as it stands (rules/wounds.Body): an investigator's sheet, an NPC's or a
    creature's world life with its sheet's traits (seeded from the sheet when the world has not kept
    one yet). None for nobody."""
    from .encounter import profiles

    inv = next((i for i in w.st.investigators if i.id == ident), None)
    if inv is not None:
        return Body.of(inv)
    p = profiles.profile(w, ident)
    if p is None:
        return None
    ent = w.rec.entities.get(p.id)
    life = ent.life if ent is not None and ent.life is not None else profiles.life(w, p)
    return Body.of(life, human=p.human, armor=p.armor, guns_halved=p.guns_halved, knockout=p.knockout, regen=p.regen)


def seed(w: World, ident: str) -> Entity | None:
    """The world entity that keeps an NPC's or a creature's body, its life seeded the first time from
    its sheet; a creature becomes an entity then, where the investigators are. None for anyone else
    (an investigator's body is their sheet)."""
    from ..world.record import Entity
    from .encounter import profiles

    p = profiles.profile(w, ident)
    if p is None:
        return None
    ent = w.rec.entities.get(p.id)
    if ent is None:  # only a creature lacks one: every module NPC is seeded with the record
        ent = w.rec.entities[p.id] = Entity(id=p.id, kind="creature", name=p.name, origin="game", at=w.st.location,
                                            changed=w.rec.seq)
    if ent.life is None:
        ent.life = profiles.life(w, p)
        w.touch(ent)
    return ent


def _put(w: World, ident: str, body: Body) -> str:
    """Write a body back where it is kept (rules/wounds.onto); the name to tell it by."""
    inv = next((i for i in w.st.investigators if i.id == ident), None)
    if inv is not None:
        wounds.onto(inv, body)
        return inv.name
    ent = seed(w, ident)
    assert ent is not None and ent.life is not None  # check() found the body
    life = ent.life
    wounds.onto(life, body)
    if ent.status != "失踪" or "死亡" in life.conditions:  # gone missing is not the body's to say
        ent.status = status_of(life.conditions, life.hp, life.hp_max, life.regenerating)
    for c in ("重伤", "昏迷", "濒死", "死亡"):  # the scene's own conditions follow the body
        if c in life.conditions and c not in ent.conditions:
            ent.conditions.append(c)
        elif c not in life.conditions and c in ent.conditions:
            ent.conditions.remove(c)
    w.touch(ent)
    return ent.name


# -- weapons and things --------------------------------------------------------------------------
def item_of(w: World, item: str, *, promote: bool = False) -> Entity | None:
    """The item entity `item` names: a record id, an investigator's inventory line (promoted to an
    item entity on first use, as world/ops.Equip promotes one: metadata for an object they carry,
    never a new possession), or a weapon written on a sheet, "<holder>:<attack>" (an entity once
    it first leaves its holder or its rounds are counted). None when there is no such thing."""
    from ..world.ops import Equip
    from ..world.record import Entity

    if (ent := w.rec.entities.get(item)) is not None:
        return ent if ent.kind == "item" else None
    if not promote:
        return None
    if (made := Equip(item=item, equipment="carried")._item(w)) is not None:
        w.rec.entities[item] = made
        w.st.item_owners.setdefault(item, made.at)
        made.changed = w.rec.seq
        return made
    holder, sep, name = item.partition(":")
    owner = w.rec.entities.get(holder) or (seed(w, holder) if sep and name else None)
    if sep and name and owner is not None and owner.kind in ("npc", "creature"):
        ent = w.rec.entities[item] = Entity(id=item, kind="item", name=name, origin="game", at=holder, changed=w.rec.seq)
        w.st.item_owners.setdefault(item, holder)
        return ent
    return None


def weapon_state(w: World, ent: Entity):
    """The item's WeaponState, made from the weapon it is (the catalog, or the sheet's attack) the
    first time: a full load, unjammed."""
    from ..rules.weapons import weapon_in
    from ..world.record import WeaponState

    if ent.weapon is None:
        known = weapon_in(ent.name)
        holder = ent.id.partition(":")[0]
        written = None
        if known is None and holder in w.rec.entities:
            from .encounter import profiles

            p = profiles.profile(w, holder)
            written = next((a for a in (p.attacks if p else []) if a.item == ent.id), None)
        capacity = known.ammo if known else written.capacity if written else 0
        ent.weapon = WeaponState(kind=known.name if known else written.name if written else ent.name,
                                 loaded=capacity if capacity else None, capacity=capacity,
                                 malfunction=known.malfunction if known else written.malfunction if written else 100)
    return ent.weapon


# -- checking and settling -------------------------------------------------------------------------
def _target(e) -> str:
    return getattr(e, "target", "") or getattr(e, "who", "") or getattr(e, "investigator", "")


def problems(w: World, effects: list) -> list[str]:
    """What keeps these effects from landing, in words; [] when they can."""
    from .encounter import profiles

    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    investigators = {i.id: i for i in w.st.investigators}
    for e in effects:
        if isinstance(e, Wound | Heal | Wake | Stabilize | Die):
            body = body_of(w, e.target)
            if body is None:
                out.append(f"{e.target} 不是调查员、NPC 或怪物")
                continue
            if isinstance(e, Wound):
                who = profiles.combatant(w, e.target)
                if not e.injury:
                    out.append(f"{e.target} 的伤害缺少 injury")
                elif e.injury in body.injuries or (who, e.injury) in seen:
                    out.append(f"伤害 {e.injury} 已经结算过，不再扣第二次")
                seen.add((who, e.injury))
        elif isinstance(e, Ammo | Jam):
            if item_of(w, e.item) is None and not _promotable(w, e.item):
                out.append(f"物件 {e.item} 不存在")
        elif isinstance(e, Ready):
            if not _promotable(w, e.item) and item_of(w, e.item) is None:
                out.append(f"物件 {e.item} 不存在")
            elif e.who not in investigators and e.who not in w.rec.entities:
                out.append(f"{e.who} 不存在")
        elif isinstance(e, Drop):
            if item_of(w, e.item) is None and not _promotable(w, e.item):
                out.append(f"物件 {e.item} 不存在，没有东西可掉：天生的爪牙不是物件")
            if e.to not in investigators and e.to not in w.rec.entities:
                out.append(f"{e.to} 不存在")
        elif isinstance(e, Relocate):
            dest = w.rec.entities.get(e.place)
            if e.who == "players":
                out.append("调查员一行的移动由引擎的到达处理（Game._arrive），不在效果里落地")
            elif dest is None or dest.kind != "location":
                out.append(f"地点 {e.place} 不存在")
            elif profiles.profile(w, e.who) is None:
                out.append(f"{e.who} 不是 NPC 或怪物")
        elif isinstance(e, Mark):
            if e.investigator not in investigators:
                out.append(f"{e.investigator} 不是调查员")
        elif isinstance(e, Luck | Fills):
            if profiles.profile(w, e.who) is None:
                out.append(f"{e.who} 不是 NPC 或怪物")
    return out


def _promotable(w: World, item: str) -> bool:
    """Whether item_of(…, promote=True) would find it, without changing anything."""
    if any(item in i.inventory for i in w.st.investigators) or item in w.st.item_owners:
        return True
    from .encounter import profiles

    holder, sep, name = item.partition(":")
    owner = w.rec.entities.get(holder)
    known = owner.kind in ("npc", "creature") if owner is not None else profiles.profile(w, holder) is not None
    return bool(sep and name and known)


@dataclass
class Settled:
    """What landing a list of effects did, for whoever tells it."""

    texts: list[str] = field(default_factory=list)  # one per wound or change of state, in order
    hurts: dict[str, Hurt] = field(default_factory=dict)  # injury id -> what that wound did


def settle(w: World, effects: list, *, oid: str) -> Settled:
    """Land effects on the world, in order (one op's settle: World.apply restores everything when
    this raises). Wounds and their kin emit a condition event, visible where the party is, so NPCs
    who saw someone fall remember it."""
    from ..world.ops import Transfer

    out = Settled()
    for e in effects:
        if isinstance(e, Wound | Heal | Wake | Stabilize | Die):
            body = body_of(w, e.target)
            assert body is not None
            text = ""
            if isinstance(e, Wound):
                hurt = wound(body, e, minutes=w.now, round=e.round)
                body = hurt.body
                out.hurts[e.injury] = hurt
                text = hurt.describe() if _is_investigator(w, e.target) else _npc_words(hurt)
            elif isinstance(e, Heal):
                after = heal(body, e)
                got = after.hp - body.hp
                text = f"恢复了 {got} 点生命" if got else ""
                if e.means == "regen" and body.regenerating and not after.regenerating:
                    text = "伤口蠕动着合拢，又站了起来"
                body = after
            elif isinstance(e, Stabilize):
                after = stabilize(body, w.now)
                text, body = ("伤势暂时稳住了" if after is not body else ""), after
            elif isinstance(e, Wake):
                after = wounds.wake(body)
                text, body = ("醒了过来" if after.conditions != body.conditions else ""), after
            else:
                after = die(body)
                text, body = (f"死了（{e.why}）" if after is not body else ""), after
            name = _put(w, e.target, body)
            if text:
                said = f"{name}{text}"
                out.texts.append(said)
                who = _actor(w, e.target)
                w.emit("condition", said, actor=who, participants=[who], place=w.place(who) or w.st.location, op=oid)
        elif isinstance(e, Ammo | Jam):
            ent = item_of(w, e.item, promote=True)
            assert ent is not None
            state = weapon_state(w, ent)
            if isinstance(e, Ammo):
                state.loaded = e.loaded
                if e.spare is not None:
                    state.spare = e.spare
            else:
                state.jammed = e.jammed
            w.touch(ent)
        elif isinstance(e, Ready):
            ent = item_of(w, e.item, promote=True)
            assert ent is not None
            if ent.at != e.who:
                _transfer(w, Transfer(id=oid, item=ent.id, to=e.who, actor="engine"), oid)
            ent.equipment, ent.hidden = "held", False
            w.touch(ent)
        elif isinstance(e, Drop):
            ent = item_of(w, e.item, promote=True)
            assert ent is not None
            if ent.at != e.to:
                _transfer(w, Transfer(id=oid, item=ent.id, to=e.to, actor="engine", visibility="auto"), oid)
                out.texts.append(f"{ent.name}到了{w.name(e.to)}")
        elif isinstance(e, Relocate):
            ident = _actor(w, e.who)
            seed(w, ident)
            ent = w.rec.entities[ident]
            if ent.at != e.place:
                src, ent.at = ent.at, e.place
                w.touch(ent)
                w.emit("move", f"{ent.name}从{w.name(src)}去了{w.name(e.place)}", actor=ident, participants=[ident], place=e.place,
                       witnesses=[*w.present(src), *w.present(e.place)], op=oid)
        elif isinstance(e, Mark):
            inv = w.st.find(e.investigator)
            if e.skill not in inv.marks:
                inv.marks.append(e.skill)
        elif isinstance(e, Luck):
            ent = seed(w, e.who)
            assert ent is not None
            ent.luck = e.left
            w.touch(ent)
        elif isinstance(e, Fills):
            ent = seed(w, e.who)
            assert ent is not None and ent.life is not None
            ent.fills.update(e.values)
            hp = e.values.get("生命") or e.values.get("HP")
            if hp and ent.life.hp_max != hp:
                whole = ent.life.hp >= ent.life.hp_max
                ent.life.hp_max = hp
                ent.life.hp = hp if whole else min(ent.life.hp, hp)
            w.touch(ent)
    return out


def _transfer(w: World, op, oid: str) -> None:
    """Transfer's settling inside this op: its checks first, so a bad hand-over fails the whole op."""
    if problems := op.check(w):
        raise ValueError("；".join(problems))
    op.settle(w, oid)


def _actor(w: World, ident: str) -> str:
    from .encounter import profiles

    return ident if _is_investigator(w, ident) else profiles.combatant(w, ident)


def _is_investigator(w: World, ident: str) -> bool:
    return any(i.id == ident for i in w.st.investigators)


def _npc_words(h: Hurt) -> str:
    if h.result.taken == 0 and h.notes:
        return "没受伤：" + "、".join(h.notes)
    return f"受到 {h.result.taken} 点伤害" + (f"，{'、'.join(h.notes)}" if h.notes else "")


# -- rolling and committing ------------------------------------------------------------------------
def harm(game, target: str, amount: int | str, *, means: Means = "other", injury: str, db: str = "0",
         body: Body | None = None) -> Wound:
    """A wound to `target`, rolled: the damage (a number, or a damage expression read by
    rules/dice_expr; ValueError with a Chinese reason when it is not one, so it never reaches
    BCDice as written) and the CON roll a major wound calls for, both through the game's current
    dice (a journaled stage replays them). `body`: the body as the caller already has it (a fight's
    working copy), else the world's."""
    from ..rules import combat as rules
    from ..rules.dice_expr import DamageExpr

    if isinstance(amount, str):
        amount = DamageExpr.parse(amount).total(game.rules.dice, db)
    w = Wound(target=target, amount=max(0, int(amount)), means=means, injury=injury)
    world = game.world
    body = body if body is not None else body_of(world, target)
    if body is None:
        raise ValueError(f"{target} 不是调查员、NPC 或怪物")
    if major(body, w):
        inv = next((i for i in world.st.investigators if i.id == target), None)
        if inv is not None:
            con = game.rules.check(inv, "体质", mark=False)
        else:
            from .encounter import profiles

            p = profiles.profile(world, target)
            con = rules.check(game.rules.dice, "体质", p.chars.get("体质", 50) if p else 50)
        w.con = con.level
    return w


@dataclass
class Committed:
    ok: bool
    op: str = ""
    problems: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    repeat: bool = False
    hurts: dict[str, Hurt] = field(default_factory=dict)

    def told(self, injury: str, name: str) -> str:
        """One wound in the investigators' words (without the name), from a first commit or a retried one."""
        if injury in self.hurts:
            return self.hurts[injury].describe()
        return next((t.removeprefix(name) for t in self.texts if t.startswith(name)), "")


def commit(w: World, effects: list, *, op_id: str, source: str) -> Committed:
    """Land effects outside an encounter in one world op (world/ops.Effects): all or none, and the
    same op id again lands nothing twice. A retried commit tells what the first one did."""
    from ..world.ops import Effects

    result = w.apply(Effects(id=op_id, effects=list(effects), source=source))
    if not result.ok:
        return Committed(False, result.op, result.problems)
    if result.repeat:
        texts = [e.text for e in w.rec.events if e.op == result.op and e.kind == "condition"]
        return Committed(True, result.op, texts=texts, repeat=True)
    done: Settled = result.data or Settled()
    return Committed(True, result.op, texts=done.texts, hurts=done.hurts)
