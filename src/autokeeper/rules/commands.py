"""Dice-bot commands familiar to Chinese CoC players (海豹骰 / Dice! style).

    .r 1d6+1          plain roll           .ra 侦查 / .rc 困难侦查 / .ra 侦查50
    .rab 侦查 / .rap2 侦查  bonus / penalty dice
    .sc 1/1d6         sanity check         .st 侦查60 hp-2 / .st 理智+3 / .st (show the sheet)
    .coc [n]          roll characteristics .en [技能]  development roll
    .ti / .li         bout of madness (real time / summary)

run() is the bare dice bot. In a game the player's commands go through Game.command, which keeps the keeper in charge: a check is only a request
(asked() reads it, the turn pipeline rules it), and nothing changes the sheet behind
the keeper's back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from .bcdice import Level
from .catalog import canonical
from .coc import DIFFICULTY_ZH, Investigator, Rules


@dataclass
class CommandResult:
    kind: str  # roll / check / sanity / set / sheet / coc / develop / madness / refused / error
    text: str
    data: dict[str, Any] = field(default_factory=dict)


class Asked(BaseModel):
    """The check a player asked for with .ra / .rc. Only a request: the arbiter decides
    whether it is rolled, how hard, and with how many bonus or penalty dice."""

    skill: str
    difficulty: Literal["常规", "困难", "极难"] = "常规"
    bonus: int = 0


_CHECK = re.compile(
    r"^\.(?:ra|rc)(?P<mode>[bp])?(?P<n>\d)?\s*(?P<diff>困难|极难)?\s*(?P<name>[^\d\s+-]+?)\s*(?P<value>\d+)?\s*$", re.IGNORECASE
)
_SC = re.compile(r"^\.sc\s*(?P<ok>[\dDd+\-]+)\s*/\s*(?P<bad>[\dDd+\-]+)\s*$")
_ST_ITEM = re.compile(r"(?P<name>[^\d\s+\-]+?)\s*(?P<op>[+\-])?\s*(?P<value>\d+)")
# Longer prefixes first: ".ra" before ".r".
_KINDS = ((".ra", "check"), (".rc", "check"), (".sc", "sanity"), (".st", "set"), (".coc", "coc"), (".en", "develop"),
          (".ti", "madness"), (".li", "madness"), (".r", "roll"))  # fmt: skip


def is_command(text: str) -> bool:
    return text.strip().startswith((".", "。"))


def kind(text: str) -> str:
    """Which command this is, without running it; "error" for anything unknown."""
    low = text.strip().replace("。", ".", 1).lower()
    return next((k for prefix, k in _KINDS if low.startswith(prefix)), "error")


def asked(text: str) -> Asked | None:
    """The check a .ra / .rc command asks for, without rolling it. None for anything else,
    and for a check typed wrong (run() explains the format). A number after the skill
    (.ra 侦查50) is left out: in a game the sheet says how good the investigator is."""
    text = text.strip().replace("。", ".", 1)
    if kind(text) != "check":
        return None
    m = _match_check(text)
    if not m:
        return None
    return Asked(skill=canonical(m["name"]), difficulty=m["diff"] or "常规", bonus=_bonus(m))  # type: ignore[arg-type]


def instant(text: str) -> bool:
    """A command answered at once, outside the story: everything but a check."""
    return is_command(text) and asked(text) is None


def run(text: str, inv: Investigator | None, rules: Rules) -> CommandResult:
    text = text.strip().replace("。", ".", 1)
    low = text.lower()
    what = kind(text)
    try:
        if what == "check":
            return _check(text, inv, rules)
        if what == "sanity":
            return _sanity(text, inv, rules)
        if what == "set":
            return _set(text[3:], inv)
        if what == "coc":
            return _coc(text[4:], rules)
        if what == "develop":
            return _develop(text[3:], inv, rules)
        if what == "madness":
            table = "BMR" if low.startswith(".ti") else "BMS"
            return CommandResult("madness", rules.dice.table(table), {"table": table})
        if what == "roll":
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


def _match_check(text: str) -> re.Match[str] | None:
    return _CHECK.match(text.replace("（", "(").replace("）", ")"))


def _bonus(m: re.Match[str]) -> int:
    n, mode = int(m["n"] or 1), (m["mode"] or "").lower()
    return n if mode == "b" else -n if mode == "p" else 0


def _check(text: str, inv: Investigator | None, rules: Rules) -> CommandResult:
    m = _match_check(text)
    if not m:
        raise ValueError("格式：.ra [困难|极难]技能[数值]，例如 .ra 侦查、.rab 图书馆使用、.ra 困难聆听")
    bonus = _bonus(m)
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
    if not body.strip():
        return CommandResult("sheet", sheet(inv))
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


def sheet(inv: Investigator) -> str:
    """The investigator sheet as text (.st with nothing after it)."""
    san = str(inv.san) if inv.san is not None else "未公开"
    rows = [
        f"{inv.name}（{inv.occupation}，{inv.age} 岁）HP {inv.hp}/{inv.hp_max} MP {inv.mp}/{inv.mp_max} 理智 {san} 幸运 {inv.luck}",
        " ".join(f"{k}{v}" for k, v in inv.characteristics.items()),
        " ".join(f"{k}{v}" for k, v in sorted(inv.skills.items(), key=lambda kv: -kv[1])),
    ]
    if inv.conditions:
        rows.append("状态：" + "、".join(inv.conditions))
    if inv.marks:
        rows.append("这局成功过、结局后可以成长：" + "、".join(inv.marks))
    return "\n".join(rows)


def _current(inv: Investigator, name: str) -> int:
    low = name.lower()
    if low in ("hp", "生命"):
        return inv.hp
    if low in ("mp", "魔法"):
        return inv.mp
    if low in ("san", "理智"):
        return inv.require_sanity()
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
