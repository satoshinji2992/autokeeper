"""The player-facing part of a pending check, shared by local and network delivery."""

from __future__ import annotations

from typing import Any

PUSH_SHOWN = ("skill", "target", "need", "difficulty", "bonus", "can_push", "luck", "left", "risk", "value", "action")


def public_push(pending: dict[str, Any]) -> dict[str, Any]:
    """Expose the offered check and its stated risk, never its success or failure contracts."""
    return {key: value for key, value in pending.items() if key in PUSH_SHOWN}
