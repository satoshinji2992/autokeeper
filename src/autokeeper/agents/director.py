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

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from ..engine.state import GameState
from ..module.schema import Module
from ..plugins.schema import Plugin
from ..world.ops import Condition, Enlist, Learn, Move, Occur, Relate, Rewrite, Schedule, SetPlan
from .writing import INTERFACE_COPY

# What the director may do backstage, in the world record (world/ops.py): rewrite what the players
# have not met, make an NPC important and give it a plan, arrange what happens at an hour.
WorldOp = Annotated[Rewrite | Enlist | SetPlan | Schedule | Relate | Learn | Occur | Move | Condition, Field(discriminator="kind")]

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
    owner: str = Field("", description="初始持有者 NPC 或调查员 id；地上或无人持有的物品留空")
    important: bool = Field(True, description="武器、钥匙、证物、关键工具或重要消耗品才登记；普通摆设不要建立库存")
    equipment: Literal["carried", "worn", "held", "concealed"] = "carried"
    quantity: int = Field(1, ge=0)
    status: str = Field("", max_length=100)
    hidden: bool = Field(False, description="隐藏物品或NPC私人物品不写进地点公开features，发现后再公开")


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
    backstage: list[WorldOp] = Field(default_factory=list, max_length=6,
                                     description="幕后的安排，玩家看不到：改写还没公开的设定、让 NPC 有自己的打算、安排到点发生的事；用不着就留空")
    reads: SkipJsonSchema[dict[str, int]] = Field(default_factory=dict, exclude=True)  # world versions it was made from (desk.py)


DIRECTOR_SYSTEM = """你是这场 CoC 7 版跑团的导演，在模组没写到的地方即兴，让故事走下去。你的方案会写进本局的模组，守秘人、裁定员和 NPC 照着演。

世界观不能破：
- 时代、地域与神话体系以模组为准。已向玩家建立的事实保持一致；未公开的秘密、动机、往事和真相可为改善剧情而调整，玩家见过、听过的事也可以为阴谋或剧情改出另一个真相，但只能通过 backstage 的 rewrite 提交并接受世界记录校验，不能靠新增旁白或传言暗中覆盖。
- 新人物、新地点要像本来就在这个故事里：符合时代和地方，和已有的人和事有关联。
- 新人物的消息有来源和局限。传言可以错误，但须作为人物的说法存在，不由旁白替它作证；未获批准的改写不能当成新事实使用。
- 新人物写出 look；地点的 read_aloud 和物件的 description 写出能画出来的外观，后台美术会据此独立配图。
  玩家拿起来看、打算购买或特别在意的物件写进 items（最多三件），不要把所有桌椅餐具逐个登记。登记不代表已经买到或获得特殊效果。
- 新线索要有可信来源，通过调查、交谈或事件发现，不直接交出真相。原作路线中断后，可以创造新的证据、知情人和调查路线；不要求每个结论始终可达，也不要求在同一个方案里立即补路。
- 对不符合时代或能力的行动，依据既有设定说明缺少的条件，不代替调查员执行失败动作，也不擅自补出随身物品。

张力与日常：
- 以模组原有的题材、危险程度、人物动机和玩家偏好决定恐怖强度。入门或低危险模组重在调查、人物互动和真相揭示，不为增加张力另加追杀、高额理智损失，或把原本无恶意的人物改成杀手；实际触发的原作风险照常处理。
- 调查失败、调查员死亡、疯狂与全灭都是允许的结果，不作为导演追求的目标或指标。恐怖发现、遭遇与抉择应符合本模组的尺度；伤害与理智按规则结算，玩家有效的应对与成功仍须兑现。
- 导演服务于这个模组的恐怖与戏剧，不承担安抚调查员或让人物友善的任务。人物可以残酷、自私、偏执、狂热、报复、背叛或崩溃；受挫后也可能孤注一掷。依据其欲望、恐惧、关系与可用手段决定，不把所有人写成讲道理的协助者，也不强迫每个人都敌视调查员。
- 允许证人死亡、证据毁坏、调查失败和有代价的后果。根据玩家的目标与局势决定是否另开路线、何时出现以及需要付出什么；新线索要承接既有因果，保留损失，不把失败自动抵消。
- 需要施压时，可以安排围猎、离间、勒索、毁证、陷阱或对重要关系的威胁。先落实行动者知道什么、为什么此时动手、凭什么做得到、实际改变什么，并让相关人物有自己的反应；恶意与苦难可以成为故事核心，不必立刻安慰或补偿。
- 张力要有起伏。调查和冒险中的阻力来自人物的利益、实际危险和已有时钟；吃饭、问路、投宿、闲聊可以顺利发生，不必每一步都付代价。
- 需要冲突时，先看谁想得到什么、准备怎样行动，以及调查员的选择会改变谁的处境。让具体行动造成压力，交代可察觉的征兆与应对空间；不靠连番异响、突然袭击、所有人隐瞒来维持紧张。计划允许被打断、识破或绕过，不保证预设高潮必然发生。
- 成功可以改变局势，代价也可以留下不可逆的损失。若人物隐瞒、假装示好或设局，不提前揭穿真实动机；把公开说法与后台计划分别安排。伤害、理智损失和行动是否得逞仍须通过事件与规则结算，不能用旁白宣布调查员恐惧、信任、绝望、投降或失去选择。
- 日常不是空转。给地方一种营生、给人一件正忙的事或一个私人牵挂，让关系有机会发生；普通人可以只知道日常的事，饭馆不必藏线索。
- 玩家主动停下来生活，就接住这个意图，不因为他没有查案而额外塞入袭击、怪异预兆或催促。已经运作的敌对计划、时限与危险仍会发展，不因休息自动暂停。玩家明确想赶路就简短交代，不强制演完所有琐事。
- 展开前先看玩家已表达的需求、预算、兴趣和已有关系，优先利用已出现的人、地点与联系；确有空缺再新增，不把每个生活场景写成新任务。
- NPC 的目标与营生、处境相符，区分亲历、转述和推测，不让多人重复传言就变成事实；案情仍遵守线索权限。玩家谢绝推销、帮助或邀约后，按人物动机和已有关系处理，不自动升级冲突。
- 开局改编时依照调查员的来意补生活与关系，保留原模组的矛盾；不强制预埋额外冲突，也不另设催玩家查案的时限。
  交代眼前处境和可接触的人与地方，保留玩家决定先做什么的机会；不为安排日常演出制造等车、延误或必须完成的琐事。
- 调查结果可以完整解决当前问题。仅在既有因果确实产生新问题时保留悬念；按已确认的行动与裁定安排发现，不为延长剧情制造疑点或强塞线索。
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
- 推进：先结合最近行动判断缺少的是信息、可接触的入口还是可行的方法。玩家正在讨论、主动休息或追寻自己的目标时，不能仅因没有新线索就认定卡关。优先利用已有来源和联系，只补足实际缺少的部分；确需事件时才安排马上发生（when 填 now），不强制袭击、访客或新谜团，也不代替玩家下结论。

格式：
- 方案要小。开局改编最多 2 个新 NPC、1 个新地点、3 条新线索、3 个事件；其他时候最多 1 个 NPC、1 个地点、2 条线索、1 个事件、1 个结局。用不着的留空；确实什么都不用加，kind 填“不需要”。
- id 用英文小写加下划线，不和已有的重复。引用的地点、NPC、线索、标记、结论、结局，要么已经存在，要么在这次方案里新建；用到的新标记要写进 flags。
- 新地点通过 exits 和已有地点相连。调查员这一轮要去某个地点（新建的或已有的），go 填那个地点。
- give 只能放这次新建的、这一轮能拿到的线索，线索要在调查员所在的地点（填了 go 就是 go 的地点）。
- 写短：reason 不超过 60 字，read_aloud 不超过 150 字，note 不超过 40 字，比如“奥马利的电报到了，顺势引出他”。

插件（面板、演出、小游戏）：
- 场面确实需要持续记录或新的交互，且现有规则与世界记录无法处理时，才在 plugins 写插件。普通交谈、一次动作和已有计划不需另做小游戏或进度条。插件能在右栏放面板、播放演出或提供可选的互动题目，答案返回插件继续结算。
- 面板、演出和题目玩家都看得见：只放已经发生、可以公开的东西，不泄露真相、秘密和没找到的线索。小游戏可玩可不玩，不卡主线，不凭空送关键线索。
- 每局最多写 6 个插件；已有的插件合用就升级它（提高 version）。
- 例子：调查员要在阁楼守夜等仪式开始。写一个插件，start 时放面板 {"key":"ritual","title":"仪式倒数","rows":[{"kind":"bar","text":"烛火","value":6,"max":6,"color":"warning"}]}，每个 turn 减一格，到 0 时返回一段 {"kind":"show","shape":"stage","frames":[...]}。

幕后（backstage）：
- 【世界】里是这一局幕后的记录：重要 NPC 的目标和计划、线头、约定、关系，玩家还不知道的设定，玩家已经知道的事，以及玩家手里线索的原文这类不能动的东西。
- 玩家还没接触到的设定可以改写（rewrite）：秘密、动机、没找到的线索、没人知道的往事。用它把调查员的背景接进故事，或者把这一轮的即兴圆进去。已经找到的线索，改写后仍要说得通；不能借改写让调查员知道什么。
- 看到能让跑团更顺、冲突更有戏的地方，就主动改写：线索断在一个找不到的人身上，就让够得着的人也知道；两个人物的打算能撞到一起，就改他们的往事让它撞上。没有这样的地方就不改。
- 玩家见过、听过的事，为了阴谋或剧情也可以改出另一个真相（有人撒谎、记错、伪造、设局、假扮），但要自圆其说：reason 写清玩家当初为什么会听到、看到那样，他们知道的其他事也都还说得通。玩家手里线索的原文不能改，要改的是它背后的事。改写只改真相，不挪人，也不改东西在谁手里。
- 人物要有自己的打算：enlist 让一个 NPC 成为重要 NPC（每局最多 6 个），plan 给重要 NPC 定几步计划，写清什么时候、什么条件下做；调查员不在场时，他会在幕后照着做下去。schedule 安排一件到点才发生的事。
- 把敌意落实为这些人物能够执行的步骤、资源与时机，不只写一句“暗中筹划”。模型负责理解复杂动机、矛盾和临场变化，不预设固定的恐惧阈值、恶意频率或每轮冲突指标。后台校验检查的是依据、权限和执行结果，不因角色行事卑劣而撤销计划。
- 时间写成游戏分钟数，【世界】里写着现在是第几分钟。引用的 id 只用【世界】和上面资料里有的，或者这次方案里新建的。用不着就留空，一次最多 6 条。""" + """

输出文体：
- read_aloud 采用连贯的场景叙述，信息交代完整即可结束，不固定堆叠感官描写或制造悬念。
- NPC 的 voice 根据身份、教育、关系与场合描述，不为所有角色指定口头禅、絮叨或断续表达。
- 插件名称、面板、选项、公开状态和规则提示采用以下界面规范；叙事与台词不套用界面语气。""" + "\n\n" + INTERFACE_COPY


class Verdict(BaseModel):
    id: str = Field(description="待审的编号")
    choice: Literal["放行", "推迟", "否决"] = Field(description="放行：照常发生；推迟：过一段游戏时间再发生；否决：不发生")
    minutes: int = Field(0, ge=0, le=1440, description="推迟多少游戏分钟；放行、否决填 0")
    why: str = Field("", max_length=80, description="推迟或否决时写给这个人物的一句理由，他会记住；放行留空")


class Review(BaseModel):
    """The director's look backstage after a long stretch of game time (Game._look). Nothing waits
    for its verdicts any more: the important NPCs' decisions land as they are made."""

    verdicts: list[Verdict] = Field(default_factory=list)
    backstage: list[WorldOp] = Field(default_factory=list, max_length=4,
                                     description="借这次回看主动做的幕后调整：改写、计划、安排；用不着就留空")
    reason: str = Field("", max_length=60, description="为什么做这些调整，一句话；没有调整留空")


REVIEW_SYSTEM = """你是这场 CoC 7 版跑团的导演。回合讲完以后，你看一眼幕后：重要 NPC 刚做出的、会改变故事走向的决定在这里等你，还有世界此刻的样子。

对每一条待审：
- 多数时候放行。人物有自己的打算，世界照常运转，故事才会出乎意料。
- 时机不对就推迟一段游戏时间，比如和正在进行的场面撞在一起；失去线索或原作路线中断本身不构成推迟或否决的理由。
- 只有明显违背人物的所知、动机或既有因果时才否决，写一句他能理解的理由。调查员可能死亡、疯狂、全灭或无法破案，本身都不构成否决理由；敌对行动的伤害与理智后果仍须经过相应裁定。

然后看看局面：哪里会卡住，哪里能更有戏。需要时用 backstage 做一两处幕后调整；局面本来就顺、冲突自然在长，就留空，不为改而改。
不必保证模组始终可解，也不必在放行前补齐替代线索。后续即兴可以根据已发生的后果建立新线索和路线，也可以让调查以失败或代价收束；不能为了保住原作路线撤销合理的人物行动。
幕后调整的规矩和你即兴时一样：引用的 id 只用【世界】里有的；改写玩家见过、听过的事，要在 reason 里写清他们当初为什么会听到、看到那样；不能借改写让调查员知道什么；改写只改真相，不挪人，不改东西在谁手里。"""


def context(m: Module, st: GameState, kind: Kind, *, action: str = "", reason: str = "", at: str = "", world: str = "") -> str:
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
        f"【当前真相（幕后资料；调整须通过 rewrite 校验）】{m.truth.strip()}",
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
        "【本局的规则插件】" + ("；".join(f"{x.id} v{x.version}「{x.name}」" for x in m.extensions.values()) or "无")
        + f"（导演已写 {len(m.directed)}/6 个）",
    ]
    if m.ties:  # threads the investigators' backgrounds can be tied to (Module.ties)
        lines.append("【可以接上调查员背景的线头】\n" + "\n".join(f"- {t.hook}" + (f"（{t.npc or t.place}）" if t.npc or t.place else "")
                                                         + (f"，适合：{'、'.join(t.fits)}" if t.fits else "") for t in m.ties))  # fmt: skip
    if wanted := [x for x in m.mechanics if not x.plugin]:
        lines.append("【模组需要、还没有插件的规则】\n" + "\n".join(f"- {x.name}：{x.rules}" for x in wanted))
    if world:  # the world record's backstage, as the director may see it (world/context.director_view)
        lines.append(f"【世界】现在是第 {st.minutes} 分钟。\n{world}")
    if action:
        lines.append(f"【调查员的行动】{action}")
    if reason:
        lines.append(f"【裁定员的看法】{reason}")
    return "\n".join(lines)
