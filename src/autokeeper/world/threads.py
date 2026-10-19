"""The tension ledger: open questions, foreshadowing, promises, conflicts and plans in motion,
and a hint for the director of which are ripe to develop or pay off."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .record import Thread

if TYPE_CHECKING:
    from .store import World

IDLE_TURNS = 8  # a thread untouched this long wants a step


def readiness(w: World, th: Thread) -> tuple[float, str]:
    """0..1, and a short hint: how much of its setup the players hold, how long it sat, how hot it is."""
    if th.status == "closed":
        return 0.0, "已收线"
    facts = [w.rec.facts[f] for f in th.facts if f in w.rec.facts]
    shown = sum(f.revealed is not None for f in facts) / len(facts) if facts else 0.0
    idle = max(0, w.st.turn - th.last_turn)
    promise = w.rec.promises.get(th.id.removeprefix("promise:")) if th.kind == "promise" else None
    overdue = bool(promise and promise.status == "active" and promise.due is not None and promise.due <= w.now)
    score = min(1.0, 0.5 * shown + min(idle, 10) / 20 + min(th.heat, 6) / 12 + (0.3 if overdue else 0))
    if overdue:
        hint = "约定到期了：让守约或失约的后果出现"
    elif facts and shown >= 0.75:
        hint = "铺垫大多已经露出来，可以收线或翻转"
    elif idle >= IDLE_TURNS:
        hint = f"{idle} 轮没有动静，可以往前推一步"
    elif th.heat >= 4:
        hint = "矛盾在升温，找机会爆发"
    else:
        hint = "还早，继续铺垫"
    return round(score, 2), hint


def ranked(w: World, n: int = 8) -> list[tuple[Thread, float, str]]:
    """Open threads, ripest first."""
    rows = [(th, *readiness(w, th)) for th in w.rec.threads.values() if th.status == "open"]
    return sorted(rows, key=lambda r: -r[1])[:n]
