"""Wounds and healing as the Keeper Rulebook settles them (ch. 6, pp. 91 and 100–103).

`Body` is a frozen snapshot of someone's hit points and wound state: an investigator's sheet
(`Body.of`), an NPC's or a creature's world record (world/record.Life, through `Body.of` too, with
the creature's traits). Every function returns a new Body and says what happened; nothing is
changed in place, nothing rolls. A roll the rules call for (CON after a major wound, the dying's
CON each round, First Aid, Medicine, the healing dice) is thrown by the caller and passed in as its
level or total, so the encounter engine can journal every die (engine/encounter) and the same
rules serve an event's damage, a plugin's and the keeper's. `onto` writes a Body back.

- A blow (`apply_wound`). Armour comes off first, but not against poison, drowning or suffocation,
  or magic (p. 91); a creature that takes half from guns halves the rolled damage before its armour
  (the ghoul's "一半，向下取整", the flying polyp's "最低伤害，然后由于其护甲再次降低", pp. 256, 258).
  Under half the maximum HP it is a minor wound; half or more a major wound: the character falls
  prone at once and rolls CON or falls unconscious; more than the maximum kills (p. 100).
- At 0 HP (p. 100): with only minor wounds, unconscious, and they recover in time; with a major
  wound marked, dying, and out cold. Suffocating or drowning to 0 is death whatever the wounds
  (p. 103). A creature at 0 is dead, or down for now when it heals itself.
- Dying (p. 101): CON at the end of the next round and every round after, death on a failure.
  First Aid only stabilizes them: 1 temporary HP, then a CON roll every hour until Medicine, a
  failure and they are dying again. Medicine helps the dying only once First Aid has stabilized them:
  the dying mark goes and 1D3 HP come back.
- First Aid (pp. 54, 100): within the hour, +1 HP, wakes the unconscious; one attempt per injury
  (a second try is a push), two people may try together. On the dying, every round until it works.
- Medicine (p. 101): an hour at least, +1D3, hard when not the same day.
  A character can have only one successful First Aid and one Medicine until hurt again (p. 54);
  each wound has its id, so "once" is once per injury.
- Natural healing (pp. 101–102): a point a day without a major wound; with one, a CON roll at the end
  of each week: 1D3, 2D3 on an extreme success, the major wound gone at an extreme success or once
  back at half the maximum or more.
- Other damage (Table III, p. 103), suffocation and drowning (CON every round, hard when exerting;
  after the first failure damage every round until breathing again), poison (an extreme CON success
  halves it).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from .bcdice import Level

Means = Literal["blow", "unarmed", "firearm", "fall", "fire", "asphyxia", "poison", "other"]

MAJOR, UNCONSCIOUS, DYING, DEAD = "重伤", "昏迷", "濒死", "死亡"
# Armour stops a blow, a bullet, the edge of an obstacle; not poison, drowning or suffocation, nor
# magic (p. 91). A fall or a fire is the keeper's harm from Table III and is not reduced by it here.
ARMOURED: tuple[Means, ...] = ("blow", "unarmed", "firearm")
MEDICINE = "1D3"  # HP a successful Medicine roll restores (p. 101)
WEEK_HEAL = "1D3"  # a week's recovery with a major wound (p. 101)
WEEK_HEAL_EXTREME = "2D3"
MEDICINE_KEY = "medicine:"  # InjuryState.aided entry of an injury that has had Medicine


@dataclass(frozen=True)
class Body:
    """Someone's hit points and wounds, and a creature's traits that change what a wound does."""

    hp: int
    hp_max: int
    conditions: tuple[str, ...] = ()
    human: bool = True  # an investigator or a person: major wounds, dying; a creature dies at 0
    armor: int = 0
    guns_halved: bool = False
    regen: int = 0  # HP it heals at the end of every round
    knockout: int = 0  # a creature stunned once fists alone have done this much (the module's notes)
    regenerating: bool = False  # a creature down at 0 HP and coming back
    # InjuryState's fields
    dying_since: int | None = None
    stabilized_at: int | None = None
    unconscious_at: int | None = None
    punched: int = 0  # unarmed damage taken towards a knockout
    injuries: tuple[str, ...] = ()
    aided: tuple[str, ...] = ()

    @classmethod
    def of(cls, who, **traits) -> Body:
        """The Body of an Investigator or a world record's Life (anything with hp, hp_max, conditions
        and an `injury`): `traits` the creature's (armor, guns_halved, regen, knockout, human)."""
        inj = getattr(who, "injury", None)
        return cls(hp=who.hp, hp_max=who.hp_max, conditions=tuple(who.conditions),
                   regenerating=bool(getattr(who, "regenerating", False)),
                   dying_since=getattr(inj, "dying_since", None), stabilized_at=getattr(inj, "stabilized_at", None),
                   unconscious_at=getattr(inj, "unconscious_at", None), punched=getattr(inj, "knockout", 0),
                   injuries=tuple(getattr(inj, "injuries", ())), aided=tuple(getattr(inj, "aided", ())), **traits)

    # -- what it is -------------------------------------------------------------------------
    def has(self, condition: str) -> bool:
        return condition in self.conditions

    @property
    def dead(self) -> bool:
        return DEAD in self.conditions

    @property
    def dying(self) -> bool:
        return DYING in self.conditions and not self.dead

    @property
    def stabilized(self) -> bool:
        return self.dying and self.stabilized_at is not None

    @property
    def conscious(self) -> bool:
        return not ({UNCONSCIOUS, DYING, DEAD} & set(self.conditions)) and not self.regenerating

    @property
    def major(self) -> bool:
        return MAJOR in self.conditions

    def _with(self, *add: str, drop: tuple[str, ...] = (), **changes) -> Body:
        conditions = [c for c in self.conditions if c not in drop]
        conditions += [c for c in add if c not in conditions]
        return replace(self, conditions=tuple(conditions), **changes)


def onto(who, body: Body) -> None:
    """Write `body` back onto an Investigator or a Life: hit points, conditions and InjuryState."""
    who.hp = body.hp
    who.conditions = list(body.conditions)
    if hasattr(who, "regenerating"):
        who.regenerating = body.regenerating
    inj = who.injury
    inj.dying_since, inj.stabilized_at, inj.unconscious_at = body.dying_since, body.stabilized_at, body.unconscious_at
    inj.knockout, inj.injuries, inj.aided = body.punched, list(body.injuries), list(body.aided)


# -- a blow ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Assessment:
    """What a blow of a given size will do before the CON roll: whether one is called for."""

    taken: int  # after guns halved and armour
    absorbed: int  # what the armour (and the halving) stopped
    major: bool
    dead: bool
    con: bool  # a major wound's CON roll is needed (the caller rolls it and passes its level)


@dataclass(frozen=True)
class WoundResult:
    body: Body
    taken: int
    absorbed: int = 0
    major: bool = False
    prone: bool = False  # a major wound knocks the character down at once (p. 100)
    unconscious: bool = False  # fell unconscious from this blow
    dying: bool = False  # began dying with this blow
    dead: bool = False  # died of this blow
    down: bool = False  # a creature fell (dead, or regenerating)
    refused: bool = False  # the same injury twice: nothing happened
    notes: list[str] = field(default_factory=list)  # what happened, in words for the account


def _reduced(body: Body, amount: int, means: Means, magic: bool) -> tuple[int, int]:
    taken = max(0, amount)
    if body.guns_halved and means == "firearm":
        taken //= 2  # the rolled damage halved, rounded down, before armour (pp. 256, 258)
    if body.armor and means in ARMOURED and not magic:
        taken = max(0, taken - body.armor)
    return taken, max(0, amount) - taken


def assess(body: Body, amount: int, means: Means = "other", *, magic: bool = False) -> Assessment:
    """What a blow of `amount` will do to `body`, before anyone rolls CON for it."""
    taken, absorbed = _reduced(body, amount, means, magic)
    if body.dead or taken <= 0:
        return Assessment(taken, absorbed, False, False, False)
    dead = body.human and taken > body.hp_max
    major = body.human and not dead and taken * 2 >= body.hp_max
    return Assessment(taken, absorbed, major, dead, major)


def apply_wound(body: Body, amount: int, means: Means = "other", *, con: Level | None = None, injury: str = "",
                magic: bool = False, finish: bool = False, round: int | None = None,
                minutes: int | None = None) -> WoundResult:
    """`body` after a blow of `amount` (as rolled, before armour) by `means`.

    `con`: the level of the CON roll a major wound called for (`assess(...).con`); None counts as
    passed, for a caller that has none to give. `injury`: the wound's id; a second wound with the same
    id changes nothing (`refused`); "" keeps no id. `finish`: a deliberate blow at someone already at
    0 HP, which kills them ("杀死昏迷角色的尝试应当成功", p. 100); other harm at 0 HP is ignored.
    `round`: the encounter round, when dying begins in one; `minutes`: the game clock."""
    if injury and injury in body.injuries:
        return WoundResult(body, 0, refused=True, notes=["这一下已经算过了"])
    if body.dead:
        return WoundResult(body, 0)
    found = assess(body, amount, means, magic=magic)
    taken, absorbed = found.taken, found.absorbed
    marked = body.injuries + ((injury,) if injury and taken > 0 else ())
    if taken <= 0:
        notes = ["没能伤到它"] if absorbed else []
        return WoundResult(replace(body, injuries=marked), 0, absorbed, notes=notes)
    body = replace(body, injuries=marked)
    if found.dead:  # more than the maximum in one blow: dead, whatever was left (p. 100)
        return WoundResult(body._with(DEAD, hp=0), taken, absorbed, dead=True, down=True, notes=["当场毙命"])
    if body.hp <= 0:  # further damage at 0 HP is ignored, unless it is meant to kill (p. 100)
        if finish or (not body.human and not body.regen):
            return WoundResult(body._with(DEAD, hp=0, regenerating=False), taken, absorbed,
                               dead=True, down=True, notes=["被结果了性命"])
        return WoundResult(body, taken, absorbed)
    hp = max(0, body.hp - taken)
    notes: list[str] = []
    body = replace(body, hp=hp, punched=body.punched + (taken if means == "unarmed" else 0))
    if not body.human:  # a creature: no major wounds, dead at 0 unless it heals itself
        if hp == 0:
            if body.regen:
                return WoundResult(replace(body, regenerating=True), taken, absorbed, down=True, notes=["倒下了"])
            return WoundResult(body._with(DEAD), taken, absorbed, dead=True, down=True, notes=["死了"])
        if body.knockout and body.punched >= body.knockout and not body.has(UNCONSCIOUS):
            return WoundResult(body._with(UNCONSCIOUS, unconscious_at=minutes), taken, absorbed, unconscious=True,
                               notes=["被打晕了"])
        return WoundResult(body, taken, absorbed)
    major = found.major
    unconscious = dying = False
    if major:  # a major wound: marked, prone at once, CON or unconscious (p. 100)
        body = body._with(MAJOR)
        notes.append("重伤倒地")
        if con is not None and not con.ok:
            body = body._with(UNCONSCIOUS, unconscious_at=minutes)
            unconscious = True
            notes.append("昏死过去")
    if hp == 0:
        if means == "asphyxia":  # suffocation and drowning to 0 HP kill, whatever the wounds (p. 103)
            return WoundResult(body._with(DEAD), taken, absorbed, major, major, dead=True, notes=[*notes, "窒息而死"])
        if body.major:  # a major wound marked and 0 HP: dying, and out cold (pp. 100–101)
            body = body._with(DYING, UNCONSCIOUS, dying_since=round, stabilized_at=None,
                              unconscious_at=body.unconscious_at if body.unconscious_at is not None else minutes)
            dying = True
            notes.append("濒临死亡")
        else:  # only minor wounds: unconscious, and they recover in time
            if not body.has(UNCONSCIOUS):
                body = body._with(UNCONSCIOUS, unconscious_at=minutes)
            unconscious = True
            notes.append("倒地不起")
    elif body.knockout and body.punched >= body.knockout and not body.has(UNCONSCIOUS):
        body = body._with(UNCONSCIOUS, unconscious_at=minutes)
        unconscious = True
        notes.append("被打晕了")
    return WoundResult(body, taken, absorbed, major, major, unconscious, dying, notes=notes)


def knockout_blow(body: Body, *, minutes: int | None = None) -> WoundResult:
    """The optional rule of p. 105: a successful knock-out manoeuvre with something blunt leaves the
    target unconscious with 1 point of damage. Only on something with a skull to hit (the keeper's
    call: not a shoggoth)."""
    hurt = apply_wound(body, 1, "blow")
    b = hurt.body
    if not b.dead and not b.has(UNCONSCIOUS):
        b = b._with(UNCONSCIOUS, unconscious_at=minutes)
    return replace(hurt, body=b, unconscious=True, notes=[*hurt.notes, "被打晕了"])


# -- dying ----------------------------------------------------------------------------------------
def dying_roll_due(body: Body, round: int) -> bool:
    """Whether the dying roll CON at the end of `round`: from the end of the round after the one they
    began dying in, every round, until First Aid stabilizes them (p. 101). Dying since an unknown
    round (None) rolls at every round's end."""
    if not body.dying or body.stabilized:
        return False
    return body.dying_since is None or round > body.dying_since


def dying_roll(body: Body, con: Level) -> Body:
    """A dying character's end-of-round CON: a failure is death (p. 101)."""
    return body if con.ok else body._with(DEAD, hp=0)


def stabilized_roll(body: Body, con: Level, *, round: int | None = None) -> Body:
    """The hourly CON of a dying character First Aid stabilized: a failure and they lose the temporary
    HP and are dying again, needing First Aid and rolling CON every round (p. 101)."""
    if not body.stabilized or con.ok:
        return body
    return replace(body, hp=0, stabilized_at=None, dying_since=round)


# -- care -----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Care:
    body: Body
    allowed: bool = True
    why: str = ""  # why it could not be tried, in words
    healed: int = 0
    woke: bool = False
    stabilized: bool = False


def _last_injury(body: Body, key: str = "") -> str:
    """The latest injury that has not had this care (`key` "" for First Aid, MEDICINE_KEY for
    Medicine); "" when there is none."""
    for injury in reversed(body.injuries):
        if key + injury not in body.aided:
            return injury
    return ""


def first_aid_allowed(body: Body, injury: str = "", *, within_hour: bool = True) -> tuple[bool, str]:
    """Whether First Aid may be tried on `body` for `injury` (the latest one when ""): on the dying
    always, every round until it works (p. 55); otherwise within an hour of the wound, once for each
    injury, a second try being a push (pp. 54, 100)."""
    if body.dead:
        return False, "人已经死了"
    if body.dying:
        return (False, "伤势已经稳定，要靠医学") if body.stabilized else (True, "")
    if not within_hour:
        return False, "受伤已经超过一小时，急救没有用了"
    if body.hp >= body.hp_max and not body.has(UNCONSCIOUS):
        return False, "没有需要急救的伤"
    if injury and injury in body.aided:
        return False, "这处伤已经急救过了，再试是孤注一掷"
    if not injury and body.injuries and not _last_injury(body):
        return False, "这些伤都已经急救过了，再试是孤注一掷"
    return True, ""


def first_aid(body: Body, success: bool, injury: str = "", *, within_hour: bool = True,
              minutes: int | None = None) -> Care:
    """First Aid on `body`, rolled already (`success`: either of the two trying passed). On the dying
    a success only stabilizes: 1 temporary HP, the dying mark kept until Medicine, an hourly CON from
    `minutes` (p. 101); the character stays out cold. Otherwise a success restores 1 HP and wakes the
    unconscious (pp. 54, 100). The attempt is spent on the injury whether it worked or not, save on
    the dying, who may be tried again every round."""
    ok, why = first_aid_allowed(body, injury, within_hour=within_hour)
    if not ok:
        return Care(body, False, why)
    if body.dying:
        if not success:
            return Care(body)
        return Care(replace(body, hp=1, stabilized_at=minutes), healed=1, stabilized=True)
    injury = injury or _last_injury(body)
    aided = body.aided + ((injury,) if injury and injury not in body.aided else ())
    body = replace(body, aided=aided)
    if not success:
        return Care(body)
    woke = body.has(UNCONSCIOUS)
    body = body._with(drop=(UNCONSCIOUS,) if woke else (), hp=min(body.hp_max, body.hp + 1),
                      unconscious_at=None if woke else body.unconscious_at)
    return Care(body, healed=1, woke=woke)


def medicine_difficulty(same_day: bool) -> Level:
    """Medicine is regular on the day of the wound, hard after (p. 101)."""
    return Level.REGULAR if same_day else Level.HARD


def medicine(body: Body, level: Level, amount: int, injury: str = "", *, same_day: bool = True) -> Care:
    """Medicine on `body` (at least an hour's work), rolled at `level`, with the `amount` of its 1D3
    rolled. It needs a regular success the same day, hard later. On the dying only after First Aid
    stabilized them: the dying mark goes and `amount` HP come on top of the temporary one (p. 101).
    One successful Medicine per injury (p. 54); a failed one may be tried again."""
    if body.dead:
        return Care(body, False, "人已经死了")
    if body.dying and not body.stabilized:
        return Care(body, False, "濒死的人要先用急救稳住伤势")
    if not body.dying:
        injury = injury or _last_injury(body, MEDICINE_KEY)
        if not injury and body.injuries:
            return Care(body, False, "这些伤都已经用医学处理过了")
        if body.hp >= body.hp_max:
            return Care(body, False, "没有需要治疗的伤")
    if level < medicine_difficulty(same_day):
        return Care(body)
    if not body.dying and injury:
        body = replace(body, aided=(*body.aided, MEDICINE_KEY + injury))
    if body.dying:
        body = body._with(drop=(DYING,), stabilized_at=None, dying_since=None)
    hp = min(body.hp_max, body.hp + max(0, amount))
    return Care(replace(body, hp=hp), healed=hp - body.hp)


def wake(body: Body) -> Body:
    """Out cold no longer (First Aid, time, the keeper's ruling); the dying and the dead stay down."""
    if body.dying or body.dead:
        return body
    return body._with(drop=(UNCONSCIOUS,), unconscious_at=None)


# -- healing over time ----------------------------------------------------------------------------
def heal_day(body: Body) -> Body:
    """A day's natural healing without a major wound: 1 HP (p. 101). With one marked, nothing: the
    weekly roll heals it."""
    if body.dead or body.dying or body.major:
        return body
    return replace(body, hp=min(body.hp_max, body.hp + 1))


def week_dice(con: Level) -> str:
    """What a week's recovery roll with a major wound heals: nothing on a failure, 1D3 on a success,
    2D3 on an extreme success (p. 101)."""
    if not con.ok:
        return ""
    return WEEK_HEAL_EXTREME if con >= Level.EXTREME else WEEK_HEAL


def week_bonus(*, rested: bool = False, cared: bool = False, poor: bool = False) -> int:
    """The recovery roll's dice: a bonus die for full rest in good surroundings, one for proper
    medical care (the best carer's Medicine roll), a penalty die for poor rest or a carer's fumble
    (p. 101)."""
    return int(rested) + int(cared) - int(poor)


def heal_week(body: Body, con: Level, amount: int) -> Body:
    """The end-of-week recovery of someone with a major wound: `amount` of `week_dice(con)` rolled.
    The major wound goes at an extreme success or once back at half the maximum HP or more
    (pp. 101–102)."""
    if body.dead or body.dying or not body.major:
        return body
    hp = min(body.hp_max, body.hp + (max(0, amount) if con.ok else 0))
    healed = replace(body, hp=hp)
    if con >= Level.EXTREME or hp * 2 >= body.hp_max:
        healed = healed._with(drop=(MAJOR,))
    return healed


def regen_tick(body: Body) -> tuple[Body, bool]:
    """A creature that heals itself, at the end of a round: its `regen` HP back; True when it was down
    and rises. The dead stay dead (the keeper's monster sheet says what kills it for good)."""
    if not body.regen or body.dead:
        return body, False
    hp = min(body.hp_max, body.hp + body.regen)
    rises = body.regenerating and hp > 0
    return replace(body, hp=hp, regenerating=body.regenerating and not rises), rises


# -- other kinds of harm (Table III, p. 103) ---------------------------------------------------------
@dataclass(frozen=True)
class Harm:
    grade: str
    dice: str
    says: str
    examples: str


OTHER_DAMAGE: tuple[Harm, ...] = (
    Harm("轻度", "1D3", "一个人可以承受很多次而不会死", "拳打脚踢、头槌、弱酸、呼吸烟雾弥漫的空气、被扔来的拳头大的石子、跌进淤泥（每 10 英尺）"),
    Harm("中度", "1D6", "可能造成重伤，挨几次会死", "跌在草地上（每 10 英尺）、棍子、强酸、呛水、暴露在真空中、小口径子弹、箭、火把的火"),
    Harm("重度", "1D10", "很可能重伤，一两次就能让人昏迷甚至死亡",
         ".38 子弹、跌在水泥地上（每 10 英尺）、斧头、火焰喷射器或穿过燃烧的房间、6 到 9 码外爆炸的手榴弹或炸药、弱毒"),
    Harm("致命", "2D10", "普通人约一半会死", "被时速 30 英里的汽车撞、3 到 6 码外爆炸的手榴弹或炸药、强毒"),
    Harm("终结", "4D10", "很可能当场死亡", "被超速的汽车撞、3 码内爆炸的手榴弹或炸药、致死毒"),
    Harm("血肉横飞", "8D10", "几乎必死", "高速正面相撞、被火车撞"),
)


def harm_dice(grade: str) -> str:
    """The dice of Table III's grade `grade` (轻度 … 血肉横飞); ValueError for another word."""
    for h in OTHER_DAMAGE:
        if h.grade == grade:
            return h.dice
    raise ValueError(f"表 III 没有“{grade}”这一档：{'、'.join(h.grade for h in OTHER_DAMAGE)}")


def asphyxia_difficulty(exerting: bool) -> Level:
    """Suffocating or drowning: CON every round, hard while exerting oneself (p. 103)."""
    return Level.HARD if exerting else Level.REGULAR


def asphyxia_round(con: Level, *, failed_before: bool, exerting: bool = False) -> bool:
    """Whether this round of suffocating or drowning does damage: from the first failed CON on, every
    round, until the victim breathes again (p. 103). The damage is the keeper's from Table III (呛水,
    中度 1D6) and goes in as means "asphyxia", which kills at 0 HP."""
    return failed_before or con < asphyxia_difficulty(exerting)


def poison_damage(amount: int, con: Level) -> int:
    """A poison's damage after the victim's CON roll: halved (rounded down) on an extreme success
    (pp. 103, 109). A critical may shake it off altogether, at the keeper's word."""
    return amount // 2 if con >= Level.EXTREME else amount
