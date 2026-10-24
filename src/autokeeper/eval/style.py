"""Keeper and NPC style, measured against human fiction.

People who role-play on DeepSeek and Claude (SillyTavern, 酒馆) report two things: a list
of phrases to avoid makes the model use them more, and whatever the first replies do
carries through the whole game. So the prompts show the voice we want instead of a ban
list, and code does the counting:

    count     narration per 1,000 字: each pattern as a multiple of its rate in human fiction
              (Lu Xun, Lao She), and how far the lot goes past it; how much sentence lengths
              vary; the example passages' props turning up in a game that never had them;
              props carried over from the narration before
    voice     NPC lines: how many open with an action in brackets, and how many repeat an
              action or catchphrase from that NPC's two lines before
    run       a scripted game with a live narrator: plans, rulings, NPC replies and dice are
              experiment inputs; this measures prose, not the keeper's NPC direction
    talk      the same questions to three NPCs, the NPCs live and nothing else

Read from any file: a trace written with AUTOKEEPER_TRACE=full, an autoplay log, a save,
or what `run` and `talk` wrote. One run is a few thousand 字, where a pattern used once
more or less moves its rate by half a point, so compare five runs or more, with spread.

    autokeeper eval cliche dev/output/autoplay/x.jsonl data/games/x.json data/logs/traces/2026-10-18.jsonl
    autokeeper --preset deepseek eval style --label before --runs 5
    autokeeper eval style --npc --label before --runs 5
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import statistics
import warnings
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import lru_cache, partial
from itertools import pairwise
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from .. import config, paths, skills
from ..agents.cast import Action, Cast, Plan, Ruling
from ..agents.guard import Habits, habits, opens_with_action, repeats
from ..engine import graph as turn_graph
from ..engine.game import Game, Turn
from ..module.compiler import ensure_compiled
from ..module.schema import Module
from ..prefs import Prefs
from ..rules.bcdice import Roll
from ..rules.coc import Rules
from . import variants

with warnings.catch_warnings():  # jieba 0.42 predates Python 3.12's stricter string escapes
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba
    import jieba.posseg as pseg

jieba.setLogLevel(logging.WARNING)

# name -> (pattern, uses per 1,000 字 in human fiction). The human rate is the mean of Lu Xun's
# 22 stories in 呐喊 and 彷徨 (91,525 字) and the first eight chapters of Lao She's 骆驼祥子
# (39,040 字), from Wikisource in simplified characters, counted with these same patterns.
# Not every phrase role-players call stock is ours to avoid: both writers use 仿佛 and 似乎
# more often than our keepers do. A pattern counts against a narration only as far as it
# goes past the human rate.
PATTERNS: dict[str, tuple[str, float]] = {
    "不是……而是……": (r"(?<!是)(?:不是|并非)[^。！？!?\n]{1,30}?(?:而是|[，,…]+\s*是|——)", 0.119),  # also 不是X，是Y and 不是X——
    "一下，又一下": (r"一下，又一下|一下又一下|一下一下|一寸一寸|一点一点|半拍", 0.013),  # counting out the beats
    "破折号": (r"——", 1.781),
    "X 混着 Y 的气味": (r"混着[^，。！？\n]{1,12}?(?:气味|味道|味儿|气息|味)|(?:气味|味道|味儿)[^，。！？\n]{0,8}混在一起", 0.0),
    "静得能听见": (r"静得(?:能|可以)?听[见到得]|安静得能", 0.0),
    "一丝": (r"一丝(?!不苟|不挂|一毫)", 0.0),
    "喉结、指节发白": (r"喉结|指节[发泛]白", 0.0),
    "有什么东西": (r"有什么东西", 0.005),
    "说不清的": (r"说不清的|说不出的|难以言喻|莫名的", 0.022),
    "嘴唇动了动": (r"嘴唇动了动|张了张嘴", 0.0),
    "几乎听不见": (r"几乎听不见|几不可闻|低得听不见", 0.0),
    "空气里、扑面而来": (r"空气(?:中|里)|扑面而来", 0.017),
    "仿佛": (r"仿佛", 0.766),
    "似乎": (r"似乎", 1.164),
    "某种": (r"某种", 0.013),
    "弥漫": (r"弥漫", 0.011),
    "寒意": (r"寒意", 0.0),
    "难以名状": (r"难以名状|不可名状", 0.0),
    "下意识": (r"下意识", 0.0),
    "涟漪": (r"涟漪", 0.0),
    "不易察觉": (r"不易察觉|微不可察|几不可察", 0.0),
}
# Similes and guesses (像, 好像, 像是, 像在……), on their own and not in the headline: not wrong
# in themselves, but a keeper who reaches for one in every sentence is telling instead of
# showing. Lao She still wrote 象 for 像, and those are in the human rate.
SIMILE = (r"(?<![画雕肖塑图影头偶佛神人想录])像(?![样素])", 2.2)
FLOOR = 0.008  # per 1,000 字: one use in all 130,565 字, for what neither writer ever used
# How much sentence lengths vary (standard deviation over the mean) and the share of sentences
# of five 字 or fewer: Lu Xun 0.81 and 0.15, Lao She 0.69 and 0.07. Sentences end at 。！？….
HUMAN_CV, HUMAN_SHORT = 0.75, 0.11
RARE = 500  # jieba's dictionary count below which a noun is a prop, not a staple: 樟脑 70, 抽屉 560, 味道 2808
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SENTENCE = re.compile(r"[^。！？!?…\n]+[。！？!?…]*")


def han(text: str) -> int:
    """Chinese characters in a text: the 字 the rates are per thousand of."""
    return len(_HAN.findall(text))


def sentences(text: str) -> list[int]:
    """The length in 字 of each sentence."""
    return [n for n in (han(s) for s in _SENTENCE.findall(text)) if n]


@lru_cache(maxsize=512)
def props(text: str) -> frozenset[str]:
    """The rarer nouns of a text, what a passage is furnished with: words in jieba's dictionary
    (樟脑, 油布, 座钟), not the staples (门, 桌子, 味道) or words for where (缝里)."""
    return frozenset(
        p.word for p in pseg.lcut(text, HMM=False)
        if p.flag.startswith("n") and len(p.word) >= 2 and (jieba.get_FREQ(p.word) or 0) <= RARE
        and p.word[-1] not in "里上下中内外边旁"
    )  # fmt: skip


def borrowed(narrations: list[str], examples: Iterable[str], own: str = "") -> dict[str, int]:
    """The examples' props that turn up in the narration though nothing in this game has them
    (`own`: the module and what the keeper was told): a keeper furnishing every room with the
    樟脑 and 油布 of the passages it was shown. Word -> narrations it is in."""
    found = {w: sum(w in t for t in narrations) for w in props("\n".join(examples)) if w not in own}
    return dict(sorted(((w, n) for w, n in found.items() if n), key=lambda kv: (-kv[1], kv[0])))


def carried(narrations: list[str], own: str = "") -> dict[str, int]:
    """Props a narration takes over from the one before it though nothing in the game has them:
    the keeper's own details coming back turn after turn. Word -> narrations that repeat it."""
    out: Counter[str] = Counter()
    for before, now in pairwise(narrations):
        out.update(w for w in props(before) & props(now) if w not in own)
    return dict(out.most_common())


def count(narrations: Iterable[str], *, examples: Iterable[str] = (), own: str = "") -> dict[str, Any]:
    """Each pattern per 1,000 字 and as a multiple of the human rate; `excess`, the headline, is
    how many uses per 1,000 字 the patterns make past the human rates, all of them together."""
    texts = list(narrations)
    chars = sum(han(t) for t in texts)
    hits = {name: sum(len(re.findall(p, t)) for t in texts) for name, (p, _) in PATTERNS.items()}
    rate = {name: n * 1000 / chars if chars else 0.0 for name, n in hits.items()}
    lengths = [n for t in texts for n in sentences(t)]
    mean = statistics.mean(lengths) if lengths else 0.0
    similes = sum(len(re.findall(SIMILE[0], t)) for t in texts)
    return {
        "narrations": len(texts),
        "chars": chars,
        "hits": hits,
        "per_1000": {name: round(r, 2) for name, r in rate.items()},
        "ratio": {name: round(r / max(PATTERNS[name][1], FLOOR), 1) for name, r in rate.items()},
        "excess": round(sum(max(0.0, r - PATTERNS[name][1]) for name, r in rate.items()), 2),
        "similes": similes,
        "similes_per_1000": round(similes * 1000 / chars, 2) if chars else 0.0,
        "sentence_cv": round(statistics.pstdev(lengths) / mean, 2) if mean else 0.0,
        "short": round(sum(n <= 5 for n in lengths) / len(lengths), 3) if lengths else 0.0,
        "borrowed": borrowed(texts, examples, own),
        "carried": carried(texts, own),
    }


# -- NPC lines ---------------------------------------------------------------------------------
def voice(said: dict[str, list[str]], habit: dict[str, Habits] | None = None) -> dict[str, Any]:
    """NPC lines by speaker, in order: the share that open with an action, overall and from each
    NPC's second line on, and the share of those later lines that repeat one of the NPC's habits
    (habits: by name) from its two lines before."""
    habit = habit or {}
    lines = [t for ts in said.values() for t in ts]
    later = [(who, i) for who, ts in said.items() for i in range(1, len(ts))]

    def share(n: int, of: int) -> float:
        return round(n / of, 2) if of else 0.0

    again = [r for who, i in later if (r := repeats(said[who][i], said[who][max(0, i - 2) : i], habit.get(who, (frozenset(), ()))))]
    return {
        "lines": len(lines),
        "line_chars": round(sum(han(t) for t in lines) / len(lines)) if lines else 0,
        "action_first": share(sum(opens_with_action(t) for t in lines), len(lines)),
        "action_first_later": share(sum(opens_with_action(said[w][i]) for w, i in later), len(later)),
        "repeats": share(len(again), len(later)),
        "repeated": dict(Counter(x for r in again for x in r).most_common(8)),
    }


# -- reading files back ---------------------------------------------------------------------
_PLAYS = re.compile(r"扮演 NPC「([^」]+)」")


def _said(text: str) -> str:
    """An NPC's line from a traced reply: the JSON it answered, or the text itself."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    return data.get("text", "") if isinstance(data, dict) else text


def read(path: Path) -> tuple[list[str], dict[str, list[str]], str]:
    """The keeper's narration, the NPCs' lines by name in order, and the module id when the file
    says: a save (its log; older turns are folded into memory), an autoplay log (keeper_end and
    npc events; the module from its summary), a trace kept with AUTOKEEPER_TRACE=full, or what
    `run` and `talk` wrote."""
    text = path.read_text(encoding="utf-8")
    told: list[str] = []
    said: dict[str, list[str]] = {}
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return [], {}, ""
        if not isinstance(data, dict):
            return [], {}, ""
        if "told" in data or "said" in data:
            told = [t for t in data.get("told", []) if isinstance(t, str)]
            said = {k: [t for t in v if isinstance(t, str)] for k, v in data.get("said", {}).items() if isinstance(v, list)}
            return told, said, str(data.get("module", ""))
        for e in data.get("log", []):
            if isinstance(e, dict) and e.get("kind") == "keeper":
                told.append(e.get("text", ""))
            elif isinstance(e, dict) and e.get("kind") == "npc":
                said.setdefault(e.get("who", "?"), []).append(e.get("text", ""))
        return told, said, str(data.get("module", ""))
    module = ""
    summary = path.with_name(path.name.removesuffix(".jsonl") + ".summary.json")
    if summary.is_file():
        try:
            module = str(json.loads(summary.read_text(encoding="utf-8")).get("module", ""))
        except (ValueError, AttributeError):
            module = ""
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "keeper_end":
            told.append(row.get("text", ""))
        elif row.get("type") == "npc":
            said.setdefault(row.get("name") or row.get("npc", "?"), []).append(row.get("text", ""))
        elif row.get("output") and not row.get("error"):
            if row.get("agent") == "keeper":
                told.append(row["output"])
            elif row.get("agent") == "npc" and (who := _PLAYS.search(row.get("system", ""))):
                said.setdefault(who.group(1), []).append(_said(row["output"]))
    return told, said, module


def narration(path: Path) -> list[str]:
    """The keeper's narration in a file (read)."""
    return read(path)[0]


def module_of(module_id: str) -> Module | None:
    """The module a file was played on, by its id (a module's folder is named after it)."""
    for folder in (paths.MODULES / module_id, paths.LOCAL_MODULES / module_id, paths.EXAMPLE):
        if module_id and folder.name == module_id and (folder / "module.yaml").is_file():
            return Module.load(folder)
    return None


def examples(system: str) -> list[str]:
    """The example passages in a prompt (keeper-craft quotes them line by line with "> ")."""
    return [line[2:] for line in system.splitlines() if line.startswith("> ") and len(line) > 10]


def _own(m: Module | None, *more: str) -> str:
    return "\n".join([json.dumps(m.model_dump(mode="json"), ensure_ascii=False) if m else "", *more])


def score(path: Path) -> dict[str, Any]:
    """One file measured: its narration (count) and NPC lines (voice). The example passages are
    keeper-craft's as it is now; the props of the file's module do not count as borrowed."""
    told, said, module_id = read(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else {}
    except json.JSONDecodeError:
        data = {}
    data = data if isinstance(data, dict) else {}
    m = module_of(module_id)
    shown = data.get("examples") if isinstance(data.get("examples"), list) else examples(skills.body("keeper-craft"))
    out = count(told, examples=shown, own=_own(m, *data.get("reports", [])))
    names = {n.name: habits(n) for n in m.npcs} if m else {}
    return out | {"voice": voice(said, names) if said else None}


def spread(values: list[float]) -> tuple[float, float]:
    """Mean and standard deviation (0 for a single value)."""
    return statistics.mean(values), statistics.stdev(values) if len(values) > 1 else 0.0


# -- a scripted game with a live keeper -------------------------------------------------------
@dataclass
class Beat:
    say: str  # what the player types
    action: Action
    ruling: Ruling
    dice: tuple[int, ...] = ()  # what the checks come up as, in order
    npc: str = ""  # the NPC's answer, when the action talks to one
    told: tuple[str, ...] = ()  # explicit disclosures in that fixed answer, never extracted from its prose


def _beats_zhuishuren() -> list[Beat]:
    who = "哈罗德"
    return [
        # The game opens at the station, and a room is reached from its house: walk there first.
        Beat("我去金博尔家找托马斯",
             Action(kind="move", summary=f"{who}去艾尔斯伯里大街 218 号的金博尔家", destination="jinboer_jia"),
             Ruling(needs_roll=False, minutes=0)),
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
             npc="（铁铲往地上一杵）金博尔？好人。常陪我扯天气、扯政治，还讲外国的事。（朝东边一指）喏，那块矮碑，他老坐那儿看书。",
             told=("c_jefferson_tomb",)),
        Beat("我蹲下查看那块墓碑周围的地面",
             Action(kind="investigate", summary=f"{who}蹲下查看那块低矮墓碑周围的地面", target="墓碑周围的地面", echo=f"{who}在矮碑旁蹲下身"),
             Ruling(needs_roll=True, skill="侦查", clues_on_success=["c_footprints"], minutes=15), dice=(83,)),
        Beat("天黑以后，我躲在灌木后面盯着那块墓碑",
             Action(kind="wait", summary=f"{who}天黑后躲在灌木丛里监视那块墓碑", target="道格拉斯最喜欢的墓碑", echo=f"{who}缩进灌木丛的阴影里"),
             Ruling(needs_roll=False, set_flags=["watching"], minutes=600, rest=True)),
        Beat("我继续守到午夜过后",
             Action(kind="wait", summary=f"{who}继续守候，等待午夜过后的动静", target="道格拉斯最喜欢的墓碑"),
             Ruling(needs_roll=False, minutes=360, rest=True), dice=(20,)),
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
    """A live prose-only keeper; plans, outcomes and NPC replies are fixed experimental inputs.

    This deliberately does not measure production NPC direction or the HostedReply tool loop.
    Its narrator retains the normal Telling contract rather than wrapping model output.
    """

    def __init__(self, cfg: config.Config, module: Module, beats: list[Beat]):
        super().__init__(cfg, module)
        self.prefs = Prefs()  # the defaults: this measures the keeper, not one player's taste
        self.beats = {b.say: b for b in beats}
        self.now: Beat | None = None
        self.reports: list[str] = []  # what the keeper was told, turn by turn

    async def intent(self, state, text: str) -> Action:
        self.now = self.beats[text]
        return self.now.action

    async def plan(self, state, text: str) -> Plan:
        return Plan(action=await self.intent(state, text))

    async def arbiter(self, state, action: Action, at: str | None = None) -> Ruling:
        assert self.now is not None
        return self.now.ruling

    async def npc(self, state, npc_id: str, message: str) -> str:
        self.told[npc_id] = list(self.now.told) if self.now else []
        return self.now.npc if self.now and self.now.npc else "……"

    async def keeper(self, state, report: str, on_delta=None, lore: str = "", *, check=None, fix: str = ""):
        if not self.reports or self.reports[-1] != report:  # a scene sent back is the same report told again
            self.reports.append(report)
        return await super().keeper(state, report, on_delta=on_delta, lore=lore, check=check, fix=fix)

    async def lore(self, state, query: str, keywords: list[str] | None = None, max_tokens: int = 1500) -> str:
        return ""


class _ScriptedGame(Game):
    """Settle the script's post-check reply before measuring any remaining narration.

    Production delegates checked dialogue to the keeper's tool loop. Here the speaker and
    answer are fixed inputs, so the ordinary reply receipt is settled after resolve and before
    talk's event/world tail. An already complete dialogue still needs no narrated repetition.
    """

    async def talk(self, turn: Turn) -> None:
        cast = self.cast
        if (isinstance(cast, Scripted) and cast.now is not None and cast.now.npc
                and (turn.primary is not None or turn.earlier is not None)
                and (npc := self._addressed(turn.action)) and npc in self._present()
                and not turn.declared and not self.fought_this_turn()):
            key = f"talk:{npc}"
            if key in turn.replies or not any(nid == npc for nid, _ in turn.npc_lines):
                await self._reply(turn, key, npc,
                                  lambda: cast.npc(self.state, npc, self._npc_message(turn, npc)), self._keep,
                                  record=partial(self._exchanged, npc, turn.action))
        await super().talk(turn)


def _write(out: Path | None, summary: dict[str, Any]) -> None:
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


async def run(module_dir: Path, *, label: str = "", out: Path | None = None) -> dict[str, Any]:
    """Measure narration with fixed plans, checks and NPC replies, not production direction."""
    m = Module.load(module_dir)
    if m.id not in SCRIPTS:
        raise ValueError(f"没有《{m.title}》的脚本（有：{'、'.join(SCRIPTS)}）")
    ensure_compiled(m, module_dir)
    beats = SCRIPTS[m.id]()
    cfg = replace(config.load(), improv="严格", visual_limit=0, imagegen=False, showcase=False)
    cast = Scripted(cfg, m, beats)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot=f"style-{m.id}-{label or 'run'}", human=[])
    told: list[str] = []
    said: dict[str, list[str]] = {}

    async def emit(ev: dict) -> None:
        if ev.get("type") == "keeper_end":
            told.append(ev["text"])
        elif ev.get("type") == "npc":
            # Offline measurements group by actual speaker, independently of player
            # aliases or descriptions that several unknown people may share.
            said.setdefault(m.npc(ev["npc"]).name, []).append(ev["text"])

    game = _ScriptedGame(m, state, cast, Rules(dice=Loaded(d for b in beats for d in b.dice)), emit=emit)  # type: ignore[arg-type]
    app = turn_graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": state.slot}}
    try:
        await game.start()
        for b in beats:
            inp: Any = {"input": b.say, "by_hand": False}
            while True:
                offer = None
                async for mode, chunk in turn_graph.stream(app, game, inp, thread, stream_mode=["custom", "updates"]):
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
        await game.close()
        await cast.close()
    keeper = cfg.role("keeper")
    shown = examples(system)
    summary = {
        "module": m.id,
        "label": label,
        "keeper": f"{keeper.model}@{keeper.effort or 'none'}",
        "prompt": hashlib.sha1(system.encode("utf-8")).hexdigest()[:10],  # which keeper prompt told it
        "variant": variants.current(),  # eval --prompts: which prompt variant, if any
        **count(told, examples=shown, own=_own(m, *cast.reports)),
        "lengths": [han(t) for t in told],
        "usage": state.usage.get("keeper", {}),
        "spent": round(sum(u.get("cost_usd", 0.0) for u in state.usage.values()), 6),  # with any summaries
        "told": told,
        "said": said,
        "reports": cast.reports,
        "examples": shown,
    }
    _write(out, summary)
    return summary


# -- the same questions to three NPCs ---------------------------------------------------------
def _talk_zhuishuren() -> list[tuple[str, list[str]]]:
    return [
        ("thomas", ["您好，金博尔先生，我是哈罗德·布伦南，收到您的信就赶过来了。", "您叔叔平时是个什么样的人？",
                    "他失踪前那几天，有没有什么反常的地方？", "这房子您打算怎么处理？"]),
        ("jefferson", ["下午好，您就是这儿的守墓人吧？", "您在这公墓干了多少年了？",
                       "听说道格拉斯·金博尔先生以前常来这儿读书？", "这活儿冬天不好干吧？"]),
        ("odell", ["您好，夫人，打扰了。我叫哈罗德·布伦南，替金博尔家办点事。", "您在这条街上住了很久了吧？",
                   "您认识道格拉斯·金博尔先生吗？", "这一带晚上太平吗？"]),
    ]  # fmt: skip


TALKS = {"zhuishuren": _talk_zhuishuren}


def _probe_zhuishuren() -> list[tuple[str, list[str]]]:
    """Requests an NPC answers with a field as well as words: going along (accompany), handing a thing
    over (items), walking off (leave)."""
    return [
        ("thomas", ["金博尔先生，天黑之前，您陪我去后面的公墓走一趟吧。", "屋里有手电吗？借我一只，晚上用得着。",
                    "我先去书房看看，您忙您的。"]),
        ("jefferson", ["您这把铁铲能借我用一会儿吗？就在这儿用，马上还您。", "您能领我去道格拉斯先生常坐的那块墓碑吗？",
                       "天快黑了，您是不是要回小屋了？"]),
        ("odell", ["夫人，您能陪我去金博尔家一趟吗？就几步路。", "您篮子里有火柴吗？能给我一盒吗？", "不耽误您了，您先回家吧。"]),
    ]  # fmt: skip


PROBES = {"zhuishuren": _probe_zhuishuren}


async def talk(module_dir: Path, *, label: str = "", out: Path | None = None, probe: bool = False) -> dict[str, Any]:
    """Ask the module's NPCs the same questions with the configured NPC model, through the game's
    own NPC call (its leak check included), and measure how they talk. No checks, no keeper.
    `probe`: the requests of PROBES instead, and what the replies filled in (eval/control.py)."""
    from .control import Watch

    m = Module.load(module_dir)
    script = PROBES if probe else TALKS
    if m.id not in script:
        raise ValueError(f"没有《{m.title}》的问话（有：{'、'.join(script)}）")
    # Measure the same twelve replies. Autonomous decisions and illustration calls
    # would change both the scene and the model-call count of this voice experiment.
    cfg = replace(config.load(), improv="严格", visual_limit=0, imagegen=False, showcase=False)
    cast = Cast(cfg, m)
    cast.prefs = Prefs()
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot=f"talk-{m.id}-{label or 'run'}", human=[])
    game = Game(m, state, cast, Rules(dice=Loaded(())))  # type: ignore[arg-type]
    watch = Watch()
    watch.attach(cast, game)
    inv = state.investigator
    said: dict[str, list[str]] = {}
    asked: dict[str, list[str]] = {}
    try:
        for npc_id, questions in script[m.id]():
            npc = m.npc(npc_id)
            state.location = npc.location or state.location
            for q in questions:
                state.turn += 1
                state.add("player", q, who=inv.name)
                turn = Turn(action=Action(kind="talk", summary=f"{inv.name}和{npc.name}说话", npc=npc_id, speech=q))
                await game.talk(turn)
                said.setdefault(npc.name, []).append(turn.npc_lines[-1][1])
                asked.setdefault(npc.name, []).append(q)
        system = "".join(s.system for s in cast.sessions.values())
    finally:
        await game.close()
        await cast.close()
    role = cfg.role("npc")
    summary = {
        "module": m.id,
        "label": label,
        "npc": f"{role.model}@{role.effort or 'none'}",
        "prompt": hashlib.sha1(system.encode("utf-8")).hexdigest()[:10],  # which NPC prompts answered
        "variant": variants.current(),
        **voice(said, {n.name: habits(n) for n in m.npcs}),
        "usage": state.usage.get("npc", {}),
        "spent": round(sum(u.get("cost_usd", 0.0) for u in state.usage.values()), 6),
        "said": said,
        "asked": asked,
    }
    if probe:
        checked = watch.result(m)
        replies = checked["npc"]["detail"]
        summary["fields"] = {"replies": replies, "said_missing": checked["fields"]["said_missing"],
                             "filled": sum(bool(r["fields"].get(f)) for r in replies for f in ("accompany", "items", "leave")),
                             "said": sum(len(r["said"]) for r in replies), "reasks": checked["npc"]["reasks"],
                             "unsettled": checked["npc"]["unsettled"]}
    _write(out, summary)
    return summary
