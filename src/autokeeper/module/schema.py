"""Structured scenario format.

A module is the keeper's whole book in data: locations, NPCs, clues and what they
point to, handouts, timed events and endings. Imported modules and generated ones
end up in the same format, are stored as modules/<id>/module.yaml and compiled into
an Agent Skill package for progressive disclosure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ..rules.coc import Investigator


class Check(BaseModel):
    skill: str
    difficulty: Literal["常规", "困难", "极难"] = "常规"
    bonus: int = 0


class Clue(BaseModel):
    id: str
    title: str = Field(description="线索本里显示的短标题")
    text: str = Field(description="调查员得到的信息（玩家可见）")
    where: str = Field(description="所在地点 id")
    feature: str = Field("", description="具体在哪：书桌、木匣、某个 NPC 口中")
    check: Check | None = Field(None, description="需要的检定；为空表示搜到即得")
    npc: str | None = Field(None, description="由哪个 NPC 提供")
    core: bool = Field(False, description="关键线索：拿不到就推进不下去")
    handout: str | None = None
    points_to: list[str] = Field(default_factory=list, description="支持哪些结论 id")
    sanity: str | None = Field(None, description="发现时的理智检定，如 0/1D3")


class Conclusion(BaseModel):
    id: str
    text: str


class NPC(BaseModel):
    id: str
    name: str
    role: str
    look: str = ""
    personality: str = ""
    voice: str = Field("", description="说话方式、口头禅")
    goal: str = ""
    knows: list[str] = Field(default_factory=list, description="这个 NPC 知道的事")
    secrets: list[str] = Field(default_factory=list, description="不被逼问不会说的事")
    lies: list[str] = Field(default_factory=list, description="会主动撒的谎")
    clues: list[str] = Field(default_factory=list, description="能给出的线索 id")
    gates: dict[str, Check] = Field(default_factory=dict, description="线索 id -> 需要的社交检定")
    location: str | None = None
    portrait: str | None = None
    stats: dict[str, int] = Field(default_factory=dict)


class Location(BaseModel):
    id: str
    name: str
    read_aloud: str = Field(description="调查员到达时看到的（玩家可见）")
    keeper: str = Field("", description="守秘人须知")
    features: list[str] = Field(default_factory=list, description="可以调查的东西")
    exits: list[str] = Field(default_factory=list)
    art: str | None = None


class Handout(BaseModel):
    id: str
    title: str
    kind: str = "信件"
    text: str


class Creature(BaseModel):
    id: str
    name: str
    description: str
    sanity: str = "0/1D6"
    stats: dict[str, int] = Field(default_factory=dict)
    attacks: list[str] = Field(default_factory=list)
    notes: str = ""


class Event(BaseModel):
    id: str
    title: str
    when: str = Field(description="触发条件：'D1 21:00' 表示时间，'flag:x' 表示标记，'visit:地点id' 表示到达")
    text: str = Field(description="守秘人须知：发生了什么")
    read_aloud: str = ""
    sets: list[str] = Field(default_factory=list)
    sanity: str | None = None
    move_to: str | None = None
    performer: str | None = Field(None, description="由哪个 NPC 当场表演（例如说书）")
    performance: str = Field("", description="表演要求，NPC 会结合本局发生过的事现编")
    ending: str | None = Field(None, description="触发时若还没有结局，就进入这个结局")


class SketchPrompt(BaseModel):
    id: str
    ask: str = Field(description="请玩家画什么")
    target: str = Field(description="真正的样子（读图员拿来比对）")
    threshold: float = 0.6
    sanity: str | None = Field(None, description="画得足够像时的理智检定")
    clue: str | None = Field(None, description="画得足够像时获得的线索")
    after: str | None = Field(None, description="哪条线索出现后才能画")


class Ending(BaseModel):
    id: str
    title: str
    condition: str
    text: str
    reward: str = ""


class Module(BaseModel):
    id: str
    title: str
    subtitle: str = ""
    system: str = "CoC7"
    era: str = ""
    setting: str = ""
    players: str = "1-3"
    duration: str = ""
    source: str = ""
    license: str = ""
    hook: str = Field(description="开场钩子（玩家可见）")
    truth: str = Field(description="真相（只有守秘人知道）")
    keeper_notes: str = ""
    flags: dict[str, str] = Field(default_factory=dict, description="剧情标记 -> 含义，结局和事件据此判断")
    start: str
    start_time: str = "D1 14:00"
    opening: str = ""
    locations: list[Location]
    npcs: list[NPC] = Field(default_factory=list)
    clues: list[Clue] = Field(default_factory=list)
    conclusions: list[Conclusion] = Field(default_factory=list)
    handouts: list[Handout] = Field(default_factory=list)
    creatures: list[Creature] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    endings: list[Ending] = Field(default_factory=list)
    sketches: list[SketchPrompt] = Field(default_factory=list)
    pregens: list[Investigator] = Field(default_factory=list)
    art: dict[str, str] = Field(default_factory=dict, exclude=True)
    art_colors: dict[str, str] = Field(default_factory=dict, exclude=True)
    pictures: dict[str, str] = Field(default_factory=dict, exclude=True)  # art name -> art/<name>.jpg

    # -- lookups ------------------------------------------------------------
    def location(self, id: str) -> Location:
        return _find(self.locations, id, "地点")

    def npc(self, id: str) -> NPC:
        return _find(self.npcs, id, "NPC")

    def clue(self, id: str) -> Clue:
        return _find(self.clues, id, "线索")

    def handout(self, id: str) -> Handout:
        return _find(self.handouts, id, "手稿")

    def npcs_at(self, location: str) -> list[NPC]:
        return [n for n in self.npcs if n.location == location]

    def clues_at(self, location: str) -> list[Clue]:
        return [c for c in self.clues if c.where == location]

    # -- io -------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> Module:
        path = Path(path)
        folder = path if path.is_dir() else path.parent
        file = folder / "module.yaml" if path.is_dir() else path
        module = cls.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")))
        art_dir = folder / "art"
        if art_dir.is_dir():
            module.art = {p.stem: p.read_text(encoding="utf-8").rstrip("\n") for p in sorted(art_dir.glob("*.txt"))}
            module.art_colors = {p.stem: p.read_text(encoding="utf-8").rstrip("\n") for p in sorted(art_dir.glob("*.color"))}
            module.pictures = {p.stem: str(p) for p in sorted(art_dir.glob("*.jpg")) if not p.stem.endswith(".glitch")}
        return module

    def save(self, folder: Path) -> Path:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json", exclude_defaults=False)
        file = folder / "module.yaml"
        file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
        if self.art:
            (folder / "art").mkdir(exist_ok=True)
            for name, text in self.art.items():
                (folder / "art" / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
            for name, mask in self.art_colors.items():
                (folder / "art" / f"{name}.color").write_text(mask + "\n", encoding="utf-8")
        return file


def _find(items, id: str, kind: str):
    for item in items:
        if item.id == id:
            return item
    raise KeyError(f"模组里没有 id 为“{id}”的{kind}")
