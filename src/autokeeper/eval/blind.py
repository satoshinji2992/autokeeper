"""Blind A/B: two sets of runs of the same script, compared pair by pair by a judge model that
is not told which is which.

A prompt change stays only if it reads better, not because a counter likes it. Run i of A is
paired with run i of B. A pair is one narration of the same beat (with the narration before
it, so a repeat shows), or one NPC's whole exchange. It is judged twice, A as 甲 and then B
as 甲; a verdict that turns with the order is a tie. Wins go to a sign test, ties left out.

    autokeeper eval blind --a before --b after           # dev/output/eval/style-before-*.json vs style-after-*.json
    autokeeper eval blind --a before --b after --npc     # talk-*.json
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .. import config, paths
from ..config import route
from ..harness.models import ModelPool
from .style import module_of


class Verdict(BaseModel):
    better: Literal["甲", "乙", "相当"] = Field(description="哪一份更好；不分高下填 相当")
    why: str = Field("", description="一句话理由")


KEEPER_JUDGE = """你在给跑团叙事做盲评。两位守秘人拿到同一份情况，各写了一段给玩家看的叙述。判断哪一段写得更好。

好的叙述：
- 像有经验的中文作者写的：具体、自然，读起来不像套话，也不像翻译腔；
- 情况里的事实照实交代（检定结果、线索原文、NPC 的话），不添不漏，不替玩家行动或下结论；
- 不重复它前一段已经用过的词、画面和句式；
- 场景与结果清楚，下一步由玩家决定；当前问题可以完整解决，平静场景可以自然结束。
不因额外悬念、危机、感官描写或行动邀请加分。篇幅相近时不因长短加分。两段不分高下就填“相当”。"""

NPC_JUDGE = """你在给跑团里的 NPC 台词做盲评。同一个 NPC 被问了同样几句话，两份回答各自连成一段对话。判断哪一份演得更好。

好的 NPC：
- 回应玩家实际表达的需要与条件，口吻符合人物身份、关系、目标和当前场合；
- 人物保有立场，接受或拒绝有根据；不因玩家谢绝请求就自动敌视、纠缠或替玩家决定；
- 亲历、转述与判断有区别，不编造资料之外的重要事实、消息来源或交往经历。
有无口头禅、情绪、动作或个人往事本身不加分。只根据给定资料和对话判断；两份不分高下就填“相当”。"""


def runs(kind: str, label: str) -> list[dict[str, Any]]:
    """What eval style wrote for a label, in run order: style-<label>-1.json, -2, ..."""
    out = []
    while (f := paths.dev_output("eval", f"{kind}-{label}-{len(out) + 1}.json")).is_file():
        out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def sign_test(wins: int, losses: int) -> float:
    """Two-sided p of a split this uneven between two equally good sides."""
    n = wins + losses
    if not n:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(max(wins, losses), n + 1)) / 2**n
    return min(1.0, 2 * tail)


def _keeper_pairs(a: list[dict], b: list[dict]) -> list[dict[str, Any]]:
    pairs = []
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        reports = x.get("reports") or [""] * len(x["told"])
        for j, (one, two) in enumerate(zip(x["told"], y["told"], strict=False)):
            pairs.append({
                "run": i + 1, "beat": j + 1, "report": reports[j] if j < len(reports) else "",
                "a": one, "b": two, "a_before": x["told"][j - 1] if j else "", "b_before": y["told"][j - 1] if j else "",
            })  # fmt: skip
    return pairs


def _npc_pairs(a: list[dict], b: list[dict], module: str) -> list[dict[str, Any]]:
    m = module_of(module)
    people = {n.name: n for n in m.npcs} if m else {}
    pairs = []
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        for name, asked in x.get("asked", {}).items():
            n = people.get(name)
            who = f"{name}：{n.role}；{n.personality}；目标：{n.goal}；说话方式：{n.voice}；外貌：{n.look}" if n else name
            pairs.append({"run": i + 1, "npc": name, "who": who, "asked": asked, "a": x["said"].get(name, []), "b": y["said"].get(name, [])})
    return pairs


def _keeper_prompt(p: dict, first: str) -> str:
    one, two = (("a", "b") if first == "a" else ("b", "a"))
    return (
        f"【情况（守秘人看到的，玩家看不到）】\n{p['report'] or '（未记录）'}\n\n"
        f"【甲的前一段】{p[one + '_before'] or '（这是开场）'}\n【甲】{p[one]}\n\n"
        f"【乙的前一段】{p[two + '_before'] or '（这是开场）'}\n【乙】{p[two]}"
    )


def _npc_prompt(p: dict, first: str) -> str:
    def exchange(lines: list[str]) -> str:
        return "\n".join(f"问：{q}\n答：{t}" for q, t in zip(p["asked"], lines, strict=False))

    one, two = (p["a"], p["b"]) if first == "a" else (p["b"], p["a"])
    return f"【人物】{p['who']}\n\n【甲】\n{exchange(one)}\n\n【乙】\n{exchange(two)}"


async def judge(a: str, b: str, *, npc: bool = False, model: str = "claude-opus-5-5", out: Path | None = None) -> dict[str, Any]:
    """Every pair of runs `a` and `b` judged both ways round; B's wins, A's wins, ties and the p."""
    kind = "talk" if npc else "style"
    first, second = runs(kind, a), runs(kind, b)
    if not first or not second:
        raise ValueError(f"没找到 dev/output/eval/{kind}-{a if not first else b}-1.json")
    pairs = _npc_pairs(first, second, first[0].get("module", "")) if npc else _keeper_pairs(first, second)
    backend, name = route(model)
    pool = ModelPool(config.Role(backend, name, "medium"), size=4, label="judge")
    system = NPC_JUDGE if npc else KEEPER_JUDGE
    prompt = _npc_prompt if npc else _keeper_prompt
    schema = Verdict.model_json_schema()

    async def ask(p: dict, lead: str) -> tuple[str, str]:
        reply = await pool.complete(system, [{"role": "user", "content": prompt(p, lead)}], schema=schema)
        said = Verdict.model_validate(reply.data) if isinstance(reply.data, dict) else Verdict(better="相当")
        return {"甲": lead, "乙": "b" if lead == "a" else "a"}.get(said.better, "tie"), said.why

    try:
        both = await asyncio.gather(*(asyncio.gather(ask(p, "a"), ask(p, "b")) for p in pairs))
    finally:
        await pool.close()
    verdicts = []
    for p, ((x, why_x), (y, why_y)) in zip(pairs, both, strict=True):
        verdicts.append({**{k: v for k, v in p.items() if k in ("run", "beat", "npc")},
                         "orders": [x, y], "verdict": x if x == y else "tie", "why": [why_x, why_y]})  # fmt: skip
    wins = sum(v["verdict"] == "b" for v in verdicts)
    losses = sum(v["verdict"] == "a" for v in verdicts)
    summary = {
        "a": a, "b": b, "kind": kind, "judge": model, "pairs": len(pairs),
        "b_wins": wins, "a_wins": losses, "ties": len(pairs) - wins - losses, "p": round(sign_test(wins, losses), 4),
        "verdicts": verdicts,
    }  # fmt: skip
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
