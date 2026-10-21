"""Shooting as the Keeper Rulebook settles it (ch. 6, pp. 95–98), without dice or story.

A shot is no contest: one roll against the firearms skill, at a difficulty set only by the range,
then bonus and penalty dice for everything else (p. 95). Pure functions; the encounter engine rolls.

- Range (`range_band`, `shot_difficulty`): within the weapon's base range regular, up to twice it
  hard, up to four times extreme; at extreme range only a critical impales (p. 95). A shotgun's
  spread keeps its chance the same at every range and only its damage falls off (pp. 54, 373).
- Dice (`shot_modifiers`, p. 95–96 and the summary table p. 98): the target dived for cover and made
  its Dodge, half or more of it hidden, it is moving at MOV 8+ at full speed, it is small (build −2
  or less), the shooter loaded one round and fired in the same round, fired 2–3 handgun shots this
  round, or fired into a melee: a penalty die each. Point blank (within the shooter's DEX/5 feet),
  a round spent aiming, a big target (build 4+): a bonus die each. Two at most either way.
- Into a melee: a fumble hits an ally instead, the one with the lowest Luck (p. 96).
- Diving for cover (`dive_for_cover`): a Dodge roll; a success gives the shooter a penalty die, and
  the diver loses its next attack either way, all of them if it makes several (p. 95).
- Reloading (`reload`): two rounds a round into a pistol, rifle or shotgun; a fresh magazine in one
  round; a machine-gun belt in two (p. 96).
- Malfunction (`malfunction`): a roll at or over the weapon's number and it does not fire; a
  revolver, break-action or bolt-action gun only had a dud; a lever-action jams, and clearing it
  takes 1D6 rounds and a Mechanical Repair or Firearms roll, one try a round (p. 98).

Automatic fire (volleys, bursts, the extra penalty dice and difficulty per volley, pp. 96–98) is not
here: the keeper rules it as an `other` action. So are the optional rules of pp. 106–108 (armour per
die, hit locations, suppressing fire, scopes, prone), apart from the prone dice in `shot_modifiers`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .bcdice import Level
from .melee import MAX_DICE

Mechanism = Literal["revolver", "break", "bolt", "lever", "pump", "auto", ""]
Malfunction = Literal["", "dud", "jam"]
Loading = Literal["rounds", "magazine", "belt"]

DIFFICULTY = (Level.REGULAR, Level.HARD, Level.EXTREME)  # by range band (p. 95)
BAND_NAMES = ("基础射程", "远射程", "超射程")
DUD = ("revolver", "break", "bolt")  # a malfunction is only a dud (p. 98)
JAM_CLEAR = "1D6"  # rounds a jam takes to clear (p. 98)
UNJAM_SKILLS = ("机械维修", "射击")  # either roll clears it once those rounds are spent (p. 98)
ROUNDS_PER_RELOAD = 2  # cartridges loaded in one round (p. 96)
BELT_ROUNDS = 2  # rounds a machine-gun belt takes (p. 96)
VOLLEY = 3  # a handgun's shots in one round at most (p. 96; the weapon table's "1(3)")


def range_band(distance: float, base_range: int, *, shotgun: bool = False) -> int | None:
    """Which range band a target `distance` yards away is in for a weapon of `base_range` yards:
    0 base, 1 long (up to twice it), 2 extreme (up to four times), None beyond (p. 95). A shotgun's
    three damage bands are 10/20/50 yards for a 12-gauge: pass its first band as `base_range` and
    `shotgun`, the bands are then its own (p. 373)."""
    if base_range <= 0:
        return 0
    if shotgun:
        bands = (base_range, base_range * 2, base_range * 5)
        return next((i for i, b in enumerate(bands) if distance <= b), None)
    for i, factor in enumerate((1, 2, 4)):
        if distance <= base_range * factor:
            return i
    return None


def shot_difficulty(band: int, *, shotgun: bool = False) -> Level:
    """The difficulty of a shot at range band `band` (p. 95). A shotgun's is regular at every band:
    its spread loses damage, not accuracy (p. 54)."""
    if shotgun:
        return Level.REGULAR
    return DIFFICULTY[max(0, min(2, band))]


def point_blank(distance_ft: float, dex: int) -> bool:
    """Within the shooter's DEX/5 feet (p. 95)."""
    return distance_ft <= dex / 5


@dataclass(frozen=True)
class Shot:
    """What the keeper and the engine know of one shot's circumstances."""

    band: int = 0  # range band (range_band)
    shotgun: bool = False
    point_blank: bool = False
    aimed: bool = False  # spent the last round aiming, not hurt nor moved since (p. 95)
    dived: bool = False  # the target dived for cover and made its Dodge this round
    partial_cover: bool = False  # half or more of the target hidden
    fast_target: bool = False  # the target is moving at full speed, MOV 8 or more
    target_build: int = 0
    loaded_one: bool = False  # loaded one round and fired in the same round
    volley: int = 1  # shots fired this round with a handgun (2–3: each at a penalty die)
    into_melee: bool = False  # the target is in a melee
    moving: bool = False  # firing on the move in a chase (p. 120)
    prone_target: bool = False  # optional rule p. 107: harder to hit unless point blank
    prone_shooter: bool = False  # optional rule p. 107: steadier
    bonus: int = 0  # anything else the keeper rules


@dataclass(frozen=True)
class ShotModifiers:
    difficulty: Level
    dice: int  # net bonus (+) or penalty (−) dice, two at most either way
    reasons: list[str] = field(default_factory=list)
    impale_needs: Level = Level.EXTREME  # the level a hit needs to impale (critical at extreme range)


def shot_modifiers(s: Shot) -> ShotModifiers:
    """The difficulty and the dice of a shot (pp. 95–96, the summary on p. 98)."""
    dice, why = s.bonus, []

    def add(n: int, reason: str) -> None:
        nonlocal dice
        dice += n
        why.append(("+" if n > 0 else "−") + reason)

    if s.point_blank:
        add(1, "抵近射击")
    if s.aimed:
        add(1, "瞄准了一轮")
    if s.target_build >= 4:
        add(1, "目标体型巨大")
    if s.target_build <= -2:
        add(-1, "目标体型很小")
    if s.dived:
        add(-1, "目标扑向掩体")
    if s.partial_cover:
        add(-1, "目标一半以上被遮住")
    if s.fast_target:
        add(-1, "目标在全速奔跑")
    if s.loaded_one:
        add(-1, "装一发就开火")
    if s.volley >= 2:
        add(-1, f"一轮连开 {min(s.volley, VOLLEY)} 枪")
    if s.into_melee:
        add(-1, "朝扭打中的人开枪")
    if s.moving:
        add(-1, "边跑边射")
    if s.prone_target and not s.point_blank:
        add(-1, "目标趴在地上")
    if s.prone_shooter:
        add(1, "趴着射击")
    extreme_range = s.band >= 2 and not s.shotgun
    return ShotModifiers(shot_difficulty(s.band, shotgun=s.shotgun), max(-MAX_DICE, min(MAX_DICE, dice)), why,
                         Level.CRITICAL if extreme_range else Level.EXTREME)


def shots_allowed(per_round: int, volley: int = 1) -> int:
    """How many shots a weapon fires in one round: its steady rate, or for a handgun ("1(3)") up to
    `volley` (2–3), each then at a penalty die (p. 96)."""
    return max(1, min(volley, VOLLEY) if volley > 1 else per_round)


def second_barrel_penalty(same_target: bool) -> int:
    """A double-barrelled shotgun's second shot in the same round: no penalty at the same target, a
    penalty die at another (the weapon table's notes, p. 375)."""
    return 0 if same_target else -1


def fumble_hits(allies: dict[str, int]) -> str:
    """Who a fumbled shot into a melee hits: the ally (id → Luck) with the lowest Luck (p. 96); ""
    when there is nobody."""
    return min(allies, key=lambda k: (allies[k], k)) if allies else ""


# -- diving for cover -----------------------------------------------------------------------------
@dataclass(frozen=True)
class Cover:
    penalty: int  # dice the shooter takes: −1 when the dive worked
    lose_next: bool = True  # the diver's next attack is gone either way
    all_attacks: bool = False  # a multi-attack creature loses every attack of its next turn


def dive_for_cover(dodge: Level, *, attacks_per_round: int = 1) -> Cover:
    """A target diving for cover rolls Dodge: a success gives the shooter a penalty die; the diver
    loses its next attack whether it worked or not, and a creature with several attacks all of them
    (p. 95)."""
    return Cover(-1 if dodge.ok else 0, True, attacks_per_round > 1)


# -- malfunction and jams -------------------------------------------------------------------------
def malfunction(roll: int, number: int, mechanism: Mechanism = "") -> Malfunction:
    """What a firearms roll of `roll` does to a gun with malfunction number `number`: "" when it
    fired; otherwise it did not fire, and that was a dud for a revolver, a break-action or a
    bolt-action gun, a jam for a lever-action (p. 98). The book names no other mechanism; an
    automatic, pump or unknown action jams here too (the project's reading)."""
    if roll < number:
        return ""
    return "dud" if mechanism in DUD else "jam"


def jam_cleared(rounds_spent: int, needed: int, roll_passed: bool) -> bool:
    """Whether a jam is cleared this round: `needed` (1D6) rounds of work have been spent, this one
    included, and this round's Mechanical Repair or Firearms roll passed. One try a round until it
    works (p. 98)."""
    return rounds_spent >= max(1, needed) and roll_passed


# -- ammunition -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Loaded:
    loaded: int
    spare: int | None  # None: what is carried is not known (the keeper fills it)
    rounds: int  # combat rounds the reload took


def fire(loaded: int, shots: int = 1) -> tuple[int, int]:
    """Rounds fired and rounds left when `shots` are fired from a gun holding `loaded`."""
    fired = max(0, min(loaded, shots))
    return fired, loaded - fired


def reload(loaded: int, capacity: int, spare: int | None, *, loading: Loading = "rounds", rounds: int = 1) -> Loaded:
    """A reload of `rounds` combat rounds. Loose cartridges: two a round; a magazine: full in one
    round; a machine-gun belt: full after two rounds (p. 96). Never more than `capacity` nor than the
    `spare` rounds carried; with `spare` None the carried rounds are not known and only the capacity
    limits it."""
    room = max(0, capacity - loaded)
    if loading == "magazine":
        put = room if rounds >= 1 else 0
    elif loading == "belt":
        put = room if rounds >= BELT_ROUNDS else 0
    else:
        put = min(room, ROUNDS_PER_RELOAD * max(0, rounds))
    if spare is not None:
        put = min(put, spare)
    return Loaded(loaded + put, None if spare is None else spare - put, rounds)


def load_one_and_fire(loaded: int, spare: int | None) -> Loaded | None:
    """Loading one round and firing it in the same round (at a penalty die, `Shot.loaded_one`,
    p. 96): the gun then holds one more, or None when there is nothing to load."""
    if spare is not None and spare <= 0:
        return None
    return Loaded(loaded + 1, None if spare is None else spare - 1, 1)


def readied_bonus(*, firing: bool, loaded: int | None, jammed: bool) -> int:
    """The +50 to DEX a character gets in the round's order for a readied firearm it means to fire
    this round (p. 95): only a loaded gun that is not jammed. `loaded` None: not known yet, counted
    as loaded (the gun is filled when it is first used)."""
    return 50 if firing and not jammed and (loaded is None or loaded > 0) else 0
