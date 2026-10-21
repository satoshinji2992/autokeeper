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

from .catalog import canonical

BRAWL = "格斗(斗殴)"
HANDGUN = "射击(手枪)"
RIFLE = "射击(步枪/霰弹枪)"
THROW = "投掷"


@dataclass(frozen=True)
class Weapon:
    name: str
    skill: str
    damage: str  # "1D3+DB"; a shotgun's by range: "4D6/2D6/1D6"
    impale: bool = False  # an extreme success rolls the weapon's damage again on top of its maximum
    firearm: bool = False
    range: int = 0  # yards of its base range; a melee weapon 0
    shots: int = 1
    ammo: int = 0  # rounds a full load holds; 0 for no ammunition
    malfunction: int = 100  # a roll of this or more jams it
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def melee(self) -> bool:
        return not self.firearm and self.skill != THROW


UNARMED = Weapon("徒手", BRAWL, "1D3+DB", aliases=("拳头", "拳脚", "拳", "空手", "徒手格斗"))

WEAPONS: tuple[Weapon, ...] = (
    UNARMED,
    Weapon("小刀", BRAWL, "1D4+DB", impale=True, aliases=("折刀", "刀子", "匕首", "剃刀", "削笔刀", "刀")),
    Weapon("大刀", "格斗(刀剑)", "1D8+DB", impale=True, aliases=("砍刀", "军刀", "刺刀", "猎刀", "剑", "大砍刀")),
    Weapon("斧头", "格斗(斧)", "1D8+2+DB", impale=True, aliases=("斧", "手斧", "柴刀")),
    Weapon("棍棒", BRAWL, "1D8+DB", aliases=("球棒", "撬棍", "铁棍", "拨火棍", "手杖", "木棍", "警棍", "扳手", "铁锹", "铲子", "铲", "锹")),
    Weapon("火把", BRAWL, "1D6+灼烧", aliases=("火炬", "燃着的木柴")),
    Weapon("投掷石块", THROW, "1D4+半DB", range=10, aliases=("石块", "石头", "砖头")),
    Weapon(".22 小手枪", HANDGUN, "1D6", impale=True, firearm=True, range=10, shots=1, ammo=6, malfunction=100,
           aliases=(".22", "小口径手枪")),
    Weapon(".32 自动手枪", HANDGUN, "1D8", impale=True, firearm=True, range=15, shots=1, ammo=8, malfunction=99,
           aliases=(".32", "7.65", "自动手枪", "勃朗宁")),
    Weapon(".38 左轮手枪", HANDGUN, "1D10", impale=True, firearm=True, range=15, shots=1, ammo=6, malfunction=100,
           aliases=(".38", "左轮", "转轮手枪", "手枪", "警用左轮")),
    Weapon(".45 自动手枪", HANDGUN, "1D10+2", impale=True, firearm=True, range=15, shots=1, ammo=7, malfunction=100,
           aliases=(".45", "柯尔特", "M1911")),
    Weapon("猎枪", RIFLE, "1D6+1", impale=True, firearm=True, range=30, shots=1, ammo=6, malfunction=99,
           aliases=(".22 步枪", "小口径步枪")),
    Weapon("步枪", RIFLE, "2D6+4", impale=True, firearm=True, range=110, shots=1, ammo=5, malfunction=100,
           aliases=("春田", "栓动步枪", ".30-06", "M1903", "温彻斯特", "来复枪")),
    Weapon("霰弹枪", RIFLE, "4D6/2D6/1D6", firearm=True, range=10, shots=1, ammo=2, malfunction=100,
           aliases=("双管", "猎用霰弹枪", "散弹枪", "12 号")),
    Weapon("汤普森冲锋枪", "射击(冲锋枪)", "1D10+2", impale=True, firearm=True, range=20, shots=1, ammo=20, malfunction=96,
           aliases=("汤普森", "冲锋枪", "芝加哥打字机")),
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
_PER_ROUND = re.compile(r"每(?:回合|轮)\s*(\d+)\s*次")


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


def parse_attack(line: str) -> Written | None:
    """One line of a creature's `attacks`, or None when it names no chance to hit (a note such as
    "每回合 1 次")."""
    text = line.strip()
    pct = _PERCENT.search(text)
    if pct is None:
        return None
    value = int(pct.group(1))
    head = text[: pct.start()].strip(" ，,：:")
    inner = re.search(r"[（(]([^）)]+)[）)]", head)
    kind = head[: inner.start()].strip() if inner else head
    name = (inner.group(1) if inner else head).strip() or "攻击"
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
    return Written(name=name, skill=skill, value=value, damage=damage, firearm=firearm, impale=impale,
                   range=int(rng.group(1)) if rng else (gun.range if gun else 0),
                   ammo=int(left.group(1)) if left else int(clip.group(1)) if clip else (gun.ammo if gun else 0))


def attacks_per_round(lines: list[str], notes: str = "") -> int:
    """How many attacks a creature makes in a round, as its sheet says (1 when it does not)."""
    for text in [*lines, notes]:
        if found := _PER_ROUND.search(text):
            return max(1, int(found.group(1)))
    return 1
