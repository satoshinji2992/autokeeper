"""One JSON line per model call, in data/logs/traces/<date>.jsonl.

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
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from .. import __version__, paths

if TYPE_CHECKING:
    from .models import Model, Reply

taps: list[Callable[[Model, Reply, dict | None], None]] = []  # told of every answered call (replay.Recorder)
_observer: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar("call_observer", default=None)
_call: ContextVar[str] = ContextVar("observed_call", default="")
_context: ContextVar[dict | None] = ContextVar("input_context", default=None)
_context_owner: ContextVar[object | None] = ContextVar("input_context_owner", default=None)


@contextmanager
def input_context(parts: dict, *, owner: object | None = None):
    token = _context.set(dict(parts))
    owner_token = _context_owner.set(owner)
    try:
        yield
    finally:
        _context.reset(token)
        _context_owner.reset(owner_token)


def context_for(owner: object) -> dict:
    """A nested model may not borrow its caller's session breakdown."""
    current = _context_owner.get()
    return dict(_context.get() or {}) if current is None or current is owner else {}


@contextmanager
def capture(observer: Callable[[dict[str, Any]], None]):
    """Observe this task's calls without changing disk tracing or observing another game."""
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


def _notify(row: dict[str, Any]) -> None:
    if observer := _observer.get():
        try:
            observer(row)
        except Exception:  # observing must never change the operation being observed
            pass


def begin(model: Model, messages: list[dict], schema: dict | None, images: list[bytes] | None):
    token = _call.set(uuid.uuid4().hex)
    progress("started", agent=model.label, model=model.spec.model, backend=model.spec.backend,
             system=model.system, input=[dict(m) for m in messages], schema=schema,
             context=_context.get(),
             image_bytes=[len(b) for b in images or []])
    return token


def progress(phase: str, **data: Any) -> None:
    _notify({"phase": phase, "call_id": _call.get(),
             "at": dt.datetime.now().isoformat(timespec="milliseconds"), **data})


def finish(token) -> None:
    _call.reset(token)


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
    row: dict[str, Any] = {
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "run": RUN,
        "version": __version__,
        "agent": model.label,
        "backend": model.spec.backend,
        "model": model.spec.model,
        "effort": model.spec.effort,
        "mode": "text" if schema is None else model.mode,
        "messages": len(messages),
        "chars_in": (_context.get() or {}).get("total_chars", len(model.system) + sum(len(m["content"]) for m in messages)),
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
    if parts := _context.get():
        row["context"] = dict(parts)
    if error:
        row["error"] = error[:500]
    _notify({**row, "phase": "complete", "call_id": _call.get(), "system": model.system,
             "input": [dict(m) for m in messages], "output": reply.text,
             "thinking": reply.thinking, "data": reply.data, "schema": schema})
    if level == "off":
        return
    if level == "full":
        row.update(system=model.system, input=messages, output=reply.text, thinking=reply.thinking)
    try:
        folder = paths.traces()
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{dt.date.today().isoformat()}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:  # tracing never breaks a game
        pass


# -- reading traces back (`autokeeper trace`) ----------------------------------------------
def read(day: str | None = None) -> list[dict[str, Any]]:
    """Rows from one day's file (default: the latest), or every file with day="all"."""
    files = sorted(paths.traces().glob("*.jsonl"))
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
            "fresh": sum(max(0, r.get("input_tokens", 0) - r.get("cache_read", 0)) for r in rs),
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
