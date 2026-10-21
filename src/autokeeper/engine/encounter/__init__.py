"""Fights and chases (0.0.4): one encounter, in the save, changed only by world ops.

- model.py: what the save keeps (EncounterState) and the changes that move it (EncounterChange).
- plan.py: what a model writes about one (EncounterStart, EncounterTurnPlan and their parts).

The effects on people and things are engine/effects.py's; the dice text rules/dice_expr.py's.
"""

from .model import EncounterChange, EncounterState, EncounterSummary, apply
from .plan import DeclaredAction, EncounterStart, EncounterTurnPlan, NpcArrangement

__all__ = [
    "DeclaredAction",
    "EncounterChange",
    "EncounterStart",
    "EncounterState",
    "EncounterSummary",
    "EncounterTurnPlan",
    "NpcArrangement",
    "apply",
]
