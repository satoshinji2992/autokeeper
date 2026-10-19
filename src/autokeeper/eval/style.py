"""Keeper style: how often the narration leans on stock phrases.

People who role-play on DeepSeek report two things: a list of phrases to avoid makes the
model use them more, and the first reply's style carries through the whole game. So the
keeper is shown two short narrations in the voice we want (skills/keeper-craft) instead
of a ban list, and this counts what we did not want, per 1,000 字 of keeper narration:

    count     hedges and stock turns (不是……而是……, 仿佛, 一丝, 某种, 似乎, ...) in any
              narration: a trace written with AUTOKEEPER_TRACE=full, an autoplay log, a save
    run       a scripted game with a live keeper: the same actions, rulings, NPC lines and
              dice every time, the director off, so two runs differ only in the telling

    autokeeper eval cliche saves/autoplay/x.jsonl saves/x.json saves/traces/2026-10-18.jsonl
    autokeeper --preset deepseek eval style --label before
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .. import config
from ..agents.cast import Action, Cast, Ruling
from ..engine import graph as turn_graph
from ..engine.game import Game
from ..module.compiler import compile_module, skill_name
from ..module.schema import Module
from ..prefs import Prefs
from ..rules.bcdice import Roll
from ..rules.coc import Rules

# name -> pattern. The first five are the headline; the rest are counted alongside.
CLICHES: dict[str, str] = {
    "不是……而是……": r"(?<!是)不是[^。！？!?\n]{1,30}?(?:而是|[，,…]+\s*是|——)",  # also 不是X，是Y and 不是X——
    "仿佛": "仿佛",
    "一丝": "一丝",
    "某种": "某种",
    "似乎": "似乎",
    "弥漫": "弥漫",
    "寒意": "寒意",
    "难以名状": "难以名状|不可名状",
    "下意识": "下意识",
    "喉结": "喉结",
    "指节发白": "指节[发泛]白",
    "涟漪": "涟漪",
    "不易察觉": "不易察觉|微不可察|几不可察",
}
CORE = ("不是……而是……", "仿佛", "一丝", "某种", "似乎")
# Similes and guesses (像, 好像, 像是, 像在……), counted on their own: not wrong in themselves,
# but a keeper who reaches for one in every sentence is telling instead of showing.
SIMILE = re.compile(r"(?<![画雕肖塑图影头偶佛神人想录])像(?![样素])")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def han(text: str) -> int:
    """Chinese characters in a text: the 字 the densities are per thousand of."""
    return len(_HAN.findall(text))


def count(narrations: Iterable[str]) -> dict[str, Any]:
    """Hits per phrase and per 1,000 字; `density` is the headline (CORE) per 1,000 字."""
    texts = list(narrations)
    chars = sum(han(t) for t in texts)
    hits = {name: sum(len(re.findall(p, t)) for t in texts) for name, p in CLICHES.items()}

    def per_k(n: int) -> float:
        return round(n * 1000 / chars, 2) if chars else 0.0

    core = sum(hits[n] for n in CORE)
    similes = sum(len(SIMILE.findall(t)) for t in texts)
    return {
        "narrations": len(texts),
        "chars": chars,
        "hits": hits,
        "per_1000": {name: per_k(n) for name, n in hits.items()},
        "core": core,
        "density": per_k(core),
        "all": per_k(sum(hits.values())),
        "similes": similes,
        "similes_per_1000": per_k(similes),
    }


def narration(path: Path) -> list[str]:
    """The keeper's narration in a file: a save (its log; older turns are folded into
    memory), an autoplay log (keeper_end events), a trace kept with AUTOKEEPER_TRACE=full,
    or what `run` wrote."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        if "told" in data:
            return [t for t in data["told"] if isinstance(t, str)]
        return [e["text"] for e in data.get("log", []) if isinstance(e, dict) and e.get("kind") == "keeper"]
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "keeper_end":
            out.append(row.get("text", ""))
        elif row.get("agent") == "keeper" and row.get("output") and not row.get("error"):
            out.append(row["output"])
    return out


# -- a scripted game with a live keeper -------------------------------------------------------
@dataclass
class Beat:
    say: str  # what the player types
    action: Action
    ruling: Ruling
    dice: tuple[int, ...] = ()  # what the checks come up as, in order
    npc: str = ""  # the NPC's answer, when the action talks to one


def _beats_zhuishuren() -> list[Beat]:
    who = "哈罗德"
    return [
        Beat("我去书房看看叔叔的书架",
             Action(kind="investigate", summary=f"{who}走进道格拉斯的书房，查看书架上的空档", target="书架上的空档",
                    destination="shufang", echo=f"{who}推开书房的门"),
             Ruling(needs_roll=False, auto_clues=["c_shelf_gap"], minutes=15)),
        Beat("我检查书房窗户的窗锁",
             Action(kind="investigate", summary=f"{who}检查书房窗户的窗锁", target="书房窗户", echo=f"{who}走到窗前，伸手去拨窗闩"),
             Ruling(needs_roll=False, auto_clues=["c_window"], minutes=10)),
        Beat("我拉开旧书桌的抽屉翻找",
             Action(kind="investigate", summary=f"{who}拉开旧书桌的抽屉翻找", target="旧书桌", echo=f"{who}拉开书桌最上面的抽屉"),
             Ruling(needs_roll=True, skill="侦查", clues_on_success=["c_diary"], minutes=20), dice=(24,)),
        Beat("我回客厅问托马斯，叔叔失踪前有没有什么反常",
             Action(kind="talk", summary=f"{who}回到客厅，问托马斯叔叔失踪前有没有什么反常", npc="thomas",
                    speech="您叔叔失踪之前，有没有什么反常的地方？", destination="jinboer_jia", echo=f"{who}回到客厅，在托马斯对面坐下"),
             Ruling(needs_roll=False, minutes=15),
             npc="（搓着手）说实在的，一点征兆也没有。头天晚上他还在书房看书，第二天一早人就不见了。他那本日记一直搁在书房里，您尽管翻。"),
        Beat("我去后面的公墓转转",
             Action(kind="move", summary=f"{who}去屋后的公墓", destination="mudi", echo=f"{who}顺着屋后的小路往公墓走"),
             Ruling(needs_roll=False, minutes=0)),
        Beat("我向守墓人打听道格拉斯常坐哪块墓碑",
             Action(kind="talk", summary=f"{who}和守墓人套近乎，打听道格拉斯常坐哪块墓碑", npc="jefferson",
                    speech="听说道格拉斯先生以前常来这儿读书，他一般坐在哪儿？", approach="魅惑", echo=f"{who}给守墓人递过去一支烟"),
             Ruling(needs_roll=True, skill="魅惑", clues_on_success=["c_jefferson_tomb"], minutes=15), dice=(9,),
             npc="（铁铲往地上一杵）金博尔？好人。常陪我扯天气、扯政治，还讲外国的事。（朝东边一指）喏，那块矮碑，他老坐那儿看书。"),
        Beat("我蹲下查看那块墓碑周围的地面",
             Action(kind="investigate", summary=f"{who}蹲下查看那块低矮墓碑周围的地面", target="墓碑周围的地面", echo=f"{who}在矮碑旁蹲下身"),
             Ruling(needs_roll=True, skill="侦查", clues_on_success=["c_footprints"], minutes=15), dice=(83,)),
        Beat("天黑以后，我躲在灌木后面盯着那块墓碑",
             Action(kind="wait", summary=f"{who}天黑后躲在灌木丛里监视那块墓碑", target="道格拉斯最喜欢的墓碑", echo=f"{who}缩进灌木丛的阴影里"),
             Ruling(needs_roll=False, set_flags=["watching"], minutes=600, rest=True)),
    ]  # fmt: skip


SCRIPTS = {"zhuishuren": _beats_zhuishuren}


class Loaded:
    """Dice that come up as scripted (BCDice's wording, so checks read them as usual);
    plain rolls come from a fixed seed."""

    def __init__(self, values: Iterable[int]):
        self.values = deque(values)
        self.rng = random.Random(1922)

    def check(self, target: int, bonus: int = 0) -> Roll:
        v = self.values.popleft() if self.values else 50
        fumble = v >= (100 if target >= 50 else 96)
        if v == 1:
            label = "大成功"
        elif fumble:
            label = "大失敗"
        elif v <= target:
            label = "極限成功" if v <= target // 5 else "困難成功" if v <= target // 2 else "一般成功"
        else:
            label = "失敗"
        text = f"(1D100<={target}) 獎勵・懲罰骰[{bonus}] ＞ {v} ＞ {v} ＞ {label}"
        return Roll(f"CC<={target}", text, success=v <= target and not fumble, failure=v > target or fumble)

    def total(self, expr: str) -> int:
        m = re.fullmatch(r"\s*(\d*)[dD](\d+)\s*([+-]\s*\d+)?\s*", expr)
        if m is None:
            return int(expr)
        n, sides, plus = m.groups()
        return sum(self.rng.randint(1, int(sides)) for _ in range(int(n or 1))) + int((plus or "0").replace(" ", ""))

    def roll(self, expr: str) -> Roll:
        return Roll(expr, f"({expr}) ＞ {self.total(expr)}")

    def table(self, name: str) -> str:
        return f"（{name}）"


class Scripted(Cast):
    """The real keeper; whatever decides what happens comes from the script."""

    def __init__(self, cfg: config.Config, module: Module, beats: list[Beat]):
        super().__init__(cfg, module)
        self.prefs = Prefs()  # the defaults: this measures the keeper, not one player's taste
        self.beats = {b.say: b for b in beats}
        self.now: Beat | None = None

    async def intent(self, state, text: str) -> Action:
        self.now = self.beats[text]
        return self.now.action

    async def arbiter(self, state, action: Action, at: str | None = None) -> Ruling:
        assert self.now is not None
        return self.now.ruling

    async def npc(self, state, npc_id: str, message: str) -> str:
        return self.now.npc if self.now and self.now.npc else "……"

    async def lore(self, state, query: str, keywords: list[str] | None = None, max_tokens: int = 1500) -> str:
        return ""


async def run(module_dir: Path, *, label: str = "", out: Path | None = None) -> dict[str, Any]:
    """Play the module's script once with the configured keeper and count its narration."""
    m = Module.load(module_dir)
    if m.id not in SCRIPTS:
        raise ValueError(f"没有《{m.title}》的脚本（有：{'、'.join(SCRIPTS)}）")
    if not (config.SKILLS / skill_name(m) / "SKILL.md").exists():
        compile_module(m, config.SKILLS)
    beats = SCRIPTS[m.id]()
    cfg = replace(config.load(), improv="严格", imagegen=False, showcase=False)  # the director would write different scenes each time
    cast = Scripted(cfg, m, beats)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot=f"style-{m.id}-{label or 'run'}", human=[])
    told: list[str] = []

    async def emit(ev: dict) -> None:
        if ev.get("type") == "keeper_end":
            told.append(ev["text"])

    game = Game(m, state, cast, Rules(dice=Loaded(d for b in beats for d in b.dice)), emit=emit)  # type: ignore[arg-type]
    app = turn_graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": state.slot}}
    try:
        await game.start()
        for b in beats:
            inp: Any = {"input": b.say, "by_hand": False}
            while True:
                offer = None
                async for mode, chunk in app.astream(inp, thread, stream_mode=["custom", "updates"]):
                    if mode == "custom":
                        await emit(chunk)
                    elif isinstance(chunk, dict) and "__interrupt__" in chunk:
                        offer = chunk["__interrupt__"][0].value
                if offer is None:
                    break
                inp = Command(resume="drop")  # a failed roll stays failed
            game.emit = emit
        system = cast.sessions["keeper"].system
    finally:
        await cast.close()
    keeper = cfg.role("keeper")
    summary = {
        "module": m.id,
        "label": label,
        "keeper": f"{keeper.model}@{keeper.effort or 'none'}",
        "prompt": hashlib.sha1(system.encode("utf-8")).hexdigest()[:10],  # which keeper prompt told it
        **count(told),
        "lengths": [han(t) for t in told],
        "usage": state.usage.get("keeper", {}),
        "spent": round(sum(u.get("cost_usd", 0.0) for u in state.usage.values()), 6),  # with any summaries
        "told": told,
    }
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
