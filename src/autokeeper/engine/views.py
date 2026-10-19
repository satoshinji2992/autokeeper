"""Which picture of a place the scene shows (GameState.view).

A place may have more than one picture (module.schema.View): inside, a corner of it, the
same street by night. On arrival the scene shows the place's own picture, or the one kept for
this hour; it turns to a view when the party looks at what that view shows (the action's
target names one of its features), and back when they turn to something else. An event with
a picture of its own (Event.art) holds the scene for its moment, until their next action.
"""

from __future__ import annotations

from ..module.schema import Location, Module
from .state import GameState, time_of_day

NIGHT = ("夜里",)  # time_of_day's hours a night view is for
AIMED = ("investigate", "use", "talk", "perform", "attack")  # actions that turn the scene to what they are aimed at


def shown(m: Module, key: str) -> bool:
    """A key with something to show: a picture, or hand-drawn art."""
    return bool(key) and (key in m.pictures or key in m.art)


def default(m: Module, loc: Location, minutes: int) -> str:
    """The place's picture for this hour: a view kept for it (by day, by night), else its own."""
    hour = "night" if time_of_day(minutes) in NIGHT else "day"
    kept = next((v.key for v in loc.views if v.time == hour and not v.features and shown(m, v.key)), "")
    return kept or loc.art or ""


def focus(m: Module, loc: Location, target: str) -> str:
    """The view of what an action is aimed at: one whose feature they name. Only that way round:
    looking at "陵墓" is not looking at "陵墓内部", whose picture shows what the door still hides."""
    target = target.strip()
    if not target:
        return ""
    return next((v.key for v in loc.views if shown(m, v.key) and any(f and f in target for f in v.features)), "")


def after(m: Module, st: GameState, kind: str, target: str) -> str:
    """The picture the scene turns to for an action here; "" to leave it as it is."""
    loc = m.location(st.location)
    if key := focus(m, loc, target):
        return key
    if st.view_kind == "moment" or kind in AIMED:  # a moment is over; a close look turns to something else
        return default(m, loc, st.minutes)
    return ""
