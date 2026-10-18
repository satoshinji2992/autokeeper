"""After the ending: the keeper's book opened to the table.

The debrief (复盘) is the engine's alone, read off the module and the state with no model:
what was really going on, the clues found and the ones missed (and where they were, and
what it would have taken), what each person was hiding, the endings not reached and what
would have led there, and the turns this story took. Spoilers are the point of it, so it
exists only once the game has ended.

The epilogue (后日谈) is the one part that is told: on request, a single keeper call on what
became of each person they met and of each investigator, kept in the save.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..harness import OnDelta
from ..module.graph import supporting_clues
from ..module.schema import Clue, Module
from .state import GameState

if TYPE_CHECKING:
    from .game import Game


@dataclass
class ClueNote:
    title: str
    where: str  # the place, and the thing or person there it was on
    how: str  # what it took: a check, a person to win over, or just a look
    core: bool
    text: str


@dataclass
class PersonNote:
    name: str
    role: str
    met: bool
    goal: str
    secrets: list[str]
    lies: list[str]


@dataclass
class RoadNote:
    """An ending they did not reach."""

    title: str
    text: str
    how: list[str]  # what would have led there


@dataclass
class Debrief:
    title: str
    ending: str
    ending_text: str
    truth: str
    found: list[ClueNote] = field(default_factory=list)
    missed: list[ClueNote] = field(default_factory=list)
    deductions: list[tuple[str, int, int]] = field(default_factory=list)  # conclusion, its clues found, its clues in all
    people: list[PersonNote] = field(default_factory=list)
    roads: list[RoadNote] = field(default_factory=list)
    turns: list[str] = field(default_factory=list)  # the story flags set, by what they mean
    unvisited: list[str] = field(default_factory=list)


def build(m: Module, st: GameState) -> Debrief:
    """What the debrief shows, for a game that has ended (ValueError before that)."""
    if not st.ending:
        raise ValueError("复盘要等这一局结束")
    end = next((e for e in m.endings if e.id == st.ending), None)
    clues = {c.id: c for c in m.clues}
    found = [clues[c] for c in st.clues if c in clues]  # in the order they turned up
    support = supporting_clues(m)
    return Debrief(
        title=m.title,
        ending=end.title if end else st.ending,
        ending_text=end.text.strip() if end else "",
        truth=m.truth.strip(),
        found=[_note(m, c) for c in found],
        missed=[_note(m, c) for c in m.clues if c.id not in st.clues],
        deductions=[(k.text, sum(c in st.clues for c in support.get(k.id, [])), len(support.get(k.id, []))) for k in m.conclusions],
        people=[
            PersonNote(n.name, n.role, bool(st.npcs.get(n.id) and st.npcs[n.id].met), n.goal, list(n.secrets), list(n.lies))
            for n in m.npcs
        ],
        roads=[RoadNote(e.title, e.text.strip(), _roads(m, e.id, e.condition)) for e in m.endings if e.id != st.ending],
        turns=[m.flags[f] for f in st.flags if m.flags.get(f)],
        unvisited=[loc.name for loc in m.locations if loc.id not in st.visited],
    )


def _note(m: Module, c: Clue) -> ClueNote:
    place = next((loc.name for loc in m.locations if loc.id == c.where), c.where)
    npc = next((n for n in m.npcs if n.id == c.npc), None)
    spot = c.feature or (npc.name if npc else "")
    gate = npc.gates.get(c.id) if npc else None
    if c.check:
        how = f"{c.check.skill}（{c.check.difficulty}）"
    elif gate:
        how = f"{gate.skill}（{gate.difficulty}）"
    else:
        how = "问到就说" if npc else "不用检定"
    return ClueNote(c.title, f"{place} · {spot}" if spot else place, how, c.core, c.text.strip())


def _roads(m: Module, ending: str, condition: str) -> list[str]:
    """What would have led to an ending: the flag it waits for and the events that set it,
    or the module's own words for it; and the event that ends the game there if nothing
    else has by then."""
    out = []
    if condition.startswith("flag:"):
        flag = condition[5:]
        out.append(f"需要：{m.flags.get(flag) or flag}")
        out += [f"〔{ev.title}〕会让它发生，前提是：{_when(m, ev.when)}" for ev in m.events if flag in ev.sets]
    else:
        out.append(f"条件：{condition}")
    out += [f"〔{ev.title}〕（{_when(m, ev.when)}）时还没有别的结局，就是这一个" for ev in m.events if ev.ending == ending]
    return out


def _when(m: Module, when: str) -> str:
    """An event's trigger as a player would put it."""
    if when.startswith("flag:"):
        return m.flags.get(when[5:]) or when[5:]
    if when.startswith("visit:"):
        return f"到了{next((loc.name for loc in m.locations if loc.id == when[6:]), when[6:])}"
    if re.fullmatch(r"D\d+ \d{1,2}:\d{2}", when):
        return f"到了 {when}"
    return when


# -- the epilogue -------------------------------------------------------------------------
def epilogue_prompt(m: Module, st: GameState) -> str:
    """What the keeper is given to write the epilogue: the ending, everyone it is about, and
    what the investigators found. The keeper's own memory of the game does the rest."""
    end = next((e for e in m.endings if e.id == st.ending), None)
    met = [n for n in m.npcs if st.npcs.get(n.id) and st.npcs[n.id].met]
    people = "\n".join(
        f"- {n.name}（{n.role}）" + "".join(f"；{label}：{v}" for label, v in (("想要", n.goal), ("秘密", "；".join(n.secrets)), ("此刻", n.arc)) if v)
        for n in met
    )
    party = "\n".join(
        f"- {i.name}（{i.occupation}，{i.age} 岁）：HP {i.hp}/{i.hp_max}，理智 {i.san}/{i.san_max}" + (f"，{'、'.join(i.conditions)}" if i.conditions else "")
        for i in st.investigators
    )
    found = "、".join(m.clue(c).title for c in st.clues if any(x.id == c for x in m.clues)) or "无"
    return (
        f"【后日谈｜{end.title if end else st.ending}】\n这一局已经结束。结局：{end.text.strip() if end else ''}\n"
        f"调查员：\n{party}\n他们见过的人：\n{people or '（一个也没见过）'}\n他们找到的线索：{found}\n\n"
        "游戏结束了，真相和秘密都可以说了。请写后日谈：结局之后过了一阵子，上面每个人后来怎样了。"
        "每人一段，两三句，段首写名字和冒号；先写见过的人，再写调查员。"
        "顺着这一局真实发生的事写，不编新的大事件，不写数值。只输出纯文本，不用 Markdown。"
    )


async def epilogue(game: Game, on_delta: OnDelta = None) -> str:
    """The epilogue, told by the keeper once and kept in the save; asked again, the kept one.
    Screens follow it through the game's events: epilogue_start, epilogue_delta, then
    epilogue with the whole text (empty, with an error, if the keeper could not finish)."""
    st = game.state
    if not st.ending:
        raise ValueError("后日谈要等这一局结束")
    if st.epilogue:
        await game.emit({"type": "epilogue", "text": st.epilogue})
        return st.epilogue
    await game.emit({"type": "epilogue_start"})

    async def delta(text: str) -> None:
        await game.emit({"type": "epilogue_delta", "text": text})
        if on_delta:
            await on_delta(text)

    try:
        text = await game.cast.epilogue(st, epilogue_prompt(game.m, st), on_delta=delta)
    except Exception as exc:
        await game.emit({"type": "epilogue", "text": "", "error": f"守秘人没能讲完后日谈（{exc.__class__.__name__}），可以再试一次"})
        raise
    st.epilogue = text
    await game.emit({"type": "epilogue", "text": text})
    return text
