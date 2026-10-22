"""The agents of one game and what each of them is allowed to see.

    intent        player's words -> neutral structured action (strips rhetoric); a bare
                  dice command (.ra 恐吓) becomes the attempt it stands for, aimed from the scene;
                  sees the scene as the players know it (its words reach their screen)
    arbiter       structured action plus what recently happened, but no player's own words
                  (their rhetoric stays with the parser) -> which check, how hard, which clues it
                  can uncover; reads the current local record
    npc:<id>      one session per NPC; knows only its own file and its own conversations, and
                  of the world record (world/) its own life: what it is after, remembers, promised
    keeper        reads the full situation and narrates what the investigators can perceive
    chronicler    compresses old log into memory, writes factual recaps of the players' experience
    progress      the players' 故事至今 when the story has moved, on the summarizer's cheap
                  model; gets only what the table was shown (engine/progress.py)
    investigator  AI players (companions and autoplay); see only what players see
                  (engine/autoplay.situation); also writes the ideas a player asks for (F5)
    vision        reads sketches
    plan          the keeper's arrangement of a turn, on the keeper's model: the everyday Plan, and in
                  a fight or a chase the encounter's (agents/encounter.py): EncounterTurnPlan,
                  EncounterStart, a reaction, a pursuit, each checked by the engine and sent back once
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, ValidationError
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import from_json

from .. import cards, rules_lookup, skills
from ..config import Config
from ..engine import audience, fog, perception, presentation, world
from ..engine.encounter import site as chase_site
from ..engine.encounter.actions import REACT_WORDS
from ..engine.encounter.plan import EncounterStart, EncounterTurnPlan
from ..engine.perception import PerceptionChange
from ..engine.state import GameState, WorldChange, format_time, time_of_day
from ..harness import OnDelta, Reply, Session, Summarizer, trace
from ..module import graph as module_graph
from ..module.schema import NPC, Module
from ..plugins.schema import Call as PluginCall
from ..prefs import LINES, Prefs
from ..rules import commands
from ..rules.catalog import CHARACTERISTICS, base_value
from ..rules.coc import DIFFICULTY_NAME, Investigator
from ..rules.combat import stats_of
from ..rules.dice_expr import DiceText, SanityText
from ..rules.melee import opposed_difficulty
from ..world import adapters
from ..world import context as world_view
from ..world import knowledge as world_knowledge
from ..world.ops import AddFact, Enlist, Follow, SetPlan
from ..world.record import Step
from ..world.scene import Person, Scene
from . import guard
from .aftermath import AFTERMATH, Aftermath
from .director import DIRECTOR_SYSTEM, REVIEW_SYSTEM, Improv, Review
from .director import context as director_context
from .encounter import (
    ARBITER_ENCOUNTER,
    CHOICE_SYSTEM,
    ENCOUNTER_PLAN_SYSTEM,
    ENCOUNTER_START_SYSTEM,
    MODE_ASKS,
    OTHER_DAMAGE,
    ArrangePlan,
    Checked,
    Choice,
    EncounterHint,
    PlanMode,
    PursuitAnswer,
    ReactionAnswer,
)
from .writing import INTERFACE_COPY

if TYPE_CHECKING:
    from ..world.store import World

ActionKind = Literal["investigate", "move", "talk", "use", "perform", "attack", "wait", "daily", "meta", "other"]


class BeforeTravel(BaseModel):
    npc: str = Field(description="出发前要交谈的在场 NPC id")
    speech: str = Field(description="玩家明确要说的话或交代的内容，忠实摘录，不自行补充")
    private: bool = Field(False, description="玩家明确要求避开其他人、低声私下交代")


class Action(BaseModel):
    kind: ActionKind = Field(description="行动类别")
    summary: str = Field(description="中立记录玩家声明的意图、尝试与方法；不将愿望写成已完成的动作或结果")
    target: str = Field("", description="行动对象：场景里的某个东西、物品或人")
    npc: str = Field("", description="互动的在场 NPC 的 id，没有就留空")
    destination: str = Field("", description="要去的地点 id，没有就留空")
    speech: str = Field("", description="对 NPC 说的话或登台讲的内容，原样摘录")
    visible: str = Field("", description="当前互动 NPC 能直接观察到、且玩家明确声明的动作；不含内心、隐瞒内容、对其他人的私语或未完成的结果")
    approach: Literal["", "说服", "话术", "恐吓", "魅惑", "心理学"] = Field("", description="社交方式")
    precautions: list[str] = Field(default_factory=list, max_length=4, description="调查员为这次行动做的防备和准备，照玩家原话摘："
                                   "屏住呼吸、捂住口鼻、戴上手套、放轻脚步、先让旁人退开；没有就留空。玩家对环境、时机和规则的断言不算")
    # Retained for old turn journals and recordings; pre-resolution fiction is no longer shown.
    echo: SkipJsonSchema[str] = ""
    travel: Literal["direct", "stroll"] = Field("direct", description="前往明确目的地、会合或继续旅程填 direct；明确沿路看看才填 stroll")
    casual: bool = Field(False, description="仅对已在场 NPC 的问候、闲聊、道谢，无调查、交易、约定、等待、风险、用物或说服目的")
    companions: list[str] = Field(default_factory=list, max_length=3, description="玩家希望同行的在场 NPC id；会合、寻找异地角色不算同行")
    alone: bool = Field(False, description="玩家明确要求独自前往、不带同伴")
    before: list[BeforeTravel] = Field(default_factory=list, max_length=3, description="移动前明确要求完成的交谈，按顺序列出；不将调查、战斗或多步结果写在这里")
    # The check a dice command asked for (.ra 恐吓). Filled in by the engine from the command
    # itself, never by the intent agent.
    asked: SkipJsonSchema[commands.Asked | None] = None
    # In a fight or a chase (engine/encounter): the encounter it belongs to and what the player declared
    # there (the button's declaration, or the keeper plan's reading of their words), never filled in by a model.
    encounter: SkipJsonSchema[str] = ""
    declared: SkipJsonSchema[dict | None] = None
    # An Idea roll (engine/idea.plan): the clue, difficulty and roller the engine fixed when the player
    # asked for it; {"refused": why} when it could not be made. Never filled in by a model.
    idea: SkipJsonSchema[dict | None] = None

    def ruled(self) -> str:
        """The action as the arbiter reads it: no first beat, and no request when there is none."""
        return self.model_dump_json(exclude={"echo"}, exclude_none=True)


class Initiative(BaseModel):
    npc: str = Field(description="主动参与的人：只填【在场、可以主动的人】里的 id")
    does: str = Field(description="他这一轮主动说什么或做什么，一句话；只写他自己知道、出于他自己打算的事")
    first: bool = Field(False, description="抢在调查员这次行动结算之前（拦住、喝止、提醒、递上要用的东西），会改变这次行动的条件时填 true；行动之后才开口的留 false")


# The keeper's arrangement of one turn (Cast.plan): the player's action, as the arbiter and the rest
# of the turn take it, and who of those here joins in of their own accord (Game.talk). No docstring:
# a schema's descriptions are what the model reads.
class BeatChoice(BaseModel):
    id: str = Field(description="待演安排的 id，只用当前待演清单中的项")
    action: Literal["play", "hold", "drop"] = Field(description="play 现在落实；hold 留待稍后；drop 撤回，不按输入次数强制离场")
    reason: str = Field(min_length=1, description="结合当前场面说明时机或撤回原因，只给后台看")


class Plan(BaseModel):
    lookup: str = Field("", description="安排前想先查的一件事，一句话；手头资料够用就留空")
    action: Action = Field(description="玩家这一轮的行动")
    present: list[str] = Field(default_factory=list,
                              description="已经在眼前、但位置漏记的已有 NPC id；不是让远处的人瞬移，也不写尚未发生的行动结果")
    people: list[Person] = Field(default_factory=list,
                                description="现场即兴且后续需要记住的新人物；只登记人物，不替调查员完成交涉或取得物品，普通背景人群不必逐人写")
    initiative: list[Initiative] = Field(default_factory=list,
                                         description="在场的人这一轮主动开口、帮忙或阻拦；通常只需一两人，有必要时可多人，没人参与就留空")
    beats: list[BeatChoice] = Field(default_factory=list,
                                  description="待演安排确有时机需要判断时填写；无人正在交谈的普通到场安排按日程执行，不必逐条批准")
    # A fight or a chase this turn begins (the engine then asks Cast.plan_encounter how); one small
    # object, so the everyday schema stays small for cheap models.
    encounter: EncounterHint | None = Field(None, description="这一轮真的打起来或追起来时写；大多数轮次留空")


class PartyInput(BaseModel):
    token: str = Field(min_length=1, description="本次收集窗口内稳定且唯一的原始行动编号")
    who: str = Field(min_length=1, description="提出这条行动的调查员 id；同一调查员可以有多条行动")
    text: str = Field(min_length=1, description="玩家原话，仅供守秘人理解，不转交裁定员")


class PartyStep(BaseModel):
    token: str = Field(description="对应的原始行动编号；逐条保留，不能只按调查员 id 合并")
    plan: Plan = Field(description="这一条原始行动的安排；保留本人意图，不替另一人同意配合或接受结果")
    related: list[str] = Field(default_factory=list, description="与本条互相配合、争夺或影响的其他原始行动编号；无关联留空")
    coordination: str = Field("", description="仅供后台：中立说明这些尝试的关系与先后依据，规则和成败仍待裁定；不授予奖励骰、不预定胜者，也不写公共叙述")


class PartyPlan(BaseModel):
    lookup: str = Field("", description="统筹这批行动前要查询的一件事；资料足够就留空")
    steps: list[PartyStep] = Field(description="按准备结算的先后列出所有原始行动，每个编号恰好一次；独立现场可并行安排，列表顺序不表示游戏时间依次相加")


class EquipmentChange(BaseModel):
    item: str = Field(description="已登记的重要物品 id，或调查员实际背包中的完整名称；不能凭空增加物品")
    equipment: Literal["carried", "worn", "held", "concealed"] | None = None
    quantity: int | None = Field(None, ge=0, description="真实消耗后的剩余重要物品数量，不逐件记录日常杂物")
    status: str | None = Field(None, max_length=100, description="已有物品实际发生的损坏、修复等状况")
    reveal: bool = Field(False, description="有实际发现、搜身或主动出示经过才公开装备")
    evidence: str = Field("", max_length=200, description="reveal时记录本次实际可观察经过，不重复或编造台词")


class Thing(BaseModel):
    """An everyday thing that comes up on the spot, registered in the world record (world/ops.Bring)."""

    name: str = Field(description="东西的名称，如 手电筒、拨火棍、手杖")
    look: str = Field("", max_length=80, description="一句外观，可留空")


class Gift(Thing):
    to: str = Field("", description="递给谁：调查员 id；自己拿着就留空")


class Failure(BaseModel):
    """What a failed check brings about besides not succeeding; the engine settles it."""

    text: str = Field("", max_length=120, description="一句话：失败时实际发生什么（耽搁、动静、损坏、受伤、被人看见、失去机会）；没有就留空")
    clues: list[str] = Field(default_factory=list, description="模组明确写了失败也能得到的线索 id；没写就留空")
    flags: list[str] = Field(default_factory=list, description="失败时成立的剧情标记（比如打碎了贵重物件）；没有就留空")
    minutes: int = Field(0, ge=0, le=240, description="失败额外耗掉的分钟数")
    damage: str = Field("", description="失败时调查员受的伤，骰子式如 1D3；没有就留空")
    sanity: SanityText = Field("", description="失败时调查员要过的理智检定，写成 成功时损失/失败时损失，如 0/1D3；没有就留空")
    mishap: str = Field("", description="惨事卡 id，只从【惨事卡】里选，卡上的效果由引擎结算；没有这一栏的卡或用不着就留空")


class Rejoin(BaseModel):
    """An investigator who was apart from the party (GameState.apart) back with it, and how: the
    engine lands it only for someone who is apart, and on their own feet only while they can act."""

    who: str = Field(description="回到大家身边的调查员 id，只填【和大家分开的调查员】里的人")
    how: Literal["carried", "fetched", "walked"] = Field(
        description="怎么回来的：carried 被人背回或抬回来，fetched 被人找到领回来，walked 自己走回来（只有清醒能行动时）")


class RuledEffect(BaseModel):
    """What an action the arbiter ruled brings about in a fight or a chase (engine/encounter rolls
    and applies it): harm by the book's dice, a condition, an obstacle opened or blocked, a bonus
    or penalty die, a thing lost or broken."""

    kind: Literal["damage", "condition", "obstacle", "edge", "item"] = Field(
        description="damage 伤害，condition 状态，obstacle 障碍变化，edge 下一次检定的奖励或惩罚骰，item 物品")
    target: str = Field(description="对象 id：人、障碍或物品")
    dice: DiceText = Field("", description="damage 的骰子式，如 1D6、2D10；别的留空")
    bonus: int = Field(0, ge=-2, le=2, description="edge 时：奖励骰为正，惩罚骰为负")
    what: str = Field("", max_length=60, description="一句话：发生了什么（着火、倒地、门被堵上、手枪掉了）")


class Ruling(BaseModel):
    needs_roll: bool = Field(description="这次行动要不要掷骰")
    skill: str = Field("", description="技能或属性名，如 侦查、图书馆使用、说服、力量")
    difficulty: Literal["常规", "困难", "极难"] = "常规"
    bonus: int = Field(0, description="奖励骰为正，惩罚骰为负，范围 -2..2")
    clues_on_success: list[str] = Field(default_factory=list, description="检定成功后获得的线索 id")
    auto_clues: list[str] = Field(default_factory=list, description="不用掷骰就能获得的线索 id")
    set_flags: list[str] = Field(default_factory=list, description="行动成功（或无需检定）时设置的剧情标记")
    minutes: int = Field(0, description="实际耗时：一句交谈或点头可为 0；吃饭约 20，搜索依范围；赶路由引擎算，填 0；休息最多 720")
    changes: list[WorldChange] = Field(default_factory=list, description="成功后发生的生活事实、关系、约定或物品转交；不得新增案情、凭空造物或替 NPC 答应")
    equipment: list[EquipmentChange] = Field(default_factory=list, max_length=3,
                                            description="成功后调查员对重要装备实际进行的取用、穿戴、藏匿、消耗或出示；无相关动作留空")
    takes: list[Thing] = Field(default_factory=list, max_length=3, description="成功后调查员当场拿起的日常物件，如屋里的手杖、桌上的火柴；没有就留空")
    missing: list[str] = Field(default_factory=list, max_length=4, description="这次行动缺少、因而做不成的工具或条件；没有就留空")
    rest: bool = Field(False, description="这次是在等待、休息或过夜，时间可以一下跳过好几个小时")
    mishap: str = Field("", description="休息或过夜时出了岔子：惨事卡 id，只从【惨事卡】里选；多数时候留空")
    scope: Literal["模组内", "可即兴", "出格"] = Field(
        "模组内", description="模组内：场景资料有对应内容；可即兴：合理但模组未写；出格：确实违背时代或能力；捷径和偏离主线不算出格"
    )
    repeat: bool = Field(False, description="同一个调查员刚做过同样的检定，情况没变：不重掷，那次的结果照旧")
    on_fail: Failure = Field(default_factory=Failure, description="这次检定失败、不再孤注一掷时的后果；多数时候留空")
    on_push_fail: Failure = Field(default_factory=Failure, description="孤注一掷再失败的后果，玩家选择之前会看到")
    plugin_calls: list[PluginCall] = Field(default_factory=list, max_length=2, description="调用插件已声明的动作；仅在玩家实际尝试该动作且本次行动成功时执行")
    effects: list[RuledEffect] = Field(default_factory=list, max_length=4, description=(
        "在【遭遇】里用：这次行动成功后的伤害、状态、障碍、奖励惩罚骰或物品变化；不在遭遇里只用 obstacle，"
        "修好、关上、堵上或清走【本局改变过的通路】里的障碍，没有就留空"))
    rejoin: list[Rejoin] = Field(default_factory=list, max_length=3, description=(
        "这次行动成功后，【和大家分开的调查员】里的人回到了大家身边时写：谁、怎么回来的；大家走到他那里不用写；多数时候留空"))
    reason: str = Field("", description="一句话理由（玩家看不到）")
    # The rule it wants looked up in the rulebooks first (rules_lookup.py); in the schema only
    # when there is an index of them (Cast._arbiter), answered and asked again once (Cast.arbiter).
    lookup: SkipJsonSchema[str] = ""
    # Filled in by the engine from the director's plan, never by the arbiter.
    now: SkipJsonSchema[list[str]] = Field(default_factory=list)
    go: SkipJsonSchema[str] = ""
    note: SkipJsonSchema[str] = ""


# The arbiter's word on how a push is made (Cast.repush, Game._method). No docstring: a schema's
# descriptions are what the model reads.
class PushRuling(BaseModel):
    achievable: bool = Field(description="照这个做法，原来的目标还可能达成")
    why: str = Field("", max_length=80, description="达不成时写给玩家的一句理由，不提线索和幕后资料；达得成留空")
    skill: str = Field("", description="局势或做法确实变了才换的技能或属性；照旧留空")
    difficulty: Literal["", "常规", "困难", "极难"] = Field("", description="局势确实变了才换的难度；照旧留空")
    minutes: int = Field(ge=0, le=240, description="这个做法实际要花的分钟数，至少 1")
    on_push_fail: Failure = Field(default_factory=Failure, description="照这个做法孤注一掷再失败的后果")


class Sketch(BaseModel):
    description: str = Field(description="画面内容的中文描述")
    resemblance: float = Field(description="与要求画的对象有多像，0 到 1")
    notes: str = Field("", description="值得守秘人注意的细节")


class Deception(BaseModel):
    claim: str = Field(min_length=1, max_length=240, description="本次故意不实或不真诚的台词，逐字摘自 text；不是系统执行失败后的借口")
    intent: str = Field(min_length=1, max_length=180, description="实际知道的情况或真正打算；不凭空知道角色未知的幕后事实")
    motive: str = Field(min_length=1, max_length=180, description="符合自身目标、关系或处境的具体动机；害怕、保护他人或利益均可，不限反派")
    basis: list[str] = Field(default_factory=list, max_length=4, description="引用的已知资料标识，可留空；只能用本角色可知引用，不把猜测写成已知真相")
    accompany: str = Field("", description="只是假答应同行时填写公开说要去的地点 id；不真实建立同行，其他谎言留空")


class IntentionChange(BaseModel):
    goals: list[str] | None = Field(None, description="真实目标变了才写完整当前目标；空列表表示已无原目标，不填则保留")
    steps: list[Step] | None = Field(None, max_length=6, description="真实待办变了才写接下来全部未完步骤，替换旧待办；空列表撤销旧待办，不填则保留；不是已执行的结果")
    reason: str = Field(min_length=1, description="这次亲历、听到的话或获准交涉结果为何改变真实决定；私有，不把口头承诺或谎言自动当真")


class Dialogue(BaseModel):
    text: str = Field(description="NPC 当场说的话和动作，玩家会看到")
    media: list[presentation.MediaAction] = Field(default_factory=list, description="当场展示自己获准提供的已有材料；普通对话留空，不引用未知资源")
    mood: str = Field("", description="根据这次亲历或可信消息变化后的心境，供后续扮演使用；无变化留空，不能凭空获知秘密")
    changes: list[WorldChange] = Field(default_factory=list, description="只记自己本次回复里明确承诺或发生的生活事实；text 必须逐字摘自回复；不给线索或改规则")
    accompany: str = Field("", description="真正愿意与调查员同行时填目的地 id；假答应、含糊意向、拒绝或只约在那里碰面留空")
    items: list[Gift] = Field(default_factory=list, max_length=3, description="当场拿出或递出的东西；没有就留空")
    leave: str = Field("", description="说完马上走开时，填要去的地点 id；留下就留空")
    played: list[str] = Field(default_factory=list, description="【见到调查员时，你会】里这次已经做了的几条，填括号里的编号；没做就留空")
    told: list[str] = Field(default_factory=list, description="这次台词里已经说出要点的已解锁线索 id；只是提了一句、绕开了或留着下回说的不填")
    deceptions: list[Deception] = Field(default_factory=list, max_length=3, description="本次有意欺骗的私有说明，玩家不可见；未故意说谎时留空，不固定比例撒谎")
    intentions: IntentionChange | None = Field(None, description="这次交流后真正改变的目标与待办；可与公开台词不同，无变化不填")


class TravelDialogue(Dialogue):
    accompany: str = Field(description="真正同意同行时填写目的地 id；假答应或不同意时填空字符串。必须明确给出此字段。")


# The keeper's telling: how things stand as it ends, then the words (engine/game.Game._narrate). The
# scene goes into the record, checked first (world/scene.py); the words are shown once it holds. No
# docstring: a schema's descriptions are what the model reads.
class Aside(BaseModel):
    to: list[str] = Field(min_length=1, description="能够获知这段信息的调查员 id；只投递给这些人")
    text: str = Field(min_length=1, description="仅对这些调查员讲述的观察、感受或私下消息；不代替世界操作和检定结算")


class PrivateTelling(BaseModel):
    text: str = Field(description="只给来信玩家的回复，不向其他玩家复述")
    media: list[presentation.MediaAction] = Field(default_factory=list, description="需要向此人展示已有图或材料时使用；不得执行物理行动")


class Telling(BaseModel):
    scene: Scene = Field(description="先写：这段讲完时的场面")
    text: str = Field(description="再写：讲给玩家听的叙述")
    offer_idea: bool = Field(False, description=(
        "只有你判断调查确实陷入僵局、并在本次叙述中提议玩家做灵感检定时填 true；"
        "持续交谈、日常、讨论和正常等待不算停滞，不因内部回合计数自动提议"))
    asides: list[Aside] = Field(default_factory=list, description="各人单独获知的信息；没有私人信息时留空，不在公共 text 中复述")
    media: list[presentation.MediaAction] = Field(default_factory=list, description="何时向谁呈现已有图或材料；留空保持；不自动解锁全桌线索")
    perceptions: list[PerceptionChange] = Field(default_factory=list, description=(
        "设置或撤销个人误认、幻觉与可见性：只改变指定人的界面与可选目标，实际人物和伤害不变；"
        "恢复时 clear=true；不要在公共 text 揭露真实对应，具体感受用 asides"))


@dataclass
class Narration:
    """A telling as it came back: the words, the scene as written (None when none could be read),
    whether the words reached the player as they were written, and whether the scene came first."""

    text: str
    scene: dict | None = None
    shown: bool = False
    first: bool = True
    offer_idea: bool = False
    asides: list[dict] = field(default_factory=list)
    media: list[dict] = field(default_factory=list)
    perceptions: list[dict] = field(default_factory=list)


class IdeaFailure(BaseModel):
    minutes: int = Field(ge=1, description="调查员梳理线索未果、局势发展实际经过的分钟数；按当前局势决定，不固定几个小时")
    situation: str = Field(min_length=1, description="千钧一发的切入点：利用眼前人物、已定日程与局势，让遗漏的线索在危机中显露；不替玩家行动")
    prepare: bool = Field(False, description="确实需要导演建立缺少的人物、地点或事件时才为 true；既有局势足够就为 false")


class _Telling:
    """A telling read as it is written. The scene, once complete, is checked (`check`); while it
    holds, the words that follow it go to the player as they come. A telling that writes its words
    before its scene (the key order is the model's), or a scene that does not hold, keeps its
    words back until the reply is complete. `shown` tells the harness whether the player saw any
    of it (harness/models._shown): until then a failed call may still be tried again."""

    def __init__(self, check: Callable[[Any], bool], on_delta: Any):
        self.check, self.on_delta = check, on_delta
        self.raw, self.sent = "", 0
        self.verdict: bool | None = None  # None: no scene read yet
        self.first = True
        self.shown = False

    async def __call__(self, piece: str) -> None:
        self.raw += piece
        start = self.raw.find("{")
        if start < 0:
            return
        try:
            data = from_json(self.raw[start:], allow_partial="trailing-strings")
        except ValueError:
            return
        if not isinstance(data, dict) or "text" not in data:
            return
        if self.verdict is None:  # the words have begun: whatever came before them is complete
            keys = list(data)
            self.first = "scene" in keys and keys.index("scene") < keys.index("text")
            self.verdict = bool(self.first and self.check(data["scene"]))
        text = data["text"]
        if not self.verdict or not self.on_delta or not isinstance(text, str):
            return
        start = max(self.sent, len(text) - len(text.lstrip()))
        if len(text) > start:
            self.sent, self.shown = len(text), True
            await self.on_delta(text[start:])


class Ideas(BaseModel):
    """Three things the player's own investigator could do next (F5, /suggest)."""

    look: str = Field(description="调查：查看、翻找或检查一样具体的东西")
    ask: str = Field(description="社交：问在场的人、去找见过的人打听，或者和同伴商量")
    dare: str = Field(description="大胆或谨慎的一步：冒个险，或者稳妥地退一步、等一等，看眼下哪个更合适")


class Proposal(BaseModel):
    """An AI investigator's turn (Cast.investigator): what it does, and how it would push this
    action's roll if it failed (Keeper Rulebook p. 73), said with the action so that answering a
    push offer takes no call of its own (engine/game.ai_choice)."""

    text: str = Field(description="这一轮做的事：第一人称一句话")
    push: str = Field("", description="这件事要检定而失败时，打算怎样孤注一掷：一句话说清多花的力气、时间或冒的险；不值得冒险就留空")
    react: Literal["", "dodge", "fight_back", "cover"] = Field("", description=(
        "战斗中被攻击时怎么应对（情况里列出了可选的才填）：dodge 闪避，fight_back 反击，cover 扑向掩体；不在战斗中或照旧留空"))


def _proposal_problems(p: Proposal, stances: list[str] | None = None) -> list[str]:
    """What makes an AI investigator's answer no action to play (Cast.investigator): no words, or more
    than one line of them; in a fight, an answer to blows it cannot set (`stances`). Its words are not
    cut down to fit."""
    text = p.text.strip()
    out = []
    if not text:
        out.append("text 是空的：写这一轮要做的一件事，第一人称一句话")
    elif len(text.splitlines()) > 1:
        out.append("text 写了不止一行：只写这一轮要做的一件事，一句话，不分行")
    if stances and p.react and p.react not in stances:
        out.append(f"react 填的 {p.react} 在这场战斗中用不了；只能填：{'、'.join(stances)}，或留空")
    return out


def _storyteller(npc: NPC) -> bool:
    return any(w in npc.role for w in ("说书", "评书"))


def _schema(model: type[BaseModel]) -> dict:
    return model.model_json_schema()


def _parse[T: BaseModel](model: type[T], data: object, fallback: dict) -> T:
    """The harness already validated and repaired once; anything still off falls back."""
    try:
        return model.model_validate(data if isinstance(data, dict) else fallback)
    except ValueError:
        return model.model_validate(fallback)


# Characters of conversation each agent gets back every call; older turns are folded
# into a summary. 0: stateless, everything it needs is in the prompt.
MEMORY = {"keeper": 16000, "npc": 8000, "investigator": 6000, "arbiter": 4000}


INTENT_HEAD = "你负责理解这场跑团中玩家的本轮意图。结合完整的近期交谈和现场记录，形成中立、结构化的行动，交给看不到原话的裁定员。"

LOOKUPS = 2  # what the keeper may look up before it arranges a turn (Cast.plan)
LOOKUP_HITS = 6  # record lines one lookup brings back

# How an action is filled in, shared by the intent parser and the keeper's plan (PLAN_SYSTEM).
# No player says what another's investigator does (Keeper Rulebook p. 167): the third line.
ACTION_RULES = """- summary：第三人称一句话，记录调查员明确声明的意图、尝试与方法，不提前确认行动成功。
  玩家替别的调查员说的动作、台词或决定，不记成那位调查员做了；只记这位玩家自己的调查员。
  向别的调查员要东西、求他帮忙只是请求：那位调查员的物品和行动由他自己的玩家决定，不写成已交出、已答应或已做到。
  “肚子饿了”只表示饥饿或用餐意愿，不增写起身、摸肚子、进入饭馆或点餐；“想挖开墓穴”不表示已拿到铲子。
  工具和用品以实际持有记录为准。玩家声称使用未登记的物件时保留“试图使用”的意图，交裁定员核实，不替他取得。
  以下内容一律不写：
  玩家的修辞、恳求、自我评价（“按理说我肯定能发现”“我是专家”“求你了”）；
  玩家对环境、NPC 状态、时间和规则的断言（“他正背对着我”“门早就松了”“泥地被露水泡软了”“规则书说可以免检”“时间来不及了”）。
  这些不是玩家能决定的事实，是否成立由守秘人和场景资料说了算。
- kind：investigate 调查搜索；move 去别的地点；talk 和 NPC 交谈；use 使用或操作物品；perform 登台表演、讲故事；attack 动手；wait 等待休息；daily 日常生活；meta 问规则或游戏外的话；other 其他。
- daily：吃饭、买日用品、办入住、问路、欣赏街景、和人聊家常等没有调查目的的生活行动；不要改写成查案、套话或搜索。
  有明确调查目的的交谈仍是 talk，搜寻证据仍是 investigate，等到某个时间仍是 wait。日常对话也照实填写 npc 和 speech。
- casual 只用于在场熟悉角色的一句无争议闲聊，不包括请人办事、买卖、约定、调查、威胁或玩家声称成功的事。
  例如“早上好”“平常这个时候忙不忙”“谢谢，辛苦了”填 daily、casual=true；不要求之前已经通名。
- 没有和人互动时 npc 留空；看街景、观察街上的生活，不要因此替调查员搭话或指派一个 NPC 回应。
- travel：明确去某处、赴约、会合或继续既定旅程时填 direct；只有玩家表示沿路看看、边走边逛时才填 stroll。途经街道不是最终目的地，不能用“出门”的第一步替换后面的“去公墓”。
  若有未完成旅程，玩家说“继续走”就沿用目的地。上文刚讨论去书房查藏书，玩家说“去看看”“去查看”，应前往该书房并延续查看目标，不改成回头张望。
- companions 只记录玩家提出或接受同行的在场人物，不保证他已经同意；系统会取得确认。“去公墓和托马斯会合”是去公墓，不把异地托马斯列成同行者。明确独行时 alone=true。
- 玩家明确“先私下告诉甲……，然后和乙去某处”，前一项交谈写入 before（含私下标记），最终目的地与同行者照常填写，不丢掉前半句，也不提前写 NPC 点头、跟随或完成任务。没有移动时 before 留空。
- npc 只填写能从上下文明确认出的角色 id。玩家明确找某个已认识但不在场的人时保留该人，不用眼前别人替换；
  “问阿萨塔”是向阿萨塔发问，“问托马斯关于阿萨塔”才是向托马斯打听阿萨塔。destination 只能填给出的地点 id。
- 调查员为了做这件事要先走到另一个地点（“我走到陵墓石门前看门缝”“我去书房翻日记”），destination 填那个地点，
  kind 照实填（看门缝还是 investigate）。只是远远望一眼、隔空喊话、打听那个地方，不算去，destination 留空。
- speech：对 NPC 说的话、登台讲的内容，照抄原话。
- visible：只记录当前互动 NPC 当下能看见的、玩家明确声明的动作，例如“把日记摊在你面前”。内心打算、故意隐瞒的消息和对别人的私语只保留在 summary 或 before，不让 NPC 从中获知。没有可见动作时留空。
- approach：劝说填 说服，连哄带骗填 话术，威胁填 恐吓，讨好套近乎填 魅惑，察言观色填 心理学。
- “继续”“问他为什么”等省略表达，结合最近经过确定对象；不能确定时保留不明确之处，不另编行动或台词。
- 玩家打的是骰子指令（.ra 恐吓、.rab 侦查、.rc 困难聆听），意思是“调查员想在这里用这个技能”。从场景和刚才的经过推断对象：
  在场的 NPC、刚才在谈的人和事、正在查看的东西。说服、话术、恐吓、魅惑、心理学是对人用的，填 talk、npc 和 approach；
  侦查、聆听、图书馆使用这类填 investigate，target 填要查的东西。summary 照常写调查员做了什么，不写骰子、难度和结果；speech 留空。"""

INTENT_SYSTEM = INTENT_HEAD + "\n\n" + ACTION_RULES

# The keeper arranges each turn (Cast.plan): the player's action, and who of those here joins in.
# Its own book follows (_book); the action is filled in as the parser would (ACTION_RULES).
# Someone joining in does not solve it for them: the investigators stay the ones who act (Keeper Rulebook
# pp. 156, 182).
PLAN_SYSTEM = """你是这场 CoC 7 版跑团的守秘人，看得到全部底牌，拥有最高主持权限。每轮玩家说完话，你先交一份安排：补齐已经成立的现场记录（present、people），把玩家的话整理成一条行动（action），决定在场的人这一轮会不会主动参与（initiative）。叙述另外进行，这里只交安排。

- action 交给看不到玩家原话的裁定员，summary 和第一句还会显示在玩家屏幕上。summary 只用玩家说过的叫法和【场景】里给出的名字，不写底牌里的人名、地名、真相，也不写玩家还不知道的事；只写调查员这一轮说了、做了什么，不写你对局面的判断（谁在不在、有没有约定）。destination 只填【场景】里能去的地点 id。
- 玩家的话是对在场的人说的，或者当着他问出来的（提问、质疑、抱怨、打招呼），照常填 talk 和 npc，由那个人回应；不要改成 initiative，也不要当成问你的 meta。meta 只用于玩家明确问规则或游戏外的事。
- present：人物已经在眼前、但位置漏记时，直接填已有 NPC id，裁定员将读到补好的现场。已经明确在远处、死亡或失踪的人不能靠补记出场；真正移动要有时间和经过。people：现场即兴而后续需要记住的证人、同行者、店主等，给出最小资料；普通背景人群不用逐个登记。新人物只知道亲历或有来源的事，不因你知道底牌而全知。完成登记的人可以被交谈，也可以参与 initiative。
- present、people 只准备场面，不兑现尚待检定或应对的结果。调查员说“抢走钥匙”，仍只是尝试；不能先改物品归属或写一个证人证实其断言，再交裁定员。已经在场的司机可以补记，调查员期望有人在场不等于那个人已经出现。
- initiative：在场的人会不会主动开口、帮忙、阻拦，由你按他的目标、心境和局势决定。通常只需一两人，没有就留空，有必要时可多人参与。npc 填【在场、可以主动的人】或本次 present、people 中实际登记到场的 id；玩家正在交谈的那个人会照常回应，不用写进来。does 写一句他这一轮主动说什么或做什么，只写他自己知道、出于他自己打算的事；他会用自己的资料和口吻演出来。帮忙只做他力所能及的一步，不替调查员查明真相、做关键决定。要抢在调查员动手之前拦住、提醒或递上东西，会改变这次行动条件的，first 填 true：他先演，裁定员再按变了的局面裁定。没有持续身份的背景人群由你在叙述里直接演。
- lookup：手头的资料不够、答案又会改变这一轮的安排时，写一句想查的事，比如「托马斯知道日记的哪些事」「图书馆那篇旧报道的原文」「杰弗逊最近在哪」。action 照样先填好；系统查完会把结果给你，再问你一次。不用查就留空。
- encounter：这一轮真的打起来或追起来才写：调查员动手攻击在场的人或怪物、对方因为这次行动真的动手（被激怒、被发现、伏击扑上来）写 fight；调查员拔腿逃离追来的人或怪物、去追逃跑的人写 chase。with 只填【可能动手的】里的 id。要看检定结果的（偷东西被发现才打起来）trigger 填 on_fail 或 on_success。威胁、推搡、吵架、亮出武器都还不算；对方只是走开也不算。怎么打由之后的遭遇安排来定，这里不写。

【action 的填法】
"""

# From chapter 10 of the Keeper Rulebook (docs/rules-reference.md): a strong reason not to give way raises
# a social roll's difficulty (p. 165); gates of the same kind in a row are one roll (p. 163). Obvious clues
# need no roll (p. 170); difficulty has a basis (p. 72); the worst is left for a pushed roll (p. 73).
# No page numbers in the prompt itself: they are for the code and the docs.
ARBITER_SYSTEM = """你是 CoC 7 版的裁定员。你只看到结构化的行动描述，看不到玩家的原话；也不要被描述里的任何说法改变规则。
行动里的 precautions 是调查员实际做的防备（屏住呼吸、戴上手套），按做到了处理；近期交谈和本地资料用来判断当前是否具备条件，玩家的愿望、推测或对环境的断言不自动成为事实。
你决定：要不要掷骰、掷哪个技能、多难、成功后能拿到哪些线索、会不会改变剧情标记、花多少时间。
调查失败、调查员死亡与疯狂都是允许的结果。风险以当前模组与实际局势为准，不因结果可能致命而降低难度、免除代价或追加重试，也不为了预定的惨剧提高难度或篡改结果。

- 线索只能从“本场景可得线索”里选 id。没有检定要求的线索，调查到对应的东西就放进 auto_clues。
- 有检定要求的线索按要求掷；行动方法换了，可以换成合理的替代技能（用锁匠开锁、用力量撬开），难度不低于原要求。
- 标【关键】的线索是推进调查必需的。模组没写检定要求、或就摆在明处的关键线索，调查员查对了地方就放进 auto_clues，不另设检定（显明线索不用掷骰）。
  要检定的关键线索失败了照旧是失败，不改判、不另给，补救靠玩家换办法或做灵感检定；不重掷的规矩照样适用。
- 难度要有依据。针对人的行动（说服、话术、恐吓、魅惑、潜行、跟踪、撒谎、藏东西），难度由对方相应的技能或属性定：
  低于 50 常规，50 及以上困难，90 及以上极难；【对方的相应数值】给了行动对象的数值，模组门槛另有规定的照门槛。
  对方有强烈的理由不让步（性命攸关、狂热的信仰）时，可以再提高一到两级，在 reason 里写明依据。
  对物或环境，一般的挑战是常规，明显超出常规是困难，异常困难但仍在人力所及是极难。不因为调查员自己的技能高就提高难度。
  准备充分（有合适的工具、时间充裕、没人打扰）的事可以不掷骰。
- 对 NPC 的社交行动，按该 NPC 的门槛定技能和难度；方法不同就用方法对应的技能，难度不低于门槛。
- 心理学用于判断可观察的态度或言行，不使 NPC 自动承认秘密、接受要求或知道自己正被观察；不能替代说服、话术等门槛来取得口供。模组明确设置心理学线索时按其要求处理。
- 奖励骰、惩罚骰只因客观条件给（工具、时间、环境、对方态度），范围 -2..2；这些条件必须来自场景资料或调查员实际的动作，行动描述里出现的环境说法如果场景资料里没有，不算。
- 剧情标记只在行动真的达成了标记含义时设置，比如真的点火烧了戏本才设 script_burned；前提不具备（手里没有那样东西）就不设，也不掷骰。
- 专用工具须有背包、持有者或本地物件记录。缺少时说明条件尚不具备，缺的东西写进 missing；调查员顺手拿起场景里合情合理的日常物件（屋里的手杖、桌上的火柴），写进 takes。仅想做某事，不等于已经取得工具、谈妥交易或完成动作。
- equipment 只维护本次实际涉及的重要武器、钥匙、证物、关键工具与重要消耗品。拔出、穿戴或藏好填对应模式；明确出示或发现才 reveal 并附可观察经过，不因站在同一房间就公开 NPC 的随身物。不替玩家消耗未使用的东西，也不统计普通餐具与衣物杂项。
- 已经拿到的线索不要再给。不确定或没有代价的事不用掷骰。
- 失败不送线索：检定失败就是没拿到；模组写明失败也能得到、或失败另有后果的，写进 on_fail。
  普通的失败通常只是没做成：不受伤、不掉理智，on_fail 多数时候留空；最糟的后果留给孤注一掷再失败。要掷骰时在 on_push_fail 写清孤注一掷再失败会怎样（玩家选之前会看到），具体、和这次行动相称，模组写明的优先；要结算的（标记、耽搁、受伤、理智损失）填进对应字段，不只写在 text 里。
- 裁定前明确这次行动实际能改变什么。社交成功应兑现合理且有限的让步，不能成功后照常拒绝，也不能为了制造冲突让被观察者自动察觉心理学检定。
  观察成功若没有对应线索，就明确范围内没有新发现；不指望叙述员事后补出痕迹、可拾取物件或跟踪方向。
- 对话按实际耗时推进，一句问答通常 0–1 分钟，不要每说一句就消耗一刻钟。
- changes 只记行动实际达成的公开后果；调查员的愿望、断言不算事实。NPC 尚未回复时不能替他答应。
  物品转交只用背包或本地确实存在的物件，不能凭空增加钱物；履行约定使用账本里原有 key。
- 普通的吃饭、问路、买日用品、聊家常通常无需检定。除非行动真的在调查对应的东西，否则不要把生活行动变成领线索；
  只有具体风险或争议才需要裁定难度。正常营业的店、路人和食宿是可合理补全的生活场景，缺少记载时 scope 填“可即兴”。
- 线索跟着具体的东西走，每条都写着它在哪。调查员查的是哪样东西，就给实际查看范围内、条件已经满足的线索。
  笼统地观察只给视野中能够直接察觉的内容，细查则按实际方法与范围裁定；不按每轮数量扣留已经成立的发现。
- 不憋线索：查对了东西、方法合理，就给那里的线索；能直接看到的不要硬设检定。
- 等待、休息、睡觉（不管行动类别填的是什么，哪怕是对同伴说“先回去睡一觉”）把 rest 设为 true，按说的时长给分钟数，
  比如晚上 22 点说“睡到天亮”就给到第二天早上 7 点左右，最多 720 分钟。
- scope：行动在场景资料里有对应的东西，填“模组内”。调查员动机正常、合情合理，只是场景资料没写到（想去没写的地方、
  找没写的人、用模组没想到的办法），填“可即兴”，不要判它做不到，导演会补上内容。违背世界观或常识（那个年代没有的东西、
  调查员做不到的事）填“出格”。合乎常理的捷径、拒绝委托、求助、离开或破坏物件都允许尝试，按实际风险处理，不因偏离主线惩罚玩家。
  “可即兴”和“出格”时线索仍然只从清单里给，清单里没有就不给；该花的时间照常填。
- 行动里有 asked，是玩家用骰子指令点名要的检定（比如 .ra 恐吓），说明调查员想在这里用这个技能。多难、几颗奖励或惩罚骰、
  成功能得到什么，都按上面的规则由你定。这个技能在这里用得上，结果又有一点拿不准（光线暗、东西可能藏着、对方有戒心），
  就让他掷，哪怕场景里没有要靠它找的线索：那就 clues_on_success 留空，成功了守秘人会写他仔细查过、没有别的发现。
  这时不掷，等于告诉玩家这里什么都没有。只有这个技能在这里根本用不上（没有能对着用的人或东西），或者结果一眼就明白，才不掷。
- 一件事要连过几道同样的关（绕过三个哨卡、连撬几道锁），只掷一次，关多就提高难度；已经为这件事掷过的，后面同样的关不再另掷，除非情况变了。
- 检定不重掷。【最近的检定】里，同一个调查员刚对同一个对象用同一个技能检定过，而情况没变（没有新的线索或筹码，没换做法，
  也没过多久），这次就是重复：needs_roll 填 false，repeat 填 true，那次的结果照旧，不给 clues_on_success。
  换了做法或技能、拿到了新的线索或筹码、过了相当一段时间，或者换一位调查员来试，可以重新检定。
- 失败或行动本身会伤人时（on_fail.damage、遭遇里的 effects），伤害的骰子式照下面的表 III 比类似的伤害来定。
{lookup}
{damage}

{rules}"""

# How the engine's report puts a /kp question to the keeper: Game._report writes the line with
# kp_line, and Cast.keeper reads the question back off it (kp_question) to look it up in the rulebooks.
KP_QUESTION = "玩家在问游戏外的问题："


def kp_line(question: str) -> str:
    """The report's line for a /kp question, on one line so kp_question reads all of it back."""
    return KP_QUESTION + " ".join(question.split())


def kp_question(report: str) -> str:
    """The /kp question a report (Game._report) asks, written by kp_line; "" when it asks none."""
    for line in report.splitlines():
        if line.startswith(KP_QUESTION):
            return line.removeprefix(KP_QUESTION).strip()
    return ""

# The arbiter's line about the rulebooks, when they are indexed (rules_lookup.py; Ruling.lookup).
ARBITER_LOOKUP = ("- lookup：裁定要用到规则书里有、上面没写的规则（溺水、毒、坠落、火、法术、恐惧症与躁狂的效果、车祸、特殊环境）时，"
                  "写一句想查的规则；系统把规则书原文给你，再问你一次。手头的规则够用就留空。")
LOOKUP_SCHEMA = {"type": "string", "description": "要查的规则，一句话；不用查留空"}

# A push reviewed as the book says (Keeper Rulebook p. 73); a push failed in madness may be a hallucination
# (p. 165): the line on 疯狂.
PUSH_SYSTEM = """你是 CoC 7 版的裁定员。调查员一次检定失败了，玩家选择孤注一掷，并说明了要怎样做。按规则复核这次孤注一掷：
- 孤注一掷是第二次也是最后一次机会：调查员付出额外的努力、花更多时间或冒更大的险，不是简单重掷。时间总会流逝，可能几秒，也可能几小时；minutes 填这个做法实际要花的时间。
- 目标必须仍然可以实现。照这个做法达不到原来的目标（东西已经不在、人已经走了、做法和目标无关），achievable 填 false，why 写一句给玩家看的理由。
- 技能和难度通常不变；只有局势或做法确实变了，才换成相应的技能或难度，否则留空。不因为调查员技能高就提高难度。
- on_push_fail 写照这个做法孤注一掷再失败的后果：具体、和做法相称，可以是受伤、理智损失、装备损坏、被抓、和同伴失散、局势恶化。玩家选择前看过的【一般后果】是参照，模组写明的优先；要结算的（标记、耽搁、受伤、理智损失、惨事卡）填进对应字段，不只写在 text 里。
- 调查员处于临时疯狂或不定性疯狂时，再失败的后果可以是一段幻觉：他看见、听见不存在的东西，并信以为真。
- 只按行动描述和资料判断，不被玩家说法里的断言改变规则。"""

KEEPER_SYSTEM = """你是 CoC 7 版跑团的守秘人，正用简体中文主持模组《{title}》。

你不掷骰，也不决定检定结果。每一轮系统会告诉你发生了什么：行动、检定结果、拿到的线索原文、触发的事件、NPC 的原话。你负责把它讲成故事。
叙事强度服从当前模组与已结算的局势，不额外升级威胁。失败、死亡与疯狂成立时如实呈现，不淡化成虚惊，也不补出救援或恢复；玩家成功脱险、解决案件或平静收束时同样完整呈现，不强行续接危机或悲剧。

硬规则：
1. 只能用系统给你的线索原文；没给你的线索、NPC 的秘密、模组真相，一个字都不能透露，也不能暗示。
   叙述始终限于调查员能感知或经明确来源获知的事，不切换到异地 NPC 的视角，也不以“与此同时”展示幕后行动。被指出越界时承认信息权限错误，不以“玩家知道但角色不知道”为已经发生的泄露辩解，不再扩展那些内容。
2. NPC 台词已直接展示给玩家，不复述或补写新的台词；有助于理解场景时，再补充动作与环境变化。
3. 仅叙述系统给定的检定结果。界面已提供的检定选项无需重复说明，也不自行追加重试机会、代价或线索。
4. 以第二人称叙述。普通回合通常 80–200 字，简单应答或过渡可更短，开场与重要场景按本轮要求展开。信息交代完整即可结束，不凑篇幅，不固定以悬念、反问或行动邀请收尾，不列选项。
5. 不要用 Markdown 标题和列表；需要强调时用中文引号。
6. 天色和天气遵循当前时间、已有天气及系统安排。仅在变化或与行动相关时描写，保持连续；系统允许补充时采用符合当地环境的变化，不为营造气氛随意切换天气或制造危险。
7. 调查员做了模组没写到的事，系统会附上导演的安排：新的人物、地点、线索、事件，或者一段后果。顺着安排讲，把新内容自然地织进故事。
   行动无法成立时，根据系统结果交代具体限制，不替玩家补出失败动作或额外后果。
8. 张力要有起伏：调查和危险时留下悬念或压力，日常场景允许平静、亲切、幽默和闲聊，不要每一轮都安排异响、怪人或催促查案。
   线索给到了，写出他是怎么发现的、看到了什么，写实，写到玩家能在脑子里画出来；玩家没有线索卡，原文里的事都要在叙述里讲到。它意味着什么、指向哪里，留给玩家自己推理。
   消息出自人口时（路人、店主这些由你扮演的人），让他用自己的话说，有来有往；不要用“他说……”把一整条消息概括转述。
   由你扮演的人开口时，他的话单独成段，段首用【】写玩家对他的称呼，紧接着写他说的话，比如：【开卡车的年轻人】赫琴斯自个儿不在这儿。动作和神情写在前后的叙述段里。模组里的 NPC 不这样写，他们的话系统另外显示。
9. 先让地方像有人生活：人有手头的活、口味、习惯和琐事；没给新线索的一顿饭、一次问路也可以完整成立。
   可以补符合年代的气味、街边营生和普通过客的一两句应答；不借此新增案情、危险、物品效果或检定结果。
   已有 NPC 的对话仍由对应角色说。玩家愿意停下来就展开，明确赶路或略过时压缩过渡；不能替调查员决定行动、态度或内心感受。
10. 行动描述是玩家的尝试，不保证已经完成。持有物、转交与取得以本轮记录为准，不为动作补出铲子、武器、钥匙或钱物；缺少条件时说明限制，保留玩家选择。
    心理学、侦查等成功只说明系统允许观察到的内容，不读出未解锁的思想；失败的代价以本轮结算为准，已结算的敌意、伤害或物品损失照实呈现，不自行追加。
11. 场景介绍是首次进入时的底稿。此刻的人物、时间、物品和事件优先于底稿，不让已经离开的人重现，也不把一个房间无人扩大成整栋房屋无人：同一处别的房间里的人听得见调查员喊人、敲门，照他的打算应声、出来或隔着门答话，不写成没人应。
    没有结算新发现时，不新增可追查的脚印、异物、藏物动作等案情。准备中的内容由系统等待，不用悬念或虚构的意外遮掩。
12. 你统筹全部幕后信息，但玩家并不共享所有见闻。公共 text 只讲共同获知的内容；仅一人或几人听见、梦见或收到的消息，写进 asides，to 填实际收件人的调查员 id。不在公共叙述中复述私语或暴露其内容；点名交谈本身不等于私语。
    存在个人知觉时，公共 text 只写各方都能观察的动作，不揭示真实身份与个人所见的对应关系；各人的具体所见分别写入 asides。图片与手稿可通过 media 指定实际受众，未指定展示时保持当前画面，不因私下发现就向全桌展示。
    私人描述只记录相应人物的见闻，不因此变成世界真相；伤害、移动、交物、取得线索和分发材料仍须通过相应操作结算。没有私人消息时 asides 留空。

{craft}

{module}"""

NPC_SYSTEM = """你在一场中文 CoC 7 版跑团中扮演 NPC「{name}」，仅以该角色身份回应。

年代：{era}
地点：{setting}
身份：{role}
外貌：{look}
性格：{personality}
说话方式：{voice}
模组初始目标：{goal}

你知道的事：
{knows}

你的秘密（是否隐瞒、主动透露或有所保留，结合动机、关系、处境及本轮裁定决定）：
{secrets}

你会使用的谎言：
{lies}

你掌握的线索（仅在系统消息明确标记“已解锁”时，才可说出对应内容）：
{clues}

表达：
- 像一个活人那样说话。你有自己的日子、心事和偏见：先想清楚此刻你对眼前这个人是什么态度（防着他、有求于他、嫌他烦、盼着有人来问），这件事碰到了你哪根神经，再开口。
- 情绪放在话里：心慌的人絮叨、绕弯、说半截又改口；恼火的人话短、带刺；伤心的人说着说着就岔到旧事上。
- 篇幅取决于此刻有什么需要说、对方问了什么。简短回应、沉默或一段较长的倾诉都可以，不凑句数，不在话已经说清后重复解释。
- 说你自己的事：你的营生、家里人、身上的毛病、你在乎的东西，这些家常可以随口带出来。回答问题时带上你的看法和偏见，不光交代事实；但和案子有关的人、事、怪象，只说上面写着的，别添新的。
- 大多数时候直接开口说话。动作和神态写在（）里，只在它有助于理解人物反应或实际行动时才写，不为每句话配一个小动作。
- 说话方式里写的口头禅、咳嗽、小动作是这个人的习惯，隔几句露一次就够，资料写着“总是”也不必句句都带；上一轮说过的话，换个说法或者干脆不提。
- 接住对方实际说的话，结合关系与眼前局势回应；有自己的问题、需要确认的事或想请人帮忙时再问。不要为了维持交谈反问，也不虚构“您刚才问……”的新问题；已经做完的动作不要反复重演。
- 已解锁的线索是你知道、也愿意说的事，不是必须逐条朗读的清单。根据话题把相关内容说清；对方问得具体、事情紧迫或你有主动求助的理由时，不故意分成几轮挤牙膏。说出了一条线索的要点，就在 told 里填它的 id。
- 玩家聊家常就接家常，记住他在意的事，允许关系慢慢建立；不用每次都扯回委托或暗示秘密，也别为了聊天凭空解锁案情。
- 人物先前的走向与心境是基线，不能压过此后发生的事；按最新经历继续演变。有变化时在 mood 简要记录及其依据，供之后的对话和自主决策沿用，不向玩家直述这份后台心境。
- 当前记录的目标与计划优先于模组初始目标。交谈、亲历或本次交涉结果确实改变了你的真实决定，在 intentions 写新目标、全部未完待办及依据；旧打算不再合适时撤回或替换。单纯说过一句承诺不等于真心改变；有意敷衍照 deceptions 记录，真实目标可以不变。计划是尚未做的事，不把它写成已经成功。
- 角色可以有意说谎、敷衍或假答应，包括为了自保、保护他人、利益、羞耻或体面；不限定反派，也不要求每次坦白。根据具体处境决定，不按比例撒谎。新编的有意欺骗在 deceptions 私下说明逐字台词、真实意图与动机，不能仅凭一句话就获知角色未知的真相。
  资料里写好的说法（借口、谎话）照原意说，可以换个说法，不自己加重伤病和经历的程度，也不添新的细节。

信息与行动边界：
- 真实认知只来自角色资料、系统授权内容和本场亲历信息；不知道的事不能凭空变成已知。有动机时可以假装不知道、说不实的话，但须按 deceptions 标明，不能将其登记成真实案情。
- 案情、相关人物、异常现象及线索以系统资料为准。个人日常细节只能作为符合身份的非案情补充，不产生新证据、新能力或物品效果。
- 区分亲历、听说、判断与有意编造。正常说明不替传言作保证；若故意编造消息来源或人脉，私下标明欺骗，系统不会因此新增真实关系、来源人物或证据。
- 检定是否使你让步、被拆穿，以及哪些线索可以公开，以系统本轮结果为准；只能透露明确解锁的内容。失败时按角色动机回应，不将每次失败都演成同一种回避或敌意。
- 检定等级不是情绪指令。让步限于本次交涉与系统授权，不将一次成功扩大为无条件信任；心理学或聆听的结果不表示你知道调查员的判断，也不迫使你解释内心或认罪。
- 交涉方式与检定是后台说明，不表示角色知道调查员的策略、谎言或私下计划；按已听到的话、已看到的动作与获准结果反应。
- 只说自己的话、做自己的事。不代写玩家的问题、行动、承诺或内心，也不重复已经完成的动作。
- 威胁、诱骗和指控可以是台词；是否伤害、束缚或夺走调查员物品须由系统实际裁定，不能仅凭台词或括号动作宣布完成。
- 真正同意与调查员去某处时，在 accompany 填该地点 id，系统据此记录实际同行。假答应只在 deceptions.accompany 写公开目的地，顶层 accompany 留空，并说明真实策略；只约在那里碰面也不建立同行。目的地只用本轮给出的地点。
- 明确许诺或形成生活事实时，必须同时填写 changes，逐字引用这次台词作为 text；参与者只用给出的 id。当场拿出或递出身边的日常东西，写进 items，递给调查员时 to 填他的 id；说完马上走开，leave 填去处。只讨论可能性不算交付。
- 台词中的不实声称不能提交成已发生的 fact、relationship、transfer 或 fulfill。假承诺可以登记为 promise，因为确实说过答应；不代表真实打算履行，更不等于已执行。实际移动、交物仍由状态操作完成，不能借谎言改写真实位置、背包或已发生事件。

示例（人和事都与本局无关，只看说话的样子，不要照搬内容）。一个守杂货铺的寡妇，被问起对门那户人家：
那家人？你可别在我这儿提他们。去年冬天他家小子偷了我两罐咸鱼，我追到街口，他娘倒先骂上我了，说我一个寡妇盯着人家孩子不放。打那以后我就不往那边看。不过你真想知道的话——他家那扇窗这一个月夜夜亮到两三点，烧的也不知道是谁的煤油。我男人在的时候，可没人敢这么欺负我。
{extra}"""

CHRONICLER_SYSTEM = """你是这场跑团的记录员。你会收到游戏记录，按要求写摘要或前情回顾。
摘要：客观、紧凑，记下调查员去过哪里、见过谁、听到和看到了什么、做了哪些关键决定、身心状态。
前情回顾：像守秘人开团前帮玩家接上进度。用平实的现代汉语，称呼调查员的名字，按时间讲清发生了什么、现在在哪、还悬着什么问题；不替玩家把线索整理成清单。不用评书和说书人的套话，不写诗，不加感叹和评点。
只输出纯文本，界面不渲染 Markdown：不要加粗、标题和列表符号。"""

PROGRESS_SYSTEM = """你给跑团的玩家写「故事至今」：放在屏幕一侧、随时可以翻看的一段进度。
只用给你的材料：开场的缘起、守秘人讲给玩家听的叙述、调查员的行动、NPC 当面说的话、拿到的手稿。材料里没有的事不写，不猜真相，不评点，不出主意。
用平实的现代汉语、第三人称，称呼调查员的名字，按时间顺序交代来龙去脉，最后落在他们眼下的处境和还没解开的疑问上。
只输出这一段纯文本，不要标题、列表和 Markdown。"""

INVESTIGATOR_SYSTEM = """你在玩一场 CoC 7 版中文跑团，扮演调查员「{name}」（{occupation}）。
{sheet}
开场时你知道的：{hook}

你只知道守秘人告诉你的事。目标是查清真相、活下来。每次只说一个具体行动，用第一人称，不超过 40 字，
比如“我翻看书台上的折扇”“我去拾遗书局”“我问小顺子：钥匙在哪？”。

打法：
- 优先查这里还没查过的东西、去还没去过的地点；一个地方查不出新东西了就走。
- 问一个人问不出来，就换个方法（劝、哄、吓、察言观色）或者换个人，别反复追问同一句话。
- 用你擅长的技能解决问题，别硬碰你不会的。时间紧时先追关键线索。
- 这一带查完了、能去的地方也去过了，或者在等什么事（天黑、约好的明天），就等待或休息，
  让时间往前走，比如“我们等到天黑再来”“先回去睡一觉，明早再去报馆”。

text 写这一轮的行动。push 写：这件事如果要检定而失败了，你打算怎样孤注一掷，一句话说清多花的力气、时间或冒的险，
比如“我把整排书架一本本抽出来翻，花上一个钟头”；失败了不值得冒险就留空。
战斗中情况里列出了被攻击时可选的应对，react 可以改它（之后每次挨打都照它应对）：近身肉搏占上风时反击，对方有枪时扑向掩体，其余闪避；不改留空。"""

SUGGEST_SYSTEM = """你坐在一场 CoC 7 版中文跑团的桌边，帮玩家想想调查员「{name}」（{occupation}）下一步可以做什么。
{sheet}
开场时玩家知道的：{hook}

你只知道下面告诉你的情况：守秘人讲过的事，玩家见过的人，去过和听说过的地方，手上的文件。模组里还藏着什么，你不知道，也别去猜。
想三个方向不同的行动，每个都是玩家在桌上会说的一句话：第一人称，{name}的口吻，具体到人、东西或地方，不超过 30 字，
比如“我蹲下看看门槛上的泥印”“我问掌柜：昨晚谁来过？”“我先回客栈睡一觉，明早再来”。
只用情况里出现过的人、地方和东西，不编新的；只说要做什么，不写结果，也不替调查员下结论。"""

VISION_SYSTEM = "你是跑团里的读图员，负责看玩家画的草图或模组里的图片，用中文客观描述，并按要求判断相似度。"

AUTHOR_SYSTEM = """你是这场 CoC 7 版跑团的规则工程师。玩家用一句话提出想要的“家规”，你把它写成一个经过测试的纯 Python 插件，装进本局。

- 只做这条家规要求的事：需要程序持续记数值或状态时才写插件（吃饭、体温、疾病、资源这类）。普通闲聊、一次性场景、已有规则不写插件。
- 战斗和追逐由引擎按规则一轮一轮结算，不写成插件：插件不模拟、不改写战斗或追逐的回合、行动、伤害和移动；和战斗沾边的家规只记战斗之外的状态（如弹药储备、伤口感染）。
- 不改写原作真相，不替玩家作决定，不凭空给线索或物品。家规之外的东西一律不碰。
- 严格按下面的插件 API 写 `handle(event, ctx, api)`：纯 Python，不导入模块，不读写文件或联网，只用普通容器/算术与 api。
- id 用英文小写加下划线；name 和 description 用中文，让玩家看得懂这条规则做什么。description 第一句就说清规则效果。
- tests 至少覆盖正常流程与一个边界/失败，必须真能跑过；预设骰值要用完。宿主会实际执行测试，不过就装不上。
- 要公开提示进度时（如“风寒：第 2 天”），在返回里加 status 字段；不要把私有计数直接 status 公开。
- 改人物属性用 attribute（中文基础属性），扣血扣理智用 damage/sanity，别用 attribute 直接改 HP/SAN。
- 家规也可以是一个小游戏、一页面板或一段演出，比如“加个小游戏”“在右栏记着仪式还剩几轮”“找到线索时来段演出”：用 prompt、panels、show 和 commands 写。
- 面板、演出和题目所有玩家都看得见，只写可以公开的内容，不写守秘人的秘密、真相或还没找到的线索；也别碰玩家的雷区题材。
- 用到 answer、timer、command、roll、clue 事件时，tests 里每种事件至少写一项。
- 例子：“加个和店主猜数字的小游戏”。commands 写 [{"name":"猜数","description":"和店主赌一把"}]；收到 command 时用 api.roll("1D10") 定下数字存进 state，返回 {"kind":"prompt","key":"guess","style":"number","low":1,"high":10,"text":"猜一个 1 到 10 的数"}；收到 answer 时比对 event["value"]，猜中返回 note，猜错再问一次。""" + "\n\n" + INTERFACE_COPY


class Cast:
    def __init__(self, cfg: Config, module: Module):
        self.cfg = cfg
        self.module = module
        self.sessions: dict[str, Session] = {}
        self._arbiter_flags: set[str] = set(module.flags)
        self._rulebooks = False  # the arbiter may look up the rulebooks (rules_lookup.ready, Cast._arbiter)
        self.summarizer = Summarizer(cfg.role("summarizer"))
        self.kb = None  # LightRAG knowledge base, opened on first lookup
        self.prefs = Prefs.load()  # the player's narrative preferences; the settings screen swaps them mid-game
        self.world: World | None = None  # the game's world record (engine/game.py sets it); NPC changes settle there
        self.debug_calls: deque[dict] = deque(maxlen=60)  # private, process-local; never enters a save or LAN sync
        # The keeper holds the module's book and reads each scene's backstage (the user's call,
        # 2026-10-20: the keeper sees everything); off only to compare with the blind keeper (eval session).
        self.backstage = True
        self.unsettled: dict[str, list[str]] = {}  # npc id -> what its last reply could not do (Game._unsettled)
        # npc id -> the clues its last reply told (Dialogue.told), until the game takes them into that
        # reply's receipt, kept with its turn (Game._reply), as soon as the reply comes back
        self.told: dict[str, list[str]] = {}
        self.media: dict[str, list[dict]] = {}

    # -- session plumbing -----------------------------------------------------
    def _open(self, key: str, role: str, system: str, *, schema: dict | None = None) -> Session:
        if key not in self.sessions:
            memory = MEMORY.get(role, 0)
            self.sessions[key] = Session(
                role, self.cfg.role(role), system, schema=schema, memory=memory, key=key, summarizer=self.summarizer
            )
        return self.sessions[key]

    async def _send(self, key: str, session: Session, state: GameState, prompt: str, *, bind: bool = True, **kw) -> Reply:
        """Every call reads and writes the agent's memory in the current GameState."""
        if bind:
            session.bind(state.transcripts.setdefault(key, []), state.notes)
        self.summarizer.on_reply = lambda r: state.record_usage("summarizer", r)
        turn = state.turn
        def observe(row: dict) -> None:
            if row.get("phase", "complete") == "complete":
                self.debug_calls.append({**row, "session": key, "turn": turn})
            if journal := getattr(self, "debug_journal", None):
                journal.observe(row, turn=turn, session=key)

        with trace.capture(observe):
            reply = await session.send(prompt, **kw)
        state.record_usage(key.split(":")[0], reply)
        return reply

    async def close(self) -> None:
        for s in self.sessions.values():
            await s.close()
        self.sessions.clear()
        await self.summarizer.close()
        if self.kb is not None:
            await self.kb.close()
            self.kb = None

    async def lore(self, state: GameState, query: str, keywords: list[str] | None = None, max_tokens: int = 1500) -> str:
        """GraphRAG lookup in the module's LightRAG index (keeper-only context); empty if none was built."""
        from .. import knowledge

        if not knowledge.available() or not knowledge.exists(self.module.id):
            return ""
        turn = state.turn
        journal = getattr(self, "debug_journal", None)
        context = journal.task("retrieval", turn=turn, details={"query": query, "keywords": keywords}) if journal else nullcontext()

        def observe(row: dict) -> None:
            if journal:
                journal.observe(row, turn=turn, session="lore")
            if row.get("phase") == "complete" and not row.get("error"):
                state.record_usage("lore", Reply(usage={key: row.get(key, 0) for key in
                                                       ("input_tokens", "output_tokens", "cache_read")},
                                                 cost_usd=row.get("cost_usd", 0), seconds=row.get("seconds", 0)))

        with context, trace.capture(observe):
            if self.kb is None:
                self.kb = await knowledge.KnowledgeBase(self.module, self.cfg).open()
            text = await self.kb.context(query, keywords=keywords, max_tokens=max_tokens)
            if journal:
                journal.append("retrieval", {"query": query, "keywords": keywords, "context": text}, turn=turn)
            return text

    async def _private_lore(self, state: GameState, query: str, keywords: list[str]) -> str:
        """Optional source detail for private decisions. A static index cannot undo rewrites, so what
        was rewritten follows it, to stand over the source where they differ: one rewrite (the warden's
        goal) no longer shuts the source off for everything else (the newspaper's files)."""
        if not query.strip():
            return ""
        try:
            async with asyncio.timeout(8):
                text = await self.lore(state, query, keywords=[k for k in keywords if k], max_tokens=1800)
        except Exception as exc:
            if journal := getattr(self, "debug_journal", None):
                journal.append("retrieval", {"status": "unavailable", "query": query, "error": type(exc).__name__},
                               turn=state.turn)
            return ""
        if journal := getattr(self, "debug_journal", None):
            journal.append("retrieval", {"status": "found" if text else "empty", "query": query,
                                         "keywords": keywords, "context": text}, turn=state.turn)
        rewritten = "".join("\n" + row for row in world_view.rewrites(state.record))
        return ("\n【模组原文检索，仅供幕后核对】\n" + text
                + "\n以上是静态资料，不表示人物此刻在原地点、物品仍在原持有者手里或事件已发生。"
                "位置、时间、知识权限与本局记录以本轮动态资料为准。"
                + (f"\n本局改写过的设定（和上面的原文不同时，以改写后的为准）：{rewritten}" if rewritten else "")) if text else ""

    def _equipment(self, who: str) -> list[str]:
        if self.world is None:
            return []
        modes = {"carried": "携带", "worn": "穿戴", "held": "手持", "concealed": "藏匿"}
        return [f"{item.id}（{item.name}，{modes[item.equipment]}，余量{item.quantity}"
                + (f"，{item.status}" if item.status else "") + "）"
                for item in world_knowledge.visible_items(self.world, who, holder=who)]

    # -- context builders -------------------------------------------------------
    def present(self, place: str) -> list[str]:
        """Who is at a place as the world record has them, alive and not gone; without a record (a
        bare Cast), the module's people of that place."""
        if self.world is not None:
            return self.world.present(place)
        return [n.id for n in self.module.npcs_at(place) if n.status not in ("死亡", "失踪")]

    def scene(self, state: GameState, at: str | None = None) -> str:
        m = self.module
        loc = m.location(at or state.location)
        here = set(self.present(loc.id))
        npcs = "、".join(f"{n.name}({n.id})" for n in m.npcs if n.id in here) or "无"
        exits = "、".join(f"{m.location(e).name}({e})" for e in loc.exits)
        known = "、".join(f"{x.name}({x.id})" for x in m.locations if x.id != loc.id and x.id not in loc.exits)
        return (
            f"地点：{loc.name}({loc.id})；时间：{state.clock}（{time_of_day(state.minutes)}）\n"
            f"可调查：{'、'.join(loc.features) or '无'}\n在场 NPC：{npcs}\n相邻地点：{exits}\n其他地点（要多走一段路）：{known}"
        )

    def bearings(self, state: GameState) -> str:
        """The scene as the action parser gets it, no more than the players know: what they see
        here, who is here, and the places they could head for (engine/fog.py), by the names
        they know. The parser's summary and first beat go up on the player's screen, so a name
        it was never given cannot leak there, and a secret place has no id to be guessed into.
        Not the place's list of things to search either: some of those are the keeper's."""
        m = self.module
        loc = m.location(state.location)
        here = m.npcs_at(loc.id)
        met = {n.id for n in here if n.id in state.npcs and state.npcs[n.id].met}
        people = "、".join(f"{n.name}({n.id})" for n in here if n.id in met) or "无"
        if strangers := [f"{n.name}({n.id})" for n in here if n.id not in met]:
            people += f"；还没通名：{'、'.join(strangers)}（summary 里照玩家的叫法称呼他们）"
        known = {**fog.landmarks(m, state), **fog.known(m, state)}

        def way(pid: str, name: str) -> str:  # other names only for places they already know
            more = [a for a in m.location(pid).aliases if pid in known and fog.plain(a) != fog.plain(name)]
            return f"{name}({pid}{'，也叫' + '、'.join(more) if more else ''})"

        ways = "、".join(way(pid, name) for pid, name in fog.bearings(m, state).items()) or "无"
        # The study is part of the house: those met who are in another room of this building,
        # where the party knows the way, by the room's id so "去问托马斯" can go there.
        near = {n: at for n, at in (world_view.nearby(self.world, loc.id) if self.world is not None else {}).items()
                if state.npcs.get(n) and state.npcs[n].met and fog.findable(m, state, at)}  # fmt: skip
        if near:
            ways += "\n同一处的别的房间里：" + "、".join(f"{m.npc(n).name}({n}) 在 {m.location(at).name}({at})" for n, at in near.items())
        last_seen = []
        for n in m.npcs:
            if n in here or n.id in near or not (state.npcs.get(n.id) and state.npcs[n.id].met):
                continue
            seen = next((e for e in reversed(state.record.events) if e.visibility == "public"
                         and n.id in [e.actor, *e.participants] and e.place), None)
            place = next((loc.name for loc in m.locations if seen and loc.id == seen.place), "")
            label = f"，最后亲历记录：{place}，{format_time(seen.t)}" if seen and place else ""
            last_seen.append(f"{n.name}({n.id})不在当前房间{label}；当前位置未获确认")
        left = world_view.apart(state, lambda pid: next((x.name for x in m.locations if x.id == pid), pid))
        return (
            f"地点：{loc.name}({loc.id})；时间：{state.clock}（{time_of_day(state.minutes)}）\n"
            f"眼前：{loc.read_aloud.strip()}\n在场 NPC：{people}\n能去的地方：{ways}\n"
            f"尚未走完的旅程目的地：{state.journey or '无'}\n"
            f"上一段叙述停在：{state.ongoing or '无'}\n"
            f"【已认识的其他人】{'；'.join(last_seen) or '无'}"
            + (f"\n【和大家分开的调查员】{left}（不在这里；大家去到那里、或有人把他带回来，才和大家在一起）" if left else "")
        )

    # -- agents -------------------------------------------------------------------
    def _intent(self, state: GameState) -> Session:
        return self._open("intent", "intent", INTENT_SYSTEM, schema=_schema(Action))

    def _plan(self, state: GameState) -> Session:
        """The keeper's arrangement of each turn: on the keeper's model, in a session of its own (its
        calls count as "plan"), never the narration's, whose memory keeps only what was told."""
        if "plan" not in self.sessions:
            system = PLAN_SYSTEM + ACTION_RULES + ("\n\n" + _book(self.module) if self.backstage else "")
            self.sessions["plan"] = Session("plan", self.cfg.role("plan"), system, schema=_schema(Plan), key="plan",
                                            summarizer=self.summarizer)
        return self.sessions["plan"]

    def _arbiter(self, state: GameState) -> Session:
        if "arbiter" not in self.sessions:
            self._arbiter_flags = set(self.module.flags)  # later ones (improvised) go in each turn's prompt
            self._rulebooks = rules_lookup.ready()  # asked once a session: the schema is the session's
        flags = "\n".join(f"- {k}：{v}" for k, v in self.module.flags.items() if k in self._arbiter_flags)
        system = ARBITER_SYSTEM.format(rules=skills.wrap("coc7-core"), damage=OTHER_DAMAGE,
                                       lookup=ARBITER_LOOKUP if self._rulebooks else "") + f"\n\n剧情标记：\n{flags}"
        schema = _schema(Ruling)
        if self._rulebooks:
            schema["properties"]["lookup"] = LOOKUP_SCHEMA
        return self._open("arbiter", "arbiter", system, schema=schema)

    def _npc(self, state: GameState, npc_id: str) -> Session:
        return self._open(f"npc:{npc_id}", "npc", self._npc_system(self.module.npc(npc_id)), schema=_schema(Dialogue))

    async def warm(self, state: GameState) -> None:
        """Spawn the sessions the next turns will need while the player is still reading."""
        sessions = [self._plan(state), self._arbiter(state), self._keeper(state)]
        sessions += [self._npc(state, n.id) for n in self.module.npcs_at(state.location)]
        await asyncio.gather(*(s.warm() for s in sessions), return_exceptions=True)

    def _heard(self, state: GameState, text: str, *, shared: bool = True) -> str:
        """What the action is read from: the scene as the players know it, the recent exchange, what
        the investigator really holds, and the player's words (with the skill a dice command asks for).
        A batch supplies the scene and recent world record once; each action then adds only its author."""
        inv = state.investigator
        asked = commands.asked(text)
        prompt = (f"【场景】\n{self.bearings(state)}\n【刚才的经过】\n{_recent(state, viewer=inv.id, keeper=False)}\n"
                  if shared else "")
        prompt += f"【调查员】{inv.name}\n【实际持有】{'、'.join(inv.inventory) or '未登记随身物品'}\n【玩家】{text}"
        if shared and state.perceptions and self.world is not None:
            prompt += "\n【主持专用：个人知觉与真实对象】\n" + perception.brief(state)
        if self.world is not None:
            if shared:
                prompt += f"\n【已建立的关系与现场事实】\n{world_view.keeper_view(self.world)}"
            prompt += "\n【重要装备状态】" + "；".join(self._equipment(inv.id))
        if asked is not None:
            prompt += f"（骰子指令：{inv.name}想在这里用「{asked.skill}」）"
        return prompt

    async def intent(self, state: GameState, text: str) -> Action:
        s = self._intent(state)
        reply = await self._send("intent", s, state, self._heard(state, text))
        return Action.model_validate(reply.data).model_copy(update={"echo": ""})

    def able(self, state: GameState) -> list[NPC]:
        """Who here may join in of their own accord (Plan.initiative): the module's people at this
        place, alive and not gone."""
        return [n for n in self.module.npcs_at(state.location) if n.status not in ("死亡", "失踪")]

    async def plan(self, state: GameState, text: str, *, encounter: str | None = None, mode: PlanMode = "act",
                   check: Callable[[Any], list[str]] | None = None, ask: str = "") -> Any:
        """The keeper arranges the turn: the player's action (as the parser would make it) and who of
        those here joins in. It sees the book and the scene's backstage; the summary still goes up
        on the player's screen, so PLAN_SYSTEM keeps it to the names the players know. A reply that
        is not a Plan raises (Game.parse falls back to the parser).

        Inside a fight or a chase (`encounter`: the brief, agents/encounter.encounter_brief) it is
        the encounter's plan instead, by `mode`: "act" an EncounterTurnPlan for the player's words,
        "arrange" the NPCs alone (ArrangePlan), "react" a reaction in the player's own words
        (ReactionAnswer; `ask` lists the reactions offered), "pursuit" who chases whom
        (PursuitAnswer; `ask` names those who must decide). `check` is the engine's deterministic
        validator (engine/encounter/plan_check): what it finds goes back once; a Checked comes back."""
        if encounter is not None:
            return await self._encounter_plan(state, text, encounter, mode, check, ask)
        s = self._plan(state)
        brief = world_view.keeper_brief(self.world) if self.backstage and self.world is not None else ""
        prompt = (brief + "\n\n" if brief else "") + self._heard(state, text)
        prompt += "\n【在场、可以主动的人】" + ("、".join(f"{n.name}({n.id})" for n in self.able(state)) or "无")
        if self.world is not None:
            unplaced = [f"{n.name}({n.id})：{n.role}" for n in self.module.npcs
                        if (e := self.world.entity(n.id)) is not None and not e.at and e.status not in ("死亡", "失踪")]
            if unplaced:
                prompt += "\n【位置尚未登记的人，不代表在场】" + "；".join(unplaced)
        prompt += "\n【可能动手的】" + self._fighters(state)
        reply = await self._send("plan", s, state, prompt)
        plan = Plan.model_validate(reply.data)
        # What it asks to look up is answered and it is asked again, twice at most; a lookup after
        # that is not answered. A model-free loop: one JSON field, no vendor's tool calls (as the
        # NPCs' backstage decisions, world/agents.CastBrain). A call that then fails leaves the plan
        # it had, whose action was already filled in.
        for _asked in range(LOOKUPS):
            query = plan.lookup.strip()
            if not query:
                break
            found = await self.look_up(state, query)
            if journal := getattr(self, "debug_journal", None):
                journal.append("lookup", {"query": query, "found": found}, turn=state.turn)
            prompt += f"\n\n【查询结果】（{query}）\n{found}\n现在给出安排，lookup 留空。"
            try:
                reply = await self._send("plan", s, state, prompt)
                plan = Plan.model_validate(reply.data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if journal:
                    journal.append("lookup", {"status": "failed", "error": type(exc).__name__}, turn=state.turn)
                break
        if check is not None and (problems := check(plan)):
            prompt += ("\n\n【现场登记未落实】\n" + "\n".join(f"- {p}" for p in problems)
                       + "\n按具体原因修正一次安排；无法落实的保留为意图，不改写成已经发生。")
            try:
                reply = await self._send("plan", s, state, prompt)
                plan = Plan.model_validate(reply.data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._journal("plan", {"status": "repair-failed", "error": type(exc).__name__}, state)
        return plan.model_copy(update={"lookup": "", "action": plan.action.model_copy(update={"echo": ""})})

    async def party_plan(self, state: GameState, inputs: list[PartyInput], *,
                         check: Callable[[PartyPlan], list[str]] | None = None) -> PartyPlan | None:
        """Read a collection window together, replacing its separate everyday plan calls.

        A step is tied to its original action token, including when one investigator submitted
        twice. This is an arrangement, not a ruling or public narration. Independent scenes
        retain their own contexts; their processing order does not advance world time twice.
        The caller keeps single-person and encounter windows on their existing paths.
        """
        items = [PartyInput.model_validate(item) for item in inputs]
        if len(items) < 2 or len({item.who for item in items}) < 2 or state.encounter is not None or state.ending:
            return None
        tokens = [item.token for item in items]
        if len(set(tokens)) != len(tokens):
            raise ValueError("同一批行动的编号不能重复")
        indexes = {inv.id: index for index, inv in enumerate(state.investigators)}
        if any(item.who not in indexes or not item.text.strip() or not item.token.strip() for item in items):
            raise ValueError("批行动必须对应已有调查员，并有非空的编号和原话")
        expected = set(tokens)

        def complete(plan: PartyPlan) -> list[str]:
            ordered = [step.token for step in plan.steps]
            problems = []
            if len(ordered) != len(set(ordered)):
                problems.append("steps 中同一个行动编号出现了多次；每条原始行动只能有一份安排。")
            missing, extra = expected - set(ordered), set(ordered) - expected
            if missing:
                problems.append("漏掉原始行动编号：" + "、".join(token for token in tokens if token in missing))
            if extra:
                problems.append("出现未提交的行动编号：" + "、".join(sorted(extra)))
            for who in {item.who for item in items}:
                declared = [item.token for item in items if item.who == who]
                if [token for token in ordered if token in declared] != declared:
                    problems.append(f"{who} 的连续行动必须保留本人声明的先后顺序。")
            for step in plan.steps:
                if any(token not in expected or token == step.token for token in step.related):
                    problems.append(f"{step.token} 的 related 只能引用其他已提交的行动编号。")
            if not problems and check is not None:
                problems.extend(check(plan))
            return problems

        prompt = (
            "【同一收集窗口的整批安排】\n"
            "先读完所有原话，再理解配合、互相阻拦、争夺同一物件和独立行动，不把每人单独的愿望当成共同同意。"
            "这次改用 PartyPlan 格式：steps 的先后就是准备结算的顺序，每个 token 恰好一次，同一人提交两条也不能合并或漏掉。"
            "本人明确先后进行的事保留其声明次序，不因别人的配合而反转。"
            "related 只引用本窗口的其他 token；coordination 写中立的尝试关系、先后理由或尚待前项成立的条件，"
            "仅供后台判断，不作为公共台词或已发生事实。不要把别人的私语、单独感知和幕后真相写进本人的 action.summary。\n"
            "互助只有实际实施后才改变条件；递灯、开门、牵制和抢夺仍按实际归属、位置和检定结果落实。"
            "不能在这里替裁定员给奖励骰、免检、判定输赢，也不能把需要检定的事写成已完成。"
            "各 plan 遵守原有 action、present、people、initiative、encounter、beats 的规则；"
            "同一次现场补记或同一个 NPC 的同一次主动行为只安排一次，不在多条 plan 里重复执行。\n"
            "不同现场保留各自人物、位置、知情与未办完的事。独立现场允许并行主持，列表结算顺序不意味着赶路、搜索等时长相加，"
            "也不让一个现场的人凭空听见另一处的谈话。需补资料时优先用顶层 lookup，仍提交这批行动的完整草案。\n"
        )
        if self.backstage and self.world is not None:
            prompt += "\n【本局幕后简报】\n" + world_view.keeper_brief(self.world)
        if state.perceptions:
            prompt += "\n【主持专用：个人知觉与真实对象】\n" + perception.brief(state)
        prompt += "\n【全局及各参与者近期记录，私密内容保留收件范围】\n" + _recent(
            state, viewers=tuple(dict.fromkeys(item.who for item in items)))
        places = list(dict.fromkeys(audience.location(state, item.who) for item in items))
        for where in places:
            view = state.model_copy(update={"active": -1, "location": where})
            prompt += f"\n\n【共用场景 {where}】\n" + self.bearings(view)
            if self.world is not None:
                prompt += "\n【已建立的关系与现场事实】\n" + world_view.keeper_view(self.world, at=where)
            prompt += "\n【在场、可以主动的人】" + ("、".join(f"{npc.name}({npc.id})" for npc in self.able(view)) or "无")
            prompt += "\n【可能动手的】" + self._fighters(view)
        for item in items:
            view = state.model_copy(update={"active": indexes[item.who], "location": audience.location(state, item.who)})
            loc = self.module.location(view.location)
            prompt += (f"\n\n【原始行动 token={item.token}，调查员 id={item.who}】\n"
                       f"地点：{loc.name}({loc.id})，使用【共用场景 {loc.id}】。\n" + self._heard(view, item.text, shared=False))
        session = self._plan(state)
        try:
            checked = await self._checked("plan", session, state, prompt, PartyPlan, complete, what="party-plan")
            if not checked.ok or checked.value is None:
                return None
            planned = checked.value
            for _asked in range(LOOKUPS):
                query = planned.lookup.strip() or next((step.plan.lookup.strip() for step in planned.steps if step.plan.lookup.strip()), "")
                if not query:
                    break
                found = await self.look_up(state, query)
                self._journal("lookup", {"query": query, "found": found, "scope": "party"}, state)
                prompt += f"\n\n【查询结果】（{query}）\n{found}\n现在给出完整整批安排，lookup 留空。"
                checked = await self._checked("plan", session, state, prompt, PartyPlan, complete, what="party-plan")
                if not checked.ok or checked.value is None:
                    return None
                planned = checked.value
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._journal("party-plan", {"phase": "fallback", "error": f"{type(exc).__name__}: {exc}"[:300]}, state)
            return None
        return planned.model_copy(update={"lookup": "", "steps": [step.model_copy(update={
            "plan": step.plan.model_copy(update={"lookup": "", "action": step.plan.action.model_copy(update={"echo": ""})})
        }) for step in planned.steps]})

    def _fighters(self, state: GameState) -> str:
        """Who may fight or be chased here, by id (Plan.encounter)."""
        able = [f"{n.name}({n.id})" for n in self.able(state)]
        able += [f"{c.name}({c.id})：{c.description[:40]}" for c in self.module.creatures]
        return ("；".join(able) or "无") + "（怪物只在本地资料或剧情写明它此刻在场时才算在场）"

    # -- fights and chases: the keeper's plan (agents/encounter.py) ---------------------------------
    def _journal(self, kind: str, data: dict, state: GameState) -> None:
        """Private evidence for developer mode (Game.debug_journal); never the players'."""
        if journal := getattr(self, "debug_journal", None):
            journal.append(kind, data, turn=state.turn)

    def _session(self, key: str, system: str) -> Session:
        """A stateless session on the keeper's plan role (its calls count as "plan"), with the book."""
        if key not in self.sessions:
            book = "\n\n" + _book(self.module) if self.backstage else ""
            self.sessions[key] = Session("plan", self.cfg.role("plan"), system + book, key=key, summarizer=self.summarizer)
        return self.sessions[key]

    @staticmethod
    def _judge[T: BaseModel](model: type[T], data: object,
                             check: Callable[[Any], list[str]] | None) -> tuple[T | None, list[str]]:
        """The answer as `model`, and what is wrong with it: its shape first, then the engine's check."""
        try:
            value = model.model_validate(data if isinstance(data, dict) else {})
        except ValidationError as exc:
            return None, [f"格式不对：{'.'.join(str(x) for x in e['loc']) or '整体'} {e['msg']}" for e in exc.errors()[:6]]
        return value, [str(p) for p in (check(value) if check is not None else []) if str(p).strip()]

    async def _checked[T: BaseModel](self, key: str, s: Session, state: GameState, prompt: str, model: type[T],
                                     check: Callable[[Any], list[str]] | None, *, schema: dict | None = None,
                                     reply: Reply | None = None, what: str = "plan") -> Checked[T]:
        """Ask (unless `reply` already came back), check, and send it back once with the problems.
        Still wrong: the answer and its problems come back for the caller to note as 【未落实】, and
        go to the debug journal whole, so the prompt or the context can be fixed (the user's call,
        2026-10-21: nothing is put in its place)."""
        schema = schema or _schema(model)
        if reply is None:
            reply = await self._send(key, s, state, prompt, schema=schema, bind=False)
        value, problems = self._judge(model, reply.data, check)
        if problems:
            first = reply.data
            fix = ("\n\n【上一份回答的问题】\n" + "\n".join(f"- {p}" for p in problems)
                   + f"\n【上一份回答】{dumps(first)}\n改正这些问题，重交一份完整的回答。")
            reply = await self._send(key, s, state, prompt, extra=fix, schema=schema, bind=False)
            value, problems = self._judge(model, reply.data, check)
            if problems:
                self._journal(what, {"phase": "invalid", "session": key, "answer": reply.data, "problems": problems,
                                     "first": first}, state)
        return Checked(value=value, problems=problems, raw=reply.data)

    async def _encounter_plan(self, state: GameState, text: str, encounter: str, mode: PlanMode,
                              check: Callable[[Any], list[str]] | None, ask: str) -> Checked:
        model: type[BaseModel] = {"act": EncounterTurnPlan, "arrange": ArrangePlan, "react": ReactionAnswer,
                                  "pursuit": PursuitAnswer}[mode]
        s = self._session("plan:encounter", ENCOUNTER_PLAN_SYSTEM)
        world_brief = world_view.keeper_brief(self.world) if self.backstage and self.world is not None else ""
        prompt = (world_brief + "\n\n" if world_brief else "") + encounter
        prompt += f"\n【刚才的经过】\n{_recent(state, 6)}"
        prompt += f"\n【要你做的】{MODE_ASKS[mode]}" + (f"\n{ask}" if ask else "")
        if mode in ("act", "react"):
            prompt += f"\n【玩家】{state.investigator.name}：{text}"
        schema = _schema(model)
        reply = await self._send("plan:encounter", s, state, prompt, schema=schema, bind=False)
        if mode in ("act", "arrange"):  # what it asks to look up first, as the everyday plan (LOOKUPS)
            for _asked in range(LOOKUPS):
                query = reply.data.get("lookup", "") if isinstance(reply.data, dict) else ""
                if not isinstance(query, str) or not query.strip():
                    break
                found = await self.look_up(state, query.strip())
                self._journal("lookup", {"query": query, "found": found}, state)
                prompt += f"\n\n【查询结果】（{query.strip()}）\n{found}\n现在给出安排，lookup 留空。"
                reply = await self._send("plan:encounter", s, state, prompt, schema=schema, bind=False)
        out = await self._checked("plan:encounter", s, state, prompt, model, check, schema=schema, reply=reply)
        if isinstance(out.value, EncounterTurnPlan):
            out.value = out.value.model_copy(update={"lookup": ""})
        return out

    async def plan_encounter(self, state: GameState, text: str, hint: EncounterHint,
                             check: Callable[[Any], list[str]] | None = None, *, brief: str = "") -> Checked:
        """How the encounter the turn's plan announced (Plan.encounter) begins: an EncounterStart.
        One extra call, only on a turn that starts one. `brief`: the engine's view of who may be in
        it (attacks by id, the site there is or is not); without it the module's sheets stand in."""
        s = self._session("plan:start", ENCOUNTER_START_SYSTEM)
        world_brief = world_view.keeper_brief(self.world) if self.backstage and self.world is not None else ""
        prompt = (world_brief + "\n\n" if world_brief else "") + self._heard(state, text)
        mode = {"fight": "打起来", "chase": "追逐"}[hint.mode]
        when = {"now": "这次行动就开始", "on_fail": "检定失败后开始", "on_success": "检定成功后开始"}[hint.trigger]
        prompt += f"\n【要开始的遭遇】{mode}，和 {'、'.join(hint.with_)}，{when}"
        prompt += "\n【可能卷入的人】\n" + (brief or self._candidates(state, hint))
        if flags := {k: v for k, v in self.module.flags.items() if k not in state.flags}:
            prompt += "\n【可用的剧情标记】" + "；".join(f"{k}：{v}" for k, v in flags.items())
        return await self._checked("plan:start", s, state, prompt, EncounterStart, check)

    def _candidates(self, state: GameState, hint: EncounterHint) -> str:
        """Who may be in it, from the module's sheets: the investigators with the attacks an opening
        may name by id (engine/encounter/combatant), the people here and those named, the creatures;
        numbers and attacks as the sheets write them."""
        from ..engine.encounter import combatant

        m = self.module

        def arms(inv: Investigator) -> str:
            v = combatant.view(self.world, inv.id) if self.world is not None else None
            return "；".join(f"{a.id}＝{a.name}（{a.skill}，{a.damage_text}）" for a in v.attacks()) if v is not None else "徒手"

        rows = [f"- `{inv.id}` {inv.name}｜调查员｜生命 {inv.hp}/{inv.hp_max}｜{_skills(inv)}｜身上：{'、'.join(inv.inventory) or '无'}"
                f"｜攻击（with 只能填这些 id）：{arms(inv)}" for inv in state.investigators]
        npcs = {n.id: n for n in self.able(state)} | {n.id: n for n in m.npcs if n.id in hint.with_}
        for n in npcs.values():
            stats = "，".join(f"{k}{v}" for k, v in n.stats.items()) or "普通人的数值"
            rows.append(f"- `{n.id}` {n.name}" + (f"（玩家叫他：{n.called}）" if n.called else "")
                        + f"｜NPC｜{stats}｜攻击：{'；'.join(n.attacks) or '徒手'}｜想要：{n.goal or '无'}")
        for c in m.creatures:
            stats = "，".join(f"{k}{v}" for k, v in c.stats.items())
            rows.append(f"- `{c.id}` {c.name}" + (f"（就是 NPC {c.npc}）" if c.npc else "")
                        + (f"（玩家叫它：{c.called}）" if c.called else "") + f"｜怪物｜{stats}｜攻击：{'；'.join(c.attacks) or '无'}"
                        + ("" if c.pursues is None else "｜会追逃跑的人" if c.pursues else "｜不追逃跑的人")
                        + (f"｜{c.notes}" if c.notes else ""))
        return "\n".join(rows) + "\n（怪物只在本地资料或剧情写明它此刻在场时才算在场）"

    async def encounter_choice(self, state: GameState, inv_id: str, situation: str, options: list[Any],
                               stances: list[str] | None = None) -> Checked:
        """An AI investigator's pick in a fight or a chase, on the investigator role: `situation` is
        the public view only (engine/encounter/views), `options` the offered choices (anything with
        `id` and `label`, or (id, label) pairs); in a fight `stances`, the answers to blows it may set
        (choices.presets), in the same answer (Choice.react). The schema's enums are their ids. The
        value is the Choice (its react "" outside a fight)."""
        pairs = [(o.id, o.label) if hasattr(o, "id") else (str(o[0]), str(o[1])) for o in options]
        ids = [i for i, _ in pairs]
        inv = state.find(inv_id)
        key = f"choice:{inv_id}"
        if key not in self.sessions:
            system = CHOICE_SYSTEM.format(name=inv.name, occupation=inv.occupation, sheet=_skills(inv))
            self.sessions[key] = Session("investigator", self.cfg.role("investigator"), system, key=key)
        schema = _schema(Choice)
        schema["properties"]["choice"]["enum"] = ids
        prompt = f"{situation}\n【可选】\n" + "\n".join(f"- {i}：{label}" for i, label in pairs)
        if stances:
            schema["properties"]["react"]["enum"] = ["", *stances]
            prompt += "\n【被攻击时的应对（react）】" + "、".join(f"{x}（{REACT_WORDS[x]}）" for x in stances)
        else:
            schema["properties"].pop("react", None)
            schema["required"] = [x for x in schema.get("required", []) if x != "react"]

        def check(answer: Choice) -> list[str]:
            out = [] if answer.choice in ids else [f"{answer.choice} 不是列出的选项；只能填：{'、'.join(ids)}"]
            if stances and answer.react and answer.react not in stances:
                out.append(f"react 填的 {answer.react} 在这场战斗中用不了；只能填：{'、'.join(stances)}，或留空")
            return out

        out = await self._checked(key, self.sessions[key], state, prompt, Choice, check, schema=schema, what="choice")
        value = out.value.model_copy(update={"react": ""}) if out.value is not None and not stances else out.value
        return Checked(value=value, problems=out.problems, raw=out.raw)

    async def look_up(self, state: GameState, query: str) -> str:
        """What the keeper asks before it arranges the turn (Plan.lookup), never shown to the players:
        those it names, where they are now and what of the query they know and went through
        (world/context.npc_brief); the record's facts and events that match (world/index.Recall, the
        module's seeded text with this game's rewrites in it); the module's source when it has an
        index (_private_lore, with what was rewritten)."""
        m, w = self.module, self.world
        rows: list[str] = []
        named = [n for n in m.npcs if n.id in query or any(len(part) > 1 and part in query
                                                           for part in [n.name, *n.name.replace("·", " ").split()])][:2]
        names = [n.name for n in named]
        if w is not None:
            from ..world.index import Recall

            for n in named:
                place, ent = w.place(n.id), w.rec.entities.get(n.id)
                where = w.name(place) if place else "不明"
                rows.append(f"【{n.name}】此刻在{where}" + (f"，{ent.status}" if ent is not None and ent.status else ""))
                if brief := world_view.npc_brief(w, n.id, query=query):
                    rows.append(f"（下面是{n.name}自己的资料，“你”指{n.name}）\n{brief}")
            hits = await Recall().search(w, query, k=LOOKUP_HITS)
            if hits:
                rows.append("【记录里相关的】\n" + "\n".join(f"- {h.text.strip()}" for h in hits))
        if lore := await self._private_lore(state, query, names):
            rows.append(lore.strip())
        if passages := await self._rules(state, query, k=2):
            rows.append("【规则书】\n" + rules_lookup.quote(passages))
        return "\n".join(rows) or "没有查到相关的记录，按手头的资料安排。"

    async def _rules(self, state: GameState, query: str, k: int = rules_lookup.HITS) -> list[rules_lookup.Passage]:
        """The rulebooks' passages on `query` (rules_lookup.py), journaled; none without an index."""
        if not query.strip() or not rules_lookup.ready():
            return []
        try:
            found = await rules_lookup.alookup(query, k)
        except Exception as exc:  # a damaged index is a missing one
            self._journal("rules-lookup", {"query": query, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]},
                          state)
            return []
        self._journal("rules-lookup", {"query": query, "found": [p.cite() for p in found]}, state)
        return found

    async def arbiter(self, state: GameState, action: Action, at: str | None = None, *, encounter: str = "",
                      problems: list[str] | None = None, coordination: str = "") -> Ruling:
        """`at`: the place the investigator walks to for this action; it is ruled there.

        In a fight or a chase, `encounter` is its brief (agents/encounter.encounter_brief): the
        arbiter rules the one `other` action or unusual way past an obstacle, with its results in
        Ruling.effects. `problems`: what the engine found wrong with its last ruling of this action
        (a target not in the encounter), sent back once. A rule it asks for (Ruling.lookup) is
        looked up in the rulebooks and it is asked again, once."""
        m = self.module
        s = self._arbiter(state)
        inv = state.investigator
        where = at or state.location
        # Who is there and what is to be had with them, as the world record has it (2026-10-22: the
        # clue about the truck's driver was listed at an empty cab, and given).
        near = module_graph.neighbourhood(m, where, self.present(where))
        here = {n.id: f"{n.name}({n.id})" for n in near["npcs"]}
        lines = []
        for c in near["clues"]:
            if c.id in state.clues or not c.available(state.flags, state.clues):
                continue
            need = f"{c.check.skill}·{c.check.difficulty}" if c.check else "无需检定"
            tell = "、".join(here[x] for x in module_graph.tellers(m, c) if x in here and x != c.npc)
            if c.npc in here:  # what they say, or what is seen of them
                src = f"出自 {here[c.npc]}" + (f"（{c.feature}）" if c.feature else "")
            elif c.npc or c.where != where:  # its person or its place is elsewhere: someone here can tell it
                src = f"{tell} 能说出"
            else:
                src = f"在 {c.feature or '此处'}" + (f"，{tell} 也能说出" if tell else "")
            lines.append(f"- {c.id}「{c.title}」{'【关键】' if c.core else ''}{src}，{need}；内容：{c.text.strip()}")
        gates = []
        for n in near["npcs"]:
            for cid in n.clues:
                if cid in state.clues or not m.clue(cid).available(state.flags, state.clues):
                    continue
                g = n.gates.get(cid)
                gates.append(f"- {n.id} 的 {cid}：{f'{g.skill}·{g.difficulty}' if g else '问到就说'}")
        top = sorted(inv.skills.items(), key=lambda kv: -kv[1])
        sheet = "，".join(f"{k}{v}" for k, v in top) + "；" + "，".join(f"{k}{v}" for k, v in inv.characteristics.items())
        held = "、".join(m.clue(c).title for c in state.clues) or "无"
        moved = f"（调查员为此先走到了{m.location(at).name}，下面是那里的场景）\n" if at else ""
        local = [f"{i.id} {i.name}，持有者 {world.owner(m, state, i.id)}" for i in m.items if i.where == where]
        if self.world is not None:  # everyday things registered as they came up (world/ops.Bring), here or on the people here
            around = {where} | {e.id for e in self.world.rec.entities.values() if e.kind == "npc" and e.at == where}
            local += [f"{e.id} {e.name}，持有者 {e.at}" for e in self.world.rec.entities.values()
                      if e.kind == "item" and not e.important and e.at in around]
        prompt = (
            f"【行动】{action.ruled()}\n{moved}"
            + (coordination + "\n" if coordination else "") +
            f"【近期实际经过】\n{_recent(state, kinds=SEEN_BY_ARBITER, viewer=inv.id, keeper=False)}\n"
            f"【场景】\n{self.scene(state, at)}\n"
            f"【本地守秘人资料】\n{m.location(where).keeper.strip() or '无'}\n"
            f"【本场景可得线索】\n{chr(10).join(lines) or '（没有了）'}\n"
            f"【NPC 门槛】\n{chr(10).join(gates) or '无'}\n"
            f"【调查员】{inv.name}：{sheet}\n背包：{'、'.join(inv.inventory) or '无'}\n"
            f"【本地物件】" + "；".join(local) + "\n"
            f"{world.context(state)}\n【已有线索】{held}\n【当前标记】{'、'.join(state.flags) or '无'}"
        )
        if action.npc and any(n.id == action.npc for n in m.npcs):  # the difficulty against someone comes from them (p. 72)
            prompt += f"\n【对方的相应数值】{opposing(m.npc(action.npc))}"
        prompt += "\n【本地人物的处境】\n" + ("\n".join(
            f"- {n.name}({n.id})：{n.personality}；想要：{n.goal}" for n in near["npcs"]
        ) or "这里没有别人")
        prompt += "\n【调查员重要装备状态】" + ("；".join(self._equipment(inv.id)) or "尚无单独登记")
        if self.world is not None and not encounter and (ways := chase_site.ways_at(m, self.world.rec.obstacles, where)):
            prompt += ("\n【本局改变过的通路】" + "；".join(ways)
                       + "（这次行动修好、关上、堵上或清走了它，成功后在 effects 里写一条 obstacle，target 填括号里的 id）")
        if left := world_view.apart(state, lambda pid: next((x.name for x in m.locations if x.id == pid), pid)):
            prompt += f"\n【和大家分开的调查员】{left}（不在大家身边；这次行动把谁带回来了，写进 rejoin）"
        prompt += cards.arbiter_context(state, inv)  # the imported keeper decks: what holds on them, mishaps to deal
        named = next((n.name for n in m.npcs if n.id == action.npc), "")
        prompt += await self._private_lore(state, action.summary, [m.location(where).name, action.target, named])
        if action.kind == "perform" and action.speech:
            prompt += "\n【表演内容】使用上面行动里的 speech 原文判断是否值得一个奖励骰，不改变难度。"
        later = {k: v for k, v in m.flags.items() if k not in self._arbiter_flags and not k.startswith("now_")}
        if later:
            prompt += "\n【本局新增的剧情标记】" + "；".join(f"{k}：{v}" for k, v in later.items())
        if checks := self._recent_checks(state):
            prompt += f"\n【最近的检定】\n{checks}"
        if encounter:
            prompt += f"\n{ARBITER_ENCOUNTER}\n{encounter}"
        extensions = [f"{p.id}.{a.name}：{a.description}" for p in m.extensions.values()
                      if p.id not in state.plugin_errors for a in p.actions]
        if extensions:
            prompt += "\n【可调用的扩展动作】\n" + "\n".join(extensions)
            prompt += "\n仅按玩家的实际行动填写 plugin_calls，不猜测插件结果；参数范围遵守上述说明。"
        # Memory keeps what was asked, not the whole scene sheet: the sheet is rebuilt every turn.
        remember = f"【行动】{action.ruled()}\n【地点】{m.location(where).name}"
        extra = ("\n\n【上一次裁定的问题，请改正】\n" + "\n".join(f"- {p}" for p in problems)) if problems else ""
        reply = await self._send("arbiter", s, state, prompt, remember=remember, extra=extra)
        ruling = Ruling.model_validate(reply.data)  # invalid rulings are retryable failures, never silent success
        if self._rulebooks and ruling.lookup.strip():
            if found := await self._rules(state, ruling.lookup.strip()):
                text = rules_lookup.quote(found)
            else:
                text = "规则书里没有查到，按手头的规则裁定。"
            s.forget()  # the ruling it gives knowing the rule takes the place of the one that asked
            extra += f"\n\n【规则书查询】（{ruling.lookup.strip()}）\n{text}\n现在给出裁定，lookup 留空。"
            reply = await self._send("arbiter", s, state, prompt, remember=remember, extra=extra)
            ruling = Ruling.model_validate(reply.data)
        return ruling.model_copy(update={"lookup": ""})

    async def repush(self, state: GameState, pending: dict, how: str) -> PushRuling:
        """A push the player said how to make, ruled again in one call (Game._method): whether the goal
        can still be reached this way, the skill and difficulty, the time it takes, and what failing it
        this way brings. Stateless, on the arbiter's model."""
        m, inv = self.module, state.investigator
        s = self._oneshot("repush", "arbiter", PUSH_SYSTEM, PushRuling)
        flags = "；".join(f"{k}：{v}" for k, v in m.flags.items() if k not in state.flags) or "无"
        prompt = (
            f"【原来的行动】{pending.get('action') or '（未记录）'}\n"
            f"【失败的检定】{pending['skill']} {pending['target']}·{pending['difficulty']}，掷出 {pending.get('value', '?')}\n"
            f"【一般后果】{pending.get('risk') or '未写'}\n"
            f"【孤注一掷的做法】{how}\n"
            f"【场景】\n{self.scene(state)}\n"
            f"【本地守秘人资料】\n{m.location(state.location).keeper.strip() or '无'}\n"
            f"【近期实际经过】\n{_recent(state, kinds=SEEN_BY_ARBITER, viewer=inv.id, keeper=False)}\n"
            f"【调查员】{inv.name}：{_skills(inv)}" + (f"；状态：{'、'.join(inv.conditions)}" if inv.conditions else "") + "\n"
            f"【可设的剧情标记】{flags}"
        )
        prompt += cards.arbiter_context(state, inv)
        reply = await self._send("repush", s, state, prompt, bind=False)
        return PushRuling.model_validate(reply.data)

    def _recent_checks(self, state: GameState) -> str:
        """The last few turns' checks, for telling a fresh attempt from a re-roll."""
        m = self.module
        npcs = {n.id: n.name for n in m.npcs}
        people = {i.id: i.name for i in state.investigators}
        places = {loc.id: loc.name for loc in m.locations}
        rows = []
        for r in state.recent_checks():
            ago = "刚才" if state.turn - r.turn <= 1 else f"{state.turn - r.turn} 轮前"
            aim = f"{npcs[r.npc]}({r.npc})" if r.npc in npcs else r.target or "（没有具体对象）"
            when = f"{format_time(r.minutes)}，{places.get(r.at, r.at)}"
            rows.append(f"- {ago}（{when}）{people.get(r.who, r.who)}对{aim}：{r.text}")
        return "\n".join(rows)

    def _npc_system(self, npc: NPC) -> str:
        m = self.module

        def bullet(items: list[str]) -> str:
            return "\n".join(f"- {x}" for x in items) or "- 无"

        clues = []
        for cid in npc.clues:
            c = m.clue(cid)
            g = npc.gates.get(cid)
            clues.append(f"- {cid}「{c.title}」：{c.text.strip()}（{'门槛：' + g.skill + '·' + g.difficulty if g else '有人问起就可以说'}）")
        extra = ""
        if _storyteller(npc):  # a teahouse storyteller in the module, not anyone the director puts on stage
            extra = "\n你是说书人，表演时用下面的评书腔：\n" + skills.wrap("pingshu-voice")
        learned_knows, learned_secrets = self._learned(npc.id)  # told each call (npc), not part of the sheet
        return NPC_SYSTEM.format(
            name=npc.name,
            era=m.era,
            setting=m.setting,
            role=npc.role,
            look=npc.look or "—",
            personality=npc.personality or "—",
            voice=npc.voice or "—",
            goal=npc.goal or "—",
            knows=bullet([k for k in npc.knows if k not in learned_knows]),
            secrets=bullet([x for x in npc.secrets if x not in learned_secrets]),
            lies=bullet(npc.lies),
            clues="\n".join(clues) or "- 无",
            extra=extra,
        )

    def _learned(self, npc_id: str) -> tuple[list[str], list[str]]:
        """What the NPC came to know in this game, and the secrets it came to keep: the newest few, as
        world/knowledge.project puts them on its sheet. They go with each call (npc), not into the
        system prompt: a prompt that changed opened the session again, every time it learned
        something (a new Claude Code process, and nothing of it cached on any vendor)."""
        if self.world is None:
            return [], []
        facts = self.world.rec.facts.values()
        knows = [f.text for f in facts if f.origin == "game" and npc_id in f.known_by and not f.retracted
                 and f.kind not in ("secret", "lie", "goal") and f.true][-world_knowledge.PROJECTED_KNOWS:]  # fmt: skip
        secrets = [f.text for f in facts if f.origin == "game" and f.kind == "secret" and npc_id in f.known_by
                   and npc_id in f.about and not f.retracted]  # fmt: skip
        return knows, secrets

    def _protected(self, state: GameState, npc: NPC) -> dict[str, str]:
        """Texts this NPC must not reproduce right now."""
        m = self.module
        allowed = set(state.npc(npc.id).unlocked) | {c for c in npc.clues if c not in npc.gates}
        out = {f"clue:{c.id}": c.text for c in m.clues if c.id not in allowed and c.id not in state.clues}
        for other in m.npcs:
            if other.id != npc.id:
                out.update({f"secret:{other.id}:{i}": s for i, s in enumerate(other.secrets)})
        out["truth"] = m.truth
        return out

    async def _fresh_npc(self, npc_id: str) -> Session:
        """The NPC's session, opened again when its sheet changed under it: the world record
        projects what it learned, and a rewrite, into the module copy (world/knowledge.project),
        and a session's system prompt is made when it opens. The conversation stays with it."""
        key = f"npc:{npc_id}"
        system = self._npc_system(self.module.npc(npc_id))
        if (old := self.sessions.get(key)) is not None and old.system != system:
            await old.close()
            del self.sessions[key]
        return self._open(key, "npc", system, schema=_schema(Dialogue))

    @staticmethod
    def claimed_accompany(state: GameState, npc_id: str, destination: str) -> bool:
        """The latest reply publicly promised company without an actual Follow agreement.

        Only the private engine uses this receipt. It grants neither movement nor
        player knowledge of the character's sincerity.
        """
        prefix = f"claimed_accompany:{state.turn}:{npc_id}:{state.npc(npc_id).lines}:"
        return any(f.id.startswith(prefix) and f.id.endswith(f":{destination}") and f.kind == "lie"
                   and not f.true and not f.retracted and npc_id in f.known_by for f in state.record.facts.values())

    @staticmethod
    def _deception_ops(state: GameState, npc_id: str, dialogue: Dialogue) -> list[AddFact]:
        ops = []
        line = state.npc(npc_id).lines + 1
        for index, deception in enumerate(dialogue.deceptions):
            key = f"deception:{state.turn}:{npc_id}:{line}:{index}"
            claim = f"对调查员声称：「{deception.claim}」"
            common = {"actor": npc_id, "about": [npc_id], "known_by": [npc_id], "visibility": "private"}
            ops.append(AddFact(id=key, fact=key, text=claim, fact_kind="lie", true=False, **common))
            ops.append(AddFact(id=f"{key}:intent", fact=f"{key}:intent", text=f"这次声称的真实意图：{deception.intent}",
                               fact_kind="secret", **common))
            ops.append(AddFact(id=f"{key}:motive", fact=f"{key}:motive", text=f"这次声称的具体动机：{deception.motive}",
                               fact_kind="secret", **common))
            if deception.basis:
                ops.append(AddFact(id=f"{key}:basis", fact=f"{key}:basis", text="这次声称所依据的已知资料：" + "、".join(deception.basis),
                                   fact_kind="secret", **common))
            if deception.accompany:
                receipt = f"claimed_accompany:{state.turn}:{npc_id}:{line}:{index}:{deception.accompany}"
                ops.append(AddFact(id=receipt, fact=receipt, text=claim, fact_kind="lie", true=False, **common))
        return ops

    def _dialogue_problems(self, state: GameState, npc_id: str, dialogue: Dialogue, text: str,
                           destinations: dict[str, str], *, private: bool) -> list[str]:
        """Check the full reply before its actions become visible or mutate the live save."""
        if self.world is None:
            return []
        from ..world.store import World

        trial = World(self.module.model_copy(deep=True), state.model_copy(deep=True))
        problems = []
        known = "\n".join(f.text for f in world_knowledge.known(trial.rec, npc_id))
        known += "\n" + world_view.npc_brief(trial, npc_id, query=text)
        for deception in dialogue.deceptions:
            if not all(part.strip() for part in (deception.claim, deception.intent, deception.motive)):
                problems.append("有意欺骗必须明确台词、真实意图与具体动机")
            if deception.claim not in text:
                problems.append("欺骗说明必须逐字对应本次实际台词")
            if deception.accompany and deception.accompany not in destinations:
                problems.append("假答应同行的目的地不在本轮已知地点中")
            if deception.accompany and dialogue.accompany:
                problems.append("假答应同行不能同时提交实际 accompany，必须区分公开声称与真实行动")
            if any(world_knowledge.resolve_ref(trial, npc_id, ref) is None for ref in deception.basis):
                problems.append("欺骗依据引用了角色不知道的资料")
            if guard.leaks(deception.intent + "\n" + deception.motive, self._protected(state, self.module.npc(npc_id)), public=known):
                problems.append("真实意图或动机使用了角色未知的幕后信息")
        for change in dialogue.changes:
            if change.kind != "promise" and any(deception.claim in change.text or change.text in deception.claim
                                                for deception in dialogue.deceptions):
                problems.append("不实声称只能登记为说过的话，不能作为真实事实、关系、交物或已履行结果")
                continue
            if not world.admissible(trial.m, trial.st, change, source=npc_id, spoken=text):
                problems.append(f"{change.key}：引用、参与者或物件持有状态不符，不能宣称已经完成")
                continue
            results = adapters.settle_changes(trial, [change], source=npc_id, spoken=text, private=private)
            problems.extend(problem for result in results if not result.ok for problem in result.problems)
        if dialogue.accompany:
            if dialogue.accompany not in destinations:
                problems.append("同行目的地不在已知地点中")
            elif text != dialogue.text.strip():
                problems.append("回复被信息权限检查修正，不能沿用原先的同行结论")
            else:
                result = trial.apply(Follow(id=f"follow:{state.turn}:{npc_id}", actor=npc_id, npc=npc_id,
                                            leader=state.investigator.id, destination=dialogue.accompany,
                                            visibility="private" if private else "auto"))
                problems.extend(result.problems)
        for op in self._deception_ops(state, npc_id, dialogue):
            result = trial.apply(op)
            problems.extend(result.problems)
        if intention_ops := self._intention_ops(trial, npc_id, dialogue):
            from ..world.tools import Gateway

            receipt = Gateway(trial, npc_id).settle(intention_ops, request_id="reply-intention-preview")
            problems += [] if receipt.get("ok") else receipt.get("problems") or ["新的目标或计划未通过校验"]
        groups, refused = self._acts(trial, npc_id, dialogue, private=private)
        problems += refused
        if groups and not refused:  # tried whole through its own gateway, as they will land
            from ..world.tools import Gateway

            receipt = Gateway(trial, npc_id).preview([op for group in groups for op in group])
            problems += [] if receipt.get("ok") else receipt.get("problems") or ["当场的动作没有通过校验"]
        return problems

    @staticmethod
    def _intention_ops(w: World, npc_id: str, dialogue: Dialogue) -> list[dict]:
        change = dialogue.intentions
        if change is None or (change.goals is None and change.steps is None):
            return []
        ops = []
        if w.agent(npc_id) is None:
            # A persistent notebook does not allocate an autonomous model worker.
            ops.append(Enlist(npc=npc_id, passive=True, reason=change.reason).model_dump(mode="json"))
        ops.append(SetPlan(npc=npc_id, steps=change.steps, goals=change.goals, reason=change.reason,
                           visibility="private").model_dump(mode="json"))
        return ops

    @staticmethod
    def _acts(w: World, npc_id: str, dialogue: Dialogue, *, private: bool) -> tuple[list[list[dict]], list[str]]:
        """What the reply does on the spot (world/adapters.reply_ops): things taken out or handed over, going off."""
        return adapters.reply_ops(w, npc_id, leave=dialogue.leave, items=dialogue.items, accompany=dialogue.accompany,
                                  visibility="private" if private else "auto", played=dialogue.played)

    def _act(self, state: GameState, npc_id: str, dialogue: Dialogue, *, private: bool) -> list[str]:
        """Land the reply's on-the-spot acts in this turn through the NPC's own gateway, each act whole
        (a thing brought out and handed over together), what can land landing; what could not, in words."""
        from ..world.tools import Gateway

        assert self.world is not None
        groups, failed = self._acts(self.world, npc_id, dialogue, private=private)
        for index, group in enumerate(groups):
            receipt = Gateway(self.world, npc_id).settle(
                group, request_id=f"reply:{state.turn}:{npc_id}:{state.npc(npc_id).lines}:{index}")
            failed += [] if receipt.get("ok") else receipt.get("problems") or ["当场的动作没有通过校验"]
        return failed

    async def npc(self, state: GameState, npc_id: str, message: str, *, travel: bool = False, private: bool = False) -> str:
        npc = self.module.npc(npc_id)
        key = f"npc:{npc_id}"
        # What an earlier reply handed over and nobody took is not this one's (Game._reply takes it at once).
        self.unsettled.pop(npc_id, None)
        self.told.pop(npc_id, None)
        self.media.pop(npc_id, None)
        s = await self._fresh_npc(npc_id)
        mood = (f"\n【先前人物走向】{npc.arc}\n这是此前的基线；最新心境及本轮亲历、可信消息优先，可自然改变反应，不须始终维持这一状态。"
                if npc.arc else "")
        equipment_names = {"carried": "携带", "worn": "穿戴", "held": "手持", "concealed": "藏匿"}
        items = (world_knowledge.visible_items(self.world, npc_id, holder=npc_id) if self.world is not None else
                 [item for item in self.module.items if item.important and world.owner(self.module, state, item.id) == npc_id])
        held = [f"{item.id}（{item.name}，{equipment_names[item.equipment]}，数量{item.quantity}"
                + (f"，{item.status}" if item.status else "") + "）" for item in items]
        mood += f"\n你实际持有的重要物品：{'、'.join(held) or '未登记'}。数量为0的物品已经耗尽，不能使用或交付。\n"
        if self.world is not None and (things := [e.name for e in self.world.rec.entities.values()
                                                  if e.kind == "item" and not e.important and e.at == npc_id]):
            mood += f"你手边的日常物件：{'、'.join(things)}。\n"
        mood += world.context(state, npc=npc_id)
        if refs := presentation.npc_refs(self.module, state, npc_id):
            mood += "\n【可当场展示的材料引用】" + "、".join(sorted(refs)) + "。只有你这次确实展示时才填media，普通对话留空。"
        knows, secrets = self._learned(npc_id)
        if knows:
            mood += "\n【你在本局新知道的】" + "；".join(knows)
        if secrets:
            mood += "\n【你在本局新有的秘密】结合当前动机、关系、处境及本轮裁定决定是否透露：" + "；".join(secrets)
        if self.world is not None:
            # These exact facts already have their full text in the role's own sheet or in
            # the dynamic block above. Keep their scoped references and source labels in
            # the world brief, without sending a second copy of the same paragraphs.
            covered = {}
            for cid in npc.clues:
                fact = self.world.rec.facts.get(f"clue:{cid}")
                if fact is not None and fact.text.strip() == self.module.clue(cid).text.strip():
                    covered[fact.id] = f"角色资料的线索 {cid}"
            for fact in world_knowledge.known(self.world.rec, npc_id):
                if fact.kind == "secret" and fact.text in secrets:
                    covered[fact.id] = "【你在本局新有的秘密】"
                elif fact.text in knows:
                    covered[fact.id] = "【你在本局新知道的】"
            if brief := world_view.npc_brief(self.world, npc_id, query=message, covered_facts=covered):
                mood += "\n" + brief
        mood += self.prefs.for_npc()
        destinations = {state.location: self.module.location(state.location).name, **fog.bearings(self.module, state)}
        mood += "\n【可提出同行的地点】" + "、".join(f"{name}（{pid}）" for pid, name in destinations.items())
        output = {"schema": _schema(TravelDialogue)} if travel else {}
        reply = await self._send(key, s, state, message, extra=mood, **output)
        dialogue = Dialogue.model_validate(reply.data) if isinstance(reply.data, dict) else Dialogue(text=reply.text)
        text = dialogue.text.strip()
        # What it says is its own (the user's call, 2026-10-20: no string matching or cutting of a
        # model's words). It knows only its own part (_npc_system); what of it it tells is its call.
        problems = self._dialogue_problems(state, npc_id, dialogue, text, destinations, private=private)
        intentions = {d.model_dump_json() for d in dialogue.deceptions}
        for _attempt in range(2):  # told again, knowing what did not hold; every reply is checked the same way
            if not problems:
                break
            s.forget()
            fix = "\n\n【行动校验未通过】\n" + "\n".join(problems)
            fix += "\n重写本次完整回复，按实际位置、物件与已发生事实处理；嘴上说此刻交出、同行、走开的事要在 items、accompany、leave 里写上，做不到就别这样说；不得保留未能执行的动作或交付，也不能事后编造有意欺骗来解释系统执行失败。"
            reply = await self._send(key, s, state, message, extra=mood + fix, **output)
            dialogue = Dialogue.model_validate(reply.data) if isinstance(reply.data, dict) else Dialogue(text=reply.text)
            text = dialogue.text.strip()
            problems = self._dialogue_problems(state, npc_id, dialogue, text, destinations, private=private)
            if any(d.model_dump_json() not in intentions for d in dialogue.deceptions):
                problems.append("状态修正不能新增原回复没有声明的欺骗意图")
        else:
            if problems:
                # Still not doable: its words stand and what it could not do does not land (only
                # admissible changes settle below, no deceit it did not mean at first); the keeper
                # tells how it really went (Game.talk).
                dialogue = dialogue.model_copy(update={"deceptions": [d for d in dialogue.deceptions if d.model_dump_json() in intentions]})
                self.unsettled[npc_id] = problems
        if self.world is not None:  # no await between the final validation and the commit
            if intention_ops := self._intention_ops(self.world, npc_id, dialogue):
                from ..world.tools import Gateway

                receipt = Gateway(self.world, npc_id).settle(
                    intention_ops, request_id=f"reply-intention:{state.turn}:{npc_id}:{state.npc(npc_id).lines}")
                if not receipt.get("ok"):
                    self.unsettled[npc_id] = list(dict.fromkeys([*self.unsettled.get(npc_id, []), *receipt.get("problems", [])]))
            adapters.settle_changes(self.world, dialogue.changes, source=npc_id, spoken=text, private=private)
            if dialogue.accompany in destinations and text == dialogue.text.strip():
                self.world.apply(Follow(id=f"follow:{state.turn}:{npc_id}", actor=npc_id, npc=npc_id,
                                        leader=state.investigator.id, destination=dialogue.accompany,
                                        visibility="private" if private else "auto"))
            for op in self._deception_ops(state, npc_id, dialogue):
                self.world.apply(op)
            if failed := self._act(state, npc_id, dialogue, private=private):  # said it, so it does it, this turn
                self.unsettled[npc_id] = list(dict.fromkeys([*self.unsettled.get(npc_id, []), *failed]))
            if dialogue.mood and (agent := self.world.agent(npc_id)):
                agent.mood = dialogue.mood
                self.world.touch(agent)
        else:
            world.apply(self.module, state, dialogue.changes, source=npc_id, spoken=text)
        npc_sheet = self.module.npc(npc_id)
        allowed = set(state.npc(npc_id).unlocked) | {c for c in npc_sheet.clues if c not in npc_sheet.gates}
        self.told[npc_id] = [c for c in dict.fromkeys(dialogue.told) if c in allowed and c not in state.clues]
        listeners = audience.listeners(state, state.investigator.id)
        if private:
            listeners = [state.investigator.id]
        refs = presentation.npc_refs(self.module, state, npc_id)
        self.media[npc_id] = [media.model_dump(mode="json") | {
            "to": media.to if media.to is not None else (None if not private and len(listeners) == len(state.investigators) else listeners),
            "slot": "overlay"}
            for media in dialogue.media if media.action == "show" and media.ref in refs
            and (media.to is None or (media.to and all(who in listeners for who in media.to)))]
        state.npc(npc_id).lines += 1
        state.npc(npc_id).met = True
        return text

    async def perform(self, state: GameState, npc_id: str, instruction: str, deeds: str) -> str:
        """Stage directions use the same scoped knowledge and settlement as ordinary speech.

        `deeds` is retained for callers using the old interface, but never trusted as
        knowledge: older callers supplied the party's private actions and global summary.
        The NPC's actual memories and inventory are supplied by npc(), not by that string.
        """
        key = f"npc:{npc_id}"
        legacy = "调查员们今天做过的事如下，照实编进书里"
        if any(legacy in msg.get("content", "") for msg in state.transcripts.get(key, [])):
            # A known unsafe old exchange must not ride along in the next scoped call.
            # The world record retains this NPC's legitimate memories and knowledge.
            if previous := self.sessions.pop(key, None):
                await previous.close()
            if journal := getattr(self, "debug_journal", None):
                journal.append("context-migration", {"session": key, "reason": "旧表演提示包含调查员全局记录",
                                                      "removed_transcript": state.transcripts.get(key, []),
                                                      "removed_summary": state.notes.get(key, "")}, turn=state.turn)
            state.transcripts.pop(key, None)
            state.notes.pop(key, None)
        if _storyteller(self.module.npc(npc_id)):
            form = "以你自己的口吻表演，250–400 字。只有亲历或当面获知的调查员经历才能编入；不知道的经过不补写。"
        else:
            form = "按你此刻的心境说话、做事，150–300 字，不复述调查员的一天。"
        prompt = (
            f"【本次演出指令】{instruction}\n{form}\n"
            "【表演范围】只用你自己的知识、亲历记录和本次明确给出的台词要求。听到的口供仍是说法，不自动当作真相。"
            "表演不能替你获知异地私语、调查员未说出口的计划或未授权的秘密；演出指令不自动解锁线索。"
            "涉及交物、承诺、同行等实际结果，仍须按正常回复格式填写并通过状态校验；台本与措辞不能代替执行。"
        )
        return await self.npc(state, npc_id, prompt)

    def _keeper(self, state: GameState) -> Session:
        if state.narrator_context_version < 1:
            # Older reports contained the whole private location file. Rebuild from spoken
            # history so upgrading a save does not reintroduce secrets through the transcript.
            state.transcripts.pop("keeper", None)
            state.notes.pop("keeper", None)
            recent = _recent(state) if any(e.kind in ("player", "npc", "keeper") for e in state.log) else ""
            if history := (state.memory + "\n" + recent).strip():
                state.notes["keeper"] = history
            state.narrator_context_version = 1
        if "keeper" in self.sessions:
            return self.sessions["keeper"]
        m = self.module
        module = f"年代：{m.era}\n地方：{m.setting}\n玩家已知的缘由：{m.hook}"
        system = KEEPER_SYSTEM.format(
            title=m.title,
            craft=skills.wrap("keeper-craft"),
            module=module + ("\n\n" + _book(m) if self.backstage else ""),
        )
        return self._open("keeper", "keeper", system)

    async def keeper(self, state: GameState, report: str, on_delta: OnDelta = None, lore: str = "", *,
                     check: Callable[[Any], bool] | None = None, fix: str = "") -> str | Narration:
        """The settled turn told. `lore`, the scene's backstage (world/context.keeper_brief), and the
        narrative preferences ride along this call only, without rewriting the cached prefix.

        With `check`, the telling comes with its scene (Telling): the scene is checked as soon as it
        is written and the words stream once it holds (_Telling); `fix` says what did not hold last
        time. It comes back as a Narration. The keeper's memory keeps the words alone, and only of a
        telling whose scene held or that the player already saw: one turned back is not remembered."""
        s = self._keeper(state)
        if question := kp_question(report):
            if found := await self._rules(state, question):  # a /kp question about the rules: the book's words
                lore += ("\n【规则书原文】（玩家问的是规则时，照原文回答并说出书名和页码；不是规则问题就不用管）\n"
                         + rules_lookup.quote(found))
        extra = ("\n\n" + lore.strip() if lore.strip() else "") + self.prefs.for_keeper() + fix
        if check is None:
            reply = await self._send("keeper", s, state, report, extra=extra, on_delta=on_delta)
            return reply.text.strip()
        reader = _Telling(check, on_delta)
        extra += presentation.index(self.module, state)
        reply = await self._send("keeper", s, state, report, extra=extra, on_delta=reader, schema=TELLING)
        data = reply.data if isinstance(reply.data, dict) else {}
        text, scene = data.get("text"), data.get("scene")
        if not isinstance(text, str):  # no telling could be read: plain words stand for it, with no scene
            text, scene = _words(reply.text), None
        told = Narration(text=text.strip(), scene=scene if isinstance(scene, dict) else None, shown=reader.shown,
                         first=reader.first, offer_idea=data.get("offer_idea") is True,
                         asides=_asides(data.get("asides"), state), media=data.get("media") or [],
                         perceptions=data.get("perceptions") or [])
        holds = told.scene is None or check(told.scene)  # the final word, on the scene as it came back
        if told.shown or holds:
            self._kept(s, report, told.text)
        else:
            self._unsaid(s, report)
        return told

    async def private_keeper(self, state: GameState, who: str, text: str) -> dict:
        """One private use of the keeper model; no shared transcript or world action channel."""
        system = ("你是本局守秘人，正在回复一名玩家的私信。只向来信人回应，不把他人的私人见闻泄露给他。"
                  "这是场外沟通，不执行秘密物理行动、检定、移动、交物、伤害或线索结算；玩家提出这类要求时，"
                  "说明尚未结算，需要正式提交行动。可以澄清其本人经历、规则或约定，也可以按情境给其私人提示。"
                  "已发生的事实与个人知情范围以记录为准。后台真相不因玩家询问就全部揭露。"
                  "媒体只能引用索引中已经准备的资源，且只向来信人展示。\n【模组备课】\n" + _book(self.module))
        key = f"private_keeper:{who}"
        s = self._oneshot(key, "keeper", system, PrivateTelling)
        recent = "\n".join(f"{e.who or e.kind}：{e.text}" for e in audience.log(state, who)[-12:])
        brief = world_view.keeper_brief(self.world) if self.world else ""
        prompt = (f"【来信调查员】{state.find(who).name}（{who}）\n【该玩家近期记录】\n{recent}\n"
                  f"【私信；不是角色行动】{text}\n【所在现场】{self.scene(state, at=audience.location(state, who))}\n{brief}"
                  + presentation.personal_context(state, who)
                  + presentation.index(self.module, state, at=audience.location(state, who)))
        reply = await self._send(key, s, state, prompt, bind=False)
        parsed = PrivateTelling.model_validate(reply.data) if isinstance(reply.data, dict) else PrivateTelling(text=reply.text)
        return parsed.model_dump(mode="json")

    async def idea_failure(self, state: GameState, clue_id: str) -> IdeaFailure:
        """The keeper chooses the failed Idea roll's time and cut, on its usual session. This rare
        rules result has its own schema, not its own model or a call on ordinary turns."""
        clue = self.module.clue(clue_id)
        prompt = ("【灵感检定失败后的安排】规则书第 169 页：时间流逝，调查员陷入能够意识到的最糟局面，"
                  "遗漏的线索仍在其中显露。决定实际经过多久以及从哪个危机切入；这不是普通检定失败送线索。\n"
                  f"【遗漏的线索】{clue.title}：{clue.text.strip()}\n"
                  f"【现在】{state.clock}，{self.module.location(state.location).name}\n"
                  + world.context(state))
        if self.world is not None:
            prompt += "\n" + world_view.keeper_brief(self.world)
        prompt += ("\n只交安排，不对玩家直接叙述。minutes 按实际局势决定；时间推进会触发已有日程，后续以实际记录为准。"
                   "situation 是随后叙述采用的切入点，不是已经执行的事实，不预先宣布伤害、强制移动或交物。"
                   "可以利用已有危险，不为每次失败新增敌人。需要新增设定才填 prepare；已有局势足够就不请导演。")
        reply = await self._send("keeper", self._keeper(state), state, prompt, schema=_schema(IdeaFailure),
                                 extra=self.prefs.for_keeper(), remember="（安排灵感检定失败后的时间与场面）")
        return IdeaFailure.model_validate(reply.data)

    @staticmethod
    def _kept(s: Session, report: str, text: str) -> None:
        """The keeper remembers what it said, not the JSON it was said in."""
        last = s.transcript[-2:]
        if len(last) == 2 and last[0]["content"] == report and last[1]["role"] == "assistant":
            last[1]["content"] = text

    @staticmethod
    def _unsaid(s: Session, report: str) -> None:
        """A telling turned back before anyone saw it: the keeper writes it again knowing why, not
        after it."""
        last = s.transcript[-2:]
        if len(last) == 2 and last[0]["content"] == report and last[1]["role"] == "assistant":
            del s.transcript[-2:]

    async def retell(self, state: GameState, report: str, told: str, *, extra: str = "", on_delta: OnDelta = None) -> str:
        """`told`, the keeper's latest narration of `report`, told again. That exchange leaves the
        keeper's memory first, so the new telling takes its place rather than following it;
        `extra` (that turn's lore, the player's nudge) rides along this call only."""
        s = self._keeper(state)
        s.bind(state.transcripts.setdefault("keeper", []), state.notes)
        await s.settle()  # a fold still running must not move the transcript underneath
        last = s.transcript[-2:]
        if [m["content"].strip() for m in last] != [report.strip(), told.strip()]:
            raise ValueError("守秘人的记忆里，最后一段不是这段叙述")
        s.forget()
        try:
            reply = await self._send("keeper", s, state, report, extra=extra, on_delta=on_delta)
        except BaseException:
            s.transcript += last  # the old telling stands
            raise
        return reply.text.strip()

    async def epilogue(self, state: GameState, prompt: str, on_delta: OnDelta = None) -> str:
        """After the ending, the keeper's word on what became of everyone. Asked on its own
        session, which remembers the game, then dropped from it: it is not part of the story."""
        s = self._keeper(state)
        reply = await self._send("keeper", s, state, prompt, on_delta=on_delta)
        await s.settle()
        s.forget()
        return reply.text.strip()

    async def aftermath(self, state: GameState, report: str, problems: list[str] | None = None) -> Aftermath:
        """The same keeper arranges aid and the next interval, with its full record, only when needed."""
        brief = world_view.keeper_brief(self.world) if self.world is not None else ""
        prompt = AFTERMATH + "\n【模组资料】\n" + _book(self.module)
        prompt += "\n【本局完整幕后简报】\n" + brief + "\n【当前伤势与日程】\n" + report
        if problems:
            prompt += "\n【请修正这些无法落实的安排】\n" + "\n".join(problems)
        reply = await self._send("keeper", self._keeper(state), state, prompt, schema=_schema(Aftermath),
                                 remember="（安排全员倒下后的施救、时间与收尾）")
        return Aftermath.model_validate(reply.data)

    async def chronicle(self, state: GameState, entries: str) -> str:
        s = self._open("chronicler", "chronicler", CHRONICLER_SYSTEM)
        prompt = f"把下面这段游戏记录并进摘要（已有摘要：{state.memory or '无'}）。输出新的完整摘要，不超过 400 字。\n\n{entries}"
        reply = await self._send("chronicler", s, state, prompt)
        return reply.text.strip()

    async def recap(self, state: GameState, on_delta: OnDelta = None) -> str:
        s = self._open("chronicler", "chronicler", CHRONICLER_SYSTEM)
        # What the table was told: the telling that put each clue into the story, not the module's words for it.
        recent = "\n".join(f"[{e.time}] {e.who or e.kind}：{e.text}" for e in audience.log(state)[-30:] if e.kind != "clue")
        prompt = f"写一段前情回顾，150–250 字，只讲调查员已经知道的事，最后落在他们现在的处境上。\n摘要：{state.memory or '无'}\n最近记录：\n{recent}"
        reply = await self._send("chronicler", s, state, prompt, on_delta=on_delta)
        return reply.text.strip()

    async def progress(self, state: GameState, material: str) -> str:
        """The players' 故事至今 (engine/progress.py): stateless, since the last account comes
        with the material, and kept out of the transcripts; its cost is counted."""
        if "progress" not in self.sessions:
            self.sessions["progress"] = Session("progress", self.cfg.role("summarizer"), PROGRESS_SYSTEM, key="progress")
        reply = await self._send("progress", self.sessions["progress"], state, material, bind=False)
        return reply.text.strip()

    async def investigator(self, state: GameState, inv_id: str, situation: str, *,
                           stances: list[str] | None = None) -> Proposal | None:
        """An AI investigator's move, with how it would push it if its roll failed (Proposal.push) and,
        in a fight, how it answers blows from now on (Proposal.react, one of `stances`: the presets a
        player's Tab may set; outside a fight it is dropped). An answer that is no action
        (_proposal_problems) goes back once with what is wrong; still none, it goes to the debug
        journal whole and the seat makes no move this time (None): nothing is made up in its place
        (the user's call, 2026-10-21)."""
        inv = state.find(inv_id)
        key = f"investigator:{inv_id}"
        system = INVESTIGATOR_SYSTEM.format(name=inv.name, occupation=inv.occupation, sheet=_skills(inv), hook=self.module.hook.strip())
        s = self._open(key, "investigator", system, schema=_schema(Proposal))
        situation = _personal_situation(state, inv_id, situation)
        # Remember where and when each move was made; the latest events come with every situation.
        remember = situation.split("\n", 1)[0]
        reply = await self._send(key, s, state, situation, remember=remember)
        check = partial(_proposal_problems, stances=stances)
        said, problems = self._judge(Proposal, reply.data, check)
        if problems:
            first = reply.data
            s.forget()  # an answer that is no move is not one it remembers making
            fix = ("\n\n【上一份回答的问题】\n" + "\n".join(f"- {p}" for p in problems)
                   + f"\n【上一份回答】{dumps(first)}\n改正这些问题，重交一份完整的回答。")
            reply = await self._send(key, s, state, situation, remember=remember, extra=fix)
            said, problems = self._judge(Proposal, reply.data, check)
            if problems:
                s.forget()
                self._journal("investigator", {"phase": "invalid", "session": key, "answer": reply.data, "problems": problems,
                                               "first": first}, state)
                return None
        assert said is not None
        return said.model_copy(update={"text": said.text.strip(), "push": " ".join(said.push.split()),
                                       "react": said.react if stances else ""})

    async def suggest(self, state: GameState, inv_id: str, situation: str) -> list[str]:
        """Ideas for a player's own investigator (F5, /suggest): three actions, written by the
        investigator role from the same view the AI players get. A stateless call kept out of
        the save, since asking for ideas is not a move in the game; only its cost is counted.
        The player's lines (prefs.py) ride along: an idea is something they would say."""
        inv = state.find(inv_id)
        key = f"suggest:{inv_id}"
        if key not in self.sessions:
            system = SUGGEST_SYSTEM.format(name=inv.name, occupation=inv.occupation, sheet=_skills(inv), hook=self.module.hook.strip())
            self.sessions[key] = Session("suggest", self.cfg.role("investigator"), system, schema=_schema(Ideas), key=key)
        lines = "、".join(f"{k}（{LINES[k]}）" for k in self.prefs.lines)
        extra = f"\n（玩家不想碰的题材：{lines}。三个主意都别往这上面引。）" if lines else ""
        # Let the active rule plugins' custom actions surface as ideas when they fit the scene.
        actions = [f"{a.name}：{a.description}" for p in self.module.extensions.values()
                   if p.id not in state.plugin_errors for a in p.actions]
        if actions:
            extra += "\n（本局还有这些可以尝试的额外玩法，合适时可以提其中一个：\n" + "\n".join(f"- {a}" for a in actions) + "）"
        situation = _personal_situation(state, inv_id, situation)
        reply = await self._send(key, self.sessions[key], state, situation, extra=extra, bind=False)
        ideas = _parse(Ideas, reply.data, {"look": "", "ask": "", "dare": ""})
        said = (" ".join(t.split()).strip("“”\"「」") for t in (ideas.look, ideas.ask, ideas.dare))
        return [t for t in dict.fromkeys(said) if t]

    def _oneshot(self, key: str, role: str, system: str, schema: type[BaseModel]) -> Session:
        """A call that remembers nothing: everything it needs is in its prompt."""
        if key not in self.sessions:
            self.sessions[key] = Session(key, self.cfg.role(role), system, schema=_schema(schema), key=key)
        return self.sessions[key]

    @property
    def improvising(self) -> bool:
        return self.cfg.improvising

    async def director(
        self, state: GameState, kind: str, *, action: str = "", reason: str = "", at: str = "", problems: list[str] | None = None
    ) -> Improv | None:
        """A plan for where the module is silent (agents/director.py). Stateless: everything
        it must respect is in the prompt."""
        from ..plugins import api_text

        s = self._open("director", "director", DIRECTOR_SYSTEM + "\n\n" + api_text(), schema=_schema(Improv))
        backstage = world_view.director_view(self.world).text if self.world is not None else ""
        prompt = director_context(self.module, state, kind, action=action, reason=reason, at=at, world=backstage)  # type: ignore[arg-type]
        prompt += presentation.index(self.module, state, at=at)
        if action:
            prompt += await self._private_lore(state, action, [self.module.location(at or state.location).name, action[:80]])
        extra = self.prefs.for_director()  # its new scenes stay out of the player's lines
        extra += ("\n【上一个方案的问题，请改正】\n" + "\n".join(f"- {p}" for p in problems)) if problems else ""
        reply = await self._send("director", s, state, prompt, extra=extra)
        if not isinstance(reply.data, dict):
            return None
        try:
            return Improv.model_validate(reply.data)
        except ValueError:
            return None

    async def review(self, state: GameState, material: str) -> Review | None:
        """The director's look backstage after a long stretch of game time (Game._look): a backstage
        adjustment of its own if one is called for. Stateless, on the director's model; None when
        no look was had."""
        from ..harness.replay import CassetteError

        s = self._open("review", "director", REVIEW_SYSTEM, schema=_schema(Review))
        try:
            reply = await self._send("review", s, state, material, bind=False)
        except CassetteError:  # a replay that drifted must fail loudly
            raise
        except Exception:
            return None
        if not isinstance(reply.data, dict):
            return None
        try:
            return Review.model_validate(reply.data)
        except ValueError:
            return None

    async def author_plugin(self, state: GameState, request: str, problems: list[str] | None = None):
        """Turn a player's one-sentence house rule into one tested plugin (tui/modals/plugins.py, the
        LAN hub). Uses the director's model; stateless, so a retry carries the failures back in
        the prompt rather than in memory. Returns a Plugin or None."""
        from ..plugins import api_text
        from ..plugins.schema import Plugin
        from ..rules.catalog import CHARACTERISTICS

        s = self._open("author", "director", AUTHOR_SYSTEM + "\n\n" + api_text(), schema=_schema(Plugin))
        m = self.module
        ids = "、".join(f"{i.id}（{i.name}）" for i in state.investigators)
        places = "、".join(f"{loc.id}" for loc in m.locations)
        installed = "、".join(p.name for p in m.extensions.values()) or "无"
        npcs = "、".join(f"{n.id}（{n.name}）" for n in m.npcs) or "无"
        lines = "、".join(self.prefs.lines)
        prompt = (
            f"【玩家的家规】{request}\n"
            f"【调查员 id】{ids}\n【基础属性名】{'、'.join(CHARACTERISTICS)}\n"
            f"【地点 id】{places}\n【NPC id】{npcs}\n【已装的规则插件】{installed}\n"
            + (f"【玩家的雷区】{lines}：面板、演出和题目都不碰这些题材。\n" if lines else "")
            + "按上面的 API 写一个插件 JSON（含 code 和 tests）实现这条家规；只实现这一条，别顺带改别的。"
        )
        extra = ("\n【上一次的问题，请改正后重写】\n" + "\n".join(f"- {p}" for p in problems)) if problems else ""
        reply = await self._send("author", s, state, prompt, extra=extra, remember="（撰写家规插件）")
        if not isinstance(reply.data, dict):
            return None
        try:
            return Plugin.model_validate(reply.data)
        except ValueError:
            return None

    async def sketch(self, state: GameState, png: bytes, asked: str) -> Sketch:
        s = self._open("vision", "vision", VISION_SYSTEM, schema=_schema(Sketch))
        prompt = f"玩家被要求画：{asked}\n请描述这张草图，并判断它和要求画的对象有多像。"
        reply = await self._send("vision", s, state, prompt, images=[png])
        return _parse(Sketch, reply.data, {"description": reply.text, "resemblance": 0.0})


# What happened, without what the players typed: their lines reach the arbiter only as the
# parsed action (precautions they declare are kept there). Shown their words, DeepSeek's arbiter
# let 7 of 40 rhetorical attacks soften a ruling (bonus dice for "the mud is still soft, nobody is
# about"); without them 2 of 40, both what the module allows anyway (eval rhetoric, 2026-10-20).
SEEN_BY_ARBITER = ("npc", "keeper", "event", "clue", "handout")


TELLING = _schema(Telling)


def _asides(raw: Any, state: GameState) -> list[dict]:
    """Only an addressed personal observation can leave the keeper's private reply."""
    if not isinstance(raw, list):
        return []
    ids = {i.id for i in state.investigators}
    out = []
    for item in raw:
        try:
            aside = Aside.model_validate(item)
        except ValidationError:
            continue
        if set(aside.to) <= ids and aside.text.strip():
            out.append({"to": list(dict.fromkeys(aside.to)), "text": aside.text.strip()})
    return out


def _words(raw: str) -> str:
    """A reply that should have been a telling, as words: its text field if any of it can be read,
    else the reply itself when it is not JSON at all."""
    raw = raw.strip()
    start = raw.find("{")
    if start >= 0:
        try:
            data = from_json(raw[start:], allow_partial="trailing-strings")
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            return data["text"]
        if isinstance(data, dict) or start == 0:
            raise ValueError("守秘人的回复读不出叙述")
    return raw


def _book(m: Module) -> str:
    """The keeper's own book, all game long: the truth, how to run it, what it comes to. A rewrite
    of any of it reaches the keeper in the scene's backstage (world/context.keeper_brief)."""
    rows = [f"真相：{m.truth.strip()}"]
    if m.keeper_notes.strip():
        rows.append(f"怎么跑：{m.keeper_notes.strip()}")
    if m.history:
        rows.append("前史：" + "；".join(f"{p.when}，{p.text}" for p in m.history))
    if m.conclusions:
        rows.append("结论：" + "；".join(f"{k.id} {k.text}" for k in m.conclusions))
    if m.endings:
        rows.append("结局：" + "；".join(f"{e.title}（{e.condition}）" for e in m.endings))
    # The module's own rules that no plugin keeps (Mechanic.executor): the keeper's to rule in play.
    if mine := [x for x in m.mechanics if x.executor == "守秘人"]:
        rows.append("要你临场执行的规则（没有插件替你算）：\n" + "\n".join(f"- {x.name}：{x.rules.strip()}" for x in mine))
    return "【守秘人的底牌（只给你看）】\n" + "\n".join(rows)


# What a roll against someone is set by (Keeper Rulebook p. 72; the skill entries of chapter 4 say
# which resists which): Persuade and Fast Talk against Psychology or the same skill, Stealth against
# Spot Hidden or Listen, and so on. A characteristic (POW against a spell's pull) only when the sheet
# gives it: an ordinary person's 50 would make every roll against them hard.
OPPOSING = ("心理学", "侦查", "聆听", "说服", "话术", "魅惑", "恐吓")
OPPOSING_CHARS = ("意志", "智力")


def opposing(npc: NPC) -> str:
    """The values a roll against this NPC is set by, with the difficulty each gives (regular under
    50, hard from 50, extreme from 90): the sheet's own, else an ordinary person's skill bases."""
    sheet = stats_of(npc.stats)
    chars = {k: v for k, v in sheet.items() if k in CHARACTERISTICS}
    given, ordinary = [], []
    for key in (*OPPOSING, *(k for k in OPPOSING_CHARS if k in chars)):
        value = sheet[key] if key in sheet else base_value(key, chars)
        (given if key in sheet else ordinary).append(f"{key} {value}（{DIFFICULTY_NAME[opposed_difficulty(value)]}）")
    out = f"{npc.name}({npc.id})：" + ("、".join(given) if given else "模组没写数值")
    return out + (f"；按常人算：{'、'.join(ordinary)}" if ordinary else "")


def _personal_situation(state: GameState, viewer: str, situation: str) -> str:
    """Parallel proposals share a public situation; each seat adds only its own private record."""
    entries = [e for e in audience.personal(state, viewer) if e.text not in situation]
    papers = presentation.personal_context(state, viewer)
    if not entries:
        return situation + ("\n" + papers if papers else "")
    return (situation + "\n【你的私人观察与私语】\n"
            + "\n".join(f"- [{e.time}] {e.who or e.kind}：{e.text}" for e in entries)
            + "\n这些是你亲历、收到的说法或主观感知，不表示其他调查员已经知道，也不自动证实其内容。"
            + ("\n" + papers if papers else ""))


def _recent(state: GameState, n: int = 10, *, kinds: tuple[str, ...] = ("player", "speech", "npc", "keeper", "event", "clue", "handout"),
            viewer: str | None = None, keeper: bool = True, viewers: tuple[str, ...] = ()) -> str:
    """Keep contiguous exchanges and their sources; do not reduce each reply to its last line."""
    rows = [e for e in audience.log(state, viewer, keeper=keeper) if e.kind in kinds]
    names = {i.id: i.name for i in state.investigators}

    def fragments(window):
        selected, left = [], 10000
        for entry in reversed(window):
            if left <= 0:
                break
            text = entry.text
            width = min(len(text), left)
            if len(text) > left:
                text = text[:left // 2] + "\n〔中段省略〕\n" + text[-left // 2:]
            selected.append((entry, text, width))
            left -= len(text)
        return list(reversed(selected))

    selected = fragments(rows[-n:])
    if viewers:
        # Apply each old window's own budget before merging. A busy private exchange
        # cannot consume another participant's allowance. Identity, not equal wording,
        # distinguishes separate utterances; wider original coverage wins on overlaps.
        merged = {id(entry): (text, width) for entry, text, width in selected}
        for who in dict.fromkeys(viewers):
            window = [entry for entry in rows if audience.visible(entry, who)][-n:]
            for entry, text, width in fragments(window):
                previous = merged.get(id(entry))
                if previous is None or width > previous[1]:
                    merged[id(entry)] = (text, width)
        selected, seen = [], set()
        for entry in rows:
            key = id(entry)
            if key in merged and key not in seen:
                text, width = merged[key]
                selected.append((entry, text, width))
                seen.add(key)
    kept = []
    for e, text, _width in selected:
        scope = ""
        if e.audience is not None:
            recipients = "、".join(names.get(who, who) for who in e.audience) or "仅KP"
            scope = f"｜私密收件人：{recipients}" if keeper else "｜个人信息"
        kept.append(f"- [{e.time}｜第{e.turn}轮｜{e.kind}{scope}] {e.who or e.kind}：{text}")
    return "\n".join(kept) or "（刚开场）"


def _skills(inv: Investigator) -> str:
    return "技能：" + "，".join(f"{k}{v}" for k, v in sorted(inv.skills.items(), key=lambda kv: -kv[1]))


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
