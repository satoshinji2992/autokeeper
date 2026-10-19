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

PLAYERS = "players"  # the table: what was told or shown to the investigators
DIRECTOR = "director"
KEEPER = "keeper"
ENGINE = "engine"

Origin = Literal["module", "improv", "game"]
FactKind = Literal["truth", "conclusion", "secret", "knows", "lie", "clue", "goal", "setting", "event", "relation", "promise", "state", "rumor"]


class Versioned(BaseModel):
    version: int = 1
    changed: int = 0  # record seq of the op that last changed it


class Entity(Versioned):
    id: str
    kind: Literal["npc", "location", "item", "investigator", "other"]
    name: str
    origin: Origin = "module"
    at: str = ""  # an NPC's or an item's place; an item's holder when someone has it
    status: str = ""  # "", 受伤, 失踪, 死亡 ...
    conditions: list[str] = Field(default_factory=list)


class Fact(Versioned):
    id: str  # a module fact's id is its path in the module: truth, npc:warden:secrets:0, clue:c_diary
    text: str
    kind: FactKind = "state"
    about: list[str] = Field(default_factory=list)  # entity ids
    origin: Origin = "game"
    true: bool = True  # a lie or a rumour records that it is said, not that it is so
    known_by: list[str] = Field(default_factory=list)  # NPC ids; PLAYERS once revealed
    revealed: int | None = None  # the turn it reached the table; frozen from then on
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
    id: str
    text: str
    due: int | None = None  # game minutes; None: whenever its condition holds
    when: str = ""  # condition (world/agents.holds); empty: none
    ops: list[dict[str, Any]] = Field(default_factory=list)  # lands without a model call while the plan is fresh
    status: Literal["pending", "done", "blocked", "dropped"] = "pending"
    note: str = ""
    woke: int | None = None  # game minutes it last woke its NPC for a decision; the NPC's timer takes it from there


class Memory(BaseModel):
    t: int
    turn: int
    text: str
    event: str = ""
    fact: str = ""
    stale: bool = False  # what it rested on was rewritten


class Agent(Versioned):
    """An important NPC who acts on its own (3–6 a game)."""

    npc: str
    goals: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    plan: list[Step] = Field(default_factory=list)
    reads: dict[str, int] = Field(default_factory=dict)  # versions the plan was made from
    memory: list[Memory] = Field(default_factory=list)
    mood: str = ""
    wake: int | None = None  # game minutes it wants to think again
    seen: int = 0  # record seq when it last decided
    acted: int = -1  # game minutes it last decided
    places: list[str] = Field(default_factory=list)  # places it knows besides the public ones and its own


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
    visibility: Literal["public", "private"] = "private"
    witnesses: list[str] = Field(default_factory=list)  # NPC ids who saw it
    facts: list[str] = Field(default_factory=list)
    trace: str = ""  # what can still be noticed at the place afterwards
    op: str = ""
    version: int = 1


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
    revisions: list[Revision] = Field(default_factory=list)
    requests: dict[str, Request] = Field(default_factory=dict)
    ticks: list[str] = Field(default_factory=list)  # time advances already simulated
    journal: list[Entry] = Field(default_factory=list)

    def event(self, event_id: str) -> Event | None:
        return next((e for e in reversed(self.events) if e.id == event_id), None)
