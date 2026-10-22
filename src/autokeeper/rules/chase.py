"""Chases as the Keeper Rulebook runs them (ch. 7, pp. 111–128), without the story.

Every number is in `ChaseRules` with the page it comes from (`ChaseRules.cite`); the few the book
leaves to the keeper and the project fixes are marked unverified there. The functions are pure: the
encounter engine (engine/encounter/chase.py) throws the dice and keeps the positions.

- Setting it up (p. 112, p. 124). Everyone rolls CON for speed (Drive Auto in a vehicle): an extreme
  success is MOV +1 for this chase, a failure −1. A quarry faster than every pursuer gets away at
  once and there is no chase; a pursuer slower than every quarry is left behind. The slowest pursuer
  starts at the start, each other one a location further on per point of MOV over it; the slowest
  quarry two locations ahead of the fastest pursuer (one in a special case, never more than two),
  each other one further on by its MOV over the slowest quarry (`placement`).
- A round (p. 113). DEX order, ties by opposed DEX. Everyone gets 1 action point plus one per point of
  MOV over the slowest in the chase; moving to the next location costs one.
- Hazards (p. 114): crossing carefully spends one or two more points for one or two bonus dice; a
  failure is harm the keeper sets from Table III and 1D3 action points lost, and the runner still
  reaches the next location. Points owed beyond this round come off the next (the farmer, p. 114).
- Barriers (pp. 115–116) block until passed or destroyed: a failure usually just means no progress.
  Damage breaks them, no attack roll needed, against their hit points (`BARRIER_HP`); the wreck may
  be a hazard for those after.
- Conflict (p. 116): an attack only on someone at the same location (a firearm excepted), one action
  point, no more attacks than one makes in a round, settled as a combat round; anyone attacked may
  always dodge or fight back, whatever points it has left. A manoeuvre's success makes the target
  lose 1D3 action points as if it failed a hazard (and perhaps harm), or does what it would in a fight.
- Optional rules (pp. 117–121): hiding at any time (Stealth, the difficulty by the situation and the
  pursuers' Spot Hidden, bonus dice when far ahead); losing track (the keeper's call: a Track roll
  once the quarry is out of sight); shooting while stopped (1 point, no move) or on the move (a
  penalty die, no points); joining mid-chase; hazards made on purpose; monsters' missing skills from
  DEX by aptitude. Vehicles (floor it, passengers, crashes, Tables V and VI) are not in v1.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Literal

# The way a chase runs is module format (module/schema.py); still importable from here.
from ..module.schema import Leg as Leg
from ..module.schema import Method as Method
from ..module.schema import Obstacle as Obstacle
from .bcdice import Level
from .melee import opposed_difficulty

Aptitude = Literal["good", "plain", "poor"]


def _rule(value, page: str, *, verified: bool = True, note: str = ""):
    return field(default=value, metadata={"page": page, "verified": verified, "note": note})


@dataclass(frozen=True)
class ChaseRules:
    """The chase's numbers, each with its page in the Keeper Rulebook (Chinese edition). A number the
    book leaves to the keeper is `verified=False`, with what the project made of it."""

    speed_extreme: int = _rule(1, "112")  # MOV after an extreme speed roll
    speed_fail: int = _rule(-1, "112")  # MOV after a failed one
    speed_floor: int = _rule(1, "112", verified=False, note="项目设定：速度检定后 MOV 不低于 1")
    start_gap: int = _rule(2, "112")  # locations between the pursuer and the quarry at the start
    start_gap_min: int = _rule(1, "112")  # "在特殊情况下也可将其缩短为一个地点"
    start_gap_max: int = _rule(2, "112")  # "不要将距离设置为大于两个地点"
    base_points: int = _rule(1, "113")  # action points everyone has
    move_cost: int = _rule(1, "113")  # one location
    careful_max: int = _rule(2, "114")  # extra points spent on a hazard for as many bonus dice
    hazard_loss: str = _rule("1D3", "114")  # action points a failed hazard costs
    delay_carries: bool = _rule(True, "114")  # points owed come off the next round (the farmer)
    hazard_moves: bool = _rule(True, "114")  # a hazard is crossed, failed or not
    barrier_cost: int = _rule(1, "115", verified=False, note="项目设定：每试一次越过障碍花 1 行动点")
    attack_cost: int = _rule(1, "116")
    act_cost: int = _rule(1, "113", verified=False, note="项目设定：撬锁、急救、装弹这类耗时的事花 1 行动点；书上只说要花时间")
    maneuver_loss: str = _rule("1D3", "116")  # action points the target of a manoeuvre loses
    shoot_stopped_cost: int = _rule(1, "120")  # stop and shoot: one point, no move
    shoot_moving_penalty: int = _rule(1, "120")  # shoot on the move: a penalty die, no points
    sight_gap: int = _rule(3, "119", verified=False,
                           note="项目设定：领先三个地点以上（起始的两个地点仍看得见）或隔着挡住视线的地点就算逃出视线；书上由守秘人判定")
    hide_far: int = _rule(2, "119", verified=False,
                          note="项目设定：领先两个地点以上时躲藏得一个奖励骰，三个以上两个；书上由守秘人视情况给")
    lock_rounds: int = _rule(1, "121")  # locking a door with Locksmith takes a whole round and a roll
    bolt_cost: int = _rule(1, "121")  # bolting or locking with a key: one point, no roll
    wardrobe_cost: int = _rule(1, "121")  # pushing a wardrobe across a door: STR and one point
    stall_cost: int = _rule(1, "121")  # overturning a fruit stall: one point
    random_hazard: tuple[int, int, int] = _rule((60, 85, 96), "117")  # 1D100: regular, hard, extreme
    swim_factor: float = _rule(0.5, "120")  # MOV in another way of moving with no listed speed

    @classmethod
    def cite(cls, name: str) -> str:
        """Where `name` comes from: "p. 114", or the project's note for an unverified one."""
        for f in fields(cls):
            if f.name == name:
                m = f.metadata
                return f"p. {m['page']}" + ("" if m["verified"] else f"（未核验：{m['note']}）")
        raise KeyError(name)

    @classmethod
    def unverified(cls) -> list[str]:
        return [f.name for f in fields(cls) if not f.metadata["verified"]]


RULES = ChaseRules()

# Barriers' hit points (p. 116).
BARRIER_HP = {
    "室内门": 5,
    "薄围栏": 5,
    "后门": 10,
    "外门": 15,
    "砖墙": 25,
    "大树": 50,
    "混凝土桥墩": 100,
}

# Hazards made on purpose (p. 121): what it costs (points; "round" a whole round) and the roll, if any.
CREATED = {
    "bolt": ("闩上门或用钥匙锁门", 1, ""),
    "lock": ("用锁匠技能锁门", "round", "锁匠"),
    "wardrobe": ("推重物挡门", 1, "力量"),
    "stall": ("掀翻摊子", 1, ""),
    "panic": ("开枪惊散人群", 1, "幸运"),
    "thief": ("喊路人帮忙抓人", 1, "话术/恐吓"),
}


# -- setting up -----------------------------------------------------------------------------------
def speed_after(mov: int, level: Level, rules: ChaseRules = RULES) -> int:
    """MOV for this chase after the speed roll (CON, or Drive Auto in a vehicle) at `level`:
    +1 at an extreme success, −1 at a failure (p. 112)."""
    delta = rules.speed_extreme if level >= Level.EXTREME else rules.speed_fail if not level.ok else 0
    return max(rules.speed_floor, mov + delta)


def escapes_at_once(quarry: int, pursuers: list[int]) -> bool:
    """A quarry of speed `quarry` faster than every pursuer gets away before the chase begins
    (pp. 112, 124). With no pursuer at all it is away too."""
    return not pursuers or quarry > max(pursuers)


@dataclass(frozen=True)
class Lineup:
    """Who is in the chase after the speeds, and where everyone starts (location 0 the rear)."""

    escaped: list[str]  # quarry faster than the fastest pursuer: away at once
    left_behind: list[str]  # pursuers slower than the slowest quarry: out of it
    positions: dict[str, int]

    @property
    def on(self) -> bool:
        """There is a chase: someone to run and someone after them."""
        return bool(self.positions)


def placement(quarry: dict[str, int], pursuers: dict[str, int], *, gap: int | None = None,
              rules: ChaseRules = RULES) -> Lineup:
    """The start of a chase (p. 124): quarry and pursuers by id → adjusted MOV. Quarry faster than the
    fastest pursuer escape; pursuers slower than the slowest quarry are left behind. The slowest
    pursuer starts at 0, each other one a location on per point of MOV over it; the slowest quarry
    `gap` (2; 1 in special cases, never more than 2) ahead of the fastest pursuer, each other one on
    by its MOV over the slowest quarry. Every quarry escaping, or no pursuer left: no positions."""
    gap = rules.start_gap if gap is None else max(rules.start_gap_min, min(rules.start_gap_max, gap))
    fastest = max(pursuers.values(), default=0)
    escaped = [q for q, s in quarry.items() if not pursuers or s > fastest]
    staying = {q: s for q, s in quarry.items() if q not in escaped}
    slowest_q = min(staying.values(), default=0)
    behind = [p for p, s in pursuers.items() if staying and s < slowest_q]
    hunting = {p: s for p, s in pursuers.items() if p not in behind}
    if not staying or not hunting:
        return Lineup(escaped, behind, {})
    slow_p = min(hunting.values())
    positions = {p: s - slow_p for p, s in hunting.items()}
    lead = max(positions.values()) + gap
    positions |= {q: lead + s - slowest_q for q, s in staying.items()}
    return Lineup(escaped, behind, positions)


def chase_order(dex: dict[str, int], ties: dict[str, int] | None = None) -> list[str]:
    """DEX order for a chase round, highest first; a tie goes by an opposed DEX roll (p. 113): `ties`
    holds each tied runner's level of success (a Level as int), the better first."""
    ties = ties or {}
    return sorted(dex, key=lambda k: (-dex[k], -ties.get(k, 0)))


# -- action points --------------------------------------------------------------------------------
def action_points(speed: int, slowest: int, rules: ChaseRules = RULES) -> int:
    """A round's action points: one, and one more per point of MOV over the slowest in the chase
    (p. 113)."""
    return rules.base_points + max(0, speed - slowest)


def start_round(points: int, owed: int, rules: ChaseRules = RULES) -> tuple[int, int]:
    """The points a runner has this round once what it owes from the last is paid, and what it still
    owes after (p. 114: the farmer pays last round's slip from this round's two)."""
    if not rules.delay_carries:
        return points, 0
    paid = min(points, owed)
    return points - paid, owed - paid


def spend(points: int, cost: int, rules: ChaseRules = RULES) -> tuple[int, int]:
    """Points left and points owed after spending `cost` with `points` left: what it cannot pay now
    it owes the next round (p. 114)."""
    left = points - max(0, cost)
    if left >= 0:
        return left, 0
    return 0, (-left if rules.delay_carries else 0)


# -- hazards and barriers -------------------------------------------------------------------------
def careful(extra: int, rules: ChaseRules = RULES) -> int:
    """Bonus dice for crossing a hazard carefully: one per extra action point spent, two at most
    (p. 114)."""
    return max(0, min(rules.careful_max, extra))


@dataclass(frozen=True)
class Crossed:
    """What crossing an obstacle did to a runner."""

    moved: bool  # reached the next location
    lost: int = 0  # action points lost (still to be paid, maybe the next round)
    harmed: bool = False  # the keeper's harm applies (Table III)
    broken: bool = False  # the barrier is open for those behind


def hazard(passed: bool, loss: int = 0, *, harm: bool = True) -> Crossed:
    """A hazard crossed: on a failure the runner loses `loss` (its 1D3, rolled) action points and
    takes the keeper's harm, if the hazard has any; failed or not it reaches the next location
    (p. 114)."""
    if passed:
        return Crossed(True)
    return Crossed(True, max(0, loss), harm)


def barrier(passed: bool, *, opens: bool = False) -> Crossed:
    """A barrier tried: passed, the runner is through (and with a method that breaks or unlocks it,
    it stays open behind them); failed, it stays where it is (p. 115)."""
    return Crossed(passed, broken=passed and opens)


def barrier_hit(hp: int, damage: int) -> tuple[int, bool]:
    """A barrier's hit points after `damage` (no attack roll needed) and whether it is destroyed:
    at 0 it no longer blocks, though its wreck may be a hazard (pp. 115–116)."""
    left = max(0, hp - max(0, damage))
    return left, left == 0


# -- conflict -------------------------------------------------------------------------------------
def attack_problems(*, same_location: bool, firearm: bool, attacks_made: int, attacks_per_round: int,
                    points: int, rules: ChaseRules = RULES) -> list[str]:
    """Why an attack in a chase cannot be made now, in words; [] when it can (p. 116): only at the
    same location unless with a firearm, one action point, no more than one's attacks in a round."""
    out = []
    if not same_location and not firearm:
        out.append("不在同一个地点，够不着")
    if attacks_made >= max(1, attacks_per_round):
        out.append(f"这一轮已经攻击了 {attacks_made} 次")
    if points < rules.attack_cost:
        out.append("这一轮的行动点用完了")
    return out


def shot_cost(moving: bool, rules: ChaseRules = RULES) -> tuple[int, int]:
    """(action points, penalty dice) of shooting in a chase: stopping to shoot costs a point and no
    move; shooting on the move costs none and takes a penalty die (p. 120)."""
    return (0, rules.shoot_moving_penalty) if moving else (rules.shoot_stopped_cost, 0)


# -- hiding and losing track ------------------------------------------------------------------------
def hide_difficulty(spot: list[int]) -> Level:
    """The difficulty of hiding from pursuers with Spot Hidden `spot`: as an opposed roll against the
    best of them (p. 119, with p. 72)."""
    return opposed_difficulty(max(spot, default=0))


def hide_bonus(lead: int, rules: ChaseRules = RULES) -> int:
    """Bonus dice a far-ahead quarry gets to hide: one at `hide_far` locations ahead, two beyond
    (p. 119 leaves it to the keeper; the project's default)."""
    if lead < rules.hide_far:
        return 0
    return 1 if lead == rules.hide_far else 2


def hidden(level: Level, difficulty: Level) -> bool:
    """A hiding quarry's Stealth at `level` against `difficulty`: it got away when it passed (p. 119)."""
    return level >= difficulty


def out_of_sight(lead: int, *, blocked: bool = False, rules: ChaseRules = RULES) -> bool:
    """Whether a quarry `lead` locations ahead of a pursuer is out of its sight: far enough ahead, or a
    sight-blocking location between them (p. 119 leaves where to the keeper)."""
    return blocked or lead >= rules.sight_gap


def lost_track(track: Level) -> bool:
    """A pursuer's Track (or Spot Hidden) once the quarry is out of sight: a failure and it has lost
    that quarry (p. 119: the deep one's Track roll outside the theatre)."""
    return not track.ok


# -- the rest ---------------------------------------------------------------------------------------
def monster_skill(dex: int, aptitude: Aptitude = "plain") -> int:
    """A chase skill (Jump, Swim, Climb…) a monster, an animal or an NPC has no number for: its DEX
    when it has an aptitude for it, half when it is ordinary at it, a fifth when it is poor (p. 121).
    Spot Hidden goes the same way from INT."""
    return {"good": dex, "plain": dex // 2, "poor": dex // 5}[aptitude]


def joins(side: Literal["quarry", "pursuer"], speed: int, quarry: list[int], pursuers: list[int]) -> bool:
    """Whether someone joining mid-chase at `speed` (after its own speed roll) is in it: a pursuer
    slower than the slowest quarry can be ignored (p. 120). A quarry always is; when it is slower than
    every runner so far, everyone's action points are worked out again from it."""
    if side == "pursuer":
        return not quarry or speed >= min(quarry)
    return True


def other_movement(mov: int, rules: ChaseRules = RULES) -> int:
    """MOV in a way of moving the sheet lists no speed for (a human swimming): half (p. 120)."""
    return max(1, int(mov * rules.swim_factor))


def random_hazard(roll: int, rules: ChaseRules = RULES) -> Level | None:
    """The optional random hazard of p. 117 (1D100): None for a clear location, else the difficulty."""
    regular, hard, extreme = rules.random_hazard
    if roll >= extreme:
        return Level.EXTREME
    if roll >= hard:
        return Level.HARD
    if roll >= regular:
        return Level.REGULAR
    return None
