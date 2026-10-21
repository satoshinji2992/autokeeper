"""The director's backstage requests: de-duplicated, checked for staleness, landed whole.

A request is a set of ops plus the versions of what the director read to make it (the
director's context shows them: fact:<id>（v3）). Before it lands, every read must still hold:
if a fact was rewritten, an NPC replanned or a promise was settled since, the request is
stale and comes back with the refs that moved, to be dropped or redone with fresh context.

The same request twice (same kind and ops) is one request. A newer request about the same
things (same key) supersedes the older one. A request older than its ttl in turns expires.
Its ops land all or nothing: if one is refused, the ones before it are undone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .ops import ENGINE_ONLY, parse
from .record import DIRECTOR, Request
from .scheduler import fresh
from .store import digest

if TYPE_CHECKING:
    from .store import World

TARGETS = ("entity", "npc", "a", "b", "fact", "target", "promise", "thread", "item", "event")


def key_of(kind: str, ops: list[dict[str, Any]]) -> str:
    """What a request is about: its kind and the things its ops touch."""
    touched = sorted({str(op[k]) for op in ops for k in TARGETS if op.get(k)})
    return f"{kind}:{'+'.join(touched)}"


def snapshot(w: World, refs: list[str]) -> dict[str, int]:
    return {ref: w.version(ref) for ref in refs}


def submit(w: World, kind: str, ops: list[dict[str, Any]], *, reads: dict[str, int], reason: str = "", ttl: int = 3,
           key: str = "") -> Request:  # fmt: skip
    for raw in ops:
        parse(raw)  # a malformed op is the caller's mistake, raised now rather than at landing
        if raw.get("kind") in ENGINE_ONLY:
            raise ValueError("伤害、效果与遭遇只由游戏引擎按掷骰结算，不能由导演提交")
    rid = f"req:{digest([kind, ops])[:12]}"
    if (same := w.rec.requests.get(rid)) and same.status == "pending":
        return same
    key = key or key_of(kind, ops)
    for old in w.rec.requests.values():
        if old.status == "pending" and old.key == key:
            old.status, old.note = "dropped", f"被 {rid} 取代"
    req = w.rec.requests[rid] = Request(id=rid, key=key, kind=kind, reason=reason, reads=dict(reads), ops=list(ops), turn=w.st.turn,
                                        t=w.now, ttl=ttl)  # fmt: skip
    return req


@dataclass
class Landing:
    status: str  # landed, stale, expired, dropped, missing
    moved: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    ops: list[str] = field(default_factory=list)


def land(w: World, rid: str) -> Landing:
    req = w.rec.requests.get(rid)
    if req is None:
        return Landing("missing")
    if req.status != "pending":
        return Landing(req.status)
    if w.st.turn - req.turn > req.ttl:
        req.status, req.note = "expired", f"超过 {req.ttl} 轮没有落地"
        return Landing("expired")
    if moved := fresh(w, req.reads):
        req.status, req.note = "stale", "、".join(moved)
        return Landing("stale", moved=moved)
    landed: list[str] = []
    for i, raw in enumerate(req.ops):
        r = w.apply({"actor": DIRECTOR, **raw, "id": f"{rid}:{i}"})
        if not r.ok:
            undone = w.undo(len([x for x in landed if x]))
            req.status, req.note = "dropped", "；".join(r.problems)[:300]
            return Landing("dropped", problems=r.problems + undone)
        landed.append("" if r.repeat else r.op)
    req.status = "landed"
    return Landing("landed", ops=[x for x in landed if x])


def pending(w: World) -> list[Request]:
    return [r for r in w.rec.requests.values() if r.status == "pending"]
