"""Chases as CoC 7th edition runs them, without the story.

- The way is a track of places (legs). The quarry starts a couple of legs ahead of the pursuers.
- Everyone rolls CON for speed at the start: an extreme success runs one MOV faster for this chase,
  a failure one slower (speed_roll). A quarry faster than every pursuer is gone at once.
- Each round, everyone has 1 + (their speed - the slowest speed in the chase) movement actions
  (actions_for). Moving one leg costs one.
- A hazard on a leg (ice, a ditch, headstones) is a roll as you cross it: a failure costs 1D3 of
  your movement actions, and some hazards hurt (cross). A barrier (a locked gate, a fence) has to
  be got through: a failure stops you there for this action.
- Out of sight (two legs ahead or more), the quarry can try to hide: Stealth against the pursuers'
  Spot Hidden. A quarry who gets far enough ahead (LOST legs) or off the end of the track gets away;
  a pursuer who reaches the quarry's leg has caught up, and it comes to blows.

Movement is the engine's (engine/chase.py): who goes in what order, what everyone chooses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# The way a chase runs is module format now (module/schema.py); still importable from here.
from ..module.schema import Leg as Leg
from ..module.schema import Method as Method
from ..module.schema import Obstacle as Obstacle
from ..module.schema import Track as Track
from .bcdice import Level
from .coc import Check
from .combat import Fighter, check

LOST = 3  # legs ahead of every pursuer at the end of a round: they have lost you
HEAD_START = 2  # legs the quarry starts ahead
SIGHT = 2  # legs ahead at which the quarry is out of the pursuers' sight and may hide
DIFFICULTY = {"常规": Level.REGULAR, "困难": Level.HARD, "极难": Level.EXTREME}


def speed_roll(dice, f: Fighter) -> Check:
    """CON for speed: an extreme success one MOV faster for this chase, a failure one slower."""
    roll = check(dice, "体质", f.con)
    f.speed = f.move + (1 if roll.level >= Level.EXTREME else -1 if not roll.passed else 0)
    f.speed = max(1, f.speed)
    return roll


def gone_at_once(quarry: list[Fighter], pursuers: list[Fighter]) -> bool:
    """Every quarry faster than every pursuer: there is no chase."""
    runners = [p for p in pursuers if p.standing]
    return bool(quarry) and (not runners or min(q.speed for q in quarry) > max(p.speed for p in runners))


def actions_for(f: Fighter, slowest: int) -> int:
    return 1 + max(0, f.speed - slowest)


class Crossing(BaseModel):
    """What happened at an obstacle."""

    who: str
    what: str
    kind: Literal["hazard", "barrier"]
    skill: str
    passed: bool
    lost: int = 0  # movement actions it cost
    harm: int = 0
    harm_text: str = ""


def cross(dice, f: Fighter, leg: Leg, *, value: int, bonus: int = 0) -> tuple[Check | None, Crossing | None]:
    """Getting through the obstacle of the leg ahead. `value`: the runner's skill for it. A hazard
    is always crossed, perhaps late and hurt; a barrier holds the runner when they fail."""
    ob = leg.obstacle
    if ob is None or (ob.kind == "barrier" and ob.open):
        return None, None
    roll = check(dice, ob.skill, value, DIFFICULTY.get(ob.difficulty, Level.REGULAR), bonus)
    out = Crossing(who=f.id, what=ob.what, kind=ob.kind, skill=ob.skill, passed=roll.passed)
    if roll.passed:
        if ob.kind == "barrier" and ob.skill in ("力量", "锁匠", "机械维修"):  # broken or unlocked: open behind them
            ob.open = True
        return roll, out
    if ob.kind == "hazard":
        out.lost = dice.total("1D3")
        if ob.harm:
            out.harm = dice.total(ob.harm)
            out.harm_text = f"{ob.harm} = {out.harm}"
    return roll, out


def gap(quarry: list[Fighter], pursuers: list[Fighter]) -> int:
    """How far ahead the nearest quarry is of the nearest pursuer still running (legs)."""
    runners = [p for p in pursuers if p.standing]
    ahead = [q for q in quarry if q.standing]
    if not runners or not ahead:
        return LOST
    return min(q.pos for q in ahead) - max(p.pos for p in runners)


def hide(dice, stealth: int, spotters: list[Fighter]) -> tuple[Check, list[tuple[str, Check]], bool]:
    """Stealth against every pursuer's Spot Hidden: hidden when the runner's level of success beats
    every one of theirs."""
    roll = check(dice, "潜行", stealth)
    seen = [(p.id, check(dice, "侦查", p.value("侦查"))) for p in spotters]
    hidden = roll.passed and all(roll.level > s.level for _, s in seen)
    return roll, seen, hidden
