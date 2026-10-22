"""Recipients of an observation: one world record, with explicit personal reading scopes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .state import GameState, LogEntry


def visible(entry: Any, viewer: str | None = None, *, keeper: bool = False) -> bool:
    recipients = entry.get("audience") if isinstance(entry, Mapping) else getattr(entry, "audience", None)
    return keeper or recipients is None or (viewer is not None and viewer in recipients)


def public(entry: Any) -> bool:
    return visible(entry)


def log(state: GameState, viewer: str | None = None, *, keeper: bool = False) -> list[LogEntry]:
    return [e for e in [*state.private_history, *state.log] if visible(e, viewer, keeper=keeper)]


def personal(state: GameState, viewer: str, n: int = 8) -> list[LogEntry]:
    return [e for e in log(state, viewer) if not public(e)][-n:]


def location(state: GameState, who: str) -> str:
    return state.apart.get(who, state.location)


def listeners(state: GameState, speaker: str) -> list[str]:
    where = location(state, speaker)
    return [i.id for i in state.investigators if location(state, i.id) == where
            and not {"死亡", "昏迷"}.intersection(i.conditions)]
