"""Record a game once, replay it forever.

A cassette is a JSONL file: every model reply (by agent, in order), every die the rules
rolled, and the game's summary at the end. Replaying it needs no model, no key and no
network: each agent gets its recorded replies back in order, the dice come up the same,
and the whole turn pipeline (intent → arbiter → rules → NPCs → keeper → memory) runs for
real. If the engine still ends in the same state, nothing that matters has changed.

Replies are matched by agent and order, not by prompt, so rewording a prompt does not
break a cassette; making a different sequence of calls does, and that is the point.

    autokeeper autoplay DIR --record game.jsonl     # play with real models, keep the tape
    autokeeper autoplay DIR --replay game.jsonl     # same game, no models
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..rules.bcdice import Roll
from . import trace
from .models import Model, Msg, OnDelta, Reply, parse_json


class Recorder:
    """Writes a cassette while a game is played with real models and real dice."""

    def __init__(self, path: Path, meta: dict[str, Any] | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("w", encoding="utf-8")
        trace.taps.append(self.reply)
        if meta:  # how the game was set up (e.g. the improvisation level), so a replay matches it
            self._write({"kind": "meta", **meta})

    def _write(self, row: dict[str, Any]) -> None:
        self.file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.file.flush()

    def reply(self, model: Model, reply: Reply, schema: dict | None) -> None:
        self._write({"kind": "reply", "agent": model.label, "text": reply.text, "data": reply.data if schema else None})

    def dice(self, call: str, args: list, result: Any) -> None:
        self._write({"kind": "dice", "call": call, "args": args, "result": asdict(result) if isinstance(result, Roll) else result})

    def close(self, summary: dict[str, Any]) -> None:
        if self.reply in trace.taps:
            trace.taps.remove(self.reply)
        self._write({"kind": "summary", **summary})
        self.file.close()


class RecordingDice:
    """The real dice, with every result written to the cassette."""

    def __init__(self, inner: Any, recorder: Recorder):
        self.inner, self.recorder = inner, recorder

    def check(self, target: int, bonus: int = 0) -> Roll:
        out = self.inner.check(target, bonus)
        self.recorder.dice("check", [target, bonus], out)
        return out

    def roll(self, expr: str) -> Roll:
        out = self.inner.roll(expr)
        self.recorder.dice("roll", [expr], out)
        return out

    def total(self, expr: str) -> int:
        out = self.inner.total(expr)
        self.recorder.dice("total", [expr], out)
        return out

    def table(self, name: str) -> str:
        out = self.inner.table(name)
        self.recorder.dice("table", [name], out)
        return out


class CassetteError(RuntimeError):
    """The game asked for something the tape does not have: the engine has drifted."""


class Cassette:
    def __init__(self, path: Path):
        self.path = path
        self.replies: dict[str, deque[dict]] = defaultdict(deque)
        self.dice: deque[dict] = deque()
        self.summary: dict[str, Any] = {}
        self.meta: dict[str, Any] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            kind = row.pop("kind")
            if kind == "reply":
                self.replies[row["agent"]].append(row)
            elif kind == "dice":
                self.dice.append(row)
            elif kind == "summary":
                self.summary = row
            elif kind == "meta":
                self.meta = row

    def reply(self, agent: str) -> dict:
        if not self.replies[agent]:
            raise CassetteError(f"录像 {self.path.name} 里 {agent} 的回复用完了：引擎多调用了一次")
        return self.replies[agent].popleft()

    def die(self, call: str, args: list) -> Any:
        if not self.dice:
            raise CassetteError(f"录像 {self.path.name} 里的骰子用完了：引擎多掷了一次（{call} {args}）")
        row = self.dice.popleft()
        if row["call"] != call or row["args"] != args:
            raise CassetteError(f"录像里这一掷是 {row['call']} {row['args']}，引擎要的是 {call} {args}")
        return row["result"]


_open: dict[str, Cassette] = {}


def cassette(path: str | Path) -> Cassette:
    """One cassette per file per process, shared by every agent's channel and the dice."""
    key = str(Path(path).resolve())
    if key not in _open:
        _open[key] = Cassette(Path(path))
    return _open[key]


def forget(path: str | Path) -> None:
    _open.pop(str(Path(path).resolve()), None)


class ReplayModel(Model):
    """Backend "replay": the model string is the cassette's path."""

    async def _call(self, messages: list[Msg], *, schema: dict | None, images: list[bytes] | None, on_delta: OnDelta) -> Reply:
        row = cassette(self.spec.model).reply(self.label)
        data = row.get("data")
        if schema is not None and schema.get("title") == "Dialogue" and data is None:
            # Recordings before structured social memory stored NPC replies as plain text.
            data = {"text": row["text"], "changes": []}
        if schema is not None and data is None:
            data = parse_json(row["text"])
        if on_delta and schema is None and row["text"]:
            await on_delta(row["text"])
        return Reply(text=row["text"], data=data)


class ReplayDice:
    """The recorded dice, in order; asking for a different roll is an error."""

    def __init__(self, tape: Cassette):
        self.tape = tape

    def check(self, target: int, bonus: int = 0) -> Roll:
        return Roll(**self.tape.die("check", [target, bonus]))

    def roll(self, expr: str) -> Roll:
        return Roll(**self.tape.die("roll", [expr]))

    def total(self, expr: str) -> int:
        return self.tape.die("total", [expr])

    def table(self, name: str) -> str:
        return self.tape.die("table", [name])
