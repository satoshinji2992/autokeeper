"""The Idea roll (灵感检定, Keeper Rulebook pp. 82, 167–169): the way out of a dead end.

The players ask for it (or the keeper offers it when the game stalls, Game._nudge). The engine
picks the most crucial clue the party has missed, sets the roll's difficulty by how present that
clue already was at the table, and the investigator with the highest INT rolls INT. Either way
the clue is gained: success lets the keeper weave it in as the investigators' own realisation;
failure costs time and the director cuts straight to the worst situation they can recognise
(千钧一发, p. 169), the clue showing in it. Everything here is deterministic and reads the state
only; Game settles the roll (Game._idea).

Which clue (`pick`): one not yet held whose `requires` are met; a core clue (Clue.core) first;
among those, the one supporting the conclusion the party is nearest to (the share of its
supporting clues already held, module/graph.supporting_clues), then the module's order. No core
clue missing: the best non-core one by the same measure.

How present it was (`presence`, counted after every turn by `note` from the log, so it outlives
the log being folded into the chronicler's memory). Only the clue itself counts (p. 168: whether
it was mentioned, stressed or discussed), never where it lies or who holds it: a line carries the
clue when it shares a run of SAME_RUN characters with the clue's own words (its text, its
handout's), the names in them left out (people, places, the clue's title and its feature).
  - mentioned: a telling (the keeper's narration, an NPC's line, an event) carried it;
  - stressed: tellings carried it STRESSED times or more, or the players' own inputs did (they
    discussed it themselves).
Being at its place, meeting its NPC or naming either is no mention: the players cannot have
missed what they were never shown.
Difficulty (p. 168): never mentioned, regular; mentioned but not stressed, hard (regular again
when the last mention is LONG_AGO game days back); stressed or discussed, extreme.

How often: once per game day for the party; a roll the keeper offered on a stall does not count
and is not held back by one made that day. The day limit and LONG_AGO are this project's
settings, not the book's (docs/rules-reference.md). The keeper decides how much time a failure
actually takes (Cast.idea_failure); no fixed time penalty is imposed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..module import graph as module_graph
from ..module.schema import Clue, Module
from ..rules.coc import Investigator
from . import audience
from .state import GameState, IdeaRoll, Mention, parse_time

WORDS = ("/灵感", "/idea")  # what asks for it in the input box, at a LAN table, from an AI player
STRESSED = 3  # tellings that carry a clue before it counts as stressed
# Project settings, not the book's: p. 168's "long ago" is weeks of real time, p. 169 only says time passes.
LONG_AGO = 2  # game days since its last mention after which a hard roll is regular again
SAME_RUN = 7  # characters in a row a line shares with a clue's own words to carry it (six match 是个和蔼的人)
TOLD = ("opening", "keeper", "npc", "event")  # log lines that tell the table something
SAID = ("player", "speech")  # the players' own words: their actions and what they said to one another
_BREAK = re.compile(r"[\s\W_]+")  # punctuation and spaces: a run of the clue's words never crosses them
_DOT = re.compile(r"[·•・]")
# A transliterated name, 道格拉斯·金博尔, though the module has no one by it: a few characters either side of the dot
# (Chinese has no spaces to say where it ends; one more character gone only shortens the run around it).
# A title in 《》 (a newspaper, a book) is a name too.
_FOREIGN = re.compile(r"\w{1,4}(?:[·•・]\w{1,4})+|《[^》]*》")

OFFER = "守秘人提议做一次灵感检定：按 Ctrl+L 或输入 /灵感。"
NOTHING = "目前没有被遗漏的线索。"
TODAY = "今天已经做过一次灵感检定。每个游戏日限一次；调查停滞时守秘人会另行提议。"
ENDED = "本局已结束。"
FIGHTING = "战斗或追逐中不能做灵感检定。"
# What a player is shown under the roll: why it is this hard, without naming anything.
BASIS = {
    "常规": "这条思路此前没有出现过，或已是很久以前的事",
    "困难": "这条思路此前出现过",
    "极难": "这条思路已被反复提起或讨论过",
}


def asked(text: str) -> bool:
    return text.strip().lower() in WORDS


@dataclass(frozen=True)
class Presence:
    mentioned: bool
    stressed: bool
    last: int  # game minutes of the last mention; 0 if none


def _names(m: Module, st: GameState, c: Clue) -> list[str]:
    """What only names where the clue is or whom it is about: people, places, the clue's title and feature.
    A full name's parts too (道格拉斯 of 道格拉斯·金博尔); the longest first, so a name goes whole."""
    names = [c.title, c.feature, *(n.name for n in m.npcs), *(i.name for i in [*m.pregens, *st.investigators])]
    for loc in m.locations:
        names += [loc.name, *loc.aliases]
    names += [part for n in list(names) if _DOT.search(n) for part in _DOT.split(n)]
    return sorted({n.strip() for n in names if len(n.strip()) >= 2}, key=len, reverse=True)


def _runs(text: str) -> set[str]:
    """Every SAME_RUN characters in a row of a text, none across punctuation."""
    return {piece[i:i + SAME_RUN] for piece in _BREAK.split(text) for i in range(len(piece) - SAME_RUN + 1)}


def words(m: Module, st: GameState, c: Clue) -> set[str]:
    """The clue's own words, as runs a line has to share to carry it: its text and its handout's, with the
    names in them taken out (a line naming only the place, the person or the clue's title carries nothing)."""
    text = c.text
    if c.handout and any(h.id == c.handout for h in m.handouts):
        text += "\n" + m.handout(c.handout).text
    for name in _names(m, st, c):
        text = text.replace(name, "\n")
    return _runs(_FOREIGN.sub("\n", text))


def note(m: Module, st: GameState) -> None:
    """Count what came up since the last count: the log lines added since (by GameState.logged) that
    carry a missed clue's own words. Called after each turn (Game.remember) and before a roll;
    counting twice adds nothing."""
    fresh = st.logged - st.mentions_at
    lines = st.log[-fresh:] if 0 < fresh <= len(st.log) else (list(st.log) if fresh > 0 else [])
    lines = [(e, _runs(e.text)) for e in lines if e.kind in TOLD + SAID and audience.public(e)]
    st.mentions_at = st.logged
    if not lines:
        return
    now = st.minutes
    for c in m.clues:
        if c.id in st.clues:
            continue
        runs = words(m, st, c)
        for e, said in lines:
            if runs.isdisjoint(said):
                continue
            try:
                at = parse_time(e.time)
            except ValueError:
                at = now
            mark = _touch(st, c.id, at)
            if e.kind in SAID:
                mark.said += 1
            else:
                mark.told += 1


def _touch(st: GameState, clue_id: str, at: int) -> Mention:
    mark = st.mentions.get(clue_id)
    if mark is None:
        mark = st.mentions[clue_id] = Mention(first=at, last=at)
    mark.last = max(mark.last, at)
    return mark


def presence(st: GameState, clue_id: str) -> Presence:
    mark = st.mentions.get(clue_id)
    if mark is None:
        return Presence(False, False, 0)
    return Presence(mark.told > 0 or mark.said > 0, mark.told >= STRESSED or mark.said > 0, mark.last)


def difficulty(st: GameState, clue_id: str) -> str:
    """The roll's difficulty by how present the clue was (p. 168): the more plainly it was put before
    the players, the harder; mentioned long ago counts as never."""
    p = presence(st, clue_id)
    if p.stressed:
        return "极难"
    if p.mentioned and st.minutes - p.last < LONG_AGO * 1440:
        return "困难"
    return "常规"


def nearness(m: Module, st: GameState) -> list[str]:
    """The open conclusions, the one the party is nearest to first: the share of its supporting
    clues they hold, then how many, then the module's order."""
    support = module_graph.supporting_clues(m)
    order = {k.id: i for i, k in enumerate(m.conclusions)}
    held = set(st.clues)
    open_ = [k for k, ids in support.items() if ids and not set(ids) <= held]
    return sorted(open_, key=lambda k: (-sum(c in held for c in support[k]) / len(support[k]),
                                        -sum(c in held for c in support[k]), order.get(k, len(order))))


def candidates(m: Module, st: GameState) -> list[Clue]:
    """The clues the party has missed, the most crucial first (see the module docstring)."""
    ranks = {k: i for i, k in enumerate(nearness(m, st))}
    order = {c.id: i for i, c in enumerate(m.clues)}
    missed = [c for c in m.clues if c.id not in st.clues and c.available(st.flags, st.clues)]

    def key(c: Clue) -> tuple:
        rank = min((ranks[k] for k in c.points_to if k in ranks), default=len(ranks))
        return (not c.core, rank, order[c.id])

    return sorted(missed, key=key)


def pick(m: Module, st: GameState) -> Clue | None:
    found = candidates(m, st)
    return found[0] if found else None


def roller(st: GameState) -> Investigator:
    """Who rolls: the investigator with the highest INT, of those who can act if any can; on a tie
    the one acting now, then the party's order."""
    able = [i for i in st.investigators if i.can_act] or list(st.investigators)
    active = st.investigator.id
    return max(able, key=lambda i: (i.characteristics.get("智力", 0), i.id == active, -st.investigators.index(i)))


def offered(st: GameState) -> bool:
    """The keeper's offer on a stall is open: made, and nothing found since."""
    return bool(st.idea_offer) and st.progress_at <= st.idea_offer


def used_today(st: GameState) -> bool:
    today = st.minutes // 1440
    return any(r.day == today and not r.offered for r in st.ideas)


def refused(m: Module, st: GameState, *, battling: bool = False) -> str:
    """Why the party cannot make an Idea roll now; "" when it can."""
    if st.ending:
        return ENDED
    if battling:
        return FIGHTING
    note(m, st)
    if pick(m, st) is None:
        return NOTHING
    if used_today(st) and not offered(st):
        return TODAY
    return ""


def plan(m: Module, st: GameState) -> dict:
    """What a roll asked for now is made of, fixed when it is asked (the turn's action keeps it): the
    clue, the difficulty, who rolls, and whether the keeper offered it."""
    note(m, st)
    clue = pick(m, st)
    assert clue is not None, "refused() first"
    level = difficulty(st, clue.id)
    return {"clue": clue.id, "difficulty": level, "roller": roller(st).id, "offered": offered(st),
            "mentioned": presence(st, clue.id).mentioned}


def record(st: GameState, plan_: dict, passed: bool) -> None:
    st.ideas.append(IdeaRoll(turn=st.turn, day=st.minutes // 1440, clue=plan_["clue"], difficulty=plan_["difficulty"],
                             passed=passed, offered=bool(plan_.get("offered"))))
    st.idea_offer = 0


def realised(title: str, *, mentioned: bool) -> str:
    """The keeper's note for a successful roll (p. 168): the investigators' own realisation."""
    origin = ("" if mentioned else
              "这条线索此前没有出现过，要为它找一个合情合理的来由：想起的一句话、翻出的旧笔记、偶然看见的东西。")
    return (f"【灵感检定成功】调查员把已知的线索重新梳理了一遍，想通了〔{title}〕。把它写成调查员自己的领悟，"
            f"从他们见过、听过的东西里自然地想到它，想到了什么具体写出来，并落到一个明确的方向上；不要写成有人直接告诉他们，也不要替玩家决定下一步。{origin}")


def cut(title: str, minutes: int, *, staged: bool) -> str:
    """The keeper's note for a failed roll (p. 169): time lost, then straight into the scene."""
    hours = f"{minutes // 60} 小时" if minutes % 60 == 0 else f"{minutes} 分钟"
    scene = ("导演安排的场面见下面的事件与安排。" if staged else
             "依据本局已有的人物、地点和局势，把调查员放进他们能意识到的最糟的局面。")
    return (f"【灵感检定失败——千钧一发】又过去了{hours}，调查员仍然理不出头绪。用一两句交代这段时间，然后直接切入眼前的场面：{scene}"
            f"让〔{title}〕在这个场面里显露出来。叙述停在调查员必须立刻应对的一刻，之后由玩家行动；不替调查员行动，也不预先给出结果。")
