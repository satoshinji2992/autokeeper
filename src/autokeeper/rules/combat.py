"""Combat's shared numbers, without the story: who acts first (turn_order), a roll of anyone's
(check), a sheet's characteristics and the damage bonus and build they make (stats_of,
damage_bonus), and what a creature's notes say the rules can use (traits). Damage expressions are
rules/dice_expr.py's.

An attack and its answer are rules/melee.py's, shots rules/firearms.py's, what a blow does
rules/wounds.py's, a chase rules/chase.py's; the encounter engine (engine/encounter) runs them."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .bcdice import Level
from .coc import Check, _build


@dataclass(frozen=True)
class Slot:
    """One combatant's place in the round's order: DEX, the +50 of a readied firearm it means to fire
    this round (rules/firearms.readied_bonus), its best combat skill for a tie."""

    id: str
    dex: int
    combat: int = 0
    readied: int = 0  # 50 or 0
    first_on_tie: bool = False  # the project's last tie-break: an investigator before anyone else


def turn_order(slots: list[Slot]) -> list[str]:
    """Who acts in what order this round (Keeper Rulebook p. 86): DEX from high to low, a readied
    firearm being fired this round +50 (p. 95), the higher combat skill first on a tie (p. 86); still
    tied, an investigator first (the project's)."""
    return [s.id for s in sorted(slots, key=lambda s: (-(s.dex + s.readied), -s.combat, not s.first_on_tie))]


def check(dice, name: str, target: int, difficulty: Level = Level.REGULAR, bonus: int = 0) -> Check:
    """A percentile roll of anyone's (an NPC's has no sheet to tick)."""
    bonus = max(-2, min(2, bonus))
    return Check(name, max(1, target), difficulty, bonus, dice.check(max(1, target), bonus))


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
