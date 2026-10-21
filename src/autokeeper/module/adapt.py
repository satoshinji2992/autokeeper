"""Compile-time adaptation: what a module needs to play well that its text leaves out.

Built as a situation, not a plot (no acts, no planned path through it): who wants what, how
they stand with each other, what comes of it if nobody steps in, what can be glimpsed early,
which threads an investigator's own past can be tied to. Each pass fills one thing the module
lacks and leaves what it has:

    npcs        人物补全    empty personality, voice, goal and what each NPC knows
    relations   冲突图      how the people see each other (Module.relations)
    agendas     局面        what the important NPCs mean to do if nobody steps in (Module.agendas)
    foreshadow  伏笔        early signs, and what each one leads to (Module.foreshadow)
    clueweb     线索网      a conclusion with fewer than three clues gets more (the three-clue rule)
    ties        私人牵连    threads an investigator's background can be tied to (Module.ties)
    endings     额外结局    the ends a table is likely to reach that the module never wrote
    mechanics   插件清单    the rules the engine does not keep by itself (Module.mechanics)
    meet        见面反应    which agenda steps are what an NPC does on meeting the investigators (Move.meet):
                            character material for its voice, never a backstage decision; for
                            modules whose agendas were written before the label
    history     前史        what happened before the game and still matters, who knows it (Module.history)
    prelude     开局演出    a few pictures and lines on the story's background, played before the
                            game screen while the director adapts (module/prelude.py); it reads
                            only what the players are told, never the keeper's book
    combat      战斗与追逐  what a fight and a chase read of the module (engine/combat.py): creature sheets
                            the rules can use (hit points, DEX, attacks, the traits of their notes), sheets
                            for the NPCs who fight, a chase route for the places a chase is likely
                            (Location.chase), the attack an event scripts (Event.fight, Event.chase);
                            the numbers from the original text, the rulebook's where it gives none

    verify      复核        what this run added, read against the original text by one more call: what gets
                            round a condition the module keeps (a roll, a prerequisite, a gate) is taken out

Every call reads the whole of the keeper's book with its conditions kept whole (book(): each
place's keeper notes, each clue's words, check and prerequisites, each NPC's gates) and the
original text from source/ (original()); when that is too long, what bears on the call and states
a condition is kept first. The passes run in waves, each wave at once: what depends on another
pass waits for it (AFTER), so the plans read the people as they were just filled in. Each pass's
additions are held to the module's conditions (module/conditions.py) before they land: what gets
round one is asked again once with the reasons, then left out. A pass whose edits break the
module's checks is taken back whole. The module's truth, its clues' words, checks and endings are
never changed: everything here is added.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .. import config
from ..engine import combat as battle
from ..harness import Session
from ..rules.chase import Leg
from ..rules.combat import stats_of, traits
from ..rules.weapons import carried, parse_attack, unread, weapon_in
from . import conditions, package, prelude, yamledit
from .graph import HARM, audit, fighting, known_skill, supporting_clues
from .schema import Agenda, Bond, Foreshadow, Mechanic, Module, Past, PreludeFrame, Tie

PASSES = ("npcs", "relations", "agendas", "meet", "foreshadow", "clueweb", "ties", "endings", "mechanics", "history", "prelude",
          "combat", "verify")
LABELS = {"npcs": "人物补全", "relations": "冲突图", "agendas": "局面（重要 NPC 的打算）", "meet": "见面反应", "foreshadow": "伏笔",
          "clueweb": "线索网", "ties": "私人牵连", "endings": "额外结局", "mechanics": "插件清单", "history": "前史",
          "prelude": "开局演出", "combat": "战斗与追逐", "verify": "复核"}  # fmt: skip
# What a pass reads that another pass writes: it waits for that one (the plans read the people as
# just filled in, the signs the clues just added).
AFTER: dict[str, tuple[str, ...]] = {
    "relations": ("npcs",), "history": ("npcs",), "ties": ("npcs",),
    "agendas": ("npcs", "relations", "history", "endings", "mechanics"),
    "meet": ("agendas",), "foreshadow": ("clueweb", "agendas", "history"),
}
BOOK_LIMIT = 40000  # characters of the keeper's book before its descriptions are shortened (conditions never are)
SOURCE_LIMIT = 40000  # characters of the original text before only what bears on the call is kept

SYSTEM = """你是 AutoKeeper 的模组改编员，在编译期给一个 CoC 7 版模组补上跑团时用得着、原文却没写的东西。
- 原作的真相、线索原文、检定、结局一个字都不改；补上的东西要和原作对得上，像本来就在里面。
- 原作的条件一个都不绕过：知道有这样东西、知道它在哪、实际找到、拿到手、读懂内容是五件事，各有各的条件（检定、前置线索、交涉门槛、时间）。
  补写不替检定交出结果，也不替守秘人决定别的拿法；检定或孤注一掷之后怎么演，是守秘人裁定以后的事。
- 你备的是给守秘人临场用的材料：人物的动机和所知、手头的资源、日程、触发条件、可能的后果。不预写台词，不穷举调查员的路线；见面反应是可以这样演，不是非演不可。
- 条件写实际成立的依据：调查员买到酒不等于酒到了他手里，调查员知道了不等于他也知道了。条件语法写不出的，把完整条件写进文字，不改用更宽的标记。
- 按局面来想，不按剧情：写清谁想要什么、彼此怎样、没人插手时事情会怎么发展；不预设调查员会怎么走，不分幕。
- id 用英文小写或拼音加下划线，不和已有的重复；引用的地点、NPC、线索、结论、事件、结局只用资料里有的。
- 写短，写具体：一个人、一样东西、一件事，不写空话。"""


class Known(BaseModel):
    text: str = Field(description="他知道的一件事，照原作写到哪一步就写到哪一步：知道有、知道在哪、见过、读过是不同的事")
    how: str = Field("", description="他是怎么知道的：亲眼见过、亲身经历、听谁说的")
    source: str = Field(description="原作哪里写到：地点、线索、NPC 的 id，或 真相、守秘人须知、原文；原作没写的不写这一条")


class NpcFill(BaseModel):
    npc: str = Field(description="NPC id")
    personality: str = Field("", description="性格，一句话；原来有就留空")
    voice: str = Field("", description="说话方式；口头禅和习惯动作写成偶尔带出的（“偶尔……”“着急时……”），不写成每句都做；原来有就留空")
    goal: str = Field("", description="他想要什么；原来有就留空")
    knows: list[Known] = Field(default_factory=list, description="他按原作知道的事；原来有就留空，不添新案情")


class NpcFills(BaseModel):
    npcs: list[NpcFill] = Field(default_factory=list)


class Bonds(BaseModel):
    relations: list[Bond] = Field(default_factory=list)


class Agendas(BaseModel):
    agendas: list[Agenda] = Field(default_factory=list)


class MeetMark(BaseModel):
    npc: str = Field(description="NPC id")
    step: str = Field(description="步骤 id")
    meet: bool = Field(description="见面时的反应填 true；要他自己在幕后去做的填 false")
    once: bool | None = Field(None, description="见面时的反应才填：只发生一次的（应门、安顿行李、头一回讲委托）填 true；一贯的态度、习惯（被问到墓地就闭嘴）填 false")


class Meets(BaseModel):
    steps: list[MeetMark] = Field(default_factory=list)


class Signs(BaseModel):
    foreshadow: list[Foreshadow] = Field(default_factory=list)


class NewClue(BaseModel):
    id: str
    title: str = Field(description="线索本里的短标题")
    text: str = Field(description="调查员得到的信息（玩家可见），不比原作的关键线索说得多，不直接说出真相")
    where: str = Field(description="所在地点 id")
    feature: str = Field(description="具体在哪样东西上、哪里")
    skill: str = Field("", description="需要什么检定，如 侦查、图书馆使用；找到就有就留空")
    difficulty: Literal["常规", "困难", "极难"] = "常规"
    points_to: list[str] = Field(description="支持哪个结论 id")


class NewClues(BaseModel):
    clues: list[NewClue] = Field(default_factory=list)


class Ties(BaseModel):
    ties: list[Tie] = Field(default_factory=list)


class NewEnding(BaseModel):
    id: str
    title: str
    flag: str = Field(description="触发它的剧情标记 id，新的")
    meaning: str = Field(description="这个标记的含义：调查员的行动真正达成了什么时它才成立")
    text: str = Field(description="结局的叙述，100–200 字")


class Endings(BaseModel):
    endings: list[NewEnding] = Field(default_factory=list, max_length=2)


class Mechanics(BaseModel):
    mechanics: list[Mechanic] = Field(default_factory=list)


class Pasts(BaseModel):
    history: list[Past] = Field(default_factory=list, max_length=8)


class Prelude(BaseModel):
    prelude: list[PreludeFrame] = Field(default_factory=list, max_length=prelude.MAX_FRAMES)


def _numbers(raw: Any) -> dict[str, int]:
    """A sheet's numbers as written, less what is no number (a bonus written "+1D4" goes to notes)."""
    out: dict[str, int] = {}
    for key, value in (raw or {}).items() if isinstance(raw, dict) else ():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


class CreatureSheet(BaseModel):
    creature: str = Field(description="怪物 id")
    stats: dict[str, int] = Field(default_factory=dict, description="缺的战斗数值，键用中文：生命、敏捷、力量、体质、体型、闪避、移动、侦查、潜行……；已有的不写")
    attacks: list[str] = Field(default_factory=list, description="它还没有读得出的攻击时才写，一条一种：“爪击 50%，伤害 1D6+DB”，命中率带 %")
    notes: str = Field("", description="原文写到、notes 里还没有的特性，照这些写法：护甲 2、枪伤减半、每轮恢复 1 点、只被拳头打 6 点就会打晕、不追；没有就留空")

    @field_validator("stats", mode="before")
    @classmethod
    def numbers_only(cls, value: Any) -> dict[str, int]:
        return _numbers(value)


class NpcSheet(BaseModel):
    npc: str = Field(description="NPC id：原文写明会动手、或剧情里会被卷进打斗的人")
    stats: dict[str, int] = Field(default_factory=dict, description="战斗数值：力量、体质、体型、敏捷、生命、闪避、格斗(斗殴)，会用的武器技能（射击(手枪)……）；原文有就照抄")
    weapon: str = Field("", description="原文写明他带着的武器（.38 左轮手枪、猎枪、斧头）；没有就留空")

    @field_validator("stats", mode="before")
    @classmethod
    def numbers_only(cls, value: Any) -> dict[str, int]:
        return _numbers(value)


class Route(BaseModel):
    location: str = Field(description="地点 id：最可能发生追逐的地方")
    legs: list[Leg] = Field(description="从那里跑出去的路，4–12 段，按跑的先后排")


class Trigger(BaseModel):
    event: str = Field(description="事件 id：原文写明这件事发生时有人或怪物当场动手、或开始追逐的")
    fight: list[str] = Field(default_factory=list, description="当场动手的 NPC 或怪物 id")
    surprise: Literal["", "party", "foe"] = Field("", description="party 调查员被偷袭，foe 对方被偷袭；都有防备留空")
    chase: list[str] = Field(default_factory=list, description="开始追逐时追或逃的 NPC 或怪物 id；写了 fight 就不写")
    chase_party: Literal["", "逃", "追"] = Field("", description="追逐时调查员是逃还是追")


class Combat(BaseModel):
    creatures: list[CreatureSheet] = Field(default_factory=list)
    npcs: list[NpcSheet] = Field(default_factory=list)
    # At most three, as the schema says; more do not void the answer: the first three that hold are kept.
    routes: list[Route] = Field(default_factory=list, json_schema_extra={"maxItems": 3})
    triggers: list[Trigger] = Field(default_factory=list)


ASK: dict[str, tuple[str, type[BaseModel]]] = {
    "npcs": ("给下面标着“缺”的 NPC 补上空着的字段：personality 性格、voice 说话方式、goal 想要什么、knows 他知道的事。"
             "只补空着的，已有的不动。voice 里的口头禅、习惯动作写成偶尔带出的素材，不写成每句话都要做的。"
             "knows 每条写他按原作知道的一件事、他是怎么知道的（how）、原作哪里写到（source）；原作没写他知道的不写，不添新的案情。"
             "知道一样东西存在，不等于知道它在哪、找到过、读过：原作写到哪一步就写到哪一步。肯不肯说留给游戏里看关系，不写在这里。", NpcFills),
    "relations": ("画出人物之间的冲突图：谁对谁怎样看（信任、恐惧、欠情、敌对、亲属、雇佣、爱慕……），多深（-100..100，负数是反过来），为什么。"
                  "写 6–14 条，先写会推动局面的：利益冲突、隐瞒、亏欠、旧怨。b 也可以写 players：他对外来调查员的态度。", Bonds),
    "agendas": ("挑 3–6 个最有自己打算的 NPC（标了“重要”的优先），写他们想要什么，以及没人插手时接下来会怎么做，每人 2–5 步。"
                "when 写 'D1 23:00' 这样的时间，或条件 flag:标记、at:谁:地点。和原作的事件时间线对得上，不替原作的事件抢戏。"
                "有 when 的步骤写 does：做成以后实际成立的事（at:他自己:地点、owner:物件:持有者、flag:标记、gone:谁），游戏里到时直接照做；"
                "要等调查员来问、来做的事写不成条件，不写 when 也不写 does，交给人物临场决定；"
                "这样的见面时的反应写 meet: true，不写 does，when 只写从什么时候、什么条件起才这样；其余没有 does 的步骤写 meet: false；"
                "does 里的 flag 只用表示世界里发生了某事的标记，不用表示调查员看见、遇到、做到或知道了什么的标记（那些由裁定员按调查员的行动立）；"
                "物件只用【物件】里的 id，交给调查员写 players；别人去哪不写在他的 does 里。原作里会把剧情推向某个结局的，用那个结局的标记。"
                "见面时的反应再标 once：只发生一次的（应门、安顿行李、头一回讲委托）填 true，一贯的态度（一问到墓地就闭嘴）填 false。"
                "一步要等别人当面做了什么、或他从别人那里听说了什么才成立的，条件写得出的用条件（owner:物件:他、knows:他:事实），"
                "写不出的把完整条件写进 only_if（“调查员当面把酒递给他、他收下以后”“有手下报告、或他亲眼看到调查员在查望远镜”），"
                "不拿表示调查员有了什么、知道了什么的标记代替。要等线索已经找到才做的，when 写 knows:players:clue:线索id。"
                "检定、孤注一掷或调查员的具体做法之后才可能发生的结果（孤注一掷成功时他过来帮忙找），写 ruling: true，交给守秘人裁定。"
                "见面反应不写台词，不把原作要检定才找到的东西写成他主动指出、交出或讲明。"
                "每步写 source：原作哪里写到（地点、线索、NPC 的 id，或 真相、守秘人须知）；推出来的写 补写。", Agendas),
    "meet": ("下面【打算里没标的步骤】，逐条判断：是见面时的反应（调查员来问、来访、在场时他才会做的事：应门招呼、领人去看、被问到时怎么说），"
             "还是要他自己在幕后去做的事（盯梢、报信、藏东西、搬走什么、找人商量）。见面时的反应填 true，幕后去做的填 false。"
             "一步里既有见面时的反应、又有之后在幕后要做的事（比如被问完就去报信），填 false。"
             "见面时的反应再标 once：只发生一次的事（应门、安顿行李、头一回讲委托）填 true；一贯的态度、习惯（被问到墓地就闭嘴）填 false。"
             "已经标了见面、只差 once 的，照样填 meet: true 和 once。只判断，不改步骤。", Meets),
    "foreshadow": ("写 3–6 条伏笔：调查员早早能在某个地点看见、听见的一点端倪，最后指向某个结论、事件或结局。"
                   "端倪本身不说破真相，只让人觉得哪里不对；也不能说出某条线索本身的内容（线索是窗锁松了，端倪就不写窗钩是松的），"
                   "不然等于不用检定就把线索给了。", Signs),
    "clueweb": ("三线索原则：每个结论至少要有三条独立的线索能推出来。下面列着每个结论现有的线索；不够三条的，补一两条新线索："
                "在哪个地点、哪样东西上，要什么检定，指向哪个结论。新线索不比原作的关键线索说得多，不直接说出真相，放在调查员会去的地方。", NewClues),
    "ties": ("写 4–8 条私人牵连：调查员背景里的重要之人、意义非凡之地、思想与信念、宝贵之物、职业，可以怎样接上这个模组的人和地方。"
             "每条牵到一个 NPC 或地点，一句话说清这层关系。开局时导演按调查员的背景挑着用。", Ties),
    "endings": ("原作的结局之外，补 1–2 个玩家很可能走到、原作却没写的结局：放弃调查离开、和对手做了交易、只救下一部分人……"
                "每个写一个新的剧情标记（写清调查员的行动真正达成什么时它才成立）和结局叙述。不改原作的结局。", Endings),
    "mechanics": ("列出模组里引擎自己不会管、要插件来管的规则：时钟和倒计时、资源、疾病、仪式进度、特殊的检定。"
                  "战斗和追逐引擎按规则自己结算，不列（怪物数值、追逐路线和事件里写定的袭击由“战斗与追逐”一步补）；"
                  "原作在战斗或追逐里另有专属规则的（只能用银器伤它、追到第几段塌方），才列那一条。"
                  "每条写规则本身（什么时候、怎么算、有什么后果）和模组里哪里用到。原作没有这样的规则就留空。"
                  "每条写 runs，谁来执行：核心规则（引擎自带的检定、孤注一掷、理智、伤害、战斗与追逐就能算的）、插件（已经有插件的，plugin 填它的 id）、"
                  "守秘人（要守秘人临场裁定的）。", Mechanics),
    "history": ("写 3–8 件开局以前发生过、至今还在起作用的事（前史），按时间先后：多久以前、谁在哪做了什么、另外还有谁知道。"
                "只用原作的真相、守秘人须知和人物资料里写到的，不新编案情；玩家开局就知道的不用写。"
                "known_by 只写知道这句话全部内容的人：只知道一部分（比如只知道书丢了、不知道是谁拿的）的人不写，他们会把这句话当成自己知道的。", Pasts),
    "prelude": ("写开局演出：进入游戏前播放的 3–5 幕，像电影开头那样一张图配一两句字幕，按先后排，只介绍故事背景："
                "时代、地方、气氛、事情是怎么找上门的。图只从【可用的图】里挑，没有合适的就留空。"
                "只用上面玩家知道的内容，不写调查员会怎么做、会发现什么，每幕不超过 60 字。", Prelude),
    "combat": ("战斗和追逐由引擎按 CoC 7 版规则一轮一轮结算，数值从模组里读；【战斗与追逐】列着引擎现在读到的。照原文补上缺的，已经齐全的不写：\n"
               "1. creatures：怪物的 stats 要有生命和敏捷（还有力量、体质、体型、闪避、移动），attacks 一条一种，写成“爪击 50%，伤害 1D6+DB”，"
               "命中率带 %，每轮不止一次攻击另写一条“每轮 3 次”；notes 写原文提到、引擎还没读到的特性（护甲 2、枪伤减半、每轮恢复 1 点、"
               "只被拳头打 6 点就会打晕、不追）。原文有数值照抄，原文没写的按 CoC 7 版规则书里同类怪物的数值，不新添能力和设定；"
               "成群出现、整群当一个对手打的不写数值。\n"
               "2. npcs：原文写明会动手、或剧情里会被卷进打斗、stats 还空着的 NPC，写上战斗数值；原文写明他带着武器的，weapon 写武器名。普通人不写。\n"
               "3. routes：还没有路线、最可能发生追逐的 1–3 个地点（怪物的巢穴、它够得着的户外、原文写到有人逃跑或追赶的地方），各画一条追逐路线："
               "从那里跑出去的 4–12 段路，按先后排，每段写名字（四到八个字）和一句样子；大约一半有一处 obstacle："
               "hazard 险阻边跑边过（结冰的台阶、灌木丛），barrier 路障过了检定才过得去（矮墙、锁着的门），"
               "skill 写跳跃、攀爬、敏捷、力量、闪避、锁匠、游泳这类技能或属性。只用这个地方和周围真有的东西，最后一段是能脱身或被逼进绝路的地方。\n"
               "4. triggers：原文写明这件事发生时有人或怪物当场扑上来动手的事件，fight 写动手的 id，surprise 写哪一方毫无防备；"
               "写明一开始就是追逐的，chase 写追或逃的 id，chase_party 写调查员是逃还是追。只是可能动手、要看调查员怎么做的不写，那由裁定员临场判。", Combat),
}


def _clip(text: str, n: int | None) -> str:
    text = " ".join(text.split())
    return text if n is None or len(text) <= n else text[: n - 1] + "…"


def _when_row(s: Any) -> str:
    """An agenda step with all it hangs on."""
    bits = [f"when {s.when}" if s.when else "", f"does {s.does}" if s.does else "", "见面反应" if s.meet else ("幕后" if s.meet is False else ""),
            {True: "一次性", False: "一贯如此"}.get(s.once, "") if s.meet else "", f"只在：{s.only_if}" if s.only_if else "",
            "要守秘人裁定" if s.ruling else "", f"依据 {s.source}" if s.source else ""]
    return f"{s.id}：{s.text}" + (f"（{'；'.join(b for b in bits if b)}）" if any(bits) else "")


def book(m: Module, *, limit: int = BOOK_LIMIT) -> str:
    """The module as the adapter reads it: the keeper's whole book, with every condition whole
    (each place's keeper notes, each clue's words, check and prerequisites, each NPC's gates);
    over `limit`, only what is read aloud and the handouts' words are shortened."""
    text = _book(m, None)
    return text if len(text) <= limit else _book(m, 120)


def _book(m: Module, short: int | None) -> str:
    npcs = {n.id: n for n in m.npcs}
    lines = [f"【模组】《{m.title}》 {m.era}，{m.setting}；{m.players}", f"【开场钩子（玩家知道）】{m.hook.strip()}", f"【真相】{m.truth.strip()}"]
    if m.keeper_notes.strip():
        lines.append(f"【守秘人须知】{m.keeper_notes.strip()}")
    if m.opening.strip():
        lines.append(f"【开场】{_clip(m.opening, short and 300)}")
    lines.append(f"【开局】{m.start_time}，在 {m.start}")
    lines.append("【剧情标记】\n" + "\n".join(f"- {k}：{v}" for k, v in m.flags.items()))
    rows = []
    for x in m.locations:
        head = f"- {x.id} {x.name}" + ("（公开）" if x.public else "") + (f"（在 {x.within} 里）" if x.within else "")
        rows.append(f"{head}：{_clip(x.read_aloud, short)}")
        if x.keeper.strip():
            rows.append(f"  守秘人须知：{x.keeper.strip()}")
        if x.features:
            rows.append(f"  可调查：{'、'.join(x.features)}")
        rows.append(f"  通往：{'、'.join(x.exits) or '无'}")
    lines.append("【地点】\n" + "\n".join(rows))
    rows = []
    for n in m.npcs:
        rows.append(f"- {n.id} {n.name}（{n.role}）{'，重要' if n.important else ''}，在 {n.location}；想要：{n.goal or '缺'}；性格：{n.personality or '缺'}；"
                    f"说话：{n.voice or '缺'}")
        rows.append(f"  知道：{'；'.join(n.knows) or '缺'}")
        if n.secrets:
            rows.append(f"  秘密（不被逼问不说）：{'；'.join(n.secrets)}")
        if n.lies:
            rows.append(f"  会撒的谎：{'；'.join(n.lies)}")
        if n.clues:
            rows.append("  能给的线索：" + "；".join(f"{c}（{'要 ' + n.gates[c].skill + '（' + n.gates[c].difficulty + '）' if c in n.gates else '问到就说'}）"
                                                for c in n.clues))
    lines.append("【NPC】\n" + "\n".join(rows))
    if m.items:
        lines.append("【物件】\n" + "\n".join(f"- {i.id} {i.name}：{_clip(i.description, short)}，在 {i.owner or i.where or '不详'}"
                                            + ("，藏着" if i.hidden else "") for i in m.items))
    rows = []
    for c in m.clues:
        where = f"在 {c.where}" + (f"·{c.feature}" if c.feature else "") + (f"，由 {c.npc} 说出" if c.npc else "")
        how = f"检定 {c.check.skill}（{c.check.difficulty}）" if c.check else "搜到即得"
        if c.npc and c.npc in npcs and c.id in npcs[c.npc].gates:
            gate = npcs[c.npc].gates[c.id]
            how += f"，交涉门槛 {gate.skill}（{gate.difficulty}）"
        extra = [how] + ([f"前置 {'、'.join(c.requires)}"] if c.requires else []) + (["关键"] if c.core else [])
        extra += [f"理智 {c.sanity}"] if c.sanity else []
        extra += [f"手稿 {c.handout}"] if c.handout else []
        rows.append(f"- {c.id}「{c.title}」{where}；{'；'.join(extra)}；指向 {'、'.join(c.points_to) or '无'}")
        rows.append(f"  原文：{c.text.strip()}")
    lines.append("【线索】\n" + "\n".join(rows))
    if m.handouts:
        lines.append("【手稿】\n" + "\n".join(f"- {h.id}「{h.title}」（{h.kind}）：{_clip(h.text, short and 200)}" for h in m.handouts))
    lines.append("【结论】\n" + "\n".join(f"- {k.id}：{k.text}" for k in m.conclusions))
    lines.append("【事件】\n" + "\n".join(
        f"- {e.id}「{e.title}」{e.when}：{_clip(e.text, None)}" + (f"；设标记 {'、'.join(e.sets)}" if e.sets else "")
        + (f"；理智 {e.sanity}" if e.sanity else "") + (f"；进入结局 {e.ending}" if e.ending else "") for e in m.events))
    lines.append("【结局】\n" + "\n".join(f"- {e.id}「{e.title}」条件 {e.condition}：{_clip(e.text, short and 120)}" for e in m.endings))
    if m.extensions:
        lines.append("【规则插件】\n" + "\n".join(f"- {x.id}「{x.name}」：{x.description}" for x in m.extensions.values()))
    if m.mechanics:
        lines.append("【规则清单】\n" + "\n".join(f"- {x.name}（{x.executor}执行）：{x.rules}" for x in m.mechanics))
    # The situation already written (by hand, or by the passes before this one).
    if m.relations:
        lines.append("【人物关系（已写）】\n" + "\n".join(f"- {b.a}→{b.b} {b.kind} {b.value}：{b.why}" for b in m.relations))
    if m.history:
        lines.append("【前史（已写）】\n" + "\n".join(f"- {p.when}：{p.text}（牵涉 {'、'.join(p.who) or '无'}；另外知道的 {'、'.join(p.known_by) or '无'}）"
                                                for p in m.history))
    if m.agendas:
        lines.append("【各人的打算（已写）】\n" + "\n".join(
            f"- {a.npc} 想要 {'；'.join(a.goals) or '无'}\n" + "\n".join(f"  · {_when_row(s)}" for s in a.steps) for a in m.agendas))
    if m.foreshadow:
        lines.append("【伏笔（已写）】\n" + "\n".join(f"- {f.id} 在 {f.where}：{f.hint} → {f.pays_off}" for f in m.foreshadow))
    return "\n".join(lines)


def original(folder: Path | None, focus: list[str] | tuple[str, ...] = (), *, limit: int = SOURCE_LIMIT) -> str:
    """The module's original text from source/ (the imported pages, or the import notes when there
    are none), whole when it fits in `limit`; else the parts that name what this call is about
    (`focus`) or state a condition (a roll, an if, an only), in their order, up to `limit`."""
    if folder is None:
        return ""
    found = next((p for p in (Path(folder) / "source" / "source.md", Path(folder) / "source" / "source" / "source.md",
                              Path(folder) / "source" / "notes.md") if p.is_file()), None)
    if found is None:
        return ""
    text = found.read_text(encoding="utf-8").strip()
    if len(text) <= limit:
        return text
    parts = [x for x in re.split(r"\n\s*\n|(?=^-{5,} 第)", text, flags=re.M) if x.strip()]
    rule = re.compile(r"检定|成功|失败|孤注一掷|如果|若|只有|才|除非|必须|前提|之前|之后|要求|理智")

    def rank(part: str) -> int:
        return 2 * sum(1 for f in focus if f and f in part) + (1 if rule.search(part) else 0)

    keep, used = set(), 0
    for i in sorted(range(len(parts)), key=lambda i: -rank(parts[i])):
        if rank(parts[i]) == 0 or used + len(parts[i]) > limit:
            continue
        keep.add(i)
        used += len(parts[i])
    return "（原文较长，下面按原顺序只摘了与这次相关、写着条件的段落）\n" + "\n\n".join(parts[i] for i in sorted(keep))


def _focus(m: Module, name: str) -> list[str]:
    """The names a pass is about, for picking the parts of a long original."""
    if name == "npcs":
        return [n.name for n in m.npcs if not (n.personality and n.voice and n.goal and n.knows)]
    if name == "combat":
        return [c.name for c in m.creatures] + [n.name for n in m.npcs if n.important] + [e.title for e in m.events if e.id in _violent(m)]
    return [n.name for n in m.npcs if n.important] or [n.name for n in m.npcs]


# An event whose words tell of an attack or a pursuit (the combat pass asks about it when it scripts none).
VIOLENT = re.compile(r"扑向|扑上来|扑过来|袭击|突袭|伏击|偷袭|动手|开枪|开火|攻击|追杀|追赶|追捕|围攻|抓咬|撕咬")


def _violent(m: Module) -> list[str]:
    return [e.id for e in m.events if not (e.fight or e.chase) and VIOLENT.search(f"{e.text} {e.read_aloud}")]


def _unfit(m: Module) -> list[str]:
    """The creatures a fight cannot read whole (engine/combat.from_creature): no hit points or DEX,
    no attack the rules read. A swarm with no sheet needs neither: it fights as one."""
    out = []
    for c in m.creatures:
        if battle.SWARM.search(c.notes + c.description) and not c.stats:
            continue
        s = stats_of(c.stats)
        natural = any(w in k for k in s for w in battle.NATURAL)
        if "生命" not in s or "敏捷" not in s or not (natural or any(parse_attack(x) for x in c.attacks)):
            out.append(c.id)
    return out


def wanted(m: Module, name: str) -> bool:
    """Whether the module lacks what a pass fills."""
    if name == "npcs":
        return any(not (n.personality and n.voice and n.goal and n.knows) for n in m.npcs)
    if name == "combat":  # a sheet the rules cannot read, or a module with something to fight and no way to run
        return bool(_unfit(m)) or (bool(m.creatures or _violent(m)) and not any(x.chase for x in m.locations))
    if name == "clueweb":
        from .simulate import playthrough

        run = playthrough(m)
        return bool(run.lean) or any(run.fragile.values()) or bool(run.drift.lost)
    if name == "endings":
        return len(m.endings) < 3
    if name == "meet":
        return bool(_unmarked(m))
    return not getattr(m, name)


def _unmarked(m: Module) -> list[tuple[str, Any]]:
    """Agenda steps without an outcome, left for the NPC to judge (not the keeper's to rule), not
    yet marked as meeting reactions or backstage work, or meeting reactions not yet marked as
    happening once or always."""
    return [(a.npc, s) for a in m.agendas for s in a.steps if not s.does and not s.ruling and (s.meet is None or (s.meet and s.once is None))]


def _armoury(m: Module) -> str:
    """What a fight and a chase read of the module now, for the combat pass: each creature's sheet as
    written and as the rules read it, the NPCs' numbers and weapons, the routes, the events' fights."""
    unfit = set(_unfit(m))
    rows = ["\n【战斗与追逐：引擎现在读到的】", "怪物："]
    for c in m.creatures:
        f = battle.from_creature(c)
        if battle.SWARM.search(c.notes + c.description) and not c.stats:
            rows.append(f"- {c.id} {c.name}：成群，整群当一个对手打，不用写数值。{_clip(c.description, 80)}")
            continue
        read = "、".join(f"{a.name} {a.value}%（{a.damage}）" for a in f.attacks)
        rows.append(f"- {c.id} {c.name}：{_clip(c.description, 80)}\n  stats {c.stats or '空'}；attacks {c.attacks or '空'}；notes {c.notes.strip() or '空'}"
                    f"\n  引擎读到：生命 {f.hp_max}、敏捷 {f.dex}，攻击 {read}" + ("；还缺" if c.id in unfit else ""))
    if not m.creatures:
        rows.append("- 没有")
    rows.append("NPC 的战斗数值：")
    for n in m.npcs:
        things = [i.name for i in m.items if i.owner == n.id]
        weapons = "、".join(w.name for w in carried([n.look, *things])[1:]) or "没有武器"
        rows.append(f"- {n.id} {n.name}（{n.role}）：" + (f"stats {n.stats}；{weapons}" if n.stats else f"没写（按普通人算）；{weapons}"))
    ways = [x.id for x in m.locations if x.chase]
    rows.append(f"追逐路线：已有 {'、'.join(ways)}" if ways else "追逐路线：还没有")
    rows += [f"事件 {e.id} 写定了" + (f"打斗 {e.fight}" if e.fight else f"追逐 {e.chase}") for e in m.events if e.fight or e.chase]
    if violent := _violent(m):
        rows.append(f"字面上写到动手或追赶的事件（看原文判断是不是当场就打起来）：{'、'.join(violent)}")
    if problems := [i.text for i in fighting(m)]:
        rows += ["引擎读不出的：", *(f"- {x}" for x in problems)]
    return "\n".join(rows)


def _material(m: Module, name: str) -> str:
    if name == "combat":
        return _armoury(m)
    if name == "meet":
        rows = [f"- {npc} / {s.id}：{s.text}" + (f"（条件 {s.when}）" if s.when else "") + ("（已标见面反应，只差 once）" if s.meet else "")
                for npc, s in _unmarked(m)]
        return "\n【打算里没标的步骤】\n" + "\n".join(rows)
    if name == "clueweb":
        from .simulate import playthrough

        run = playthrough(m)  # which clues a table can actually reach, and what a conclusion cannot do without
        support = supporting_clues(m)
        rows = []
        for k, v in support.items():
            got = [c for c in v if c in run.reach.clues]
            hangs = run.fragile.get(k) or []
            rows.append(f"- {k}：拿得到的 {'、'.join(got) or '没有'}（{len(got)} 条）" + (f"；离不开{'、'.join(hangs)}，新线索放到别处、别人口中" if hangs else ""))
        text = "\n【每个结论现有的线索】\n" + "\n".join(rows)
        if run.drift.lost:  # what the agendas take away if no one steps in: the new way must not hang on it
            from .simulate import _clock

            text += "\n【没人插手时会断的结论】\n" + "\n".join(
                f"- {k}：{_clock(t)}「{why}」之后一条线索也拿不到了，新线索要放在这一步动不到的地方和人身上" for k, (t, why) in run.drift.lost.items())
        return text
    return ""


def _public(m: Module) -> str:
    """What the players are told before play, for the opening performance: never the keeper's book."""
    start = next((x for x in m.locations if x.id == m.start), None)
    lines = [f"【模组】《{m.title}》{m.subtitle}", f"【时代与地方】{m.era}，{m.setting}", f"【开场钩子（玩家知道）】{m.hook.strip()}"]
    if start is not None:
        lines.append(f"【开场地点】{start.name}：{start.read_aloud.strip()}")
    lines.append("【可用的图】\n" + ("\n".join(f"- {key}：{what}" for key, what in prelude.public_art(m).items()) or "- 没有"))
    return "\n".join(lines)


def _source(text: str) -> str:
    return f"\n\n【原文（整理资料的出处；条件和整理资料对不上时以原文为准）】\n{text}" if text.strip() else ""


async def _ask(cfg: config.Config, name: str, m: Module, *, source: str = "", problems: list[str] | tuple[str, ...] = (),
               hints: list[str] | tuple[str, ...] = ()) -> BaseModel:
    """One pass's call: the keeper's book with its conditions and the original text (the opening
    performance reads only what the players are told), and, asked again, what the last answer got
    round."""
    task, schema = ASK[name]
    s = Session("importer", cfg.role("importer"), SYSTEM, schema=schema.model_json_schema())
    material = _public(m) if name == "prelude" else f"{book(m)}{_source(source)}{_material(m, name)}"
    ask = f"{material}\n\n【这次要做的】{LABELS[name]}：{task}"
    if problems:
        ask += "\n\n【上一次补写里这几条写法不对，这次改掉；条件写不出的写进 only_if，不要删掉前提】\n" + "\n".join(f"- {x}" for x in problems)
    if hints:  # found by words, not by reading: the call that reads the original decides
        ask += ("\n\n【程序按关键词觉得这几条可能绕过了原作的条件，对照原文判断：确实绕过的就改（删掉、写清实际依据，"
                "或交给守秘人裁定），没有绕过的照原样写】\n" + "\n".join(f"- {x}" for x in hints))
    try:
        reply = await s.send(ask)
        return schema.model_validate(reply.data or {})
    finally:
        await s.close()


Edit = tuple[str, ...] | tuple[Any, ...]


def edits(m: Module, name: str, out: Any) -> list[tuple[Any, ...]]:
    """What a pass's answer writes: ("put", group, id, field, value), ("replace", group, id, field, value) for a field
    that has a value already, ("append", group, items), ("key", group, key, value)."""
    npcs, places = {n.id for n in m.npcs}, {x.id for x in m.locations}
    if name == "npcs":
        found = []
        for f in out.npcs:
            n = next((x for x in m.npcs if x.id == f.npc), None)
            if n is None:
                continue
            for key in ("personality", "voice", "goal"):
                if not getattr(n, key) and getattr(f, key).strip():
                    found.append(("put", "npcs", n.id, key, getattr(f, key).strip()))
            # What it knows, as far as the module goes and how it came to know it; one the module
            # never gives it (no source, or 补写) is no fact of its.
            rows = [k.text.strip() + (f"（{k.how.strip()}）" if k.how.strip() else "") for k in f.knows
                    if k.text.strip() and k.source.strip() and k.source.strip() != "补写"]
            if not n.knows and rows:
                found.append(("put", "npcs", n.id, "knows", rows[:6]))
        return found
    if name == "meet":
        open_ = {(npc, s.id): s for npc, s in _unmarked(m)}
        marks: dict[tuple[str, str, str], bool] = {}
        for x in out.steps:
            if (s := open_.get((x.npc, x.step))) is None:
                continue
            if s.meet is None:
                marks[(x.npc, x.step, "meet")] = x.meet
            if (s.meet or x.meet) and s.once is None and x.once is not None:
                marks[(x.npc, x.step, "once")] = x.once
        return [("step", "agendas", npc, step, key, value) for (npc, step, key), value in marks.items()]
    if name == "clueweb":
        taken = {c.id for c in m.clues}
        conclusions = {k.id for k in m.conclusions}
        new = []
        for c in out.clues:
            if c.id in taken or c.where not in places or not set(c.points_to) & conclusions or not c.text.strip():
                continue
            taken.add(c.id)
            item: dict[str, Any] = {"id": c.id, "title": c.title, "text": c.text.strip(), "where": c.where, "feature": c.feature}
            if c.skill:
                item["check"] = {"skill": c.skill, "difficulty": c.difficulty}
            item["points_to"] = [k for k in c.points_to if k in conclusions]
            new.append(item)
        return [("append", "clues", new)] if new else []
    if name == "endings":
        found = []
        taken = {e.id for e in m.endings}
        for e in out.endings:
            flag = re.sub(r"[^a-z0-9_]", "_", e.flag.lower()).strip("_")
            if e.id in taken or not flag or flag in m.flags or not e.text.strip():
                continue
            found.append(("key", "flags", flag, e.meaning.strip()))
            found.append(("append", "endings", [{"id": e.id, "title": e.title, "condition": f"flag:{flag}", "text": e.text.strip()}]))
        return found
    if name == "combat":
        return _armed(m, out)
    if name == "prelude":  # a shot that shows what is not public yet, or says too much, is left out
        art = prelude.public_art(m)
        shots = [f.model_copy(update={"art": f.art if f.art in art else ""}) for f in out.prelude]
        shots = [f for f in shots if not prelude.shot(m, f)][: prelude.MAX_FRAMES]
        return [("append", "prelude", [f.model_dump(mode="json", exclude_defaults=True) for f in shots])] if len(shots) >= 2 else []
    items: list[BaseModel] = getattr(out, name)
    if name == "relations":
        items = [b for b in items if b.a in npcs and (b.b in npcs or b.b == "players") and b.a != b.b]
    elif name == "agendas":
        from .graph import _outcome

        seen: set[str] = set()
        items = [a for a in items if a.npc in npcs and not (a.npc in seen or seen.add(a.npc)) and a.steps][:6]  # type: ignore[func-returns-value]
        for a in items:  # an outcome naming what the module lacks is cut down to what it has, not the whole pass
            for step in a.steps:
                step.only_if, step.source = step.only_if.strip(), step.source.strip()
                # A time joined to a condition stays whole (「D1 22:00 & flag:x」, agents.split_when); one written
                # some other way is asked again (held), never cut down to its hour: that would drop its premise.
                if step.ruling:  # what comes of the keeper's ruling is the keeper's: no outcome done, no meeting reaction
                    step.does, step.meet = "", None
                if step.meet:  # what it does on meeting them is its voice's to play: no outcome
                    step.does = ""
                else:
                    step.once = None
                parts = [x.strip() for x in step.does.replace("|", "&").split("&") if x.strip() and "!" not in x]
                # Without a time or a condition the step is the NPC's to judge in play: no outcome done for it.
                step.does = " & ".join(x for x in parts if not _outcome(m, a.npc, x)) if step.when.strip() else ""
    elif name == "foreshadow":
        from ..agents import guard

        targets = {k.id for k in m.conclusions} | {e.id for e in m.events} | {e.id for e in m.endings}
        clues = {c.id: c.text for c in m.clues}
        names = " ".join([*(n.name for n in m.npcs), *(x.name for x in m.locations), m.setting, m.hook])
        items = [f for f in items if f.where in places and f.pays_off in targets
                 and not guard.leaks(f.hint, clues, public=names)]  # a sign that says a clue's words gives it away
    elif name == "ties":
        items = [t for t in items if (t.npc in npcs or not t.npc) and (t.place in places or not t.place) and t.hook.strip()]
    elif name == "history":  # names the module does not have are left out, the event kept
        items = [p.model_copy(update={"who": [x for x in p.who if x in npcs], "known_by": [x for x in p.known_by if x in npcs],
                                      "where": p.where if p.where in places else ""}) for p in items if p.text.strip()][:8]
    dumped = [x.model_dump(mode="json", exclude_defaults=True) for x in items]
    return [("append", name, dumped)] if dumped else []


def _marks(notes: str) -> set[str]:
    """What the rules make of a creature's notes: its traits, letting the runners go, a swarm."""
    return set(traits(notes)) | ({"lets_go"} if battle.LETS_GO.search(notes) else set()) | (
        {"swarm"} if battle.SWARM.search(notes) else set())


def _armed(m: Module, out: Combat) -> list[tuple[Any, ...]]:
    """What the combat pass writes, added to what the module has: a creature's missing numbers (a
    swarm's none: a sheet would make it one beast), attacks when none of its lines reads (in place of
    the lines with no chance to hit in them, which say less of the same), the traits its notes lack; a sheet for an NPC who fights and has none, the weapon the text gives them as
    something they carry (Item.owner); a route for a place with none, its obstacles the rules cannot
    cross left out; the fight or the chase of an event that scripts none. What names someone or
    somewhere the module lacks is left out."""
    found: list[tuple[Any, ...]] = []
    creatures = {c.id: c for c in m.creatures}
    for x in out.creatures:
        if (c := creatures.get(x.creature)) is None:
            continue
        swarm = bool(battle.SWARM.search(c.notes + c.description)) and not c.stats
        have = set(stats_of(c.stats))
        added: dict[str, int] = {}
        for key, value in x.stats.items():
            canon = next(iter(stats_of({key: value})), "")
            if canon and canon not in have:
                have.add(canon)
                added[key] = value
        if added and not swarm:
            found.append(("replace", "creatures", c.id, "stats", {**c.stats, **added}))
        lines = [a.strip() for a in x.attacks if parse_attack(a.strip()) is not None]
        if lines and not any(parse_attack(a) for a in c.attacks):  # the old lines with no chance in them go: these say it whole
            kept = [a for a in c.attacks if not unread(a) or re.search(r"\d\s*[%％]", a)]
            found.append(("replace", "creatures", c.id, "attacks", [*kept, *lines]))
        if x.notes.strip() and _marks(x.notes) - _marks(c.notes):
            found.append(("replace", "creatures", c.id, "notes", f"{c.notes.strip()} {x.notes.strip()}".strip()))
    npcs, items = {n.id: n for n in m.npcs}, {i.id for i in m.items}
    for x in out.npcs:
        if (n := npcs.get(x.npc)) is None:
            continue
        if x.stats and not n.stats:
            found.append(("put", "npcs", n.id, "stats", x.stats))
        held = carried([n.look, *(i.name for i in m.items if i.owner == n.id)])
        if (w := weapon_in(x.weapon)) is not None and w not in held:
            key = next((k for k in (f"{n.id}_weapon", *(f"{n.id}_weapon_{i}" for i in range(2, 9))) if k not in items), "")
            if not key:
                continue
            items.add(key)
            found.append(("append", "items", [{"id": key, "name": x.weapon.strip(), "description": f"{n.name}随身带着的{x.weapon.strip()}",
                                                "where": n.location or m.start, "owner": n.id}]))
    places, taken = {x.id: x for x in m.locations}, set()
    for r in out.routes:
        loc = places.get(r.location)
        if loc is None or loc.chase or loc.id in taken or not 4 <= len(r.legs) <= 12 or len(taken) >= 3:
            continue
        legs = []
        for leg in r.legs:
            ob = leg.obstacle
            if ob is not None and (not known_skill(ob.skill) or (ob.harm and not HARM.fullmatch(ob.harm.replace(" ", "")))):
                leg = leg.model_copy(update={"obstacle": None})  # one the rules cannot cross is no obstacle
            legs.append(leg.model_dump(mode="json", exclude_defaults=True))
        taken.add(loc.id)
        found.append(("put", "locations", loc.id, "chase", legs))
    people, events = {*npcs, *creatures}, {e.id: e for e in m.events}
    for t in out.triggers:
        if (e := events.get(t.event)) is None or e.fight or e.chase or e.ending:
            continue
        fight, chase = [x for x in dict.fromkeys(t.fight) if x in people], [x for x in dict.fromkeys(t.chase) if x in people]
        if fight:
            found += [("put", "events", e.id, "fight", fight)] + ([("put", "events", e.id, "surprise", t.surprise)] if t.surprise else [])
        elif chase:
            found += [("put", "events", e.id, "chase", chase)] + ([("put", "events", e.id, "chase_party", t.chase_party)] if t.chase_party else [])
    return found


def _target(folder: Path, group: str) -> Path:
    """The file a top-level group goes in: its part file when the module is split (made if new)."""
    part = next((n for n, keys in package.PARTS.items() if group in keys), None)
    split = any((folder / n).is_file() for n in package.PARTS)
    return folder / part if part and split else yamledit.where(folder, group)


def land(folder: Path, found: list[tuple[Any, ...]]) -> None:
    for edit in found:
        kind, group = edit[0], edit[1]
        path = _target(folder, group)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if kind == "put":
            text = yamledit.put(text, group, edit[2], edit[3], edit[4])
        elif kind == "replace":  # a field's whole value, what it had kept in it (a sheet's numbers and more)
            text = yamledit.replace(text, group, edit[2], edit[3], edit[4])
        elif kind == "step":
            text = yamledit.put_step(text, group, edit[2], edit[3], edit[4], edit[5])
        elif kind == "append":
            text = yamledit.append(text, group, edit[2])
        else:
            text = yamledit.put_key(text, group, edit[2], edit[3])
        path.write_text(text, encoding="utf-8")


def preview(m: Module, found: list[tuple[Any, ...]]) -> Module:
    """The module as it would be with a pass's edits, in memory (to hold them to its conditions
    before anything is written)."""
    data = m.model_dump(mode="json")
    for e in found:
        kind, group = e[0], e[1]
        if kind in ("put", "replace"):
            next(x for x in data[group] if x["id"] == e[2])[e[3]] = e[4]
        elif kind == "step":
            agenda = next(a for a in data[group] if a["npc"] == e[2])
            next(x for x in agenda["steps"] if x["id"] == e[3])[e[4]] = e[5]
        elif kind == "append":
            data.setdefault(group, []).extend(e[2])
        else:
            data.setdefault(group, {})[e[2]] = e[3]
    out = Module.model_validate(data)
    out.extensions = m.extensions
    return out


def _refs(m: Module) -> set[str]:
    """What the situation layer has, by the refs the review names them by."""
    return ({p.ref for p in conditions.pieces(m)} | {f"knows:{n.id}:{i}" for n in m.npcs for i in range(len(n.knows))}
            | {f"history:{i}" for i in range(len(m.history))} | {f"mechanics:{i}" for i in range(len(m.mechanics))})


def held(m: Module, name: str, found: list[tuple[Any, ...]]) -> list[conditions.Finding]:
    """What of a pass's edits gets round the module's conditions (module/conditions.py)."""
    if name not in ("npcs", "agendas", "foreshadow") or not found:
        return []
    after = preview(m, found)
    if name == "npcs":
        chosen = [p for e in found if e[0] == "put" and e[3] == "knows" for p in conditions.knows(after, e[2])]
    else:
        old = {p.ref for p in conditions.pieces(m)}
        chosen = [p for p in conditions.pieces(after) if p.ref not in old]
    out = conditions.findings(after, chosen)
    if name == "agendas":  # a condition the runtime cannot read is no premise kept: written again, never cut down
        from .graph import _waits

        steps = {f"agendas:{a.npc}:{s.id}": s for a in after.agendas for s in a.steps}
        for p in chosen:
            if (s := steps.get(p.ref)) is None:
                continue
            atoms = [x.strip().lstrip("!") for group in s.when.split("|") for x in group.split("&") if x.strip()]
            bad = _waits(after, atoms, s.when) + [f"标记 {x[5:]} 没有声明" for x in atoms if x.startswith("flag:") and x[5:] not in after.flags]
            if bad:
                out.append(conditions.Finding(p.ref, "error", f"{p.ref} 的条件 {s.when} 写法不对：{'；'.join(bad)}"))
    return out


def without(found: list[tuple[Any, ...]], refs: set[str]) -> list[tuple[Any, ...]]:
    """A pass's edits less the steps, signs and facts named in `refs`."""
    out: list[tuple[Any, ...]] = []
    for e in found:
        if e[0] == "append" and e[1] == "agendas":
            kept = [{**a, "steps": [x for x in a.get("steps", []) if f"agendas:{a['npc']}:{x['id']}" not in refs]} for a in e[2]]
            e = ("append", "agendas", [a for a in kept if a["steps"]])  # an agenda with nothing left to do is no agenda
        elif e[0] == "append" and e[1] == "foreshadow":
            e = ("append", "foreshadow", [f for f in e[2] if f"foreshadow:{f['id']}" not in refs])
        elif e[0] == "put" and e[3] == "knows":
            e = (*e[:4], [k for i, k in enumerate(e[4]) if f"knows:{e[2]}:{i}" not in refs])
        if (e[0] == "append" and not e[2]) or (e[0] == "put" and e[3] == "knows" and not e[4]):
            continue
        out.append(e)
    return out


def take_out(folder: Path, refs: list[str]) -> None:
    """Take steps, signs, past events and facts out of the module's files, by their refs."""
    m = Module.load(folder)
    knows: dict[str, set[int]] = {}
    past: list[int] = []
    rules: list[int] = []
    for ref in refs:
        kind, _, rest = ref.partition(":")
        if kind == "knows":
            npc, _, i = rest.rpartition(":")
            knows.setdefault(npc, set()).add(int(i))
        elif kind == "history":
            past.append(int(rest))
        elif kind == "mechanics":
            rules.append(int(rest))
    for ref in refs:
        kind, _, rest = ref.partition(":")
        if kind == "agendas":
            npc, _, step = rest.partition(":")
            path = _target(folder, "agendas")
            path.write_text(yamledit.drop_step(path.read_text(encoding="utf-8"), "agendas", npc, step), encoding="utf-8")
        elif kind == "foreshadow":
            path = _target(folder, "foreshadow")
            path.write_text(yamledit.drop_entry(path.read_text(encoding="utf-8"), "foreshadow", rest), encoding="utf-8")
    for group, gone in (("history", past), ("mechanics", rules)):
        if gone:
            path = _target(folder, group)
            text = path.read_text(encoding="utf-8")
            for i in sorted(set(gone), reverse=True):
                text = yamledit.drop(text, group, i)
            path.write_text(text, encoding="utf-8")
    for npc, gone in knows.items():
        path = _target(folder, "npcs")
        kept = [k for i, k in enumerate(m.npc(npc).knows) if i not in gone]
        path.write_text(yamledit.replace(path.read_text(encoding="utf-8"), "npcs", npc, "knows", kept), encoding="utf-8")


@dataclass
class Result:
    name: str
    written: int = 0
    problems: list[str] | None = None
    left_out: list[str] = field(default_factory=list)  # what got round a condition and was not written, or taken out by the review


async def adapt(folder: Path, *, passes: tuple[str, ...] = PASSES, cfg: config.Config | None = None,
                log: Callable[[str], None] = print, dry: bool = False) -> list[Result]:  # fmt: skip
    """Run the passes the module still needs, in waves (AFTER): a wave's calls at once, its edits one
    pass at a time, each held to the module's conditions first and taken back whole if the module
    no longer passes its checks; then the review of what this run added (verify)."""
    folder = Path(folder)
    cfg = cfg or config.load()
    results: list[Result] = []
    added: list[str] = []
    left = [name for name in passes if name != "verify"]
    while left:
        wave = [name for name in left if not set(AFTER.get(name, ())) & set(left)]
        left = [name for name in left if name not in wave]
        m = Module.load(folder)
        todo = [name for name in wave if wanted(m, name)]
        for name in wave:
            if name not in todo:
                log(f"[dim]{LABELS[name]}：已经有了[/]")
        if not todo:
            continue
        log(f"[bold]编译期改编[/] {len(todo)} 步一起问：{'、'.join(LABELS[x] for x in todo)}…")
        answers = await asyncio.gather(*(_ask(cfg, name, m, source=original(folder, _focus(m, name))) for name in todo),
                                       return_exceptions=True)
        for name, out in zip(todo, answers, strict=True):
            results.append(await _settle(folder, cfg, name, out, log=log, dry=dry, added=added))
    if "verify" in passes:
        results.append(await verify(folder, cfg, refs=added if len(passes) > 1 else None, log=log, dry=dry))
    return results


async def _settle(folder: Path, cfg: config.Config, name: str, out: Any, *, log: Callable[[str], None], dry: bool,
                  added: list[str]) -> Result:  # fmt: skip
    """Write one pass's answer: what gets round a condition asked again once with the reasons and
    then left out; the whole pass taken back if the module no longer passes its checks."""
    if isinstance(out, BaseException):
        log(f"  [yellow]✗ {LABELS[name]}：{type(out).__name__}[/]")
        return Result(name, problems=[f"{type(out).__name__}: {out}"[:200]])
    m = Module.load(folder)
    found = edits(m, name, out)
    wrong = held(m, name, found)
    if wrong and not dry:
        log(f"  [yellow]{LABELS[name]}：{len(wrong)} 条要再看一遍，带着原因重问一次[/]")
        try:
            again = await _ask(cfg, name, m, source=original(folder, _focus(m, name)),
                               problems=[f.text for f in wrong if f.level == "error"],
                               hints=[f.text for f in wrong if f.level != "error"])
            found = edits(m, name, again)
            wrong = held(m, name, found)
        except Exception as exc:  # the first answer stands, less what is surely wrong
            log(f"  [yellow]{LABELS[name]}：重问没有成功（{type(exc).__name__}）[/]")
    # Only what is surely wrong is left out; what words merely matched, the call that read the original
    # kept on a second look, and the review (verify) reads it once more.
    wrong = [f for f in wrong if f.level == "error"]
    left_out = [f.text for f in wrong]
    if wrong:
        found = without(found, {f.ref for f in wrong})
        for f in wrong:
            log(f"  [yellow]  没有写入 {f.ref}：{f.text[:90]}[/]")
    count = sum(len(e[2]) if e[0] == "append" else 1 for e in found)
    if dry or not found:
        log(f"  {LABELS[name]}：{count} 处" + ("（只看不写）" if dry else ""))
        return Result(name, count, left_out=left_out)
    before = {p: p.read_text(encoding="utf-8") if p.is_file() else None for p in {_target(folder, e[1]) for e in found}}
    errors_before = {str(i) for i in audit(m) if i.level == "error"}
    try:
        land(folder, found)
        now = Module.load(folder)
        new_errors = [str(i) for i in audit(now) if i.level == "error" and str(i) not in errors_before]
    except Exception as exc:  # malformed YAML, a missing entry: the pass is taken back
        new_errors = [f"{type(exc).__name__}: {exc}"[:200]]
    if new_errors:
        for path, text in before.items():
            path.unlink(missing_ok=True) if text is None else path.write_text(text, encoding="utf-8")
        log(f"  [yellow]✗ {LABELS[name]}：补上以后校验不过，没有写入（{new_errors[0]}）[/]")
        return Result(name, problems=new_errors, left_out=left_out)
    added += sorted(_refs(now) - _refs(m))
    log(f"  [green]✓ {LABELS[name]}[/]：补了 {count} 处")
    return Result(name, count, left_out=left_out)


VERIFY_SYSTEM = """你是 CoC 7 版跑团的资深守秘人，开团前核对编译期补写的条目有没有绕过原作的条件。下面先是整理好的守秘人资料和原文（条件以原文为准），
最后是这次要核对的补写条目。只挑这几种：
- 提前兑现：原作要检定、前置线索或交涉门槛才拿得到的东西，补写成人物见面就指出、交出、讲明，或写成别人早就知道的事；
- 混淆条件：拿调查员有了什么、知道了什么，当成东西已经到了 NPC 手里、NPC 也知道了；
- 凭空多出：原作没有的物件、获知途径或别的拿法（守秘人临场可以裁定，编译期不替他定）；
- 把检定或孤注一掷之后的某种演法，写成了一般的见面反应或日程；
- 规则清单（mechanics）和原文、事件或别的补写对不上（原文写锁窗后人影撞窗进来，规则却写进不来），或者检定、数值、遭遇表是原文没有的：守秘人会照它临场执行，不照原文的就列出来。
知道有这样东西、知道它在哪、实际找到、拿到手、读懂内容是五件事，原作写到哪一步就是哪一步；原作写明失败也能得到的，照原作。
写了 only_if、要守秘人裁定（ruling）或等线索找到以后（knows:players:clue:）才成立的，条件写对了就不算。
没有问题的不列，拿不准的不列。每条写编号，和它绕过了原作哪一条：哪条线索、什么检定，原文怎么写。"""


class Flag(BaseModel):
    ref: str = Field(description="有问题的那一条的编号，照抄【补写的条目】里方括号中的编号")
    problem: str = Field(description="它绕过了原作哪一条条件：哪条线索、什么检定或前置，原文怎么写；一句话")


class Verdict(BaseModel):
    flags: list[Flag] = Field(default_factory=list, max_length=16)


def _rows(m: Module, refs: set[str]) -> list[str]:
    """The added entries to review, each with its ref and all it hangs on."""
    rows = []
    for a in m.agendas:
        rows += [f"[agendas:{a.npc}:{s.id}] {a.npc} 的打算 {_when_row(s)}" for s in a.steps if f"agendas:{a.npc}:{s.id}" in refs]
    rows += [f"[foreshadow:{f.id}] 伏笔，在 {f.where}：{f.hint} → 指向 {f.pays_off}" for f in m.foreshadow if f"foreshadow:{f.id}" in refs]
    rows += [f"[history:{i}] 前史 {p.when}：{p.text}（牵涉 {'、'.join(p.who) or '无'}；另外知道的 {'、'.join(p.known_by) or '无'}）"
             for i, p in enumerate(m.history) if f"history:{i}" in refs]
    rows += [f"[knows:{n.id}:{i}] {n.name}知道：{k}" for n in m.npcs for i, k in enumerate(n.knows) if f"knows:{n.id}:{i}" in refs]
    rows += [f"[mechanics:{i}] 规则「{x.name}」（{x.executor}执行）：{x.rules}" for i, x in enumerate(m.mechanics) if f"mechanics:{i}" in refs]
    return rows


async def _judge(cfg: config.Config, material: str) -> Verdict:
    """The review's one call."""
    s = Session("importer", cfg.role("importer"), VERIFY_SYSTEM, schema=Verdict.model_json_schema())
    try:
        reply = await s.send(material)
        return Verdict.model_validate(reply.data or {})
    finally:
        await s.close()


async def verify(folder: Path, cfg: config.Config | None = None, *, refs: list[str] | None = None,
                 log: Callable[[str], None] = print, dry: bool = False) -> Result:  # fmt: skip
    """复核: what was added (this run's `refs`, or the whole situation layer when none are given:
    its steps, signs, past and rules, never the NPCs' sheets)
    held to the module's conditions by rule (module/conditions.py), then read against the original
    text by one call; what gets round one is taken out of the files."""
    folder = Path(folder)
    cfg = cfg or config.load()
    m = Module.load(folder)
    # The whole layer, when no run's refs are given: what the NPCs know is the module's own sheet then, not this.
    chosen = {r for r in _refs(m) if not r.startswith("knows:")} if refs is None else set(refs) & _refs(m)
    if not chosen:
        log(f"[dim]{LABELS['verify']}：这次没有新补的[/]")
        return Result("verify")
    pieces = [p for p in conditions.pieces(m) if p.ref in chosen]
    pieces += [p for n in m.npcs for p in conditions.knows(m, n.id) if p.ref in chosen]
    found_by_rule = conditions.findings(m, pieces)
    flagged = {f.ref: f.text for f in found_by_rule if f.level == "error"}  # surely wrong: out whatever the call says
    hints = [f"[{f.ref}] {f.text}" for f in found_by_rule if f.level != "error"]  # matched by words: the call decides
    log(f"[bold]{LABELS['verify']}[/] {len(chosen)} 条补写对照原文…")
    try:
        verdict = await _judge(cfg, f"{book(m)}{_source(original(folder, _focus(m, 'verify')))}\n\n【补写的条目】\n" + "\n".join(_rows(m, chosen))
                               + ("\n\n【程序按关键词找出的疑点（可能误报，对照原文判断，没问题就不列）】\n" + "\n".join(hints) if hints else ""))
    except Exception as exc:  # the rules' findings still stand
        log(f"  [yellow]{LABELS['verify']}：模型复核没有成功（{type(exc).__name__}），只按规则[/]")
        verdict = Verdict()
    for f in verdict.flags:
        if f.ref in chosen:
            flagged.setdefault(f.ref, f.problem.strip())
    found = [f"{ref}：{why}" for ref, why in flagged.items()]
    for line in found:
        log(f"  [yellow]{'（只看不改）' if dry else '拿掉'} {line[:120]}[/]")
    if dry or not flagged:
        if not flagged:
            log(f"  [green]✓ {LABELS['verify']}[/]：没有绕过原作条件的")
        return Result("verify", problems=found or None)
    files = {_target(folder, g) for g in ("agendas", "foreshadow", "history", "npcs")}
    before = {p: p.read_text(encoding="utf-8") for p in files if p.is_file()}
    errors_before = {str(i) for i in audit(m) if i.level == "error"}
    try:
        take_out(folder, list(flagged))
        new_errors = [str(i) for i in audit(Module.load(folder)) if i.level == "error" and str(i) not in errors_before]
    except Exception as exc:
        new_errors = [f"{type(exc).__name__}: {exc}"[:200]]
    if new_errors:
        for path, text in before.items():
            path.write_text(text, encoding="utf-8")
        log(f"  [yellow]✗ {LABELS['verify']}：拿掉以后校验不过，没有改（{new_errors[0]}）[/]")
        return Result("verify", problems=[*found, *new_errors])
    log(f"  [green]✓ {LABELS['verify']}[/]：拿掉了 {len(flagged)} 条")
    return Result("verify", len(flagged), problems=found, left_out=found)


# -- how ready a module is to play -------------------------------------------------------------
@dataclass
class Score:
    total: int  # 0–100
    parts: dict[str, tuple[int, int, str]]  # name -> (points, out of, why)


def score(m: Module) -> Score:
    """A module's readiness for play, from its structure alone: no model call. Not whether the
    fields are filled but whether a keeper can run it as a situation:

        校验      no errors
        推演      everything in it reachable, no conclusion hanging on one hidden place or one person
                  (module/simulate.py: a playthrough with no model)
        线索网    every conclusion reachable three ways, and not all of them in one place
        人物      each NPC with a want, a manner, a voice and something to say
        冲突      the important people at odds or bound to someone else, not only to the party
        局面      what they mean to do moves on its own: some of it at an hour, not only on the party's cue
        伏笔      early signs, as many as there are conclusions, and leading to more than one
        牵连      threads for the kinds of background an investigator has
        结局      more than one way out, and not every one waiting on the party
    """
    from .simulate import playthrough

    npcs = {n.id: n for n in m.npcs}
    run = playthrough(m)
    support = {k: [c for c in v if c in run.reach.clues] for k, v in supporting_clues(m).items()}
    where = {c.id: c.npc or c.where for c in m.clues}
    errors = [i for i in audit(m) if i.level == "error"]
    parts: dict[str, tuple[int, int, str]] = {}

    def part(name: str, share: float, out_of: int, why: str) -> None:
        parts[name] = (round(max(0.0, min(1.0, share)) * out_of), out_of, why)

    part("校验", 0.0 if errors else 1.0, 10, f"{len(errors)} 个错误" if errors else "通过")
    part("推演", run.share(), 15, f"{len(run.issues)} 处问题" if run.issues else "走得通，没有只靠一处的结论")
    shares = []
    for clues in support.values():
        spread = len({where[c] for c in clues})
        shares.append(min(1.0, len(clues) / 3) * (1.0 if spread >= 2 else 0.6))
    three = sum(len(v) >= 3 for v in support.values())
    part("线索网", sum(shares) / len(shares) if shares else 0, 15,
         f"{three}/{len(support)} 个结论有三条以上拿得到的线索" + ("" if all(x == 1 for x in shares) else "；有的只出自一个地方或一个人"))  # fmt: skip
    full = sum(bool(n.personality and n.voice and n.goal and n.knows) for n in npcs.values())
    part("人物", full / len(npcs) if npcs else 0, 10, f"{full}/{len(npcs)} 个 NPC 性格、口吻、想要的、知道的都有")
    important = {n.id for n in npcs.values() if n.important} | {a.npc for a in m.agendas if a.npc in npcs}
    bound = {x for b in m.relations if b.a in npcs and b.b in npcs and abs(b.value) >= 30 for x in (b.a, b.b)}
    at_odds = any(b.a in npcs and b.b in npcs and b.value <= -30 for b in m.relations)
    tied = len(important & bound) / len(important) if important else 0
    part("冲突", tied * (1.0 if at_odds else 0.6), 15,
         f"{len(important & bound)}/{len(important)} 个重要 NPC 和别的 NPC 有分量的关系" + ("" if at_odds else "；没有谁和谁真正对立"))  # fmt: skip
    steps = [s for a in m.agendas for s in a.steps]
    timed = sum(bool(re.fullmatch(r"D\d+ \d{1,2}:\d{2}", s.when.strip())) for s in steps)
    moving = min(1.0, len(m.agendas) / 3) * (1.0 if timed else 0.5)
    part("局面", moving, 20, f"{len(m.agendas)} 个 NPC 有自己的打算，{timed} 步到点就做" + ("" if timed else "（全靠调查员触发，局面不会自己动）"))
    aims = {f.pays_off for f in m.foreshadow}
    part("伏笔", min(1.0, len(m.foreshadow) / max(3, len(m.conclusions))) * (1.0 if len(aims) >= 2 else 0.6), 5,
         f"{len(m.foreshadow)} 条，指向 {len(aims)} 处")  # fmt: skip
    kinds = {k for t in m.ties for k in t.fits}
    part("牵连", min(1.0, len(m.ties) / 4) * min(1.0, len(kinds) / 3), 5, f"{len(m.ties)} 条，覆盖 {len(kinds)} 类背景")
    ends = run.reach.endings
    coming = any(how == "事件" for how in ends.values())
    part("结局", (len(ends) - 1) / 2 * (1.0 if coming else 0.6), 5,
         f"{len(ends)}/{len(m.endings)} 个到得了" + ("" if coming else "；没有一个会随时间或事件自己到来"))  # fmt: skip
    return Score(sum(p[0] for p in parts.values()), parts)


REVIEW_SYSTEM = """你是 CoC 7 版跑团的资深守秘人，替一个准备开团的同行审读模组。只挑跑起来会出问题的地方：
- 关键信息只有一条路，玩家错过就卡死；
- 前后矛盾：时间、地点、谁知道什么对不上；
- 人物动机说不通，或者没人插手时局面不会动；
- 原文写得含糊、守秘人当场没法裁定的规则或情形；
- 补写的局面（人物知道的事、打算、见面反应、伏笔）绕过了原作的条件：不用检定就交出线索，拿调查员的进展当成 NPC 已经知道。
每条写清在哪、为什么会出问题、怎么补（补，不改原作真相）。没有问题的方面不要写。最多 8 条，按严重程度排。"""


class Finding(BaseModel):
    where: str = Field(description="在哪：地点、NPC、线索、事件或结局的 id，或“整体”")
    problem: str = Field(description="跑起来会出什么问题，一句话")
    fix: str = Field(description="怎么补，一句话；不改原作真相")
    severity: Literal["高", "中", "低"] = "中"


class Review(BaseModel):
    findings: list[Finding] = Field(default_factory=list, max_length=8)


async def review(folder: Path, cfg: config.Config | None = None) -> Review:
    """A keeper's read of the module before the table (a report: nothing is written)."""
    cfg = cfg or config.load()
    m = Module.load(folder)
    s = Session("importer", cfg.role("importer"), REVIEW_SYSTEM, schema=Review.model_json_schema())
    try:
        reply = await s.send(f"{book(m)}{_source(original(folder, _focus(m, 'review')))}")
        return Review.model_validate(reply.data or {})
    finally:
        await s.close()
