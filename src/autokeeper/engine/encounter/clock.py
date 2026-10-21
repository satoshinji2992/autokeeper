"""An encounter's own time: seconds a round, whole minutes handed to the world clock.

A round takes `pace` seconds of story (the keeper sets it: a scuffle 3–6, a gunfight 6–10, a chase
10–30). Rounds add up in `elapsed`; whole minutes of it are `committed`, and the game clock takes
`committed - applied` at each round's end, so a deadline or a reinforcement scheduled for later
still comes in the middle of a fight. Nothing in a fight adds minutes per action.
"""

from __future__ import annotations

from .model import EncounterClock

DEFAULT_PACE = 6  # seconds of a round the keeper has not set yet: the book's "a few seconds" (p. 86)


def tick(clock: EncounterClock) -> EncounterClock:
    """The clock after one more round."""
    pace = clock.pace if clock.pace is not None else DEFAULT_PACE
    elapsed = clock.elapsed + pace
    return clock.model_copy(update={"elapsed": elapsed, "committed": elapsed // 60})


def owed(clock: EncounterClock) -> int:
    """Minutes the game clock is still owed."""
    return max(0, clock.committed - clock.applied)


def paid(clock: EncounterClock) -> EncounterClock:
    return clock.model_copy(update={"applied": clock.committed})


def settle_minutes(clock: EncounterClock) -> int:
    """At the end: what is left of a started minute counts as one (the old engine's ceiling)."""
    rest = clock.elapsed - clock.committed * 60
    return owed(clock) + (1 if rest > 0 else 0)


__all__ = ["DEFAULT_PACE", "owed", "paid", "settle_minutes", "tick"]
