"""Append-only, private per-game evidence. No model calls and no narrative changes."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import paths

if TYPE_CHECKING:
    from .game import Game


MAX_BYTES = 200 * 1024 * 1024  # split into numbered segments; no older segment is overwritten
READ_BYTES = 64 * 1024
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
        if self.owner is not None and (owner := self.owner()) is not None:
            if scene := getattr(owner.state, "scene", None):
                row["scene"] = scene.id
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.parent.chmod(0o700)
                if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                    segments = self._segments()
                    number = int(segments[-1].stem.rsplit(".", 1)[1]) + 1 if segments else 1
                    os.replace(self.path, self.path.with_suffix(f".{number}.jsonl"))
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

    def _segments(self) -> list[Path]:
        """Numbered archives in creation order, including the original .1 archive."""
        prefix = self.path.stem + "."
        archives = [p for p in self.path.parent.glob(prefix + "*.jsonl")
                    if p.name[len(prefix):-len(".jsonl")].isdigit()]
        return sorted(archives, key=lambda p: int(p.name[len(prefix):-len(".jsonl")]))

    def _lines(self) -> Iterator[tuple[int, str]]:
        """A stable view of all segments, using global line cursors across rotation.

        Snapshot lengths and the live segment's eventual archive name under the
        writer's lock. Each bounded read closes before yielding: on Windows an
        open ordinary reader would prevent the writer from rotating the segment.
        """
        with self._lock:
            archives = self._segments()
            files = [(p, p.stat().st_size, False) for p in archives]
            if self.path.exists():
                number = int(archives[-1].stem.rsplit(".", 1)[1]) + 1 if archives else 1
                files.append((self.path.with_suffix(f".{number}.jsonl"), self.path.stat().st_size, True))
        number = 0
        for archive, length, live in files:
            position, fragments = 0, []
            while position < length:
                with self._lock:
                    path = self.path if live and not archive.exists() else archive
                    with path.open("rb") as stream:
                        stream.seek(position)
                        chunk = stream.read(min(READ_BYTES, length - position))
                if not chunk:
                    break
                position += len(chunk)
                pieces = chunk.split(b"\n")
                for piece in pieces[:-1]:
                    fragments.append(piece)
                    number += 1
                    yield number, b"".join(fragments).decode("utf-8") + "\n"
                    fragments.clear()
                fragments.append(pieces[-1])
            if any(fragments):
                number += 1
                yield number, b"".join(fragments).decode("utf-8")

    def read(self, *, after: int = 0, limit: int | None = None, kind: str = "") -> tuple[list[dict], int]:
        """Line cursors permit complete history without loading it all into the webpage."""
        out, cursor = [], after
        try:
            for number, line in self._lines():
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
            for number, line in self._lines():
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
