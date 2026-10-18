"""Building an investigator by the CoC 7th edition creation rules.

Characteristics come from the quick-fire array, occupation points from one of the
book's formulas (credit rating is paid from them), interest points are INT×2, and every
skill starts from its base chance. A draft that breaks a rule is repaired here, so the
player never gets a sheet the rules would refuse.
"""

from __future__ import annotations

from .catalog import CHARACTERISTICS, base_value, canonical
from .coc import Investigator

ARRAY = (80, 70, 60, 60, 50, 50, 50, 40)  # quick fire: each value once
FORMULAS: dict[str, dict[str, int]] = {
    "教育×4": {"教育": 4},
    "教育×2+敏捷×2": {"教育": 2, "敏捷": 2},
    "教育×2+力量×2": {"教育": 2, "力量": 2},
    "教育×2+外貌×2": {"教育": 2, "外貌": 2},
    "教育×2+意志×2": {"教育": 2, "意志": 2},
}
CAP = 90  # highest skill at creation
NOT_BOUGHT = {"克苏鲁神话", "信用评级", "理智", "幸运"}  # nobody starts knowing the Mythos; credit is apart


def _clean(points: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, value in points.items():
        key = canonical(name)
        if key in CHARACTERISTICS or key in NOT_BOUGHT or int(value) <= 0:
            continue
        out[key] = out.get(key, 0) + int(value)
    return out


def _fit(points: dict[str, int], budget: int) -> dict[str, int]:
    """The allocations scaled down to the budget, in proportion."""
    total = sum(points.values())
    if total <= budget:
        return points
    return {k: v * budget // total for k, v in points.items()}


def make(
    inv_id: str,
    *,
    name: str,
    occupation: str,
    age: int,
    characteristics: dict[str, int],
    formula: str,
    credit: int,
    occupation_skills: dict[str, int],
    interest_skills: dict[str, int],
    luck: int,
    **profile,
) -> tuple[Investigator, list[str]]:
    """The investigator, and what had to change to make it legal."""
    fixes: list[str] = []
    stats = {k: int(characteristics.get(k, 0)) for k in CHARACTERISTICS}
    if sorted(stats.values(), reverse=True) != list(ARRAY):
        ranked = sorted(CHARACTERISTICS, key=lambda k: -stats[k])  # stable: ties keep the sheet's order
        stats = {k: ARRAY[ranked.index(k)] for k in CHARACTERISTICS}
        fixes.append("属性按 80、70、60、60、50、50、50、40 重新分配，强弱顺序不变")
    if formula not in FORMULAS:
        formula = "教育×4"
    budget = sum(stats[k] * n for k, n in FORMULAS[formula].items())
    credit = max(0, min(int(credit), CAP, budget))
    occ, hobby = _clean(occupation_skills), _clean(interest_skills)
    if sum(occ.values()) > budget - credit:
        occ = _fit(occ, budget - credit)
        fixes.append(f"职业技能点只有 {budget}（{formula}），按比例减少")
    if sum(hobby.values()) > stats["智力"] * 2:
        hobby = _fit(hobby, stats["智力"] * 2)
        fixes.append(f"兴趣技能点只有 {stats['智力'] * 2}（智力×2），按比例减少")
    skills: dict[str, int] = {}
    for key in {**occ, **hobby}:
        value = base_value(key, stats) + occ.get(key, 0) + hobby.get(key, 0)
        if value > CAP:
            fixes.append(f"{key} 建卡时最多 {CAP}")
        skills[key] = min(value, CAP)
    skills = dict(sorted(skills.items(), key=lambda kv: -kv[1]))
    if credit:
        skills["信用评级"] = credit
    inv = Investigator(
        id=inv_id, name=name, occupation=occupation, age=max(15, min(int(age), 89)),
        characteristics=stats, luck=luck, skills=skills, **profile,
    )  # fmt: skip
    return inv, fixes


def accept(
    inv_id: str,
    *,
    name: str,
    characteristics: dict[str, int],
    skills: dict[str, int],
    luck: int = 0,
    san: int = 0,
    hp: int = 0,
    mp: int = 0,
    age: int = 30,
    **profile,
) -> tuple[Investigator, list[str]]:
    """A card made elsewhere (another tool, a dice bot, paper), taken as written: only what
    cannot be is corrected, and every correction is said."""
    fixes: list[str] = []
    stats: dict[str, int] = {}
    for k in CHARACTERISTICS:
        value = int(characteristics.get(k, 0) or 0)
        if not 1 <= value <= 99:
            fixes.append(f"原卡的{k}是 {value or '空的'}，按 50 算")
            value = 50
        stats[k] = value
    if not 1 <= int(luck or 0) <= 99:
        fixes.append("原卡没写幸运，按 50 算")
        luck = 50
    kept: dict[str, int] = {}
    for raw, value in skills.items():
        key = canonical(raw)
        if key in CHARACTERISTICS or key in ("理智", "幸运", "灵感", "知识"):
            continue
        value = int(value or 0)
        if value > 99:
            fixes.append(f"{key} {value} 超过 99，按 99 算")
        if value > base_value(key, stats) or (key == "克苏鲁神话" and value > 0):
            kept[key] = min(value, 99)
    kept = dict(sorted(kept.items(), key=lambda kv: -kv[1]))
    inv = Investigator(id=inv_id, name=name, age=max(15, min(int(age or 30), 89)), characteristics=stats, luck=int(luck), skills=kept, **profile)
    for field_name, value, top in (("san", san, inv.san_max), ("hp", hp, inv.hp_max), ("mp", mp, inv.mp_max)):
        if value and 0 < int(value) <= top:  # the card says where they stand now: an old wound, sanity already spent
            setattr(inv, field_name, int(value))
    inv.san_start_of_day = inv.san
    return inv, fixes
