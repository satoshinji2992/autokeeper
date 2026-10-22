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


def shared(entry: Any) -> bool:
    """Suitable for a common recap: public and delivered without personal differences."""
    if not public(entry):
        return False
    presented = entry.get("presented", {}) if isinstance(entry, Mapping) else getattr(entry, "presented", {})
    return all(all(value == (entry.get(key) if isinstance(entry, Mapping) else getattr(entry, key, None))
                   for key, value in view.items()) for view in presented.values())


def log(state: GameState, viewer: str | None = None, *, keeper: bool = False) -> list[LogEntry]:
    rows = [e for e in [*state.private_history, *state.log] if visible(e, viewer, keeper=keeper)]
    if keeper:
        return rows
    if viewer is None:
        rows = [e for e in rows if shared(e)]
    out = []
    for entry in rows:
        if not entry.presented:
            out.append(entry)
            continue
        # A differently observed public event is part of each person's own memory. It
        # cannot later be silently turned back into the keeper's objective wording.
        scope = {"audience": [viewer]} if viewer is not None and public(entry) and not shared(entry) else {}
        out.append(entry.model_copy(update={**entry.presented.get(viewer, {}), **scope, "presented": {}}))
    return out


def personal(state: GameState, viewer: str, n: int = 8) -> list[LogEntry]:
    return [e for e in log(state, viewer) if not public(e)][-n:]


def location(state: GameState, who: str) -> str:
    return state.apart.get(who, state.location)


def listeners(state: GameState, speaker: str) -> list[str]:
    where = location(state, speaker)
    return [i.id for i in state.investigators if location(state, i.id) == where
            and not {"死亡", "昏迷"}.intersection(i.conditions)]
