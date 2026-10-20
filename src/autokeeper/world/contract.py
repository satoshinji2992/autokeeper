"""A hard rule every writer keeps, whoever it is: the module stays solvable.

For each conclusion, the investigators either hold a clue for it already or can still reach one
(module/simulate.reach, run on the world as it stands: where people are now, who is gone, which
flags have come about, what the director has added). A change that would leave a conclusion
with no way at all does not land, whether an important NPC decided it, an arrangement came due
or the director planned it: the NPC thinks again, the director is told what it would cut and may
add another way in the same plan. The same playthrough the compiler runs (module/simulate.py),
from where the game is instead of from the start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..module.graph import supporting_clues
from ..module.simulate import reach

if TYPE_CHECKING:
    from .store import World

GONE = ("死亡", "失踪")


def ways(w: World) -> dict[str, bool]:
    """For each conclusion: whether the investigators hold a clue for it or can still get one."""
    at = {e.id: e.at for e in w.rec.entities.values() if e.kind == "npc"}
    gone = frozenset(e.id for e in w.rec.entities.values() if e.kind == "npc" and e.status in GONE)
    r = reach(w.m, at=at, without_npcs=gone, flags=frozenset(w.st.flags))
    found = set(w.st.clues)
    return {k: any(c in found or c in r.clues for c in clues) for k, clues in supporting_clues(w.m).items()}


def cut(before: World, after: World) -> list[str]:
    """Conclusions that had a way in `before` and have none in `after`, in words."""
    had, now = ways(before), ways(after)
    return [f"这样一来结论 {k} 就再也拿不到线索了" for k, ok in had.items() if ok and not now.get(k, False)]
