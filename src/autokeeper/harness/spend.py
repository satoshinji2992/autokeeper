"""What metered API calls cost, and a hard cap on it.

Claude Code reports its own cost. For API backends the harness estimates from token
usage at peak list prices, so the estimate errs high; `budget_usd` in the config stops
further calls once the running total crosses it. The ledger is data/spend.json.

Several processes may spend at once (an autoplay next to the TUI), so each update holds a
file lock around read-modify-write and lands by atomic rename: a reader never sees half a
file, and a broken ledger is set aside instead of being silently restarted from zero.
"""

from __future__ import annotations

import functools
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from filelock import FileLock

from .. import config, paths

# USD per million tokens: (input, cached input, output). DeepSeek peak rates (off-peak is half).
PRICES: dict[str, tuple[float, float, float]] = {
    "deepseek-flash": (0.30, 0.006, 1.20),
    "deepseek-v4-flash": (0.30, 0.006, 1.20),
    "deepseek-v4-pro": (1.32, 0.044, 3.96),
}

_lock = threading.Lock()
_run: ContextVar[tuple[float | None, Path] | None] = ContextVar("spend_run", default=None)


class BudgetExceeded(RuntimeError):
    pass


def _file():
    return _run.get()[1] if _run.get() is not None else paths.spend()


@contextmanager
def scoped(cap: float | None, ledger_file: Path) -> Iterator[None]:
    """One evaluation's cap and ledger, inherited by its tasks and restored on exit.

    Processes joining the same evaluation use the same ledger_file; record() retains
    its file lock. Nothing is written to the user's settings or ordinary game ledger.
    """
    if cap is not None and cap < 0:
        raise ValueError("费用上限不能为负数")
    token = _run.set((cap, ledger_file))
    try:
        yield
    finally:
        _run.reset(token)


def ledger() -> dict[str, Any]:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"total_usd": 0.0, "models": {}}


@functools.cache
def _priced() -> dict[str, tuple[float, float, float]]:
    """The built-in prices and the config's own ([prices] "model" = [input, cached input, output],
    USD per million tokens), for an API the table does not know."""
    own = {str(k).split(":", 1)[-1]: tuple(float(x) for x in v) for k, v in (config.load().prices or {}).items()
           if isinstance(v, (list, tuple)) and len(v) == 3}
    return {**PRICES, **own}  # type: ignore[dict-item]


def price(model: str) -> tuple[float, float, float] | None:
    return _priced().get(model.split(":", 1)[-1])


def billed(model: str) -> bool:
    """Whether calls on the model cost money: a priced API, or Claude on an API key (what Claude
    Code reports on a subscription is what it would have cost, not a bill)."""
    return price(model) is not None or (model.split(":", 1)[-1].startswith("claude-") and bool(os.environ.get("ANTHROPIC_API_KEY")))


def estimate(model: str, usage: dict) -> float:
    p = price(model)
    if not p:
        return 0.0
    cached = usage.get("cache_read", 0)
    fresh = max(0, usage.get("input_tokens", 0) - cached)
    return (fresh * p[0] + cached * p[1] + usage.get("output_tokens", 0) * p[2]) / 1_000_000


def budget() -> float | None:
    # Settings may be changed while a LAN room is paused at a limit. Do not pin the
    # first cap for the process's lifetime, or a corrected setting still cannot resume.
    return _run.get()[0] if _run.get() is not None else config.load().budget_usd


def guard(model: str) -> None:
    """Refuse a metered call once the ledger is past the cap."""
    if (cap := budget()) is None or not billed(model):
        return
    spent = ledger()["total_usd"]
    if spent >= cap:
        raise BudgetExceeded(f"API 花费估计已达 ${spent:.3f}，超过预算 ${cap:g}（账本：{_file()}）")


def record(model: str, usage: dict, cost: float | None = None) -> float:
    """Into the ledger: the estimate from the usage, or what the backend reported it cost."""
    cost = estimate(model, usage) if cost is None else cost
    if not cost:
        return 0.0
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, FileLock(f"{path}.lock", timeout=30):
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"total_usd": 0.0, "models": {}}
        except json.JSONDecodeError:  # keep the evidence; never quietly restart the count
            os.replace(path, path.with_suffix(".json.bad"))
            data = {"total_usd": 0.0, "models": {}}
        row = data["models"].setdefault(model, {"calls": 0, "input": 0, "cache_read": 0, "output": 0, "usd": 0.0})
        row["calls"] += 1
        row["input"] += usage.get("input_tokens", 0)
        row["cache_read"] += usage.get("cache_read", 0)
        row["output"] += usage.get("output_tokens", 0)
        row["usd"] = round(row["usd"] + cost, 6)
        data["total_usd"] = round(data["total_usd"] + cost, 6)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    return cost
