"""Weather in words: what a place's description implies, whether a place is open to the
sky, and how the keeper's narration changes the sky. Drawing it is atmosphere.py's job;
this module is plain text, so the engine can use it.
"""

from __future__ import annotations

import re

KINDS = {
    "rain": "雨",
    "storm": "雷雨",
    "snow": "雪",
    "fog": "雾或烟",
    "leaves": "风吹落叶",
    "dust": "光里的浮尘",
    "embers": "火星与火光",
    "flicker": "灯火摇曳",
    "torch": "只有手电或火把照亮",
    "redlight": "暗房红灯或红灯笼的光",
    "warp": "哈哈镜般的扭曲",
}
DARK = {"傍晚", "夜里", "深夜"}
DUSK_TO_DAWN = {"清晨", "傍晚", "夜里", "深夜"}


def infer(name: str, text: str, when: str = "") -> list[str]:
    """Weather read off a location's name and description, at most two kinds. Rain and snow
    only fall under the open sky: a post office whose windows show the snow has none inside."""
    words = name + text
    place = name.split("·")[-1]
    inside = any(w in place for w in INDOOR) and not any(w in place for w in OPEN)  # clearly a room, not just unnamed

    def has(*ws: str) -> bool:
        return any(w in words for w in ws)

    kinds = []
    if has("雷", "闪电", "暴雨", "暴风雨"):
        kinds.append("storm")
    elif has("雨") and not has("雨停", "雨后", "雨过", "雨伞", "雨衣") and not inside:
        kinds.append("rain")
    if has("雪") and not has("雪白", "雪亮", "雪茄") and not inside:
        kinds.append("snow")
    if has("哈哈镜") or (has("镜") and has("变形", "扭曲", "忽胖忽瘦")):
        kinds.append("warp")
    if has("红灯"):  # a darkroom's safelight, red lanterns
        kinds.append("redlight")
    if has("雾", "蒸汽", "水汽", "缭绕") or (any(w in name for w in ("墓", "坟", "沼", "湖", "河", "林")) and when in DUSK_TO_DAWN):
        kinds.append("fog")
    if has("手电", "火把", "提灯", "马灯") and has("黑", "暗", "隧道", "地道", "地下", "洞", "地窖", "通道", "只能照"):
        kinds.append("torch")
    if has("叶") and has("飘", "旋", "落", "风"):
        kinds.append("leaves")
    if has("壁炉", "炉火", "篝火", "火堆", "火盆"):
        kinds.append("embers")
    if has("灰尘", "尘土", "积灰", "落满灰", "尘埃", "浮尘"):
        kinds.append("dust")
    if when in DARK and has("台灯", "油灯", "煤油灯", "蜡烛", "烛", "灯笼", "灯火", "路灯") and "torch" not in kinds:
        kinds.append("flicker")
    return kinds[:2]


OPEN = ("弄堂", "街口", "路口", "院子", "天台", "码头")  # outdoors, though 堂 or 口 might say otherwise
INDOOR = ("室", "屋", "房", "馆", "厅", "店", "铺", "社", "局", "堂", "宅", "家", "楼", "间", "棚", "隧", "洞", "窖", "舱", "库", "所")
OUTDOOR = ("街", "路", "巷", "墓", "坟", "园", "场", "野", "林", "山", "河", "湖", "港", "桥", "岸", "镇", "村", "田", "海", "滩", "坡")


def outdoors(name: str) -> bool:
    """Open to the sky, going by the name: 公墓 and 后弄堂 are, 书房 and 警察局 are not. In
    "霞飞路·西点铺" the part after the dot is the place itself."""
    place = name.split("·")[-1]
    if any(w in place for w in OPEN):
        return True
    return not any(w in place for w in INDOOR) and any(w in place for w in OUTDOOR)


# What the keeper's words do to the sky, in the order they are said. Rain during a storm is
# still the storm; "-thunder" turns a storm into rain, "-rain" clears rain and storms,
# "-all" clears everything.
SKY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"雷声|惊雷|闷雷|闪电(?!般)|雷雨|暴雨"), "storm"),  # not 手电光, not 闪电般
    (re.compile(r"下起了?雨|(?<![要会快])下雨|雨下得|雨越下|雨点|雨丝|细雨|小雨|大雨|冷雨|雨幕|雨声|落雨|飘雨|阵雨|夜雨|秋雨|春雨"), "rain"),
    (re.compile(r"下起了?雪|(?<![要会快])下雪|雪花|飘雪|大雪|小雪|风雪|落雪"), "snow"),
    (re.compile(r"(?<![烟喷])雾(?![气散淡退水])|雾气(?![散淡退])"), "fog"),  # not 烟雾 (smoke), not 一头雾水
    (re.compile(r"雷声(渐渐)?(停|远|歇|小)|雷停"), "-thunder"),
    (re.compile(r"雨停|雨住了|雨歇|雨已经停"), "-rain"),
    (re.compile(r"雪停|雪住了"), "-snow"),
    (re.compile(r"雾气?(渐渐)?[散淡退]"), "-fog"),
    (re.compile(r"放晴|天晴了|雨过天晴|云开"), "-all"),
]
LASTS = 180  # game minutes a mentioned sky holds without being mentioned again


def sky(narration: str, current: str = "") -> tuple[str, bool]:
    """The sky after a stretch of narration, and whether the narration spoke of it at all.
    The last mention wins: "雨停了" clears rain, "雾散了" clears fog."""
    found = sorted((m.start(), -len(m.group()), action) for pattern, action in SKY for m in pattern.finditer(narration))
    said, end = [], -1
    for start, length, action in found:  # a longer phrase swallows the words inside it: 雷声渐渐远 is not 雷声
        if start >= end:
            said.append(action)
            end = start - length
    for action in said:
        if action == "rain" and current == "storm":
            continue
        if action == "-thunder":
            current = "rain" if current == "storm" else current
        elif not action.startswith("-"):
            current = action
        elif action == "-all" or current == action[1:] or (action == "-rain" and current == "storm"):
            current = ""
    return current, bool(said)
