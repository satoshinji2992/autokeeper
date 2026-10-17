"""One JSON line per model call, in saves/traces/<date>.jsonl.

Metadata by default: who called which model, how (text / native / json), how many
characters went in, tokens, cache hits, cost, latency, repair rounds, errors.
AUTOKEEPER_TRACE=full also keeps the messages, the output and the reasoning text;
AUTOKEEPER_TRACE=off turns tracing off. Every row carries a run id (the process, or the
autoplay slot), so `autokeeper trace --run last` can sum up one game.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .. import config

if TYPE_CHECKING:
    from .models import Model, Reply

taps: list[Callable[[Model, Reply, dict | None], None]] = []  # told of every answered call (replay.Recorder)

RUN = os.environ.get("AUTOKEEPER_RUN") or f"{dt.datetime.now():%m%d-%H%M%S}-{os.getpid()}"


def label_run(name: str) -> None:
    """Name this process's run, e.g. after the autoplay slot."""
    global RUN
    RUN = name


def emit(model: Model, messages: list[dict], reply: Reply, *, schema: dict | None, error: str = "") -> None:
    if not error:
        for tap in taps:
            tap(model, reply, schema)
    level = os.environ.get("AUTOKEEPER_TRACE", "meta")
    if level == "off":
        return
    row: dict[str, Any] = {
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "run": RUN,
        "agent": model.label,
        "backend": model.spec.backend,
        "model": model.spec.model,
        "effort": model.spec.effort,
        "mode": "text" if schema is None else model.mode,
        "messages": len(messages),
        "chars_in": len(model.system) + sum(len(m["content"]) for m in messages),
        "chars_out": len(reply.text),
        "thinking_chars": len(reply.thinking),
        **reply.usage,
        "cost_usd": round(reply.cost_usd, 6),
        "seconds": round(reply.seconds, 2),
        "repairs": reply.repairs,
        "retries": reply.retries,
    }
    if model.standby_for:
        row["fallback_for"] = model.standby_for
    if error:
        row["error"] = error[:500]
    if level == "full":
        row.update(system=model.system, input=messages, output=reply.text, thinking=reply.thinking)
    try:
        folder = config.SAVES / "traces"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{dt.date.today().isoformat()}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:  # tracing never breaks a game
        pass


# -- reading traces back (`autokeeper trace`) ----------------------------------------------
def read(day: str | None = None) -> list[dict[str, Any]]:
    """Rows from one day's file (default: the latest), or every file with day="all"."""
    files = sorted((config.SAVES / "traces").glob("*.jsonl"))
    if day != "all":
        files = [f for f in files if f.stem == day] if day else files[-1:]
    out = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:  # a line cut short by a crash
                continue
    return out


def runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per run, in the order they started."""
    seen: dict[str, dict[str, Any]] = {}
    for r in rows:
        run = seen.setdefault(r.get("run", "?"), {"run": r.get("run", "?"), "start": r["at"], "rows": []})
        run["rows"].append(r)
        run["end"] = r["at"]
    return [{**summarize(v["rows"], by=None)[0], "run": k, "start": v["start"], "end": v["end"]} for k, v in seen.items()]


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarize(rows: list[dict[str, Any]], by: str | None = "agent") -> list[dict[str, Any]]:
    """Per agent (or model, backend, run; None: all together): calls, tokens, cache hit
    rate, latency, repairs, retries, fallbacks, errors and cost. Busiest first."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r.get(by) or "?") if by else "合计", []).append(r)
    out = []
    for key, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        tokens = sum(r.get("input_tokens", 0) for r in rs)
        cached = sum(r.get("cache_read", 0) for r in rs)
        seconds = [r.get("seconds", 0.0) for r in rs]
        out.append({
            "key": key,
            "calls": len(rs),
            "input": tokens,
            "cached": cached,
            "cache_rate": cached / tokens if tokens else 0.0,
            "output": sum(r.get("output_tokens", 0) for r in rs),
            "thinking": sum(r.get("thinking_chars", 0) for r in rs),
            "p50": _quantile(seconds, 0.5),
            "p95": _quantile(seconds, 0.95),
            "seconds": sum(seconds),
            "repairs": sum(r.get("repairs", 0) for r in rs),
            "retries": sum(r.get("retries", 0) for r in rs),
            "fallbacks": sum(1 for r in rs if r.get("fallback_for")),
            "errors": sum(1 for r in rs if r.get("error")),
            "cost": sum(r.get("cost_usd", 0.0) for r in rs),
        })
    return out
