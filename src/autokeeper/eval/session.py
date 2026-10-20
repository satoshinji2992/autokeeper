"""Replay a real session's turns through the current pipeline and check what the story told
against the world it kept (`autokeeper eval session`).

The turn graph's checkpoints keep the whole game after every turn (graph.remember), so a
recorded turn N can be played again from the game as it stood after turn N-1, with the
player's own words and the dice as they fell that night. A turn's mistake cannot run on into
the next one, nothing is saved, and the words are a person's, not an AI investigator's.

Per turn, for the recorded turn (the baseline) and the replayed one alike:

    coherence   a judge model reads the narration and the NPCs' lines beside the world after the
                turn: who is here and who is near (rooms of the same house), who goes along, what
                is held, the hour, what the turn settled. It lists what contradicts the record;
                it reads twice, and a problem stands when both readings found one of its kind.
    target      the player named someone; did the parsed action keep them, or put another in
    echo        how much of an NPC's line the narration after it says again
    calls       seconds and cost of the turn's model calls (replays only)

    autokeeper eval session <slot> --turns 70-80      # these turns only
    autokeeper eval session <slot> --recorded         # judge the recorded turns, play nothing
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import re
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .. import config, paths
from ..agents.guard import grams, leaks
from ..engine import check
from ..engine.state import GameState
from ..harness import trace
from ..harness.models import ModelPool
from ..module.schema import Module
from ..world import knowledge
from ..world.context import rooms
from .style import Loaded, module_of

GONE = ("死亡", "失踪")
BACKGROUND = {"world", "summarizer", "progress", "judge"}


# -- the recorded session --------------------------------------------------------------------------
def recorded(slot: str) -> dict[int, dict[str, Any]]:
    """Turn -> {"state": the game after it, "action", "ruling"} from the turn graph's checkpoints."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = paths.checkpoints()
    if not path.is_file():
        raise FileNotFoundError(f"没有找到回合记录：{path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    try:
        out: dict[int, tuple[str, dict[str, Any]]] = {}
        for cp in SqliteSaver(conn).list({"configurable": {"thread_id": slot}}):
            values = cp.checkpoint.get("channel_values", {})
            snap = values.get("snapshot")
            if not isinstance(snap, dict) or "turn" not in snap:
                continue
            ts = cp.checkpoint.get("ts", "")
            turn = int(snap["turn"])
            # The snapshot channel keeps its value while the next turn runs, with that turn's
            # action in the other channels: the first checkpoint that holds it is its own turn's.
            if turn not in out or ts < out[turn][0]:
                out[turn] = (ts, {"state": snap, "action": values.get("action"), "ruling": values.get("ruling")})
        return {t: v for t, (_, v) in out.items()}
    finally:
        conn.close()


def said(state: dict[str, Any], turn: int) -> str:
    """What the player typed in this turn: the first line of theirs logged in it."""
    return next((e["text"] for e in state.get("log", []) if e.get("turn") == turn and e.get("kind") == "player"), "")


def rolled(state: dict[str, Any], turn: int) -> list[int]:
    """The d100 values of this turn's checks, in order, from their log lines (Check.describe)."""
    values = []
    for e in state.get("log", []):
        if e.get("turn") == turn and e.get("kind") in ("dice", "sanity"):
            values += [int(v) for v in re.findall(r"→ (\d+)", e.get("text", ""))]
    return values


def parse_turns(spec: str, have: list[int]) -> list[int]:
    if not spec:
        return have
    chosen: set[int] = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        if a:
            chosen |= set(range(int(a), int(b or a) + 1))
    return [t for t in have if t in chosen]


# -- the world after a turn, as the judge reads it -------------------------------------------------
def _name(m: Module, ident: str) -> str:
    return next((x.name for x in [*m.locations, *m.npcs] if x.id == ident), ident)


world_after = check.account  # the record a telling is read against, in play and here alike


def told(st: GameState, turn: int) -> tuple[str, list[str]]:
    """The keeper's narration of the turn and the NPCs' lines in it."""
    keeper = "\n\n".join(e.text for e in st.log if e.turn == turn and e.kind == "keeper")
    lines = [f"{e.who}：{e.text}" for e in st.log if e.turn == turn and e.kind == "npc"]
    return keeper, lines


# -- the measures ----------------------------------------------------------------------------------
def echo(keeper: str, lines: list[str]) -> float:
    """The share of the NPCs' 5-character runs the narration says again (0 when nobody spoke)."""
    spoken = set().union(*(grams(x.partition("：")[2]) for x in lines)) if lines else set()
    return round(len(spoken & grams(keeper)) / len(spoken), 2) if spoken else 0.0


def target(m: Module, st: GameState, text: str, action: dict[str, Any] | None) -> str:
    """"" when the action keeps whom the player named; else what it did instead. Only someone
    here or near (a question about an absent man may go to whoever is here), or someone the
    player sets out to find or call ("去找阿萨塔", "叫托马斯")."""
    if not action or not action.get("npc"):
        return ""
    near = rooms(m, st.location) | {st.location}
    at = {e.id: e.at for e in st.record.entities.values() if e.kind == "npc"}

    def called(n) -> bool:
        parts = [p for p in [n.name, *n.name.split("·")] if len(p) >= 2 and p in text]
        if not parts:
            return False
        sought = any(re.search(rf"(找|叫|喊|跟上|跟着|追)\s*{re.escape(p)}", text) for p in parts)
        return sought or at.get(n.id) in near

    named = [n for n in m.npcs if called(n)]
    if not named or any(n.id == action["npc"] for n in named):
        return ""
    return f"玩家说的是{'、'.join(n.name for n in named)}，行动换成了{_name(m, action['npc'])}"


AGREED, Problem, Verdict, JUDGE = check.AGREED, check.Problem, check.Verdict, check.RULES


async def ask_judge(pool: ModelPool, record: str, said_text: str, keeper: str, lines: list[str]) -> list[dict[str, str]]:
    """Two readings; a problem stands when the other reading found one of its kind too."""
    asked = [{"role": "user", "content": judge_prompt(record, said_text, keeper, lines)}]

    async def read() -> list[Problem]:
        reply = await pool.complete(JUDGE, asked, schema=Verdict.model_json_schema())
        try:
            found = Verdict.model_validate(reply.data or {}).problems
        except ValueError:
            return []
        return check.standing(found)

    first, second = await asyncio.gather(read(), read())
    kinds = {p.kind for p in second}
    return [p.model_dump() for p in first if p.kind in kinds]


# -- what the players must not hear yet ----------------------------------------------------------
# The keeper sees everything (the user's call, 2026-10-20); what it lets slip is measured here.
HIDDEN_KINDS = ("truth", "conclusion", "secret")

LEAK_RULES = """你核对跑团守秘人的一段叙述有没有说漏玩家还不知道的事。给你一份还没向玩家公开的清单（每条前面是编号）、这一轮本来就可以露出的东西，和这一轮守秘人的叙述。
叙述直接说出、换个说法讲出，或者明白暗示了清单里某一条的内容，才算说漏；只是写到同一个人、同一个地方、同一样东西，写了眼前看得见的景象和人物的举动，写的是这一轮本来就可以露出的东西，或者写的是清单之外的事，都不算。
逐条核对，没有说漏就留空。"""

NPC_LEAK_RULES = """你核对跑团里一个 NPC 的台词有没有说出他本来不可能知道的事。给你一份这个 NPC 不知道的事（每条前面是编号）、这一轮已经正式交给玩家的东西（线索、手稿、事件），和这一轮他说的话。
他直接说出、换个说法讲出，或者明白暗示了清单里某一条的内容，而这一轮并没有把它正式交给玩家，才算说漏；他自己知道的事愿意讲出来，不在清单里，不算；说的谎、猜测、闲话，只提到同一个人、同一个地方，也不算。
逐条核对，没有说漏就留空。"""

FINDING_RULES = """你给跑团叙述里几处“没有经过这一轮结算的具体细节”分类。给你玩家还不知道的真相和线索，以及这些细节。逐条判断：
- 假线索：和真相或线索对不上，或者指向一个并不存在的方向，会把玩家带偏；
- 白给：讲出了某条还没找到的线索的内容，玩家没调查到、没检定就知道了；
- 伏笔：和真相一致的气氛或痕迹，没有讲出线索的内容，也不会把人带偏。
每一条都要给出分类。"""


class Leak(BaseModel):
    secret: str = Field(description="说漏的是清单里的哪一条，照抄它的编号")
    claim: str = Field(description="叙述里说漏它的那一小段原文，不超过 40 字")


class Leaks(BaseModel):
    leaks: list[Leak] = Field(default_factory=list, max_length=6, description="说漏的地方；没有就留空")


class Finding(BaseModel):
    index: int = Field(description="细节的编号")
    label: Literal["假线索", "白给", "伏笔"]


class Findings(BaseModel):
    findings: list[Finding] = Field(default_factory=list, description="每一处细节一条")


def secrets(st: GameState) -> dict[str, str]:
    """Fact id -> text of what the players do not know yet, from the record after the turn: the
    truth, the conclusions, the NPCs' secrets and the clues not found. A reveal counts only with
    its source; an old marker without one (the 10-19 audit's cascade) stays hidden."""
    found = set(st.clues)
    out = {}
    for f in st.record.facts.values():
        if f.retracted or not f.text.strip() or (f.revealed is not None and f.reveal_source):
            continue
        if f.kind in HIDDEN_KINDS or (f.id.startswith("clue:") and f.id.removeprefix("clue:") not in found):
            out[f.id] = f.text.strip()
    return out


def speakers(m: Module, lines: list[str]) -> dict[str, list[str]]:
    """NPC id -> what it said this turn, from the told lines ("名字：台词")."""
    out: dict[str, list[str]] = {}
    for line in lines:
        name, _, words = line.partition("：")
        if (n := next((x for x in m.npcs if x.name == name), None)) is not None:
            out.setdefault(n.id, []).append(words)
    return out


def seen_before(st: GameState, turn: int) -> str:
    """What the table had read before this turn: shared words there are not a slip."""
    return "\n".join(e.text for e in st.log if e.turn < turn and e.kind in ("opening", "keeper", "npc", "clue", "handout", "event"))


def shown_now(st: GameState, turn: int, hints: list[str] | None = None, *, npc: bool = True) -> str:
    """What this turn may show anyway: the clues and handouts it gave, the events it played, the
    module's foreshadowing handed to the keeper, and (for the keeper's words) the NPCs' own."""
    kinds = ("clue", "handout", "event", "npc") if npc else ("clue", "handout", "event")
    rows = [e.text for e in st.log if e.turn == turn and e.kind in kinds]
    return "\n".join([*rows, *(hints or [])])


async def ask_leaks(pool: ModelPool, hidden: dict[str, str], keeper: str, shown: str = "", *, npc: bool = False) -> list[dict[str, str]]:
    """Two readings of the narration (or, with `npc`, the NPCs' lines) against what the players do
    not know yet; a slip stands when both readings name the same item."""
    if not keeper.strip() or not hidden:
        return []
    listed = "\n".join(f"- {k}：{v}" for k, v in hidden.items())
    given, said = ("这一轮已经正式交给玩家的", "NPC 的台词") if npc else ("这一轮本来就可以露出的", "守秘人的叙述")
    asked = [{"role": "user", "content": f"【还没向玩家公开的】\n{listed}\n\n【{given}】\n{shown.strip() or '（没有）'}"
                                         f"\n\n【{said}】\n{keeper}"}]  # fmt: skip

    async def read() -> list[Leak]:
        reply = await pool.complete(NPC_LEAK_RULES if npc else LEAK_RULES, asked, schema=Leaks.model_json_schema())
        try:
            return [x for x in Leaks.model_validate(reply.data or {}).leaks if x.secret in hidden]
        except ValueError:
            return []

    first, second = await asyncio.gather(read(), read())
    named = {x.secret for x in second}
    return [x.model_dump() for x in first if x.secret in named]


async def ask_findings(pool: ModelPool, hidden: dict[str, str], claims: list[str]) -> list[dict[str, str]]:
    """What the details a turn did not settle are, read against the truth: a false lead or a clue
    given away (both bad), or foreshadowing that holds (a keeper's craft). Two readings; a label
    stands when both give it, else 不确定."""
    if not claims:
        return []
    listed = "\n".join(f"- {k}：{v}" for k, v in hidden.items()) or "（没有）"
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    asked = [{"role": "user", "content": f"【玩家还不知道的】\n{listed}\n\n【这些细节】\n{numbered}"}]

    async def read() -> dict[int, str]:
        reply = await pool.complete(FINDING_RULES, asked, schema=Findings.model_json_schema())
        try:
            return {x.index: x.label for x in Findings.model_validate(reply.data or {}).findings}
        except ValueError:
            return {}

    first, second = await asyncio.gather(read(), read())
    return [{"claim": c, "label": first[i] if first.get(i) and first.get(i) == second.get(i) else "不确定"}
            for i, c in enumerate(claims, 1)]


async def rejudge(path: Path, *, out: Path | None = None) -> dict[str, Any]:
    """Judge a report's turns again with today's judge, from what it kept (record, narration,
    lines): a change to the judge needs no replay."""
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    pool = ModelPool(replace(config.load().role("summarizer"), effort=None), size=4, label="judge")
    sem = asyncio.Semaphore(8)

    async def one(row: dict[str, Any], side: str) -> None:
        got = row.get(side)
        if not got or "record" not in got or (not got.get("keeper") and not got.get("npc")):
            return
        async with sem:
            got["problems"] = await ask_judge(pool, got["record"], row["input"], got.get("keeper", ""), got.get("npc", []))
            if got.get("hidden") is not None:
                # A report from before "shown" was kept: the turn's record (settled results, the
                # foreshadowing it was handed) and the NPCs' lines stand in for it.
                shown = got.get("shown") or (got["record"] + "\n" + "\n".join(got.get("npc", [])))
                got["leaks"] = await ask_leaks(pool, got["hidden"], got.get("keeper", ""), shown)
                got["findings"] = await ask_findings(pool, got["hidden"], [p["claim"] for p in got["problems"] if p["kind"] == "发现"])
                if got.get("npc_said"):
                    given = got.get("given") or got["record"]
                    got["npc_leaks"] = [x for npc, words in got["npc_said"].items() for x in await ask_leaks(
                        pool, {fid: got["hidden"][fid] for fid in got["npc_unknown"].get(npc, [])}, "\n".join(words), given, npc=True)]

    try:
        await asyncio.gather(*(one(r, side) for r in report["rows"] for side in ("recorded", "replayed")))
    finally:
        await pool.close()
    report["summary"] = summarize(report["rows"])
    out = out or Path(path).with_name(Path(path).stem + "-rejudged.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    report["out"] = str(out)
    return report


judge_prompt = check.prompt


# -- playing a turn again --------------------------------------------------------------------------
_turn = contextvars.ContextVar("eval_turn", default=0)


class _Calls:
    """The model calls of each turn, by the turn whose task made them (tasks inherit it)."""

    def __init__(self) -> None:
        self.by_turn: dict[int, list[dict[str, Any]]] = {}

    def __call__(self, model, reply, schema) -> None:
        if t := _turn.get():
            self.by_turn.setdefault(t, []).append({"agent": model.label, "seconds": round(reply.seconds, 2),
                                                   "cost": round(reply.cost_usd, 6), "repairs": reply.repairs})  # fmt: skip


async def replay(m_dir: Path, before: dict[str, Any], text: str, dice: list[int], *, turn: int,
                 timeout: float = 240, blind: bool = False) -> tuple[GameState, list[dict[str, Any]]]:  # fmt: skip
    """One turn played again from the game as it stood before it: the game after it, and what
    the turn showed (its events, without the narration's pieces). `blind` keeps the keeper from
    the backstage, to compare with the keeper that sees it."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from ..agents.cast import Cast
    from ..engine import graph
    from ..engine.game import Game
    from ..rules.coc import Rules

    _turn.set(turn)
    cfg = replace(config.load(), visual_limit=0, imagegen=False, showcase=False)
    m = Module.load(m_dir)
    st = GameState.model_validate(before)
    st.pending_turn = {}
    cast = Cast(cfg, m)
    cast.backstage = not blind
    game = Game(m, st, cast, Rules(dice=Loaded(dice)))  # type: ignore[arg-type]
    game.director_wait = None  # nobody is waiting: a plan the turn asks for lands in it
    game.world_later = True  # as on a screen: the NPCs' own decisions come after the turn is told
    app = graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": f"eval-{turn}"}}
    shown: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(timeout):
            inp: Any = {"input": text, "by_hand": False}
            while True:
                offer = None
                async for mode, chunk in app.astream(inp, thread, stream_mode=["custom", "updates"]):
                    if mode == "custom" and chunk.get("type") not in ("keeper_delta", "status"):
                        shown.append(chunk)
                    elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
                        offer = chunk["__interrupt__"][0].value
                if offer is None:
                    break
                inp = Command(resume="drop")  # a failed roll stays failed, as most players leave it
            if game._world_task is not None:  # counted with the turn, though nobody waited for it
                await asyncio.gather(game._world_task, return_exceptions=True)
        report = game.told.report if game.told is not None else ""
        shown.append({"type": "hints", "lines": [line.removeprefix("备注：") for line in report.splitlines() if "端倪" in line]})
        shown.append({"type": "woke", "lines": [x for r in game.debug_world for x in r.woke]})  # who decided backstage, and why
    finally:
        await game.close()
        await cast.close()
    return st, shown


# -- the whole run -----------------------------------------------------------------------------------
async def run(slot: str, *, turns: str = "", recorded_only: bool = False, out: Path | None = None, parallel: int = 3,
              log=print, blind: bool = False) -> dict[str, Any]:  # fmt: skip
    record = recorded(slot)
    have = sorted(t for t in record if t - 1 in record)
    chosen = parse_turns(turns, have)
    if not chosen:
        raise ValueError("没有可以重放的回合（要有它前一轮的记录）")
    first = GameState.model_validate(record[chosen[0]]["state"])
    m_dir = next((p for p in (paths.MODULES / first.module, paths.LOCAL_MODULES / first.module) if (p / "module.yaml").is_file()), None)
    if m_dir is None or module_of(first.module) is None:
        raise FileNotFoundError(f"找不到模组 {first.module}")
    out = out or paths.dev_output("eval") / f"session-{slot}-{time.strftime('%m%d-%H%M%S')}.json"
    work = out.with_suffix("")  # the replays' own games folder: their journals and pictures stay out of data/games
    cfg = config.load()
    pool = ModelPool(replace(cfg.role("summarizer"), effort=None), size=4, label="judge")
    calls = _Calls()
    trace.taps.append(calls)
    real_games = paths.games
    paths.games = lambda: work / "games"  # type: ignore[assignment]
    sem = asyncio.Semaphore(parallel)
    rows: list[dict[str, Any]] = []

    async def judge(m: Module, st: GameState, turn: int, before: dict[str, Any], text: str, ruling: dict | None,
                    side: dict[str, Any], hints: list[str] | None = None) -> list[dict[str, str]]:  # fmt: skip
        keeper, lines = told(st, turn)
        side["leaks"], side["verbatim"], side["npc_leaks"] = [], [], []
        if not keeper and not lines:
            return []
        record_text = side["record"] = world_after(m, st, turn, before, ruling, hints)  # kept, so a report can be judged again
        hidden = side["hidden"] = secrets(st)  # kept too, so a report can be read for slips again
        shown = side["shown"] = shown_now(st, turn, hints)
        given = side["given"] = shown_now(st, turn, hints, npc=False)  # what an NPC's words may hold: what the turn handed over
        side["verbatim"] = leaks(keeper, hidden, public=seen_before(st, turn) + "\n" + shown) if keeper else []
        # What an NPC tells of its own knowledge is its call (the user's word, 2026-10-20: 阿萨塔 telling
        # the party of the handprints once she trusts them is initiative); only what it could not know counts.
        said = side["npc_said"] = speakers(m, lines)
        unknown = side["npc_unknown"] = {npc: [fid for fid in hidden if not knowledge.sees(st.record.facts[fid], npc)] for npc in said}
        problems, side["leaks"], *npc_slips = await asyncio.gather(
            ask_judge(pool, record_text, text, keeper, lines), ask_leaks(pool, hidden, keeper, shown),
            *(ask_leaks(pool, {fid: hidden[fid] for fid in unknown[npc]}, "\n".join(words), given, npc=True) for npc, words in said.items()))
        side["npc_leaks"] = [x for slips in npc_slips for x in slips]
        side["findings"] = await ask_findings(pool, hidden, [p["claim"] for p in problems if p["kind"] == "发现"])
        return problems

    def measures(m: Module, st: GameState, turn: int, text: str, action: dict | None, before: dict[str, Any]) -> dict[str, Any]:
        keeper, lines = told(st, turn)
        then = GameState.model_validate(before)  # whom the player could name: those here before they acted
        return {"target": target(m, then, text, action), "echo": echo(keeper, lines), "chars": len(keeper),
                "keeper": keeper, "npc": lines, "action": action}  # fmt: skip

    async def one(turn: int) -> None:
        async with sem:
            before = record[turn - 1]["state"]
            after = record[turn]
            text = said(after["state"], turn)
            if not text:
                return
            m = Module.load(m_dir)
            from ..module import improv

            then = GameState.model_validate(after["state"])
            if then.improv:
                improv.restore(m, then.improv)
            row: dict[str, Any] = {"turn": turn, "input": text}
            row["recorded"] = measures(m, then, turn, text, after.get("action"), before)
            row["recorded"]["problems"] = await judge(m, then, turn, before, text, after.get("ruling"), row["recorded"])
            if not recorded_only:
                started = time.monotonic()
                try:
                    st, shown = await replay(m_dir, before, text, rolled(after["state"], turn), turn=turn, blind=blind)
                    m2 = Module.load(m_dir)
                    if st.improv:
                        improv.restore(m2, st.improv)
                    action = next(({k: v for k, v in e.items() if k != "type"} for e in reversed(shown) if e.get("type") == "action"), None)
                    ruling = next(({k: v for k, v in e.items() if k != "type"} for e in reversed(shown) if e.get("type") == "ruling"), None)
                    row["replayed"] = measures(m2, st, turn, text, action, before)
                    row["replayed"]["ruling"] = ruling
                    hints = next((e["lines"] for e in shown if e.get("type") == "hints"), [])
                    row["replayed"]["problems"] = await judge(m2, st, turn, before, text, ruling, row["replayed"], hints)
                    row["replayed"]["woke"] = next((e["lines"] for e in shown if e.get("type") == "woke"), [])
                except Exception as exc:  # one turn that fails to replay is reported, the rest go on
                    row["replayed"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                row["seconds"] = round(time.monotonic() - started, 1)
            rows.append(row)
            got = row.get("replayed", {})
            log(f"  第 {turn} 轮：原来 {len(row['recorded']['problems'])} 处" + (
                f"，重放 {len(got.get('problems', []))} 处" if "problems" in got else f"，重放失败 {got.get('error', '')}" if got else ""))

    try:
        await asyncio.gather(*(one(t) for t in chosen))
    finally:
        paths.games = real_games  # type: ignore[assignment]
        trace.taps.remove(calls)
        await pool.close()
    rows.sort(key=lambda r: r["turn"])
    for r in rows:
        if "replayed" in r:
            r["replayed"]["calls"] = calls.by_turn.get(r["turn"], [])
    summary = summarize(rows)
    report = {"slot": slot, "module": first.module, "turns": [r["turn"] for r in rows], "summary": summary, "rows": rows,
              "settings": {"blind_keeper": blind}}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    report["out"] = str(out)
    return report


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def side(key: str) -> dict[str, Any]:
        done = [r[key] for r in rows if key in r and "problems" in r[key]]
        if not done:
            return {}
        kinds: dict[str, int] = {}
        for x in done:
            for p in x["problems"]:
                kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
        spoke = [x for x in done if x["npc"]]
        calls = [c for x in done for c in x.get("calls", [])]
        out = {
            "turns": len(done),
            "turns_with_problems": sum(bool(x["problems"]) for x in done),
            "problems": sum(len(x["problems"]) for x in done),
            "by_kind": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
            "turns_with_leaks": sum(bool(x.get("leaks")) for x in done),
            "leaks": sum(len(x.get("leaks", [])) for x in done),
            "verbatim_leaks": sum(len(x.get("verbatim", [])) for x in done),
            "turns_with_npc_leaks": sum(bool(x.get("npc_leaks")) for x in done),
            "npc_leaks": sum(len(x.get("npc_leaks", [])) for x in done),
            "findings": {label: sum(f["label"] == label for x in done for f in x.get("findings", []))
                         for label in ("假线索", "白给", "伏笔", "不确定")},
            "target_swaps": sum(bool(x["target"]) for x in done),
            "echo_when_npc_spoke": round(sum(x["echo"] for x in spoke) / len(spoke), 2) if spoke else 0.0,
            "keeper_chars": round(sum(x["chars"] for x in done) / len(done)),
        }
        if calls:
            out["cost_usd"] = round(sum(c["cost"] for c in calls), 4)
            out["model_seconds_per_turn"] = round(sum(c["seconds"] for c in calls) / len(done), 1)
            # What the player sits through: not the NPCs' own decisions, the folding of old
            # conversation or the 进展 page, which run beside the game (nor this eval's judge).
            waited = [c for c in calls if c["agent"] not in BACKGROUND]
            out["wait_seconds_per_turn"] = round(sum(c["seconds"] for c in waited) / len(done), 1)
            out["calls_per_turn"] = round(len(calls) / len(done), 1)
            # Each turn starts again from the save: an NPC woken once in play is woken again on every
            # replayed turn, so the backstage calls are not comparable with a game; what is, is how
            # many different (NPC, reason) wakes the whole run had.
            out["world_calls_not_comparable"] = sum(c["agent"] == "world" for c in calls)
        if any("woke" in x for x in done):
            out["woke_pairs"] = len({line for x in done for line in x.get("woke", [])})
        return out

    return {"recorded": side("recorded"), "replayed": side("replayed"), "failed": sum("error" in r.get("replayed", {}) for r in rows)}
