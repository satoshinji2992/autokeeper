"""Names and base values of CoC 7th edition characteristics and 1920s skills (Chinese)."""

from __future__ import annotations

CHARACTERISTICS = {
    "力量": "STR",
    "体质": "CON",
    "体型": "SIZ",
    "敏捷": "DEX",
    "外貌": "APP",
    "智力": "INT",
    "意志": "POW",
    "教育": "EDU",
}

# Rolls that use a characteristic or pool rather than a skill.
CHARACTERISTIC_ALIASES = {
    "灵感": "智力",
    "知识": "教育",
    "STR": "力量",
    "CON": "体质",
    "SIZ": "体型",
    "DEX": "敏捷",
    "APP": "外貌",
    "INT": "智力",
    "POW": "意志",
    "EDU": "教育",
}

# Base chances; "敏捷/2" style entries are derived from characteristics.
SKILL_BASE: dict[str, int | str] = {
    "会计": 5,
    "人类学": 1,
    "估价": 5,
    "考古学": 1,
    "艺术与手艺": 5,
    "魅惑": 15,
    "攀爬": 20,
    "信用评级": 0,
    "克苏鲁神话": 0,
    "乔装": 5,
    "闪避": "敏捷/2",
    "汽车驾驶": 20,
    "电气维修": 10,
    "话术": 5,
    "格斗(斗殴)": 25,
    "射击(手枪)": 20,
    "射击(步枪/霰弹枪)": 25,
    "急救": 30,
    "历史": 5,
    "恐吓": 15,
    "跳跃": 20,
    "外语": 1,
    "母语": "教育",
    "法律": 5,
    "图书馆使用": 20,
    "聆听": 20,
    "锁匠": 1,
    "机械维修": 10,
    "医学": 1,
    "博物学": 10,
    "导航": 10,
    "神秘学": 5,
    "操作重型机械": 1,
    "说服": 10,
    "驾驶": 1,
    "精神分析": 1,
    "心理学": 10,
    "骑术": 5,
    "科学": 1,
    "妙手": 10,
    "侦查": 25,
    "潜行": 20,
    "生存": 10,
    "游泳": 20,
    "投掷": 20,
    "追踪": 10,
}

SKILL_ALIASES = {
    "侦察": "侦查",
    "观察": "侦查",
    "图书馆": "图书馆使用",
    "查资料": "图书馆使用",
    "斗殴": "格斗(斗殴)",
    "格斗": "格斗(斗殴)",
    "手枪": "射击(手枪)",
    "步枪": "射击(步枪/霰弹枪)",
    "霰弹枪": "射击(步枪/霰弹枪)",
    "开锁": "锁匠",
    "撬锁": "锁匠",
    "躲藏": "潜行",
    "隐匿": "潜行",
    "开车": "汽车驾驶",
    "游说": "说服",
    "快速交谈": "话术",
    "信誉": "信用评级",
    "信用": "信用评级",
    "克苏鲁": "克苏鲁神话",
    "CM": "克苏鲁神话",
    "汽车": "汽车驾驶",
    "电脑": "计算机使用",
    "计算机": "计算机使用",
    "自然学": "博物学",
    "领航": "导航",
    "重型机械": "操作重型机械",
    "重型操作": "操作重型机械",
    "语言": "母语",
}

# Skills that never get an improvement tick.
NO_IMPROVEMENT = {"克苏鲁神话", "信用评级"}

# Skills that can never be pushed, and rolls that are not skills at all.
UNPUSHABLE = {"理智", "幸运", "克苏鲁神话"}


def canonical(name: str) -> str:
    """Normalise a skill or characteristic name typed by a player or produced by a model."""
    name = name.strip().replace("（", "(").replace("）", ")").replace(" ", "")
    if name in CHARACTERISTIC_ALIASES or name.upper() in CHARACTERISTIC_ALIASES:  # .st lines write str60 as often as STR60
        return CHARACTERISTIC_ALIASES.get(name) or CHARACTERISTIC_ALIASES[name.upper()]
    return SKILL_ALIASES.get(name) or SKILL_ALIASES.get(name.upper(), name)


def base_value(skill: str, characteristics: dict[str, int]) -> int:
    """Base chance of a skill nobody has invested in."""
    skill = canonical(skill)
    key = skill if skill in SKILL_BASE else skill.split("(")[0]
    base = SKILL_BASE.get(key, 1)
    if isinstance(base, int):
        return base
    stat, _, div = base.partition("/")
    value = characteristics.get(stat, 0)
    return value // int(div) if div else value
