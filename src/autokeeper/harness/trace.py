"""One JSON line per model call, in saves/traces/<date>.jsonl.

Metadata by default: who called which model, how (text / native / json), how many
characters went in, tokens, cache hits, cost, latency, repair rounds, errors.
AUTOKEEPER_TRACE=full also keeps the messages, the output and the reasoning text;
AUTOKEEPER_TRACE=off turns tracing off.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import TYPE_CHECKING, Any

from .. import config

if TYPE_CHECKING:
    from .models import Model, Reply


def emit(model: Model, messages: list[dict], reply: Reply, *, schema: dict | None, error: str = "") -> None:
    level = os.environ.get("AUTOKEEPER_TRACE", "meta")
    if level == "off":
        return
    row: dict[str, Any] = {
        "at": dt.datetime.now().isoformat(timespec="seconds"),
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
