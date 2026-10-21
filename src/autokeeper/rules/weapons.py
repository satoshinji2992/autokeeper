"""Weapons as CoC 7th edition lists them, and the attacks a module writes in words.

An investigator fights with what they carry: inventory lines and the world record's held items
are matched against WEAPONS by name ("一把 .38 左轮" is the .38 revolver), and the bare hands
are always there. A creature or an NPC fights with what its sheet says: "爪击 50% (25/10)，伤害
1D6", "格斗（剥皮爪击）60%，伤害 1D4+2+DB", "射击（步枪）30%，……2D6+4" (parse_attack). Damage
expressions are kept as written, with "DB" standing for the attacker's damage bonus; the engine
puts the bonus in when it rolls (rules/combat.py)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .catalog import canonical

BRAWL = "格斗(斗殴)"
HANDGUN = "射击(手枪)"
RIFLE = "射击(步枪/霰弹枪)"
THROW = "投掷"


Kind = Literal["melee", "thrown", "missile", "firearm"]


@dataclass(frozen=True)
class Weapon:
    """A weapon of the Keeper Rulebook's weapon table (Table XVII, pp. 371–375)."""

    name: str
    skill: str
    damage: str  # "1D3+DB"; a shotgun's by range: "4D6/2D6/1D6"; 半DB for a throw
    impale: bool = False  # an extreme success rolls the weapon's damage again on top of its maximum
    firearm: bool = False
    range: int = 0  # yards of its base range; a melee weapon 0; a throw STR/5 (base_range)
    shots: int = 1  # attacks a round at no penalty (the table's 每轮; a double barrel's "1或2" is 2)
    ammo: int = 0  # rounds a full load holds; 0 for no ammunition
    malfunction: int = 100  # a roll of this or more and it does not fire
    aliases: tuple[str, ...] = field(default_factory=tuple)
    natural: bool = False  # the body's own (fists, claws, a bite): never disarmed, never dropped
    volley: int = 1  # a handgun's "1(3)": up to 3 shots a round, each at a penalty die (p. 96)
    mechanism: str = ""  # revolver, break, bolt, lever, pump, auto: what a malfunction is (rules/firearms)
    loading: str = "rounds"  # rounds (two a round), magazine (one round), belt (two rounds) (p. 96)
    burns: bool = False  # sets alight: a Luck roll or burn (the table's notes, p. 375)

    @property
    def kind(self) -> Kind:
        if self.firearm:
            return "firearm"
        if self.skill == THROW:
            return "thrown"
        return "missile" if self.skill.startswith("射击") else "melee"

    @property
    def melee(self) -> bool:
        return self.kind == "melee"

    def base_range(self, strength: int = 50) -> int:
        """Yards of its base range: a throw's is STR/5 (the weapon table, p. 371)."""
        return strength // 5 if self.kind == "thrown" and not self.range else self.range


UNARMED = Weapon("徒手", BRAWL, "1D3+DB", natural=True, aliases=("拳头", "拳脚", "拳", "空手", "徒手格斗"))

# The weapon table's entries an investigator of the 1920s is likely to carry or pick up (pp. 371–375):
# damage, base range, attacks a round, capacity and malfunction number as printed there.
WEAPONS: tuple[Weapon, ...] = (
    UNARMED,
    Weapon("黄铜指虎", BRAWL, "1D3+1+DB", aliases=("指虎", "铜指虎")),
    Weapon("小刀", BRAWL, "1D4+DB", impale=True, aliases=("折刀", "刀子", "匕首", "剃刀", "削笔刀", "弹簧刀", "刀")),
    Weapon("切肉刀", BRAWL, "1D4+2+DB", impale=True, aliases=("菜刀", "剁刀", "中型刀具")),
    Weapon("大砍刀", BRAWL, "1D8+DB", impale=True, aliases=("砍刀", "弯刀", "猎刀", "柴刀", "刺刀", "大刀", "大型刀具")),
    Weapon("马刀", "格斗(剑)", "1D8+1+DB", impale=True, aliases=("军刀", "大型剑")),
    Weapon("长剑", "格斗(剑)", "1D6+1+DB", impale=True, aliases=("重剑", "剑")),
    Weapon("剑杖", "格斗(剑)", "1D6+DB", impale=True, aliases=("花剑", "轻型剑")),
    Weapon("斧头", "格斗(斧)", "1D8+2+DB", impale=True, aliases=("伐木斧", "斧", "大斧")),
    Weapon("手斧", "格斗(斧)", "1D6+1+DB", impale=True, aliases=("镰刀", "短柄斧")),
    Weapon("棍棒", BRAWL, "1D8+DB", aliases=("球棒", "棒球棍", "撬棍", "铁棍", "拨火棍", "铁锹", "铲子", "铲", "锹", "扳手", "大型棍棒")),
    Weapon("警棍", BRAWL, "1D6+DB", aliases=("手杖", "木棍", "短棍", "小型棍棒")),
    Weapon("火把", BRAWL, "1D6", burns=True, aliases=("火炬", "燃着的木柴")),
    Weapon("矛", "格斗(矛)", "1D8+1", impale=True, aliases=("长矛", "骑枪")),
    Weapon("投掷石块", THROW, "1D4+半DB", aliases=("石块", "石头", "砖头")),
    Weapon(".22 自动手枪", HANDGUN, "1D6", impale=True, firearm=True, range=10, ammo=6, malfunction=100, volley=3,
           mechanism="auto", loading="magazine", aliases=(".22", "小口径手枪", ".22 小手枪")),
    Weapon(".25 德林杰手枪", HANDGUN, "1D6", impale=True, firearm=True, range=3, ammo=1, malfunction=100,
           mechanism="break", aliases=(".25", "德林杰")),
    Weapon(".32 左轮手枪", HANDGUN, "1D8", impale=True, firearm=True, range=15, ammo=6, malfunction=100, volley=3,
           mechanism="revolver", aliases=(".32 左轮", "7.65mm 左轮")),
    Weapon(".32 自动手枪", HANDGUN, "1D8", impale=True, firearm=True, range=15, ammo=8, malfunction=99, volley=3,
           mechanism="auto", loading="magazine", aliases=(".32", "7.65", "自动手枪", "勃朗宁")),
    Weapon(".38 左轮手枪", HANDGUN, "1D10", impale=True, firearm=True, range=15, ammo=6, malfunction=100, volley=3,
           mechanism="revolver", aliases=(".38", "左轮", "转轮手枪", "手枪", "警用左轮")),
    Weapon(".38 自动手枪", HANDGUN, "1D10", impale=True, firearm=True, range=15, ammo=8, malfunction=99, volley=3,
           mechanism="auto", loading="magazine", aliases=("9mm 自动手枪", "鲁格", "9mm")),
    Weapon(".45 左轮手枪", HANDGUN, "1D10+2", impale=True, firearm=True, range=15, ammo=6, malfunction=100, volley=3,
           mechanism="revolver", aliases=(".45 左轮",)),
    Weapon(".45 自动手枪", HANDGUN, "1D10+2", impale=True, firearm=True, range=15, ammo=7, malfunction=100, volley=3,
           mechanism="auto", loading="magazine", aliases=(".45", "柯尔特", "M1911")),
    Weapon("猎枪", RIFLE, "1D6+1", impale=True, firearm=True, range=30, ammo=6, malfunction=99, mechanism="bolt",
           aliases=(".22 步枪", "小口径步枪", ".22 栓动步枪")),
    Weapon("杠杆步枪", RIFLE, "2D6", impale=True, firearm=True, range=50, ammo=6, malfunction=98, mechanism="lever",
           aliases=(".30 杠杆步枪", "温彻斯特", "杠杆式步枪")),
    Weapon("步枪", RIFLE, "2D6+4", impale=True, firearm=True, range=110, ammo=5, malfunction=100, mechanism="bolt",
           aliases=("春田", "栓动步枪", ".30-06", "M1903", "来复枪")),
    Weapon("李-恩菲尔德步枪", RIFLE, "2D6+4", impale=True, firearm=True, range=110, ammo=10, malfunction=100,
           mechanism="bolt", aliases=(".303", "李-恩菲尔德", "李恩菲尔德")),
    Weapon("霰弹枪", RIFLE, "4D6/2D6/1D6", firearm=True, range=10, shots=2, ammo=2, malfunction=100, mechanism="break",
           aliases=("双管", "猎用霰弹枪", "散弹枪", "12 号")),
    Weapon("汤普森冲锋枪", "射击(冲锋枪)", "1D10+2", impale=True, firearm=True, range=20, ammo=20, malfunction=96,
           mechanism="auto", loading="magazine", aliases=("汤普森", "冲锋枪", "芝加哥打字机")),
)

_KEYS: list[tuple[str, Weapon]] = sorted(((key, w) for w in WEAPONS for key in (w.name, *w.aliases)),
                                         key=lambda kv: -len(kv[0]))


def weapon_in(text: str) -> Weapon | None:
    """The weapon a line of the inventory or an order names, the longest name first ("左轮手枪"
    before "手枪"), or None."""
    plain = text.replace("（", "(").replace("）", ")").replace(" ", "")
    for key, w in _KEYS:
        if key.replace(" ", "") in plain:
            return w
    return None


def carried(lines: list[str]) -> list[Weapon]:
    """The weapons in what someone carries, each once, the bare hands first."""
    out = [UNARMED]
    for line in lines:
        w = weapon_in(line)
        if w is not None and w not in out:
            out.append(w)
    return out


# -- a creature's or an NPC's attack, as the module writes it --------------------------------
_PERCENT = re.compile(r"(\d{1,3})\s*[%％]")
_DAMAGE = re.compile(r"(?:伤害|damage)\s*[:：]?\s*((?:\d+\s*[dD]\s*\d+|\d+)(?:\s*[+\-]\s*(?:\d+\s*[dD]\s*\d+|\d+|DB|伤害加值|db))*)")
_BARE_DICE = re.compile(r"(\d+\s*[dD]\s*\d+(?:\s*[+\-]\s*(?:\d+\s*[dD]\s*\d+|\d+|DB))*)")
# "每回合 1 次", "每轮攻击 3 次", and the rulebook's own "每回合攻击次数：3" / "攻击次数：2"
_PER_ROUND = re.compile(r"每(?:回合|轮)(?:攻击)?\s*(\d+)\s*次|(?:每(?:回合|轮))?攻击次数\s*[:：]?\s*(\d+)")
_LEAD = re.compile(r"^(?:攻击方式|战斗方式|攻击)\s*[:：]\s*")  # "攻击：爪击 50%" names the 爪击


@dataclass(frozen=True)
class Written:
    """An attack read off a sheet's words."""

    name: str
    skill: str
    value: int
    damage: str
    firearm: bool = False
    impale: bool = False
    range: int = 0
    ammo: int = 0
    natural: bool = False  # claws, a bite, fists: the body's own, never disarmed


def parse_attack(line: str) -> Written | None:
    """One line of a creature's `attacks`, or None when it names no chance to hit (a note such as
    "每回合 1 次") or is no attack at all (the dodge a stat block lists under its attacks)."""
    text = _LEAD.sub("", line.strip())
    pct = _PERCENT.search(text)
    if pct is None:
        return None
    value = int(pct.group(1))
    head = text[: pct.start()].strip(" ，,：:")
    inner = re.search(r"[（(]([^）)]+)[）)]", head)
    kind = head[: inner.start()].strip() if inner else head
    name = (inner.group(1) if inner else head).strip() or "攻击"
    if canonical(kind) == "闪避" or canonical(name) == "闪避":
        return None
    found = _DAMAGE.search(text) or _BARE_DICE.search(text[pct.end():])
    damage = re.sub(r"\s+", "", found.group(1)).replace("伤害加值", "DB").replace("db", "DB") if found else "1D3"
    firearm = "射击" in kind or any(w in text for w in ("枪", "射程", "弹夹", "子弹"))
    gun = weapon_in(text) if firearm else None
    if kind.startswith(("格斗", "射击")) and "(" in canonical(kind):  # 格斗(刀剑) as written
        skill = canonical(kind)
    elif firearm:  # 射击（步枪）: the gun in the brackets, or in the words
        named = canonical(name)
        skill = named if named.startswith("射击") else gun.skill if gun else RIFLE
    else:
        skill = BRAWL
    rng = re.search(r"射程\s*(\d+)", text)
    clip = re.search(r"弹夹\s*(\d+)", text)
    left = re.search(r"(?:只有|仅有|还剩)\s*(\d+)\s*发", text)
    impale = firearm or any(w in name for w in ("刺", "咬", "獠牙", "角", "刀", "矛"))
    natural = not firearm and canonical(kind) != THROW and weapon_in(name) is None
    return Written(name=name, skill=skill, value=value, damage=damage, firearm=firearm, impale=impale, natural=natural,
                   range=int(rng.group(1)) if rng else (gun.range if gun else 0),
                   ammo=int(left.group(1)) if left else int(clip.group(1)) if clip else (gun.ammo if gun else 0))


def attacks_per_round(lines: list[str], notes: str = "") -> int:
    """How many attacks a creature makes in a round, as its sheet says (1 when it does not)."""
    for text in [*lines, notes]:
        if found := _PER_ROUND.search(text):
            return max(1, int(found.group(1) or found.group(2)))
    return 1


def unread(line: str) -> str:
    """What keeps a line of a sheet's `attacks` from reaching the rules as written, in words for the
    module's author; "" when it reads whole, or is a note of how many attacks it makes in a round."""
    text = _LEAD.sub("", line.strip())
    if not text or (_PER_ROUND.search(text) and not _PERCENT.search(text)):
        return ""
    pct = _PERCENT.search(text)
    if pct is None:
        return "读不出命中率，要写成“爪击 50%，伤害 1D6+DB”这样"
    if parse_attack(text) is None:
        return "闪避不是攻击，写进 stats（闪避: 40）"
    if not (_DAMAGE.search(text) or _BARE_DICE.search(text[pct.end():])):
        return "没写伤害，按 1D3 算"
    return ""
