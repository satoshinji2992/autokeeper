"""Game state: everything a save file needs, and nothing the model has to remember."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..rules.coc import Investigator

LogKind = Literal["opening", "player", "keeper", "npc", "dice", "clue", "sanity", "damage", "event", "system", "handout", "ending"]


class LogEntry(BaseModel):
    turn: int
    time: str
    kind: LogKind
    who: str = ""
    text: str


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
    status: Literal["active", "fulfilled"] = "active"
    turn: int
    source: str


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
    minutes: int = 0  # game clock when the attempt was over
    seen: int = 0  # clues + flags + events by then: a higher count later means something new came up

    @property
    def aim(self) -> str:
        return self.npc or self.target


class GameState(BaseModel):
    module: str
    slot: str = "default"
    investigators: list[Investigator]
    human: list[str] = Field(default_factory=list)  # investigator ids played by humans; the rest are AI
    active: int = 0
    location: str
    journey: str = ""  # intended destination when the player pauses along the way
    visited: list[str] = Field(default_factory=list)
    heard: dict[str, str] = Field(default_factory=dict)  # places named in what the players saw: id -> the name heard (engine/fog.py)
    minutes: int = 0
    clues: list[str] = Field(default_factory=list)
    handouts: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    fired: list[str] = Field(default_factory=list)
    npcs: dict[str, NpcState] = Field(default_factory=dict)
    world: dict[str, WorldFact] = Field(default_factory=dict)
    item_owners: dict[str, str] = Field(default_factory=dict)
    pending_turn: dict = Field(default_factory=dict)  # serializable stage journal; retry without rolling twice
    narrator_context_version: int = 0
    # Agent memory lives in the save, not in any backend: per-agent conversation windows
    # (keeper, npc:<id>, arbiter, investigator:<id>) and the summaries of older turns.
    transcripts: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)
    log: list[LogEntry] = Field(default_factory=list)
    memory: str = ""  # chronicler's summary of everything before the recent log
    pending_push: dict | None = None
    checks: list[CheckRecord] = Field(default_factory=list)  # the latest skill checks, newest last
    developed: list[str] = Field(default_factory=list)  # investigators who made their growth rolls (.en)
    improv: list[dict] = Field(default_factory=list)  # the director's plans, applied again on load (module/improv.py)
    visuals: dict[str, dict[str, str]] = Field(default_factory=dict)  # per-save illustrations, reused after loading
    looks: dict[str, dict[str, str]] = Field(default_factory=dict)  # art key -> what it shows since the story changed it (engine/visuals.py)
    plugin_data: dict[str, dict] = Field(default_factory=dict)
    plugin_versions: dict[str, str] = Field(default_factory=dict)
    plugin_sources: dict[str, dict] = Field(default_factory=dict)
    plugin_receipts: list[str] = Field(default_factory=list)
    plugin_errors: dict[str, str] = Field(default_factory=dict)
    missed: dict[str, int] = Field(default_factory=dict)  # core clue -> failed attempts at it (Game._fail_forward)
    progress_at: int = 0  # last turn that found a clue, set a flag, fired an event or reached a new place
    sky: str = ""  # outdoor weather from the keeper's narration: rain, storm, snow, fog
    sky_at: int = 0  # game minute it was last spoken of; it passes after weather.LASTS
    ending: str | None = None
    epilogue: str = ""  # after the ending, what became of everyone: told once, on request (engine/debrief.py)
    turn: int = 0
    usage: dict[str, dict[str, float]] = Field(default_factory=dict)
    turn_usage: list[dict[str, float]] = Field(default_factory=list)  # what each turn cost (tally)
    tally_at: dict[str, float] = Field(default_factory=dict)  # the totals when the last turn was counted

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

    def add(self, kind: LogKind, text: str, who: str = "") -> LogEntry:
        entry = LogEntry(turn=self.turn, time=self.clock, kind=kind, who=who, text=text)
        self.log.append(entry)
        return entry

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


TURNS_KEPT = 200  # per-turn usage rows a save keeps
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
