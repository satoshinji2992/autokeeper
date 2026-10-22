"""A melee exchange as the Keeper Rulebook settles it (ch. 6, pp. 86–91), without dice or story.

Everything here is a pure function of numbers and levels already rolled: the encounter engine
throws the dice through its journal (engine/encounter), asks these what they mean, and writes the
result as effects. Nothing here keeps or changes state.

- Who wins (`melee_outcome`, pp. 86–87). Against a dodge the attacker needs a success better than
  the dodger's: a tie goes to the dodger. Against fighting back the attacker wins a tie of two
  successes, and the defender who rolls a better success hurts the attacker instead. When both fail
  nobody is hurt, whatever their skills. An attack nobody answers (surprised, held, out cold, or
  answered with nothing) hits on anything but a fumble (p. 91).
- How hard (`damage_mode`, `damage_plan`, p. 87). Only the attacker on its own turn can do extreme
  damage: an extreme success with a blunt weapon is the most the weapon and the damage bonus can do;
  with an impaling one, that and the weapon's damage rolled once more. A defender who wins by
  fighting back does normal damage, however well it rolled (also the weapon table's notes, p. 375).
- Manoeuvres (p. 89): the same contest, no damage; a penalty die per point of build the target has
  over the attacker, two at most; three or more and it cannot work.
- Outnumbered (p. 91): once a defender has dodged or fought back as many times this round as it
  makes attacks, every further melee attack on it gets a bonus die. Never for a shot or a throw.
- Throws and missiles (p. 91): a thrown weapon can be dodged; a missile (a bow) is met by diving for
  cover as a shot is; neither can be fought back unless the defender is within DEX/5 feet; a throw
  or a strength-drawn bow adds half the damage bonus (written 半DB in the weapon's damage).
- Surprise (pp. 90–91): Listen or Spot Hidden against the attacker's Stealth (`opposed_difficulty`)
  decides whether the target is ready; an unready target cannot answer, and the keeper either lets
  the blow land unless it fumbles or gives a bonus die. A ranged attack is always rolled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .bcdice import Level
from .dice_expr import DamageExpr

Reaction = Literal["dodge", "fight_back", "maneuver", "cover", "none"]
Winner = Literal["attacker", "defender", "none"]
Mode = Literal["none", "normal", "extreme"]
AttackKind = Literal["melee", "thrown", "missile", "firearm"]

MAX_DICE = 2  # bonus or penalty dice on one roll (p. 78)


@dataclass(frozen=True)
class MeleeOutcome:
    """Who won the exchange and what the winner's blow does: `mode` "none" when nobody is hurt (or a
    manoeuvre landed: `effect` True), "normal" or "extreme" damage to the loser."""

    winner: Winner
    mode: Mode = "none"
    effect: bool = False  # the winner's manoeuvre takes effect instead of damage

    @property
    def hit(self) -> bool:
        return self.winner == "attacker"


def melee_outcome(att: Level, dfn: Level | None, reaction: Reaction, *, maneuver: bool = False,
                  own_turn: bool = True) -> MeleeOutcome:
    """The result of a melee attack rolled at `att` and answered by `reaction` rolled at `dfn`.

    `reaction` "none" (or `dfn` None) is an attack nobody answers: it lands unless it fumbles (p. 91).
    "dodge": the attacker needs a success better than the dodge (a tie is the dodger's, p. 87).
    "fight_back": a tie of successes is the attacker's; a better success hurts the attacker instead.
    "maneuver": the defender fights back with a manoeuvre of its own, as fighting back but its effect
    instead of damage (p. 89). Both failing is nobody's (pp. 86–87). `maneuver`: the attacker's is a
    manoeuvre and does no damage. `own_turn`: the attack is made on the attacker's own DEX slot, the
    only time an extreme success does extreme damage (p. 87)."""
    if reaction == "cover":
        raise ValueError("diving for cover answers a shot, not a melee attack (p. 95)")
    if reaction == "none" or dfn is None:
        winner: Winner = "attacker" if att != Level.FUMBLE else "none"
    elif reaction == "dodge":
        winner = "attacker" if att.ok and att > dfn else "none"
    elif att.ok and att >= dfn:  # fighting back: the attacker wins a tie of two successes
        winner = "attacker"
    elif dfn.ok and dfn > att:
        winner = "defender"
    else:
        winner = "none"
    if winner == "none":
        return MeleeOutcome("none")
    if winner == "attacker":
        if maneuver:
            return MeleeOutcome("attacker", effect=True)
        if not att.ok:  # unanswered, it lands on anything but a fumble (p. 91): its ordinary damage
            return MeleeOutcome("attacker", "normal")
        return MeleeOutcome("attacker", damage_mode(att, own_turn=own_turn))
    if reaction == "maneuver":
        return MeleeOutcome("defender", effect=True)
    return MeleeOutcome("defender", "normal")  # fighting back never does extreme damage (p. 87)


def damage_mode(level: Level, *, own_turn: bool = True, extreme_range: bool = False) -> Mode:
    """The damage a successful attack rolled at `level` does: extreme at an extreme success on the
    attacker's own turn (p. 87); at a gun's extreme range only a critical (p. 95)."""
    if not level.ok:
        return "none"
    if not own_turn:
        return "normal"
    needed = Level.CRITICAL if extreme_range else Level.EXTREME
    return "extreme" if level >= needed else "normal"


# -- damage ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class DamagePlan:
    """What a blow does before it is rolled: `fixed` points plus the BCDice command `roll` ("" when
    nothing is rolled). `text` tells how, for the keeper's account: "1D4+1D4", "最大 8 + 1D4"."""

    fixed: int
    roll: str
    text: str

    def total(self, dice) -> int:
        """Rolled through `dice` (BCDice, or a test's FakeDice), never below 0."""
        return max(0, self.fixed + (int(dice.total(self.roll)) if self.roll else 0))


def damage_plan(expr: DamageExpr | str, db: str = "0", mode: Mode = "normal", *, impale: bool = False,
                band: int = 0) -> DamagePlan:
    """The damage of a blow with weapon damage `expr` ("1D4+DB", a shotgun's "4D6/2D6/1D6" at `band`)
    and damage bonus `db`. "extreme" with a blunt weapon: the weapon and the bonus at their maximum;
    with an impaling one (`impale`), that plus the weapon's own damage rolled once more (p. 87).
    ValueError (a Chinese reason) when `expr` is no damage expression."""
    parsed = expr if isinstance(expr, DamageExpr) else DamageExpr.parse(expr)
    if mode == "none" or parsed.none:
        return DamagePlan(0, "", "0")
    if mode == "normal":
        command = parsed.rollable(db, band)
        if not command:
            fixed = parsed.fixed(db, band) or 0
            return DamagePlan(fixed, "", str(fixed))
        return DamagePlan(0, command, command)
    top = parsed.maximum(db, band)
    if not impale:
        return DamagePlan(top, "", f"最大 {top}")
    more = parsed.weapon(band)
    command = more.rollable("0")
    if not command:
        extra = more.fixed("0") or 0
        return DamagePlan(top + extra, "", f"贯穿：最大 {top} + {extra}")
    return DamagePlan(top, command, f"贯穿：最大 {top} + {command}")


# -- manoeuvres -----------------------------------------------------------------------------------
def maneuver_feasible(att_build: int, dfn_build: int) -> tuple[bool, int]:
    """Whether a manoeuvre against a target of `dfn_build` can work at all, and the penalty dice it
    takes: one per point of build the target has over the attacker, at most two; three or more
    over and it cannot (p. 89)."""
    over = dfn_build - att_build
    if over >= 3:
        return False, 0
    return True, max(0, over)


# -- answering attacks ----------------------------------------------------------------------------
def outnumbered_bonus(responses: int, allowance: int = 1, *, ranged: bool = False, immune: bool = False) -> int:
    """The bonus die a melee attack gets on a defender that has already dodged or fought back
    `responses` times this round: one once it has answered as many attacks as it makes in a round
    (`allowance`: 1, or a multi-attack creature's attacks). Never for ranged attacks; never against
    a being the rules say is never outnumbered (`immune`, ch. 14) (p. 91)."""
    if ranged or immune:
        return 0
    return 1 if responses >= max(1, allowance) else 0


def reactions_against(kind: AttackKind, *, distance_ft: float | None = None, dex: int = 0,
                      ready: bool = True) -> tuple[Reaction, ...]:
    """What a defender may answer an attack of `kind` with. A melee blow: dodge, fight back (or
    fight back with a manoeuvre). A thrown weapon: dodge (p. 91). A missile (a bow) or a shot: dive
    for cover (pp. 91, 95). A throw or a missile can be fought back only within DEX/5 feet (p. 91);
    a shot never can be. An unready (surprised) defender answers nothing (p. 90)."""
    if not ready:
        return ("none",)
    close = distance_ft is not None and distance_ft <= dex / 5
    if kind == "melee":
        return ("dodge", "fight_back", "maneuver", "none")
    if kind == "thrown":
        return ("dodge", "fight_back", "none") if close else ("dodge", "none")
    if kind == "missile":
        return ("cover", "fight_back", "none") if close else ("cover", "none")
    return ("cover", "none")


def keeper_default(withdrawing: bool = False) -> Reaction:
    """How an NPC or a monster answers a melee attack unless the keeper says otherwise: it fights
    back, and dodges only when it means to withdraw (p. 87)."""
    return "dodge" if withdrawing else "fight_back"


def opposed_difficulty(value: int) -> Level:
    """The difficulty a roll against someone's skill or characteristic `value` is made at: regular
    under 50, hard from 50, extreme from 90 (p. 72). Surprise sets Listen or Spot Hidden against the
    attacker's Stealth this way (p. 90), hiding in a chase Stealth against the pursuer's Spot (p. 119)."""
    if value >= 90:
        return Level.EXTREME
    if value >= 50:
        return Level.HARD
    return Level.REGULAR


SurpriseRule = Literal["auto", "bonus"]


def surprise_attack(level: Level, *, ranged: bool, rule: SurpriseRule = "auto") -> bool:
    """Whether an attack on a target that did not see it coming lands. The keeper either lets it
    land unless it fumbles ("auto") or gives the attacker a bonus die and asks for a success
    ("bonus", the die already in `level`); a ranged attack (a shot, a throw) is always rolled
    and needs a success (pp. 90–91)."""
    if ranged or rule == "bonus":
        return level.ok
    return level != Level.FUMBLE


def surprise_bonus(*, ranged: bool, rule: SurpriseRule = "auto") -> int:
    """The bonus die the attack roll on an unready target takes: one under the keeper's "bonus" rule
    (p. 90); none otherwise (an "auto" blow lands unless it fumbles; a shot is rolled as ever)."""
    return 1 if rule == "bonus" and not ranged else 0


def disadvantage_bonus(*, prone: bool = False, held: bool = False) -> int:
    """The bonus die a melee attack takes on someone down or held. Held: a manoeuvre's lasting
    disadvantage, allies get a bonus die against them (p. 89). Prone: the optional rule, kicking
    someone on the ground is easier (p. 107). One die however many apply."""
    return 1 if prone or held else 0


def clamp(dice: int) -> int:
    """Bonus (+) or penalty (−) dice on one roll, after they cancel: two at most either way (p. 78)."""
    return max(-MAX_DICE, min(MAX_DICE, dice))
