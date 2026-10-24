"""The director: improvises where the module is silent, without breaking its world.

Called on four occasions: at the start of a game (weave the investigators' backstories
into the module and patch its weak spots), when an investigator with a sensible motive
does something the module never wrote (open a new line that leads back to the story),
when an investigator does something out of place (stage the consequence instead of
saying no), and when the party failed an Idea roll (千钧一发: cut them into the worst situation
they can recognise, the clue they missed showing in it; engine/idea.py). Its plan
is a small patch in the module's own format; module/improv.py validates it and writes
it into this game only.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from .. import cards
from ..engine.presentation import MediaAction
from ..engine.state import GameState
from ..module.schema import Check, Leg, Module, RoundGate, Stake
from ..plugins.schema import Plugin
from ..rules.consequences import Die
from ..world.ops import Condition, Enlist, Learn, Move, Occur, Relate, Rewrite, Schedule, SetPlan
from .writing import INTERFACE_COPY

# What the director may do backstage, in the world record (world/ops.py): rewrite what the players
# have not met, make an NPC important and give it a plan, arrange what happens at an hour.
WorldOp = Annotated[Rewrite | Enlist | SetPlan | Schedule | Relate | Learn | Occur | Move | Condition, Field(discriminator="kind")]

# A stall offers the Idea roll (Game._nudge); the director is not asked for a lead.
Kind = Literal["开局改编", "展开", "后果", "千钧一发"]


class NewFlag(BaseModel):
    id: str = Field(description="英文小写加下划线")
    meaning: str = Field(description="标记的含义，比如“调查员被巡警盯上了”")


class NewLocation(BaseModel):
    id: str
    name: str
    read_aloud: str = Field(description="调查员到达时看到的（玩家可见）")
    keeper: str = Field("", description="守秘人须知：场景背景、隐藏情况、检定条件与后果；需要现场执行的规则写清，不只写氛围")
    features: list[str] = Field(default_factory=list, description="可以调查的东西")
    exits: list[str] = Field(description="和哪些已有地点相连（会自动双向连通）")
    within: str | None = Field(None, description="在哪个地点里面（已有的，或这次新建的），比如地窖在某户人家里；不在别处里面就留空")
    travel_minutes: dict[str, int] = Field(default_factory=dict, description="到出口地点的实际分钟数；未填按普通路程，室内相邻房间可写 0")
    chase: list[Leg] = Field(default_factory=list, description="确实可能追逐时的路线，沿用模组追逐段格式；不需要则留空")


class NewNPC(BaseModel):
    id: str
    name: str
    role: str = Field(description="身份，一句话")
    look: str = Field("", description="可见的年龄、衣着、外貌，作为独立立绘的依据")
    location: str = Field(description="此刻实际在哪个地点，不是未来约见地点；稍后才来的人先放在合理的别处，届时由事件 present 带到现场")
    personality: str = ""
    voice: str = Field("", description="说话方式")
    goal: str = ""
    knows: list[str] = Field(default_factory=list, description="本人知道的事与来源；包括与调查员的联系、如何得知并找到当前地点，不等于已经向玩家说过")
    secrets: list[str] = Field(default_factory=list, description="不公开的事；结合人物动机、关系和局势决定是否透露，不固定要逼问才说")
    lies: list[str] = Field(default_factory=list, description="会主动撒的谎")
    card: str = Field("", description="用【好事者卡】里的一张做底子时填卡 id：人物得到卡上的属性、技能与攻击；不用就留空")
    stats: dict[str, int] = Field(default_factory=dict, description="确实需要时的属性、技能与生命等数值，键用中文；人物卡作基线，这里覆盖指定项，空着按普通人")
    attacks: list[str] = Field(default_factory=list, description="会用的攻击及伤害，格式同模组人物卡；无特殊攻击留空")
    called: str = Field("", description="身份尚未认出时玩家看到的称呼，只写外观，不泄露真实身份")


class NewClue(BaseModel):
    id: str
    title: str = Field(description="线索的短标题，只给守秘人看")
    text: str = Field(description="调查员得到的信息，守秘人照它在叙述里讲出")
    where: str = Field(description="所在地点 id")
    feature: str = Field("", description="具体在哪：某件东西，或某个 NPC 口中")
    npc: str = Field("", description="由哪个 NPC 说出来，不是 NPC 给的就留空")
    skill: str = Field("", description="需要什么检定，留空表示找到就有")
    difficulty: Literal["常规", "困难", "极难"] = "常规"
    points_to: list[str] = Field(default_factory=list, description="支持模组里的哪些结论 id")
    sanity: str = Field("", description="实际获知时必然需要的理智检定，如 0/1D4；失败专属后果不填")
    mythos: int = Field(0, ge=0, description="实际获知时增加的克苏鲁神话百分点")


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
    when: str = Field("", description="now 表示这一轮马上发生；或 'D1 22:00'、'flag:标记'、'visit:地点id'；仅按遭遇轮次触发时留空")
    after_rounds: RoundGate | None = Field(None, description="指定事件开启的遭遇完成若干轮后发生，与 when 的条件同时成立")
    where: str = Field("", description="事件发生的地点；异地发生只记世界，不强行移动调查员")
    check: Check | None = Field(None, description="条件成立后还需通过的检定；每游戏日最多一次，失败次日可再试")
    advance_to: str = Field("", pattern=r"^(?:(?:D[1-9]\d* )?(?:[01]\d|2[0-3]):[0-5]\d)?$",
                            description="在场经历此事至少推进到HH:MM当日时刻或D2 20:00指定时刻；已过不倒退，异地事件不推进玩家时间")
    read_aloud: str = Field(description="调查员看到、听到的那一段演出")
    text: str = Field("", description="守秘人须知")
    sanity: str = Field("", description="理智检定，如 0/1D3，不需要就留空")
    damage: str = Field("", description="当场受到的伤害，如 1D3，由行动的调查员承受，不需要就留空")
    effects: list[Die] = Field(default_factory=list, description="无须检定、已经确定的死亡后果，目标与依据须明确；不提前兑现仍需交锋、检定或玩家选择的结果")
    sets: list[str] = Field(default_factory=list, description="事件发生时设置的标记")
    move_to: str = Field("", description="把调查员带到哪个地点，不需要就留空")
    performer: str = Field("", description="由哪个 NPC 当场表演，不需要就留空")
    performance: str = Field("", description="表演要求")
    ending: str = Field("", description="事件发生时直接进入的结局 id，不需要就留空")
    present: list[str] = Field(default_factory=list, description="本次实际出现在调查员眼前的 NPC id；触发时登记到现场，电话或传闻不填")
    fight: list[str] = Field(default_factory=list, description="事件当场动手的 NPC 或怪物 id；实际触发战斗，不只写在朗读文字里")
    surprise: Literal["", "party", "foe"] = Field("", description="战斗中毫无防备的一方；双方都有准备留空")
    chase: list[str] = Field(default_factory=list, description="事件直接开始追逐时的 NPC 或怪物 id；与 fight 二选一")
    chase_party: Literal["", "逃", "追"] = Field("", description="追逐时调查员的角色，留空按逃")
    stakes: list[Stake] = Field(default_factory=list, max_length=4, description="战斗或追逐的结果条件与剧情标记；由引擎按实际结果结算")
    sights: list[str] = Field(default_factory=list, description="本事件 sanity 已覆盖的已有怪物 id，避免首次照面重复结算")


class NewEnding(BaseModel):
    id: str
    title: str
    condition: str = Field(description="flag:某个标记")
    text: str = Field(description="结局的叙述")
    requires: list[str] = Field(default_factory=list, description="实际取得的线索或flag:标记；获准交涉不等于已经听到真相")
    sanity_gain: str = Field("", description="进入结局时恢复的理智骰式，如1D6")


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
    kind: Literal["开局改编", "展开", "后果", "千钧一发", "不需要"]
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
    media: list[MediaAction] = Field(default_factory=list, description="方案落实后向指定调查员展示已有图或材料；普通准备留空，不提前给未到时刻的观众")
    backstage: list[WorldOp] = Field(default_factory=list, max_length=6,
                                     description="幕后的安排，玩家看不到：改写还没公开的设定、让 NPC 有自己的打算、安排到点发生的事；用不着就留空")
    reads: SkipJsonSchema[dict[str, int]] = Field(default_factory=dict, exclude=True)  # world versions it was made from (desk.py)


# 千钧一发, the failed Idea roll's cut: Keeper Rulebook p. 169.
DIRECTOR_SYSTEM = """你为这场 CoC 7 版跑团的守秘人准备模组未写到的可执行场面。你提交的人物、地点、事件与世界操作并入本局记录，守秘人结合当前行动与局势继续主持；裁定员和 NPC 在各自职责与知情范围内使用这些资料。

世界观不能破：
- 时代、地域与神话体系以模组为准。已向玩家建立的事实保持一致；未公开的秘密、动机、往事和真相可为改善剧情而调整，玩家见过、听过的事也可以为阴谋或剧情改出另一个真相，但只能通过 backstage 的 rewrite 提交并接受世界记录校验，不能靠新增旁白或传言暗中覆盖。
- 新人物、新地点要像本来就在这个故事里：符合时代和地方，和已有的人和事有关联。
  主动来找调查员的人，写明做什么营生、为谁或什么事而来、如何认识对方或得知其去处；放进 role、goal、knows 或关系记录，供守秘人交代。登记姓名不等于玩家已经认识此人，出场须有可以理解的引见、自报家门或已有联系。
- 本局调查员保持自己的 id 与身份，不把队友或调查员背景中对自己的描述另建成同名 NPC，也不代替真人玩家作答、同意或行动。
- 新人物的消息有来源和局限。传言可以错误，但须作为人物的说法存在，不由旁白替它作证；尚未提交到记录的改写不能当成新事实使用。
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
  先读原作开场和当前地点的准备，不把已经写好的抵达、接待、必发事件重复安排一遍。私人联系可以先作为人物、关系或以后有条件才发生的事件备好；只有确实此刻到场且来由成立的事才写 now，不把每位调查员的背景同时变成登门求助的新任务。
  人物的 location 是此刻实际所在；准备稍后到访的人，不先放进调查员当前地点，等对应事件的 present 使其到场。
- 调查结果可以完整解决当前问题。仅在既有因果确实产生新问题时保留悬念；按已确认的行动与裁定安排发现，不为延长剧情制造疑点或强塞线索。
- 有必要时给 NPC 一条角色弧光：在 arcs 里写这个 NPC 此刻的心境和正在发生的变化（从戒备到信任、从隐瞒到崩溃），他之后的言行会随之变化。
- 这一轮的事让某个已有地点或 NPC 的样子明显变了（失火烧毁、被水淹、被洗劫一空、受了重伤、变异、死去），在 looks 里写一条：target 填它的 id，change 写画面上看得见的变化。心情、天气和昼夜不算。
- 这次的安排造出一个重大的新节点（要现身的怪物、逼近的危机、新的结局），而且它要过一阵才到，可以在 showcases 里加一个大场景：when 写它到来的时刻，画面整屏展示。每局最多三个，平常留空。
- 可以调动情绪，靠具体的细节和动作，写得克制。

按触发原因安排：
- 开局改编：读调查员的背景（重要之人、意义非凡之地、信念、宝贵之物），挑有因果依据的联系织进故事，比如原有委托与职业相符、背景里的人成了当地熟人，或某地保留着他们共同经历的痕迹。联系不必开场就全部揭出，已有来意足够时可以不新增。保留原作开局事实和原有结局；补充进入故事的承接，不把开场变成任务清单。
- 展开：调查员动机正常、合情合理，只是模组没写到。不要拒绝，给它一个合理的结果：一段演出、一个新地点或一个新 NPC。
  日常行动只补生活需要的人、地点和应答，clues、give、endings 留空；普通吃饭、闲聊不算偏离主线，不要强行带回查案。
  只有调查员真的在那里调查，才考虑新线索；需要引路时用人物反应轻轻带一下，不直接交出关键信息。玩家主动追寻别的故事时才考虑新结局。
- 后果：只处理确实发生的风险。违背时代或能力的愿望平实地解释行不通，不虚构玩家的动作、伤害或理智损失。
  合理的捷径、求助、拒绝委托和离开都应允许；危险行为按已经说明的风险和人物利益产生相称后果，不因偏离主线而惩罚。
- 千钧一发：调查员的灵感检定失败了。【调查员的行动】写着他们漏掉的那条线索，它这一轮无论如何都会到手；守秘人已按局势决定耗时并推进时间，按当前时间和实际记录安排，不再额外假定过去几个小时。
  把调查员（不论是否参与了检定）直接放进他们能意识到的最糟的局面，让这条线索在局面里显露出来：比如他们终于找到那个地方，却撞上了正在那里的危险。
  go 填这个场面所在的地点（已有的或这次新建的），events 安排马上发生的事（when 填 now），由已有的人物、地点和因果引出，危险程度符合本模组的尺度。
  场面停在调查员必须立刻应对的一刻：不替他们行动，不预先决定结果，伤害和理智损失只写进事件由规则结算。give 留空：那条线索由引擎交出。

格式：
- 围绕这次需要准备可交互的场面，人物有动机，调查有依据，局势能被玩家改变。通常一处地点和少数人物就够；确需多人、几条路线或连续事件才能成立时一并准备，不为凑数量扩写，也不因默认规模拆掉必要关系。用不着的留空；确实什么都不用加，kind 填“不需要”。
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
- 提交前由你结合完整世界记录核对时间、地点、人物动机、消息来源与已发生的后果，说明新真相怎样解释既有经历。保留调查员确实做过、见过、听过的事，不以改写暗中撤销他们的行动成果；需要移动或交物时另交相应操作。
- 人物要有自己的打算：enlist 登记 NPC 的持久目标，plan 写接下来几步及条件；记录人数不是后台调用并发数。调查员不在场时按计划或幕后触发继续，schedule 安排到点发生的事。事件里实际出场的人填 present；当场战斗、追逐与其结果填 fight/chase/stakes，不只写在朗读里。确定、无须检定的死亡后果填 effects，仍需交锋、检定或玩家选择的结果不提前兑现；遭遇若干轮后的事件用 after_rounds，保留实际时机。
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
幕后调整的规矩和你即兴时一样：引用的 id 只用【世界】里有的；由你在提交前结合完整记录核对新旧因果，改写玩家见过、听过的事，要在 reason 里写清他们当初为什么会听到、看到那样；不能借改写让调查员知道什么；改写只改真相，不挪人，不改东西在谁手里。"""


def context(m: Module, st: GameState, kind: Kind, *, action: str = "", reason: str = "", at: str = "", world: str = "") -> str:
    """Everything the director must not contradict, and what it was called for."""
    found = set(st.clues)
    here = m.location(at or st.location)
    people = []
    for inv in st.investigators:
        story = "；".join(f"{k}：{v}" for k, v in inv.backstory.items() if k != "说明" and v)
        people.append(f"- {inv.name}({inv.id}，{'真人玩家' if inv.id in st.human else 'AI调查员'})，{inv.age} 岁，"
                      f"{inv.occupation}。{story or '没有写背景'}")
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
    if kind == "开局改编":
        lines.append(f"【原作开场准备】\n{m.opening.strip() or '未另写'}")
        lines.append(f"【抵达地点的准备】\n眼前：{here.read_aloud}\n守秘人须知：{here.keeper or '无'}")
        events = [e for e in m.events if e.id not in st.fired and (e.where == here.id or e.when == f"visit:{here.id}")]
        if events:
            lines.append("【此地已有事件（按各自条件发生，不是全在开场发生）】\n" + "\n".join(
                f"- {e.id}｜条件 {e.when or '遭遇轮次'}：{e.text}"
                + (f"\n  眼前演出：{e.read_aloud}" if e.read_aloud else "") for e in events))
    if m.ties:  # threads the investigators' backgrounds can be tied to (Module.ties)
        lines.append("【可以接上调查员背景的线头】\n" + "\n".join(f"- {t.hook}" + (f"（{t.npc or t.place}）" if t.npc or t.place else "")
                                                         + (f"，适合：{'、'.join(t.fits)}" if t.fits else "") for t in m.ties))  # fmt: skip
    if wanted := [x for x in m.mechanics if not x.plugin]:
        lines.append("【模组需要、还没有插件的规则】\n" + "\n".join(f"- {x.name}：{x.rules}" for x in wanted))
    if world:  # the world record's backstage, as the director may see it (world/context.director_view)
        lines.append(f"【世界】现在是第 {st.minutes} 分钟。\n{world}")
    if ready_made := cards.npc_menu(f"{action} {reason}"):  # the imported 好事者 deck (cards.py)
        lines.append(ready_made)
    if action:
        lines.append(f"【调查员的行动】{action}")
    if reason:
        lines.append(f"【裁定员的看法】{reason}")
    return "\n".join(lines)
