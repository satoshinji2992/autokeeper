"""The player's narrative preferences: how long, how dark, how fast, and what stays off screen.

SillyTavern's Author's Note, made safe. Only fixed choices exist: the keeper knows the
module's secrets, and free text would let a player talk to it. Every word an agent is
sent from here is one of ours, looked up by a validated key; a hand-edited file with
anything else in it falls back to the defaults.

Preferences change how a thing is told, never what happens. The intent parser and the
arbiter never see them, so dice, clue gates and sanity rules stay as they are. The keeper
gets them as a one-turn note (never stored, so its cached prefix stays the same), the
director keeps its new scenes out of the lines (雷区), and NPCs talk around them. A line
moves a thing off screen, implies it or cuts past it; it still happens.

    data/preferences.json    kept between games, like the cards in data/cards
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
)

from . import paths

LENGTHS = ("短", "中", "长")
HORRORS = ("克制", "标准", "浓烈")
PACES = ("舒缓", "标准", "紧凑")

# The lines a player can draw, with what each one covers (shown on the settings screen and
# told to the agents).
LINES: dict[str, str] = {
    "血腥细节": "伤口、血肉和尸体的样子",
    "虫": "成群的虫、蛆和蜘蛛",
    "蛇": "蛇和像蛇的东西",
    "溺水": "沉进水里、喘不上气",
    "幽闭": "被困、被活埋、挤在窄处出不去",
    "动物受伤": "猫狗马鸟受伤或死去",
    "自伤与自杀": "伤害自己、寻死",
    "身体恐怖": "身体变形、被寄生、长出不该长的东西",
    "儿童受害": "孩子受伤、失踪或死去",
    "性暴力": "性侵害和带性意味的威胁",
}

# What each choice asks of the keeper. The middle of each scale asks nothing, so a player
# who changes nothing gets exactly the prompts everyone else does.
LENGTH_NOTES = {
    "短": "篇幅短：优先交代行动与结果，普通回合通常 50–100 字；简单应答可更短。开场、插曲和结局采用较简洁的展开，不为达到字数下限增加描写。",
    "长": "篇幅长：充分展开与当前行动有关的环境、人物和过程，普通回合通常 150–280 字。开场、插曲和结局可更详细，但不为凑字数重复信息或增加线索。",
}
HORROR_NOTES = {
    "克制": "恐怖克制：对已有恐怖内容选择少量关键细节，理智损失简要呈现；平静场景不额外增添异常或惊恐反应。",
    "浓烈": "恐怖浓烈：充分呈现已有威胁及其感官细节，选择与现场相关的描写，不必逐项覆盖所有感官。理智反应与系统给定的损失相称，不为强化气氛增加伤害或异常。",
}
PACE_NOTES = {
    "舒缓": "节奏舒缓：给调查员慢慢摸索的余地，场景和人物多留一笔，不必每轮都加压。",
    "紧凑": "节奏紧凑：及时交代结果与关键变化，压缩重复过渡；有既定时限时清楚呈现压力，不额外制造时限或催促玩家选择。",
}
DIRECTOR_HORROR = {
    "克制": "恐怖克制：新写的演出点到为止，多写迹象和人的反应。",
    "浓烈": "恐怖浓烈：新写的演出可以更有压迫感，写得具体。",
}
DIRECTOR_PACE = {
    "舒缓": "节奏舒缓：保留日常活动与探索空间，既有事件按其条件发生，不为维持紧张而持续打断调查员。",
    "紧凑": "节奏紧凑：减少重复等待，在既有条件和时钟内及时推进事件，不无故新增危机或时限。",
}
NPC_HORROR = {"克制": "说到吓人的事点到为止。", "浓烈": "说到吓人的事可以说得具体些。"}


def _file():
    return paths.preferences()


class Prefs(BaseModel):
    """One player's choices. Only the values above exist; anything else reads as the default."""

    model_config = ConfigDict(frozen=True)

    length: Literal["短", "中", "长"] = "中"
    horror: Literal["克制", "标准", "浓烈"] = "标准"
    pace: Literal["舒缓", "标准", "紧凑"] = "标准"
    lines: tuple[str, ...] = ()  # keys of LINES, in LINES order

    @field_validator("length", "horror", "pace", mode="wrap")
    @classmethod
    def _known(cls, value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo) -> str:
        try:
            return handler(value)
        except ValidationError:  # not one of the choices: a hand-edited file
            return cls.model_fields[info.field_name or ""].default

    @field_validator("lines", mode="before")
    @classmethod
    def _listed(cls, value: Any) -> tuple[str, ...]:
        chosen = {v for v in value if isinstance(v, str)} if isinstance(value, (list, tuple, set)) else set()
        return tuple(k for k in LINES if k in chosen)

    # -- kept between games ---------------------------------------------------------------
    @classmethod
    def load(cls) -> Prefs:
        """The saved choices; the defaults when there are none or the file cannot be read."""
        try:
            data = json.loads(_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls.model_validate(data if isinstance(data, dict) else {})

    def save(self) -> None:
        path = _file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(self.model_dump_json(indent=1), encoding="utf-8")
        os.replace(tmp, path)  # a reader never sees half a file

    # -- what the agents are told -----------------------------------------------------------
    def _named(self) -> str:
        return "、".join(f"{k}（{LINES[k]}）" for k in self.lines)

    def for_keeper(self) -> str:
        """The keeper's one-turn note; empty when nothing differs from the defaults."""
        rows = [LENGTH_NOTES.get(self.length, ""), HORROR_NOTES.get(self.horror, ""), PACE_NOTES.get(self.pace, "")]
        if self.lines:
            rows.append(
                f"雷区：{self._named()}。碰到这些，事情照样发生、照样算数，只是不正面写：放到镜头外，"
                "用声音、旁人的反应或一句话带过，或者直接切到之后。线索里的信息照样交代清楚。"
            )
        rows = [r for r in rows if r]
        if not rows:
            return ""
        head = "【玩家的叙事偏好】只改写法：发生的事、检定结果、线索和理智损失都照常。"
        return "\n\n" + head + "\n" + "\n".join(f"- {r}" for r in rows)

    def for_director(self) -> str:
        """For the director, whose new scenes must stay out of the lines."""
        rows = [DIRECTOR_HORROR.get(self.horror, ""), DIRECTOR_PACE.get(self.pace, "")]
        if self.lines:
            rows.insert(0, f"雷区：{self._named()}。这次新写的人物、地点、线索、事件和后果都不碰这些题材；模组原有的照常发生，守秘人会换个写法。")
        rows = [r for r in rows if r]
        if not rows:
            return ""
        rule = "偏好只管怎么写，不改规则：理智检定、伤害和线索检定按平常的标准定，不因为偏好加重或减轻。"
        return "\n\n【玩家的叙事偏好】\n" + "\n".join(f"- {r}" for r in [*rows, rule])

    def for_npc(self) -> str:
        """For an NPC's lines and performances: talk around the lines, without holding back
        what it may say."""
        said = []
        if self.lines:
            said.append(f"玩家的雷区是{'、'.join(self.lines)}：说到这些事时含糊带过，不细说，但该说的信息照样说清楚。")
        if note := NPC_HORROR.get(self.horror):
            said.append(note)
        return f"\n（{''.join(said)}）" if said else ""
