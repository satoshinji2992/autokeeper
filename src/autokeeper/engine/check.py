"""How the replay eval reads a telling against the record (eval/session.py, `autokeeper eval session`).

The world as a turn left it (account): who is here and who is in the other rooms of the building,
who walks with the investigators, what they and the people here carry, what this turn settled,
which signs of the module's foreshadowing the keeper was handed; and the rules a judge model reads
a turn's narration and NPC lines by. Nothing reads a telling in play any more (the user's call,
2026-10-20: no cutting down of what the model wrote); the keeper sees the backstage instead, and
what a turn could not bring about reaches it as notes.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..module.schema import Module
from ..world.context import rooms
from .state import GameState, format_time, time_of_day

GONE = ("死亡", "失踪")

RULES = """你核对一段跑团叙述和游戏后台的记录是否一致。记录是准的，叙述是要查的。只找确实矛盾的地方，不评文笔。

矛盾有这几种：
- 在场：叙述里有人此刻在调查员身边说话、动手、带路，记录里他在别处、已经死去或失踪；或者叙述写某人不在、找不到、没人应答，记录里他就在身边或在同一栋房子里。
- 同行：叙述写某人跟着调查员走、一起到了这里，记录里他没有同行，人也不在这里。
- 物品：调查员用了、拿着、掏出的东西，不在他的随身物品里，这一轮也没有得到，叙述也没交代是从哪来的。叙述交代了来处的日常物件（谁递给他、借给他，他在哪里顺手拿起的手电、手杖、火柴）不算矛盾，哪怕记录里还没有；NPC 拿出、抓起、递出自己的日常物件也不算。
- 发现：叙述里出现了这一轮结算之外的具体新物证（血迹、新的脚印、被拖拽或打斗的痕迹、遗落的物件、写着字的东西），而结算里没有这样的线索或事件。光线、声音、天气、气味、情绪和早已知道的东西不算。
- 时间：叙述的时刻、昼夜，或者一段事情花了多久，和记录明显对不上。
- 代做：叙述替调查员做了玩家这一轮没说的事：多出来的动作、话、态度或心里的想法，不是他说的行动本身就要做的；玩家要悄悄做的事，叙述写成了当众做。这一轮理智检定失败时一瞬间不由自主的反应（惊叫、僵住、往后缩）是规则的结果，不算代做。
- 升级：结算里没有的受伤、流血、理智损失、袭击、追赶或被人发现，叙述写成已经发生；或者把危险写得比记录里更近、更重（有东西扑上来、有人拔出武器），这一轮并没有这样的事件。
- 其他：把记录里没有发生的事当成已经发生；NPC 嘴上说此刻把东西交给调查员、答应同行或带路，记录里这一轮却没有结算这件事。

只核对记录里有名字的人；街上的行人、店铺开门关门、窗帘灯火这类寻常景象不算在场，也不算发现。回忆、转述、猜测，只是提到某人的名字，都不算在场。
这些不是矛盾，不要列：叙述写某人不在这里、没跟来、不见了、找不到、一路没见着，而记录里他确实不在调查员身边；同一处别的房间里的人隔着门应声、在门口探身，没有走进调查员所在的地方；记录里刚一起到达、同行已经结束、此刻就在身边的人。NPC 对事情的说法可以撒谎、可以不知道，不算矛盾；要看的是旁白叙述、NPC 的动作描写，以及 NPC 嘴上说此刻正在做的交接和同行。NPC 自己身上的东西不算调查员的物品。
逐条核对，核对下来一致的不要列；没有矛盾就留空。"""

# A reader that checked a line and found it agreed sometimes lists it anyway, saying so.
AGREED = re.compile(r"不矛盾|不构成矛盾|没有矛盾|并不矛盾|一致[。；，,]?$|一致，|所以一致|这句一致")


class Problem(BaseModel):
    kind: Literal["在场", "同行", "物品", "发现", "时间", "代做", "升级", "其他"]
    claim: str = Field(description="叙述里出问题的那一小段原文，不超过 40 字")
    record: str = Field(description="记录里是怎样的，一句话")


class Verdict(BaseModel):
    problems: list[Problem] = Field(default_factory=list, max_length=6, description="和记录矛盾的地方；没有就留空")


def _name(m: Module, ident: str) -> str:
    return next((x.name for x in [*m.locations, *m.npcs] if x.id == ident), ident)


def account(m: Module, st: GameState, turn: int, before: dict[str, Any], ruling: dict[str, Any] | None,
            hints: list[str] | None = None) -> str:
    """The world as the turn left it, for reading a telling against."""
    rec = st.record
    here = st.location
    minutes_before = int(before.get("minutes", st.minutes))
    came = before.get("location", here)
    near = rooms(m, here)  # the rest of the building, as the game reckons it
    people = [e for e in rec.entities.values() if e.kind == "npc" and e.status not in GONE]
    present = [e.name + (f"（{e.status}）" if e.status else "") for e in people if e.at == here]
    beside = [f"{e.name}在{_name(m, e.at)}" for e in people if e.at in near]
    along = [f"{e.name}（同行，去{_name(m, e.follow_to) or '同一处'}）" for e in people if getattr(e, "following", "")]
    gone = [f"{e.name}（{e.status}）" for e in rec.entities.values() if e.kind == "npc" and e.status in GONE]
    elsewhere = [f"{e.name}在{_name(m, e.at)}" for e in people if e.at and e.at != here and e.at not in near]
    lines = [
        f"时间：{format_time(st.minutes)}（{time_of_day(st.minutes)}），这一轮经过 {max(0, st.minutes - minutes_before)} 分钟",
        f"调查员所在：{_name(m, here)}" + (f"（同一处的其他房间：{'、'.join(_name(m, x) for x in sorted(near))}）" if near else ""),
        f"这一轮从{_name(m, came)}到了{_name(m, here)}" if came != here else f"这一轮没有离开{_name(m, here)}",
        f"此刻就在调查员身边的人：{'、'.join(present) or '没有别人'}",
        f"同一处别的房间里、不在眼前的人：{'、'.join(beside) or '没有'}",
        f"和调查员同行的人：{'、'.join(along) or '没有'}",
        f"在别处的人：{'、'.join(elsewhere) or '无'}",
    ]
    if gone:
        lines.append(f"已经不在了的人：{'、'.join(gone)}")
    for inv in st.investigators:
        lines.append(f"{inv.name}的随身物品：{'、'.join(inv.inventory) or '没有登记的东西'}")
    held = [f"{_name(m, e.at)}：{e.name}" for e in rec.entities.values()
            if e.kind == "item" and e.at in {p.id for p in people if p.at == here}]
    if held:
        lines.append(f"身边的人自己带着的东西：{'；'.join(held)}")
    found = [c.title for c in m.clues if c.id in st.clues]
    if found:
        lines.append(f"调查员早已找到的线索（不算新发现）：{'、'.join(found)}")
    settled = [f"{e.kind}：{e.text[:160]}" for e in st.log if e.turn == turn and e.kind in ("dice", "clue", "handout", "event", "sanity", "damage")]
    lines.append("这一轮结算的结果：\n" + ("\n".join(f"- {x}" for x in settled) or "- 没有检定，没有新线索，没有事件"))
    if ruling and ruling.get("reason"):
        lines.append(f"裁定员对这一轮的说明（能有什么结果）：{ruling['reason']}")
    if hints:  # the module's foreshadowing, handed to the keeper this turn: telling it is no invention
        lines.append("这一轮守秘人可以露出的端倪（照写不算凭空补的物证）：\n" + "\n".join(f"- {h}" for h in hints))
    return "\n".join(lines)


def prompt(record: str, said: str, keeper: str, lines: list[str]) -> str:
    npc = "\n".join(lines) or "（这一轮没有 NPC 开口）"
    return f"【记录：这一轮结束时】\n{record}\n\n【玩家这一轮说】{said}\n\n【NPC 的话】\n{npc}\n\n【守秘人的叙述】\n{keeper or '（没有叙述）'}"


def standing(found: list[Problem]) -> list[Problem]:
    """What a reading found, without the lines it checked and found agreed."""
    return [p for p in found if not AGREED.search(p.record)]

