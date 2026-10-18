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

from ..plugins.schema import Plugin
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
    requires: list[str] = Field(default_factory=list, description="全部前置条件：线索 id，或 flag:标记；未满足时不能发出线索或附图")
    points_to: list[str] = Field(default_factory=list, description="支持哪些结论 id")
    sanity: str | None = Field(None, description="发现时的理智检定，如 0/1D3")

    def available(self, flags: list[str], clues: list[str]) -> bool:
        return all(key[5:] in flags if key.startswith("flag:") else key in clues for key in self.requires)


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
    arc: str = Field("", description="此刻的心境和正在发生的变化（角色弧光），会随剧情更新")


class Location(BaseModel):
    id: str
    name: str
    read_aloud: str = Field(description="调查员到达时看到的（玩家可见）")
    keeper: str = Field("", description="守秘人须知")
    features: list[str] = Field(default_factory=list, description="可以调查的东西")
    exits: list[str] = Field(default_factory=list)
    travel_minutes: dict[str, int] = Field(default_factory=dict, description="到相邻地点的分钟数；未写的路段为 10 分钟")
    art: str | None = None
    public: bool = Field(False, description="公开营业或可问路找到的地标，不包含秘密入口和待调查地点")
    weather: list[Literal["rain", "storm", "snow", "fog", "leaves", "dust", "embers", "flicker", "torch", "redlight", "warp"]] = Field(
        default_factory=list,
        description="画面上的天气与光：rain 雨、storm 雷雨、snow 雪、fog 雾或烟、leaves 风吹落叶、dust 光里的浮尘、"
        "embers 火星与火光、flicker 灯火摇曳、torch 只有手电或火把照亮、redlight 暗房红灯或红灯笼、warp 哈哈镜般的扭曲；"
        "最多两种，不写就按名字和描述推断",
    )


class Item(BaseModel):
    id: str
    name: str
    description: str = Field(description="物件可见的样子，不包含隐藏线索或未发生的效果")
    where: str
    owner: str = Field("", description="初始持有者 NPC id；无人持有则留空，物品在 where")
    art: str | None = None


class Handout(BaseModel):
    id: str
    title: str
    kind: str = "信件"
    text: str
    images: list[str] = Field(default_factory=list, description="原模组手稿/地图的图片键，对应 art/<键>.png 或 .jpg；只在该手稿解锁后展示")


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
    damage: str | None = Field(None, description="当场受到的伤害，如 1D3（由行动的调查员承受）")
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
    cover: str | None = Field(None, description="不含剧透的原作封面图片键；选择模组时以 ASCII 预览")
    plugins: list[str] = Field(default_factory=list, max_length=8, description="plugins/<id>/ 下经过测试的自定义规则插件")
    extensions: dict[str, Plugin] = Field(default_factory=dict, exclude=True)
    extension_errors: dict[str, str] = Field(default_factory=dict, exclude=True)
    hook: str = Field(description="开场钩子（玩家可见）")
    truth: str = Field(description="真相（只有守秘人知道）")
    keeper_notes: str = ""
    flags: dict[str, str] = Field(default_factory=dict, description="剧情标记 -> 含义，结局和事件据此判断")
    start: str
    start_time: str = "D1 14:00"
    opening: str = ""
    locations: list[Location]
    npcs: list[NPC] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
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
        module.extensions, module.extension_errors = {}, {}
        from ..plugins.storage import load as load_plugin

        for identity in module.plugins:
            try:
                plugin = load_plugin(folder / "plugins", identity)
                module.extensions[identity] = plugin
                for key, meaning in plugin.flags.items():
                    module.flags.setdefault(key, meaning)
            except (OSError, ValueError, TypeError) as exc:
                module.extension_errors[identity] = f"{type(exc).__name__}: {str(exc)[:500]}"
        art_dir = folder / "art"
        if art_dir.is_dir():
            module.art = {p.stem: p.read_text(encoding="utf-8").rstrip("\n") for p in sorted(art_dir.glob("*.txt"))}
            module.art_colors = {p.stem: p.read_text(encoding="utf-8").rstrip("\n") for p in sorted(art_dir.glob("*.color"))}
            module.pictures = {p.stem: str(p) for p in sorted(art_dir.iterdir())
                               if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and not p.stem.endswith((".glitch", ".prep"))}
        return module

    def save(self, folder: Path) -> Path:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json", exclude_defaults=False)
        file = folder / "module.yaml"
        file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
        if self.extensions:
            from ..plugins.storage import save as save_plugin

            for plugin in self.extensions.values():
                save_plugin(plugin, folder / "plugins")
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
