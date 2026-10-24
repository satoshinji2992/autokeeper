"""CoC 7th edition bookkeeping on top of BCDice.

Percentile checks are thrown with physical dice (physdice.py); BCDice rolls everything else
and the tables. This module only applies the consequences: sanity loss and insanity, damage
and wounds, luck, pushing, opposed rolls and skill improvement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, field_validator, model_serializer
from pydantic.json_schema import SkipJsonSchema

from .bcdice import BCDice, Level, Roll
from .catalog import CHARACTERISTICS, NO_IMPROVEMENT, UNPUSHABLE, UnknownSkill, base_value, canonical, known
from .physdice import PhysDice, physical

DIFFICULTY_ZH = {"常规": Level.REGULAR, "困难": Level.HARD, "极难": Level.EXTREME}
DIFFICULTY_NAME = {v: k for k, v in DIFFICULTY_ZH.items()}


class InjuryState(BaseModel):
    """What a wound leaves that HP and the conditions do not say: since when someone has been dying,
    stabilized or out, the unarmed damage towards a knockout, and which injuries have had First Aid
    (once each). An investigator's is `Investigator.injury`, an NPC's or a creature's is in the world
    record (world/record.Life). Every time is None until it happened: 0 is a real round or minute."""

    dying_since: int | None = None  # the encounter round dying began in
    stabilized_at: int | None = None  # game minutes
    unconscious_at: int | None = None  # game minutes
    knockout: int = 0  # unarmed damage taken towards a knockout (a creature's 打晕)
    injuries: list[str] = Field(default_factory=list)  # ids of the wounds taken (engine/effects.Wound.injury)
    aided: list[str] = Field(default_factory=list)  # those that have had First Aid


class MadnessState(BaseModel):
    """The rule-owned temporary-insanity clock, in game minutes. The bout table is an immediate
    event, not this whole latent period. None never infers a start from an old condition tag."""

    temporary_since: int | None = None
    temporary_until: int | None = None


class Investigator(BaseModel):
    id: str
    name: str
    occupation: str = ""
    age: int = 30
    sex: str = ""
    residence: str = ""
    birthplace: str = ""
    portrait: str = ""  # ASCII art asset id
    backstory: dict[str, str] = Field(default_factory=dict)
    known_locations: list[str] = Field(default_factory=list, description="本人背景明确熟悉的模组地点 id；只知地址与路线，不代表本局已到访或知道现场内容，不向同伴公开")

    characteristics: dict[str, int]
    luck: int
    hp: int = 0
    hp_max: int = 0
    mp: int = 0
    mp_max: int = 0
    san: int = 0
    san_start_of_day: int = 0
    san_lost_today: int = 0
    san_day: int = Field(default=0, description="规则维护：个人每日理智计数最近初始化的游戏日")
    skills: dict[str, int] = Field(default_factory=dict)
    inventory: list[str] = Field(default_factory=list)

    conditions: list[str] = Field(default_factory=list)  # 重伤 / 昏迷 / 濒死 / 死亡 / 临时疯狂 / 不定性疯狂 / 永久疯狂
    marks: list[str] = Field(default_factory=list)  # skills ticked for improvement
    # Kept by the rules (rules/wounds via engine/effects), never written by a module or a model: out
    # of the schema, and out of the dump while there is nothing in it (_without_empty_injury).
    injury: SkipJsonSchema[InjuryState] = Field(default_factory=InjuryState)
    madness: SkipJsonSchema[MadnessState] = Field(default_factory=MadnessState)

    @field_validator("skills")
    @classmethod
    def _skill_names(cls, skills: dict[str, int]) -> dict[str, int]:
        """Cards, modules and saves share the same names as checks and weapon skills."""
        named: dict[str, int] = {}
        for name, value in skills.items():
            key = canonical(name)
            if key == "克苏鲁神话" and not 0 <= value <= 99:
                raise ValueError("调查员的克苏鲁神话须在 0 到 99 之间。")
            if key in named and named[key] != value:
                raise ValueError(f"{key} 有两个不同的技能值，请在人物卡中保留一个")
            named[key] = value
        return named

    @field_validator("characteristics")
    @classmethod
    def _characteristic_names(cls, values: dict[str, int]) -> dict[str, int]:
        named: dict[str, int] = {}
        for name, value in values.items():
            key = canonical(name)
            if key in named and named[key] != value:
                raise ValueError(f"{key} 有两个不同的属性值，请在人物卡中保留一个")
            named[key] = value
        return named

    @model_serializer(mode="wrap")
    def _without_empty_injury(self, handler: SerializerFunctionWrapHandler) -> dict:
        """A sheet nobody has hurt dumps without it: a module's pregens carry no field the module
        format does not have (module/importer._unknown_fields)."""
        out = handler(self)
        if isinstance(out, dict) and out.get("injury") in ({}, InjuryState().model_dump()):
            out.pop("injury")
        if isinstance(out, dict) and out.get("madness") in ({}, MadnessState().model_dump()):
            out.pop("madness")
        return out

    def model_post_init(self, _context) -> None:
        """What a sheet leaves out is worked out from its characteristics; what it gives stands, a 0
        as much as any other number. An investigator at SAN 0 is permanently insane and one at HP 0
        is down, not one whose numbers were never written (the review of 2026-10-21: a save at SAN 0
        came back at POW)."""
        c, given = self.characteristics, self.model_fields_set
        if "hp_max" not in given:
            self.hp_max = (c["体质"] + c["体型"]) // 10
        if "hp" not in given:
            self.hp = self.hp_max
        if "mp_max" not in given:
            self.mp_max = c["意志"] // 5
        if "mp" not in given:
            self.mp = self.mp_max
        if "san" not in given:
            self.san = c["意志"]
        if "san_start_of_day" not in given:
            self.san_start_of_day = self.san

    # -- lookups ------------------------------------------------------------
    @property
    def san_max(self) -> int:
        return max(0, 99 - self.skills.get("克苏鲁神话", 0))

    def learn_mythos(self, points: int) -> None:
        """Forbidden knowledge lowers SAN's ceiling, not a horror roll or daily SAN loss
        (Keeper Rulebook, Cthulhu Mythos skill / Maximum Sanity). SAN 0 still ends a PC."""
        if points < 0:
            raise ValueError("克苏鲁神话增长不能为负数。")
        self.skills["克苏鲁神话"] = min(99, self.value("克苏鲁神话") + points)
        self.san = max(0, min(self.san, self.san_max))
        if self.san == 0:
            self.add_condition("永久疯狂")

    @property
    def damage_bonus(self) -> str:
        return _build(self.characteristics["力量"] + self.characteristics["体型"])[0]

    @property
    def build(self) -> int:
        return _build(self.characteristics["力量"] + self.characteristics["体型"])[1]

    @property
    def move(self) -> int:
        c = self.characteristics
        if c["敏捷"] < c["体型"] and c["力量"] < c["体型"]:
            mov = 7
        elif c["敏捷"] > c["体型"] and c["力量"] > c["体型"]:
            mov = 9
        else:
            mov = 8
        if self.age >= 40:
            mov -= min(5, (self.age - 30) // 10)
        return mov

    def value(self, name: str) -> int:
        """Current value of a skill, characteristic, 理智 or 幸运."""
        key = canonical(name)
        if key in self.characteristics:
            return self.characteristics[key]
        if key == "理智":
            return self.san
        if key == "幸运":
            return self.luck
        if key in self.skills:
            return self.skills[key]
        # specialised skills such as 外语(英语) fall back to the general entry
        general = key.split("(")[0]
        for skill, v in self.skills.items():
            if skill.split("(")[0] == general and "(" not in key:
                return v
        if not known(key):
            raise UnknownSkill(name)
        return base_value(key, self.characteristics)

    def set(self, name: str, value: int) -> str:
        key = canonical(name)
        if key in self.characteristics:
            self.characteristics[key] = value
        elif key.lower() in ("理智", "san"):
            self.san = value
            return "理智"
        elif key.lower() in ("幸运", "luck"):
            self.luck = value
            return "幸运"
        elif key.lower() in ("生命", "hp"):
            self.hp = max(0, min(value, self.hp_max))
            return "HP"
        elif key.lower() in ("魔法", "mp"):
            self.mp = max(0, min(value, self.mp_max))
            return "MP"
        else:
            self.skills[key] = value
        return key

    def add_condition(self, name: str) -> None:
        if name not in self.conditions:
            self.conditions.append(name)

    @property
    def can_act(self) -> bool:
        return not {"昏迷", "濒死", "死亡", "永久疯狂"} & set(self.conditions)

    def new_day(self, day: int | None = None) -> None:
        if day is not None:
            if day <= self.san_day:
                return
            self.san_day = day
        self.san_start_of_day = self.san
        self.san_lost_today = 0

    def recover_madness(self, now: int, *, rested_since: int | None = None) -> list[str]:
        """Expire only the state whose clock ran out. A completed safe night's rest may end
        temporary insanity sooner, but cannot erase a new trauma during that rest, indefinite
        insanity, SAN loss, phobias, disease or wounds (Keeper Rulebook p. 164 / local p. 139)."""
        m, recovered = self.madness, []
        rested = (rested_since is not None and now > rested_since
                  and (m.temporary_since is None or m.temporary_since <= rested_since))
        if ((m.temporary_until is not None and now >= m.temporary_until) or rested) and "临时疯狂" in self.conditions:
            self.conditions.remove("临时疯狂")
            m.temporary_since = m.temporary_until = None
            recovered.append("临时疯狂已恢复（安全过夜休息）" if rested else "临时疯狂已到期恢复")
        return recovered


def _build(total: int) -> tuple[str, int]:
    if total <= 64:
        return "-2", -2
    if total <= 84:
        return "-1", -1
    if total <= 124:
        return "0", 0
    if total <= 164:
        return "+1D4", 1
    if total <= 204:
        return "+1D6", 2
    extra = (total - 205) // 80
    return f"+{2 + extra}D6", 3 + extra


# -- outcomes -----------------------------------------------------------------
@dataclass
class Check:
    name: str
    target: int
    difficulty: Level
    bonus: int
    roll: Roll
    pushed: bool = False
    luck_spent: int = 0

    @property
    def level(self) -> Level:
        level = self.roll.level
        assert level is not None, self.roll.text
        if self.luck_spent:
            return max(level, self.difficulty)
        return level

    @property
    def passed(self) -> bool:
        return self.level >= self.difficulty

    @property
    def value(self) -> int:
        return self.roll.value or 0

    @property
    def can_push(self) -> bool:
        return (
            not self.passed
            and not self.pushed
            and self.level != Level.FUMBLE
            and canonical(self.name) not in UNPUSHABLE
        )

    def luck_needed(self) -> int | None:
        """Luck points that would turn this failure into a pass (optional rule)."""
        if self.passed or self.pushed or self.level == Level.FUMBLE:
            return None
        if canonical(self.name) in ("理智", "幸运"):
            return None
        threshold = {Level.REGULAR: self.target, Level.HARD: self.target // 2, Level.EXTREME: self.target // 5}[
            self.difficulty
        ]
        return max(0, self.value - threshold)

    def describe(self) -> str:
        diff = "" if self.difficulty == Level.REGULAR else f"·{DIFFICULTY_NAME[self.difficulty]}"
        dice = ""
        if self.bonus:
            dice = f"{'奖励' if self.bonus > 0 else '惩罚'}骰×{abs(self.bonus)} "
        tag = "孤注一掷 " if self.pushed else ""
        mark = "✓" if self.passed else "✗"
        luck = f"（花 {self.luck_spent} 点幸运）" if self.luck_spent else ""
        return f"{tag}{self.name} {self.target}{diff} {dice}→ {self.value} {self.level.zh} {mark}{luck}"


@dataclass
class Sanity:
    check: Check
    expr: str
    loss: int
    before: int
    after: int
    int_check: Check | None = None
    temporary: bool = False
    indefinite: bool = False
    permanent: bool = False
    bout: str | None = None
    duration_hours: int | None = None

    def describe(self) -> str:
        parts = [f"理智检定 {self.check.target} → {self.check.value} {self.check.level.zh}",
                 f"损失 {self.expr} = {self.loss}（{self.before} → {self.after}）"]
        if self.int_check:
            parts.append(f"智力 {self.int_check.value} {self.int_check.level.zh}")
        if self.permanent:
            parts.append("永久疯狂")
        elif self.indefinite:
            parts.append("不定性疯狂")
        elif self.temporary:
            parts.append("临时疯狂")
        if self.duration_hours is not None:
            parts.append(f"临时疯狂持续 {self.duration_hours} 小时")
        return "，".join(parts)


@dataclass
class Damage:
    amount: int
    before: int
    after: int
    major_wound: bool = False
    con_check: Check | None = None
    unconscious: bool = False
    dying: bool = False
    dead: bool = False

    def describe(self) -> str:
        s = f"受到 {self.amount} 点伤害（HP {self.before} → {self.after}）"
        if self.dead:
            return s + "，当场死亡"
        if self.major_wound:
            s += "，重伤"
        if self.dying:
            s += "，濒死"
        elif self.unconscious:
            s += "，昏迷"
        return s


@dataclass
class Opposed:
    a: Check
    b: Check
    winner: str  # "a" | "b" | "tie"


@dataclass
class Improvement:
    skill: str
    roll: int
    gain: int
    before: int

    def describe(self) -> str:
        if self.gain:
            return f"{self.skill} 成长检定 {self.roll} → +{self.gain}（{self.before} → {self.before + self.gain}）"
        return f"{self.skill} 成长检定 {self.roll} → 没有成长"


_DICE_EXPR = re.compile(r"^\s*(\d*)[dD](\d+)\s*([+-]\s*\d+)?\s*$")


def max_of(expr: str) -> int:
    """Maximum of a simple expression like 1D6+1 (used when a sanity roll fumbles)."""
    expr = expr.strip()
    if re.fullmatch(r"\d+", expr):
        return int(expr)
    m = _DICE_EXPR.match(expr)
    if not m:
        raise ValueError(f"无法计算“{expr}”的最大值")
    n, sides, mod = m.groups()
    return int(n or 1) * int(sides) + int((mod or "0").replace(" ", ""))


# -- the rules ------------------------------------------------------------------
@dataclass
class Rules:
    dice: BCDice | PhysDice = field(default_factory=physical)

    def check(
        self,
        inv: Investigator,
        name: str,
        difficulty: Level = Level.REGULAR,
        bonus: int = 0,
        *,
        pushed: bool = False,
        target: int | None = None,
        mark: bool = True,
    ) -> Check:
        key = canonical(name)
        target = inv.value(key) if target is None else target
        result = Check(key, target, difficulty, max(-2, min(2, bonus)), self.dice.check(target, bonus), pushed)
        if mark and result.passed and key not in NO_IMPROVEMENT and key not in CHARACTERISTICS and key not in (
            "理智",
            "幸运",
        ) and key not in inv.marks:
            inv.marks.append(key)
        return result

    def push(self, inv: Investigator, failed: Check) -> Check:
        if not failed.can_push:
            raise ValueError(f"这次{failed.name}检定不能孤注一掷")
        return self.check(inv, failed.name, failed.difficulty, failed.bonus, pushed=True, target=failed.target)

    def spend_luck(self, inv: Investigator, failed: Check) -> bool:
        need = failed.luck_needed()
        if need is None or need > inv.luck:
            return False
        inv.luck -= need
        failed.luck_spent = need
        return True

    def opposed(self, a: Check, b: Check) -> Opposed:
        if a.level != b.level:
            return Opposed(a, b, "a" if a.level > b.level else "b")
        if a.target != b.target:
            return Opposed(a, b, "a" if a.target > b.target else "b")
        return Opposed(a, b, "tie")

    def sanity(self, inv: Investigator, on_success: str, on_failure: str, *, now: int = 0) -> Sanity:
        inv.new_day(now // 1440)
        check = self.check(inv, "理智", mark=False)
        expr = on_success if check.passed else on_failure
        if check.level == Level.FUMBLE:
            loss = max_of(on_failure)
        else:
            loss = int(expr) if expr.strip().isdigit() else self.dice.total(expr)
        before = inv.san
        result = Sanity(check, expr, loss, before, max(0, before - loss))
        if result.after == 0:
            inv.san = 0
            inv.san_lost_today += loss
            result.permanent = True
            inv.add_condition("永久疯狂")
            return result
        if inv.san_start_of_day and inv.san_lost_today + loss >= max(1, inv.san_start_of_day // 5):
            result.indefinite = True
        if loss >= 5:
            result.int_check = self.check(inv, "智力", mark=False)
            if result.int_check.passed:
                result.temporary = True
        if result.temporary or result.indefinite:
            result.bout = self.dice.table("BMR")
            if result.temporary:
                result.duration_hours = self.dice.total("1D10")
        # All dice, including the duration, have returned: a failed bridge cannot leave SAN
        # deducted and a temporary condition without its clock (also outside Game's trial copies).
        inv.san = result.after
        inv.san_lost_today += loss
        if result.indefinite:
            inv.add_condition("不定性疯狂")
        if result.temporary:
            inv.add_condition("临时疯狂")
            inv.madness.temporary_since = now
            inv.madness.temporary_until = now + result.duration_hours * 60
        return result

    def damage(self, inv: Investigator, amount: int | str, *, means: str = "other", injury: str = "") -> Damage:
        """A wound to an investigator, through rules/wounds (Keeper Rulebook p. 100): a major wound
        rolls CON or falls unconscious, more than the maximum kills, 0 HP is unconscious or, with a
        major wound, dying. `amount` a number or dice to roll; `injury` the wound's id (once only)."""
        from . import wounds

        amount = amount if isinstance(amount, int) else self.dice.total(amount)
        body = wounds.Body.of(inv)
        found = wounds.assess(body, amount, means)  # type: ignore[arg-type]
        con = self.check(inv, "体质", mark=False) if found.con else None
        hurt = wounds.apply_wound(body, amount, means, con=con.level if con else None,  # type: ignore[arg-type]
                                  injury=injury)
        result = Damage(amount, inv.hp, hurt.body.hp, major_wound=hurt.major, con_check=con,
                        unconscious=hurt.unconscious or (hurt.major and con is not None and not con.passed),
                        dying=hurt.dying, dead=hurt.dead)
        wounds.onto(inv, hurt.body)
        return result

    def heal(self, inv: Investigator, amount: int) -> int:
        inv.hp = min(inv.hp_max, inv.hp + amount)
        return inv.hp

    def develop(self, inv: Investigator, skills: list[str] | None = None) -> list[Improvement]:
        out = []
        for skill in skills or list(inv.marks):
            before = inv.value(skill)
            roll = self.dice.total("1D100")
            gain = self.dice.total("1D10") if roll > before or roll > 95 else 0
            if gain:
                inv.skills[canonical(skill)] = before + gain
            out.append(Improvement(canonical(skill), roll, gain, before))
            if skill in inv.marks:
                inv.marks.remove(skill)
        return out

    def roll_characteristics(self) -> dict[str, int]:
        three = "3D6*5"
        two = "(2D6+6)*5"
        stats = {name: self.dice.total(two if name in ("体型", "智力", "教育") else three) for name in CHARACTERISTICS}
        stats["幸运"] = self.dice.total(three)
        return stats
