"""Game state: everything a save file needs, and nothing the model has to remember."""

from __future__ import annotations

import json
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from .. import __version__
from ..cards import Drawn
from ..rules.coc import Investigator
from ..world.record import WorldRecord
from .encounter.model import EncounterState, EncounterSummary
from .perception import Appearance

# The layout of a save file (GameState.save_format), raised when a save of the version before can no longer be
# read as it is. A save without it (0.0.3 and before) or with another one is refused whole (read_save): loading it
# would drop what this version does not know (0.0.3's `chase`, `pending_resolution`) and the next autosave would
# write it back without them. Old saves are not migrated; their files are left as they are.
SAVE_FORMAT = 1

LogKind = Literal["opening", "player", "speech", "keeper", "npc", "dice", "clue", "sanity", "damage", "event", "system", "handout", "ending"]


class LogEntry(BaseModel):
    turn: int
    time: str
    kind: LogKind
    who: str = ""
    text: str
    audience: list[str] | None = None  # None: public; []: keeper only; otherwise these investigator ids
    presented: dict[str, dict[str, str]] = Field(default_factory=dict)  # immutable delivery-time personal lines
    delivery: str = ""  # the same accepted telling in saved history and live delivery
    scene: str = ""  # stable source scene, never inferred from the viewer's latest place


class Deed(BaseModel):
    """A line of one investigator's own record, the sheet's 本局经历: what they did and rolled,
    the papers handed to them on their turn, what it cost them. Only what the table saw, so it
    goes to every screen at a shared table; and it is kept after the log is folded into memory.
    No clue: what they learnt is in the story and in their own notes, never under the module's
    title for it. A `clue` line comes only from a save made before 0.0.5, and is shown nowhere."""

    turn: int
    time: str
    who: str  # investigator id
    kind: Literal["act", "check", "clue", "handout", "sanity", "damage"]
    text: str
    audience: list[str] | None = None


DEEDS = {"player": "act", "dice": "check", "handout": "handout", "sanity": "sanity", "damage": "damage"}


class Progress(BaseModel):
    """The players' 故事至今 (engine/progress.py): the text and the turn it was written after;
    the turn it was last asked for (written or not), how many log lines had been read by then
    and what had turned up (progress.marks), to tell what is new since."""

    text: str = ""
    turn: int = 0
    asked: int = -1  # -1: never yet
    seen: int = 0  # GameState.logged when it was asked for
    marks: dict[str, int] = Field(default_factory=dict)


class NpcState(BaseModel):
    met: bool = False
    unlocked: list[str] = Field(default_factory=list)  # gated clues the NPC may now give
    lines: int = 0


class WorldChange(BaseModel):
    kind: Literal["fact", "relationship", "promise", "fulfill", "transfer"]
    key: str = Field(description="同一事实或约定沿用同一个简短 id")
    text: str = Field(description="已经实际发生或明确答应的事情；不是玩家的愿望或推测")
    npc: str = Field("", description="亲历此事的 NPC id；没有就留空")
    item: str = Field("", description="transfer 时填已存在的物件 id 或背包里的完整名称")
    owner: str = Field("", description="transfer 时填新持有者的调查员/NPC/地点 id")


class WorldFact(BaseModel):
    kind: str
    text: str
    who: str
    npc: str = ""
    status: Literal["active", "fulfilled", "broken"] = "active"
    turn: int
    source: str


class Mention(BaseModel):
    """How present a clue has been at the table, for the Idea roll's difficulty (engine/idea.py):
    when it first and last came up (game minutes), how many tellings carried its own words (the
    keeper's, an NPC's, an event's) and how many of the players' own inputs did. Its place and its
    NPC are no mention."""

    first: int = 0
    last: int = 0
    told: int = 0
    said: int = 0


class IdeaRoll(BaseModel):
    """One Idea roll of the party (灵感检定, Keeper Rulebook pp. 82, 167–169)."""

    turn: int
    day: int  # game day (minutes // 1440): one a day, unless the keeper offered it
    clue: str
    difficulty: str
    passed: bool
    offered: bool = False  # the keeper offered it when the game stalled (Game._nudge)


class CheckRecord(BaseModel):
    """A skill check, kept for a few turns: the arbiter reads them, and the engine does not
    roll the same failed attempt again (Game._earlier). A repeat that was not rolled counts
    as the latest attempt: turn, minutes and seen move up to it."""

    turn: int
    who: str  # investigator id
    at: str  # location id
    skill: str
    npc: str = ""
    target: str = ""
    text: str  # Check.describe(), after any push or luck
    passed: bool
    action: str = ""  # neutral attempt, so a new request is not mistaken for the same roll on the same NPC
    minutes: int = 0  # game clock when the attempt was over
    seen: int = 0  # clues + flags + events by then: a higher count later means something new came up
    bonus: int = 0  # bonus (+) or penalty (-) dice it was rolled with
    difficulty: str = ""  # 常规 / 困难 / 极难 it was rolled at; "" in older saves
    audience: list[str] | None = None  # the original result's readers; public dice do not grant its information
    roll_audience: list[str] | None = None  # optional table-public numbers, not the attempt or failure contract

    @property
    def aim(self) -> str:
        return self.npc or self.target


class SceneState(BaseModel):
    """One continuing scene's execution cursors, not a copy of the shared world."""

    id: str
    members: list[str]
    place: str
    at: int
    active: int = 0  # index in the canonical investigator roster
    turn: int = 0
    busy: bool = False
    closed: bool = False
    ongoing: str = ""
    journey: str = ""
    pending_turn: dict = Field(default_factory=dict)
    pending_push: dict | None = None
    encounter: EncounterState | None = None
    encounter_log: list[EncounterSummary] = Field(default_factory=list)
    round_spent: int | None = None
    round_elapsed: dict[str, int] = Field(default_factory=dict)
    round_plan: dict = Field(default_factory=dict)
    queued_actions: list[tuple[str, str]] = Field(default_factory=list)
    queued_receipts: list[dict] = Field(default_factory=list)
    held: list[dict] = Field(default_factory=list)
    aftercare_seconds: int = Field(0, ge=0, lt=60)
    day: int = 0
    transcripts: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)
    log: list[LogEntry] = Field(default_factory=list)
    private_history: list[LogEntry] = Field(default_factory=list)
    logged: int = 0
    memory: str = ""
    progress: Progress = Field(default_factory=Progress)
    progress_at: int = 0
    view: str = ""
    view_kind: str = ""
    sky: str = ""
    sky_at: int = 0
    usage: dict[str, dict[str, float]] = Field(default_factory=dict)
    turn_usage: list[dict[str, float]] = Field(default_factory=list)
    tally_at: dict[str, float] = Field(default_factory=dict)
    passed_until: int | None = None  # explicit time permission; -1 means until the next own declaration
    reservation: dict = Field(default_factory=dict)  # original action interval and resource dependencies
    notices: list[dict] = Field(default_factory=list)  # committed cross-scene observations awaiting this scene


class GameState(BaseModel):
    save_format: int = SAVE_FORMAT  # see SAVE_FORMAT; checked before a save file is read (read_save)
    module: str
    slot: str = "default"
    label: str = ""  # the player's own name for this save (继续调查 → 改名); the module title when empty
    investigators: list[Investigator]
    human: list[str] = Field(default_factory=list)  # investigator ids played by humans; the rest are AI
    active: int = 0
    location: str
    scenes: dict[str, SceneState] = Field(default_factory=dict)
    positions: dict[str, str] = Field(default_factory=dict)  # canonical physical PL positions when scenes are running
    next_scene: int = 0
    journey: str = ""  # intended destination when the player pauses along the way
    ongoing: str = ""  # what was going on as the last telling stopped (a chase, a stand-off): the keeper's scene (Game._narrate)
    visited: list[str] = Field(default_factory=list)
    heard: dict[str, str] = Field(default_factory=dict)  # places named in what the players saw: id -> the name heard (engine/fog.py)
    minutes: int = 0
    aftercare_seconds: int = Field(0, ge=0, lt=60)  # sub-minute aftercare rounds; old saves start at zero
    day: int = 0  # the game day the investigators' daily SAN count belongs to (minutes // 1440)
    clues: list[str] = Field(default_factory=list)
    clue_settled: dict[str, list[str]] = Field(default_factory=dict)  # clue -> investigators whose acquisition costs committed
    handouts: list[str] = Field(default_factory=list)
    presentations: list[dict] = Field(default_factory=list)  # addressed media snapshots; never a shared discovery
    private_messages: dict[str, dict] = Field(default_factory=dict)  # keeper correspondence receipts, keyed by seat/request
    presentation_issues: dict[str, dict] = Field(default_factory=dict)  # attempted media not delivered; next keeper can finish it
    flags: list[str] = Field(default_factory=list)
    fired: list[str] = Field(default_factory=list)
    event_attempts: dict[str, dict] = Field(default_factory=dict)  # {day, roll}: checked events, once per game day
    pending_story_flags: dict[str, list[str]] = Field(default_factory=dict)  # completion flags waiting for actual finds
    npcs: dict[str, NpcState] = Field(default_factory=dict)
    world: dict[str, WorldFact] = Field(default_factory=dict)
    item_owners: dict[str, str] = Field(default_factory=dict)
    # This game's world record (world/): entities, facts and who knows them, relations, promises,
    # threads, important NPCs and the event stream. Private at a LAN table (net/hub.py PRIVATE).
    record: WorldRecord = Field(default_factory=WorldRecord)
    pending_turn: dict = Field(default_factory=dict)  # serializable stage journal; retry without rolling twice
    unfinished: dict[str, dict] = Field(default_factory=dict)  # private intent; execution only resumes an already authorized wait
    last_actions: dict[str, int] = Field(default_factory=dict)  # last committed action segment, including a request's continuation
    # A LAN round's actions happen at once (net/hub.py _settle, Game.begin_round): the minutes the
    # longest of them took so far (None outside a round), and the reports held for the one telling.
    round_spent: int | None = None
    round_elapsed: dict[str, int] = Field(default_factory=dict)  # serial work per investigator inside the shared window
    round_plan: dict = Field(default_factory=dict)  # private batch plans and consumption receipts, not player-visible
    queued_actions: list[tuple[str, str]] = Field(default_factory=list)  # LAN declarations not yet assigned to a settled window
    queued_receipts: list[dict] = Field(default_factory=list)  # unstarted LAN input: seat, text and accepted game minute
    held: list[dict] = Field(default_factory=list)  # {"turn", "who", "report", "rest"}, told together (Game.narrate)
    # 0.0.4's fights and chases (engine/encounter/): the one going on, in either mode, and the last
    # finished ones (KEEP_ENCOUNTERS). Private at a LAN table: the players see its public view only.
    encounter: EncounterState | None = None
    encounter_log: list[EncounterSummary] = Field(default_factory=list)
    sightings: dict[str, list[str]] = Field(default_factory=dict)  # investigator -> horrors whose first sight they paid for
    identified: list[str] = Field(default_factory=list)  # creatures the players can name (their true name is shown)
    perceptions: dict[str, dict[str, Appearance]] = Field(default_factory=dict)  # observer -> real entity -> appearance
    perception_key: str = Field(default_factory=lambda: uuid.uuid4().hex)  # private signing key for seat-bound choices
    # investigator -> place, left behind by a chase (or gone off alone from there); named to the keeper
    # (world/context.keeper_brief) and the plan (Cast.bearings) until the party comes to that place or a ruling
    # brings them back (Ruling.rejoin: carried, fetched, walked back while they can act)
    apart: dict[str, str] = Field(default_factory=dict)
    # What a LAN client is sent of the encounter going on (engine/encounter/views.table): its public view and
    # each seat's choices. Only ever in a client's copy; the host's own is computed when it is needed.
    encounter_public: dict | None = None
    narrator_context_version: int = 0
    # Agent memory lives in the save, not in any backend: per-agent conversation windows
    # (keeper, npc:<id>, arbiter, investigator:<id>) and the summaries of older turns.
    transcripts: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)
    log: list[LogEntry] = Field(default_factory=list)
    private_history: list[LogEntry] = Field(default_factory=list)  # private or differently perceived originals folded out of log
    logged: int = 0  # lines ever added to the log, which keeps only the recent ones
    memory: str = ""  # chronicler's summary of everything before the recent log
    deeds: list[Deed] = Field(default_factory=list)  # each investigator's own record (the sheet's 本局经历)
    # The players' own notes (F2, tui/notes.py), investigator id -> the page, in a local game. Never sent to
    # a LAN table (net/hub.py PRIVATE): there each player keeps theirs on their own machine.
    notebook: dict[str, str] = Field(default_factory=dict)
    progress: Progress = Field(default_factory=Progress)  # the players' 故事至今, on the 进展 page
    prologue: str = ""  # the keeper's opening as the table heard it, on the 背景 page
    pending_push: dict | None = None
    checks: list[CheckRecord] = Field(default_factory=list)  # the latest skill checks, newest last
    developed: list[str] = Field(default_factory=list)  # investigators who made their growth rolls (.en)
    improv: list[dict] = Field(default_factory=list)  # the director's plans, applied again on load (module/improv.py)
    expanded: dict[str, int] = Field(default_factory=dict)  # place -> latest requested expansion, for diagnostics
    visuals: dict[str, dict[str, str]] = Field(default_factory=dict)  # per-save illustrations, reused after loading
    # art key -> what it shows since the story changed it; with `when`, a look's key -> its picture drawn ahead of the
    # foreshadowing step that brings it, shown once that flag is set (engine/visuals.py)
    looks: dict[str, dict[str, str]] = Field(default_factory=dict)
    shown: list[str] = Field(default_factory=list)  # big scenes whose moment has come, shown or not (engine/showcase.py)
    plugin_data: dict[str, dict] = Field(default_factory=dict)
    plugin_versions: dict[str, str] = Field(default_factory=dict)
    plugin_sources: dict[str, dict] = Field(default_factory=dict)
    plugin_receipts: list[str] = Field(default_factory=list)
    plugin_errors: dict[str, str] = Field(default_factory=dict)
    # Public, shareable at a LAN table (the hub keeps the rest of the plugin_* fields private):
    # a plugin's opt-in one-line status, and the last few public effects it produced this game.
    plugin_status: dict[str, str] = Field(default_factory=dict)
    plugin_log: list[dict] = Field(default_factory=list)
    plugin_panels: dict[str, list[dict]] = Field(default_factory=dict)  # public too: each plugin's panels, by name
    # Host only: open and waiting prompts (the hub sends each to its own player), timers, the
    # per-plugin rate marks, and a counter that keeps prompt ids and commands apart.
    plugin_prompts: list[dict] = Field(default_factory=list)
    plugin_timers: list[dict] = Field(default_factory=list)
    plugin_marks: dict[str, dict] = Field(default_factory=dict)
    plugin_serial: int = 0
    missed: dict[str, int] = Field(default_factory=dict)  # core clue -> failed attempts, from older saves (a failure no longer hands a clue over)
    drawn: list[Drawn] = Field(default_factory=list)  # keeper cards dealt: phobias, manias, bouts, mishaps (cards.py)
    # The Idea roll (engine/idea.py): how present each clue has been (counted after each turn from the
    # log, up to `mentions_at` lines ever logged), the rolls made, and the turn of the keeper's open
    # offer of one when the game stalled (0: none).
    mentions: dict[str, Mention] = Field(default_factory=dict)
    mentions_at: int = 0
    ideas: list[IdeaRoll] = Field(default_factory=list)
    idea_offer: int = 0
    progress_at: int = 0  # last turn that found a clue, set a flag, fired an event or reached a new place
    sky: str = ""  # outdoor weather from the keeper's narration: rain, storm, snow, fog
    view: str = ""  # the picture of this place the scene shows (engine/views.py); "" for its own
    view_kind: str = ""  # "" the place's picture for the hour, "focus" a view of what they look at, "moment" an event's
    signs: dict[str, int] = Field(default_factory=dict)  # foreshadow id -> signs of it the keeper was handed: its hint, then its stages (engine/signs.py)
    sky_at: int = 0  # game minute it was last spoken of; it passes after weather.LASTS
    ending: str | None = None
    epilogue: str = ""  # after the ending, what became of everyone: told once, on request (engine/debrief.py)
    turn: int = 0
    usage: dict[str, dict[str, float]] = Field(default_factory=dict)
    turn_usage: list[dict[str, float]] = Field(default_factory=list)  # what each turn cost (tally)
    tally_at: dict[str, float] = Field(default_factory=dict)  # the totals when the last turn was counted

    def model_post_init(self, _context) -> None:
        # A save from before the record and the count: start them from the log it has.
        self.logged = max(self.logged, len(self.log))
        if not self.deeds and self.log:
            acting = ""
            for e in self.log:
                acting = e.who if e.kind == "player" else acting
                self._deed(e, acting)

    @property
    def investigator(self) -> Investigator:
        return self.investigators[self.active]

    def find(self, inv_id: str) -> Investigator:
        for inv in self.investigators:
            if inv.id == inv_id:
                return inv
        raise KeyError(inv_id)

    @property
    def clock(self) -> str:
        return format_time(self.minutes)

    def add(self, kind: LogKind, text: str, who: str = "", *, audience: list[str] | None = None,
            presented: dict[str, dict[str, str]] | None = None, delivery: str = "") -> LogEntry:
        shown = {viewer: {"who": appearance.name or "未辨明的身影", "text": "受了伤。" if kind == "damage" else text}
                 for viewer, masks in self.perceptions.items() for target, appearance in masks.items()
                 if who and appearance.original_name == who}
        shown.update(presented or {})
        entry = LogEntry(turn=self.turn, time=self.clock, kind=kind, who=who, text=text,
                         audience=list(dict.fromkeys(audience)) if audience is not None else None, presented=shown,
                         delivery=delivery)
        self.log.append(entry)
        self.logged += 1
        self._deed(entry, self.investigator.name if 0 <= self.active < len(self.investigators) else "")
        return entry

    def _deed(self, e: LogEntry, acting: str) -> None:
        """A log line about one investigator goes into their record too: what was done and
        rolled under their name; a handout goes to `acting`, whose turn it was."""
        kind = DEEDS.get(e.kind)
        name = acting if e.kind == "handout" else e.who
        who = next((i.id for i in self.investigators if i.name == name), "") if kind else ""
        if not who:
            return
        self.deeds.append(Deed(turn=e.turn, time=e.time, who=who, kind=kind, text=e.text[:120],
                               audience=e.audience))  # type: ignore[arg-type]
        del self.deeds[:-KEEP_DEEDS]

    def npc(self, npc_id: str) -> NpcState:
        return self.npcs.setdefault(npc_id, NpcState())

    def recent_checks(self) -> list[CheckRecord]:
        """Checks of the last few turns: what the arbiter weighs a repeat against."""
        return [r for r in self.checks if self.turn - r.turn <= RECENT_TURNS]

    def tally(self) -> None:
        """What the turn just played cost, kept per turn for the usage view (usage holds the totals)."""
        total = {k: sum(u.get(k, 0) for u in self.usage.values()) for k in ("calls", "input", "cache_read", "output", "cost_usd")}
        before = self.tally_at or dict.fromkeys(total, 0)
        spent = {k: total[k] - before.get(k, 0) for k in total}
        if spent["calls"]:
            self.turn_usage = [*self.turn_usage, {"turn": self.turn, **spent}][-TURNS_KEPT:]
        self.tally_at = total

    def record_usage(self, role: str, reply) -> None:
        u = self.usage.setdefault(role, {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cost_usd": 0.0, "seconds": 0.0})
        usage = reply.usage or {}
        u["calls"] += 1
        u["input"] += usage.get("input_tokens", 0)
        u["cache_read"] += usage.get("cache_read", 0)
        u["output"] += usage.get("output_tokens", 0)
        u["cost_usd"] += reply.cost_usd or 0.0
        u["seconds"] += reply.seconds or 0.0


class SaveRefused(ValueError):
    """A save file of another save format (SAVE_FORMAT): not loaded, so never written over. Its words say so."""

    def __init__(self, found: object):
        self.found = found
        newer = type(found) is int and found > SAVE_FORMAT
        super().__init__(f"{'新' if newer else '旧'}版本存档，{__version__} 无法读取")


def check_save(raw: object) -> dict:
    """A save file's data, when this version reads it. SaveRefused for another save format (none: 0.0.3 and
    before); ValueError for what is no save at all."""
    if not isinstance(raw, dict) or "module" not in raw:
        raise ValueError("不是存档")
    if raw.get("save_format") != SAVE_FORMAT:
        raise SaveRefused(raw.get("save_format"))
    return raw


def read_save(text: str) -> GameState:
    """A save file's text as the game. Every way a save file is opened goes through here (the saves screen,
    a LAN room, `autokeeper host --load`): SaveRefused for another save format, ValueError for a broken file."""
    return GameState.model_validate(check_save(json.loads(text)))


TURNS_KEPT = 200  # per-turn usage rows a save keeps
KEEP_DEEDS = 300  # record lines a save keeps, the whole party's
RECENT_TURNS = 5  # how far back a check still counts as recent
KEEP_CHECKS = 12  # check records a save keeps
RECHECK_AFTER = 60  # game minutes after which the same attempt is no longer an immediate repeat


_TIME = re.compile(r"D(\d+)\s+(\d{1,2}):(\d{2})")


def parse_time(text: str) -> int:
    m = _TIME.fullmatch(text.strip())
    if not m:
        raise ValueError(f"时间格式应为“D1 21:00”：{text}")
    day, hour, minute = map(int, m.groups())
    return (day - 1) * 1440 + hour * 60 + minute


def hours(rest: str) -> tuple[int, int] | None:
    """hour:22-6, from ten at night to six in the morning; None when it is not that."""
    a, sep, b = rest.partition("-")
    if not (sep and a.isdecimal() and b.isdecimal()) or not (0 <= int(a) <= 24 and 0 <= int(b) <= 24) or int(a) == int(b):
        return None
    return int(a), int(b)


def within(rest: str, minutes: int) -> bool:
    """Whether the game time falls in hour:a-b (it may run past midnight)."""
    span = hours(rest)
    if span is None:
        return False
    a, b, h = span[0] % 24, span[1] % 24, (minutes // 60) % 24
    return a <= h < b if a < b else (h >= a or h < b)


def format_time(minutes: int) -> str:
    day, rest = divmod(minutes, 1440)
    return f"D{day + 1} {rest // 60:02d}:{rest % 60:02d}"


def time_of_day(minutes: int) -> str:
    hour = (minutes % 1440) // 60
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "晌午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "夜里"
    return "深夜"
