"""The world record of one game: what is so in this game, who knows it, and how it came about.

It is plain data inside GameState (GameState.record), so it is written with the save,
snapshotted with every LangGraph checkpoint, copied with a copied save and trashed with a
deleted one. Nothing here touches files or models; world/store.py settles changes into it.

Three layers, never mixed:
    module base   module.yaml, read-only; seeded into `entities` and `facts` with origin "module"
    game truth    everything in this record: improvisations, changes, rewrites, each versioned
    knowledge     Fact.known_by: NPC ids, and PLAYERS once a fact reached the table (frozen)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from ..engine.encounter.plan import ObstacleState
from ..rules.coc import InjuryState

PLAYERS = "players"  # the table: what was told or shown to the investigators
DIRECTOR = "director"
KEEPER = "keeper"
ENGINE = "engine"

Origin = Literal["module", "improv", "game"]
FactKind = Literal["truth", "conclusion", "secret", "knows", "lie", "clue", "goal", "setting", "event", "relation", "promise", "state", "rumor"]


class Versioned(BaseModel):
    version: int = 1
    changed: int = 0  # record seq of the op that last changed it


class Life(BaseModel):
    """An NPC's or a creature's body in this game (Entity.life): written once a fight seeds it from
    the module's sheet, then kept, so whoever was hurt or killed stays so. An investigator's is on
    the sheet itself (rules/coc.Investigator)."""

    hp: int
    hp_max: int
    conditions: list[str] = Field(default_factory=list)  # 重伤 昏迷 濒死 死亡, the investigators' words
    injury: InjuryState = Field(default_factory=InjuryState)
    regenerating: bool = False  # down at 0 HP and coming back (a creature that heals each round)
    source: Literal["module", "fill", "ordinary"] = "module"  # the sheet, a number the keeper fixed, an ordinary person's


class WeaponState(BaseModel):
    """A weapon item's own state (Entity.weapon): what it is, and what is in it. A gun keeps its
    rounds and its jam from one fight to the next; no fight reloads it by itself."""

    kind: str  # the weapon it is: a catalog name (rules/weapons.WEAPONS) or the sheet's attack name
    loaded: int | None = None  # rounds in it; None: not known yet (filled when it is first used)
    capacity: int = 0  # rounds a full load holds; 0 for a weapon that takes none
    jammed: bool = False
    spare: int | None = None  # rounds carried for it; None: unknown, the keeper fills it before a reload
    malfunction: int = 100  # a roll of this or more jams it


class Entity(Versioned):
    id: str
    kind: Literal["npc", "location", "item", "investigator", "creature", "other"]
    name: str
    origin: Origin = "module"
    at: str = ""  # an NPC's or an item's place; an item's holder when someone has it
    status: str = ""  # "", 受伤, 失踪, 死亡 ...
    conditions: list[str] = Field(default_factory=list)
    following: str = ""  # investigator this NPC agreed to accompany, until follow_to
    follow_to: str = ""
    important: bool = True  # equipment bookkeeping is reserved for important items
    equipment: Literal["carried", "worn", "held", "concealed"] = "carried"
    quantity: int = Field(1, ge=0)
    hidden: bool = False
    observed_by: list[str] = Field(default_factory=list)  # witnessed location visits, or known item ownership/equipment
    look: str = ""  # an everyday thing's one line of how it looks (ops.Bring)
    life: Life | None = None  # an NPC's or a creature's body, once a fight has seeded it; None before
    weapon: WeaponState | None = None  # an item that is a weapon, once it was used in a fight
    fills: dict[str, int] = Field(default_factory=dict)  # numbers the keeper fixed for a sheet that lacked them (生命, 闪避 ...)
    luck: int | None = None  # an important NPC's Luck pool left (Keeper Rulebook p. 168); None: never drawn on


class Fact(Versioned):
    id: str  # a module fact's id is its path in the module: truth, npc:warden:secrets:0, clue:c_diary
    text: str
    kind: FactKind = "state"
    about: list[str] = Field(default_factory=list)  # entity ids
    origin: Origin = "game"
    true: bool = True  # a lie or a rumour records that it is said, not that it is so
    known_by: list[str] = Field(default_factory=list)  # NPC ids; PLAYERS once revealed
    revealed: int | None = None  # the turn it reached the table; frozen from then on
    reveal_source: str = ""  # clue:<id>, event:<id>, log:<turn>:<kind>:<index>, etc.; never inferred by overlap
    reveal_excerpt: str = ""  # the displayed words, retained after the public log is folded
    legacy_revealed: int | None = None  # an old, unverified reveal marker; not permission to disclose its text
    reveal_note: str = ""  # why an old marker could not be verified; visible to developer diagnostics
    explains: list[str] = Field(default_factory=list)  # clue ids this fact accounts for
    retracted: bool = False
    t: int = 0
    key: str = ""  # its GameState.world key, for a fact mirrored there (engine/world.py)


class Relation(Versioned):
    a: str
    b: str
    kind: str  # 信任 恐惧 欠情 敌对 盟友 亲属 雇佣 ... how a sees b
    value: int = 0  # -100..100
    note: str = ""
    known_by: list[str] = Field(default_factory=list)
    since: int = 0

    @property
    def key(self) -> str:
        return relation_key(self.a, self.b, self.kind)


def relation_key(a: str, b: str, kind: str) -> str:
    return f"{a}|{b}|{kind}"


class Promise(Versioned):
    id: str
    by: str  # who promised
    to: str
    text: str
    made: int = 0  # game minutes
    turn: int = 0
    due: int | None = None
    status: Literal["active", "fulfilled", "broken"] = "active"
    public: bool = True
    settled: int | None = None
    noted: bool = False  # its due time came and the parties were woken


class Thread(Versioned):
    """A line of tension: an open question, foreshadowing, a promise, a conflict, a plan in motion."""

    id: str
    kind: Literal["question", "foreshadow", "promise", "conflict", "plan"]
    title: str
    status: Literal["open", "closed"] = "open"
    opened: int = 0
    opened_turn: int = 0
    last: int = 0  # game minutes it last moved
    last_turn: int = 0
    entities: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    beats: list[str] = Field(default_factory=list)
    heat: int = 0
    resolution: str = ""


class Step(BaseModel):
    id: str = Field(description="步骤 id，英文小写加下划线")
    text: str = Field(description="这一步做什么，一句话")
    due: int | None = Field(None, description="最早在第几分钟做（游戏时间）；不限时留空")  # None: whenever its condition holds
    when: str = Field("", description="开始条件；& 表示且，| 表示或：flag:标记、!flag:标记、at:谁:地点、promise:约定:状态、"
                                       "owner:物件:持有者、knows:NPC:事实、after:分钟、gone:谁、hour:22-6（几点到几点）；"
                                       "状态为 active/fulfilled/broken；调查员集体用 players")
    completion: Literal["state", "report"] = Field("state", description="state：实际状态或执行证据核验完成；移动、交物等用此类。"
        "report：仅人物的定性工作完成报告，不产生外部结果；不得填 achieved 或 ops，完成时本人或主持角色用 step.note 具体说明该人物所做工作及判断。")
    achieved: str = Field("", description="完成后必须实际成立的条件，语法同 when；移动填 at:NPC:目的地，交物填 owner:物品:接收者；不能以打算或口头宣称代替")
    # Lands without a model call while the plan is fresh.
    ops: list[dict[str, Any]] = Field(default_factory=list, description="到时直接结算的操作，格式同操作；要到时再想就留空")
    meet: bool | None = Field(None, description="见到调查员时才做的事（他们来问、来访时怎样回应、领他们去看什么）填 true：这是人物资料，"
                                                "见面时由人物自己演，不在幕后想，due 和 when 只表示从什么时候起才这样；幕后要他自己去做的填 false")
    once: SkipJsonSchema[bool | None] = None  # the module's meeting reaction (Move.once): True once only, False its way every time
    status: SkipJsonSchema[Literal["pending", "done", "blocked", "dropped"]] = "pending"
    note: SkipJsonSchema[str] = ""
    woke: SkipJsonSchema[int | None] = None  # game minutes it last woke its NPC for a decision; the NPC's timer takes it from there
    started: SkipJsonSchema[int] = 0  # record seq when this plan was installed; completion needs a real outcome since then


class Memory(BaseModel):
    t: int
    turn: int
    text: str
    event: str = ""
    fact: str = ""
    stale: bool = False  # what it rested on was rewritten


class Agent(Versioned):
    """An NPC's persistent intentions and memory; storage is not a worker allocation."""

    npc: str
    goals: list[str] = Field(default_factory=list)
    goal_reason: str = ""
    goal_source: str = ""  # operation that established the current goals
    resources: list[str] = Field(default_factory=list)
    plan: list[Step] = Field(default_factory=list)
    reads: dict[str, int] = Field(default_factory=dict)  # versions the plan was made from
    memory: list[Memory] = Field(default_factory=list)
    mood: str = ""
    wake: int | None = None  # game minutes it wants to think again
    seen: int = 0  # record seq when it last decided
    acted: int = -1  # game minutes it last decided
    places: list[str] = Field(default_factory=list)  # places it knows besides the public ones and its own
    refused: str = ""  # why what it last meant to do could not be done; it hears this when it next wakes
    # Only the module's fixed steps (a time or a condition, and what they bring about) run; nothing
    # wakes it to decide (world/scheduler.py). Those the module gives an agenda but does not mark
    # important (agents.marked); the director enlisting one makes it decide again.
    passive: bool = False


class Event(BaseModel):
    """Something that happened. Several participants make it a hyperedge."""

    id: str
    seq: int
    t: int
    turn: int
    kind: str
    actor: str = ""
    participants: list[str] = Field(default_factory=list)
    place: str = ""
    text: str
    visibility: Literal["public", "private"] = "private"  # public: the entire party actually witnessed it
    witnesses: list[str] = Field(default_factory=list)  # actors, including investigator seats, who saw it then
    facts: list[str] = Field(default_factory=list)
    trace: str = ""  # what can still be noticed at the place afterwards
    op: str = ""
    version: int = 1
    reactive: bool = True  # ordinary greeting-only exchanges remain memories without waking autonomous decisions
    motions: dict[str, dict[str, str | int]] = Field(default_factory=dict)  # host-recorded actual travel intervals


class Scheduled(BaseModel):
    id: str
    at: int
    text: str
    actor: str = ""
    place: str = ""
    participants: list[str] = Field(default_factory=list)
    ops: list[dict[str, Any]] = Field(default_factory=list)
    reads: dict[str, int] = Field(default_factory=dict)
    status: Literal["pending", "done", "dropped"] = "pending"


class Revision(BaseModel):
    id: str
    seq: int
    t: int
    turn: int
    target: str  # fact:<id> or event:<id>
    old: str
    new: str
    old_version: int
    new_version: int
    reason: str
    by: str
    touched: list[str] = Field(default_factory=list)  # what was updated with it


class Entry(BaseModel):
    """The op journal: every settled op, its digest (so a repeat is a no-op) and how to undo it."""

    id: str
    seq: int
    kind: str
    digest: str
    turn: int
    t: int
    actor: str
    summary: str
    public: bool = False
    event: str = ""
    undo: list[dict[str, Any]] | None = None  # inverse JSON patch over the settled domain
    after: str = ""  # hash of the domain right after it; undo needs the same state
    undone: bool = False


class Request(BaseModel):
    """A director's backstage plan waiting to land (world/desk.py)."""

    id: str
    key: str
    kind: str
    reason: str = ""
    reads: dict[str, int] = Field(default_factory=dict)
    ops: list[dict[str, Any]] = Field(default_factory=list)
    turn: int = 0
    t: int = 0
    ttl: int = 3  # turns before it is too old to land
    status: Literal["pending", "landed", "stale", "expired", "dropped"] = "pending"
    note: str = ""


class Beat(BaseModel):
    """Backstage work that would change the scene in front of the investigators: an arrangement
    left unfinished for the keeper, played in the next telling (world/stage.py). It lands whole
    when played, if what it rested on still holds."""

    id: str
    actor: str  # whose decision, arrangement or plan step it is
    ops: list[dict[str, Any]] = Field(default_factory=list)
    involves: list[str] = Field(default_factory=list)  # the people and things on stage it touches
    leaving: list[str] = Field(default_factory=list)  # those it takes out of the scene (stage.leaves)
    reads: dict[str, int] = Field(default_factory=dict)  # versions the decision rested on: moved, it is decided again
    when: str = ""  # the plan step's condition, still to hold when it plays
    text: str = ""  # what it is, in the words of its decision or plan step
    t: int = 0  # game minutes it was decided at
    turn: int = 0
    status: Literal["waiting", "played", "dropped"] = "waiting"
    cue: Literal["auto", "play", "hold"] = "auto"  # keeper's scene timing, not a number of player inputs
    note: str = ""  # why it was dropped


class Sighting(BaseModel):
    place: str
    event: str  # the observation's source; never inferred from a later true position
    left: bool = False  # seen leaving this place, without learning the destination
    status: str = ""
    look: str = ""
    portrait: str = ""


class WorldRecord(BaseModel):
    seq: int = 0
    seeded: list[str] = Field(default_factory=list)  # module objects already in the record
    entities: dict[str, Entity] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)
    relations: dict[str, Relation] = Field(default_factory=dict)
    promises: dict[str, Promise] = Field(default_factory=dict)
    threads: dict[str, Thread] = Field(default_factory=dict)
    agents: dict[str, Agent] = Field(default_factory=dict)
    scheduled: dict[str, Scheduled] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)
    sightings: dict[str, dict[str, Sighting]] = Field(default_factory=dict)  # investigator -> NPC -> last observation
    revisions: list[Revision] = Field(default_factory=list)
    requests: dict[str, Request] = Field(default_factory=dict)
    ticks: list[str] = Field(default_factory=list)  # time advances already simulated
    simulated: int | None = None  # game minutes the world has been simulated up to (engine/game.py)
    reviewed_at: int | None = None  # shared backstage review, once per actual time boundary across scenes
    left: dict[str, int] = Field(default_factory=dict)  # place -> record seq when the party last left it
    journal: list[Entry] = Field(default_factory=list)
    npc_budget_turn: int = -1
    npc_budget_calls: int = 0
    npc_budget_limit: int = 0
    npc_work: dict[str, dict[str, Any]] = Field(default_factory=dict)  # interrupted scheduler batches, private save data
    beats: list[Beat] = Field(default_factory=list)  # arrangements left for the keeper to play (world/stage.py)
    # What the turn's action lacks and so cannot do (agents/cast.Ruling.missing): nobody brings it
    # out that turn (ops.Bring).
    missing: list[str] = Field(default_factory=list)
    missing_turn: int = -1
    # The obstacles on the world's ways that this game changed (a door forced or unlocked, a way
    # barricaded or cleared), by the stable id of the way (engine/encounter/site.way_key): a chase
    # there finds them so (site.build) until something changes them again. Only those that changed.
    obstacles: dict[str, ObstacleState] = Field(default_factory=dict)

    def event(self, event_id: str) -> Event | None:
        return next((e for e in reversed(self.events) if e.id == event_id), None)
