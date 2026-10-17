"""Dice-bot commands familiar to Chinese CoC players (海豹骰 / Dice! style).

    .r 1d6+1          plain roll           .ra 侦查 / .rc 困难侦查 / .ra 侦查50
    .rab 侦查 / .rap2 侦查  bonus / penalty dice
    .sc 1/1d6         sanity check         .st 侦查60 hp-2 / .st 理智+3
    .coc [n]          roll characteristics .en [技能]  development roll
    .ti / .li         bout of madness (real time / summary)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .bcdice import Level
from .catalog import canonical
from .coc import DIFFICULTY_ZH, Investigator, Rules


@dataclass
class CommandResult:
    kind: str  # roll / check / sanity / set / coc / develop / madness / error
    text: str
    data: dict[str, Any] = field(default_factory=dict)


_CHECK = re.compile(
    r"^\.(?:ra|rc)(?P<mode>[bp])?(?P<n>\d)?\s*(?P<diff>困难|极难)?\s*(?P<name>[^\d\s+-]+?)\s*(?P<value>\d+)?\s*$"
)
_SC = re.compile(r"^\.sc\s*(?P<ok>[\dDd+\-]+)\s*/\s*(?P<bad>[\dDd+\-]+)\s*$")
_ST_ITEM = re.compile(r"(?P<name>[^\d\s+\-]+?)\s*(?P<op>[+\-])?\s*(?P<value>\d+)")


def is_command(text: str) -> bool:
    return text.strip().startswith((".", "。"))


def run(text: str, inv: Investigator | None, rules: Rules) -> CommandResult:
    text = text.strip().replace("。", ".", 1)
    low = text.lower()
    try:
        if low.startswith((".ra", ".rc")):
            return _check(low[:3] + text[3:], inv, rules)
        if low.startswith(".sc"):
            return _sanity(text, inv, rules)
        if low.startswith(".st"):
            return _set(text[3:], inv)
        if low.startswith(".coc"):
            return _coc(text[4:], rules)
        if low.startswith(".en"):
            return _develop(text[3:], inv, rules)
        if low.startswith((".ti", ".li")):
            table = "BMR" if low.startswith(".ti") else "BMS"
            return CommandResult("madness", rules.dice.table(table), {"table": table})
        if low.startswith(".r"):
            expr = text[2:].strip() or "1D100"
            roll = rules.dice.roll(expr)
            return CommandResult("roll", f"{expr} → {roll.total if roll.total is not None else roll.text}", {"text": roll.text})
    except Exception as exc:  # BCDice or rule errors are shown to the player as-is
        return CommandResult("error", str(exc))
    return CommandResult("error", f"不认识的指令：{text}")


def _need(inv: Investigator | None) -> Investigator:
    if inv is None:
        raise ValueError("还没有调查员，先建卡或选择预设调查员")
    return inv


def _check(text: str, inv: Investigator | None, rules: Rules) -> CommandResult:
    m = _CHECK.match(text.replace("（", "(").replace("）", ")"))
    if not m:
        raise ValueError("格式：.ra [困难|极难]技能[数值]，例如 .ra 侦查、.rab 图书馆使用、.ra 困难聆听")
    n = int(m["n"] or 1)
    bonus = n if m["mode"] == "b" else -n if m["mode"] == "p" else 0
    difficulty = DIFFICULTY_ZH.get(m["diff"] or "常规", Level.REGULAR)
    name = canonical(m["name"])
    if m["value"]:
        target = int(m["value"])
        result = rules.check(inv, name, difficulty, bonus, target=target) if inv else _bare(rules, name, target, difficulty, bonus)
    else:
        result = rules.check(_need(inv), name, difficulty, bonus)
    return CommandResult("check", result.describe(), {"check": result})


def _bare(rules: Rules, name: str, target: int, difficulty: Level, bonus: int):
    from .coc import Check

    return Check(name, target, difficulty, bonus, rules.dice.check(target, bonus))


def _sanity(text: str, inv: Investigator | None, rules: Rules) -> CommandResult:
    m = _SC.match(text.lower().replace(" ", ""))
    if not m:
        raise ValueError("格式：.sc 成功损失/失败损失，例如 .sc 1/1d6")
    result = rules.sanity(_need(inv), m["ok"].upper(), m["bad"].upper())
    return CommandResult("sanity", result.describe(), {"sanity": result})


def _set(body: str, inv: Investigator | None) -> CommandResult:
    inv = _need(inv)
    changes = []
    for m in _ST_ITEM.finditer(body):
        name, op, value = m["name"], m["op"], int(m["value"])
        current = _current(inv, name) if op else 0
        new = current + value if op == "+" else current - value if op == "-" else value
        key = inv.set(name, new)
        changes.append(f"{key} {new}")
    if not changes:
        raise ValueError("格式：.st 技能数值，例如 .st 侦查60 图书馆使用70 hp-2")
    return CommandResult("set", "已更新：" + "，".join(changes), {"changes": changes})


def _current(inv: Investigator, name: str) -> int:
    low = name.lower()
    if low in ("hp", "生命"):
        return inv.hp
    if low in ("mp", "魔法"):
        return inv.mp
    if low in ("san", "理智"):
        return inv.san
    if low in ("luck", "幸运"):
        return inv.luck
    return inv.value(name)


def _coc(body: str, rules: Rules) -> CommandResult:
    n = max(1, min(int(body.strip() or 1), 5))
    rows = []
    sets = []
    for _ in range(n):
        stats = rules.roll_characteristics()
        sets.append(stats)
        total = sum(v for k, v in stats.items() if k != "幸运")
        rows.append(" ".join(f"{k}{v}" for k, v in stats.items()) + f" 共计{total}")
    return CommandResult("coc", "\n".join(rows), {"sets": sets})


def _develop(body: str, inv: Investigator | None, rules: Rules) -> CommandResult:
    inv = _need(inv)
    skills = [canonical(s) for s in body.split()] or None
    results = rules.develop(inv, skills)
    if not results:
        return CommandResult("develop", "没有需要成长检定的技能")
    return CommandResult("develop", "\n".join(r.describe() for r in results), {"results": results})
