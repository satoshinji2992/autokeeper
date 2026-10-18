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


class NewNPC(BaseModel):
    id: str
    name: str
    role: str = Field(description="身份，一句话")
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
    events: list[NewEvent] = Field(default_factory=list)
    endings: list[NewEnding] = Field(default_factory=list)
    arcs: list[NewArc] = Field(default_factory=list, description="NPC 的角色弧光：此刻的心境和变化")
    go: str = Field("", description="这一轮调查员去到哪个地点（新建的或已有的 id），不移动就留空")
    give: list[str] = Field(default_factory=list, description="这一轮马上交给调查员的线索 id")
    note: str = Field("", description="一句话告诉守秘人这一轮怎么讲")


DIRECTOR_SYSTEM = """你是这场 CoC 7 版跑团的导演，在模组没写到的地方即兴，让故事走下去。你的方案会写进本局的模组，守秘人、裁定员和 NPC 照着演。

世界观不能破：
- 时代、地点、神话设定和真相都以模组为准。不引入模组之外的神话存在，不出现那个年代没有的东西，不改写模组真相（谁做了什么、发生过什么、怪物是什么）。
- 新人物、新地点要像本来就在这个故事里：符合时代和地方，和已有的人和事有关联。
- 不是玩家想要什么就给什么。新线索要靠调查得来，不能直接说出真相，也不能顶替模组的关键线索。
- 调查员不会凭空拥有那个年代没有的东西，也不会突然做到做不到的事。这类行动演成落空，不要让它真的发生。
  比如 1920 年代掏手机：他把手伸进口袋，只摸到一包烟和几枚硬币。不要让他摸出任何能当手机用、或者说不清是什么的东西。

张力：
- 每次即兴都带一点阻力：小冲突（NPC 的戒心、利益冲突、时间压力、有人阻挠），让调查员的每一步都有代价或要做选择。
- 开局改编时，用定时或标记触发的事件预先埋下一处小冲突和一处大冲突（对峙、威胁、两难的抉择），分别放在故事的中段和后段。
- 给出答案的同时留下新的问题。调查员查对了地方、方法合理，就让他拿到东西；但不要为了推进剧情硬塞线索。
- 有必要时给 NPC 一条角色弧光：在 arcs 里写这个 NPC 此刻的心境和正在发生的变化（从戒备到信任、从隐瞒到崩溃），他之后的言行会随之变化。
- 可以调动情绪，靠具体的细节和动作，写得克制。

按触发原因安排：
- 开局改编：读调查员的背景（重要之人、意义非凡之地、信念、宝贵之物），挑一两样织进故事，让调查员和这个案子有私人牵连，比如背景里的人成了 NPC、寄来一封信、出现在某个地点。模组里体验不好的地方（关键线索只有一条路、某段时间无事可做）可以补一条线索或一个事件。不改开场，不改原有结局。
- 展开：调查员动机正常、合情合理，只是模组没写到。不要拒绝，给它一个合理的结果：一段演出、一个新地点或一个新 NPC，不一定要给线索。需要指回主线时，用人物的反应、一个名字或一句传闻轻轻带一下，指向还没去过的地方或能推动的剧情标记，不要把关键信息直接交到调查员手上；只有调查员真的在那里动手查了，才给一条新线索。真的偏离主线太远，可以加一个新结局，条件是一个新的剧情标记。
- 后果：调查员的做法违背世界观或常识、想绕过调查直接拿结果，或者乱来。不要说“不能”，安排一段当场发生的演出（when 填 now），让后果自然出现：受伤、理智损失、被人盯上或惹恼某人（新标记）、白费时间。惩罚要和行为相称，让人从剧情里明白这条路走不通。
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
