"""What a model writes about a fight or a chase: the keeper plan's encounter schemas.

The keeper plan (agents/cast.Cast.plan, the keeper model's `plan` role) is the only model that
arranges an encounter. Outside one it may start one (`EncounterStart`); inside one, every free-text
input is planned as an `EncounterTurnPlan`: what the speaking investigator does (a `DeclaredAction`),
how each NPC and creature means to fight (`NpcArrangement`), who joins, numbers a sheet lacked, what
the outcome brings about, and how long a round takes.

These are the types a model fills, so their field descriptions are the instructions it reads, in
Chinese, and the schemas are kept small for cheap models (tests/test_encounter_models.py records
their sizes). What only the engine keeps on the same objects (a node seen, an obstacle broken) is
left out of the schema (SkipJsonSchema). Nothing here checks a plan against the encounter: that is
engine/encounter/plan_check.py's, which sends a bad plan back once with concrete reasons; what the
state keeps is engine/encounter/model.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from ...module.schema import Method, Stake
from ...rules.dice_expr import StoredDiceText

Mode = Literal["fight", "chase"]
Side = Literal["party", "foe", "neutral"]
Reaction = Literal["dodge", "fight_back", "cover", "none"]
ActionDo = Literal["attack", "shoot", "maneuver", "aim", "aid", "reload", "unjam", "ready", "defend", "flee", "pursue",
                   "move", "cross", "hide", "barrier", "help", "speak", "surrender", "stand", "use", "other"]
Difficulty = Literal["常规", "困难", "极难"]

# The encounter atoms a stake's `when` may use besides the world's condition grammar
# (world/agents.holds), as engine/encounter/stakes.py evaluates them.
STAKE_ATOMS = {
    "down": "down:谁 倒下了（昏迷、濒死或死亡）",
    "dead": "dead:谁 死了",
    "escaped": "escaped:谁 脱身了",
    "caught": "caught:谁 被抓住了",
    "surrendered": "surrendered:谁 投降了",
    "ended": "ended:方式 遭遇这样结束（won lost fled escaped caught parley surrender dispersed）",
}

_REACT = "挨打时怎么应对：dodge 闪避，fight_back 反击（只对近战），cover 扑向掩体（只对开枪），none 不应对"


class AttackPick(BaseModel):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    target: str = Field(description="打谁：遭遇里的人的 id")
    with_: str = Field("", alias="with", description="用什么打：简报里列出的攻击 id（拿着的武器或天生的爪牙）；徒手留空")
    shots: int = Field(1, ge=1, le=3, description="这一下开几枪，手枪最多 3；近战写 1")


class Thing(BaseModel):
    """An everyday thing picked up on the spot, as agents/cast.Thing (world/ops.Bring registers it)."""

    name: str = Field(description="东西的名称，如 拨火棍、油灯")
    look: str = Field("", max_length=80, description="一句外观，可留空")


class DeclaredAction(BaseModel):
    do: ActionDo = Field(description=(
        "做什么：attack 近战，shoot 开枪或投掷，maneuver 战技，aim 瞄准（下一枪得奖励骰），aid 急救救人，reload 装弹，unjam 排除卡壳，ready 拿出武器，"
        "defend 只防守，flee 逃跑，pursue 追赶，move 追逐中跑一段，cross 过障碍，hide 躲藏（潜行，藏好了对手看不见），barrier 设路障，"
        "help 帮同伴（他下一次检定得奖励骰），speak 喊话，surrender 投降，stand 站住迎战，use 用一样东西，other 别的（交给裁定）"))
    attacks: list[AttackPick] = Field(default_factory=list, max_length=4, description=(
        "attack、shoot、maneuver 才写：每次攻击一条，一轮攻击几次的怪物写几条；别的行动留空"))
    maneuver: Literal["", "缴械", "推倒", "制住", "夺取"] = Field("", description="maneuver 时是哪一种")
    target: str = Field("", description=(
        "aid、help、speak、surrender、pursue 的对象 id；use、ready、reload、unjam 的物品 id；cross 的障碍 id；没有留空"))
    to: str = Field("", description="move、flee 往哪去：节点 id，或 away 表示离开现场")
    method: str = Field("", description="cross 用障碍的哪种办法（办法 id）")
    react: Literal["", "dodge", "fight_back", "cover", "none"] = Field("", description=_REACT + "；留空照旧")
    takes: list[Thing] = Field(default_factory=list, max_length=2, description="动手前先拿起的日常物件（屋里的拨火棍）；没有留空")
    speech: str = Field("", max_length=80, description="同时说的话；不说话留空")
    summary: str = Field(max_length=80, description="中立的一句话：谁对谁做什么；不照抄玩家原话")


class NpcArrangement(BaseModel):
    who: str = Field(description="NPC 或怪物 id")
    goal: str = Field(max_length=40, description="他在这场里要什么，几个字：护书撤退、夺回钥匙、救同伴、拖延时间")
    side: Literal["", "party", "foe", "neutral"] = Field("", description="站哪边：party 帮调查员，foe 对手，neutral 不掺和；不变留空")
    actions: list[DeclaredAction] = Field(min_length=1, max_length=3, description="想做的行动，按优先排；轮到他时第一条做得到的落实")
    react: Reaction = Field(description=_REACT)
    say: str = Field("", max_length=24, description="动手时喊的一句；不说话留空")
    speak: bool = Field(False, description="这一轮要他本人开口说一段话时填 true")
    # Explicit both ways: a fleeing investigator nobody wrote down stops the round for a decision.
    pursue: list[str] = Field(default_factory=list, description="有调查员逃跑时，他追谁（id）")
    let_go: list[str] = Field(default_factory=list, description="有调查员逃跑时，他放走谁、不追（id）")
    keep: bool = Field(True, description="情况没大变就沿用到下一轮")
    # An important NPC's Luck pool (Keeper Rulebook p. 168, optional): the keeper may spend it on its
    # own failed rolls this round, up to this many points; the engine settles it.
    luck: int = Field(0, ge=0, le=99, description="简报写了幸运池时：这一轮他自己的检定失败，最多花多少点幸运改成成功；不花写 0")


class EndProposal(BaseModel):
    how: Literal["parley", "surrender", "gave_up", "dispersed"] = Field(
        description="怎么收场：parley 谈和，surrender 一方投降，gave_up 一方放弃，dispersed 散了")
    who: list[str] = Field(description="谁投降、放弃或谈和（id）")
    terms: str = Field("", max_length=80, description="条件，一句话；没有留空")


class Fill(BaseModel):
    who: str = Field(description="缺数值的人或怪物 id")
    key: str = Field(description="缺的是哪一项：生命、闪避、格斗(斗殴)、射击(手枪)、备弹……")
    value: int = Field(ge=0, description="定下的数值")
    why: str = Field("", max_length=60, description="依据，一句话")
    by: SkipJsonSchema[Literal["keeper", "module", "ordinary"]] = "keeper"  # who fixed it; a model is the keeper


# -- a chase's site, as the keeper draws one when neither the module nor the map gives it ------------
class SiteNode(BaseModel):
    id: str = Field(description="节点 id，英文小写加下划线")
    name: str = Field(description="这一处叫什么，几个字：墓碑间的窄道")
    look: str = Field("", max_length=60, description="一句样子")
    place: str = Field("", description="在哪个地点里（地点 id）；不在模组地点里留空")
    obstacle: str = Field("", description="进这里要过的障碍 id；畅通留空")
    exit: bool = Field(False, description="跑到这里就能脱身时填 true")
    blocks_sight: bool = Field(False, description="这里挡住视线（拐角、树丛）时填 true")
    seen: SkipJsonSchema[bool] = False  # the players have seen it (disclosure)


class SiteEdge(BaseModel):
    a: str = Field(description="节点 id")
    b: str = Field(description="相连的另一个节点 id")
    cost: int = Field(1, ge=1, le=3, description="跑过去要几次移动")


class ObstacleState(BaseModel):
    id: str = Field(description="障碍 id，英文小写加下划线")
    kind: Literal["hazard", "barrier"] = Field(description="hazard 险阻，边跑边过、失败耽搁或受伤；barrier 路障，过了检定才能通过")
    what: str = Field(description="是什么，几个字：结冰的台阶、锁着的铁门")
    methods: list[Method] = Field(min_length=1, max_length=4, description="过它的几种办法")
    harm: StoredDiceText = Field("", description="失败时受的伤，骰子式如 1D3；不伤人留空")
    hp: int | None = Field(None, ge=1, description="路障能挨几点伤害才破；不能打破留空")
    # The chase's own: whether it still stands, who opened or blocked it, whether the players know it.
    state: SkipJsonSchema[Literal["intact", "open", "broken", "blocked"]] = "intact"
    by: SkipJsonSchema[str] = ""
    revealed: SkipJsonSchema[bool] = False


class SiteDraft(BaseModel):
    nodes: list[SiteNode] = Field(min_length=3, max_length=14, description="追逐经过的地方，起点在前")
    edges: list[SiteEdge] = Field(description="哪两处直接相连；整张图要连通")
    obstacles: list[ObstacleState] = Field(default_factory=list, max_length=6, description="路上的障碍，节点的 obstacle 引用它")


class Contest(BaseModel):
    by: list[str] = Field(description="偷袭的一方（id）")
    skill: str = Field(description="他们用的技能，如 潜行")
    against: str = Field(description="被偷袭的一方用的技能，如 聆听、侦查")


class EncounterStart(BaseModel):
    mode: Mode = Field(description="fight 打起来，chase 追逐")
    foes: list[str] = Field(min_length=1, description="对手：NPC 或怪物 id")
    allies: list[str] = Field(default_factory=list, description="站在调查员这边一起动手的 NPC id")
    quarry: Literal["", "party", "foe"] = Field("", description="追逐时谁在逃：party 调查员，foe 对手；打斗留空")
    surprise: Literal["", "party", "foe"] = Field("", description="一方毫无防备：party 调查员被偷袭，foe 对手被偷袭；都有防备留空")
    surprise_contest: Contest | None = Field(None, description="偷袭成不成要掷骰对抗时写；surprise 已经定了就留空")
    opening: DeclaredAction | None = Field(None, description="调查员这次行动本身就是第一下（扑上去、开枪）时写它；不是留空")
    npcs: list[NpcArrangement] = Field(default_factory=list, description="每个对手和同伴的安排，一人一条")
    stakes: list[Stake] = Field(default_factory=list, max_length=4, description="胜负会带来什么，一条一个结果")
    site: SiteDraft | None = Field(None, description="追逐的路线：只有简报说没有现成路线时才画；否则留空")
    # Required of a model (plan_check); an event or a tool starting one leaves it to the first plan.
    pace: int | None = Field(None, ge=1, le=600, description="一轮在故事里过几秒，必填：扭打 3–6，枪战 6–10，追逐 10–30")
    reason: str = Field(max_length=60, description="为什么打起来或追起来，一句话")


class CoverNote(BaseModel):
    who: str = Field(description="谁：遭遇里的人的 id")
    what: str = Field(max_length=20, description="躲在什么后面：大钟、墓碑、翻倒的桌子")
    full: bool = Field(False, description="整个人藏住、对手看不见时填 true；只挡住一半填 false")


class Distance(BaseModel):
    a: str = Field(description="一个人的 id")
    b: str = Field(description="另一个人的 id")
    yards: int = Field(ge=0, le=2000, description="相隔多少码（1 码约 0.9 米）")


class EncounterTurnPlan(BaseModel):
    lookup: str = Field("", description="安排前想先查的一件事，一句话；手头资料够用就留空")
    action: DeclaredAction | None = Field(None, description="说话的这位调查员这一轮做什么；只安排 NPC 时留空")
    npcs: list[NpcArrangement] = Field(default_factory=list, description=(
        "NPC 和怪物的安排：还没有安排的、情况变了的要写；照旧的不写"))
    join: list[str] = Field(default_factory=list, description="这一轮加入的 NPC id")
    fills: list[Fill] = Field(default_factory=list, max_length=6, description="简报说缺的数值，在这里定下")
    stakes: list[Stake] = Field(default_factory=list, max_length=3, description="新出现的胜负后果；没有留空")
    end: EndProposal | None = Field(None, description="有人投降、谈和或放弃，遭遇就此收场时写")
    pace: int | None = Field(None, ge=1, le=600, description="一轮在故事里过几秒；不变留空")
    cover: list[CoverNote] = Field(default_factory=list, max_length=6, description="谁躲在了什么后面（局面变了才写）；没有留空")
    distances: list[Distance] = Field(default_factory=list, max_length=6, description="开枪时相隔的距离（定射程用）；近身或不开枪留空")
