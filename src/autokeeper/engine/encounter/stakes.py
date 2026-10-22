"""What an encounter's outcome brings about: its stakes (StakeState), evaluated by the engine.

A stake is a condition and the story flags it sets (module/schema.Stake). The condition is the
world's grammar (world/agents.holds: flag, at, gone, owner, knows ...), `&` binding tighter than
`|`, plus the encounter's own atoms:

    down:谁         fell (out cold, dying, dead, or a creature down and coming back)
    dead:谁         died
    escaped:谁      got away (the encounter is over for them)
    caught:谁       was caught (held fast, or caught at the end of a chase)
    surrendered:谁  gave up
    ended:方式       the encounter ended so (won lost fled escaped caught parley surrender dispersed)

`谁` is an NPC's, a creature's (a creature form names its NPC) or an investigator's id, or
`players` for the investigators' side: all of them for down, dead, escaped and surrendered; any of
them for caught. The module or the keeper plan declares stakes; nobody is asked what the outcome
means once it comes. A stake fires once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...world.agents import condition_atoms
from ...world.agents import holds as world_holds
from . import profiles

if TYPE_CHECKING:
    from .model import EncounterState

ATOMS = ("down", "dead", "escaped", "caught", "surrendered", "ended")


def _who(game: Any, enc: EncounterState, rest: str) -> list[str]:
    if rest == "players":
        return [p.id for p in enc.participants if p.kind == "investigator"]
    return [profiles.combatant(game.world, rest)]


def _one(game: Any, enc: EncounterState, kind: str, pid: str) -> bool:
    from .combatant import view

    p = enc.get(pid)
    v = view(game.world, pid)
    body = v.body() if v is not None else None
    if kind == "down":
        return body is not None and (body.dead or body.dying or body.regenerating or "昏迷" in body.conditions)
    if kind == "dead":
        return body is not None and body.dead
    if kind == "escaped":
        return p is not None and p.presence == "escaped"
    if kind == "caught":
        return p is not None and (p.presence == "captured" or any(h.held == pid for h in enc.scene.holds))
    return p is not None and p.presence == "surrendered"


def atom(game: Any, enc: EncounterState, kind: str, rest: str, ended: str = "") -> bool:
    """One encounter atom; `ended` is how it ended ("" while it goes on)."""
    if kind == "ended":
        return bool(ended) and rest == ended
    who = _who(game, enc, rest)
    if not who:
        return False
    hits = [_one(game, enc, kind, x) for x in who]
    return any(hits) if kind == "caught" else all(hits)


def holds(game: Any, enc: EncounterState, when: str, ended: str = "") -> bool:
    """Whether `when` holds now; a condition that names what does not exist holds nowhere."""
    if not when.strip():
        return False
    for group in when.split("|"):
        ok = True
        for kind, rest, negate in condition_atoms(group):
            if kind in ATOMS:
                value = atom(game, enc, kind, rest, ended)
            else:
                value = world_holds(game.world, f"{kind}:{rest}")
            if value == negate:
                ok = False
                break
        if ok:
            return True
    return False


def due(game: Any, enc: EncounterState, ended: str = "") -> list[int]:
    """The stakes not fired yet that hold now, by index."""
    return [i for i, s in enumerate(enc.stakes) if not s.fired and holds(game, enc, s.when, ended)]


__all__ = ["ATOMS", "atom", "due", "holds"]
