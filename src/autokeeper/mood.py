"""An NPC's mood, read off their own line: the stage directions the NPC agent writes in
brackets ("（搓着手，声音压低了些）", "（猛地站起来）") and the words they say. No model: a
lexicon of cues, each worth a point or two for one mood, and the mood with the most points
wins. Directions count double, since they show how the NPC looks; what they say can be
meant for someone else ("别怕" calms the listener). Unsure (nothing found, or a tie) is 平静.

The mood never reaches the screen as words: it only shapes the speaker's portrait (colours
in artkit.TONES, a short motion in tui/speaker.py).

jieba finds where words begin and end. A one-character cue must be a word of its own, so 怕
is not found in 恐怕 or 不怕, nor 笑 in 可笑 or 冷笑; a longer one must begin or end where a
word does (发抖 in 浑身发抖, 救命 in 喊救命). A negation just before a cue (不生气, 没有慌)
cancels it.
Gestures the module gives the NPC as a habit (托马斯 "说话时爱搓手") say nothing about this
line, so a cue that the NPC's look already contains does not count.
"""

from __future__ import annotations

import logging
import re
import warnings
from functools import lru_cache

with warnings.catch_warnings():  # jieba 0.42 predates Python 3.12's stricter string escapes
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

jieba.setLogLevel(logging.WARNING)  # it reports loading its dictionary on stderr, under the TUI

CALM = "平静"
MOODS = (CALM, "警惕", "害怕", "发怒", "难过", "友善", "慌乱")

# mood -> {cue: points}. A cue is a word or a short regular expression.
CUES: dict[str, dict[str, int]] = {
    "警惕": {
        "警惕": 2, "警觉": 2, "戒备": 2, "提防": 2, "防备": 2, "狐疑": 2, "怀疑": 2, "审视": 2, "打量": 2, "嘘": 2,
        "压低(了)?声音": 2, "声音压低": 2, "四下(张望|看)": 2, "左右看了看": 2, "环顾": 2, "眯(起|着)?眼(睛)?": 2,
        "盯着你": 2, "皱(起|着|了皱)?眉(头)?": 1, "小声": 1, "低声": 1, "压低": 1, "盯着": 1, "犹豫": 1, "迟疑": 1,
        "冷冷(地)?": 1, "哼": 1, "警告": 1, "小心": 1, "当心": 1, "别声张": 1, "哪儿来的": 1, "干什么的": 1,
    },
    "害怕": {
        "发抖": 2, "颤抖": 2, "哆嗦": 2, "打颤": 2, "发颤": 2, "战栗": 2, "瑟瑟": 2, "惊恐": 2, "恐惧": 2, "害怕": 2,
        "吓(坏|呆|傻)": 2, "吓得": 2, "毛骨悚然": 2, "不寒而栗": 2, "魂飞魄散": 2, "面无血色": 2,
        "脸色(发白|煞白|惨白|一白)": 2, "(往后|向后)缩": 2, "缩成一团": 2, "怕": 1, "后退": 1, "退了一步": 1, "退后": 1,
        "缩了缩": 1, "冷汗": 1, "咽了(口|一口)?唾沫": 1, "求求": 1, "救命": 1, "不敢": 1, "僵住": 1,
    },
    "发怒": {
        "怒": 2, "大怒": 2, "愤怒": 2, "怒吼": 2, "怒道": 2, "怒视": 2, "暴怒": 2, "恼怒": 2, "发火": 2, "火冒三丈": 2,
        "气得": 2, "拍桌子": 2, "拍案": 2, "吼": 2, "吼道": 2, "咆哮": 2, "骂": 2, "骂道": 2, "瞪": 2, "瞪着": 2,
        "(攥|握)紧(了)?拳头": 2, "咬牙切齿": 2, "咬着牙": 2, "滚": 2, "滚出去": 2, "混账": 2, "放肆": 2, "找死": 2,
        "冷笑": 1, "嗤笑": 1, "冷哼": 1, "厉声": 1, "喝道": 1, "呵斥": 1, "揪住": 1, "胡说": 1, "胡扯": 1,
        "少废话": 1, "闭嘴": 1,
    },
    "难过": {
        "哭": 2, "哭泣": 2, "抽泣": 2, "啜泣": 2, "哽咽": 2, "抹(了抹)?眼泪": 2, "眼泪": 2, "泪水": 2, "泪": 2,
        "眼(圈|眶)(一)?红": 2, "红了眼(圈|眶)?": 2, "难过": 2, "伤心": 2, "悲伤": 2, "心碎": 2, "黯然": 2,
        "叹(了)?(口|一口)?气": 2, "长叹": 2, "叹息": 2, "(低|垂)(下|着)头": 1, "苦笑": 1, "沉默": 1, "声音低沉": 1,
        "唉": 1, "可怜": 1, "想念": 1, "落寞": 1, "失神": 1,
    },
    "友善": {
        "笑眯眯": 2, "笑呵呵": 2, "笑吟吟": 2, "微笑": 2, "笑着": 2, "笑了笑": 2, "热情": 2, "亲切": 2, "和蔼": 2,
        "欢迎": 2, "感谢": 2, "谢谢": 2, "多谢": 2, "请坐": 2, "请进": 2, "不客气": 2, "拍拍你的肩(膀)?": 2,
        "握住你的手": 2, "笑": 1, "点(了)?点头": 1, "点头": 1, "和气": 1, "温和": 1, "招手": 1, "递(给|过来)": 1,
        "放心": 1, "别客气": 1, "别怕": 1, "别担心": 1, "热茶": 1,
    },
    "慌乱": {
        "慌": 2, "慌张": 2, "慌乱": 2, "慌忙": 2, "惊慌": 2, "心慌": 2, "手忙脚乱": 2, "结结巴巴": 2, "结巴": 2,
        "语无伦次": 2, "支支吾吾": 2, "支吾": 2, "吞吞吐吐": 2, "手足无措": 2, "坐立不安": 2, "六神无主": 2,
        "急忙": 1, "连忙": 1, "搓(着|了搓)?手": 1, "擦(了擦)?汗": 1, "躲闪": 1, "避开(你的)?目光": 1, "踱(来踱去|步)": 1,
        "猛(地|然)站(了)?起来": 1, "赶紧": 1, "快走": 1, "快跑": 1, "糟了": 1, "怎么办": 1,
    },
}
DIRECTIONS = re.compile(r"[（(]([^（）()]*)[）)]")
STUTTER = re.compile(r"([\u4e00-\u9fff])[、…，]+\1")  # 我、我 / 没……没: a stammer, two points of 慌乱
NEGATIONS = ("不", "没", "没有", "别", "并不", "毫不", "从不", "不再", "不要", "不用", "不必", "未", "莫", "无需")


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[str, int, re.Pattern[str]]]:
    return [(mood, points, re.compile(cue)) for mood, cues in CUES.items() for cue, points in cues.items()]


def warm() -> None:
    """Load jieba's dictionary now (a second or so), not on the first line someone says."""
    jieba.initialize()


def _score(text: str, weight: int, habits: str, scores: dict[str, float]) -> None:
    if not text.strip():
        return
    starts, ends, at = set(), set(), 0
    for word in jieba.lcut(text):
        starts.add(at)
        at += len(word)
        ends.add(at)
    hits = []
    for mood, points, pattern in _patterns():
        if habits and pattern.search(habits):
            continue
        for m in pattern.finditer(text):
            bounded = (m.start() in starts, m.end() in ends)
            if (all(bounded) if len(m.group()) == 1 else any(bounded)) and not text[: m.start()].endswith(NEGATIONS):
                hits.append((m.end() - m.start(), m.start(), m.end(), mood, points))
    taken: set[int] = set()
    for _, start, end, mood, points in sorted(hits, key=lambda h: -h[0]):  # the longest cue wins where they overlap
        if taken.isdisjoint(range(start, end)):
            taken.update(range(start, end))
            scores[mood] += points * weight
    scores["慌乱"] += 2 * len(STUTTER.findall(text)) * weight


def read(text: str, *, habits: str = "", words: bool = True) -> str:
    """The mood of one line, one of MOODS. `habits` is the NPC's look: gestures it names are
    not this line's. `words=False` reads the directions alone (a performance, whose words
    are a story about other people)."""
    scores = dict.fromkeys(MOODS[1:], 0.0)
    _score(" ".join(DIRECTIONS.findall(text)), 2, habits, scores)
    if words:
        _score(DIRECTIONS.sub(" ", text), 1, habits, scores)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (best, top), (_, second) = ranked[0], ranked[1]
    return best if top >= 2 and top > second else CALM
