"""Append-only, private per-game evidence. No model calls and no narrative changes."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .. import paths

if TYPE_CHECKING:
    from .game import Game


MAX_BYTES = 200 * 1024 * 1024  # a journal past this is set aside (<slot>.1.jsonl) and a new one begun
# The state, the world and the sessions are written whole when they changed: on every event that
# was nearly every event, 16 snapshots a turn and some 2 MB (a two-turn game came to 3.9 MB). The
# events themselves are all kept; the whole picture is taken once a turn is over (its "sheet"),
# at the end of the game, and on an error.
SNAPSHOT_ON = {"sheet", "ending", "epilogue"}


class Journal:
    def __init__(self, slot: str):
        self.path = paths.game_debug(slot)
        self.run = uuid.uuid4().hex
        self.error = ""
        self._lock = threading.Lock()
        self._seen: dict[str, str] = {}
        self._seq = 0
        self.owner = None
        self.active_calls: dict[str, dict] = {}
        self.active_tasks: dict[str, dict] = {}

    def append(self, kind: str, data: Any, *, turn: int | None = None) -> None:
        from .diagnostics import redact

        row = redact({"id": uuid.uuid4().hex, "at": datetime.now().isoformat(timespec="milliseconds"),
                      "run": self.run, "kind": kind, "turn": turn, "data": data})
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.parent.chmod(0o700)
                if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                    os.replace(self.path, self.path.with_suffix(".1.jsonl"))
                    self._seen.clear()  # the new file starts with whole snapshots again
                # A crash can leave half a JSON line. Preserve it for inspection while
                # putting subsequent complete records on their own lines.
                descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(descriptor, "ab+") as stream:
                    stream.seek(0, os.SEEK_END)
                    if stream.tell():
                        stream.seek(-1, os.SEEK_END)
                        if stream.read(1) != b"\n":
                            stream.write(b"\n")
                    stream.write((json.dumps(row, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
                self.path.chmod(0o600)
                self.error = ""
        except OSError as exc:
            self.error = f"完整记录写入失败：{type(exc).__name__}"

    def read(self, *, after: int = 0, limit: int | None = None, kind: str = "") -> tuple[list[dict], int]:
        """Line cursors permit complete history without loading it all into the webpage."""
        out, cursor = [], after
        try:
            with self.path.open(encoding="utf-8") as stream:
                for number, line in enumerate(stream, 1):
                    if number <= after:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # incomplete final line after an interrupted write
                    cursor = number
                    if not kind or row.get("kind") == kind:
                        out.append({**row, "cursor": number})
                    if limit is not None and len(out) >= limit:
                        break
        except OSError:
            pass
        return out, cursor

    def calls(self) -> list[dict]:
        rows, _ = self.read(kind="call")
        return [row["data"] for row in rows]

    def recent(self, *, before: int = 0, limit: int = 100, kind: str = "") -> tuple[list[dict], int]:
        rows: deque[dict] = deque(maxlen=limit)
        try:
            with self.path.open(encoding="utf-8") as stream:
                for number, line in enumerate(stream, 1):
                    if before and number >= before:
                        break
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not kind or row.get("kind") == kind:
                        rows.append({**row, "cursor": number})
        except OSError:
            pass
        return list(rows), rows[0]["cursor"] if rows else 0

    def observe(self, row: dict, *, turn: int, session: str = "") -> None:
        kind = "call" if row.get("phase", "complete") == "complete" else "model"
        call_id = row.get("call_id", "")
        if call_id and row.get("phase") == "started":
            self.active_calls[call_id] = {k: row.get(k) for k in ("call_id", "agent", "model", "phase", "at")}
            self.active_calls[call_id].update(session=session, turn=turn)
        elif kind == "call":
            self.active_calls.pop(call_id, None)
        elif call_id in self.active_calls and row.get("phase") != "delta":
            self.active_calls[call_id].update({k: row[k] for k in ("phase", "attempt", "error", "delay", "at") if k in row})
        self.append(kind, {**row, "turn": turn, "session": session or row.get("agent", "")}, turn=turn)
        if kind == "call" and self.owner is not None and (game := self.owner()) is not None:
            self.sync(game)

    @contextmanager
    def task(self, name: str, *, turn: int, details: Any = None):
        from ..harness import trace

        task_id = uuid.uuid4().hex
        self.active_tasks[task_id] = {"task_id": task_id, "name": name, "turn": turn, "status": "running"}
        self.append("task", {"task_id": task_id, "name": name, "status": "running", "details": details}, turn=turn)
        try:
            with trace.capture(lambda row: self.observe(row, turn=turn, session=name)):
                yield
        except BaseException as exc:
            self.append("task", {"task_id": task_id, "name": name, "status": "cancelled" if
                                 type(exc).__name__ == "CancelledError" else "failed",
                                 "error": f"{type(exc).__name__}: {exc}"}, turn=turn)
            raise
        else:
            self.append("task", {"task_id": task_id, "name": name, "status": "complete"}, turn=turn)
        finally:
            self.active_tasks.pop(task_id, None)
            if self.owner is not None and (game := self.owner()) is not None:
                self.sync(game)

    def sync(self, game: Game) -> None:
        """Persist complete changed sections, including old entries before ring buffers expire."""
        try:
            self._sync(game)
        except Exception as exc:
            self.error = f"状态记录失败：{type(exc).__name__}"

    def _sync(self, game: Game) -> None:
        from .diagnostics import snapshot

        data = snapshot(game, include_calls=False)
        for key in ("state", "world", "module", "backstage", "sessions", "maintenance"):
            section = data[key]
            encoded = json.dumps(section, ensure_ascii=False, sort_keys=True, default=str)
            if self._seen.get(key) != encoded:
                self.append(f"snapshot.{key}", section, turn=game.state.turn)
                self._seen[key] = encoded
        self._seq = game.state.record.seq


def record_event(game: Game, event: dict) -> None:
    journal = getattr(game, "debug_journal", None)
    if journal is not None:
        journal.append("event", event, turn=game.state.turn)
        if str(event.get("type", "")) in SNAPSHOT_ON:
            journal.sync(game)
