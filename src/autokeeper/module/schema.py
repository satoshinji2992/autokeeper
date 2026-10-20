"""Structured scenario format.

A module is the keeper's whole book in data: locations, NPCs, clues and what they
point to, handouts, timed events and endings. Imported modules and generated ones
end up in the same format, are stored as a folder (module/package.py) and compiled into
an Agent Skill package for progressive disclosure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

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
    art: str = Field("", description="找到的那样东西的照片的图片键（art/items/<键>.jpg）；线索卡上配这张图，不剧透")

    def available(self, flags: list[str], clues: list[str]) -> bool:
        return all(key[5:] in flags if key.startswith("flag:") else key in clues for key in self.requires)


class Conclusion(BaseModel):
    id: str
    text: str


class Look(BaseModel):
    """How a place or a person looks once the story has changed them, for the picture."""

    when: str = Field(description="flag:标记；标记成立后，这个地点或人物改用这张图")
    change: str = Field(description="和平常相比画面上看得见的变化，比如“礼拜堂烧成焦黑的空壳，屋顶塌了一半”；不写剧透")
    art: str = Field("", description="这张图的图片键（art/<键>.jpg）；留空就用 <原图键>_<标记>，导入时由 Codex 照原图生成")

    def key(self, base: str) -> str:
        return self.art or f"{base}_{self.when.removeprefix('flag:')}"


class View(BaseModel):
    """Another picture of the same place: inside, a corner, by night. The scene switches to it when
    the party investigates what it shows, or at its hour."""

    key: str = Field(description="这张图的图片键（art/places/<键>.jpg），小写字母、数字和下划线")
    label: str = Field(description="拍的是哪儿，几个字：门厅、书房一角、后院、夜里的外景")
    features: list[str] = Field(default_factory=list, description="调查这些东西时换成这张图；写这个地点 features 里的名字")
    time: Literal["", "day", "night"] = Field("", description="只在白天（day）或夜里（night）用；不限就留空")
    prompt: str = Field("", description="画面里看得见什么，给找照片和画图用；不写人名，不剧透")


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
    looks: list[Look] = Field(default_factory=list, description="剧情让他明显变样之后的头像（受伤、变异、死去），最多两种")
    important: bool = Field(False, description="重要 NPC：有自己的目标和打算，调查员不在时也会行动；每个模组最多标 6 个，普通角色不标")
    status: SkipJsonSchema[str] = Field("", exclude=True)  # this game's: 受伤, 失踪, 死亡 (world/knowledge.project)


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
    aliases: list[str] = Field(default_factory=list, description="玩家可能用的别的叫法，比如门牌地址、主人的名字（托马斯家）")
    within: str | None = Field(None, description="在哪个地点里面：外面那个地点的 id，比如书房在某户人家里、陵墓在公墓里；"
                               "地图上会画在它里面。只写确实在里面的，相邻或同一条街上不算；不在别处里面就留空")
    weather: list[Literal["rain", "storm", "snow", "fog", "leaves", "dust", "embers", "flicker", "torch", "redlight", "warp"]] = Field(
        default_factory=list,
        description="画面上的天气与光：rain 雨、storm 雷雨、snow 雪、fog 雾或烟、leaves 风吹落叶、dust 光里的浮尘、"
        "embers 火星与火光、flicker 灯火摇曳、torch 只有手电或火把照亮、redlight 暗房红灯或红灯笼、warp 哈哈镜般的扭曲；"
        "最多两种，不写就按名字和描述推断",
    )
    looks: list[Look] = Field(default_factory=list, description="剧情让这里明显变样之后的场景图（烧毁、被淹、被洗劫），最多两种；昼夜不用写")
    views: list[View] = Field(default_factory=list, max_length=4, description="同一个地点的另外几张图：室内、近景、夜景，最多 4 张")


class Item(BaseModel):
    id: str
    name: str
    description: str = Field(description="物件可见的样子，不包含隐藏线索或未发生的效果")
    where: str
    owner: str = Field("", description="初始持有者 NPC 或调查员 id；无人持有则留空，物品在 where")
    important: bool = Field(True, description="只登记武器、钥匙、证物、关键工具和重要消耗品；普通生活物件可为 false")
    equipment: Literal["carried", "worn", "held", "concealed"] = Field("carried", description="携带、穿戴、手持或藏匿")
    quantity: int = Field(1, ge=0, description="重要物品数量；0 表示已耗尽，不逐发模拟普通弹药")
    status: str = Field("", max_length=100, description="实际状况，如损坏、未上锁；不增加物品能力")
    hidden: bool = Field(False, description="是否藏在场景中或不应随地点描述展示；NPC随身物本身不自动公开")
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
    art: str = Field("", description="这件事发生那一刻的图片键（art/places/<键>.jpg）；有就在场景里放这一张，直到调查员下一次行动")


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


class Showcase(BaseModel):
    """大场景: one big picture over the whole screen at a turning point (engine/showcase.py)."""

    id: str
    kind: Literal["开场", "突发", "危机", "重要场景", "怪物", "最终boss", "结局"] = Field(
        description="开场 开局的氛围；突发 突然发生的事；危机 生死关头；重要场景 关键地点；怪物 怪物现身；最终boss 最后的对手；结局 每个结局一张")
    when: str = Field(
        description="什么时候展示，每局最多一次：opening 开局；visit:地点id 第一次到达；event:事件id 事件发生；"
        "creature:怪物id 怪物的名字第一次出现在事件或叙述里；flag:标记 标记成立；ending:结局id 进入这个结局")
    title: str = Field(description="画面下方的短标题，不超过 12 个字；只说此刻玩家看得见的，不剧透")
    prompt: str = Field(description="画面描述，写给画师：画什么、构图、光线和气氛；只写看得见的，不写名字和幕后真相")
    art: str = Field("", description="模组自带的、正好合适的图片键（art/<键>.jpg），有就直接用这张")
    base: str = Field("", description="参考图的图片键：照着这张原图重画，比如这个地点或怪物的原图；留空就用触发的地点")
    improvised: SkipJsonSchema[bool] = Field(False, exclude=True)  # the director's, in this game: its picture is kept per save


class PreludeFrame(BaseModel):
    """One shot of the opening performance (开局演出): a picture and a line or two, played before
    the game screen while the director adapts the module (module/prelude.py)."""

    art: str = Field("", description="这一幕的图片键：封面、开场地点，或钩子和背景里已经点名的地方；没有合适的留空")
    text: str = Field(description="这一幕的字幕，一两句，只讲故事背景：时代、地方、气氛、事情的由来；不剧透，不写调查员会怎么做")


# -- the situation (局面): who wants what, and what comes of it if nobody steps in ---------------
class Bond(BaseModel):
    """How one person sees another: an edge of the conflict graph."""

    a: str = Field(description="谁：NPC id")
    b: str = Field(description="对谁：NPC id，或 players 表示调查员们")
    kind: str = Field(description="怎样的看法：信任、恐惧、欠情、敌对、盟友、亲属、雇佣、爱慕……")
    value: int = Field(0, ge=-100, le=100, description="多深，-100..100，负数是反过来")
    why: str = Field("", description="因为什么，一句话")


class Move(BaseModel):
    """One step of an agenda."""

    id: str = Field(description="步骤 id，英文小写加下划线")
    text: str = Field(description="这一步做什么，一句话")
    when: str = Field("", description="'D1 23:00' 表示到这个时间去做；也可以是条件 flag:标记、at:谁:地点；不写就是想到就做")
    does: str = Field("", description="做成了以后实际成立的事，用 & 连接：at:他自己:地点、owner:物件:持有者、flag:标记、"
                                      "gone:谁（死了或不见了）；只写原作里这一步真会造成的，没有就留空")


class Agenda(BaseModel):
    """What an important NPC means to do, and does if nobody steps in."""

    npc: str = Field(description="NPC id")
    goals: list[str] = Field(default_factory=list, description="他想要的，一两条")
    steps: list[Move] = Field(default_factory=list, max_length=6, description="他会怎么做，按先后排")


class Foreshadow(BaseModel):
    id: str
    hint: str = Field(description="可以早早露出来的一点端倪，玩家看得见、听得见的一句：一个声音、一样摆错位的东西")
    where: str = Field(description="在哪个地点露出来（地点 id）")
    pays_off: str = Field(description="最后指向什么：结论、事件或结局 id")


class Tie(BaseModel):
    """A thread an investigator's background can be tied to (the director's 开局改编 picks from these)."""

    id: str
    hook: str = Field(description="可以接上调查员背景的一条线头，一句话，比如“镇上的牧师是他战时的随军神父”")
    npc: str = Field("", description="牵到哪个 NPC")
    place: str = Field("", description="或者牵到哪个地点")
    fits: list[str] = Field(default_factory=list, description="适合哪类背景：重要之人、意义非凡之地、思想与信念、宝贵之物、职业")


class Mechanic(BaseModel):
    """A rule the module needs that the engine does not keep by itself: a plugin's job."""

    name: str = Field(description="规则叫什么，几个字")
    rules: str = Field(description="规则本身：什么时候、怎么算、有什么后果")
    why: str = Field("", description="模组里哪里用到")
    plugin: str = Field("", description="写好的插件 id；还没写就留空")


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
    # Redacted public summaries of the active rule plugins, set only for a LAN table's copy
    # (net/hub.py). Excluded from the module's own files; a host's loaded module leaves it empty.
    plugin_info: list[dict] = Field(default_factory=list, exclude=True)
    directed: list[str] = Field(default_factory=list, exclude=True)  # plugins the director wrote this game (module/improv.py)
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
    showcases: list[Showcase] = Field(default_factory=list, description="大场景：在重要节点整屏展示一张大图，每个模组 5–15 个")
    prelude: list[PreludeFrame] = Field(default_factory=list, max_length=8,
                                        description="开局演出：进入游戏前播放的几幕，图和字介绍故事背景；编译时写好，不剧透")
    pregens: list[Investigator] = Field(default_factory=list)
    relations: list[Bond] = Field(default_factory=list, description="冲突图：人物之间的看法，开局就在本局的世界记录里")
    agendas: list[Agenda] = Field(default_factory=list, description="重要 NPC 的打算：没人插手时他们会做什么")
    foreshadow: list[Foreshadow] = Field(default_factory=list, description="伏笔：早早可以露出来的端倪，和它最后指向什么")
    ties: list[Tie] = Field(default_factory=list, description="私人牵连：调查员的背景可以接上的线头")
    mechanics: list[Mechanic] = Field(default_factory=list, description="插件清单：引擎自己不管、要插件来管的规则")
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
        """A module folder (module/package.py), or one YAML file holding the whole module."""
        from . import package

        path = Path(path)
        folder = path if path.is_dir() else path.parent
        if path.is_dir() or path.name == package.MAIN:
            data = package.read(folder)
        else:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        module = cls.model_validate(data)
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
        module.art, module.art_colors = package.ascii_art(folder / "art")
        module.pictures = package.pictures(folder / "art")
        return module

    def save(self, folder: Path) -> Path:
        """The YAML split into its files, the plugins and the ASCII art (art/ascii/); photos are
        files already and stay where they are."""
        from . import package

        folder = Path(folder)
        file = package.write(folder, self.model_dump(mode="json", exclude_defaults=False))
        if self.extensions:
            from ..plugins.storage import save as save_plugin

            for plugin in self.extensions.values():
                save_plugin(plugin, folder / "plugins")
        if self.art:
            (folder / "art" / "ascii").mkdir(parents=True, exist_ok=True)
            for name, text in self.art.items():
                (folder / "art" / "ascii" / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
            for name, mask in self.art_colors.items():
                (folder / "art" / "ascii" / f"{name}.color").write_text(mask + "\n", encoding="utf-8")
        return file


def _find(items, id: str, kind: str):
    for item in items:
        if item.id == id:
            return item
    raise KeyError(f"模组里没有 id 为“{id}”的{kind}")
