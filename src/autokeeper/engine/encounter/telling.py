"""How a fight or a chase is told: what the keeper is asked to write, and the account in its words.

The units of an encounter (engine/encounter/runner.py) write their lines in keeper form, with
`{id}` where someone is named, so the same line can be read out with the true names (the keeper's
report, which also gets a name map: who the players know by another name) or with the names the
players use (the screens). The player's own words never get here: an action is its neutral
summary (the keeper plan's, or the button the player pressed).

Danger the players can judge (the keeper-craft review, guide #24): what a creature's sheet makes it
(armour, guns that do little, wounds that close, many claws, sheer size) is told as what the
investigators see when it becomes visible, never as a number (`cues`).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .profiles import CombatProfile

FIGHT_TELL = ("下面是这一轮按先后结算出来的经过。照它写一段紧凑、有画面的叙述：谁先动手、打中了谁、伤成什么样，混乱和恐惧可以写，节奏要快。"
              "检定和伤害的数字已经显示给玩家，不要复述数字；只写结算出来的结果，不新添伤口、倒地、死亡或别的动作，"
              "不替调查员决定下一轮做什么。120–220 字。")
CHASE_TELL = ("下面是这一轮追逐按先后结算出来的经过。照它写一段急促的叙述：脚步、喘息、身后的动静、绊倒和翻越，"
              "前面还有什么路。数字已经显示过，不复述；只写结算出来的结果，不替调查员决定下一步。100–200 字。")
BEGIN_TELL = ("打斗或追逐刚刚开始，这一轮还没有人出手。写对方怎样扑上来、追上来，此刻的局面、距离和声响，紧张，有画面；"
              "不写谁打中了谁、谁受了伤，不替调查员决定怎么应对，显示过的数字不复述。80–160 字。")
WAIT_TELL = ("这一轮还没打完：下面是到现在为止结算出来的经过，接下来轮到调查员应对或出手。照它写，"
             "停在调查员要做决定的那一刻，不替他决定，数字不复述。80–180 字。")

ROUND_HEADS = ("【战斗 · 第", "【追逐 · 第", "【战斗结束", "【追逐结束", "【战斗转为追逐", "【追逐转为战斗")
_REF = re.compile(r"\{([^{}\s]+)\}")


def tell(lines: list[str]) -> str:
    """What the keeper is asked to write for a turn with a fight or a chase in it (Turn.fight): the
    round as settled, or how it begins when nothing is settled yet (an event's scripted attack)."""
    if not any(line.startswith(ROUND_HEADS) for line in lines):
        return BEGIN_TELL
    chase = any(line.startswith("【追逐") for line in lines) and not any(line.startswith("【战斗") for line in lines)
    return CHASE_TELL if chase else FIGHT_TELL


def render(text: str, names: dict[str, str]) -> str:
    """A keeper-form line with each `{id}` read out as `names` has it (unknown ids as they are)."""
    return _REF.sub(lambda m: names.get(m.group(1), m.group(1)), text)


def name_map(keeper: dict[str, str], players: dict[str, str]) -> str:
    """The report's line of who the players know by another name: 道格拉斯·金博尔（玩家所见：犬脸的佝偻身影）."""
    rows = [f"{keeper[i]}（玩家所见：{players[i]}）" for i in keeper if players.get(i) and players[i] != keeper[i]]
    if not rows:
        return ""
    return "【称呼】" + "；".join(rows) + "。叙述里只用玩家所见的叫法，真名和真相不说破"


# -- danger the players can judge ------------------------------------------------------------------
def first_sight(p: CombatProfile) -> list[str]:
    """What anyone sees of it at once: many claws, sheer size, a swarm, a hide like armour."""
    out = []
    if p.swarm:
        out.append("数不清的影子一齐涌过来，根本分不出哪一只是哪一只")
    elif p.per_round >= 2:
        out.append("它不止一只爪子能伸过来，一扑就是好几下")
    if p.build >= 4:
        out.append("那东西的块头大得吓人，压过来像一堵墙")
    elif p.build >= 2:
        out.append("它的个头比常人壮出一截")
    if p.armor >= 3:
        out.append("它身上像裹着一层硬壳，看着就不好下手")
    return out


def blow_cue(p: CombatProfile, *, firearm: bool, absorbed: int, taken: int) -> str:
    """What a blow that did little shows: a bullet it hardly feels, a hide that turns the blade."""
    if absorbed <= 0:
        return ""
    if firearm and p.guns_halved:
        return "子弹钻进它身体，它却像没怎么感觉到，照样逼过来"
    if taken <= 0:
        return "这一下落在它身上，像砍进了皮革，它连晃都没晃"
    return "这一下打是打中了，可它身上那层东西挡掉了大半"


def rising_cue() -> str:
    return "倒下的它身上的伤口在蠕动着合拢，它又撑着站了起来"


def cue(text: str) -> str:
    return f"【可观察】{text}（这是调查员看得到的样子，写进叙述，不说数字）"


__all__ = ["BEGIN_TELL", "CHASE_TELL", "FIGHT_TELL", "WAIT_TELL", "cue", "name_map", "render", "tell"]
