"""Combat as CoC 7th edition plays it, without the story: who acts when, an attack and its answer,
damage and what it does to someone. The engine (engine/combat.py) keeps a fight going round by
round and tells the screens and the keeper; here are only the numbers.

- Who acts first: the higher DEX, a readied firearm +50 (order).
- A melee attack is answered by a dodge or by fighting back (melee). Both roll; the better level
  of success wins. A tie goes to the dodger against a dodge, to the attacker against fighting back.
  An attack nobody answers (surprised, already down) needs only to succeed. Someone who has already
  answered as many attacks this round as they make attacks is outnumbered: every further attacker
  gets a bonus die.
- A shot cannot be fought back; diving for cover is a dodge roll that, if it succeeds, gives the
  shooter a penalty die and costs the diver their next action (shoot). Firing into a melee is at a
  penalty die. A roll at or over the gun's malfunction number jams it.
- A manoeuvre (disarm, knock down, hold) is an attack that does no damage; a bigger target gives a
  penalty die for every point of build it has over the attacker, and three or more make it impossible.
- Damage is the weapon's plus the damage bonus for a melee weapon (half of it thrown). An extreme
  success does the most the weapon can and, with an impaling weapon, its damage again on top.
- A blow of half someone's hit points or more is a major wound: a CON roll or they fall
  unconscious. At 0 hit points a human with a major wound is dying (a CON roll every round, death on
  a failure), without one unconscious. A creature at 0 is dead, or down for now if it heals itself.
  Investigators take their damage through Rules.damage, the same as anywhere in the game."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from .bcdice import Level
from .coc import Check, _build
from .weapons import BRAWL, UNARMED, Weapon, Written

Side = Literal["party", "foe"]
Stance = Literal["dodge", "fight", "cover"]
DOWN = ("昏迷", "濒死", "死亡", "倒下")  # can do nothing now
OUT = ("逃走", "投降")  # left the fight
STANCES = {"dodge": "闪避", "fight": "反击", "cover": "找掩护"}
MANEUVERS = {"缴械": "夺下或打掉对方手里的武器", "推倒": "把对方摔倒在地，对方失去下一个行动", "制住": "抓牢对方，对方只能设法挣脱"}


class Attack(BaseModel):
    name: str
    skill: str
    value: int
    damage: str  # "1D6", "1D4+2+DB", "4D6/2D6/1D6"
    firearm: bool = False
    impale: bool = False
    range: int = 0
    ammo: int | None = None  # rounds in it now; None when it uses none
    load: int = 0  # a full load
    malfunction: int = 100
    jammed: bool = False

    @property
    def thrown(self) -> bool:
        return self.skill == "投掷"

    @property
    def unarmed(self) -> bool:
        return self.name in (UNARMED.name, *UNARMED.aliases)

    def usable(self) -> bool:
        return not self.jammed and (self.ammo is None or self.ammo > 0)


def attack_of(weapon: Weapon, value: int) -> Attack:
    return Attack(name=weapon.name, skill=weapon.skill, value=value, damage=weapon.damage, firearm=weapon.firearm,
                  impale=weapon.impale, range=weapon.range, ammo=weapon.ammo if weapon.firearm else None,
                  load=weapon.ammo, malfunction=weapon.malfunction)


def attack_written(w: Written) -> Attack:
    return Attack(name=w.name, skill=w.skill, value=w.value, damage=w.damage, firearm=w.firearm, impale=w.impale,
                  range=w.range, ammo=w.ammo if w.firearm else None, load=w.ammo)


class Fighter(BaseModel):
    """Someone in a fight or a chase: an investigator (their sheet stays the truth for hit points
    and conditions; this follows it), an NPC or a creature."""

    id: str
    name: str
    kind: Literal["investigator", "npc", "creature"]
    side: Side
    dex: int = 50
    con: int = 50
    str_: int = Field(50, alias="str")
    hp: int = 10
    hp_max: int = 10
    build: int = 0
    db: str = "0"
    move: int = 8
    dodge: int = 25
    fighting: int = 25
    attacks: list[Attack] = Field(default_factory=list)
    per_round: int = 1  # attacks it makes, and attacks it can answer, in a round
    armor: int = 0
    guns_halved: bool = False
    regen: int = 0  # hit points it heals at the end of every round
    knockout: int = 0  # stunned once fists alone have done this much
    human: bool = True
    status: str = ""  # "", 重伤, 昏迷, 濒死, 死亡, 倒下, 逃走, 投降
    prone: bool = False  # knocked down: their next action goes on getting up, and blows land easier
    held_by: str = ""  # who holds them (制住): they can only try to break free
    stance: Stance = "dodge"
    weapon: int = 0  # the attack in hand
    responded: int = 0  # attacks answered this round
    lost_action: bool = False  # dived for cover or was knocked down: their next action is gone
    surprised: bool = False
    punched: int = 0  # damage done by bare hands (knockout)
    struck: bool = False  # hurt by anything else
    skills: dict[str, int] = Field(default_factory=dict)  # the rest of its sheet: 侦查, 潜行, 攀爬, 跳跃 ...
    portrait: str = ""
    look: str = ""
    notes: str = ""
    sanity: str = ""  # what seeing it costs (a creature's)
    # A chase (rules/chase.py): where on the track, its speed for this chase, actions left this round
    pos: int = 0
    speed: int = 0
    actions: int = 0

    model_config = {"populate_by_name": True}

    def value(self, name: str) -> int:
        """A skill or characteristic of theirs: the sheet's, else the base an untrained one has."""
        from .catalog import base_value, canonical

        key = canonical(name)
        own = {"力量": self.str_, "体质": self.con, "敏捷": self.dex, "闪避": self.dodge, BRAWL: self.fighting}
        if key in own:
            return own[key]
        if key in self.skills:
            return self.skills[key]
        return base_value(key, {"敏捷": self.dex, "教育": 50})

    @property
    def standing(self) -> bool:
        return self.status not in DOWN and self.status not in OUT

    @property
    def can_act(self) -> bool:
        return self.standing and not self.lost_action

    @property
    def wielding(self) -> Attack:
        if 0 <= self.weapon < len(self.attacks):
            return self.attacks[self.weapon]
        return self.attacks[0] if self.attacks else attack_of(UNARMED, self.fighting)

    def best_melee(self) -> Attack:
        melee = [a for a in self.attacks if not a.firearm and not a.thrown]
        return max(melee, key=lambda a: (a.value, not a.unarmed)) if melee else attack_of(UNARMED, self.fighting)

    def wound_word(self) -> str:
        """How hurt they look, in words: what the table sees of a foe, never its numbers."""
        if self.status in DOWN or self.status in OUT:
            return self.status
        if self.hp_max <= 0 or self.hp >= self.hp_max:
            return "无伤"
        share = self.hp / self.hp_max
        if self.status == "重伤" or share <= 0.34:
            return "重伤"
        return "受伤" if share <= 0.75 else "轻伤"


def first_firearm(f: Fighter) -> bool:
    return f.wielding.firearm and f.wielding.usable()


def order(fighters: list[Fighter]) -> list[str]:
    """Who acts in what order this round: DEX, a readied firearm +50, the better fighter on a tie,
    an investigator before anyone else on a tie still."""
    def key(f: Fighter) -> tuple:
        return (-(f.dex + (50 if first_firearm(f) else 0)), -max(f.fighting, f.wielding.value), f.kind != "investigator")

    return [f.id for f in sorted(fighters, key=key)]


# -- rolling ------------------------------------------------------------------------------------
def check(dice, name: str, target: int, difficulty: Level = Level.REGULAR, bonus: int = 0) -> Check:
    """A percentile roll of anyone's (an NPC's has no sheet to tick)."""
    bonus = max(-2, min(2, bonus))
    return Check(name, max(1, target), difficulty, bonus, dice.check(max(1, target), bonus))


_TERM = re.compile(r"([+\-]?)\s*(\d*)\s*[dD]\s*(\d+)|([+\-]?)\s*(\d+)")


def _terms(expr: str) -> list[tuple[int, int, int]]:
    """(sign, count, sides) for every term of a sum; sides 0 is a constant `count`."""
    out = []
    for sign, n, sides, csign, const in _TERM.findall(expr.replace(" ", "")):
        if sides:
            out.append((-1 if sign == "-" else 1, int(n or 1), int(sides)))
        elif const:
            out.append((-1 if csign == "-" else 1, int(const), 0))
    return out


def max_total(expr: str) -> int:
    """The most a sum of dice can come to: 1D4+2+1D4 is 10."""
    return sum(sign * n * (sides or 1) for sign, n, sides in _terms(expr))


def split_damage(damage: str, db: str, *, band: int = 0) -> tuple[str, str]:
    """The weapon's own dice and the bonus that goes with them, apart: ("1D4+2", "+1D4").
    A shotgun's damage is by range (band 0, 1, 2); DB in it is the damage bonus, 半DB half of it (a
    throw); "灼烧" (a torch) is fire, said in the story, nothing to roll."""
    parts = damage.split("/")
    expr = parts[min(band, len(parts) - 1)].replace(" ", "")
    half = "半DB" in expr
    has_db = "DB" in expr
    for word in ("+灼烧", "灼烧", "+半DB", "半DB", "+DB", "DB"):
        expr = expr.replace(word, "")
    expr = expr.strip("+") or "0"
    bonus = db.strip()
    if bonus in ("", "0", "+0") or not has_db:
        return expr, ""
    if half:  # half the bonus, as 7th edition throws: +1D4 becomes +1D2
        found = re.fullmatch(r"([+\-]?)(\d*)[dD](\d+)", bonus)
        bonus = (f"{found.group(1) or '+'}{found.group(2)}D{max(1, int(found.group(3)) // 2)}" if found
                 else f"{'-' if bonus.startswith('-') else '+'}{abs(int(bonus)) // 2}")
    return expr, bonus if bonus.startswith(("+", "-")) else "+" + bonus


def roll_damage(dice, weapon: str, bonus: str = "", *, extreme: bool = False, impale: bool = False) -> tuple[int, str]:
    """The damage and how it came. An extreme success does the most the weapon and the bonus can;
    an impaling weapon then its own damage again: "贯穿：最大 10 + 1D6(4) = 14"."""
    expr = weapon + bonus if bonus else weapon
    if extreme:
        top = max_total(expr)
        if impale:
            more = dice.total(weapon) if any(sides for _, _, sides in _terms(weapon)) else max_total(weapon)
            return max(0, top + more), f"贯穿：最大 {top} + {weapon}（{more}）= {top + more}"
        return max(0, top), f"极难成功：最大 {top}"
    if not _terms(expr):
        return 0, "0"
    total = dice.total(expr) if any(sides for _, _, sides in _terms(expr)) else max_total(expr)
    return max(0, total), f"{expr} = {max(0, total)}"


# -- an exchange --------------------------------------------------------------------------------
@dataclass
class Exchange:
    """One attack and its answer, as the screens and the keeper are told it."""

    attacker: str
    defender: str
    kind: Literal["melee", "shot", "maneuver"]
    attack: str  # the attack's name
    roll: Check | None = None
    answer: Check | None = None  # the defender's dodge or fighting back
    reaction: Literal["dodge", "fight", "cover", "none"] = "none"
    winner: Literal["attacker", "defender", "none"] = "none"
    damage: int = 0
    damage_text: str = ""
    hurt: str = ""  # who took the damage (the defender, or the attacker beaten when fighting back)
    extreme: bool = False
    effect: str = ""  # a manoeuvre's: 缴械 推倒 制住
    jammed: bool = False
    empty: bool = False
    bonus: int = 0
    reasons: list[str] = field(default_factory=list)  # why a bonus or penalty die

    @property
    def landed(self) -> bool:
        return self.winner != "none" and (self.damage > 0 or bool(self.effect))


def _outnumbered(d: Fighter) -> bool:
    return d.responded >= max(1, d.per_round)


def melee(dice, a: Fighter, atk: Attack, d: Fighter, *, bonus: int = 0, reasons: list[str] | None = None,
          maneuver: str = "") -> Exchange:
    """A melee attack (or a manoeuvre) and its answer. Rolls, decides the winner and rolls the
    damage; changes nobody (the engine applies it), but for the defender's count of answers."""
    reasons = list(reasons or [])
    if maneuver:
        over = d.build - a.build
        if over >= 3:
            return Exchange(a.id, d.id, "maneuver", maneuver, effect="", reasons=["对方体格大得多，这个战技做不到"])
        if over > 0:
            bonus -= over
            reasons.append(f"对方体格大 {over}")
    answering = d.standing and not d.surprised and not d.held_by
    if answering and _outnumbered(d):
        bonus += 1
        reasons.append("以多打少")
    if d.held_by or d.prone:
        bonus += 1
        reasons.append("对方" + ("被制住" if d.held_by else "倒在地上"))
    roll = check(dice, atk.skill if not atk.unarmed else BRAWL, atk.value, bonus=bonus)
    ex = Exchange(a.id, d.id, "maneuver" if maneuver else "melee", maneuver or atk.name, roll=roll,
                  bonus=max(-2, min(2, bonus)), reasons=reasons)
    la = roll.level
    if not answering:
        ex.winner = "attacker" if la.ok else "none"
    else:
        d.responded += 1
        if d.stance == "fight":
            own = d.best_melee()
            ex.reaction, ex.answer = "fight", check(dice, own.skill if not own.unarmed else BRAWL, own.value)
            ld = ex.answer.level
            if la.ok and la >= ld:
                ex.winner = "attacker"
            elif ld.ok and ld > la:
                ex.winner = "defender"
        else:
            ex.reaction, ex.answer = "dodge", check(dice, "闪避", d.dodge)
            ld = ex.answer.level
            if la.ok and la > ld:
                ex.winner = "attacker"
    if ex.winner == "none":
        return ex
    if maneuver and ex.winner == "attacker":
        ex.effect = maneuver
        return ex
    if ex.winner == "attacker":
        ex.hurt, level, hit = d.id, la, atk
        db = a.db
    else:  # fought back and won: the defender's blow lands
        ex.hurt, level, hit = a.id, ex.answer.level if ex.answer else Level.REGULAR, d.best_melee()
        db = d.db
    ex.extreme = level >= Level.EXTREME
    weapon, bonus = split_damage(hit.damage, db)
    ex.damage, ex.damage_text = roll_damage(dice, weapon, bonus, extreme=ex.extreme, impale=hit.impale)
    return ex


def shoot(dice, a: Fighter, atk: Attack, d: Fighter, *, band: int = 0, into_melee: bool = False,
          bonus: int = 0, reasons: list[str] | None = None) -> Exchange:
    """A shot. `band`: 0 within the gun's range, 1 up to twice it (hard), 2 up to four times (extreme).
    Uses a round of its ammunition, jams on its malfunction number."""
    reasons = list(reasons or [])
    ex = Exchange(a.id, d.id, "shot", atk.name)
    if not atk.usable():
        ex.empty, ex.jammed = atk.ammo == 0, atk.jammed
        return ex
    if into_melee:
        bonus -= 1
        reasons.append("朝扭打中的人开枪")
    if d.standing and not d.surprised and d.stance == "cover" and not d.lost_action:
        ex.reaction, ex.answer = "cover", check(dice, "闪避", d.dodge)
        d.lost_action = True  # the dive costs their next action, whether or not it helps
        if ex.answer.passed:
            bonus -= 1
            reasons.append("对方扑向掩体")
    difficulty = (Level.REGULAR, Level.HARD, Level.EXTREME)[max(0, min(2, band))]
    ex.roll = check(dice, atk.skill, atk.value, difficulty, bonus)
    ex.bonus, ex.reasons = max(-2, min(2, bonus)), reasons
    if ex.roll.value >= atk.malfunction:
        atk.jammed, ex.jammed = True, True
        return ex
    if atk.ammo is not None:
        atk.ammo = max(0, atk.ammo - 1)
    if not ex.roll.passed:
        return ex
    ex.winner, ex.hurt = "attacker", d.id
    ex.extreme = ex.roll.level >= Level.EXTREME
    weapon, _ = split_damage(atk.damage, "0", band=band)
    ex.damage, ex.damage_text = roll_damage(dice, weapon, extreme=ex.extreme, impale=atk.impale)
    if d.guns_halved and ex.damage:
        ex.damage //= 2
        ex.damage_text += "，枪伤减半"
    return ex


# -- what a blow does to someone who is not an investigator ---------------------------------------
def wound(dice, f: Fighter, amount: int, *, unarmed: bool = False) -> list[str]:
    """An NPC's or a creature's hit points and state after `amount` of damage; what happened, in
    words. Armor first. Investigators go through Rules.damage instead (engine/combat.py)."""
    notes: list[str] = []
    amount = max(0, amount - f.armor)
    if f.armor and amount == 0:
        return ["没能伤到它"]
    before = f.hp
    if unarmed:
        f.punched += amount
    else:
        f.struck = True
    if f.human and amount > f.hp_max:  # more than the maximum kills; exactly it is a major wound (Keeper Rulebook p. 100)
        f.hp, f.status = 0, "死亡"
        return ["当场毙命"]
    f.hp = max(0, f.hp - amount)
    if f.knockout and not f.struck and f.punched >= f.knockout and f.hp > 0:
        f.status = "昏迷"
        return ["被打晕了"]
    if f.human and amount * 2 >= f.hp_max and amount:
        f.status = "重伤" if f.status == "" else f.status
        notes.append("重伤")
        con = check(dice, "体质", f.con)
        if not con.passed:
            f.status = "昏迷"
            notes.append("昏死过去")
    if f.hp == 0 and before > 0:
        if f.human:
            f.status = "濒死" if f.status == "重伤" or "重伤" in notes else "昏迷"
            notes.append("倒地不起" if f.status == "昏迷" else "濒临死亡")
        else:
            f.status = "倒下" if f.regen else "死亡"
            notes.append("倒下了" if f.regen else "死了")
    return notes


def dying(dice, f: Fighter) -> Check:
    """A dying human's CON roll at the end of the round; a failure is death."""
    roll = check(dice, "体质", f.con)
    if not roll.passed:
        f.status, f.hp = "死亡", 0
    return roll


def heal_round(f: Fighter) -> bool:
    """A creature that heals itself, at the end of a round; True when it gets back up."""
    if not f.regen or f.status == "死亡" or f.status in OUT:
        return False
    was_down = f.status == "倒下"
    f.hp = min(f.hp_max, f.hp + f.regen)
    if was_down and f.hp > 0:
        f.status = ""
        return True
    return False


# -- a sheet's numbers --------------------------------------------------------------------------
def stats_of(raw: dict[str, int]) -> dict[str, int]:
    """A module's stats with its keys made the sheet's own: STR or 力量, HP or 生命."""
    from .catalog import canonical

    out: dict[str, int] = {}
    for k, v in raw.items():
        key = canonical(str(k))
        key = {"HP": "生命", "hp": "生命", "MP": "魔法", "MOV": "移动", "移动力": "移动", "DB": "伤害加值"}.get(key, key)
        if isinstance(v, int):
            out[key] = v
    return out


def damage_bonus(strength: int, size: int) -> tuple[str, int]:
    return _build(strength + size)


def traits(notes: str) -> dict:
    """What a creature's notes say that the rules can use: armor ("护甲 2", "护甲：厚皮 4 点", "2 点
    护甲"), guns halved (the rulebook's "火器和穿刺武器造成的伤害减半" too), healing every round,
    fists that stun, its damage bonus, build and MOV when written out."""
    out: dict = {}
    if found := re.search(r"护甲\s*[:：]?\s*[^\d，。；,;\n]{0,6}?(\d+)|(\d+)\s*点护甲", notes):
        out["armor"] = int(found.group(1) or found.group(2))
    if re.search(r"(?:枪|火器|子弹|射击)[^，。；,;\n]{0,12}?减半", notes):
        out["guns_halved"] = True
    if found := re.search(r"每(?:轮|回合)(?:恢复|回复|再生|愈合)\s*(\d+)\s*点", notes):
        out["regen"] = int(found.group(1))
    if found := re.search(r"(?:只被|仅被|只用)拳头?打[^，。；]*?(\d+)\s*点[^，。；]*?(?:击晕|打晕|昏)", notes):
        out["knockout"] = int(found.group(1))
    if found := re.search(r"(?:伤害加[值成]|DB)\s*[:：]?\s*([+\-]?\d*D?\d+)", notes):
        out["db"] = found.group(1) if found.group(1).startswith(("+", "-")) or found.group(1) == "0" else "+" + found.group(1)
    if found := re.search(r"体格\s*[:：]?\s*(-?\d+)", notes):
        out["build"] = int(found.group(1))
    if found := re.search(r"(?:移动力?|MOV)\s*[:：]?\s*(\d+)", notes):
        out["move"] = int(found.group(1))
    return out
