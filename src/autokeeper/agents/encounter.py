"""What the keeper plan (agents/cast.Cast.plan) is told and asked inside a fight or a chase.

The engine (engine/encounter/, WP-C) owns the state and the checks; this module owns the words: the
encounter brief (`encounter_brief`, from plain data the engine fills in: `Brief`), the system prompt
of the keeper's encounter plan and of the start of an encounter, the small answers only the agents
need (a reaction in a player's own words, a pursuit decision, an AI investigator's pick), the
`EncounterHint` on the everyday plan, and the arbiter's encounter section with the book's table of
other damage.

The user's call (2026-10-21): an answer still invalid after its one resend means the prompt or the
context did not give the model what it needed. So the brief puts everything in front of it: exact
HP for all, true and public names, everyone's id, each keeper-controlled person's actions with what
they need and whether they can do them now, the attacks and weapons actually in hand by id, ammo
and jams, engagements, goals, stakes, the site, the last round. No fallback policy lives here:
what is still invalid goes back to the caller with its problems (Checked).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from ..engine.encounter.plan import EncounterTurnPlan, Mode, ObstacleState, Reaction, SiteEdge, SiteNode

PlanMode = Literal["act", "arrange", "react", "pursuit"]


@dataclass
class Checked[T]:
    """A model's answer after the deterministic check and its one resend. `value` is the answer as
    it last came back (None when it was not even the schema's shape); `problems` what the check
    still finds wrong ([]: it holds). The caller lands what holds and notes the rest as 【未落实】;
    nothing is put in its place."""

    value: T | None
    problems: list[str] = field(default_factory=list)
    raw: object = None  # the reply's data as it came

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.problems


# -- the everyday plan's hint that an encounter begins (agents/cast.Plan.encounter) -------------
class EncounterHint(BaseModel):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    mode: Mode = Field(description="fight 打起来，chase 追逐")
    with_: list[str] = Field(alias="with", min_length=1, max_length=6, description="对手或被追、追来的 NPC、怪物 id")
    trigger: Literal["now", "on_fail", "on_success"] = Field("now", description=(
        "什么时候开始：now 这次行动就是动手或逃跑；on_fail 检定失败才打起来（偷东西被发现）；on_success 检定成功才开始（追上了逃的人）"))


# -- small answers --------------------------------------------------------------------------------
class ReactionAnswer(BaseModel):
    reaction: Reaction = Field(description="dodge 闪避，fight_back 反击（只对近战），cover 扑向掩体（只对开枪），none 不应对；只能选给出的几种")
    summary: str = Field(max_length=60, description="中立的一句话：调查员怎么应对；不照抄玩家原话")


class Pursuit(BaseModel):
    who: str = Field(description="守秘人控制的对手或怪物 id")
    pursue: list[str] = Field(default_factory=list, description="他追谁（逃跑者的 id）")
    let_go: list[str] = Field(default_factory=list, description="他放走谁、不追（逃跑者的 id）")
    why: str = Field("", max_length=40, description="一句话：照他的本性和目标，为什么追或不追")


class PursuitAnswer(BaseModel):
    decisions: list[Pursuit] = Field(min_length=1, description="每个列出的对手一条；每个逃跑者要么在 pursue 要么在 let_go")


class ArrangePlan(EncounterTurnPlan):
    """An encounter plan with no action of the investigator's (mode "arrange": the player pressed a
    button, so the action is already set)."""

    action: SkipJsonSchema[None] = None


class Choice(BaseModel):
    """An AI investigator's pick among the offered options; the schema's enum is set per call."""

    choice: str = Field(description="选哪一项：只填列出的选项 id")


# -- the brief: plain data the engine fills in ----------------------------------------------------
class BriefAttack(BaseModel):
    id: str  # what AttackPick.with takes: the item id of a weapon, or the natural attack's name
    name: str
    skill: str = ""
    value: int | None = None
    damage: str = ""  # as the book writes it, DB included: 1D6+DB
    firearm: bool = False
    thrown: bool = False
    natural: bool = False
    range: str = ""
    loaded: int | None = None  # None: not a gun, or not known yet (a fill)
    capacity: int = 0
    spare: int | None = None
    jammed: bool = False
    held: bool = True  # in hand (natural attacks always); a carried weapon must be readied first
    per_round: int = 1


class BriefAction(BaseModel):
    do: str
    ok: bool = True
    why: str = ""  # what it needs that is missing, when not ok; what it would use, when ok


class BriefPerson(BaseModel):
    id: str
    name: str  # the true name
    public: str = ""  # what the players call them, when not the true name
    kind: Literal["investigator", "npc", "creature"] = "npc"
    side: str = "foe"
    controller: Literal["human", "ai", "keeper"] = "keeper"
    presence: str = "in"
    node: str = ""
    hp: int | None = None
    hp_max: int | None = None
    conditions: list[str] = Field(default_factory=list)
    dex: int | None = None
    build: int | None = None
    mov: int | None = None
    speed: int | None = None
    db: str = ""
    skills: dict[str, int] = Field(default_factory=dict)  # 闪避, 格斗(斗殴), 潜行 …
    attacks: list[BriefAttack] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)  # other things carried: "books（三本日记）"
    goal: str = ""
    arranged: str = ""  # the standing arrangement, in a line; "" none
    react: str = ""
    needs_arrangement: bool = False
    speaks: bool = True
    surprised: bool = False
    lost_next: str = ""
    actions: list[BriefAction] | None = None  # None: worked out from the attacks (default_actions)
    traits: list[str] = Field(default_factory=list)  # 护甲 2、枪伤减半、再生 …, and what the engine cannot use
    missing: list[str] = Field(default_factory=list)  # numbers the sheet lacks: fills
    # What this NPC or creature perceives (the keeper knows all; the NPC does not, guide #25): who it
    # can see or hear now, who it cannot and why, what it has been told. None: not worked out (a human's).
    sees: list[str] | None = None
    unseen: list[str] = Field(default_factory=list)  # "lin（藏在掩体后面，看不见）"
    told: list[str] = Field(default_factory=list)  # what it was told of the others, in a line each
    luck: int | None = None  # an important NPC's Luck pool left (Keeper Rulebook p. 168); None: it has none


class BriefStake(BaseModel):
    when: str
    flags: list[str] = Field(default_factory=list)
    note: str = ""
    fired: bool = False


class Brief(BaseModel):
    id: str = ""
    mode: Mode = "fight"
    round: int = 0
    phase: str = "open"
    place: str = ""
    reason: str = ""
    pace: int | None = None
    people: list[BriefPerson] = Field(default_factory=list)
    engaged: list[tuple[str, str]] = Field(default_factory=list)
    cover: list[str] = Field(default_factory=list)  # "brennan 躲在翻倒的桌子后（部分掩体）"
    prone: list[str] = Field(default_factory=list)
    holds: list[str] = Field(default_factory=list)  # "douglas 制住了 brennan"
    distances: dict[str, int] = Field(default_factory=dict)  # "a|b" -> yards
    nodes: list[SiteNode] = Field(default_factory=list)
    edges: list[SiteEdge] = Field(default_factory=list)
    obstacles: list[ObstacleState] = Field(default_factory=list)
    quarry: list[str] = Field(default_factory=list)
    pursuers: list[str] = Field(default_factory=list)
    lost_sight: list[str] = Field(default_factory=list)
    site_missing: bool = False  # a chase with no route yet: the plan draws one (EncounterStart.site)
    fled: list[str] = Field(default_factory=list)  # who fled this round: keeper opponents must decide pursuit
    stakes: list[BriefStake] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)  # the last round's account, keeper form
    history: list[str] = Field(default_factory=list)  # older rounds, compact
    unsettled: list[str] = Field(default_factory=list)  # last plan's 【未落实】
    waiting: str = ""  # what the round waits on


# What each action needs and which fields it takes: the system prompt's table and the brief's
# "现在做不到" reasons speak the same words.
ACTION_NEEDS: dict[str, str] = {
    "attack": "近战。要和目标近身（追逐中在同一节点）；attacks 每次攻击一条，with 填他【攻击】里的近战 id，徒手留空",
    "shoot": "开枪或投掷。with 填手里的枪或投掷物 id；枪要有弹、没卡壳；手枪一轮最多开 3 枪（shots）",
    "maneuver": "战技（缴械、推倒、制住、夺取）。目标体格比他大 3 或以上就无效；attacks 写目标，maneuver 写哪一种",
    "aim": "瞄准一轮：target 填目标 id；下一枪得一个奖励骰",
    "aid": "急救倒下的人。target 填伤者 id；要在他身边",
    "reload": "装弹。target 填枪的 id；要有备弹",
    "unjam": "排除卡壳。target 填卡壳的枪 id",
    "ready": "拿出身上的武器。target 填武器 id；拿出来下一轮才能用它",
    "defend": "这一轮只防守",
    "flee": "脱离并逃跑。to 填节点 id 或 away；被制住时不行",
    "pursue": "追逃跑的人。target 填被追的人 id",
    "move": "追逐中跑到相邻节点。to 填节点 id",
    "cross": "过障碍。target 填障碍 id，method 填它的办法 id",
    "hide": "躲起来（潜行对对手的侦查）：藏好了对手就看不见他、打不着他；他一出手就暴露",
    "barrier": "设路障（闩门、推倒柜子）",
    "help": "帮同伴，他下一次检定得奖励骰。target 填同伴 id",
    "speak": "喊话、劝说、谈判。target 填对象 id，speech 写说的话；不会说话的不能选",
    "surrender": "投降。target 填向谁",
    "stand": "追逐中站住迎战",
    "use": "用身上或手里的一样东西。target 填物品 id",
    "other": "以上都不是：交给裁定员按规则裁定，summary 写清做什么",
}


def default_actions(p: BriefPerson, b: Brief) -> list[BriefAction]:
    """What `p` can do now, from what the brief knows of them: the engine passes its own
    (engine/encounter/actions.preconditions) when it has them."""
    melee = [a for a in p.attacks if not a.firearm and not a.thrown and a.held]
    guns = [a for a in p.attacks if (a.firearm or a.thrown) and a.held]
    stowed = [a for a in p.attacks if not a.held and not a.natural]
    held_by = [h for h in b.holds if h.split(" ", 1)[-1].endswith(p.id)]
    out: list[BriefAction] = []
    if b.mode == "chase":
        out.append(BriefAction(do="move"))
        if any(n.obstacle for n in b.nodes):
            out.append(BriefAction(do="cross"))
    fists = p.kind != "creature"
    out.append(BriefAction(do="attack", ok=bool(melee) or fists,
                           why="用：" + "、".join(a.id for a in melee) + ("，或徒手" if fists else "") if melee else
                           ("徒手" if fists else "没有近战攻击")))
    ready = [a for a in guns if not a.jammed and (a.loaded is None or a.loaded > 0 or a.thrown)]
    if guns:
        out.append(BriefAction(do="shoot", ok=bool(ready), why=("用：" + "、".join(a.id for a in ready)) if ready else
                               "；".join(f"{a.id}{'卡壳了' if a.jammed else '没子弹了'}" for a in guns)))
    out.append(BriefAction(do="maneuver"))
    reload = [a for a in p.attacks if a.firearm and a.capacity and (a.loaded or 0) < a.capacity]
    if reload:
        can = [a for a in reload if a.spare != 0]
        out.append(BriefAction(do="reload", ok=bool(can), why="、".join(a.id for a in can) if can else "没有备弹"))
    if jammed := [a.id for a in p.attacks if a.jammed]:
        out.append(BriefAction(do="unjam", why="、".join(jammed)))
    if stowed:
        out.append(BriefAction(do="ready", why="、".join(a.id for a in stowed)))
    if down := [x.id for x in b.people if x.side == p.side and x.id != p.id
                and set(x.conditions) & {"濒死", "昏迷"} and "死亡" not in x.conditions]:
        out.append(BriefAction(do="aid", why="可救：" + "、".join(down)))
    out.append(BriefAction(do="flee", ok=not held_by, why="被制住了" if held_by else ""))
    if b.mode == "fight" and b.fled:
        out.append(BriefAction(do="pursue"))
    out.append(BriefAction(do="defend"))
    if b.mode == "chase":
        out += [BriefAction(do="hide"), BriefAction(do="barrier"), BriefAction(do="stand")]
    if p.speaks:
        out.append(BriefAction(do="speak"))
    out += [BriefAction(do="surrender"), BriefAction(do="other")]
    return out


def _attack(a: BriefAttack) -> str:
    bits = [f"{a.skill} {a.value}" if a.skill and a.value is not None else a.skill, a.damage]
    if a.firearm:
        bits.append(f"弹 {'?' if a.loaded is None else a.loaded}/{a.capacity}" if a.capacity else "")
        bits.append(f"备弹 {'未知' if a.spare is None else a.spare}")
        bits.append("卡壳" if a.jammed else "")
    bits += [f"射程 {a.range}" if a.range else "", "投掷" if a.thrown else "", f"每轮 {a.per_round} 次" if a.per_round > 1 else "",
             "天生的" if a.natural else "" if a.held else "收着，要先 ready"]
    return f"{a.id}＝{a.name}（{'，'.join(x for x in bits if x)}）"


def _person(p: BriefPerson, b: Brief) -> list[str]:
    who = {"human": "玩家", "ai": "AI 队友", "keeper": "守秘人（你）"}[p.controller]
    side = {"party": "调查员一方", "foe": "对手", "neutral": "中立"}.get(p.side, p.side)
    named = p.name + (f"（玩家所见：{p.public}）" if p.public and p.public != p.name else "")
    hp = f"生命 {'?' if p.hp is None else p.hp}/{'?' if p.hp_max is None else p.hp_max}"
    head = [f"`{p.id}` {named}", side, f"{who}控制", hp]
    if p.conditions:
        head.append("、".join(p.conditions))
    if p.presence != "in":
        head.append({"fled": "已逃跑", "escaped": "已脱身", "surrendered": "已投降", "captured": "被抓住", "left": "已离开"}
                    .get(p.presence, p.presence))
    stats = [f"敏捷 {p.dex}" if p.dex is not None else "", f"体格 {p.build}" if p.build is not None else "",
             f"移动 {p.mov}" if p.mov is not None else "", f"速度 {p.speed}" if p.speed is not None else "",
             f"伤害加值 {p.db}" if p.db else "", f"在 {p.node}" if p.node else ""]
    rows = ["- " + "｜".join(head) + ("｜" + "，".join(x for x in stats if x) if any(stats) else "")]
    if p.skills:
        rows.append("  技能：" + "，".join(f"{k} {v}" for k, v in p.skills.items()))
    rows.append("  攻击（with 只能填这些 id）：" + ("；".join(_attack(a) for a in p.attacks) or
                                               ("没有武器，只能徒手" if p.kind != "creature" else "没有攻击")))
    if p.items:
        rows.append("  身上别的东西：" + "、".join(p.items))
    if p.traits:
        rows.append("  特性：" + "、".join(p.traits))
    if p.surprised:
        rows.append("  被偷袭：这一轮不能应对")
    if p.lost_next:
        rows.append(f"  下一次行动作废：{p.lost_next}")
    if p.controller != "human":
        actions = p.actions if p.actions is not None else default_actions(p, b)
        can = [f"{a.do}" + (f"（{a.why}）" if a.why else "") for a in actions if a.ok]
        cannot = [f"{a.do}（{a.why}）" for a in actions if not a.ok]
        rows.append("  现在能做：" + "、".join(can))
        if cannot:
            rows.append("  现在做不到：" + "、".join(cannot))
    if p.sees is not None:
        rows.append("  他察觉得到：" + ("、".join(p.sees) or "谁也看不见")
                    + ("；察觉不到：" + "、".join(p.unseen) if p.unseen else "")
                    + "。他只能对察觉得到的人出手、追赶")
    if p.told:
        rows.append("  他听说过：" + "；".join(p.told))
    if p.luck is not None:
        rows.append(f"  幸运池：{p.luck}（只在他自己的检定失败时可花，用掉不回；安排里 luck 写这一轮最多花多少）")
    if p.controller == "keeper":
        mind = [f"目标：{p.goal}" if p.goal else "", f"现有安排：{p.arranged}" if p.arranged else "还没有安排",
                f"挨打应对：{p.react}" if p.react else "", "【需要安排】" if p.needs_arrangement or not p.arranged else ""]
        rows.append("  " + "；".join(x for x in mind if x))
    if p.missing:
        rows.append("  缺少的数值（在 fills 里定下）：" + "、".join(p.missing))
    return rows


def encounter_brief(b: Brief) -> str:
    """The keeper's whole view of an encounter, as the plan reads it. Nothing is clipped: a plan
    is only as valid as what it was shown."""
    kind = {"fight": "战斗", "chase": "追逐"}[b.mode]
    head = f"【遭遇 {b.id}】{kind}，第 {b.round} 轮" + ("（善后：对手已倒下，还有人要救或要收拾）" if b.phase == "aftermath" else "")
    head += f"｜地点：{b.place}" if b.place else ""
    head += f"｜起因：{b.reason}" if b.reason else ""
    head += f"｜一轮 {b.pace} 秒" if b.pace else "｜一轮多少秒：未定，pace 必填"
    out = [head, "【这场遭遇里的 id】" + "、".join(f"{p.id}（{p.public or p.name}）" for p in b.people)]
    out.append("【各人】")
    for p in b.people:
        out += _person(p, b)
    scene = []
    if b.engaged:
        scene.append("近身缠斗：" + "；".join(f"{a} 与 {c}" for a, c in b.engaged))
    if b.holds:
        scene.append("压制：" + "；".join(b.holds))
    if b.cover:
        scene.append("掩体：" + "；".join(b.cover))
    if b.prone:
        scene.append("倒在地上：" + "、".join(b.prone))
    if b.distances:
        scene.append("距离：" + "；".join(f"{k.replace('|', '—')} {v} 码" for k, v in b.distances.items()))
    if scene:
        out.append("【局面】" + "｜".join(scene))
    if b.mode == "chase" or b.nodes:
        if b.site_missing:
            out.append("【追逐路线】没有现成的路线：在 site 里画一条（3–14 个节点，起点在前，连通）")
        else:
            out.append("【追逐路线】节点：" + "；".join(
                f"{n.id}＝{n.name}" + (f"（障碍 {n.obstacle}）" if n.obstacle else "") + ("（出口，到这就脱身）" if n.exit else "")
                + ("（挡视线）" if n.blocks_sight else "") for n in b.nodes))
            out.append("相连：" + "；".join(f"{e.a}—{e.b}" + (f"（{e.cost} 步）" if e.cost > 1 else "") for e in b.edges))
            for o in b.obstacles:
                ways = "、".join(f"{m.id}＝{m.label}（{m.skill}·{m.difficulty}）" for m in o.methods)
                out.append(f"障碍 {o.id}：{o.what}，{'险阻' if o.kind == 'hazard' else '路障'}，{o.state}"
                           + (f"，失败伤害 {o.harm}" if o.harm else "") + (f"，耐久 {o.hp}" if o.hp else "") + f"；办法：{ways}")
        if b.quarry or b.pursuers:
            out.append(f"逃的：{'、'.join(b.quarry) or '无'}；追的：{'、'.join(b.pursuers) or '无'}"
                       + (f"；跟丢了：{'、'.join(b.lost_sight)}" if b.lost_sight else ""))
    if b.fled:
        keepers = [p.id for p in b.people if p.controller == "keeper" and p.side == "foe" and p.presence == "in"]
        out.append(f"【有人逃跑】{'、'.join(b.fled)}：{'、'.join(keepers) or '对手'} 每人都要在 pursue 或 let_go 里写明追不追")
    if b.stakes:
        out.append("【胜负后果】" + "；".join(f"{s.when} → {'、'.join(s.flags) or '无标记'}" + (f"（{s.note}）" if s.note else "")
                                         + ("（已成立）" if s.fired else "") for s in b.stakes))
    if b.history:
        out.append("【更早的回合】\n" + "\n".join(b.history))
    out.append("【上一轮经过】\n" + ("\n".join(b.lines) if b.lines else "（这是第一轮）"))
    if b.unsettled:
        out.append("【上次没落实的安排】\n" + "\n".join(b.unsettled))
    if b.waiting:
        out.append(f"【正在等】{b.waiting}")
    return "\n".join(out)


# -- prompts -------------------------------------------------------------------------------------
_TABLE = "\n".join(f"- {k}：{v}" for k, v in ACTION_NEEDS.items())

ENCOUNTER_PLAN_SYSTEM = """你是这场 CoC 7 版跑团的守秘人，看得到全部底牌。现在正在一场战斗或追逐里。每次给你【遭遇】简报和要你做的事，你交一份安排；掷骰和结算由引擎按规则做，叙述另外进行。

规则（守秘人规则书第六、七章）：
- 每个人每轮在自己的敏捷顺序上行动一次；一轮攻击几次的怪物，attacks 就写几条，都在它自己的回合打完。
- 挨打时的应对不占行动：近战可以闪避或反击，开枪只能闪避扑向掩体（cover）或不应对。NPC 和怪物默认反击（react 填 fight_back），只在打算撤离时才闪避（第 87 页）。
- 战斗中的检定不能孤注一掷。失败、受伤、死亡与疯狂都是允许的结果：不替调查员手下留情，也不为制造惨剧让对手乱来。

填法：
- 所有 id 只用【这场遭遇里的 id】、各人【攻击】里的攻击 id、追逐路线的节点和障碍 id；不写名字代替 id。
- action（要你填时）：只写玩家这一轮说要做的事，不替他加动作。说的不在下表里，do 填 other；拿起手边没登记的日常物件当武器（拨火棍、油灯），先写进 takes，再用 other 交裁定。summary 是中立的一句话，只用玩家知道的叫法（【这场遭遇里的 id】括号里的称呼），不照抄玩家原话。
- npcs：给标着【需要安排】的、和局面明显变了的守秘人控制的人各写一条；照旧的不写。actions 按优先排 1–3 条，只从他【现在能做】里选，第一条做不到才轮到下一条。照他的本性、目标和处境：模组写了打算就照办；野兽和怪物凭本能扑向最近或最弱的；普通人重伤会想逃、投降或求饶，狂热的、被控制的不会；不追打已经倒下的人，除非它的本性如此。
- react 是他这一轮挨打时怎么应对；say 是动手时喊的一句（会说话的人才写，24 字内，多数留空）；speak 只在这一轮要他本人开口说一段话时填 true。
- 简报里有【有人逃跑】时，每个守秘人控制的对手都要在 pursue 或 let_go 里写明每个逃跑者，二者必选其一；模组写了它追不追就照办。
- join：这一轮主动加入的在场 NPC。fills：只填简报里【缺少的数值】。stakes：新出现的胜负后果。end：有人投降、谈和或放弃，就此收场时写。pace：一轮在故事里过几秒，简报写着未定时必填（扭打 3–6，枪战 6–10，追逐 10–30）。
- lookup：资料不够、答案又会改变安排时写一句想查的事；不用查留空。

【行动和它的条件】
""" + _TABLE

MODE_ASKS: dict[str, str] = {
    "act": "玩家说了这一轮要做的事（下面【玩家】）。填 action，再按需要安排 npcs。",
    "arrange": "调查员的行动已经定了（玩家按了选项）。只安排 npcs，需要时写 join、fills、stakes、end、pace。",
    "react": "一次攻击正要落到调查员身上，玩家用自己的话说了怎么应对（下面【玩家】）。选 reaction，只能从【可选的应对】里选；"
             "summary 中立一句话。玩家没说清就选对他最自然的那种。",
    "pursuit": "有人逃跑了，下面列出的对手还没定追不追。每个对手一条：pursue 或 let_go 写明每个逃跑者。",
}

ENCOUNTER_START_SYSTEM = """你是这场 CoC 7 版跑团的守秘人，看得到全部底牌。这一轮的行动要打起来或追起来了：定下这场遭遇怎么开始。掷骰和结算由引擎按规则做，叙述另外进行。

- foes 是对手，allies 是站在调查员这边一起动手的 NPC，都只用【可能卷入的人】里的 id；只写此刻真在场、真会动手或追逃的。
- quarry：追逐时谁在逃。surprise：一方毫无防备才写；偷袭成不成要掷骰对抗时写 surprise_contest（如 潜行 对 聆听），不要两个都写。
- opening：调查员这次行动本身就是第一下（扑上去、开枪、拔腿就跑）时，照玩家说的写成一条行动，with 用他【攻击】里的 id；不是就留空。
- npcs：每个对手和同伴一条安排，actions 只用他的攻击 id；NPC 和怪物默认反击（react 填 fight_back），打算撤离时才闪避（规则书第 87 页）。
- stakes：这场的胜负会带来什么，一条一个结果，when 用条件写法，flags 只用模组里的剧情标记。
- site：只有简报说没有现成追逐路线时才画：3–14 个节点，起点在前，连通；符合这个地方、时间和天气，不新增案情、怪物或别的人。
- pace 必填：一轮在故事里过几秒（扭打 3–6，枪战 6–10，追逐 10–30）。reason 一句话：为什么打起来或追起来。
- 失败、受伤、死亡与疯狂都是允许的结果；对手照它的本性和目标行事，不手下留情，也不为制造惨剧乱来。

【行动和它的条件】
""" + _TABLE

CHOICE_SYSTEM = """你在玩一场 CoC 7 版中文跑团，扮演调查员「{name}」（{occupation}）。
{sheet}
现在正在战斗或追逐里。你只知道眼前看得到的事。从列出的选项里选一个：像一个谨慎的玩家，先救濒死的同伴，打得过就打，打不过就退，不去送死。只填选项 id。"""

# The book's Table III (Keeper Rulebook, pp. 102–103), for harm the engine has no rule of its own for.
OTHER_DAMAGE = """【其他类型的伤害】（规则书表 III，第 102–103 页；按情况可增可减。一次或一轮的量，仍在伤害源里的每轮再受一次）
- 轻度 1D3：拳打脚踢、头槌、弱酸、呼吸烟雾弥漫的空气*、投来的拳头大的石子、坠落到淤泥地（每 10 英尺）
- 中度 1D6：坠落到草地（每 10 英尺）、棍子、强酸、呛水*、暴露在真空*、小口径子弹、箭、火焰（燃烧的火把）
- 重度 1D10：.38 口径子弹、坠落到水泥地（每 10 英尺）、斧头、火焰（火焰喷射器、穿过燃烧的房间）、离爆炸的手榴弹 6–9 码、弱毒剂**
- 致命 2D10：被 30 英里时速的汽车撞、离爆炸的手榴弹 3–6 码、强毒剂**
- 终结 4D10：被超速的汽车撞、离爆炸的手榴弹 3 码内、致死毒剂**
- 血肉横飞 8D10：高速直接撞击、被火车撞
*窒息和溺水：每轮一次体质检定（剧烈活动时困难），失败后每轮受伤直到死亡或重新能呼吸；生命归零即死（不看重伤）。
**毒药：体质检定极难成功伤害减半；可能腹痛、呕吐、痉挛、昏迷、瘫痪等，由你定能否行动或给惩罚骰。"""

ARBITER_ENCOUNTER = """【遭遇中的裁定】
现在在一场战斗或追逐里（见【遭遇】）。命中、伤害、闪避反击都由引擎按规则结算，你只裁定交给你的这一件事（砸油灯、关门、喊人、念咒、用路上没写到的办法过障碍）：
- 照常给技能和难度（needs_roll、skill、difficulty、bonus）；对象只用【这场遭遇里的 id】。
- 成功后的结果写进 effects，每条一种：damage 伤害（target 填 id，dice 写骰子式，照下表比类似的伤害定）、condition 状态（倒地、着火、被缠住、看不见，what 写清）、obstacle 障碍（打开、堵上、破坏了哪个障碍，target 填障碍 id）、edge 下一次检定的奖励或惩罚骰（bonus 填 1、2 或 -1、-2，target 填谁）、item 东西（掉了、坏了、被夺走，target 填物品 id）。
- 不新增在场的人或怪物，不替对手决定它这一轮做什么，那是守秘人的安排。伤害照表 III 定。
"""
