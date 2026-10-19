"""The director: improvises where the module is silent, without breaking its world.

Called on four occasions: at the start of a game (weave the investigators' backstories
into the module and patch its weak spots), when an investigator with a sensible motive
does something the module never wrote (open a new line that leads back to the story),
when an investigator does something out of place (stage the consequence instead of
saying no), and when the party has been stuck for a while (send them a lead). Its plan
is a small patch in the module's own format; module/improv.py validates it and writes
it into this game only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..engine.state import GameState
from ..module.schema import Module
from ..plugins.schema import Plugin

Kind = Literal["开局改编", "展开", "后果", "推进"]


class NewFlag(BaseModel):
    id: str = Field(description="英文小写加下划线")
    meaning: str = Field(description="标记的含义，比如“调查员被巡警盯上了”")


class NewLocation(BaseModel):
    id: str
    name: str
    read_aloud: str = Field(description="调查员到达时看到的（玩家可见）")
    keeper: str = Field("", description="守秘人须知")
    features: list[str] = Field(default_factory=list, description="可以调查的东西")
    exits: list[str] = Field(description="和哪些已有地点相连（会自动双向连通）")
    within: str | None = Field(None, description="在哪个地点里面（已有的，或这次新建的），比如地窖在某户人家里；不在别处里面就留空")


class NewNPC(BaseModel):
    id: str
    name: str
    role: str = Field(description="身份，一句话")
    look: str = Field("", description="可见的年龄、衣着、外貌，作为独立立绘的依据")
    location: str = Field(description="在哪个地点（已有的，或这次新建的）")
    personality: str = ""
    voice: str = Field("", description="说话方式")
    goal: str = ""
    knows: list[str] = Field(default_factory=list, description="肯说的事")
    secrets: list[str] = Field(default_factory=list, description="不被逼问不会说的事")
    lies: list[str] = Field(default_factory=list, description="会主动撒的谎")


class NewClue(BaseModel):
    id: str
    title: str = Field(description="线索本里的短标题")
    text: str = Field(description="调查员得到的信息（玩家可见）")
    where: str = Field(description="所在地点 id")
    feature: str = Field("", description="具体在哪：某件东西，或某个 NPC 口中")
    npc: str = Field("", description="由哪个 NPC 说出来，不是 NPC 给的就留空")
    skill: str = Field("", description="需要什么检定，留空表示找到就有")
    difficulty: Literal["常规", "困难", "极难"] = "常规"
    points_to: list[str] = Field(default_factory=list, description="支持模组里的哪些结论 id")


class NewItem(BaseModel):
    id: str
    name: str
    description: str = Field(description="物件看得见的形状、材质、颜色；不夹带未发现的线索或物品效果")
    where: str = Field(description="出现的地点 id")
    owner: str = Field("", description="初始持有者 NPC id，例如店主的商品；地上或无人持有的物品留空")


class NewEvent(BaseModel):
    id: str
    title: str
    when: str = Field(description="now 表示这一轮马上发生；也可以是 'D1 22:00'、'flag:标记'、'visit:地点id'")
    read_aloud: str = Field(description="调查员看到、听到的那一段演出")
    text: str = Field("", description="守秘人须知")
    sanity: str = Field("", description="理智检定，如 0/1D3，不需要就留空")
    damage: str = Field("", description="当场受到的伤害，如 1D3，由行动的调查员承受，不需要就留空")
    sets: list[str] = Field(default_factory=list, description="事件发生时设置的标记")
    move_to: str = Field("", description="把调查员带到哪个地点，不需要就留空")
    performer: str = Field("", description="由哪个 NPC 当场表演，不需要就留空")
    performance: str = Field("", description="表演要求")
    ending: str = Field("", description="事件发生时直接进入的结局 id，不需要就留空")


class NewEnding(BaseModel):
    id: str
    title: str
    condition: str = Field(description="flag:某个标记")
    text: str = Field(description="结局的叙述")


class NewLook(BaseModel):
    target: str = Field(description="样子明显变了的已有地点 id 或 NPC id")
    change: str = Field(description="画面上看得见的变化，比如“书店烧成焦黑的空壳，橱窗全碎了”“他左臂缠着渗血的绷带，脸色灰白”；只写外观，不写剧透")


class NewShowcase(BaseModel):
    id: str
    kind: Literal["突发", "危机", "重要场景", "怪物", "最终boss", "结局"]
    when: str = Field(description="之后才会到的时刻：event:事件id、visit:地点id（还没去过的）、flag:标记、ending:结局id")
    title: str = Field(description="画面下方不剧透的短标题，不超过 12 个字")
    prompt: str = Field(description="画面描述：画什么、构图、光线和气氛，只写看得见的")


class NewArc(BaseModel):
    npc: str = Field(description="NPC id（已有的或这次新建的）")
    arc: str = Field(description="这个 NPC 此刻的心境和正在发生的变化，比如“从戒备开始动摇，想说又怕被当成疯子”")


class Improv(BaseModel):
    kind: Literal["开局改编", "展开", "后果", "推进", "不需要"]
    reason: str = Field(description="为什么这样安排，玩家看不到")
    flags: list[NewFlag] = Field(default_factory=list)
    locations: list[NewLocation] = Field(default_factory=list)
    npcs: list[NewNPC] = Field(default_factory=list)
    clues: list[NewClue] = Field(default_factory=list)
    items: list[NewItem] = Field(default_factory=list, description="镜头关注的新物件：随身小物、店里的商品或场景道具，最多三件")
    events: list[NewEvent] = Field(default_factory=list)
    endings: list[NewEnding] = Field(default_factory=list)
    arcs: list[NewArc] = Field(default_factory=list, description="NPC 的角色弧光：此刻的心境和变化")
    looks: list[NewLook] = Field(default_factory=list, description="这件事让已有地点或 NPC 的样子明显变了才写，最多一条；配图会照现在的图改画")
    showcases: list[NewShowcase] = Field(default_factory=list, max_length=1, description="大场景：这次的安排造出重大的新节点时才写，平常留空")
    plugins: list[Plugin] = Field(default_factory=list, max_length=1, description="确有缺失的持续玩法时编写一个经过测试的纯 Python 插件；普通闲聊留空")
    go: str = Field("", description="这一轮调查员去到哪个地点（新建的或已有的 id），不移动就留空")
    give: list[str] = Field(default_factory=list, description="这一轮马上交给调查员的线索 id")
    note: str = Field("", description="本轮可公开描写的动作或氛围，不包含未解锁线索、真相、秘密或未来安排")


DIRECTOR_SYSTEM = """你是这场 CoC 7 版跑团的导演，在模组没写到的地方即兴，让故事走下去。你的方案会写进本局的模组，守秘人、裁定员和 NPC 照着演。

世界观不能破：
- 时代、地点、神话设定和真相都以模组为准。不引入模组之外的神话存在，不出现那个年代没有的东西，不改写模组真相（谁做了什么、发生过什么、怪物是什么）。
- 新人物、新地点要像本来就在这个故事里：符合时代和地方，和已有的人和事有关联。
- 新人物知道的事、嘴里的传言，不能推翻模组写明的事实（谁住在哪、谁失踪了、谁在做什么）。传言可以不准，只能错在细节上。
- 新人物写出 look；地点的 read_aloud 和物件的 description 写出能画出来的外观，后台美术会据此独立配图。
  玩家拿起来看、打算购买或特别在意的物件写进 items（最多三件），不要把所有桌椅餐具逐个登记。登记不代表已经买到或获得特殊效果。
- 不是玩家想要什么就给什么。新线索要靠调查得来，不能直接说出真相，也不能顶替模组的关键线索。
- 调查员不会凭空拥有那个年代没有的东西，也不会突然做到做不到的事。这类行动演成落空，不要让它真的发生。
  比如 1920 年代掏手机：他把手伸进口袋，只摸到一包烟和几枚硬币。不要让他摸出任何能当手机用、或者说不清是什么的东西。

张力与日常：
- 张力要有起伏。调查和冒险中的阻力来自人物的利益、实际危险和已有时钟；吃饭、问路、投宿、闲聊可以顺利发生，不必每一步都付代价。
- 日常不是空转。给地方一种营生、给人一件正忙的事或一个私人牵挂，让关系有机会发生；普通人可以只知道日常的事，饭馆不必藏线索。
- 玩家主动停下来生活，就接住这个意图，不往事件里塞袭击、怪异预兆或催促查案。玩家明确想赶路就简短交代，不强制演完所有琐事。
- 开局改编时依照调查员的来意补生活与关系，保留原模组的矛盾；不强制预埋额外冲突，也不另设催玩家查案的时限。
- 给出答案的同时留下新的问题。调查员查对了地方、方法合理，就让他拿到东西；但不要为了推进剧情硬塞线索。
- 有必要时给 NPC 一条角色弧光：在 arcs 里写这个 NPC 此刻的心境和正在发生的变化（从戒备到信任、从隐瞒到崩溃），他之后的言行会随之变化。
- 这一轮的事让某个已有地点或 NPC 的样子明显变了（失火烧毁、被水淹、被洗劫一空、受了重伤、变异、死去），在 looks 里写一条：target 填它的 id，change 写画面上看得见的变化。心情、天气和昼夜不算。
- 这次的安排造出一个重大的新节点（要现身的怪物、逼近的危机、新的结局），而且它要过一阵才到，可以在 showcases 里加一个大场景：when 写它到来的时刻，画面整屏展示。每局最多三个，平常留空。
- 可以调动情绪，靠具体的细节和动作，写得克制。

按触发原因安排：
- 开局改编：读调查员的背景（重要之人、意义非凡之地、信念、宝贵之物），挑一两样织进故事，让调查员和这个案子有私人牵连，比如背景里的人成了 NPC、寄来一封信、出现在某个地点。模组里体验不好的地方（关键线索只有一条路、某段时间无事可做）可以补一条线索或一个事件。不改开场，不改原有结局。
- 展开：调查员动机正常、合情合理，只是模组没写到。不要拒绝，给它一个合理的结果：一段演出、一个新地点或一个新 NPC。
  日常行动只补生活需要的人、地点和应答，clues、give、endings 留空；普通吃饭、闲聊不算偏离主线，不要强行带回查案。
  只有调查员真的在那里调查，才考虑新线索；需要引路时用人物反应轻轻带一下，不直接交出关键信息。玩家主动追寻别的故事时才考虑新结局。
- 后果：只处理确实发生的风险。违背时代或能力的愿望平实地解释行不通，不虚构玩家的动作、伤害或理智损失。
  合理的捷径、求助、拒绝委托和离开都应允许；危险行为按已经说明的风险和人物利益产生相称后果，不因偏离主线而惩罚。
- 推进：调查员卡住很久了。安排一件马上发生的事（when 填 now），比如有人来访、送来一封信、传来一个消息，把他们引向还没去过的地方或还没找到的关键线索。

格式：
- 方案要小。开局改编最多 2 个新 NPC、1 个新地点、3 条新线索、3 个事件；其他时候最多 1 个 NPC、1 个地点、2 条线索、1 个事件、1 个结局。用不着的留空；确实什么都不用加，kind 填“不需要”。
- id 用英文小写加下划线，不和已有的重复。引用的地点、NPC、线索、标记、结论、结局，要么已经存在，要么在这次方案里新建；用到的新标记要写进 flags。
- 新地点通过 exits 和已有地点相连。调查员这一轮要去某个地点（新建的或已有的），go 填那个地点。
- give 只能放这次新建的、这一轮能拿到的线索，线索要在调查员所在的地点（填了 go 就是 go 的地点）。
- 写短：reason 不超过 60 字，read_aloud 不超过 150 字，note 不超过 40 字，比如“奥马利的电报到了，顺势引出他”。"""


def context(m: Module, st: GameState, kind: Kind, *, action: str = "", reason: str = "", at: str = "") -> str:
    """Everything the director must not contradict, and what it was called for."""
    found = set(st.clues)
    here = m.location(at or st.location)
    people = []
    for inv in st.investigators:
        story = "；".join(f"{k}：{v}" for k, v in inv.backstory.items() if k != "说明" and v)
        people.append(f"- {inv.name}，{inv.age} 岁，{inv.occupation}。{story or '没有写背景'}")
    recent = "\n".join(f"- {e.who or e.kind}：{e.text[:160]}" for e in st.log[-12:] if e.kind != "system")
    arcs = "；".join(f"{n.name}：{n.arc}" for n in m.npcs if n.arc) or "无"
    before = "\n".join(f"- 第 {p.get('turn', 0)} 轮 {p.get('kind', '')}：{p.get('plan', {}).get('reason', '')}" for p in st.improv) or "无"
    lines = [
        f"【这次的任务】{kind}",
        f"【模组】《{m.title}》 {m.era} {m.setting}".rstrip(),
        f"【开场钩子（玩家知道）】{m.hook.strip()}",
        f"【真相（只有守秘人知道，不能改）】{m.truth.strip()}",
        "【结论】" + "；".join(f"{k.id}：{k.text}" for k in m.conclusions),
        "【结局】" + "；".join(f"{e.id}「{e.title}」条件 {e.condition}" for e in m.endings),
        "【剧情标记】" + "；".join(f"{k}：{v}{'（已达成）' if k in st.flags else ''}" for k, v in m.flags.items()),
        "【地点】" + "；".join(
            f"{x.id} {x.name}，通往 {'、'.join(x.exits) or '无'}{'，去过' if x.id in st.visited else ''}" for x in m.locations
        ),
        "【NPC】" + "；".join(f"{n.id} {n.name}，{n.role}，在 {n.location}" for n in m.npcs),
        f"【NPC 的心境】{arcs}",
        "【线索】" + "；".join(
            f"{c.id}「{c.title}」在 {c.where}{'，关键' if c.core else ''}{'，已找到' if c.id in found else ''}" for c in m.clues
        ),
        "【调查员】\n" + "\n".join(people),
        f"【现在】{st.clock}，在 {here.name}（{here.id}）",
        f"【到目前为止】{st.memory or '刚开始'}",
        f"【最近发生的事】\n{recent or '无'}",
        f"【之前的即兴】\n{before}",
    ]
    if action:
        lines.append(f"【调查员的行动】{action}")
    if reason:
        lines.append(f"【裁定员的看法】{reason}")
    return "\n".join(lines)
