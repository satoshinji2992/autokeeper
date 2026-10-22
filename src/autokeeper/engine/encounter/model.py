"""A fight or a chase going on: what the engine keeps of it, in the save (GameState.encounter).

One `EncounterState` holds both modes, so a fight that turns into a chase and back is the same
encounter with the same wounds, ammunition and spent actions. It keeps only what is the
encounter's own: who is in it and on which side, the round and whose turn it is, what each has
declared and what came of it (`ActionReceipt`), the fight's engagements and cover, the chase's
site, the choice a player is being asked, the stakes and the clock. Bodies and things are the
world's and are never copied here: an investigator's HP is on the sheet, an NPC's or a creature's
in the world record (world/record.Life), a gun's rounds on its item (world/record.WeaponState).
The encounter names them by id.

Nothing changes it in place. Every change is an `EncounterChange` inside one world op
(world/ops.EncounterStep), together with the effects on people and things (engine/effects.py),
so a unit of the fight lands whole or not at all. The changes are absolute sets, not deltas
(the participant as it now is, the cursor where it now is), so the op journal reads plainly and
a change applied twice is the same as once; `apply` is what each one means.

Every number that may be "not known yet" is None, never 0: 0 is a real round, a real speed, a
real number of rounds left (the zero-value review of 2026-10-21).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ...module.schema import Stake
from ..effects import Effect
from .plan import (
    DeclaredAction,
    Fill,
    Mode,
    NpcArrangement,
    ObstacleState,
    Reaction,
    Side,
    SiteEdge,
    SiteNode,
)

Presence = Literal["in", "fled", "escaped", "surrendered", "captured", "left"]
Phase = Literal["open", "aftermath", "ended"]
KEEP_ENCOUNTERS = 20  # finished encounters a save keeps (GameState.encounter_log)


class Edge(BaseModel):
    """Bonus (+) or penalty (-) dice waiting for someone's next roll: a companion's help, a dive."""

    dice: int
    why: str
    source: str  # who or what gave it
    until: Literal["next_roll", "round_end", "freed"]


class Participant(BaseModel):
    id: str  # investigator, NPC or creature entity id; HP and items are never copied here
    kind: Literal["investigator", "npc", "creature"]
    side: Side
    controller: Literal["human", "ai", "keeper"]
    joined_round: int = 0
    presence: Presence = "in"
    node: str = ""  # the site node they are at (a chase); in a fight, the node the fight is at
    speed: int | None = None  # this encounter's MOV after the speed roll; None until rolled; kept across mode switches
    surprised: bool = False
    react: Reaction | None = None  # this round's preset answer to an attack; None: not chosen
    ask: Literal["always", "major", "never"] = "major"  # a human's: when to stop and ask for a reaction
    asked: bool = False  # a reaction prompt was shown to them in this encounter
    arrangement: NpcArrangement | None = None  # a keeper participant's, from the keeper plan
    arranged_round: int | None = None  # the round that arrangement was made in
    edges: list[Edge] = Field(default_factory=list)


class RoundBudget(BaseModel):
    """What this round has used up, by participant id."""

    responses: dict[str, int] = Field(default_factory=dict)  # melee responses made (outnumbered past the allowance)
    acted: list[str] = Field(default_factory=list)
    lost_next: dict[str, str] = Field(default_factory=dict)  # a forfeited next action and why: 扑向掩体, 爬起来
    movement: dict[str, int] = Field(default_factory=dict)  # a chase's movement actions left
    owed: dict[str, int] = Field(default_factory=dict)  # a chase's delay carried into the next round
    attacks_made: dict[str, int] = Field(default_factory=dict)
    shots: dict[str, int] = Field(default_factory=dict)


# -- a fight's ground ---------------------------------------------------------------------------
class Engagement(BaseModel):
    """Two people in melee with each other; it lasts across rounds until one breaks away."""

    a: str
    b: str
    since: int  # round


class Cover(BaseModel):
    who: str
    what: str  # 翻倒的桌子, 墓碑
    partial: bool = True
    since: int


class Hold(BaseModel):
    """`held` is held fast by `by` (a 制住 maneuver)."""

    held: str
    by: str
    since: int


class FightScene(BaseModel):
    engaged: list[Engagement] = Field(default_factory=list)
    cover: list[Cover] = Field(default_factory=list)
    holds: list[Hold] = Field(default_factory=list)
    prone: list[str] = Field(default_factory=list)
    distance: dict[str, int] = Field(default_factory=dict)  # "a|b" -> yards, filled by the keeper when shooting at range


# -- a chase's ground ---------------------------------------------------------------------------
class ChaseSite(BaseModel):
    """The graph a chase runs on: from the module's routes, the map, or a keeper's draft. Each chase
    has its own copy, so its broken doors stay broken for it alone."""

    nodes: list[SiteNode]
    edges: list[SiteEdge]
    obstacles: dict[str, ObstacleState] = Field(default_factory=dict)
    origin: str  # the node it starts at
    source: Literal["module", "location_graph", "keeper"]


class ChaseState(BaseModel):
    site: ChaseSite
    quarry: list[str]
    pursuers: list[str]
    goals: dict[str, str] = Field(default_factory=dict)  # quarry -> the node they make for (the keeper's)
    lost_sight: list[str] = Field(default_factory=list)  # "pursuer|quarry"
    started_round: int


# -- what was declared and what came of it -------------------------------------------------------
class RollRecord(BaseModel):
    purpose: str  # attack / dodge / fight_back / cover / damage / con_major / con_dying / speed / hazard / first_aid ...
    who: str
    skill: str = ""
    target: int | None = None  # the value rolled against; None for a damage roll
    difficulty: str = "常规"
    bonus: int = 0
    value: int | None = None  # the d100 it settled on; None for a damage roll
    level: str = ""
    expr: str = ""  # a damage roll's BCDice command
    total: int | None = None
    view: dict = Field(default_factory=dict)  # the dice tray's replay of this throw (Game._check_view)


class ReactionReceipt(BaseModel):
    """How a defender answered one attack (`attack`: its index in the receipt's attacks)."""

    attack: int
    attacker: str
    defender: str
    options: list[Reaction]
    chosen: Reaction | None = None
    by: Literal["preset", "player", "keeper", "rule"] = "preset"
    asked: bool = False


class ActionReceipt(BaseModel):
    """One declared action, from its declaration to what came of it. A pause for a player's
    reaction (`awaiting_reaction`) comes before any die of the attack is thrown."""

    id: str  # f"{enc.id}:r{round}:{actor}:{n}", the prefix of its units' op ids
    round: int
    actor: str
    source: Literal["button", "words", "keeper", "ai", "event", "table"]
    declared: DeclaredAction
    summary: str  # neutral, never the player's raw words
    ruling: dict | None = None  # the arbiter's Ruling (JSON) for an `other` action or an unlisted method
    phase: Literal["declared", "awaiting_reaction", "committed", "lapsed", "cancelled"] = "declared"
    reactions: list[ReactionReceipt] = Field(default_factory=list)
    rolls: list[RollRecord] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    lapsed: str = ""  # why it could not land (the missing condition)
    lines: list[str] = Field(default_factory=list)  # keeper-form lines with {id} refs, rendered per audience
    settled: int = 0  # attacks of `declared` settled so far (a multi-attack creature makes them all on its slot)
    prompted: bool = False  # the player throwing their own dice was shown the roll before its first die
    moved: bool = False  # a chase: its running is done (before its attack, or after it)


# -- a choice a player is being asked ------------------------------------------------------------
class ChoiceOption(BaseModel):
    id: str
    label: str
    note: str = ""
    value: dict = Field(default_factory=dict)  # the DeclaredAction or the reaction it stands for


class PendingChoice(BaseModel):
    id: str  # f"{receipt}:react:{k}" | f"{enc}:declare:{who}:{round}" | f"{receipt}:roll"
    kind: Literal["reaction", "redeclare", "roll", "declare"]
    who: str
    receipt: str = ""
    prompt: str
    options: list[ChoiceOption]
    default: str  # the option id taken when nobody answers
    deadline: float | None = None
    free_text: bool = True


# -- outcome and time ----------------------------------------------------------------------------
class StakeState(Stake):
    """A stake in play: who set it, and whether it has fired (it fires once)."""

    source: Literal["module", "keeper"] = "keeper"
    fired: bool = False


class EncounterClock(BaseModel):
    pace: int | None = None  # seconds a round takes in the story; None until the keeper sets it
    elapsed: int = 0  # seconds
    committed: int = 0  # whole minutes due to the world clock
    applied: int = 0  # whole minutes already added to it


class Change(BaseModel):
    """A material change: one is a reason to arrange the keeper's people again."""

    round: int
    kind: Literal["down", "joined", "fled", "surrendered", "disarmed", "switched", "reached", "freed"]
    who: str


class EndRecord(BaseModel):
    round: int
    how: Literal["won", "lost", "fled", "escaped", "caught", "parley", "surrender", "dispersed", "aftermath_done"]
    per: dict[str, str] = Field(default_factory=dict)  # participant -> how it ended for them
    places: dict[str, str] = Field(default_factory=dict)  # participant -> the world place they ended at
    minutes: int = 0
    text: str = ""


class EncounterState(BaseModel):
    id: str
    rules: str = "coc7e/0.0.4"
    mode: Mode
    phase: Phase = "open"
    place: str  # the world location it began at
    reason: str = ""
    started_turn: int
    participants: list[Participant]
    round: int = 0
    order: list[str] = Field(default_factory=list)  # this round's DEX order
    cursor: int = 0  # index into order
    budget: RoundBudget = Field(default_factory=RoundBudget)
    scene: FightScene = Field(default_factory=FightScene)
    chase: ChaseState | None = None
    receipts: list[ActionReceipt] = Field(default_factory=list)  # the current and the previous round in full
    history: list[str] = Field(default_factory=list)  # older rounds, compact
    pending: PendingChoice | None = None
    stakes: list[StakeState] = Field(default_factory=list)
    fills: list[Fill] = Field(default_factory=list)
    clock: EncounterClock = Field(default_factory=EncounterClock)
    changes: list[Change] = Field(default_factory=list)
    switches: list[str] = Field(default_factory=list)  # mode switches, "r3:chase"
    ended: EndRecord | None = None

    def get(self, pid: str) -> Participant | None:
        return next((p for p in self.participants if p.id == pid), None)

    def receipt(self, rid: str) -> ActionReceipt | None:
        return next((r for r in self.receipts if r.id == rid), None)


class EncounterSummary(BaseModel):
    """A finished encounter, as the save keeps it (GameState.encounter_log, the last 20)."""

    id: str
    mode: Mode  # the mode it ended in
    place: str
    reason: str = ""
    started_turn: int
    rounds: int
    participants: list[str] = Field(default_factory=list)
    ended: EndRecord | None = None
    history: list[str] = Field(default_factory=list)


# -- changes: absolute sets, in one op ---------------------------------------------------------------
class Begin(BaseModel):
    kind: Literal["begin"] = "begin"
    state: EncounterState


class SetParticipant(BaseModel):
    """The participant as they now are: replaces the one with their id, or joins them."""

    kind: Literal["participant"] = "participant"
    participant: Participant


class SetReceipt(BaseModel):
    """The receipt as it now is: replaces the one with its id, or adds it."""

    kind: Literal["receipt"] = "receipt"
    receipt: ActionReceipt


class SetRound(BaseModel):
    kind: Literal["round"] = "round"
    round: int
    order: list[str]
    cursor: int = 0
    budget: RoundBudget = Field(default_factory=RoundBudget)


class SetCursor(BaseModel):
    kind: Literal["cursor"] = "cursor"
    cursor: int


class SetBudget(BaseModel):
    kind: Literal["budget"] = "budget"
    budget: RoundBudget


class SetScene(BaseModel):
    kind: Literal["scene"] = "scene"
    scene: FightScene


class SetChase(BaseModel):
    kind: Literal["chase"] = "chase"
    chase: ChaseState | None


class SetPending(BaseModel):
    kind: Literal["pending"] = "pending"
    pending: PendingChoice | None


class SetMode(BaseModel):
    """Fight to chase or back; `switch` ("r3:chase") is noted once in `switches`."""

    kind: Literal["mode"] = "mode"
    mode: Mode
    switch: str = ""


class SetPhase(BaseModel):
    """Open to aftermath: no opponent can act, but someone is dying or a foe is coming back."""

    kind: Literal["phase"] = "phase"
    phase: Literal["open", "aftermath"]


class SetStake(BaseModel):
    kind: Literal["stake"] = "stake"
    index: int
    fired: bool


class AddStakes(BaseModel):
    """New stakes; one already there (the same `when` and flags) is not added twice."""

    kind: Literal["add_stakes"] = "add_stakes"
    stakes: list[StakeState]


class SetFills(BaseModel):
    kind: Literal["fills"] = "fills"
    fills: list[Fill]


class AddChanges(BaseModel):
    """Material changes noted; one already noted is not noted twice."""

    kind: Literal["add_changes"] = "add_changes"
    changes: list[Change]


class Compact(BaseModel):
    """Older rounds folded: the history as it now is, and the ids of the receipts kept in full."""

    kind: Literal["compact"] = "compact"
    history: list[str]
    receipts: list[str]


class SetClock(BaseModel):
    kind: Literal["clock"] = "clock"
    clock: EncounterClock


class Close(BaseModel):
    """It is over: the record is kept, nothing more is asked; it stays until archived."""

    kind: Literal["close"] = "close"
    record: EndRecord


class Archive(BaseModel):
    """Out of GameState.encounter into GameState.encounter_log, as `summary`."""

    kind: Literal["archive"] = "archive"
    summary: EncounterSummary


EncounterChange = Annotated[
    Begin | SetParticipant | SetReceipt | SetRound | SetCursor | SetBudget | SetScene | SetChase | SetPending
    | SetMode | SetPhase | SetStake | AddStakes | SetFills | AddChanges | Compact | SetClock | Close | Archive,
    Field(discriminator="kind"),
]


def apply(enc: EncounterState | None, changes: list[EncounterChange]) -> EncounterState | None:
    """The encounter after `changes`, in order, on a copy; None after an Archive (whoever settles the
    op files the Archive's summary into GameState.encounter_log). Applying the same changes again
    to the result gives the same result. ValueError for a change with no encounter to change."""
    out = enc.model_copy(deep=True) if enc is not None else None
    for change in changes:
        if isinstance(change, Begin):
            out = change.state.model_copy(deep=True)
            continue
        if isinstance(change, Archive):
            out = None
            continue
        if out is None:
            raise ValueError(f"没有进行中的遭遇，不能 {change.kind}")
        _apply(out, change)
    return out


def _apply(enc: EncounterState, c) -> None:
    match c:
        case SetParticipant(participant=p):
            enc.participants = _put(enc.participants, p.model_copy(deep=True))
        case SetReceipt(receipt=r):
            enc.receipts = _put(enc.receipts, r.model_copy(deep=True))
        case SetRound():
            enc.round, enc.order, enc.cursor, enc.budget = c.round, list(c.order), c.cursor, c.budget.model_copy(deep=True)
        case SetCursor():
            enc.cursor = c.cursor
        case SetBudget():
            enc.budget = c.budget.model_copy(deep=True)
        case SetScene():
            enc.scene = c.scene.model_copy(deep=True)
        case SetChase():
            enc.chase = c.chase.model_copy(deep=True) if c.chase else None
        case SetPending():
            enc.pending = c.pending.model_copy(deep=True) if c.pending else None
        case SetMode():
            enc.mode = c.mode
            if c.switch and c.switch not in enc.switches:
                enc.switches.append(c.switch)
        case SetPhase():
            enc.phase = c.phase
        case SetStake():
            if not 0 <= c.index < len(enc.stakes):
                raise ValueError(f"遭遇 {enc.id} 没有第 {c.index} 条后果")
            enc.stakes[c.index].fired = c.fired
        case AddStakes():
            for s in c.stakes:
                if not any(x.when == s.when and x.flags == s.flags for x in enc.stakes):
                    enc.stakes.append(s.model_copy(deep=True))
        case SetFills():
            enc.fills = [f.model_copy() for f in c.fills]
        case AddChanges():
            enc.changes += [x for x in c.changes if x not in enc.changes]
        case Compact():
            enc.history = list(c.history)
            enc.receipts = [r for r in enc.receipts if r.id in c.receipts]
        case SetClock():
            enc.clock = c.clock.model_copy()
        case Close():
            enc.ended, enc.phase, enc.pending = c.record.model_copy(deep=True), "ended", None
        case _:  # pragma: no cover - the union is closed
            raise ValueError(f"不认识的遭遇变化：{c!r}")


def _put[T: BaseModel](items: list[T], item: T) -> list[T]:
    """`items` with the one of the same id replaced by `item`, or `item` added at the end."""
    ids = [x.id for x in items]  # type: ignore[attr-defined]
    if item.id in ids:  # type: ignore[attr-defined]
        return [item if x.id == item.id else x for x in items]  # type: ignore[attr-defined]
    return [*items, item]
