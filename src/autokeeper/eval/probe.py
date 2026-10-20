"""Cheap prompt A/B on frozen inputs (`autokeeper eval probe`).

A replay of a real session (eval/session.py) runs every agent of every turn, and its noise comes
from all of them. To try a prompt, only the role that reads it has to run: keep what that role was
actually sent in a game, and send it again with the other system prompt.

    build   replay turns of a save once with the prompts as they are, and keep, for each call of the
            keeper (its telling, with the scene), an NPC (its reply) and the keeper's arrangement
            (plan): the system prompt, the messages, the schema, what came back, and the game as it
            stood, so a scene can be checked again and an NPC's reply tried again. One library per
            build, in dev/output/probes/<name>/samples.jsonl; it is reused for every variant after.
    run     each sample of a role sent once with variant B's system prompt (A is what was kept, or a
            variant too); both outputs scored by rule; every ten pairs a sign test, stopping once
            the split is clear or plainly even.

The rules, per role (lower is better):
    keeper  the scene does not hold (world/scene.plan, on the game as it stood); a clue behind a check
            told before anyone found it; something meant quietly called out; nobody answers while
            someone is in the house; hurt with nothing settled
    npc     what Cast._dialogue_problems would send back; said it goes along, hands over, walks off,
            without the field; offered something to play on meeting and left played empty
    plan    an NPC in initiative who is not here; a destination that is not a place

    autokeeper eval probe build zhuishuren-1019-224053 --turns 11,22,23 --name zhui
    autokeeper eval probe run zhui --role keeper --b dev/prompts/kcmix.yaml
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
from pathlib import Path
from typing import Any

from .. import config, paths
from ..engine.state import GameState
from ..harness import trace
from ..harness.models import ModelPool
from ..module.schema import Module
from . import control, variants

ROLES = {"keeper": "keeper", "npc": "npc", "plan": "plan"}  # role -> the model label it is called under


def library(name: str) -> Path:
    return paths.dev_output("probes", name)


# -- build ------------------------------------------------------------------------------------------
async def build(slot: str, *, turns: str, name: str, log=print) -> Path:
    """Replay the turns once, keeping every keeper, NPC and plan call with what it needs to be scored."""
    from ..world import scene
    from . import session

    folder = library(name)
    folder.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    snaps: dict[str, Any] = {}
    plan_fn = scene.plan

    def plan(w, raw, ctx):  # the game as the keeper's scene is checked against
        snaps["scene"] = {"state": w.st.model_dump(mode="json"),
                          "ctx": {k: getattr(ctx, k) for k in ("elapsed", "missing", "walk", "ids", "start")}}
        return plan_fn(w, raw, ctx)

    def seen(row: dict[str, Any]) -> None:
        if row.get("phase", "complete") != "complete" or row.get("error") or row.get("agent") not in ROLES.values():
            return
        keep = {k: row.get(k) for k in ("agent", "system", "input", "schema", "output", "data")}
        keep["turn"] = session._turn.get()
        if row["agent"] == "keeper" and (row.get("schema") or {}).get("title") == "Telling":
            keep["scene"] = snaps.pop("scene", None)
        rows.append(keep)

    notify = trace._notify

    def tapped(row: dict[str, Any]) -> None:  # beside the game's own observer (Cast._send captures for its journal)
        notify(row)
        seen(row)

    scene.plan, trace._notify = plan, tapped
    try:
        report = await session.run(slot, turns=turns, parallel=1, baseline=False, log=log,
                                   out=folder / "replay.json", npc_snapshots=snaps)
    finally:
        scene.plan, trace._notify = plan_fn, notify
    # Every NPC reply is checked once after it comes back, in order: the snapshots line up with the calls.
    npc_rows = [r for r in rows if r["agent"] == "npc"]
    for r, snap in zip(npc_rows, snaps.get("npc", []), strict=False):
        r["npc"] = snap
    said = {row["turn"]: row["input"] for row in report["rows"]}
    with (folder / "samples.jsonl").open("w", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            role = next(k for k, v in ROLES.items() if v == r["agent"])
            f.write(json.dumps({"id": i, "role": role, "said": said.get(r["turn"], ""), "module": report["module"], **r},
                               ensure_ascii=False) + "\n")
    log(f"样本库：{folder}（{len(rows)} 条）")
    return folder


def samples(name: str, role: str) -> list[dict[str, Any]]:
    path = library(name) / "samples.jsonl"
    out = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [s for s in out if s["role"] == role and (role != "keeper" or s.get("scene"))]


# -- a variant's system prompt, from the one that was sent --------------------------------------------
def pairs(v: variants.Variant) -> list[tuple[str, str]]:
    """What the variant changes, as literal (old, new) text: a template's lines with no {field} in them,
    a skill's body whole. Applied to a system prompt that was built from the code as it is."""
    out: list[tuple[str, str]] = []
    for target, new in v.texts.items():
        old = variants.original(target)
        if not variants._is_prompt(target):
            out.append((old, new))
            continue
        a, b = old.split("\n"), new.split("\n")
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            lo, hi = i1, i2
            before, after = "\n".join(a[lo:hi]), "\n".join(b[j1:j2])
            if re.search(r"\{\w+\}", before + after):
                raise ValueError(f"{target} 改到了带占位符的行，探针换不了：{before[:40]}")
            if tag == "delete":  # whole lines gone: with their line breaks
                out.append((before + "\n", "") if hi < len(a) else ("\n" + before, ""))
            elif not before:  # a pure insertion: anchor it on the line before
                anchor = a[i1 - 1] if i1 else ""
                out.append((anchor, anchor + "\n" + after))
            else:
                out.append((before, after))
    return out


def swap(system: str, changes: list[tuple[str, str]]) -> str:
    for old, new in changes:
        if system.count(old) != 1:
            raise ValueError(f"系统提示里「{old[:30]}」出现了 {system.count(old)} 次")
        system = system.replace(old, new)
    return system


# -- scoring ------------------------------------------------------------------------------------------
def _world(m: Module, state: dict[str, Any]):
    from ..module import improv
    from ..world.store import World

    st = GameState.model_validate(state)
    m = m.model_copy(deep=True)
    if st.improv:
        improv.restore(m, st.improv)
    return World(m, st)


def _travel(m: Module):
    from ..engine.game import Game

    probe = Game.__new__(Game)
    probe.m = m
    return probe._travel


def score_keeper(m: Module, s: dict[str, Any], data: Any) -> dict[str, Any]:
    from ..world import scene as scene_mod
    from . import session

    data = data if isinstance(data, dict) else {}
    text, raw = str(data.get("text") or ""), data.get("scene")
    w = _world(m, s["scene"]["state"])
    ctx = scene_mod.Context(travel=_travel(w.m), **s["scene"]["ctx"])
    problems = scene_mod.plan(w, raw, ctx).problems if isinstance(raw, dict) else ["没有 scene"]
    st = w.st
    record = session.world_after(w.m, st, st.turn, {"minutes": st.minutes, "location": st.location}, None)
    ruled = control.ruled(w.m, record, s["said"], text, [], found=list(st.clues), ruling=None,
                          public=session.seen_before(st, st.turn + 1))
    found = {"scene": len(problems), "early_told": len(ruled["clues"]["early_told"]),
             **{k: len(v) for k, v in ruled["rules"].items()}}
    return {"score": sum(found.values()), "found": found, "why": problems[:3], "chars": len(text)}


def score_npc(m: Module, s: dict[str, Any], data: Any) -> dict[str, Any]:
    from ..agents.cast import Cast, Dialogue

    snap = s.get("npc")
    try:
        dialogue = Dialogue.model_validate(data) if isinstance(data, dict) else Dialogue(text=str(data or ""))
    except ValueError:
        return {"score": 1, "found": {"format": 1}, "why": ["回复不是合法的 Dialogue"], "chars": 0}
    found: dict[str, int] = {}
    why: list[str] = []
    if snap:
        w = _world(m, snap["state"])
        cast = Cast(config.load(), w.m)
        cast.world = w
        problems = cast._dialogue_problems(w.st, snap["npc"], dialogue, dialogue.text.strip(), snap["destinations"],
                                           private=snap["private"])
        found["checks"] = len(problems)
        why += problems[:3]
        offered = snap.get("offered") or []
        found["played_missing"] = int(bool(offered) and not dialogue.played)
        found["played_invalid"] = sum(p not in offered for p in dialogue.played)
    fields = {"accompany": dialogue.accompany or any(d.accompany for d in dialogue.deceptions), "items": dialogue.items,
              "leave": dialogue.leave}
    found["said_missing"] = sum(1 for f in control.cues(dialogue.text) if not fields.get(f))
    return {"score": sum(found.values()), "found": found, "why": why, "chars": len(dialogue.text)}


def score_plan(m: Module, s: dict[str, Any], data: Any) -> dict[str, Any]:
    data = data if isinstance(data, dict) else {}
    here = set(re.findall(r"[（(]([a-z_][a-z0-9_]*)[)）]", "\n".join(x.get("content", "") for x in s["input"] if isinstance(x, dict))))
    places = {loc.id for loc in m.locations}
    action = data.get("action") or {}
    bad_npc = sum(1 for x in data.get("initiative") or [] if here and x.get("npc") not in here)
    bad_place = int(bool(action.get("destination")) and action.get("destination") not in places)
    found = {"format": int(not action), "initiative_absent": bad_npc, "destination": bad_place}
    return {"score": sum(found.values()), "found": found, "why": [], "chars": 0}


SCORE = {"keeper": score_keeper, "npc": score_npc, "plan": score_plan}


# -- run ----------------------------------------------------------------------------------------------
def _stop(wins: int, losses: int, n: int) -> str:
    """Every ten pairs: a clear split, or plainly even after twenty."""
    from .blind import sign_test

    if n % 10:
        return ""
    if wins + losses >= 5 and sign_test(wins, losses) < 0.05:
        return "分出高下"
    if n >= 20 and abs(wins - losses) <= max(1, (wins + losses) // 5):
        return "打平"
    return ""


async def run(name: str, *, role: str, b: Path, a: Path | None = None, limit: int = 0, log=print,
              out: Path | None = None) -> dict[str, Any]:
    """Variant B (and A, when given; else what the library kept) on every sample of the role."""
    from .blind import sign_test

    pool = ModelPool(config.load().role(ROLES[role]), size=1, label=ROLES[role])
    vb = variants.load(b)
    va = variants.load(a) if a else None
    cb, ca = pairs(vb), (pairs(va) if va else None)
    rows, wins, losses, stopped = [], 0, 0, ""
    m: Module | None = None
    chosen = samples(name, role)
    if limit:
        chosen = chosen[:limit]
    try:
        for s in chosen:
            m = m or Module.load(next(p for p in (paths.MODULES / s["module"], paths.LOCAL_MODULES / s["module"]) if (p / "module.yaml").is_file()))
            got = {}
            for side, changes, kept in (("a", ca, s.get("data") or s.get("output")), ("b", cb, None)):
                if changes is None:
                    data = kept
                else:
                    try:
                        system = swap(s["system"], changes)
                    except ValueError as exc:
                        log(f"  样本 {s['id']}：{exc}")
                        data = None
                        system = ""
                    if system:
                        reply = await pool.complete(system, s["input"], schema=s.get("schema"))
                        data = reply.data if s.get("schema") else reply.text
                got[side] = {"data": data, **SCORE[role](m, s, data)}
            d = got["b"]["score"] - got["a"]["score"]
            wins += d < 0
            losses += d > 0
            rows.append({"id": s["id"], "turn": s["turn"], **got})
            log(f"  样本 {s['id']}（第 {s['turn']} 轮）A {got['a']['score']} {got['a']['found']} · B {got['b']['score']} {got['b']['found']}")
            if stopped := _stop(wins, losses, len(rows)):
                break
    finally:
        await pool.close()
    summary = {"library": name, "role": role, "a": str(a or "kept"), "b": str(b), "pairs": len(rows), "b_better": wins,
               "b_worse": losses, "p": round(sign_test(wins, losses), 4), "stopped": stopped or "样本用完",
               "a_total": sum(r["a"]["score"] for r in rows), "b_total": sum(r["b"]["score"] for r in rows),
               "by_rule": {side: _sum([r[side]["found"] for r in rows]) for side in ("a", "b")},
               "chars": {side: round(sum(r[side]["chars"] for r in rows) / max(1, len(rows))) for side in ("a", "b")},
               "rows": rows}
    target = out or library(name) / f"run-{role}-{vb.name}{'-vs-' + va.name if va else ''}.json"
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    summary["out"] = str(target)
    return summary


def _sum(found: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in found:
        for k, v in f.items():
            out[k] = out.get(k, 0) + v
    return out


def main_sync(coro):  # for the CLI
    return asyncio.run(coro)
