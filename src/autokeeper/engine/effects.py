"""What happens to people and things, already rolled: the one way damage and its kin reach the game.

A wound used to be applied wherever it was rolled: by a fight to its own copies of the fighters,
by an event to the sheet, by a plugin to whatever it was given. Some of them never reached the
world (an NPC came back standing in the next fight), and some reached it twice. These models are
the results instead. Whoever rolls the dice (an encounter's unit, an event, a failed check, a
plugin, the MCP keeper) turns what they rolled into a list of `Effect`s; applying them is then
deterministic, in one op (world/ops.EncounterStep inside an encounter, world/ops.Effects outside
one), so they land together or not at all, and the op's id makes a retry land nothing twice.

This module holds the models only. Rolling (`harm`) and applying them (`commit`, the settling in
world/store.py) are work package B's; the rules they apply are rules/wounds.py's.

Every amount is absolute where it can be: `Ammo` says what is in the gun now, not how many were
fired, so applying it again changes nothing. A `Wound` carries the id of the injury it is; a second
wound with the same id is refused, so a plugin and the engine cannot both deduct the same blow.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..rules.bcdice import Level

Means = Literal["blow", "unarmed", "firearm", "fall", "fire", "asphyxia", "poison", "other"]


class Wound(BaseModel):
    """`amount` HP off `target` (an investigator, NPC or creature id), before armour."""

    kind: Literal["wound"] = "wound"
    target: str
    amount: int = Field(ge=0)
    means: Means = "other"  # unarmed counts towards a knockout, firearm is halved for some creatures
    con: Level | None = None  # the CON roll a major wound called for, already thrown; None when none was
    injury: str  # stable id of this injury: no second wound with it lands; First Aid is once per injury


class Heal(BaseModel):
    kind: Literal["heal"] = "heal"
    target: str
    amount: int = Field(ge=0)
    means: Literal["first_aid", "medicine", "natural", "regen", "other"]
    injury: str = ""  # the injury First Aid was given for (InjuryState.aided)


class Wake(BaseModel):
    """Out cold no longer (First Aid, or the keeper's ruling)."""

    kind: Literal["wake"] = "wake"
    target: str


class Stabilize(BaseModel):
    """Dying no longer gets worse until Medicine can be used."""

    kind: Literal["stabilize"] = "stabilize"
    target: str


class Die(BaseModel):
    kind: Literal["die"] = "die"
    target: str
    why: str  # 濒死时体质检定失败、一击超过生命上限……


class Ammo(BaseModel):
    """What is in the gun `item` and carried for it now, absolute (WeaponState)."""

    kind: Literal["ammo"] = "ammo"
    item: str
    loaded: int = Field(ge=0)
    spare: int | None = None  # None: unknown, unchanged


class Jam(BaseModel):
    kind: Literal["jam"] = "jam"
    item: str
    jammed: bool


class Ready(BaseModel):
    """`who` has `item` in hand (equipment "held"), as world/ops.Equip makes it."""

    kind: Literal["ready"] = "ready"
    who: str
    item: str


class Drop(BaseModel):
    """`item` leaves its holder for `to`: a place (dropped, knocked away) or someone (taken). The
    inventory follows, as world/ops.Transfer moves it. A creature's own claws are never an item."""

    kind: Literal["drop"] = "drop"
    item: str
    to: str


class Relocate(BaseModel):
    """`who` (an NPC or a creature) is now at `place`; "players" moves the party, which the engine
    does through its arrival, since arriving fires events."""

    kind: Literal["relocate"] = "relocate"
    who: str
    place: str


class Mark(BaseModel):
    """A skill an investigator used successfully ticked for improvement."""

    kind: Literal["mark"] = "mark"
    investigator: str
    skill: str


Effect = Annotated[
    Wound | Heal | Wake | Stabilize | Die | Ammo | Jam | Ready | Drop | Relocate | Mark,
    Field(discriminator="kind"),
]
