"""One game: deterministic resolution around the agents.

The engine owns the facts: dice (BCDice), which clues are found, time, events,
sanity and endings. Agents interpret intentions, propose actions and narrate results.
The engine validates and commits world operations before their outcomes enter the story.
"""

from __future__ import annotations

import asyncio
import heapq
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from functools import partial
from itertools import pairwise
from typing import Any

from pydantic import TypeAdapter

from .. import cards, pictures, weather
from ..agents.cast import (
    Action,
    Cast,
    Failure,
    IdeaFailure,
    Initiative,
    PartyInput,
    PartyPlan,
    Plan,
    Rejoin,
    Ruling,
    kp_line,
)
from ..agents.director import Improv
from ..harness.models import refused
from ..harness.replay import CassetteError
from ..module import graph as module_graph
from ..module import improv
from ..module.schema import Event, Item, Module
from ..plugins.engine import Manager as PluginManager
from ..rules import commands, wounds
from ..rules.bcdice import Level, Roll
from ..rules.catalog import UnknownSkill, canonical
from ..rules.coc import DIFFICULTY_ZH, Check, Investigator, Rules, Sanity
from ..world import adapters, desk, knowledge, scene, scheduler, stage
from ..world import context as world_view
from ..world.agents import CastBrain, holds, marked, split_when
from ..world.ops import Equip, Follow, Improvise, Learn, Move, Occur, SetFlag
from ..world.record import DIRECTOR, KEEPER
from ..world.store import World, digest
from . import audience as recipients
from . import diagnostics, effects, fog, idea, perception, presentation, progress, signs, views, world
from .encounter.runner import Encounters
from .encounter.telling import tell
from .showcase import Showcases
from .state import (
    KEEP_CHECKS,
    RECHECK_AFTER,
    CheckRecord,
    Deed,
    GameState,
    LogEntry,
    Progress,
    format_time,
    parse_time,
    time_of_day,
)
from .visuals import Visuals

Emit = Callable[[dict[str, Any]], Awaitable[None]]


async def _silent(_: dict[str, Any]) -> None:
    return None


@dataclass
class Said:
    """One NPC reply of a turn as it landed (Game._reply), kept with the turn: its words, the
    unlocked clues it told (Dialogue.told) and how far its settling got (shown on the screens,
    those clues in the notebook, with the stages of that in `journal`). A turn resumed after a
    crash goes on from it: the NPC is not asked again, and nothing it said or told is lost."""

    npc: str
    line: str
    told: list[str] = field(default_factory=list)
    shown: bool = False
    heard: bool = False
    journal: dict = field(default_factory=dict)
    media: list[dict] = field(default_factory=list)
    audience: list[str] | None = None
    communication: str = "local"


@dataclass
class Turn:
    action: Action | None = None
    ruling: Ruling | None = None
    checks: list[Check] = field(default_factory=list)
    primary: int | None = None  # the action's own final check in checks; event gates never replace it
    rest: dict | None = None  # this actor's executed rest interval, not the shared clock increment
    interval: dict = field(default_factory=dict)  # host-owned actor, actual start/end, participants and travel endpoints
    clues: list[str] = field(default_factory=list)
    sanity: list[tuple[str, Sanity]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    told: bool = False  # the opening: its events are the keeper's to tell, woven into one telling, not shown first
    arrived: str | None = None
    first_visit: bool = False
    route: list[str] = field(default_factory=list)
    npc_lines: list[tuple[str, str]] = field(default_factory=list)
    joined: list[str] = field(default_factory=list)  # of those, who spoke of their own accord (Game._join_in)
    performance: tuple[str, str] | None = None
    performed: list[str] = field(default_factory=list)
    push: dict | None = None
    earlier: CheckRecord | None = None  # a repeated attempt: this earlier check stands, nothing is rolled
    notes: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)  # a rule plugin's one line the keeper must weave in
    foreshadow: list[str] = field(default_factory=list)  # Module.foreshadow ids of the place they came to (Game._foreshadow)
    world: list[str] = field(default_factory=list)  # what the rest of the world did in front of the party (world/)
    ending: str | None = None
    interlude: bool = False  # no action of theirs: the director's scene comes in between turns
    fight: list[str] = field(default_factory=list)  # a fight's or a chase's round as settled, for the keeper (engine/encounter)
    declared: bool = False  # a LAN round's order, settled with the others' by the last of them
    replies: dict[str, Said] = field(default_factory=dict)  # each NPC reply's receipt, by what asked for it (Game._reply)
    voices: list[str] = field(default_factory=list)  # NPCs the keeper plan has speak in this round of an encounter (Game.talk)
    narrating: dict = field(default_factory=dict)  # the stages narrate settles before the keeper speaks (Game._foreshadow)

    def absorb(self, later: Turn) -> None:
        """What another stage of the same turn settled (the replies before the ruling, the push
        choice): its rolls, finds, events, lines and notes join this turn's, so the NPCs on stage
        and the keeper's report have them."""
        for source, checked in enumerate(later.checks):
            # Luck changes the outcome of the same throw. Keep its original dice and one final
            # result, rather than leaving NPCs and the keeper with the pre-choice failure.
            index = self.primary if checked.luck_spent and source == later.primary else None
            if index is None:
                index = len(self.checks)
                self.checks.append(checked)
            else:
                self.checks[index] = checked
            if source == later.primary:
                self.primary = index
        for name in ("sanity", "npc_lines", "joined", "notes", "hints", "world", "fight"):
            getattr(self, name).extend(getattr(later, name))
        self.voices += [x for x in later.voices if x not in self.voices]
        for key, said in later.replies.items():
            self.replies.setdefault(key, said)
        self.clues += [c for c in later.clues if c not in self.clues]
        self.events += [e for e in later.events if all(e.id != x.id for x in self.events)]
        self.performed += [e for e in later.performed if e not in self.performed]
        self.performance = later.performance or self.performance
        self.rest = later.rest or self.rest
        if later.interval:
            if self.interval and self.interval.get("actor") == later.interval.get("actor"):
                self.interval["end"] = max(self.interval["end"], later.interval["end"])
                self.interval["participants"] = list(dict.fromkeys([
                    *self.interval["participants"], *later.interval["participants"]]))
                self.interval["destination"] = later.interval.get("destination", self.interval.get("destination", ""))
            elif not self.interval:
                self.interval = dict(later.interval)
        if later.arrived:  # an event carried them off
            self.arrived, self.first_visit, self.route = later.arrived, later.first_visit, later.route
        self.ending = self.ending or later.ending


TURN_RECORD = TypeAdapter(Turn)
CHECK_DATA = TypeAdapter(Check)


@dataclass
class Backstage:
    """A director's plan still being written after its turn moved on."""

    kind: str
    task: asyncio.Task
    at: str  # where the investigators were when it was asked for
    turn: int = 0


class PreparationError(RuntimeError):
    """A scene could not be prepared this time: no plan passed its checks, or its first picture
    did not come. Game._ready only retries recoverable service or picture failures."""


@dataclass
class Told:
    """The latest narration, kept so it can be told again: what the keeper was given (the
    report and that turn's lore), and what telling it left behind (its log entry, and the
    sky as it was before, since the words set the weather)."""

    report: str
    lore: str
    entry: LogEntry
    sky: tuple[str, int]
    memory: str | None = None


class _ResolutionDice:
    """Keep completed tool results when a later die/table in the same stage fails."""

    def __init__(self, game: Game, turn: Turn, source: Any, row: dict):
        self.game, self.turn, self.source, self.row = game, turn, source, row
        self.index = 0

    def __getattr__(self, name: str) -> Any:
        if name not in ("check", "roll", "total", "table"):
            return getattr(self.source, name)

        def invoke(*args: Any, **kwargs: Any) -> Any:
            calls = self.row.setdefault("tools", [])
            request = {"method": name, "args": list(args), "kwargs": kwargs}
            index, self.index = self.index, self.index + 1
            if index < len(calls):
                saved = calls[index]
                if saved["request"] != request:
                    raise ValueError("恢复中的骰子参数发生变化，未重新掷骰")
                return Roll(**saved["value"]) if saved["roll"] else saved["value"]
            result = getattr(self.source, name)(*args, **kwargs)
            calls.append({"request": request, "roll": isinstance(result, Roll),
                          "value": asdict(result) if isinstance(result, Roll) else result})
            self.game._resolution_save(self.turn)
            return result

        return invoke


class Game(Encounters):
    def __init__(self, module: Module, state: GameState, cast: Cast, rules: Rules, emit: Emit | None = None):
        self.m, self.state, self.cast, self.rules = module, state, cast, rules
        pictures.adopt(module)  # what was found or drawn for the module on this machine (pictures.py)
        self.emit: Emit = emit or _silent
        self.public_rolls = getattr(getattr(cast, "cfg", None), "public_rolls", False) is True
        self.checkpoint: Callable[[], None] | None = None
        self.turn_checkpoint: Callable[[Turn], None] | None = None
        self._resolution: dict | None = None
        self._resolution_scope: list[str] = []
        self._resolution_keep: Callable[[Turn], None] | None = None  # a journal kept with its turn (Game._journal)
        self._resolution_lock = asyncio.Lock()
        self._firing_events: set[str] = set()
        if state.improv:  # a loaded game brings its own branches
            improv.restore(module, state.improv)
        # This game's world record (world/): who is where, who knows what, promises, important
        # NPCs with plans of their own. Made from the module, then written back into this game's
        # copy of it, so a loaded game finds everyone where the record left them.
        self.world = World(module, state)
        for op in marked(self.world):  # the NPCs the module marks important act on their own
            self.world.apply(op)
        cast.world = self.world
        self.brain = self._brain(cast)
        self._here = self._present()  # who the screens show in the scene
        self._status = {n.id: n.status for n in module.npcs}  # how each NPC is, for their picture
        # Optional future preparations may run in the background. A current action or
        # opening always waits for the complete, validated scene, independent of this limit.
        self.director_wait: float | None = None
        self.backstage: list[Backstage] = []
        self._late = Turn()  # what landed from backstage, told in the next narration
        self._walked_receipts: set[str] = set()  # restored visit results already joined to this instance's _late
        self.debug_world: list[scheduler.Report] = []  # recent scheduling decisions, including refused operations
        self.told: Told | None = None  # the latest narration, while it can still be told again (retell)
        self.holding = False  # a round's action that is not its last: its telling waits for the others (narrate)
        if not state.pending_turn and not self.pending_party():  # unfinished batches retain their shared time ledger
            state.round_spent = None
            state.round_elapsed.clear()
            state.round_plan.clear()
        # The players' 故事至今 (engine/progress.py), written in the background when the story
        # has moved. Off in autoplay and replays, where nobody reads it; a screen and the Hub
        # turn it on.
        self.write_progress = False
        self._recounting: asyncio.Task | None = None
        # A screen sets this: the rest of the world (important NPCs deciding on their own) lives
        # after a turn is told, not while the player waits for it. What they did comes in an
        # interlude or the next narration. Replaying a real session, it was two decisions a
        # turn and 12.8 s of every turn's model time, most of them "wait". Autoplay and replays
        # leave it off, so their turns run as before.
        self.world_later = False
        self._world_owed = False
        self._world_task: asyncio.Task | None = None
        self._dealing: set[str] = set()  # who the investigators dealt with this turn: their beats wait (world/stage.py)
        self.initiative: list[Initiative] = []  # who here the keeper's plan has join in this turn (Cast.plan, talk)
        self._directed: set[str] = set()  # those it has join in: the world does not decide for them too this turn
        self._hosted_turn: int | None = self._hosted_at()  # the accepted plan also covers the people who stay quiet
        self.prelude_played = False  # a screen played the opening performance: no big picture over the keeper's first words
        self._moved_on = False  # the party went somewhere else since the world last lived (scheduler.advance)
        self.listener: Emit = self.emit  # the screen's own: background work outlives a turn's stream
        self.visuals = Visuals(module, state, getattr(cast, "cfg", None), self.emit)
        self.showcases = Showcases(module, state, getattr(cast, "cfg", None), self.emit, self.visuals.folder,
                                   self.visuals.idle, self.visuals.limit)  # big scenes: prepared ahead, the opening first
        self.plugins = PluginManager(self)
        diagnostics.attach(self)
        self.visuals.schedule()
        self.showcases.prepare()

    async def close(self) -> None:
        """Stop work owned by this game before the model sessions and screen disappear."""
        for pending in self.backstage:
            pending.task.cancel()
        recounting = [self._recounting] if self._recounting else []
        for task in recounting:
            task.cancel()
        if self._world_task is not None:
            self._world_task.cancel()
            await asyncio.gather(self._world_task, return_exceptions=True)
            self._world_task = None
        await asyncio.gather(*(p.task for p in self.backstage), *recounting, return_exceptions=True)
        self.backstage.clear()
        self._recounting = None
        await self.visuals.close()
        await self.showcases.close()
        await diagnostics.close(self)

    def _brain(self, cast: Cast) -> CastBrain | None:
        """The world model chooses important NPCs' autonomous actions within the shared allowance."""
        cfg = getattr(cast, "cfg", None)
        minds = getattr(cfg, "minds", 0) if cfg is not None else 0
        self.minds = scheduler.Budget(turn=minds, skip=2 * minds)
        return CastBrain(cast, self.state) if minds and hasattr(cast, "sessions") else None

    def _debug(self, kind: str, data: dict) -> None:
        """Private evidence must never enter the player/LAN event stream."""
        if journal := getattr(self, "debug_journal", None):
            journal.append(kind, data, turn=self.state.turn)

    async def replace_cast(self, cast: Cast) -> None:
        """A model switch also replaces the game's outstanding background work."""
        await self.close()
        self.cast = cast
        self.public_rolls = getattr(getattr(cast, "cfg", None), "public_rolls", False) is True
        cast.world = self.world
        self.brain = self._brain(cast)
        self.visuals.cfg = self.showcases.cfg = getattr(cast, "cfg", None)
        diagnostics.attach(self)
        self.visuals.tasks.clear()
        self.visuals.schedule()
        self.showcases.tried.clear()
        self.showcases.prepare()

    @property
    def improvising(self) -> bool:
        return bool(getattr(self.cast, "improvising", False))

    # -- setup ----------------------------------------------------------------------
    @staticmethod
    def new(module: Module, investigators: list[Investigator], *, slot: str = "default", human: list[str] | None = None) -> GameState:
        for inv in investigators:
            fog.validate_familiar(module, inv)
        state = GameState(
            module=module.id,
            slot=slot,
            investigators=investigators,
            human=human if human is not None else [investigators[0].id],
            location=module.start,
            visited=[module.start],
            minutes=parse_time(module.start_time),
        )
        state.day = state.minutes // 1440
        for inv in state.investigators:  # the 1/5-of-SAN-in-a-day count starts now
            inv.new_day()
        return state

    async def prepare_opening(self) -> improv.Applied | None:
        """Adapt custom identities; authored pregens already have prepared connections.

        Resume an existing adaptation receipt before introducing any of its new people.
        The keeper still receives every investigator's background for the opening itself.
        """
        st = self.state
        if not st.pending_turn:
            st.pending_turn.update(input="/opening", opening=True)
            if self.checkpoint:
                self.checkpoint()
        adapting = st.pending_turn.get("scene_package", {}).get("kind") == "开局改编"
        authored = {inv.id: inv for inv in self.m.pregens}
        identity = ("name", "occupation", "backstory", "known_locations")
        custom = any(
            inv.id not in authored or any(getattr(inv, key) != getattr(authored[inv.id], key) for key in identity)
            for inv in st.investigators
        )
        if adapting or (custom and not st.improv):
            return await self._improvise("开局改编", required=True)
        return None

    async def start(self, on_delta=None) -> str:
        st, m = self.state, self.m
        if not st.pending_turn:
            st.pending_turn.update(input="/opening", opening=True)
        self.visuals.pause()
        if self.checkpoint:
            self.checkpoint()
        adapted = await self.prepare_opening()
        opening = Turn(told=True)
        await self.plugins.run("start", opening)
        await self._arrive(st.location, opening)
        loc = m.location(st.location)
        await self._time(opening)
        if adapted is not None:
            await self._flags(adapted.now, opening)
        party = "\n".join(
            f"- {i.name}（{i.occupation}）：" + ("；".join(f"{k}：{v}" for k, v in i.backstory.items() if k != "说明" and v) or "未写背景")
            for i in st.investigators
        )
        report = (
            f"【开场｜{st.clock}｜{loc.name}】\n年代与地方：{m.era}，{m.setting}\n调查员：\n{party}\n"
            f"开场钩子（玩家已经知道）：{m.hook.strip()}\n"
            f"【开场素材（尚未朗读，融入叙事，不要先照抄再重讲）】\n{m.opening.strip()}\n"
            f"【进入地点】\n{self._public_location(loc.id)}\n"
        )
        if adapted is not None and adapted.note:
            report += f"导演为这几位调查员改编了模组：{adapted.note}\n"
        happened = [e.read_aloud.strip() or e.title for e in opening.events]
        if happened:
            report += "【开场时发生的事（尚未展示给玩家，融入同一段叙事，按先后自然带出，不逐件罗列）】\n" + "\n".join(happened) + "\n"
        report += "".join(f"{line}\n" for line in self._found(opening))  # a rule's find at the start (plugins "start")
        report += (
            "请用 180–320 字、两到四个自然段引入故事（有玩家篇幅偏好时以偏好为准）："
            "先用来信、旅途、职业或已写的关系自然交代为何而来，再让调查员看见眼前的生活和地方。"
            "不要把钩子念成任务简报，不要一开口就列调查方向或制造恐怖。"
            "原作开场中已写明的人数、物件数量与必发事实必须保留；只有一位调查员时称‘你’，不要凭空加队友。"
            f"叙事停在当前地点“{loc.name}”能接话或行动的一刻，之后由玩家决定去哪、是否停留。"
            "到达别处、见到委托人、进屋或开始调查，若尚未发生就不能替玩家完成；也不能擅自编造调查员的经历与心情。"
            "人物背景是各自的资料，不代表队友已经知情；私下来意、关系和随身物件不要在共同开场公开。"
            "需要交代个人已知的事，用 asides 只发给本人；共同正文只写在场者能观察到的情境，不替玩家出示或分享物品。"
        )
        self.showcases.prepare()  # (already at it when the game was made inside a running loop)
        if not self.prelude_played:  # after the prelude the keeper's first words are not covered by a picture
            await self.showcases.fire("opening")  # the big picture of the opening, the narration streaming behind it
        text = await self._narrate(report, on_delta, lore=self._brief())
        st.prologue = text  # the 背景 page keeps it after the log has moved on
        st.progress.marks = progress.marks(st)  # milestones count from here
        st.heard = fog.heard(m, st)  # the places the opening named reach the screens at a shared table
        self.world.sync()  # what the opening told is out
        st.pending_turn.clear()
        if self.checkpoint:
            self.checkpoint()
        self.visuals.resume()
        return text

    # -- dice commands ------------------------------------------------------------------
    async def command(self, text: str) -> commands.CommandResult:
        """A dice-bot command answered at once, without using up a turn. A check (.ra) is not
        one of them: it is an action like any other, ruled by the keeper (graph.which sends it
        through parse). Nothing here changes the investigator behind the keeper's back: sanity
        checks are the keeper's call, the sheet changes only in play, growth waits for the end."""
        st = self.state
        inv = st.investigator
        what = commands.kind(text)
        if what == "check" and commands.asked(text):
            result = _refused("检定要在回合里交给守秘人裁定。")
        elif what == "sanity":
            result = _refused("理智检定由守秘人发起：调查员撞见可怕的东西时，系统会替你掷。")
        elif what == "set" and text.strip()[3:].strip():
            result = _refused("游戏中不能改人物卡，技能和属性只随检定、受伤和成长变化。只打 .st 可以看人物卡。")
        elif what == "develop" and not st.ending:
            result = _refused("成长检定要等这局结束：结局出来以后再打 .en。")
        elif what == "develop" and inv.id in st.developed:
            result = _refused(f"{inv.name}这局的成长检定已经做过了。")
        else:
            # Growth covers the skills ticked in play: naming others does not add them.
            result = commands.run(".en" if what == "develop" else text, inv, self.rules)
            if what == "develop":
                st.developed.append(inv.id)
            if result.kind in ("roll", "develop"):  # things that happened at the table; lookups do not count
                st.add("dice", result.text, who=inv.name)
        await self.emit({"type": "command", "kind": result.kind, "text": result.text, "who": inv.name})
        await self.emit({"type": "sheet"})
        return result

    # -- the turn pipeline --------------------------------------------------------------
    def _focus_blocked(self) -> str:
        """Changing the acting seat cannot leave an unfinished individual turn behind."""
        st = self.state
        if self.world.place(st.investigator.id) == st.location:
            return ""
        if st.pending_push or set(st.pending_turn) - {"input", "active", "by_hand"}:
            return "上一回合尚未讲完，请先继续原回合，再切换到异地调查员。"
        return ""

    def _focus(self) -> None:
        """Anchor the ordinary turn to its investigator, without moving anyone or visiting."""
        st = self.state
        here = self.world.place(st.investigator.id)
        if here == st.location:
            return
        if why := self._focus_blocked():
            raise ValueError(why)
        places = {i.id: self.world.place(i.id) for i in st.investigators}
        st.location = here
        st.apart = {who: place for who, place in places.items() if place != here}
        st.ongoing, st.journey = "", ""
        st.view, st.view_kind = views.default(self.m, self.m.location(here), st.minutes), ""
        self._here = self._present()

    async def parse(self, text: str) -> Action:
        await self._world_settled()  # finish work at the preceding focus before changing its context
        self._focus()
        self._bind_party_declaration(text)
        self.initiative, self._directed = [], set()  # a new turn: nobody arranged yet, a question to the keeper too
        self._hosted_turn = None
        if text.startswith("/kp "):
            question = text[4:].strip()
            self.state.add("system", f"对守秘人：{question}", who=self.state.investigator.name)
            await self.emit({"type": "status", "text": "守秘人正在回应…"})
            return Action(kind="meta", summary=question)
        if idea.asked(text) and (why := self.idea_refused()):  # said why at once; no turn is spent
            await self.emit({"type": "command", "kind": "refused", "text": why, "who": self.state.investigator.name})
            return Action(kind="meta", summary="灵感检定", idea={"refused": why})
        self.visuals.schedule()  # resumes unfinished illustration work after loading
        self.showcases.settle()  # someone acts: the opening's big picture, if still on its way, is not shown now
        self.showcases.prepare()
        st = self.state
        resumed_at = st.pending_turn.get("arrangement", {}).get("at")
        if resumed_at is not None:
            st.turn = resumed_at  # a saved arrangement is inside this same parse, before graph.parse's action receipt
        else:
            await self._curtain(Action(kind="other", summary=text))
            st.turn += 1
        continuing = st.pending_turn.get("request", {}).get("part", 0) > 0
        if not continuing and text.strip() != "/encounter" and not any(e.turn == st.turn and e.kind == "player" for e in st.log):
            from .perception import input_label

            entry = st.add("player", input_label(self, st.investigator.id, text), who=st.investigator.name,
                           audience=[st.investigator.id])
            entry.time = format_time(self._actor_cursor())
        if idea.asked(text):  # the Idea roll: the engine's own action, no model reads it
            return self.idea_action()
        await self.emit({"type": "status", "text": "解析行动…"})
        if self.battling():  # in a fight or a chase: a button's words, or the keeper plan's reading (engine/encounter)
            return await self.encounter_input(text)
        action = await self._arrange(text)
        # What a dice command asks for is read off the command, never taken from the model.
        action = action.model_copy(update={"asked": None if continuing else commands.asked(text), "echo": ""})
        # Meaning, destination and pace belong to the keeper's reading of the whole input.
        # A place-name regex cannot distinguish a rejected destination from the next stop,
        # or a route description from a direct journey. Resolution still validates the route.
        if (action.kind == "daily" and action.npc and not action.approach and not action.destination
                and re.fullmatch(r"[，。！？、\s]*(早上好|早安|你好|您好|午安|晚安|谢谢|多谢|辛苦了|今天忙不忙|平常这个时候忙不忙)[，。！？、\s]*", action.speech)):
            action = action.model_copy(update={"casual": True})
        if action.kind == "daily":
            st.progress_at = st.turn  # do not suggest the player is stuck while choosing everyday life
        return action

    async def _arrange(self, text: str, *, continuation: dict | None = None) -> Action:
        """The keeper's plan of the turn (Cast.plan), or the parser's action when there is no plan to
        be had: a cast without one, a failed call, a reply that is no Plan. Who joins in is held to
        those it may be (here, alive, the module's: _present), by their ids alone, and kept for
        talk(), in the turn's journal too, so a resumed turn still has it."""
        st = self.state
        receipt = (continuation if continuation is not None else
                   st.pending_turn.setdefault("arrangement", {}) if st.pending_turn else {})
        ids = f"plan:{st.turn}:prepared" if continuation is not None else f"plan:{st.turn}"
        planner = getattr(self.cast, "plan", None)
        if continuation is not None and planner is None:
            raise PreparationError("当前主持接口缺少新场面的行动交接，尚未裁定或执行剩余行动")
        batched = self._party_step(text) if continuation is None and not receipt.get("plan") else None
        if planner is not None or batched is not None or receipt.get("plan"):
            try:
                if receipt.get("plan"):
                    plan = Plan.model_validate(receipt["plan"])
                elif batched is not None:
                    plan = Plan.model_validate(batched["plan"])
                    if st.pending_turn:
                        st.pending_turn["party_token"] = batched["token"]
                elif _takes(planner, "check"):
                    plan = await planner(st, text, check=lambda p: self._planned_scene(p, ids=ids).problems)
                else:
                    plan = await planner(st, text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the parser still reads the player's words
                self._debug("plan", {"phase": "fallback", "error": f"{type(exc).__name__}: {exc}"[:300]})
                if continuation is not None:
                    raise PreparationError("新场面的剩余行动尚未安排完成，原行动与已完成步骤已保留") from exc
            else:
                if "units" not in receipt:
                    prepared = self._planned_scene(plan, ids=ids)
                    receipt.update(at=st.turn, plan=plan.model_dump(mode="json"), units=[u.dump() for u in prepared.held],
                                   problems=prepared.problems)
                    if batched is not None and batched["token"] not in st.round_plan.setdefault("used", []):
                        st.round_plan["used"].append(batched["token"])
                    if receipt["units"] and self.checkpoint:
                        self.checkpoint()
                if not receipt.get("landed"):
                    units = [scene.Unit.load(u) for u in receipt["units"]]
                    landed = {u.key for u in scene.land(self.world, units)} if units else set()
                    receipt["problems"].extend(f"{'；'.join(u.said) or u.key}未落实，按当前记录继续。"
                                               for u in units if u.key not in landed)
                    receipt["landed"] = True
                    if plan.people:
                        self.visuals.schedule()
                    if units and self.checkpoint:
                        self.checkpoint()
                if plan.beats and plan.action.kind != "meta":
                    await self._cue_scene(plan, receipt)
                    if not st.pending_turn and "cue_turn" in receipt:
                        self._late.absorb(TURN_RECORD.validate_python(receipt["cue_turn"]))
                if not st.pending_turn:
                    self._late.notes.extend(f"现场登记未落实：{p}" for p in receipt["problems"])
                here, kept = set(self._present()), {}
                for x in plan.initiative:
                    if x.npc in here and x.npc not in kept and x.does.strip():
                        kept[x.npc] = x
                self.initiative = list(kept.values())
                self._directed = {x.npc for x in self.initiative}  # the world does not decide for them this turn
                if st.pending_turn:
                    st.pending_turn["initiative"] = [x.model_dump() for x in self.initiative]
                    # A fight or a chase this turn begins (engine/encounter/runner._start_from), kept for resolve.
                    hint = getattr(plan, "encounter", None)
                    st.pending_turn["encounter_hint"] = hint.model_dump(mode="json", by_alias=True) if hint else None
                self._debug("plan", {"phase": "planned", "initiative": [x.model_dump() for x in plan.initiative],
                                     "kept": [x.npc for x in self.initiative], "registration": receipt})
                self._hosted_turn = st.turn
                if plan.remaining.strip() and st.pending_turn and "request" not in st.pending_turn:
                    st.pending_turn["request"] = {
                        "id": f"{st.investigator.id}:{st.turn}", "actor": st.investigator.id,
                        "original": text, "origin_turn": st.turn, "part": 0, "started": self._actor_cursor(),
                        "remaining": plan.remaining.strip(), "completed": [],
                    }
                # The keeper read this person's old note together with the new input. Its
                # adopted plan replaces it; only an explicitly retained tail stays pending.
                st.unfinished.pop(st.investigator.id, None)
                return plan.action
        return await self.cast.intent(st, text)

    async def _cue_scene(self, plan: Plan, receipt: dict) -> None:
        """An explicit interruption changes the scene before the rules assistant reads it.
        Its operations, consequences and notices use the ordinary durable stage journal."""
        journal = receipt.setdefault("cues", {})
        receipt.setdefault("cue_present", self._present())
        turn = TURN_RECORD.validate_python(receipt["cue_turn"]) if "cue_turn" in receipt else Turn()

        def keep(kept: Turn) -> None:
            receipt["cue_turn"] = TURN_RECORD.dump_python(kept, mode="json")
            if self.checkpoint:
                self.checkpoint()

        with self._journal(journal, keep):
            for choice in plan.beats:
                def cue(choice=choice) -> dict:
                    stage.cue(self.world, choice.id, choice.action, choice.reason)
                    beat = next((b for b in stage.waiting(self.world) if b.id == choice.id), None)
                    if choice.action != "play" or beat is None:
                        return {"flags": [], "shown": []}
                    since, flags = self.world.rec.seq, set(self.state.flags)
                    problems = stage.play(self.world, beat)
                    if problems:
                        turn.notes.append(f"待演安排未落实：{choice.id}，{'；'.join(problems)}")
                        shown = []
                    else:
                        shown = world_view.happened(self.world, since)
                        turn.world.extend(shown)
                    return {"flags": [flag for flag in self.state.flags if flag not in flags], "shown": shown}

                result = await self._resolution_step(f"cue:{choice.id}", turn, cue)
                if result["shown"] and choice.text.strip():
                    await self._resolution_step(f"cue-notice:{choice.id}", turn, lambda choice=choice: self.emit({
                        "type": "event", "id": f"cue:{choice.id}", "title": "", "text": choice.text.strip(),
                        "audience": recipients.listeners(self.state, self.state.investigator.id),
                    }))
                if flags := result["flags"]:
                    await self._resolution_step(f"cue-flags:{choice.id}", turn,
                                                lambda flags=flags: self._flags(flags, turn, landed=True))

    def _planned_scene(self, plan: Plan, *, ids: str = "") -> scene.Plan:
        """Register the established cast before ruling, without handing over things or resolving
        anybody's attempted action. Real moves still use the scene's route and time checks."""
        if plan.action.kind == "meta":
            return scene.Plan()
        prepared = scene.Plan()
        actors = {i.id for i in self.state.investigators} | {n.id for n in self.m.npcs} | {p.id for p in plan.people}
        references = [plan.action.npc, *plan.action.companions, *(step.npc for step in plan.action.before)]
        for ident in dict.fromkeys(ref for ref in references if ref):
            if ident not in actors:
                prepared.problems.append(f"行动中的人物 id {ident} 未登记；使用本轮资料里的原 id，不能另拼或按称呼猜 id。")
        if plan.action.destination and plan.action.destination not in {loc.id for loc in self.m.locations}:
            prepared.problems.append(f"目的地 id {plan.action.destination} 未登记；已有地点使用原 id，"
                                     "需要新去处时 destination 留空，在 summary 保留完整目的，交裁定与即兴准备。")
        if plan.present or plan.people:
            missing_contact = {p.id for p in plan.people if plan.action.communication == "remote"
                               and p.id == plan.action.npc and not p.location}
            here = list(dict.fromkeys([*self._present(), *plan.present,
                                      *(p.id for p in plan.people if p.id not in missing_contact
                                        and (not p.location or p.location == self.state.location))]))
            raw = scene.Scene(people=[p for p in plan.people if p.id not in missing_contact], here=here)
            cursor = self._actor_cursor()
            prepared = scene.plan(self.world, raw, scene.Context(travel=self._travel, walk=False,
                                                               ids=ids or f"plan:{self.state.turn}",
                                                               interval=(cursor, cursor)))
            prepared.problems += [f"远程联系人 {who} 须登记实际所在的已有location；不能默认放在调查员眼前。"
                                  for who in missing_contact]
        introduced = {p.id for p in plan.people}
        for ident in plan.present:
            if ident not in introduced and (not (e := self.world.entity(ident)) or e.kind != "npc"):
                prepared.problems.append(f"{ident} 不是已登记的 NPC；需要持续记住的新人物写进 people。")
        pending = {b.id for b in stage.waiting(self.world)}
        for choice in plan.beats:
            if choice.id not in pending:
                prepared.problems.append(f"{choice.id} 不是当前待演安排，不能重新执行或改动已完成的安排。")
        return prepared

    async def rule(self, action: Action, *, prepared: bool = False) -> Ruling | None:
        if not prepared and (saved := self.state.pending_turn.get("prepared_action")):
            return await self._continue_prepared(action, improv.Applied(**saved["applied"]))
        if action.idea is not None:  # the Idea roll: INT at the difficulty the engine set (engine/idea.py)
            if action.idea.get("refused"):
                return None
            level = action.idea["difficulty"]
            basis = idea.BASIS.get(level, "")
            return Ruling(needs_roll=True, skill="灵感", difficulty=level, reason=basis, why=basis)
        if action.kind in ("meta",):
            return None
        if action.declared is not None:  # in an encounter: the rules settle it, the arbiter only something else
            ruled = self.encounter_ruling(action)
            if ruled is not None:
                return ruled
            return await self._encounter_arbiter(action)
        hint = self.state.pending_turn.get("encounter_hint") if self.state.pending_turn else None
        if hint and hint.get("trigger", "now") == "now":  # the action is the fight or the run itself
            return Ruling(needs_roll=False, reason="遭遇开始")
        await self._curtain(action)  # finish a scene this action would otherwise overtake
        if self.state.pending_turn.get("preparation", {}).get("blocked"):
            return Ruling(needs_roll=False, reason="出发前的交谈或同行尚未确认")
        if action.kind == "move" and action.destination and not action.arrival_after_success:
            me = self.state.investigator
            if me.id in self.state.apart and action.destination == self.state.location:  # back to the others on foot
                way = self._travel(self.state.apart[me.id], action.destination) or 0
                return Ruling(needs_roll=False, minutes=way, rejoin=[Rejoin(who=me.id, how="walked")], reason="走回大家身边")
            return Ruling(needs_roll=False, minutes=0, reason="移动")
        if self._stop_on_route(action) and not action.arrival_after_success:
            return Ruling(needs_roll=False, minutes=0, reason="先走到沿途地点，之后由玩家决定是否继续")
        if (action.kind in ("daily", "talk") and action.casual and action.npc and not action.asked
                and not action.destination and not action.approach
                and any(n.id == action.npc for n in self.m.npcs_at(self.state.location))):
            return Ruling(needs_roll=False, minutes=0, reason="普通闲聊，不必另作裁定")
        heading = self._heading(action)
        cued_present = self.state.pending_turn.get("arrangement", {}).get("cue_present", [])
        if (self._addressed(action) and action.npc in cued_present
                and action.npc not in self.world.present(heading or self.state.location)):
            return Ruling(needs_roll=False, missing=[f"{self.world.name(action.npc)}不在当前交谈现场"],
                          reason="交谈对象已不在现场，本次尚未完成的请求不能按原场面继续检定；已说过的话与实际变化仍成立")
        await self.emit({"type": "status", "text": "裁定中…"})
        journal = self.state.pending_turn
        draft = journal.get("draft_ruling", {})
        arbiter = self.cast.arbiter
        kwargs = {"coordination": self._party_context()} if _takes(arbiter, "coordination") else {}
        ruling = (Ruling.model_validate(draft["ruling"]) if draft.get("action") == action.ruled()
                  else await arbiter(self.state, action, at=heading, **kwargs))
        ruling = await self.checked_skill(action, ruling, at=heading, **kwargs)
        if ruling.scope != "模组内" and not prepared:  # the module is silent here: the director fills the gap
            if journal:
                journal["draft_ruling"] = {"action": action.ruled(), "ruling": ruling.model_dump(mode="json")}
                if self.checkpoint:
                    self.checkpoint()
            kind = "展开" if ruling.scope == "可即兴" else "后果"
            applied = await self._improvise(kind, action=action, ruling=ruling, at=heading or self.state.location,
                                           required=True)
            if applied is not None:
                gated = any(self.m.clue(cid).check or (self.m.npc(self.m.clue(cid).npc).gates.get(cid)
                                                      if self.m.clue(cid).npc else None) for cid in applied.give)
                if gated or applied.go:
                    return await self._continue_prepared(action, applied)
                ruling = self._steer(ruling, applied)
        return self._no_reroll(action, ruling, heading or self.state.location)

    async def checked_skill(self, action: Action, ruling: Ruling, **context: Any) -> Ruling:
        """Resolve aliases before an offer; an unrecognised roll is corrected once, never rolled at 1%."""
        if not ruling.needs_roll or ruling.repeat or not ruling.skill.strip():
            return ruling
        try:
            self.state.investigator.value(ruling.skill)
            return ruling
        except UnknownSkill as exc:
            problem = str(exc)
        journal = self.state.pending_turn
        key = action.ruled()
        saved = journal.get("skill_correction", {})
        if saved.get("action") == key:
            corrected = Ruling.model_validate(saved["ruling"])
        else:
            arbiter = self.cast.arbiter
            if not _takes(arbiter, "problems"):
                raise PreparationError(problem + "；原行动已保留，尚未掷骰")
            corrected = await arbiter(self.state, action, problems=[problem], **context)
            if journal:
                journal["skill_correction"] = {"action": key, "ruling": corrected.model_dump(mode="json")}
                if self.checkpoint:
                    self.checkpoint()
        if corrected.needs_roll and not corrected.repeat:
            try:
                self.state.investigator.value(corrected.skill)
            except UnknownSkill as exc:
                raise PreparationError(str(exc) + "；原行动已保留，尚未掷骰") from exc
        return corrected

    async def _continue_prepared(self, action: Action, applied: improv.Applied) -> Ruling | None:
        """A new stage needs an action, not a check assembled from its first clue. The same keeper
        reads the original request and completed prefix, then rules the actual remainder once.
        Keep both decisions so a retry neither repeats preparation nor reuses an obsolete target.
        """
        st = self.state
        saved = st.pending_turn.setdefault("prepared_action", {"applied": asdict(applied)}) if st.pending_turn else {}
        if "action" not in saved:
            package = st.pending_turn.get("scene_package", {}).get("plan")
            package = package or (st.improv[-1]["plan"] if st.improv else {})
            preparation = st.pending_turn.get("preparation", {}).get("turn", {})
            request = st.pending_turn.get("input") or action.summary
            prompt = (f"【本轮原始请求】{request}\n【已准备的新场面，仅供主持】\n"
                      + json.dumps(package, ensure_ascii=False)
                      + "\n【已经完成的出发前步骤，不能重演】\n" + json.dumps(preparation, ensure_ascii=False)
                      + "\n只安排原请求尚未执行的部分，明确当前交谈对象、目的地和公开请求。准备好资料不等于已经赶路、"
                      "问话、说服或取得线索；新线索只是候选，不照 give 直接发放或掷骰。已交谈、交物或同意的步骤"
                      "沿用实际记录，不再次执行。新情况确实需要玩家选择时，先正常呈现场面，保留尚未做的事，"
                      "不替玩家接受新条件、改选对象或泄露尚未发现的内容。")
            remaining = await self._arrange(prompt, continuation=saved.setdefault("arrangement", {}))
            remaining = remaining.model_copy(update={"asked": action.asked, "echo": ""})
            saved["action"] = remaining.model_dump(mode="json")
            if self.checkpoint:
                self.checkpoint()
        remaining = Action.model_validate(saved["action"])
        for name in type(action).model_fields:
            setattr(action, name, getattr(remaining, name))
        if st.pending_turn:
            st.pending_turn["action"] = action.model_dump(mode="json")
        if "ruling" in saved:
            return Ruling.model_validate(saved["ruling"]) if saved["ruling"] is not None else None
        await self.prepare(action)
        ruling = await self.rule(action, prepared=True)
        if ruling is not None:
            # The director's prepared events and stage note remain relevant. Its proposed
            # destination and clues do not override the keeper's newly understood action.
            ruling = ruling.model_copy(update={"now": list(applied.now), "note": applied.note})
        saved["ruling"] = ruling.model_dump(mode="json") if ruling is not None else None
        if self.checkpoint:
            self.checkpoint()
        return ruling

    async def _encounter_arbiter(self, action: Action) -> Ruling:
        """Something else in a fight (a lamp thrown, a door slammed): the arbiter rules it with the
        encounter's brief, its results in Ruling.effects; a target not in the encounter goes back once."""
        from .encounter import views

        await self.emit({"type": "status", "text": "裁定中…"})
        brief = views.keeper_text(self)
        asked = Action(kind="other", summary=action.summary)
        arbiter = self.cast.arbiter
        ruling = await arbiter(self.state, asked, encounter=brief)
        enc = self.encounter()
        known = {p.id for p in enc.participants} if enc is not None else set()
        problems = [f"effects 里的 {e.target} 不在这场遭遇里" for e in ruling.effects
                    if e.kind in ("damage", "edge", "freed") and e.target not in known]
        if problems:
            ruling = await arbiter(self.state, asked, encounter=brief, problems=problems)
        return await self.checked_skill(asked, ruling, encounter=brief)

    async def prepare(self, action: Action) -> None:
        """Who steps in before the action is settled (Initiative.first), then explicit conversations
        and consent before travelling, checkpointed one at a time. Each reply settles as any does
        (_reply), into the preparation's own turn (_preparing)."""
        await self._step_in(action)
        if not action.destination or action.kind == "meta" or not fog.findable(self.m, self.state, action.destination, viewer=self.state.investigator.id):
            return
        st = self.state
        journal, turn, keep = self._preparing(action)
        present = {n.id for n in self.m.npcs_at(st.location)}
        party_ids = {p.id for p in st.investigators}
        along = [] if action.alone else [nid for nid in dict.fromkeys(action.companions) if nid not in party_ids]
        requests = [(f"before:{digest([step.npc, step.speech, step.private])}", step.npc, step.speech, step.private)
                    for step in action.before]
        said = {step.npc for step in action.before}  # the one spoken to hears the words with the invitation
        requests += [(f"companion:{nid}:{action.destination}", nid,
                      action.speech if nid == action.npc and nid not in said else "", False) for nid in along]
        place = self.m.location(action.destination).name
        for key, nid, speech, private in requests:
            if key in journal["done"]:
                continue
            if nid in party_ids:
                # A teammate can hear the invitation; a model never answers on that player's behalf.
                if speech:
                    audience = [st.investigator.id, nid] if private else None
                    await self._resolution_step(f"party:{key}:said", turn,
                                                lambda speech=speech, nid=nid, audience=audience, private=private:
                                                self.speak(st.investigator.id, speech, st.find(nid).name,
                                                           audience=audience, private=private).model_dump())
                    await self._resolution_step(f"party:{key}:notice", turn,
                                                lambda speech=speech, nid=nid, audience=audience: self.emit({
                        "type": "speech", "who": st.investigator.name, "to": st.find(nid).name,
                        "text": speech, "audience": audience,
                    }))
                journal["done"].append(key)
                keep(turn)
                continue
            if key not in turn.replies and nid not in present:
                name = next((n.name for n in self.m.npcs if n.id == nid), nid)
                turn.notes.append(f"{name}不在当前地点，尚未完成出发前的交谈；不能替他回应或同行。")
                journal["blocked"] = True
            else:
                ent = self.world.rec.entities[nid]
                # Along already: to this very place, or wherever they go (the keeper's scene, world/scene.py).
                agreed = ent.following == st.investigator.id and ent.follow_to in (action.destination, "")
                if key in turn.replies or speech or not agreed:  # (one that answered goes on from its receipt)
                    # Asked along: its answer says whether it comes (TravelDialogue), whatever else is said to it
                    # ("我陪您去" and no accompany left the man behind, replay turn 82); asked once, not again after.
                    asked = nid in along and not agreed
                    request = speech or f"请与我一同前往{place}。"
                    invite = f"调查员想请你一同前往{place}。" if asked and speech else ""
                    privacy = "这是避开其他人的私下交谈，其他人没有听到。" if private else ""
                    address = f"对你说：「{speech}」。" if speech else f"提出与你一同前往{place}。"
                    context = f"眼前的行动：{action.visible or action.target}。" if asked and (action.visible or action.target) else ""
                    message = (f"（系统：出发前，调查员{st.investigator.name}{address}{invite}{context}{privacy}"
                               "是否同意由你决定，不替其他人答应。）")
                    ask = partial(self.cast.npc, st, nid, message, travel=asked or not speech, private=private)
                    await self._reply(turn, key, nid, ask, keep,
                                      audience=[st.investigator.id] if private else None,
                                      record=partial(self._before_journey, turn, journal, action, nid, request, asked, private))
            journal["done"].append(key)
            keep(turn)

    def _preparing(self, action: Action) -> tuple[dict, Turn, Callable[[Turn], None]]:
        """What prepare settles before the ruling: its journal in the turn's (pending_turn), and a turn
        of its own for the replies there (_reply), kept in that journal as each lands. The action's
        turn takes it in at its first stage (_resolve_stages)."""
        journal = self.state.pending_turn.setdefault("preparation", {"done": [], "blocked": False})
        turn = TURN_RECORD.validate_python(journal["turn"]) if "turn" in journal else Turn(action=action)

        def keep(kept: Turn) -> None:
            journal["turn"] = TURN_RECORD.dump_python(kept, mode="json")
            if self.checkpoint:
                self.checkpoint()

        return journal, turn, keep

    def _before_journey(self, turn: Turn, journal: dict, action: Action, nid: str, request: str, asked: bool,
                        private: bool, line: str) -> None:
        """A reply before the journey, landing with its receipt (_reply): the exchange goes into the
        world record, and one asked along that neither came nor said it would holds the party back."""
        st = self.state
        self._exchanged(nid, Action(kind="talk", summary=request, speech=request), line, private=private)
        ent = self.world.rec.entities[nid]
        actual = ent.following == st.investigator.id and ent.follow_to == action.destination
        claimed = bool(getattr(self.cast, "claimed_accompany", lambda *_: False)(st, nid, action.destination))
        if asked and not actual and not claimed:
            consent = (f"只同意前往{self.world.name(ent.follow_to)}，还没同意继续到{self.world.name(action.destination)}"
                       if ent.following == st.investigator.id and ent.follow_to else "尚未同意同行")
            turn.notes.append(f"{self.m.npc(nid).name}{consent}；本轮只完成出发前交谈，调查员尚未出发，"
                              "由玩家决定分段走、独行或另作安排。")
            journal["blocked"] = True
        if asked:
            journal["done"].append(f"companion:{nid}:{action.destination}")  # answered here

    # -- no re-rolls ------------------------------------------------------------------
    def _no_reroll(self, action: Action, ruling: Ruling, at: str) -> Ruling:
        """CoC does not repeat a check: a failure may be pushed once, not rolled again while
        nothing has changed. The arbiter sees the recent checks and marks a repeat; the engine
        catches the plain re-roll even when the arbiter does not, but never overrules an arbiter
        that rolls it under other conditions (a lamp brought for a bonus die)."""
        earlier = self._earlier(action, ruling, at)
        if earlier is not None:
            return ruling.model_copy(update={"needs_roll": False, "repeat": True, "skill": earlier.skill})
        return ruling.model_copy(update={"repeat": False}) if ruling.repeat else ruling  # nothing there to repeat

    def _earlier(self, action: Action, ruling: Ruling, at: str) -> CheckRecord | None:
        """The check this action repeats: the one the arbiter means, or an immediate re-roll.
        That is the investigator's own last check, failed, of the same skill on the same
        target at the same place, and nothing else done by them since; nothing found or
        happened at the table meanwhile, and not much time gone. Another skill, another
        target, something new, an action in between or time passed: a new attempt."""
        st = self.state
        inv = st.investigator
        skill = canonical(ruling.skill or (action.asked.skill if action.asked else ""))
        if ruling.repeat:
            mine = [r for r in st.recent_checks() if r.who == inv.id]
            return next((r for r in reversed(mine) if r.skill == skill
                         and r.at == at and r.aim == _aim(action)), None)
        last = next((r for r in reversed(st.checks) if r.who == inv.id), None)
        if not ruling.needs_roll or last is None or last.passed:
            return None
        same = (last.turn, last.at, last.skill, last.aim) == (self._acted_before(), at, skill, _aim(action))
        same = same and bool(last.action) and last.action == action.summary.strip()
        quiet = last.seen == self._seen() and st.minutes - last.minutes < RECHECK_AFTER
        # The arbiter judges what changed: more bonus dice or another difficulty is another attempt.
        changed = ruling.bonus > last.bonus or (bool(last.difficulty) and ruling.difficulty != last.difficulty)
        return last if same and quiet and not changed else None

    def _acted_before(self) -> int:
        """The turn of the acting investigator's previous action (0 if none is on record):
        companions acting in between do not make a repeat any less immediate."""
        st = self.state
        name = st.investigator.name
        logged = max((e.turn for e in st.log if e.kind == "player" and e.who == name and e.turn < st.turn), default=0)
        actual = st.last_actions.get(st.investigator.id, 0)
        return max(logged, actual if actual < st.turn else 0)

    def _seen(self) -> int:
        """Clues found, flags set and events fired so far. It only grows: a different count
        means something new has come up."""
        st = self.state
        return len(st.clues) + len(st.flags) + len(st.fired)

    def _check_scope(self, actor: str, earlier: CheckRecord | None = None) -> dict:
        """Freeze one check's readers before delivery; replay never consults the current focus."""
        if earlier is not None:
            return {"audience": earlier.audience, "roll_audience": earlier.roll_audience}
        watching = list(dict.fromkeys([actor, *self.world.observers(self.world.place(actor))]))
        return {"audience": watching, "roll_audience": None if self.public_rolls else watching}

    def _record(self, action: Action, check: Check, *, scope: dict | None = None) -> None:
        st = self.state
        scope = self._check_scope(st.investigator.id) if scope is None else scope
        st.checks.append(CheckRecord(
            turn=st.turn, who=st.investigator.id, at=st.location, skill=check.name, npc=action.npc,
            target=action.target.strip(), text=check.describe(), passed=check.passed, action=action.summary.strip(), minutes=st.minutes,
            bonus=check.bonus, difficulty=next((zh for zh, level in DIFFICULTY_ZH.items() if level == check.difficulty), ""),
            **scope,
        ))  # fmt: skip
        del st.checks[:-KEEP_CHECKS]

    # -- the player's own roll -----------------------------------------------------------
    def roll_prompt(self, action: Action, ruling: Ruling) -> dict[str, Any]:
        """What a player sees before throwing their own dice (graph.ask_roll): the check the
        keeper asks for, the numbers to beat, and one line on why. Nothing it shows is
        anything the module keeps back."""
        inv = self.state.investigator
        skill = canonical(ruling.skill)
        if action.idea:  # the Idea roll: the party's highest INT rolls it, whoever asked
            inv, skill = self.state.find(action.idea["roller"]), "灵感"
        target = inv.value(skill)
        return {
            "who": inv.name,
            "skill": skill,
            "difficulty": ruling.difficulty,
            "bonus": max(-2, min(2, ruling.bonus)),
            "target": target,
            "hard": target // 2,
            "extreme": target // 5,
            "need": _threshold(target, ruling.difficulty),
            "action": action.summary,
            "why": self._why(ruling),
        }

    def _why(self, ruling: Ruling) -> str:
        """Only the arbiter's public explanation. Its private reasoning never enters the prompt."""
        return ruling.why.strip()

    def _led_to(self, won: list[str], done: list[str], passed: bool, npc: str = "", offer: dict | None = None) -> list[str]:
        """What a roll led to, written under it: that it found something and who gave it up, what
        it set in motion, or that it came to nothing. Never what was found: the telling says that,
        in the story's words (the user, 2026-10-22: no clue's title or summary on the screen)."""
        m = self.m
        lines = []
        if won:
            owner = next((n.name for cid in won for n in m.npcs if cid in n.clues), "")
            lines.append((f"{owner}松了口" if owner else "有所发现") if passed else "没过，但仍有所得，代价见叙述")
        if done:
            lines.append("行动奏效了")  # flag descriptions are private notes, not player-facing outcomes
        if offer is not None:
            if offer.get("can_push") and offer.get("luck") is not None:
                lines.append("还有一次机会：孤注一掷，或者花幸运")
            elif offer.get("can_push"):
                lines.append("可以选择孤注一掷")
            else:
                lines.append("可以花费幸运改变结果")
        if not lines:
            lines.append("检定成功" if passed else "检定失败")
        return lines

    def _settle(self, record: CheckRecord | None, passed: bool | None = None, text: str = "") -> None:
        """A check's final word once its turn is done: a push or luck may have changed the
        result, and what the turn brought is not "something new" for the next attempt."""
        if record is None:
            return
        if passed is not None:
            record.passed, record.text = passed, text
        record.minutes, record.seen = self.state.minutes, self._seen()

    # -- improvisation ----------------------------------------------------------------
    async def present(self, actions: list[dict], *, key: str = "", shown: list[int] | None = None) -> list[str]:
        """One addressed delivery path for keeper, NPC and director media. No clue acquisition.
        An optional caller-owned `shown` receipt records only notifications whose emit returned."""
        st, errors = self.state, []
        prefix = key or f"present:{st.turn}:{len(st.presentations)}"
        for n, raw in enumerate(actions):
            if shown is not None and n in shown:
                continue
            ident = f"{prefix}:{n}"
            scope = raw.get("to")
            if isinstance(scope, list):
                scope = sorted(str(who) for who in scope)
            intent = "media:" + digest([scope, raw.get("slot", "overlay")])
            saved = next((p for p in st.presentations if p["id"] == ident), None)
            if saved is None:
                try:
                    saved = presentation.prepare(self.m, st, raw, ident)
                except ValueError as exc:
                    errors.append(str(exc))
                    st.presentation_issues[intent] = {"action": raw, "problem": str(exc)}
                    if self.checkpoint:
                        self.checkpoint()
                    continue
                st.presentations.append(saved)
                st.presentation_issues.pop(intent, None)
                if saved["action"] == "show" and saved["payload"].get("kind") == "handout":
                    h = saved["payload"]["handout"]
                    entry = st.add("system", f"收到材料：{h['title']}", who="守秘人", audience=saved["audience"])
                    for who in saved["audience"] if saved["audience"] is not None else [i.id for i in st.investigators]:
                        st.deeds.append(Deed(turn=entry.turn, time=entry.time, who=who, kind="handout",
                                             text=h["title"], audience=saved["audience"]))
                if self.checkpoint:
                    self.checkpoint()
            await self.emit(presentation.event(saved))
            if shown is not None:
                shown.append(n)
                if self.checkpoint:
                    self.checkpoint()
        if errors:
            self._debug("presentation", {"phase": "unavailable", "problems": errors})
        return errors

    def receive_private_message(self, who: str, text: str, *, request: str = "", offer: dict | None = None) -> tuple[str, dict]:
        """Save accepted correspondence before putting its model response in a queue."""
        st = self.state
        if not text.strip() or who not in {i.id for i in st.investigators}:
            raise ValueError("私信需要本局调查员和正文")
        ident = f"{who}:{request or len(st.private_messages)}"
        receipt = st.private_messages.get(ident)
        fresh = receipt is None
        if receipt is None:
            receipt = st.private_messages[ident] = {"who": who, "text": text.strip()}
            st.add("system", f"对守秘人（私信）：{text.strip()}", who=st.find(who).name, audience=[who])
        elif receipt["text"] != text.strip():
            raise ValueError("同一私信编号不能替换正文")
        if "reply" not in receipt and not receipt.get("context"):
            from ..agents.cast import private_context

            receipt["context"] = private_context(self.m, st, who, text.strip(), self.world)
            if not fresh:
                receipt["context_restored"] = True
                receipt["context"] = ("【恢复说明】原信未保存提问时上下文，本答复只依据本次恢复时的记录，不能追认原提问时状态。\n"
                                      + receipt["context"].replace("【提问时刻】", "【恢复时刻】", 1))
            if offer is not None:
                label = "本人恢复时待答选择" if receipt.get("context_restored") else "本人提问时待答选择"
                receipt["context"] += f"\n【{label}】" + json.dumps(offer, ensure_ascii=False)
                receipt["waiting"] = True
            if self.checkpoint:
                self.checkpoint()
        return ident, receipt

    async def show_private_input(self, who: str, receipt: dict) -> None:
        """Acknowledge accepted mail without waiting for the keeper or the player's roll."""
        st = self.state
        if receipt.get("input_shown"):
            return
        await self.emit({"type": "private_input", "audience": [who], "text": receipt["text"], "who": st.find(who).name})
        receipt["input_shown"] = True
        if self.checkpoint:
            self.checkpoint()

    async def private_message(self, who: str, text: str, *, request: str = "", defer_media: bool = False) -> str:
        """A PL message to the keeper, with a private reply; never a secret physical action."""
        st = self.state
        ident, receipt = self.receive_private_message(who, text, request=request)
        await self.show_private_input(who, receipt)
        if "reply" not in receipt:
            result = await self.cast.private_keeper(st, who, receipt["text"], context=receipt["context"])
            prefix = ("（原提问时上下文未保存；以下按本次恢复时的记录答复，不改变当前选择或已结算结果。）\n"
                      if receipt.get("context_restored") else
                      "（按你提问时的状态答复，不改变当前待答选择或已结算结果。）\n" if receipt.get("waiting") else "")
            receipt["reply"] = prefix + result["text"]
            receipt["media"] = [{**m, "to": [who]} for m in result.get("media", [])]
            st.add("keeper", receipt["reply"], who="守秘人", audience=[who])
            if self.checkpoint:
                self.checkpoint()
        if not receipt.get("reply_shown"):
            await self.emit({"type": "aside", "audience": [who], "text": receipt["reply"], "who": "守秘人"})
            receipt["reply_shown"] = True
            if self.checkpoint:
                self.checkpoint()
        # Text and attachments have separate receipts: a disconnect after the text must
        # still resume the same saved media without asking the keeper again.
        if defer_media and receipt.get("media"):
            if not receipt.get("media_deferred"):
                await self.emit({"type": "aside", "audience": [who], "who": "守秘人",
                                 "text": "私信附件已保存，将在本次行动结束后单独发送。"})
                receipt["media_deferred"] = True
            if self.checkpoint:
                self.checkpoint()
            return receipt["reply"]
        receipt["delivered"] = not await self.present(receipt.get("media", []), key=f"private:{ident}")
        if self.checkpoint:
            self.checkpoint()
        return receipt["reply"]

    async def _improvise(
        self, kind: str, *, action: Action | None = None, ruling: Ruling | None = None, at: str = "",
        patience: float | None = None, required: bool = False,
    ) -> improv.Applied | None:
        """Prepare a scene. Current actions wait; unrelated future work may stay backstage."""
        st = self.state
        saved = st.pending_turn.get("scene_package", {})
        if required and saved.get("kind") == kind and saved.get("at") == (at or st.location):
            if saved.get("applied"):
                plan = saved.get("plan", {})
                await self.present(plan.get("media", []), key=f"director:{digest(plan)}")
                return improv.Applied(**saved["applied"])
            if saved.get("given_up"):
                return None
        if not self.improvising:
            return None
        if len(self.backstage) >= BACKSTAGE_LIMIT and not required:
            return None
        await self.emit({"type": "status", "text": "KP 正在构思…"})
        where = at or st.location
        if kind == "展开":  # diagnostic history, never a veto on a player's new action
            st.expanded[where] = st.turn
        receipt = st.pending_turn.setdefault("scene_package", {}) if required and st.pending_turn else None
        if receipt is not None:
            if receipt.get("kind") != kind or receipt.get("at") != where:
                receipt.clear()
            if receipt.get("applied"):
                return improv.Applied(**receipt["applied"])
            receipt.update(kind=kind, at=where)
        draft = receipt.setdefault("draft", {}) if receipt is not None else {}
        if receipt is not None and receipt.get("plan"):
            async def cached():
                restored = Improv.model_validate(receipt["plan"])
                restored.reads = dict(receipt.get("reads", {}))
                return restored
            task = asyncio.create_task(cached())
        else:
            task = asyncio.create_task(self._plan(kind, action=action, ruling=ruling, at=where, draft=draft))
        if required:
            return await self._ready(kind, task, action=action, ruling=ruling, at=where, receipt=receipt, draft=draft)
        wait = self.director_wait if patience is None else patience
        try:
            if wait is None:
                plan = await task
            else:
                plan = await asyncio.wait_for(asyncio.shield(task), wait)
            return await self._land(kind, plan, where) if plan is not None else None
        except TimeoutError:
            self.backstage.append(Backstage(kind, task, where, self.state.turn))
            return None
        except PreparationError as exc:
            self._debug("director", {"phase": "failed", "kind": kind, "error": str(exc)})
            return None

    async def _ready(self, kind: str, task: asyncio.Task, *, action: Action | None, ruling: Ruling | None,
                     at: str, receipt: dict | None, draft: dict | None = None) -> improv.Applied | None:
        """Prepare a needed scene with one repair per invalid draft. Temporary service failures,
        stale world reads and first-picture recovery have a separate PREPARE_LIMIT deadline.
        Then the scene proceeds without its picture (drawn in the background), or, with no plan
        that fits, the action settles as the module has it. The player is never left at /retry."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + PREPARE_LIMIT
        if draft is None:
            draft = receipt.setdefault("draft", {}) if receipt is not None else {}
        plan: Improv | None = None
        failures = drawn = 0
        while True:
            if plan is None:
                try:
                    async with asyncio.timeout_at(deadline):
                        plan = await task
                except TimeoutError:
                    break
                except PreparationError as exc:
                    self._debug("director", {"phase": "failed", "kind": kind, "error": str(exc),
                                             "cause": repr(exc.__cause__)[:500] if exc.__cause__ else ""})
                    # _plan already gave an invalid draft one concrete repair. Starting it over
                    # would discard that limit and spend repeatedly on the same rejected scene.
                    # Service failures keep their separate, paced recovery below.
                    if exc.__cause__ is None or refused(exc.__cause__):
                        break
                    failures += 1
                    pause = min(PREPARE_PAUSE * failures, deadline - loop.time())
                    if pause <= 0:
                        break
                    await asyncio.sleep(pause)  # a service that failed outright gets a moment first
                    task = asyncio.create_task(self._plan(kind, action=action, ruling=ruling, at=at, draft=draft))
                    continue
                if plan is None:
                    return None  # nothing to add here
                if receipt is not None:
                    receipt["plan"] = plan.model_dump(mode="json")
                    receipt["reads"] = dict(plan.reads)
                    if self.checkpoint:
                        self.checkpoint()
            left = deadline - loop.time()
            try:
                applied = await self._land(kind, plan, at, receipt=receipt,
                                           draw=left if drawn < PREPARE_DRAWS and left > 0 else 0)
            except PreparationError as exc:
                self._debug("director", {"phase": "failed", "kind": kind, "error": str(exc)})
                drawn += 1
                continue
            if applied is not None:
                return applied
            if loop.time() >= deadline:
                break
            # Artwork/background work may have outlived the facts that were read.
            # Rebuild the complete package; never retain just the module half.
            await self.emit({"type": "status", "text": "KP 正在构思…"})
            plan = None
            draft.clear()  # a changed world starts a new package, unlike a service retry
            if receipt is not None:
                receipt.pop("plan", None)
                receipt.pop("reads", None)
                if self.checkpoint:
                    self.checkpoint()
            task = asyncio.create_task(self._plan(kind, action=action, ruling=ruling, at=at, draft=draft))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._debug("director", {"phase": "gave_up", "kind": kind, "seconds": loop.time() - started,
                                 "limit_seconds": PREPARE_LIMIT})
        if receipt is not None:  # a resumed turn settles the same way
            receipt.clear()
            receipt.update(kind=kind, at=at, given_up=True)
            if self.checkpoint:
                self.checkpoint()
        return None

    async def _plan(self, kind: str, *, action: Action | None, ruling: Ruling | None, at: str,
                    draft: dict | None = None) -> Improv | None:
        """Check package, plugins and world operations deterministically, with one repair.

        The director judges a rewrite's meaning against its full world context; another model
        does not approve it. The world still preserves observations, materials and revision history.
        """
        said = f"{action.summary}（{action.kind}，对象：{action.target or '无'}）" if action else ""
        draft = {} if draft is None else draft
        problems: list[str] = draft.get("problems", [])
        while draft.get("failed", 0) < 2:
            reads = world_view.director_view(self.world).reads  # what it is shown now, for the desk to check later
            try:
                plan = await self.cast.director(self.state, kind, action=said, reason=ruling.reason if ruling else "", at=at, problems=problems)
            except CassetteError:  # a replay that drifted must fail loudly
                raise
            except Exception as exc:
                raise PreparationError("导演没有给出方案") from exc
            if plan is None or plan.kind == "不需要":
                return None
            plan.kind = kind  # type: ignore[assignment]
            problems = improv.check(self.m, plan, at=at, actors={i.id for i in self.state.investigators},
                                    player_names={i.name for i in self.state.investigators})
            if not problems:
                from ..plugins.runtime import verify

                for plugin in plan.plugins:
                    problems += [f"插件 {plugin.id}：{problem}" for problem in await verify(plugin)]
            if not problems:
                plan.reads = reads
                problems = self._trial(plan, at) if plan.backstage else []
            if not problems:
                self._debug("director", {"phase": "validated", "kind": kind, "plan": plan.model_dump(mode="json")})
                return plan
            # Failed service calls do not reach here: retry the same draft with its original
            # problems. Persist rejected outputs so cancellation/readback cannot reset repair.
            draft.update(failed=draft.get("failed", 0) + 1, problems=list(problems))
            if self.checkpoint:
                self.checkpoint()
        self._debug("director", {"phase": "rejected", "kind": kind, "problems": problems})
        raise PreparationError("场景方案两次都没有通过检查")

    def _trial(self, plan: Improv, at: str) -> list[str]:
        """The plan's backstage ops tried on a copy of this game, after its module patch: what the
        world record would refuse, in words the director can fix."""
        w = World(self.m.model_copy(deep=True), self.state.model_copy(deep=True))
        patched = w.apply(Improvise(plan=plan.model_dump(mode="json", exclude={"backstage"}), at=at, actor=DIRECTOR))
        if not patched.ok:
            return patched.problems
        problems: list[str] = []
        for i, op in enumerate(plan.backstage):
            done = w.apply(op.model_copy(update={"actor": DIRECTOR, "id": f"trial:{i}"}))
            problems += [f"幕后第 {i + 1} 条（{op.kind}）：{p}" for p in done.problems]
        return problems

    async def _land(self, kind: str, plan: Improv, at: str = "", *, receipt: dict | None = None,
                    draw: float | None = None) -> improv.Applied | None:
        """Commit module, world actions and plugin sources together, from a validated copy. Its
        first pictures are waited for, up to `draw` seconds when given; with 0 the scene goes on
        and they are drawn in the background."""
        where = at or self.state.location
        problems = improv.check(self.m, plan, at=where, actors={i.id for i in self.state.investigators},
                                player_names={i.name for i in self.state.investigators})
        moved = scheduler.fresh(self.world, plan.reads)
        if problems or moved:
            self._debug("director", {"phase": "rejected", "kind": kind, "problems": problems, "stale": moved})
            return None
        def version() -> str:
            return digest([self.world.domain(), self.state.record.model_dump(mode="json"),
                           self.m.model_dump(mode="json", exclude={"art", "art_colors", "pictures"}),
                           self.state.improv, self.state.plugin_sources, self.state.turn, self.state.minutes,
                           self.state.location])

        stamp = version()
        staged = World(self.m.model_copy(deep=True), self.state.model_copy(deep=True))
        result = adapters.improvise(staged, plan.model_copy(update={"kind": kind}), at=where)
        if not result.ok or result.repeat:  # the story moved on since it was checked, or it is in already
            return None
        applied: improv.Applied = result.data
        if plan.backstage:
            ops = [op.model_dump(mode="json", exclude={"id"}) | {"actor": DIRECTOR} for op in plan.backstage]
            # Reads were checked against the original record, before adding new facts.
            req = desk.submit(staged, f"director:{kind}", ops, reads={}, reason=plan.reason)
            landed = desk.land(staged, req.id)
            req.reads = dict(plan.reads)
            if landed.status != "landed":
                self._debug("director", {"phase": "rejected", "kind": kind,
                                          "problems": landed.problems, "stale": landed.moved})
                return None
        for plugin in plan.plugins:
            staged.st.plugin_sources[plugin.id] = plugin.model_dump(mode="json")
        visible = applied.go or where
        keys = {improv.visual_key("scene", loc.id) for loc in plan.locations if loc.id == visible}
        keys |= {improv.visual_key("portrait", n.id) for n in plan.npcs if staged.place(n.id) == visible}
        keys |= {improv.visual_key("item", item.id) for item in plan.items if staged.place(item.id) == visible
                 and knowledge.item_known(staged, item.id, staged.st.investigator.id)}
        if draw == 0:
            keys = set()
        pictures = Visuals(staged.m, staged.st, getattr(self.cast, "cfg", None), _silent)
        pictures.debug_journal = getattr(self.visuals, "debug_journal", None)
        usage_before = {key: dict(value) for key, value in staged.st.usage.items()}
        try:
            if keys and pictures.cfg is not None:
                await self.emit({"type": "status", "text": "KP 正在构思…"})
            try:
                async with asyncio.timeout(draw):
                    missing = await pictures.prepare(keys)
            except TimeoutError:
                missing = sorted(keys)
        finally:
            await pictures.close()
            # Image work costs are real even when its scene became stale or was cancelled.
            for key, values in staged.st.usage.items():
                for metric, value in values.items():
                    delta = value - usage_before.get(key, {}).get(metric, 0)
                    if delta > 0:
                        live = self.state.usage.setdefault(key, {})
                        live[metric] = live.get(metric, 0) + delta
        if missing:
            self._debug("director", {"phase": "art_failed", "kind": kind, "keys": missing})
            raise PreparationError("场景插图没有画成")
        if version() != stamp:
            self._debug("director", {"phase": "rejected", "kind": kind, "stale": ["图像准备期间世界发生变化"]})
            return None
        if moved := scheduler.fresh(self.world, plan.reads):
            self._debug("director", {"phase": "rejected", "kind": kind, "stale": moved})
            return None
        for name in ("art", "art_colors", "pictures"):
            rendered = getattr(staged.m, name)
            setattr(staged.m, name, {**getattr(self.m, name), **{key: rendered[key] for key in keys if key in rendered}})
        # No await between validation and commit. Retain live artwork/usage dictionaries:
        # their independent tasks may still hold references to individual records.
        for name in type(self.m).model_fields:
            setattr(self.m, name, getattr(staged.m, name))
        for name in ("record", "flags", "item_owners", "world", "investigators", "improv", "plugin_sources"):
            setattr(self.state, name, getattr(staged.st, name))
        for key in keys:
            if key in staged.st.visuals:
                self.state.visuals[key] = staged.st.visuals[key]
        if receipt is not None:
            receipt.pop("draft", None)
            receipt["applied"] = asdict(applied)
            if self.checkpoint:
                self.checkpoint()
        self._debug("director", {"phase": "committed", "kind": kind, "plan": plan.model_dump(mode="json")})
        for target, change in applied.looks:  # a place burnt, a man wounded: their picture is redrawn
            self.visuals.look(target, change)
        await self._people()  # someone it brings in stands here
        self.visuals.schedule()
        self.showcases.prepare()  # a big scene of the director's gets its picture ahead of its moment
        await self.present([a.model_dump(mode="json") for a in plan.media], key=f"director:{digest(plan.model_dump(mode='json'))}")
        await self.emit({"type": "improv", "kind": kind, "summary": applied.summary, "reason": plan.reason})
        return applied

    def ready(self) -> bool:
        """A plan is finished backstage and can take the stage, or the world did something in
        front of the party while they read the last narration."""
        lived = self._world_task is not None and self._world_task.done() and bool(self._late.world or self._late.events)
        return lived or any(b.task.done() for b in self.backstage)

    async def interlude(self) -> Turn | None:
        """Between turns: a plan the director has finished plays out at once, told in a short
        narration of its own, so no one waits for their next action to see it."""
        if self.state.ending or not self.ready():
            return None
        await self._world_settled()
        await self._curtain()
        turn, self._late = self._late, Turn()
        if not (turn.events or turn.clues or turn.notes or turn.hints or turn.arrived or turn.sanity or turn.world):
            return None
        turn.interlude = True
        await self._time(turn)
        await self._check_ending(turn)
        await self.narrate(turn)
        await self.remember()
        return turn

    async def _curtain(self, action: Action | None = None) -> None:
        """Plans finished backstage take the stage before the next action is ruled: their places
        and people exist from now on, their scenes play out, and the next narration tells it."""
        st, late = self.state, self._late
        if action is not None:
            for b in self.backstage:
                if b.at in {st.location, action.destination} and not b.task.done():
                    await self.emit({"type": "status", "text": "KP 正在构思…"})
                    await asyncio.gather(b.task, return_exceptions=True)
        for b in [b for b in self.backstage if b.task.done()]:
            self.backstage.remove(b)
            if not b.task.cancelled() and (error := b.task.exception()):
                self._debug("director", {"phase": "failed", "kind": b.kind,
                                          "error": f"{type(error).__name__}: {error}"})
            plan = None if b.task.cancelled() or b.task.exception() else b.task.result()
            if (plan is None or st.location != b.at or st.turn > b.turn + 1
                    or improv.check(self.m, plan, at=b.at, actors={i.id for i in self.state.investigators},
                                    player_names={i.name for i in self.state.investigators})):
                continue
            try:
                applied = await self._land(b.kind, plan, b.at)
            except Exception as exc:
                self._debug("director", {"phase": "failed", "kind": b.kind,
                                          "error": f"{type(exc).__name__}: {exc}"})
                continue
            if applied is None:
                continue
            if applied.go and applied.go != st.location and st.location == b.at:  # they were on their way
                minutes = self._travel(st.location, applied.go)
                if minutes is not None:
                    st.minutes += minutes
                    await self._arrive(applied.go, late, party=True)
            await self._flags(applied.now, late)
            await self._gain(self._valid_clues(applied.give, None), late, None)
            if applied.note:
                late.notes.append(f"导演的安排：{applied.note}")

    def _steer(self, ruling: Ruling, applied: improv.Applied) -> Ruling:
        """Unconditional deliveries and prepared events; new gates belong to a fresh ruling."""
        r = ruling.model_copy(deep=True)
        for cid in applied.give:
            clue = self.m.clue(cid)
            gate = clue.check or (self.m.npc(clue.npc).gates.get(cid) if clue.npc else None)
            if gate is None:
                r.auto_clues.append(cid)
        r.now, r.go, r.note = list(applied.now), applied.go, applied.note
        return r

    async def _failed(self, failure: Failure, wanted: list[str], turn: Turn, action: Action | None, *,
                      pushed: bool = False) -> None:
        """A check that failed stays failed (the user's call, 2026-10-20: no clue for a failure, no
        fixed price). Only what the arbiter said a failure brings about, the module's own word
        first, is settled here (a clue the module lets a failure find, a flag, time, a wound),
        and the keeper is told what was tried, what was not found and what happened."""
        st, step = self.state, self._resolution_step

        def awards() -> dict:
            clues = self._valid_clues([c for c in failure.clues if c not in st.clues], action)
            originals = [cid for cid in clues if module_graph.original_source_at_hand(
                self.m, self.m.clue(cid), st.location, self.world.present(st.location), record=self.world.rec)]
            return {"clues": clues, "originals": originals}

        awarded = await step("fail-clue-awards", turn, awards)
        clues = awarded["clues"]
        missed = [self.m.clue(c).title for c in wanted if c not in st.clues and c not in clues]
        flags = [f for f in failure.flags if f in self.m.flags and f not in st.flags]

        def time() -> int:
            minutes = max(0, min(failure.minutes, 240))
            self._spend(minutes, turn)
            return minutes

        elapsed = await step("fail-time", turn, time)
        if flags:
            await step("fail-flags", turn, lambda: self._flags(flags, turn))
        if clues:
            await step("fail-clues", turn, lambda: self._gain(clues, turn, action, originals=set(awarded["originals"])))
        damage_notice = None
        if failure.damage.strip():
            injury = f"fail:{st.turn}:{self._resolution_key('fail-damage')}"
            damage_notice = await step("fail-damage", turn, lambda: self._wound_event(
                st.investigator.id, failure.damage.strip(), injury, turn))
            if damage_notice:
                await step("fail-damage-notice", turn, lambda: self.emit(damage_notice))
        sanity_notice = None
        if failure.sanity:  # the SAN roll the failure calls for, settled as an event's or a clue's
            sanity_notice = await step("fail-sanity", turn, lambda: self._lose_sanity(st.investigator, failure.sanity, turn))
            await step("fail-sanity-notice", turn, lambda: self.emit(sanity_notice))
        mishap = await self._mishap(failure.mishap, turn, "fail-mishap") if failure.mishap.strip() else ""
        gained = [self.m.clue(cid).title for cid in failure.clues if cid in turn.clues]
        set_flags = [f for f in failure.flags if f in st.flags]
        sanity_text = sanity_notice["text"] if sanity_notice else ""
        if sanity_notice and sanity_notice["loss"] > 0:
            sanity_text = f"{st.investigator.name}的理智受到冲击：{sanity_text}"
        settled = [x for x in (f"耽搁了 {elapsed} 分钟" if elapsed else "",
                               damage_notice["text"] if damage_notice else "",
                               sanity_text,
                               f"已取得线索：{'、'.join(gained)}" if gained else "",
                               f"已设置标记：{'、'.join(set_flags)}" if set_flags else "",
                               f"惨事卡〔{mishap}〕" if mishap else "") if x]
        narrative = failure.text.strip()
        if not missed and not settled and not narrative:
            return  # the roll's card says it: nothing was within reach and nothing else came of it
        note = ("孤注一掷也没有成功" if pushed else "这次检定失败") + (f"，没有取得：{'、'.join(missed)}" if missed else "")
        if settled:
            note += f"。【已结算的失败结果】{'；'.join(settled)}。这些结果已入账，只描写，不再次结算。"
        if narrative:
            note += (f"。【须在本轮场面演出落实的失败后果】{narrative}。"
                     "这是须承担的失败代价，不因尚未演完而取消；其中未演出的回应、行程或交付不代表已经完成。"
                     "按实际演出提交场面，不替调查员作出尚待回应的选择；与已结算项目重合的内容只描写一次，不再扣除或发放。")
        await step("fail-note", turn, lambda: turn.notes.append(
            note + "。照实讲失败，不在叙述里补出没拿到的东西；之后可以有新的机会，这一轮不补偿。"))

    def _wound_event(self, who: str, amount: str, injury: str, turn: Turn) -> dict | None:
        """A wound outside a fight (an event's, a failed check's), rolled and landed through
        engine/effects in one world op whose id is the injury's: the damage notice, or None (and a
        note for the keeper) when the damage is no expression the dice can roll."""
        try:
            wound = effects.harm(self, who, amount, injury=injury)
        except ValueError as exc:
            turn.notes.append(f"伤害式无法使用：{exc}")
            return None
        done = effects.commit(self.world, [wound], op_id=f"effects:{injury}", source=injury.partition(":")[0])
        if not done.ok:
            turn.notes.append("伤害没有结算：" + "；".join(done.problems))
            return None
        inv = self.state.find(who)
        text = done.told(injury, inv.name)
        self.state.add("damage", text, who=inv.name)
        return {"type": "damage", "who": inv.name, "text": text, "injury": injury}

    async def _nudge(self, turn: Turn) -> None:
        """A private pacing signal after a stretch without recorded progress. The keeper decides
        whether this is a genuine dead end in its normal telling (Telling.offer_idea); counting
        turns does not tell players they are stuck, nor start a director call."""
        st = self.state
        step = self._resolution_step
        eligible = await step("eligible", turn, lambda: not (turn.action and turn.action.kind == "daily")
                              and not st.ending and not idea.offered(st) and st.turn - st.progress_at >= STALL_TURNS)
        if not eligible:
            return

        def signal() -> None:
            st.progress_at = st.turn  # do not repeat the same internal reminder every turn
            if self.battling() or idea.pick(self.m, st) is None:
                return
            turn.notes.append("【内部节奏参考】近期没有新的线索、标记、事件或地点记录，不代表玩家陷入停滞。"
                              "结合近期交谈判断：关系发展、讨论、日常和有意等待都可以继续，不必催促或暗示遗漏。"
                              "确实无从着手时，才在叙述中提议灵感检定并填写 offer_idea；否则不向玩家展示这条参考。")

        await step("signal", turn, signal)

    # -- the Idea roll (engine/idea.py) -------------------------------------------------------------
    def idea_refused(self) -> str:
        """Why the party cannot make an Idea roll now (the game is over, a fight is on, nothing was
        missed, one was made today and the keeper has not offered another); "" when it can."""
        return idea.refused(self.m, self.state, battling=self.battling())

    def idea_action(self) -> Action:
        """The action an Idea roll asked for now is: the clue, difficulty and roller fixed (idea.plan)."""
        plan = idea.plan(self.m, self.state)
        who = self.state.find(plan["roller"]).name
        return Action(kind="other", summary=f"调查员们重新梳理已知的线索，由{who}做灵感检定", idea=plan)

    async def _idea(self, action: Action, turn: Turn) -> None:
        """An Idea roll (pp. 167–169): the investigator with the highest INT rolls INT. Either way the
        missed clue is gained. Success: the keeper weaves it in as their own realisation. Failure:
        time passes, and the director cuts them into the worst situation they can recognise
        (千钧一发), the clue showing in it; the keeper tells the cut, and the players act after it."""
        st, m, step = self.state, self.m, self._resolution_step
        plan = action.idea or {}
        clue, roller = m.clue(plan["clue"]), st.find(plan["roller"])

        def roll() -> dict:
            rolled = self.rules.check(roller, "智力", DIFFICULTY_ZH.get(plan["difficulty"], Level.REGULAR), 0, mark=False)
            turn.checks.append(rolled)
            st.add("dice", f"灵感检定：{rolled.describe()}", who=roller.name)
            idea.record(st, plan, rolled.passed)
            view = _check_view(rolled)
            return {"passed": rolled.passed,
                    "event": {"type": "dice", "who": roller.name, **view, "skill": "灵感", "text": f"灵感检定：{view['text']}"}}

        checked = await step("idea-check", turn, roll)
        await step("idea-notice", turn, lambda: self.emit(checked["event"]))
        passed, staged, minutes = checked["passed"], False, 0
        if not passed:
            lost_key = self._resolution_key("idea-lost")

            async def arrange() -> dict:
                old_time = (self._resolution or {}).get("steps", {}).get(lost_key, {})
                if old_time.get("done"):  # pre-0.0.5 journal: its historical three-hour cost already landed
                    return IdeaFailure(minutes=180, situation="沿用此前已经结算的时间，从当前实际局势继续。",
                                       prepare=self.improvising).model_dump(mode="json")
                choose = getattr(self.cast, "idea_failure", None)
                if choose is None:  # older/scripted casts: use the next concrete deadline, not a fixed three hours
                    due = [parse_time(e.when) - st.minutes for e in m.events
                           if e.id not in st.fired and re.fullmatch(r"D\d+ \d{1,2}:\d{2}", e.when)
                           and parse_time(e.when) > st.minutes]
                    return IdeaFailure(minutes=min(due, default=1), situation="按实际推进后的局势切入危机。",
                                       prepare=self.improvising).model_dump(mode="json")
                chosen = await choose(st, clue.id)
                return IdeaFailure.model_validate(chosen).model_dump(mode="json")

            arranged = await step("idea-arrangement", turn, arrange)
            minutes = arranged["minutes"]

            def lost() -> None:
                self._spend(minutes, turn)

            await step("idea-lost", turn, lost)
            await step("idea-time", turn, lambda: self._time(turn))
            await step("idea-arrangement-note", turn, lambda: turn.notes.append(
                "【灵感失败后的切入安排】" + arranged["situation"] + "。以时间推进后的实际记录为准，未结算的变化在场面快照中落实。"))
            applied = (await step("idea-scene", turn, lambda: self._cut(clue.id, arranged["situation"]))
                       if arranged["prepare"] and self.improvising and not st.ending else None)
            if applied:
                staged = True
                if applied["go"] and applied["go"] != st.location and any(x.id == applied["go"] for x in m.locations):
                    await step("idea-go", turn, lambda: self._arrive(applied["go"], turn, party=True))
                await step("idea-flags", turn, lambda: self._flags(applied["now"], turn))  # the clue is the engine's (idea-clue)
                if applied["note"]:
                    await step("idea-staged", turn, lambda: turn.notes.append(f"导演的安排：{applied['note']}"))
        await step("idea-clue", turn, lambda: self._gain([clue.id], turn, None))
        told = (idea.realised(clue.title, mentioned=bool(plan.get("mentioned", True))) if passed
                else idea.cut(clue.title, minutes, staged=staged))
        await step("idea-note", turn, lambda: turn.notes.append(told))
        lines = (["灵感检定成功：理出了一条思路"] if passed else  # what it is, the telling says
                 [f"灵感检定失败：经过 {_span(minutes)}，局势发生变化", "那条思路仍会在局面中显露"])
        await step("idea-outcome", turn, lambda: self.emit({"type": "outcome", "who": roller.name, "lines": lines}))
        await step("turn-plugins", turn, lambda: self.plugins.run("turn", turn, action=action.model_dump(mode="json")))
        await step("ending", turn, lambda: self._check_ending(turn))

    async def _cut(self, clue_id: str, situation: str = "") -> dict | None:
        """The director's 千钧一发 after a failed Idea roll, prepared before the turn goes on."""
        m = self.m
        clue = m.clue(clue_id)
        where = m.location(clue.where).name + (f"的{clue.feature}" if clue.feature else "")
        said = Action(kind="other", target=clue.title,
                      summary=f"灵感检定失败。调查员漏掉的线索〔{clue.title}〕：{clue.text.strip()}（在{where}）")
        why = Ruling(needs_roll=False, reason="灵感检定失败，千钧一发：时间已经过去，直接切入调查员能意识到的最糟局面，让这条线索在局面里显露出来。" + situation)
        applied = await self._improvise("千钧一发", action=said, ruling=why, required=True)
        return asdict(applied) if applied is not None else None

    def _heading(self, action: Action) -> str | None:
        """Where an action happens when the investigator walks somewhere to do it ("I go up to
        the tomb door and check the seams"): a reachable place other than here, which they know
        of or can find from here (engine/fog.py)."""
        dst = action.destination
        if (action.kind == "move" or action.arrival_after_success or not dst or dst == self.state.location
                or self._travel(self.state.location, dst) is None):
            return None
        return dst if fog.findable(self.m, self.state, dst, viewer=self.state.investigator.id) else None

    def _resolution_key(self, key: str) -> str:
        return key[1:] if key.startswith("/") else "/".join([*self._resolution_scope, key])

    def _resolution_pending(self, key: str) -> bool:
        row = (self._resolution or {}).get("steps", {}).get(self._resolution_key(key))
        return row is not None and not row.get("done")

    def _resolution_save(self, turn: Turn) -> None:
        if self._resolution is None:
            return
        if self._resolution_keep is not None:  # the journal is the turn's own: kept with it (Game._journal)
            self._resolution_keep(turn)
            return
        self._resolution["turn"] = TURN_RECORD.dump_python(turn, mode="json")
        if self.checkpoint:
            self.checkpoint()

    @contextmanager
    def _journal(self, journal: dict, keep: Callable[[Turn], None]) -> Iterator[None]:
        """Stages settled in `journal` as a resolution's are (_resolution_step: each stage and its
        dice once), for a journal that lives inside a turn (an NPC reply's receipt, Said.journal):
        each stage is saved by keeping that turn (`keep`), not by copying the turn into it."""
        outer = self._resolution, self._resolution_scope, self._resolution_keep
        self._resolution, self._resolution_scope, self._resolution_keep = journal, [], keep
        try:
            yield
        finally:
            self._resolution, self._resolution_scope, self._resolution_keep = outer

    async def _resolution_step(self, key: str, turn: Turn, work: Callable[[], Any]) -> Any:
        """Resume one stage. Synchronous mutations and its receipt share one checkpoint.

        Async work may be retried until it returns; any committed effects inside it must
        therefore use nested stages. Notifications can be redelivered after a broken
        connection, but their preceding dice, time and facts are never settled twice.
        """
        if self._resolution is None:
            value = work()
            return await value if inspect.isawaitable(value) else value
        full = self._resolution_key(key)
        steps = self._resolution.setdefault("steps", {})
        row = steps.setdefault(full, {"done": False})
        if row.get("done"):
            return row.get("value")
        self._resolution_save(turn)
        old_scope = self._resolution_scope
        self._resolution_scope = full.split("/")
        try:
            dice = self.rules.dice
            self.rules.dice = _ResolutionDice(self, turn, dice, row)
            with self.world.at(turn.interval.get("end")):
                try:
                    value = work()
                finally:
                    self.rules.dice = dice
                # Async stages own nested synchronous receipts. A parent dice cursor
                # must not capture those rolls too, or skipped children shift it on resume.
                if inspect.isawaitable(value):
                    value = await value
            row.update(done=True, value=value)
            self._resolution_save(turn)
            return value
        except BaseException:
            self._resolution_save(turn)
            raise
        finally:
            self._resolution_scope = old_scope

    def _stage_journal(self, key: str, identity: dict, busy: str, **fresh: Any) -> dict:
        """The journal of one stage of the turn going on (pending_turn[key]: its resolution, a push, an
        answer to a choice): the same stage again resumes it; another one while it is unfinished is
        refused with `busy`. A call with no turn going on (a scripted test's) journals for itself
        alone: there is no turn to resume."""
        st = self.state
        saved = st.pending_turn.get(key)
        if saved is not None and saved.get("identity") != identity:
            raise ValueError(busy)
        journal = saved or {"identity": identity, "steps": {}, **fresh}
        if st.pending_turn:
            st.pending_turn[key] = journal
        return journal

    async def resolve(self, action: Action, ruling: Ruling | None) -> Turn:
        """Settle an action in durable stages, including partially completed resolution."""
        async with self._resolution_lock:
            return await self._resolve(action, ruling)

    async def _resolve(self, action: Action, ruling: Ruling | None) -> Turn:
        st = self.state
        identity = {"at": st.turn, "action": action.model_dump(mode="json"),
                    "ruling": ruling.model_dump(mode="json") if ruling else None}
        journal = self._stage_journal("resolution", identity, "尚有未完成的行动结算，请先继续原回合")
        self._resolution = journal
        self._resolution_scope = []
        turn = TURN_RECORD.validate_python(journal["turn"]) if "turn" in journal else Turn(action=action, ruling=ruling)
        try:
            if not journal.get("complete"):
                await self._resolve_stages(action, ruling, turn)
                journal["complete"] = True
                self._resolution_save(turn)
            return turn
        finally:
            self._resolution = None
            self._resolution_scope = []

    async def _resolve_stages(self, action: Action, ruling: Ruling | None, turn: Turn) -> None:
        st = self.state
        if action.kind == "meta":
            return

        def initial() -> bool:
            arrangements = [st.pending_turn.get("arrangement", {}),
                            st.pending_turn.get("prepared_action", {}).get("arrangement", {})]
            for arranged in arrangements:
                turn.notes.extend(f"现场登记未落实：{p}" for p in arranged.get("problems", []))
                if "cue_turn" in arranged:
                    turn.absorb(TURN_RECORD.validate_python(arranged["cue_turn"]))
            preparation = st.pending_turn.get("preparation", {})
            if "turn" in preparation:  # who stepped in or was spoken to before the journey, and what they told (_reply)
                turn.absorb(TURN_RECORD.validate_python(preparation["turn"]))
            if preparation.get("blocked"):
                return False
            late, self._late = self._late, Turn()
            for attr in ("events", "clues", "sanity", "notes", "hints", "world", "fight"):  # fight: one an event began meanwhile
                getattr(turn, attr).extend(getattr(late, attr))
            turn.arrived, turn.first_visit = late.arrived, late.first_visit
            return ruling is not None

        step = self._resolution_step
        await step("interval", turn, lambda: self._spend(0, turn))
        self._dealing = self._engaged(action)
        flagged = await step("beats", turn, lambda: self._play(self._late, self._dealing))
        if flagged:
            await step("beat-flags", turn, lambda: self._flags(flagged, self._late, landed=True))
        if not await step("initial", turn, initial) or ruling is None:
            return
        if action.idea:  # an Idea roll (engine/idea.py): settled on its own, never pushed
            await self._idea(action, turn)
            return
        if ruling.missing:
            await step("missing", turn, lambda: self._lacking(ruling, turn))
        # A round of a fight or a chase, the start of one, or an ordinary action. Decided once (I7): a fight
        # an event of this very turn began, or one that closed before the turn was cut short, does not
        # change what this turn is when it is resumed.
        kind = await step("kind", turn, lambda: self._turn_kind(action))
        if kind["kind"] == "encounter":
            await self._encounter_stages(action, ruling, turn, kind)
            return
        inv = st.investigator
        blocked = await step("entry-condition", turn, lambda: bool(action.arrival_after_success and (
            ruling.missing or not action.destination or not fog.findable(self.m, st, action.destination, viewer=st.investigator.id)
            or self._travel(st.location, action.destination) is None))) if action.arrival_after_success else False
        if blocked:
            await step("entry-blocked", turn, lambda: turn.notes.append(
                "进入所需条件或可用路线尚未成立，调查员仍在原处；没有完成进入或目的地的后续行动。"))
            return

        def travel() -> dict:
            if action.arrival_after_success:
                return {}  # the attempted entry is settled here; successful arrival belongs to _success
            if inv.id in st.apart:
                return self._straggle(inv, ruling.go or action.destination, turn)
            heading = ruling.go or (action.destination if action.kind == "move" else self._heading(action))
            heading = None if heading == st.location else heading
            if heading and heading != ruling.go and not fog.findable(self.m, st, heading, viewer=inv.id):
                heading = None
            if not heading and not ruling.go and (action.kind == "move" or action.destination) and action.destination != st.location:
                turn.notes.append(NOWHERE)
            if not heading:
                return {}
            intended = heading
            stopping = self._stop_on_route(action) and heading != ruling.go
            if stopping:
                heading = self._route(st.location, heading)[1]
                st.journey = intended
            minutes = self._travel(st.location, heading)
            if minutes is None:
                turn.notes.append(f"去不了“{heading}”")
                return {}
            turn.route = self._route(st.location, heading) or []
            coming = self._travellers(action.alone)
            timed = self._spend(minutes, turn, participants=coming)
            return {"heading": heading, "intended": intended, "stopping": stopping, "interval": timed}

        moving = await step("travel", turn, travel)
        # The declared work has an end time, not an extra duration to add after an arrival
        # event has already taken the party through those hours. Freeze it before arrival;
        # failure costs still add time after the attempt, and a route stop does no later work.
        def action_end() -> dict:
            resting = action.kind == "wait" or ruling.rest
            minutes = 0 if moving.get("stopping") else max(0, min(ruling.minutes, WAIT_CAP if resting else ACT_CAP))
            # A later parallel action can use a whole night's effort while adding no
            # global minutes. Freeze its own interval before committing personal work.
            start = self._actor_cursor()
            rest = ({"who": inv.id, "start": start, "end": start + minutes,
                     "interrupted": bool(ruling.mishap or self.battling())} if ruling.rest and minutes > 0 else None)
            self._spend(minutes, turn)
            return {"end": st.minutes, "rest": rest}

        timed = await step("action-end", turn, action_end)
        if moving:
            await step("arrival", turn, lambda: self._arrive(moving["heading"], turn, alone=action.alone, party=True,
                                                             companions=action.companions, interval=moving["interval"]))

            def arrived() -> None:
                if moving["intended"] == moving["heading"]:
                    st.journey = ""
                if moving["stopping"]:
                    name = self.m.location(moving["intended"]).name
                    turn.notes.append(f"路途暂歇，尚未抵达{name}；只描写眼前，不能替玩家继续赶路或执行目的地的行动。")

            await step("arrived", turn, arrived)
            if moving["stopping"]:
                await step("travel-plugins", turn, lambda: self.plugins.run("turn", turn, action=action.model_dump(mode="json")))
                await step("time", turn, lambda: self._time(turn))
                await step("ending", turn, lambda: self._check_ending(turn))
                return

        def elapsed() -> None:
            if ruling.note:
                turn.notes.append(f"导演的安排：{ruling.note}")
            st.minutes = max(st.minutes, timed["end"])
            turn.rest = timed["rest"]

        await step("elapsed", turn, elapsed)

        async def focus() -> None:
            if key := views.after(self.m, st, action.kind, action.target):
                await self._view(key, "focus" if key == views.focus(self.m, self.m.location(st.location), action.target) else "")

        await step("focus", turn, focus)

        scope = await step("audience", turn, lambda: self._check_scope(
            inv.id, self._earlier(action, ruling, st.location) if ruling.repeat else None))

        def check() -> dict:
            passed = not ruling.needs_roll and not ruling.repeat
            event = None
            if ruling.repeat:
                turn.earlier = e = self._earlier(action, ruling, st.location)
                if e is not None:
                    st.checks.remove(e)
                    st.checks.append(e)
                    e.turn = st.turn
                    event = {"type": "repeat", "who": inv.name, "skill": e.skill, "text": e.text, "passed": e.passed}
            elif action.asked and not (ruling.needs_roll and ruling.skill) and action.kind != "move":
                event = {"type": "waived", "who": inv.name, "skill": canonical(action.asked.skill)}
            elif ruling.needs_roll and ruling.skill:
                rolled = self.rules.check(inv, ruling.skill, DIFFICULTY_ZH.get(ruling.difficulty, Level.REGULAR), ruling.bonus)
                turn.primary = len(turn.checks)
                turn.checks.append(rolled)
                passed = rolled.passed
                self._record(action, rolled, scope=scope)
                st.add("dice", rolled.describe(), who=inv.name, audience=scope["roll_audience"])
                event = {"type": "dice", "who": inv.name, **_check_view(rolled)}
                need = rolled.luck_needed()
                luck = need if need is not None and need <= inv.luck else None
                if not passed and (rolled.can_push or luck is not None):
                    # A second chance belongs to the attempted action, not only to clue searches.
                    # Keep its whole success contract: a later success must also transfer the thing,
                    # open the route, reunite the party, or run the action's declared plugin.
                    turn.push = {"skill": rolled.name, "target": rolled.target,
                                 "need": _threshold(rolled.target, ruling.difficulty), "difficulty": ruling.difficulty,
                                 "bonus": ruling.bonus, "clues": self._valid_clues(ruling.clues_on_success, action),
                                 "flags": ruling.set_flags, "can_push": rolled.can_push,
                                 "luck": luck, "left": inv.luck - (luck or 0),
                                 "risk": ruling.on_push_fail.risk.strip(),
                                 "on_fail": ruling.on_fail.model_dump(), "on_push_fail": ruling.on_push_fail.model_dump(),
                                 "npc": action.npc if action.kind in ("talk", "perform") else "", "value": rolled.value,
                                 "rolled": CHECK_DATA.dump_python(rolled, mode="json"),
                                 "tells": self._asks(action, turn), "action": action.summary,
                                 "actor": inv.id, "place": st.location,
                                 **scope,
                                 "action_record": action.model_dump(mode="json"),
                                 "success": ruling.model_dump(mode="json")}
                    st.pending_push = turn.push
                    if rolled.can_push and not turn.push["risk"]:
                        self._debug("push", {"phase": "risk-missing", "action": action.summary,
                                             "note": "未写明具体公开风险，玩家仅收到通用提示；不公开后台失败结算。"})
            if event is not None:
                event["audience"] = scope["roll_audience"]
            return {"passed": passed, "event": event, "failed": not passed and turn.push is None and bool(turn.checks)}

        checked = await step("check", turn, check)
        passed = checked["passed"]
        if checked["event"]:
            await step("check-notice", turn, lambda: self.emit(checked["event"]))
        if checked.get("failed"):
            await self._failed(ruling.on_fail, self._valid_clues(ruling.clues_on_success, action), turn, action)
        hint = st.pending_turn.get("encounter_hint") if st.pending_turn else None
        if hint and hint.get("trigger") in ("on_fail", "on_success") and turn.push is None \
                and passed == (hint["trigger"] == "on_success") and not self.battling():
            await step("encounter-after", turn, lambda: self._start_from(action, turn))

        def available() -> dict:
            gained = self._valid_clues(ruling.auto_clues, action)
            if passed:
                gained += [c for c in self._valid_clues(ruling.clues_on_success, action) if c not in gained]
            spoken = self._spoken(gained, self._asks(action, turn))  # the one talked to tells these, in their own time (talk)
            gained = [c for c in gained if c not in spoken and module_graph.original_source_at_hand(
                self.m, self.m.clue(c), st.location, self.world.present(st.location), record=self.world.rec)]
            return {"known": list(st.flags), "gained": gained, "spoken": spoken}

        found = await step("available", turn, available)
        if passed:
            completed = await self._success(ruling, turn)
            if not completed:
                await step("travel-plugins", turn, lambda: self.plugins.run("turn", turn, action=action.model_dump(mode="json")))
                await step("time", turn, lambda: self._time(turn))
                await step("ending", turn, lambda: self._check_ending(turn))
                if turn.primary is not None:
                    await step("settle", turn, lambda: self._settle(st.checks[-1]))
                return
        await step("gain", turn, lambda: self._gain(found["gained"], turn, action))
        if turn.checks:
            won = [c for c in turn.clues if c in ruling.clues_on_success]
            done = [f for f in ruling.set_flags if f in st.flags and f not in found["known"]]
            npc = action.npc if action.kind in ("talk", "perform") else ""
            await step("outcome", turn, lambda: self.emit({"type": "outcome", "who": inv.name,
                       "audience": scope["audience"],
                       "lines": self._led_to(won, done, passed, npc, offer=turn.push)}))
        if ruling.now:
            await step("now", turn, lambda: self._flags(ruling.now, turn))

        if (action.kind == "wait" or ruling.rest) and ruling.mishap.strip():  # a bad night (cards.py)
            await self._mishap(ruling.mishap, turn, "rest-mishap")
        if passed:
            for index, call in enumerate(ruling.plugin_calls):
                await step(f"action-plugin:{index}", turn, lambda call=call: self.plugins.run("action", turn, **call.model_dump(mode="json")))
        await step("settled-plugins", turn, lambda: self.plugins.settled(turn))
        await step("turn-plugins", turn, lambda: self.plugins.run("turn", turn, action=action.model_dump(mode="json")))
        await step("time", turn, lambda: self._time(turn))
        if action.kind == "daily":
            await step("progress", turn, lambda: setattr(st, "progress_at", st.turn))
        await step("nudge", turn, lambda: self._nudge(turn))
        await step("ending", turn, lambda: self._check_ending(turn))
        await step("party-down", turn, lambda: self._party_down(turn))
        for item in self._items_for(action):
            await step(f"item:{item.id}", turn, lambda item=item: self.emit({"type": "item", "id": item.id, "name": item.name,
                       "text": item.description, "art": item.art}))
        if turn.primary is not None or turn.earlier:
            await step("settle", turn, lambda: self._settle(st.checks[-1]))

    async def _success(self, ruling: Ruling, turn: Turn) -> bool:
        """The same success operations for an immediate, lucky, or pushed success.

        Each operation retains its own receipt, so a dropped connection cannot transfer a
        possession twice. Effort and automatic discoveries belong to the original attempt;
        successful entry alone adds its route time and visit.
        """
        step = self._resolution_step
        if turn.action is not None and turn.action.arrival_after_success:
            if not await step("entry", turn, lambda: self._enter(turn.action, turn)):
                return False
        def flags() -> list[str]:
            immediate = []
            for flag in ruling.set_flags:
                required = list(dict.fromkeys(req for end in self.m.endings
                                              if end.condition == f"flag:{flag}" for req in end.requires))
                if required and not self._requirements(required):
                    self.state.pending_story_flags[flag] = required
                else:
                    immediate.append(flag)
            return immediate

        immediate = await step("completion-flags", turn, flags)
        await step("flags", turn, lambda: self._flags(immediate, turn))

        def changes() -> None:
            adapters.settle_changes(self.world, ruling.changes, source="arbiter")
            self._equipment(ruling, turn)
            self._takes(ruling, turn)

        await step("changes", turn, changes)
        if ruling.rejoin:
            await step("rejoin", turn, lambda: self._rejoin(ruling.rejoin, turn))
        if any(e.kind == "obstacle" for e in ruling.effects):
            await step("ways", turn, lambda: self._ways(ruling, turn))
        return True

    async def _enter(self, action: Action, turn: Turn) -> bool:
        """Commit successful entry once, including its normal route time and visit consequences."""
        st, step = self.state, self._resolution_step

        def route() -> dict:
            path = self._route(st.location, action.destination)
            if not path or not fog.findable(self.m, st, action.destination, viewer=st.investigator.id):
                turn.notes.append("原定路线已无法使用，尚未抵达目的地。")
                return {}
            stopping = action.travel == "stroll" and len(path) > 2
            target = path[1] if stopping else action.destination
            return {"target": target, "route": self._route(st.location, target),
                    "minutes": self._travel(st.location, target), "stopping": stopping}

        moving = await step("route", turn, route)
        if not moving:
            return False

        def elapsed() -> None:
            turn.route = moving["route"]
            moving["interval"] = self._spend(moving["minutes"], turn, participants=self._travellers(action.alone))

        await step("elapsed", turn, elapsed)
        await step("arrival", turn, lambda: self._arrive(moving["target"], turn, alone=action.alone, party=True,
                                                         companions=action.companions, interval=moving["interval"]))

        def arrived() -> None:
            st.journey = action.destination if moving["stopping"] else ""
            if moving["stopping"]:
                name = self.m.location(action.destination).name
                turn.notes.append(f"路途暂歇，尚未抵达{name}；只描写眼前，不能替玩家继续赶路或执行目的地的行动。")

        await step("arrived", turn, arrived)
        return not moving["stopping"]

    def _lacking(self, ruling: Ruling, turn: Turn) -> None:
        """What this action lacks and so cannot do (Ruling.missing) is kept for the turn: whoever
        registers a thing this turn cannot bring it out (world/ops.Bring), and the keeper hears it."""
        rec = self.world.rec
        rec.missing, rec.missing_turn = [x.strip() for x in ruling.missing if x.strip()], self.state.turn
        if rec.missing:
            turn.notes.append(f"这次行动缺{'、'.join(rec.missing)}，做不成；叙述和场面里都不能把它变出来。")

    def _ways(self, ruling: Ruling, turn: Turn) -> None:
        """Ways here whose obstacle this game changed (WorldRecord.obstacles), as an ordinary ruling
        changes them again (Ruling.effects, kind obstacle): mended as the module has it, opened or
        cleared, broken, shut or blocked; the next chase there finds them so. In one op."""
        from .encounter.site import MEND, ruled, way_place

        st, rec = self.state, self.world.rec
        found = []
        for e in ruling.effects:
            if e.kind != "obstacle":
                continue
            if e.target not in rec.obstacles or way_place(self.m, e.target) != st.location:
                turn.notes.append(f"{e.target} 不是这里本局改变过的通路，没有改变。")
                continue
            ob = rec.obstacles[e.target].model_copy(deep=True)
            if re.search(MEND, e.what):
                found.append(effects.Obstacle(way=e.target, obstacle=None))
            elif ruled(ob, e.what, st.investigator.id):
                found.append(effects.Obstacle(way=e.target, obstacle=ob))
        if found:
            done = effects.commit(self.world, found, op_id=f"ways:{st.turn}:{st.investigator.id}", source="arbiter")
            if not done.ok:
                turn.notes.append("通路的变化没有落实：" + "；".join(done.problems))

    def _straggle(self, inv, dest: str, turn: Turn) -> dict:
        """Where someone apart from the party (GameState.apart) goes: there alone, the party staying where
        it is (no split party in this version: no scene or event of their own there); back to the party is
        a rejoin (rule), not this. {}: the party does not move."""
        st = self.state
        here = st.apart[inv.id]
        if not dest or dest in (here, st.location) or not fog.findable(self.m, st, dest, viewer=inv.id):
            return {}
        minutes = self._travel(here, dest)
        if minutes is None:
            turn.notes.append(f"{inv.name}从{self.world.name(here)}去不了“{dest}”")
            return {}
        st.apart[inv.id] = dest
        self._spend(minutes, turn)
        turn.notes.append(f"{inv.name}独自从{self.world.name(here)}去了{self.world.name(dest)}，没有和大家在一起；大家还在"
                          f"{self.world.name(st.location)}。")
        return {}

    def _rejoin(self, rejoins: list, turn: Turn) -> None:
        """Those of the investigators apart from the party (GameState.apart) whom a ruling brings back
        (Ruling.rejoin): with the party again, as the ruling says they came; never someone who is not
        apart, nor someone walking back who cannot act. What does not hold, the keeper hears of."""
        st = self.state
        words = {"carried": "被背了回来", "fetched": "被找到领了回来", "walked": "自己走了回来"}
        for r in rejoins:
            inv = next((i for i in st.investigators if i.id == r.who), None)
            if inv is None or r.who not in st.apart:
                turn.notes.append(f"{inv.name if inv else r.who}本来就和大家在一起，不用会合。")
                continue
            if r.how == "walked" and not inv.can_act:
                turn.notes.append(f"{inv.name}现在没法自己走回来，还在{self.world.name(st.apart[r.who])}。")
                continue
            was = st.apart.pop(r.who)
            text = f"{inv.name}从{self.world.name(was)}{words[r.how]}，和大家在一起了"
            self.world.apply(Occur(id=f"rejoin:{st.turn}:{r.who}", actor=inv.id, participants=[inv.id], place=st.location,
                                   text=text, visibility="public"))
            turn.notes.append(text + "。")

    def _takes(self, ruling: Ruling, turn: Turn) -> None:
        """Everyday things the investigator takes up in this action (Ruling.takes), registered in
        their hands (world/adapters.bring_ops); what cannot be, the keeper hears of."""
        if not ruling.takes:
            return
        inv = self.state.investigator
        found = adapters.bring_ops(self.world, ruling.takes, actor="arbiter", holder=inv.id)
        problems = list(found.problems)
        for op in found.ops:
            result = self.world.apply(op)
            problems += [] if result.ok else [f"{getattr(op, 'name', '') or op.item}：{'；'.join(result.problems)}"]
        if problems:
            turn.notes.append(f"【未落实】{inv.name}没拿到：{'；'.join(problems)}。叙述里照实交代，不写成已经拿到。")

    def _equipment(self, ruling: Ruling, turn: Turn) -> None:
        """Maintain important gear after success, without publishing a permanent inventory UI."""
        if not ruling.equipment:
            return
        trial = World(self.m.model_copy(deep=True), self.state.model_copy(deep=True))
        who = self.state.investigator.id
        ops = []
        for index, change in enumerate(ruling.equipment):
            ident = world.item_id(trial.m, trial.st, change.item, holder=who)
            if ident is None:
                turn.notes.append("装备名称对应多个物件，尚未落实改动；需使用实际物件的记录编号。")
                return
            change = change.model_copy(update={"item": ident})
            ent = trial.rec.entities.get(change.item)
            holder = ent.at if ent else world.owner(trial.m, trial.st, change.item)
            # A search can reveal another person's property, but cannot manipulate it
            # on that person's behalf. Physical taking remains a separate transfer.
            observing = change.reveal and change.equipment is None and change.quantity is None and change.status is None
            if holder != who and not observing:
                turn.notes.append("这项装备操作尚未完成：只能整理调查员实际持有的物品，观察不等于取走。")
                return
            op = Equip(**change.model_dump(), actor="engine" if observing else who,
                       id=f"equipment:{self.state.turn}:{who}:{index}")
            result = trial.apply(op)
            if not result.ok:
                self._debug("equipment", {"phase": "rejected", "op": op.model_dump(mode="json"), "problems": result.problems})
                turn.notes.append("装备改动未通过实际持有或操作条件检查，不叙述已经完成的取用、藏匿、发现或消耗。")
                return
            ops.append(op)
        for op in ops:
            result = self.world.apply(op)
            if result.ok:
                if op.reveal:
                    turn.notes.append(f"眼前实际发现或出示：{op.evidence}")
                else:
                    item = self.world.rec.entities.get(op.item)
                    if item:
                        mode = {"carried": "携带", "worn": "穿戴", "held": "手持", "concealed": "藏匿"}[item.equipment]
                        turn.notes.append(f"调查员的重要装备已更新：{item.name}，{mode}，余量{item.quantity}"
                                          + (f"，{item.status}" if item.status else "") + "。只在与本次动作有关时叙述，不逐项播报装备栏。")

    async def push(self, choice: str | dict) -> Turn:
        """Resume the saved choice without paying luck or rolling its push twice. `choice` is "luck" or
        "drop", or {"choice": "push", "how": "..."}: a push the player said how to make (Keeper
        Rulebook p. 73), ruled again once before it is rolled (_method)."""
        mode, how = push_choice(choice)
        async with self._resolution_lock:
            st = self.state
            identity = {"kind": "push", "at": st.turn, "mode": mode, "active": st.active} | ({"how": how} if how else {})
            if st.pending_turn.get("push_resolution") is None and not st.pending_push:
                return Turn()
            journal = self._stage_journal("push_resolution", identity, "已有未完成的检定选择，请先继续原选择",
                                          pending=dict(st.pending_push or {}))
            self._resolution, self._resolution_scope = journal, []
            turn = TURN_RECORD.validate_python(journal["turn"]) if "turn" in journal else Turn()
            try:
                if not journal.get("complete"):
                    await self._push_stages(mode, journal["pending"], turn, how)
                    journal["complete"] = True
                    self._resolution_save(turn)
                return turn
            finally:
                self._resolution, self._resolution_scope = None, []

    async def _push_stages(self, mode: str, pending: dict, turn: Turn, how: str = "") -> None:
        st = self.state
        step = self._resolution_step
        record = st.checks[-1] if st.checks and st.checks[-1].turn == st.turn else None
        inv, npc = st.investigator, pending.get("npc", "")

        def initial() -> dict:
            if pending["actor"] != inv.id:
                raise ValueError("这次检定属于另一名调查员")
            if pending["place"] != st.location:
                raise ValueError("检定现场已经改变，请先处理原行动")
            if mode == "push" and not pending["can_push"]:
                raise ValueError("这次检定不能孤注一掷")
            if mode == "luck" and (pending.get("luck") is None or pending["luck"] > inv.luck):
                raise ValueError("这次检定无法使用当前的幸运点数")
            turn.action = Action.model_validate(pending["action_record"])
            turn.ruling = Ruling.model_validate(pending["success"])
            self._spend(0, turn)
            st.pending_push = None
            return {"flags": list(st.flags), "minutes": st.minutes}

        start = await step("initial", turn, initial)
        known = start["flags"]
        if mode == "drop":
            await self._failed(Failure.model_validate(pending.get("on_fail") or {}), pending["clues"], turn, None)
            await step("plugins", turn, lambda: self.plugins.settled(turn))
            won = [c for c in turn.clues if c in pending["clues"]]
            await step("outcome", turn, lambda: self.emit({"type": "outcome", "who": inv.name,
                       "audience": pending.get("audience"),
                       "lines": self._led_to(won, [], False, npc) if won else ["放弃了，换条路"]}))
        else:
            await self._pushed(mode, pending, turn, record, known, how)
        # A fight or a chase the plan said this check's outcome sets off (Plan.encounter, on_fail or
        # on_success): the pushed or dropped roll is that outcome (the review: it never began after a push).
        hint = st.pending_turn.get("encounter_hint") if st.pending_turn else None
        if hint and hint.get("trigger") in ("on_fail", "on_success") and not self.battling():
            passed = mode != "drop" and record is not None and record.passed
            if passed == (hint["trigger"] == "on_success"):
                await step("encounter-after", turn, lambda: self._start_from(Action(kind="other", summary=""), turn))
        # What the failure cost settles as any turn's does: the clock moved on, so what was due by
        # now happens (scheduled events, the rest of the world, rule timers), and an ending it
        # brought about ends the game.
        if st.minutes > start["minutes"]:
            await step("time", turn, lambda: self._time(turn))
        await step("ending", turn, lambda: self._check_ending(turn))
        await step("settle", turn, lambda: self._settle(record))

    async def _pushed(self, mode: str, pending: dict, turn: Turn, record: CheckRecord | None, known: list[str],
                      how: str = "") -> None:
        """The second chance: luck spent, or the pushed roll and what it brings. A push made a stated
        way is ruled again first (_method): a way that cannot reach the goal is refused and nothing is
        rolled, the failure standing as when it is dropped; otherwise its time passes before the roll."""
        st, step = self.state, self._resolution_step
        inv, npc = st.investigator, pending.get("npc", "")
        lucky = mode == "luck" and pending.get("luck") is not None
        if mode == "push":  # a push says how it is made (push_choice), ruled again first
            method = await step("method", turn, lambda: self._method(pending, how))

            def told() -> None:
                st.add("player", f"孤注一掷：{how}", who=inv.name, audience=pending.get("audience"))
                if method["achievable"]:
                    self._spend(method["minutes"], turn)
                    turn.notes.append(f"孤注一掷的做法：{how}（约 {method['minutes']} 分钟）")
                else:
                    why = method["why"].rstrip("。") or "这样做达不到目标"
                    turn.notes.append(f"孤注一掷不成立（{how}）：{why}。没有掷骰，原来的失败照旧。")

            await step("method-told", turn, told)
            if not method["achievable"]:
                await self._failed(Failure.model_validate(pending.get("on_fail") or {}), pending["clues"], turn, None)
                await step("plugins", turn, lambda: self.plugins.settled(turn))
                await step("outcome", turn, lambda: self.emit({"type": "outcome", "who": inv.name,
                           "audience": pending.get("audience"),
                           "lines": [f"孤注一掷不成立：{method['why'] or '这样做达不到目标'}", "没有掷骰，按放弃处理"]}))
                return
            pending = {**pending, **method["pending"]}

        def check() -> dict:
            if lucky:
                adjusted = CHECK_DATA.validate_python(pending["rolled"])
                adjusted.luck_spent += pending["luck"]
                turn.primary = len(turn.checks)
                turn.checks.append(adjusted)
                inv.luck -= pending["luck"]
                st.add("dice", f"花费幸运 {pending['luck']} 点，{pending['skill']} 检定改为成功", who=inv.name,
                       audience=pending.get("roll_audience"))
                self._settle(record, True, f"{record.text}，花 {pending['luck']} 点幸运改为成功" if record else "")
                return {"passed": True, "notice": {"type": "luck", "who": inv.name, "spent": pending["luck"],
                                                   "left": inv.luck, "audience": pending.get("roll_audience")}}
            diff = DIFFICULTY_ZH.get(pending["difficulty"], Level.REGULAR)
            rolled = self.rules.check(inv, pending["skill"], diff, pending["bonus"], pushed=True, target=pending["target"])
            turn.primary = len(turn.checks)
            turn.checks.append(rolled)
            st.add("dice", rolled.describe(), who=inv.name, audience=pending.get("roll_audience"))
            self._settle(record, rolled.passed, rolled.describe())
            return {"passed": rolled.passed, "notice": {"type": "dice", "who": inv.name,
                                                       "audience": pending.get("roll_audience"), **_check_view(rolled)}}

        checked = await step("check", turn, check)
        passed = checked["passed"]
        await step("check-notice", turn, lambda: self.emit(checked["notice"]))
        if passed:
            ruling = Ruling.model_validate(pending["success"])
            if not await self._success(ruling, turn):
                await step("plugins", turn, lambda: self.plugins.settled(turn))
                return
            spoken = await step("spoken", turn, lambda: self._spoken(pending["clues"], pending.get("tells", "")))
            discovered = [c for c in pending["clues"] if c not in spoken and module_graph.original_source_at_hand(
                self.m, self.m.clue(c), st.location, self.world.present(st.location), record=self.world.rec)]
            await step("gain", turn, lambda: self._gain(discovered, turn, None))
            for index, call in enumerate(ruling.plugin_calls):
                await step(f"action-plugin:{index}", turn,
                           lambda call=call: self.plugins.run("action", turn, **call.model_dump(mode="json")))
        else:  # what was said before the choice now happens (Failure, Ruling.on_push_fail)
            await self._failed(Failure.model_validate(pending.get("on_push_fail") or {}), pending["clues"], turn, None, pushed=True)
        await step("plugins", turn, lambda: self.plugins.settled(turn))
        won = [c for c in turn.clues if c in pending["clues"]]
        done = [f for f in pending.get("flags", []) if f in st.flags and f not in known]
        lines = self._led_to(won, done, passed, npc)
        if lucky:
            lines.insert(0, f"花了 {pending['luck']} 点幸运，算作成功")
        elif not passed:
            lines.insert(0, "孤注一掷也没成")
        await step("outcome", turn, lambda: self.emit({"type": "outcome", "who": inv.name, "lines": lines,
                                                     "audience": pending.get("audience")}))

    async def _method(self, pending: dict, how: str) -> dict:
        """The arbiter's word on how a push is made (Cast.repush, one call): can the goal still be
        reached this way, the skill and difficulty for it (unchanged unless the situation changed),
        the time it takes (time always passes, Keeper Rulebook p. 73), and what failing it this way brings."""
        r = await self.cast.repush(self.state, pending, how)
        out: dict = {"achievable": r.achievable, "why": r.why.strip(), "minutes": max(1, min(r.minutes, ACT_CAP)), "pending": {}}
        changed: dict = {}
        skill = canonical(r.skill) if r.skill.strip() else pending["skill"]
        if skill != pending["skill"]:
            target = self.state.investigator.value(skill)
            changed.update(skill=skill, target=target)
        if r.difficulty and r.difficulty != pending["difficulty"]:
            changed["difficulty"] = r.difficulty
        if changed:
            changed["need"] = _threshold(changed.get("target", pending["target"]), changed.get("difficulty", pending["difficulty"]))
        risk = r.on_push_fail
        if risk.model_dump() != Failure().model_dump():
            if r.achievable:
                # A push is still the same goal. The module's explicit failure finds
                # survive a revised method; its costs may change with that method.
                promised = (pending.get("on_push_fail") or {}).get("clues", [])
                risk = risk.model_copy(update={"clues": self._valid_clues([*promised, *risk.clues], None)})
            changed.update(on_push_fail=risk.model_dump(), risk=risk.risk.strip())
            if r.achievable and not risk.risk.strip():
                self._debug("push", {"phase": "risk-missing", "method": how,
                                     "note": "新做法未写明具体公开风险；不使用后台失败结算作为公开文本。"})
        out["pending"] = changed
        return out

    async def talk(self, turn: Turn) -> None:
        """NPC replies and stage performances, after the engine has resolved checks. Each settles
        through _reply; a resumed turn goes on from the receipts it kept (Turn.replies)."""
        st, m = self.state, self.m
        action = turn.action
        if (action is not None and action.declared is not None) or self.fought_this_turn():
            # A round of a fight, or a turn that began one (a talk that went wrong): the encounter's voices
            # are its own (engine/encounter), no ordinary reply on top of it (design §1.6). Those the keeper
            # plan has speak this round say it through the NPC's own reply, as anywhere else.
            for npc_id in turn.voices:
                if any(n.id == npc_id for n in m.npcs) and self.world.place(npc_id) == st.location:
                    npc = m.npc(npc_id)
                    await self._reply(turn, f"encounter:{npc_id}:{st.turn}", npc_id,
                                      lambda npc_id=npc_id: self.cast.npc(st, npc_id, self._encounter_message(turn, npc_id)),
                                      self._keep, status=f"等待 {npc.name} 开口…")
            await self._world(turn)
            await self._people()
            self._keep(turn)
            return
        if addressed := self._addressed(action):
            key = f"talk:{addressed}"
            if key not in turn.replies and any(nid == addressed for nid, _ in turn.npc_lines):
                pass  # answered already this turn (stepping in, before the journey): never asked the same thing twice
            elif key not in turn.replies and addressed not in self._callable(turn):
                turn.notes.append(f"{addressed} 不在这里")
            elif turn.primary is None and turn.earlier is None:
                npc = m.npc(addressed)
                remote = action.communication == "remote"
                await self._reply(turn, key, npc.id, lambda: self.cast.npc(st, npc.id, self._npc_message(turn, npc.id),
                                  **({"communication": "remote"} if remote else {})), self._keep,
                                  record=partial(self._exchanged, npc.id, action), status=f"等待 {npc.name} 回应…",
                                  audience=[st.investigator.id] if remote else None,
                                  communication=action.communication)
        await self._perform_events(turn, self._keep)
        # Important information heard in a zero-minute exchange can change a person's
        # next action now, using the same allowance as this turn's elapsed-time work.
        await self._world(turn)
        await self._people()
        await self._check_ending(turn)
        self._keep(turn)

    async def _perform_events(self, turn: Turn, keep: Callable[[Turn], None]) -> None:
        for ev in turn.events:
            if ev.performer and ev.id not in turn.performed:
                name = self.m.npc(ev.performer).name
                await self._reply(turn, f"perform:{ev.id}", ev.performer,
                                  lambda ev=ev: self.cast.perform(self.state, ev.performer, ev.performance,
                                                                 self._deeds(ev.performer)),
                                  keep, kind="performance", status=f"{name}登台了…")
                turn.performed.append(ev.id)
                keep(turn)

    @staticmethod
    def _addressed(action: Action | None) -> str:
        """The one talk() asks this turn: the NPC an action speaks to (a talk, a performance, a daily
        word with them). Nobody when it only watches them (心理学 without a word), only invites them
        along (settled before the journey) or is a daily action that says nothing: then what the
        turn finds about them is found, not told (_spoken)."""
        if action is None or not action.npc:
            return ""
        if not (action.kind in ("talk", "perform") or (action.kind == "daily" and (action.speech or action.casual))):
            return ""
        if action.destination and not action.speech and action.npc in action.companions:
            return ""  # the invitation was already resolved before departure
        if action.approach == "心理学" and not action.speech:
            return ""  # watching someone's demeanour does not start a conversation
        return action.npc

    def _keep(self, turn: Turn) -> None:
        """The turn as it stands, saved where its caller keeps it (graph.build's keep)."""
        if self.turn_checkpoint:
            self.turn_checkpoint(turn)

    def _voice_intents(self, turn: Turn) -> list[dict[str, str]]:
        """Unplayed participation ideas for the keeper, never instructions delivered to an NPC.

        A check may have invalidated a pre-roll idea. The keeper sees its actual result before
        choosing a scoped performance; the voice does not receive the player's hidden objective.
        """
        if turn.interlude or turn.action is None:
            return []
        st = self.state
        saved = st.pending_turn.get("initiative") if st.pending_turn else None
        plans = [Initiative.model_validate(x) for x in saved] if saved is not None else self.initiative
        spoken = {nid for nid, _ in turn.npc_lines}
        here = set(self._present())
        out = [{"npc": x.npc, "intention": x.does} for x in plans if not x.first and x.npc in here and x.npc not in spoken]
        if (addressed := self._addressed(turn.action)) and addressed in self._callable(turn) and addressed not in spoken:
            out.insert(0, {"npc": addressed, "intention": "回应调查员实际提出的问题；以最终检定和当前记录确定本次可演范围"})
        return out

    def _callable(self, turn: Turn) -> list[str]:
        """Physical voices, plus the one explicitly addressed over this action's remote channel."""
        when = int(turn.interval["end"]) if turn.interval else self.world.now
        out = list(self._present(at=when))
        action = turn.action
        if action is not None and action.communication == "remote" and (addressed := self._addressed(action)):
            ent = self.world.entity(addressed)
            if ent is not None and ent.kind == "npc" and ent.status not in ("死亡", "失踪"):
                out.append(addressed)
        return list(dict.fromkeys(out))

    async def _step_in(self, action: Action) -> None:
        """Those the keeper's plan has step in before the action is settled (Initiative.first: the
        warden's 「别碰！」 before the gears are touched, a lamp handed over before the cellar): played
        now, before the arbiter rules, so the ruling and the roll go on what they said, did and told
        (the review of 2026-10-20: the action was settled first, and the warden spoke too late). Their
        replies settle as the travel exchanges' do, into the preparation's turn (_preparing), which
        joins the action's; _join_in passes them by. The review of 2026-10-21: what the warden told
        here never reached the notebook."""
        st = self.state
        saved = st.pending_turn.get("initiative") if st.pending_turn else None
        plans = [Initiative.model_validate(x) for x in saved] if saved is not None else self.initiative
        first = [x for x in plans if x.first]
        if action.kind == "meta" or not first or not st.pending_turn:
            return
        journal, turn, keep = self._preparing(action)
        for x in first:
            key = f"first:{x.npc}"
            if key in journal["done"]:
                continue
            if key in turn.replies or x.npc in self._present():
                await self._reply(turn, key, x.npc, lambda x=x: self.cast.npc(st, x.npc, self._joining(turn, x)),
                                  keep, record=partial(self._initiated, x.npc, first=True), joined=True,
                                  status=f"{self.m.npc(x.npc).name}开口了…")
            journal["done"].append(key)
            keep(turn)

    def _initiated(self, npc_id: str, line: str, *, first: bool = False, key: str = "", private: bool = False) -> None:
        """What one joining in of its own accord said goes into the world record, before everyone
        there: ahead of the action (Initiative.first, _step_in) or after it (_join_in)."""
        st, inv, name = self.state, self.state.investigator, self.m.npc(npc_id).name
        text = f"{name}抢在{inv.name}动手之前：「{line}」" if first else f"{name}主动对{inv.name}：「{line}」"
        ident = f"talk:{st.turn}:{npc_id}:{key or 'initiative'}"
        done = self.world.apply(Occur(id=ident, actor=inv.id, participants=[npc_id], place=st.location,
                                     text=text, visibility="private" if private else "public", transcript=True))
        if done.ok:
            self.world.observe(st.location, [inv.id] if private else self.world.observers(st.location), done.event,
                               leaving=npc_id if self.world.place(npc_id) != st.location else "")

    def _hosted_at(self) -> int | None:
        """An accepted arrangement's existing receipt, including a prepared scene's continuation."""
        pending = self.state.pending_turn
        arrangements = (pending.get("arrangement", {}), pending.get("prepared_action", {}).get("arrangement", {}))
        return next((r["at"] for r in arrangements
                     if r.get("plan") and r.get("landed") and r.get("at") == self.state.turn), None)

    def _held(self) -> set[str]:
        """An accepted keeper plan owns this turn's actual scene, including silent bystanders.
        Prepared schedules still run; only autonomous decisions wait. Departed people are free
        to decide offscreen, and casts without a keeper plan retain the initiative-only boundary.
        """
        st = self.state
        if self._hosted_turn == st.turn or self._hosted_at() == st.turn:
            return set(self.world.present(st.location))
        saved = st.pending_turn.get("initiative") if st.pending_turn else None
        return self._directed | {x["npc"] for x in saved or [] if isinstance(x, dict) and x.get("npc")}

    def _joining(self, turn: Turn, x: Initiative) -> str:
        """A pre-ruling reaction to observable words and attempted actions, never the concealed plan."""
        st, m = self.state, self.m
        inv, action = st.investigator, turn.action
        heard = []
        if action is not None:
            if action.visible:
                heard.append(f"{inv.name}正要：{action.visible}（还没做成）。")
            if action.speech:
                heard.append(f"{inv.name}说：「{action.speech}」。")
        return (f"（系统：{st.clock}，{m.location(st.location).name}。{''.join(heard)}"
                f"这一轮你抢在前面主动：{x.does}。这是守秘人的安排，不是你知道的事：只用你自己的知识、亲历和口吻演出来，不知道的不说。"
                "当场拿出或递出东西、走开、同行，照常写在回复里；不替调查员说话或做决定。"
                + self._npc_disclosure(turn, x.npc) + "）")

    def _exchanged(self, npc_id: str, action: Action, line: str, *, private: bool = False, key: str = "") -> None:
        """A conversation goes into the world record: everyone there saw it, an important NPC keeps
        it as a memory. Significant exchanges can wake its autonomous action this turn."""
        st, npc = self.state, self.m.npc(npc_id)
        inv = st.investigator
        remote = action.communication == "remote"
        private = private or remote
        said = action.speech or (action.visible if not remote else "") or "（没有逐字台词）"
        text = f"{inv.name}和{npc.name}说话：「{said}」{npc.name}答：「{line}」"
        ident = f"talk:{st.turn}:{inv.id}:{npc_id}" + (f":{key}" if key else "")
        done = self.world.apply(Occur(id=ident, actor=inv.id, participants=[npc_id], place=st.location,
                                     text=text, visibility="private" if private else "public", reactive=not action.casual, transcript=True))  # fmt: skip
        if done.ok and not remote:
            self.world.observe(st.location, [inv.id] if private else self.world.observers(st.location), done.event,
                               leaving=npc_id if self.world.place(npc_id) != st.location else "")

    def speak(self, inv_id: str, line: str, to: str = "", *, audience: list[str] | None = None,
              private: bool = False) -> LogEntry:
        """Words an investigator says to the party (engine/chat.py): no keeper, no action, no time.
        The log keeps who said it, to whom and where, for the keeper's next report, the chronicler and
        the AI companions' situation; the world record keeps it at the place, so the NPCs there heard
        it (World.emit), and what they make of it waits until the party moves on (scheduler.TALK)."""
        st = self.state
        inv = st.find(inv_id)
        nearby = recipients.listeners(st, inv_id)
        if inv_id not in nearby:
            raise ValueError("调查员当前无法交谈。")
        where = recipients.location(st, inv_id)
        if audience is not None:
            audience = list(dict.fromkeys([inv_id, *audience]))
            if any(who not in nearby for who in audience):
                raise ValueError("私语收件人不在身边，或当前无法听见。")
        elif private:
            raise ValueError("私语需要指定收件人。")
        elif len(nearby) != len(st.investigators):
            audience = nearby  # ordinary speech is not heard across separate places
        entry = st.add("speech", f"在{self.world.name(where)}{'私下' if private else ''}对{to or '同伴'}说：{line}",
                       who=inv.name, audience=audience)
        heard = [who for who in (audience if audience is not None else nearby) if who != inv_id]
        self.world.apply(Occur(id=f"{scheduler.TALK}{st.turn}:{inv.id}:party:{st.logged}", actor=inv.id, participants=heard,
                               place=where if where in self.world.rec.entities else "",
                               text=f"{inv.name}{entry.text}", visibility="private" if private else "public", transcript=True))  # fmt: skip
        return entry

    def _party_talk(self) -> list[str]:
        """What the investigators said to each other since the keeper last told: for its report."""
        st = self.state
        last = max((i for i, e in enumerate(st.log) if e.kind == "keeper"), default=-1)
        names = {i.id: i.name for i in st.investigators}

        def line(e: LogEntry) -> str:
            scope = ("【仅" + "、".join(names.get(who, who) for who in e.audience) + "知晓，勿公开复述】") if e.audience is not None else ""
            return f"{scope}{e.who}{e.text}"

        return [line(e) for e in st.log[last + 1:] if e.kind == "speech"][-6:]

    def _unsettled(self, npc_id: str) -> list[str]:
        """What an NPC's last reply meant to do and could not (agents/cast.Cast.unsettled): the
        words stand, the deed did not land, and the keeper tells how it really went."""
        failed = self.cast.unsettled.pop(npc_id, [])
        if not failed:
            return []
        name = self.m.npc(npc_id).name
        return [f"【未落实】{name}刚才的回应里有做不到的事：{'；'.join(failed)}。叙述里按实际情况交代（东西不在身上、去不了那里），"
                "不写成已经做到，也不删改他说过的话。"]

    async def _reply(self, turn: Turn, key: str, npc_id: str, ask: Callable[[], Awaitable[str]], keep: Callable[[Turn], None],
                     *, record: Callable[[str], None] | None = None, joined: bool = False, kind: str = "npc",
                     status: str = "", audience: list[str] | None = None, communication: str = "local") -> Said:
        """One NPC reply settled, whatever asked for it (talk, _join_in, _step_in, prepare): its words,
        and what it meant to do and could not, go into `turn`, the log and the world record (`record`),
        with its receipt (Said: what it told, Dialogue.told), and are kept before anything is shown;
        then the words are shown (`kind`: an npc line or a performance), and what it told goes in the
        notebook, the card after the words. What it handed over or where it went landed with the
        reply itself (Cast.npc). The receipt is the turn's, under `key`: a turn resumed after a crash
        goes on from it, never asking the NPC again (the review of 2026-10-21: the line was kept and
        what the warden told was lost)."""
        st, m = self.state, self.m
        npc = m.npc(npc_id)
        said = turn.replies.get(key)
        if said is None:
            when = turn.interval.get("end", self._actor_cursor())
            available = self.world.available_at(npc_id)
            if available is not None and available > when:
                turn.notes.append(f"{npc.name}已有更晚时段的实际行动，本段较早的交谈尚未发生；按当前记录继续。")
                said = turn.replies[key] = Said(npc=npc_id, line="", shown=True, heard=True)
                keep(turn)
                return said
            allowed = knowledge.disclosable_clues(m, st, npc_id, record=self.world.rec, present=communication != "remote")
            listeners = self.world.observers(st.location, at=when)
            if audience is None and len(listeners) != len(st.investigators):
                audience = listeners
            if status:
                await self.emit({"type": "status", "text": status, "audience": audience})
            with self.world.at(when):
                line = await ask()
            # Taken from the cast at once (Cast.told, Cast.unsettled): nothing waits between the reply
            # and its receipt, kept below with the words.
            told = self.cast.told.pop(npc_id, [])
            said = turn.replies[key] = Said(npc=npc_id, line=line,
                                           told=self._told_clues(npc_id, [cid for cid in told if cid in allowed], present=False),
                                           media=getattr(self.cast, "media", {}).pop(npc_id, []), audience=audience,
                                           communication=communication)
            if audience is not None:
                said.media = [{**raw, "to": audience} for raw in said.media]
            if kind == "performance":
                turn.performance = (npc_id, line)
            else:
                turn.npc_lines.append((npc_id, line))
            if joined:
                turn.joined.append(npc_id)
            turn.notes += self._unsettled(npc_id)
            if communication != "remote" and (gone := self.world.place(npc_id)) and gone != st.location:
                turn.notes.append(f"{npc.name}说完就走了，去了{self.world.name(gone)}：他已经不在这里。")
            st.npc(npc_id).met = True
            st.add("npc", line, who=npc.name, audience=said.audience)
            if record is not None:
                with self.world.at(when):
                    record(line)
            keep(turn)
        if not said.shown:
            await self.emit({"type": kind, "npc": npc_id, "name": npc.name, "text": said.line,
                             "portrait": m.art.get(npc.portrait or "", "") if said.communication != "remote" else "",
                             "audience": said.audience})
            said.shown = True
            keep(turn)
        if said.told and not said.heard:
            if said.audience is not None:
                # A private testimony updates its listeners, never the party's shared clue flags.
                for cid in said.told:
                    self.world.apply(Learn(id=f"private-told:{st.turn}:{npc_id}:{key}:{cid}", fact=f"clue:{cid}",
                                           who=said.audience, actor=npc_id, visibility="private", how="私下告知"))
            else:
                with self._journal(said.journal, keep):  # each card and its dice once, however often resumed
                    await self._gain(said.told, turn, None, heard=npc_id)
            said.heard = True
            keep(turn)
        await self.present(said.media, key=f"npc:{st.turn}:{npc_id}:{key}")
        return said

    async def _foreshadow(self, turn: Turn) -> None:
        """The signs of the place they came to, given to the keeper as this turn is told: a first
        hint, or the next stage of one seen before (engine/signs.py); waking there after a rest
        counts as coming to it. A roll that failed and found nothing keeps them back: a trace on
        the very thing they searched is what the failure did not find. They come out on a later
        visit. A stage's flag is set as it shows, so the place's picture changes with it; the
        picture of the stage after it is drawn meanwhile, in the background. Handed out in a stage
        of the turn's own journal (Turn.narrating), kept with the turn before the keeper is asked:
        a telling that failed and is tried again (/retry) tells the same hint, and the sign does
        not move on twice (the review of 2026-10-22)."""
        st, step = self.state, self._resolution_step

        def hand() -> list[list[str]]:
            missed = not turn.clues and not turn.events and (
                any(not c.passed for c in turn.checks) or (turn.earlier is not None and not turn.earlier.passed))
            if turn.ruling is not None and turn.ruling.rest:
                turn.foreshadow += signs.at(self.m, st, st.location)
            handed = [] if missed else signs.hand(self.m, st, turn.foreshadow)
            turn.foreshadow = []
            turn.notes += [note for _, note, _ in handed]
            return [[fid, s.flag] for fid, _, s in handed]

        async def foreshadow() -> None:
            handed = await step("hand", turn, hand)
            if flags := [flag for _, flag in handed if flag]:
                await self._flags(flags, turn)
                self.visuals.schedule()  # a picture drawn for the change (Location.looks) shows with this telling
            self.visuals.ahead(signs.coming(self.m, st, [fid for fid, _ in handed]))  # there by the next step

        # Kept with the turn the graph is telling; an interlude's or a sketch's telling is no turn of the graph's.
        keep = self._keep if self.state.pending_turn else (lambda _turn: None)
        with self._journal(turn.narrating, keep):
            await step("foreshadow", turn, foreshadow)

    async def narrate(self, turn: Turn, on_delta=None) -> str:
        st = self.state
        if turn.action is not None and turn.action.idea and turn.action.idea.get("refused"):  # said on the screen already
            self.told = None
            await self.emit({"type": "dialogue_end"})
            return ""
        if turn.declared:  # a LAN round's order: told with the round, by the one who settles it
            self.told = None
            await self.emit({"type": "held"})
            return ""
        await self._foreshadow(turn)
        readers = self._narrative_audience(turn)
        readers = sorted(readers) if readers is not None else None
        when = turn.interval.get("end", self.world.now)
        actor = turn.interval.get("actor", st.investigator.id)
        place = self.world.place_at(actor, when)
        if st.held and ((turn.action is not None and turn.action.alone)
                        or any(h["audience"] != readers or h["place"] != place for h in st.held)):
            # The earlier reports already settled. Tell them to their saved readers,
            # without applying an old Turn's world operations at this new focus.
            await self.tell_held()
        # A reply to the player is the turn's telling; someone joining in of their own accord is not:
        # what the player did still wants telling, and so does a find no one said in their line.
        told = self._told_by(turn)
        dialogue_only = (turn.action is not None and (turn.action.kind == "talk" or turn.action.casual)
                         and not (turn.ruling and turn.ruling.rest))
        continuing = bool(st.pending_turn.get("request", {}).get("remaining"))
        voices_pending = bool(self._voice_intents(turn))
        if (not continuing and not voices_pending and dialogue_only and any(nid not in turn.joined for nid, _ in turn.npc_lines)
                and not turn.events and not turn.notes and not turn.ending
                and not turn.arrived and not turn.world and not turn.sanity and not turn.hints and all(c in told for c in turn.clues)):
            self.told = None
            await self.emit({"type": "dialogue_end"})
            if not st.held or self.holding:
                return ""  # the reply, in its words what it told, and the roll outcomes have already been shown
            return await self.tell_held(turn=turn, on_delta=on_delta)  # the round's last: the others still want telling
        report = self._report(turn)
        token = st.pending_turn.get("party_token")
        later = [step for step in st.round_plan.get("steps", [])
                 if step["token"] != token and step["token"] not in st.round_plan.get("done", [])]
        next_actor = st.round_plan["input"][int(later[0]["token"])][0] if later else None
        same_scene = (readers is None and turn.action is not None and not turn.action.alone
                      and (next_actor is None or self.world.place(next_actor) == place))
        if self.holding and same_scene and not turn.ending and not continuing and not voices_pending:
            if not any(h["turn"] == st.turn for h in st.held):  # a resumed turn holds it once
                st.held.append({"turn": st.turn, "who": st.investigator.name, "actor": actor, "place": place,
                                "audience": readers, "report": report, "rest": turn.rest})
            self.told = None
            await self.emit({"type": "held"})
            return ""
        if st.held:
            return await self.tell_held(report, turn=turn, on_delta=on_delta)
        return await self._narrate(report, on_delta, lore=self._brief(), turn=turn)

    # -- a round of simultaneous actions (a LAN table: net/hub.py _settle) -------------------
    def pending_party(self) -> list[tuple[str, str | None]] | None:
        receipt = self.state.round_plan
        pending = [tuple(receipt["input"][int(step["token"])]) for step in receipt.get("steps", [])
                   if step["token"] not in receipt.get("done", [])]
        return pending or None

    async def arrange_party(self, order: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        """Persist every declaration before any planning, and resume only unfinished tokens."""
        st, receipt = self.state, self.state.round_plan
        if not self.pending_party():
            if st.pending_turn:
                return order
            started = receipt.get("started", st.minutes)
            receipt.clear()
            receipt.update(input=[list(pair) for pair in order], steps=[{"token": str(n)} for n in range(len(order))],
                           used=[], done=[], at=st.clock, started=started, planned=False)
            if self.checkpoint:
                self.checkpoint()
        if receipt.get("planned") or receipt.get("used") or receipt.get("done"):
            return self.pending_party() or []
        planner = getattr(self.cast, "party_plan", None)
        able = {i.id for i in st.investigators if i.can_act}
        inputs = [PartyInput(token=str(n), who=who, text=text) for n, (who, text) in enumerate(receipt["input"])
                  if who in able and text and not text.startswith("/kp ") and not commands.instant(text)
                  and not idea.asked(text)]
        if (planner is None or st.ending or self.battling() or len(inputs) < 2
                or len({row.who for row in inputs}) < 2):
            receipt["planned"] = True
            return self.pending_party() or []
        await self._world_settled()
        try:
            planned = await planner(st, inputs)
            if planned is None:
                raise ValueError("这一批行动尚未取得完整场面安排")
            planned = PartyPlan.model_validate(planned)
            tokens = [step.token for step in planned.steps]
            wanted = {row.token for row in inputs}
            if len(tokens) != len(wanted) or set(tokens) != wanted:
                raise ValueError("整批安排没有逐一保留原始行动编号")
            for who in {row.who for row in inputs}:
                declared_order = [row.token for row in inputs if row.who == who]
                if [token for token in tokens if token in declared_order] != declared_order:
                    raise ValueError("同一调查员的连续行动须保留声明顺序")
            if any(set(step.related) - wanted or step.token in step.related for step in planned.steps):
                raise ValueError("整批安排引用了不存在或自身的关联行动")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._late.notes.append("本批尚未完成整体安排，按原行动继续；配合、争抢与相互影响仍须裁定，不按输入先后预定结果。")
            self._debug("party-plan", {"phase": "fallback", "error": f"{type(exc).__name__}: {exc}"[:300]})
        else:
            declared = {step.token for step in planned.steps}
            receipt["steps"] = [step.model_dump(mode="json") for step in planned.steps] + [
                step for step in receipt["steps"] if step["token"] not in declared]
        receipt["planned"] = True
        if self.checkpoint:
            self.checkpoint()
        return self.pending_party() or []

    def _bind_party_declaration(self, text: str) -> None:
        st, receipt = self.state, self.state.round_plan
        if not st.pending_turn or "party_token" in st.pending_turn:
            return
        for step in receipt.get("steps", []):
            token = step["token"]
            if token in receipt.get("done", []) or token in receipt.get("used", []):
                continue
            who, original = receipt["input"][int(token)]
            if who == st.investigator.id and original == text:
                st.pending_turn["party_token"] = token
                receipt.setdefault("used", []).append(token)
                return

    def finish_party_declaration(self, who: str, text: str | None, *, token: str | None = None) -> None:
        """Close exactly one original declaration, including a rejected or absent AI action."""
        receipt = self.state.round_plan
        for step in receipt.get("steps", []):
            key = step["token"]
            if token is not None and key != token:
                continue
            if tuple(receipt["input"][int(key)]) != (who, text):
                continue
            if key not in receipt.setdefault("done", []):
                receipt["done"].append(key)
                return
            if token is not None:
                return

    def _party_step(self, text: str) -> dict | None:
        st, receipt = self.state, self.state.round_plan
        if self.battling():
            return None
        for step in receipt.get("steps", []):
            token = step["token"]
            if not step.get("plan") or token in receipt.get("done", []):
                continue
            if token in receipt.get("used", []) and token != st.pending_turn.get("party_token"):
                continue
            who, original = receipt["input"][int(token)]
            if who == st.investigator.id and original == text:
                return step
        return None

    def _party_context(self) -> str:
        """Neutral coordination for the rules assistant; original player wording stays out."""
        st = self.state
        token = st.pending_turn.get("party_token")
        step = next((row for row in st.round_plan.get("steps", []) if row["token"] == token), None)
        if step is None or not (step.get("coordination") or step.get("related")):
            return ""
        names = {i.id: i.name for i in st.investigators}
        lines = ["【这批行动的关系】" + step.get("coordination", "")]
        for other in st.round_plan["steps"]:
            if other["token"] in step.get("related", []):
                who = st.round_plan["input"][int(other["token"])][0]
                progress_ = "已结算，实际结果以当前记录为准" if other["token"] in st.round_plan.get("done", []) else "尚待结算的尝试"
                action = Plan.model_validate(other["plan"]).action
                neutral = f"{action.kind}；{action.summary}" + (f"；对象={action.target or action.npc}" if action.target or action.npc else "")
                actual = st.round_plan.get("results", {}).get(other["token"], "")
                lines.append(f"- {names.get(who, who)}（{progress_}）：{neutral}" + (f"；实际检定={actual}" if actual else ""))
        return "\n".join(lines) + "\n配合须确实成立才影响条件；争抢不因结算次序预定胜者，不替另一位玩家同意或决定行动。"

    def complete_party_action(self) -> None:
        token = self.state.pending_turn.get("party_token")
        receipt = self.state.round_plan
        if token is not None and token not in receipt.setdefault("done", []):
            receipt["done"].append(token)
            completed = self.state.pending_turn.get("request", {}).get("completed", [])
            checks = ([check for part in completed for check in part.get("checks", [])] if completed else
                      self.state.pending_turn.get("turn", {}).get("checks", []))
            receipt.setdefault("results", {})[token] = str(checks) if checks else "无需检定；其余结果以当前记录为准"

    async def finish_request(self, turn: Turn | None) -> bool:
        """Commit one actual segment and hand off the next plan from the same keeper reply.

        A waiting intention is a private note, not a pending execution lock. Every next segment
        gets its own turn and ordinary resolution journal; the player's original words are logged once.
        The turn graph calls this after remember, before its final checkpoint.
        """
        st, journal = self.state, self.state.pending_turn
        if turn is not None and turn.action is not None:
            st.last_actions[st.investigator.id] = st.turn
        request = journal.get("request")
        if not request:
            self.complete_party_action()
            journal.clear()
            return False
        completed = request.setdefault("completed", [])
        if turn is not None and not any(row["turn"] == st.turn for row in completed):
            completed.append({"turn": st.turn, "action": turn.action.summary if turn.action else "",
                              "start": turn.interval.get("start", request["started"]),
                              "end": turn.interval.get("end", st.minutes), "place": st.location,
                              "checks": [check.describe() for check in turn.checks],
                              "notes": turn.notes, "npc_lines": turn.npc_lines})
        remaining = request.get("remaining", "").strip()
        next_plan, pending, reason = None, "", ""

        def offered() -> tuple[dict | None, str, str]:
            told = journal.get("keeper", {})
            while told.get("following_keeper"):
                told = told["following_keeper"]
            control = told.get("continuation")
            if not told.get("scene_complete") or not told.get("landed"):
                return None, remaining, "当前场面未完整落实，后续行动尚未执行"
            if told.get("continuation_error") or control is None:
                return None, remaining, told.get("continuation_error") or "后续行动尚未安排"
            return control.get("next"), control.get("pending", ""), ""

        if remaining:
            next_plan, pending, reason = offered()
        if next_plan or (journal.get("request_world") and not journal["request_world"].get("complete")):
            await self._request_world_barrier(turn)
            next_plan, pending, reason = offered()
        if next_plan and (st.ending or not st.investigator.can_act or self.battling()
                          or self.pending_choice() is not None or st.pending_push):
            next_plan, pending, reason = None, remaining, "当前局势需要先处理，后续行动尚未执行"
        if next_plan:
            plan = Plan.model_validate(next_plan)
            request.update(part=request["part"] + 1, started=self._actor_cursor(), previous_turn=st.turn,
                           remaining=plan.remaining.strip(), ready=True)
            next_journal = {"input": request["original"], "by_hand": journal.get("by_hand", False),
                            "active": journal.get("active", st.active), "request": request,
                            "arrangement": {"at": st.turn + 1, "plan": plan.model_dump(mode="json")}}
            if "party_token" in journal:
                next_journal["party_token"] = journal["party_token"]
            journal.clear()
            journal.update(next_journal)
            return True
        if pending:
            st.unfinished[request["actor"]] = {"original": request["original"], "remaining": pending,
                                                "completed": completed, "reason": reason}
        else:
            st.unfinished.pop(request["actor"], None)
        self.complete_party_action()
        journal.clear()
        if reason:
            await self.emit({"type": "command", "kind": "pending", "text": reason,
                             "audience": [request["actor"]], "who": st.find(request["actor"]).name})
        return False

    def _request_world_work(self) -> Awaitable[None] | None:
        """Freeze a continuing request's world boundary before its task can run or checkpoint."""
        st, journal = self.state, self.state.pending_turn
        request = journal.get("request", {})
        told = journal.get("keeper", {})
        while told.get("following_keeper"):
            told = told["following_keeper"]
        if not (request.get("remaining") and told.get("scene_complete") and told.get("landed")
                and not told.get("continuation_error") and (told.get("continuation") or {}).get("next")):
            return None
        work = next((work for work in self.world.rec.npc_work.values()
                     if work["turn"] == st.turn and work["end"] == st.minutes
                     and work["start"] == self.world.rec.simulated), None)
        receipt = journal.setdefault("request_world", {
            "turn": st.turn, "start": self.world.rec.simulated, "end": st.minutes,
            "since": work["since"] if work else self.world.rec.seq, "flags": list(st.flags),
            "moved_on": self._moved_on or bool(journal.get("turn", {}).get("arrived")),
            "dealing": sorted(self._engaged(Action.model_validate(journal["action"])))
                       if journal.get("action") else sorted(self._dealing),
            "late": TURN_RECORD.dump_python(self._late, mode="json"),
        })
        self._late = Turn()
        if self.checkpoint:
            self.checkpoint()
        return self._request_world_run(receipt)

    async def _request_world_run(self, receipt: dict) -> None:
        """The existing world stages with the local Turn retained across scheduler completion."""
        if receipt.get("settled"):
            return
        late = TURN_RECORD.validate_python(receipt["late"])

        def keep(kept: Turn) -> None:
            receipt["late"] = TURN_RECORD.dump_python(kept, mode="json")
            if self.checkpoint:
                self.checkpoint()

        step = self._resolution_step
        with self._journal(receipt.setdefault("work", {}), keep):
            if not receipt["work"].get("steps", {}).get("live", {}).get("done"):
                self._moved_on = self._moved_on or receipt["moved_on"]
            await step("live", late, lambda: self._live(late, inline=False))
            await step("play", late, lambda: self._play(late, set(receipt["dealing"])))
            start = receipt["start"]
            if self.world.reviewing and start is not None and self.state.minutes - start >= self.minds.long:
                await step("look", late, self._look)
                await step("play-after-look", late, lambda: self._play(late, set(receipt["dealing"])))
            await step("flags", late, lambda: self._flags(
                [flag for flag in self.state.flags if flag not in receipt["flags"]], late, landed=True))
        # A completed scheduler tick removes npc_work before _live can return. Its saved since
        # recovers observable operations in that gap, without replaying any completed NPC work.
        late.world = list(dict.fromkeys([*late.world, *world_view.happened(self.world, receipt["since"])]))
        receipt["settled"] = True
        keep(late)

    async def _request_world_barrier(self, owner: Turn | None) -> None:
        """Present actual between-segment changes before accepting the preceding next Plan."""
        st, journal = self.state, self.state.pending_turn
        receipt = journal.get("request_world")
        if receipt is None:
            rec = self.world.rec
            unfinished = any(work["turn"] == st.turn and work["end"] == st.minutes
                             and work["start"] == rec.simulated for work in rec.npc_work.values())
            behind = rec.simulated is not None and rec.simulated < st.minutes
            moved_on = self._moved_on or bool(owner and owner.arrived)
            reactive = scheduler.reactions_ready(self.world, moved_on=moved_on) if self.world_later else False
            if not self.world_later or not (unfinished or behind or reactive):
                return  # inline work was already told; do not introduce deferred simulation there
            # The action's world debt may have been checkpointed before remember started its
            # task. Owed/task are memory only; the persisted interval/work is the recovery basis.
            recovery = self._request_world_work()
            if recovery is None:
                return
            self._world_owed = False
            receipt = journal["request_world"]
            await recovery
        if self._world_task is not None:
            await self._world_settled()
            if not receipt.get("settled"):
                raise PreparationError("后台工作尚未完成，已执行结果保留，请继续原回合")
        elif not receipt.get("settled"):
            # Tasks are not saved. The world's own npc_work and this stage journal resume the
            # same interval/decision receipts after loading, rather than starting another tick.
            await self._request_world_run(receipt)
        if receipt.get("complete"):
            return
        late = TURN_RECORD.validate_python(receipt["late"])

        def keep(kept: Turn) -> None:
            receipt["late"] = TURN_RECORD.dump_python(kept, mode="json")
            if self.checkpoint:
                self.checkpoint()

        if not any((late.events, late.sanity, late.notes, late.hints, late.world, late.fight, late.arrived)):
            receipt["complete"] = True
            keep(late)
            return
        late.interlude = True
        await self._perform_events(late, keep)
        with self._journal(receipt.setdefault("ending", {}), keep):
            await self._check_ending(late)
        told = journal["keeper"]
        if "keeper_depth" not in receipt:
            depth = 1
            while told.get("following_keeper"):
                told = told["following_keeper"]
                depth += 1
            told["following_keeper"] = {}
            receipt["keeper_depth"] = depth
            keep(late)
        told = journal["keeper"]
        for _ in range(receipt["keeper_depth"]):
            told = told["following_keeper"]
        await self._narrate(self._report(late), lore=self._brief(), turn=late, _receipt=told, _owner=owner)
        receipt["complete"] = True
        keep(late)

    def begin_round(self) -> None:
        """The actions about to be settled happen at once: the clock moves by the longest of
        them, not their sum, and the keeper tells them together (narrate, holding)."""
        if self.state.round_spent is None or (not self.state.pending_turn and not self.pending_party()):
            self.state.round_spent = 0
            self.state.round_elapsed.clear()
            self.state.round_plan["started"] = self.state.minutes

    def end_round(self, *, interrupted: bool = False) -> None:
        if not interrupted and not self.state.pending_turn:
            self.state.round_spent = None
            self.state.round_elapsed.clear()
            self.state.round_plan.clear()
        elif not self.state.pending_turn and not self.pending_party():
            self.state.round_spent = None
            self.state.round_elapsed.clear()
        self.holding = False

    def _actor_cursor(self, who: str | None = None) -> int:
        st = self.state
        if st.round_spent is None:
            return st.minutes
        base = st.round_plan.setdefault("started", st.minutes - st.round_spent)
        return base + st.round_elapsed.get(who or st.investigator.id, 0)

    def _travellers(self, alone: bool) -> list[str]:
        st = self.state
        return ([st.investigator.id] if alone else
                [i.id for i in st.investigators if self.world.place(i.id) == st.location])

    def _spend(self, minutes: int, turn: Turn, *, participants: list[str] | None = None) -> dict:
        """Commit serial personal work, or a journey shared only by its real travellers."""
        st, who = self.state, self.state.investigator.id
        people = list(dict.fromkeys([who, *(participants or [])]))
        own_start = self._actor_cursor(who)
        start = max(self._actor_cursor(person) for person in people)
        end = start + max(0, minutes)
        if not turn.interval:
            turn.interval = {"actor": who, "start": own_start, "end": end, "participants": people,
                             "origin": self.world.place(who), "destination": self.world.place(who)}
        else:
            turn.interval["end"] = max(turn.interval["end"], end)
            turn.interval["participants"] = list(dict.fromkeys([*turn.interval["participants"], *people]))
        if st.round_spent is not None:
            base = st.round_plan["started"]
            for person in people:
                st.round_elapsed[person] = end - base
            st.round_spent = max(st.round_spent, end - base)
        st.minutes = max(st.minutes, end)
        if self.world.clock is not None:
            self.world.clock = end
        return {"start": start, "end": end, "participants": people}

    async def tell_held(self, report: str = "", *, turn: Turn | None = None, on_delta=None) -> str:
        """Tell one settled scene to its saved readers; combine only the same observation scope."""
        st = self.state
        current = self._narrative_audience(turn) if turn is not None else None
        current = sorted(current) if current is not None else None
        actor = turn.interval.get("actor", st.investigator.id) if turn is not None else ""
        place = self.world.place_at(actor, turn.interval.get("end", self.world.now)) if turn is not None else ""
        told = ""
        while st.held:
            first = st.held[0]
            group = []
            for row in st.held:
                if row["audience"] != first["audience"] or row["place"] != first["place"]:
                    break
                group.append(row)
            include_current = (turn is not None and len(group) == len(st.held) and current is None
                               and turn.action is not None and not turn.action.alone
                               and current == first["audience"] and place == first["place"])
            parts = [f"〔{h['who']}〕\n{h['report']}" for h in group]
            if include_current and report:
                parts.append(f"〔{st.investigator.name}〕\n{report}")
            combined = ("【同一现场同时行动、已经结算的经过】下面按结算先后列出各人的经过，在场人物和时间以各段记录为准。"
                        "只向这些经过的实际读者讲述；不向新到场者补发他们没看见的事。"
                        "讲成连贯叙述，不重复已经显示的台词和检定，不执行新的行动。\n\n" + "\n\n".join(parts))
            if include_current:
                told = await self._narrate(combined, on_delta, lore=self._brief(), turn=turn)
                report, turn = "", None
            else:
                receipt = first.setdefault("telling", {"audience": first["audience"]})
                told = await self._narrate(combined, on_delta, lore=self._brief(), _receipt=receipt,
                                           _tools=False, _settled_only=True)
            del st.held[:len(group)]
            if self.checkpoint:
                self.checkpoint()
        if turn is not None and report:
            told = await self._narrate(report, on_delta, lore=self._brief(), turn=turn)
        return told

    def _brief(self) -> str:
        """What the keeper keeps behind the screen for this telling (world/context.keeper_brief),
        set apart from what it rides along with (the report, a nudge to tell it again)."""
        brief = world_view.keeper_brief(self.world) if getattr(self.cast, "backstage", True) else ""
        return "\n\n" + brief if brief else ""

    async def remember(self) -> None:
        st = self.state
        await self.archive_encounter()  # an encounter this turn ended goes into the log of finished ones
        st.tally()
        if self._world_owed and self._world_task is None:
            self._world_owed = False
            work = self._request_world_work()
            self._world_task = asyncio.create_task(work if work is not None else self._live_later(), name="world")
        self.world.sync()  # before the log is folded: what the players were told is out
        idea.note(self.m, st)  # before the log is folded: how present each missed clue has been
        self._recount()  # before the log is folded: it reads the lines as they stand
        st.heard = fog.heard(self.m, st)  # the places named in the log outlive its folding into memory
        recent = [e for e in st.log if e.kind != "dice"]
        if len(recent) < 48:
            return
        old, keep = st.log[:-24], st.log[-24:]
        # What the table was told, not the module's own words for each clue: the memory feeds the
        # players' recap and 故事至今, and the telling that put each clue into the story is there.
        text = "\n".join(f"[{e.time}] {e.who or e.kind}：{e.text}" for e in old
                         if e.kind not in ("dice", "clue") and recipients.shared(e))
        if text:
            await self.emit({"type": "status", "text": "记录员整理笔记…"})
            st.memory = await self.cast.chronicle(st, text)
        st.private_history.extend(e for e in old if not recipients.shared(e))
        st.log = keep

    def _recount(self) -> None:
        """After a turn, when the story has moved enough (progress.due): the players' 故事至今,
        written again in the background. The prompt is made now; no turn waits for the reply."""
        if self.write_progress and not self._writing() and progress.due(self.state):
            self._ask_progress()

    def recount(self) -> str:
        """A player asks for it now (Ctrl+Y): once a turn, and only with something new since
        the last one. Why not, when it cannot be done; "" when it is being written."""
        why = "正在整理进展。" if self._writing() else progress.refused(self.state)
        if not why:
            self._ask_progress()
        return why

    def _writing(self) -> bool:
        return self._recounting is not None and not self._recounting.done()

    def _ask_progress(self) -> None:
        st = self.state
        st.progress.asked = st.turn  # the next call waits progress.APART turns, whatever comes of this one
        prompt = progress.material(self.m, st)
        self._recounting = asyncio.create_task(self._recount_in(prompt, st.turn, st.logged, progress.marks(st)))

    async def _recount_in(self, prompt: str, turn: int, seen: int, marks: dict[str, int]) -> None:
        """Only an account that came moves on what has been read and counted: after a failure
        the next one starts from the last that was written."""
        st = self.state
        try:
            text = progress.trim(await self.cast.progress(st, prompt))
            if text:
                st.progress = Progress(text=text, turn=turn, asked=st.progress.asked, seen=seen, marks=marks)
                await self.listener({"type": "progress", "text": text, "turn": turn})
        except Exception:  # the old text stands
            pass

    async def sketch(self, png: bytes, prompt_id: str | None = None) -> str:
        """Interpret a player's drawing; module sketch prompts can trigger sanity or clues."""
        st, m = self.state, self.m
        prompt = next((p for p in m.sketches if p.id == prompt_id), None) if prompt_id else None
        asked = prompt.ask if prompt else "玩家自由作画"
        target = prompt.target if prompt else "（无固定对象）"
        await self.emit({"type": "status", "text": "读图员在看你的画…"})
        result = await self.cast.sketch(st, png, f"{asked}；真实样子：{target}")
        st.turn += 1
        st.add("player", f"（画了一幅草图：{result.description}）", who=st.investigator.name)
        turn = Turn(action=Action(kind="use", summary=f"{st.investigator.name}画了一幅草图：{result.description}"))
        turn.notes.append(f"读图员：与“{asked}”的相似度 {result.resemblance:.2f}。{result.notes}")
        if prompt and result.resemblance >= prompt.threshold:
            if prompt.sanity:
                ok, bad = prompt.sanity.split("/")
                watching = set(self.world.observers(self.world.place(st.investigator.id)))
                for inv in st.investigators:
                    if inv.id not in watching or not inv.can_act:
                        continue
                    s = self._sanity(inv, ok, bad)
                    turn.sanity.append((inv.name, s))
                    st.add("sanity", s.describe(), who=inv.name)
                    await self.emit({"type": "sanity", "who": inv.name, **_sanity_view(s), **self._madness(inv, s, turn)})
                turn.notes.append("画得太像了——他本不该画得出来")
            if prompt.clue:
                await self._gain([prompt.clue], turn, None)
        return await self.narrate(turn)

    # -- helpers --------------------------------------------------------------------------
    async def _mishap(self, card_id: str, turn: Turn, key: str) -> str:
        """A mishap card the arbiter dealt the investigator (cards.deal_mishap): what it does at once
        settled by the rules, the card shown and its words given to the keeper. Its name, or "" when
        there is no such card."""
        st, step = self.state, self._resolution_step

        def commit() -> dict:
            inv = st.investigator
            trial = inv.model_copy(deep=True)
            dealt = cards.deal_mishap(st, trial, card_id, self.rules, hp=False)
            if dealt is None:
                return {"name": "", "events": []}
            inv.__dict__.update(trial.__dict__)
            # Its HP loss lands as every wound does (engine/effects): once, by its injury id, with the
            # CON a major wound calls for (the review: it went straight onto the sheet).
            wounded = self._wound_event(inv.id, dealt.card.hp, f"mishap:{st.turn}:{self._resolution_key(key)}", turn) \
                if dealt.card.hp else None
            if wounded:
                dealt.lines.insert(0, f"{inv.name}{wounded['text']}")
            st.add("event", f"惨事卡〔{dealt.card.name}〕", who=inv.name)
            turn.notes.append(cards.mishap_note(inv, dealt))
            events = [{"type": "card", **cards.view(dealt.card, inv.name)}, *([wounded] if wounded else [])]
            if dealt.sanity is not None:
                turn.sanity.append((inv.name, dealt.sanity))
                st.add("sanity", dealt.sanity.describe(), who=inv.name)
                events.append({"type": "sanity", "who": inv.name, **_sanity_view(dealt.sanity), **self._madness(inv, dealt.sanity, turn)})
            return {"name": dealt.card.name, "events": [*events, {"type": "sheet"}]}

        done = await step(key, turn, commit)
        for index, notice in enumerate(done["events"]):
            await step(f"{key}-notice:{index}", turn, lambda notice=notice: self.emit(notice))
        return done["name"]

    def _lose_sanity(self, inv: Investigator, roll: str, turn: Turn) -> dict[str, Any]:
        """A SAN roll (`roll`: "0/1D3", what passing and what failing it costs) settled for `inv`: the
        loss, any madness and its card, in the log and the turn. The notice for the screens. Called in
        a resolution stage, so a turn resumed after a crash does not roll or lose it again."""
        ok, bad = roll.split("/")
        trial = inv.model_copy(deep=True)
        sanity = self._sanity(trial, ok, bad)
        inv.__dict__.update(trial.__dict__)
        turn.sanity.append((inv.name, sanity))
        when = self.world.now
        place = self.world.place_at(inv.id, when)
        observers = self.world.observers(place, at=when) if place else []
        audience = recipients.scope(self.state, [inv.id, *observers])
        self.state.add("sanity", sanity.describe(), who=inv.name, audience=audience)
        return {"type": "sanity", "who": inv.name, **_sanity_view(sanity), **self._madness(inv, sanity, turn),
                "audience": audience}

    def _sanity(self, inv: Investigator, ok: str, bad: str) -> Sanity:
        """A SAN check at the actual game clock; the bout table remains its own immediate event."""
        return self.rules.sanity(inv, ok, bad, now=self.world.now)

    async def _recover_madness(self, turn: Turn, *, rested: dict[str, tuple[int, int]] | None = None) -> None:
        """Rule-owned expiry or an accepted telling's completed safe rest, with one saved receipt
        for the state, log and notices. This never interprets narrative text or a player's wish."""
        st = self.state

        def commit() -> list[dict]:
            notices = []
            for inv in st.investigators:
                period = (rested or {}).get(inv.id)
                since = period[0] if period is not None and period[0] < period[1] <= st.minutes else None
                for recovered in inv.recover_madness(st.minutes, rested_since=since):
                    text = f"{inv.name}：{recovered}；已损失的理智不返还。"
                    st.add("system", text, audience=[inv.id])
                    turn.notes.append(text)
                    notices.append({"text": text, "audience": [inv.id]})
            return notices

        notices = await self._resolution_step("madness-rest" if rested else "madness-expiry", turn, commit)
        for index, notice in enumerate(notices):
            await self._resolution_step(f"madness-notice:{index}", turn,
                                        lambda notice=notice: self.emit({"type": "status", **notice}))
        if notices:
            await self._resolution_step("madness-sheet", turn, lambda: self.emit({"type": "sheet"}))

    def _madness(self, inv: Investigator, s: Sanity, turn: Turn) -> dict[str, Any]:
        """The phobia deck's card for this madness (cards.for_sanity): on the sheet, in the keeper's
        notes, shown with the SAN result. Nothing without imported decks."""
        card = cards.for_sanity(self.state, inv, s)
        if card is None:
            return {}
        turn.notes.append(cards.madness_note(inv, card))
        return {"card": cards.view(card, inv.name)}

    def _items_for(self, action: Action) -> list[Item]:
        if not action.target:
            return []
        return [
            item for item in self.m.items if knowledge.item_known(self.world, item.id, self.state.investigator.id)
            and self.world.rec.entities[item.id].quantity > 0
            and world.owner(self.m, self.state, item.id) in (self.state.location, self.state.investigator.id)
            and (item.name in action.target or item.id == action.target or (len(action.target) >= 2 and action.target in item.name))
        ]

    def _route(self, src: str, dst: str) -> list[str] | None:
        if dst == src:
            return [src]
        ids = {loc.id for loc in self.m.locations}
        if dst not in ids:
            return None
        seen, queue, order = set(), [(0, 0, [src])], 0
        while queue:
            cost, _, path = heapq.heappop(queue)
            if path[-1] in seen:
                continue
            seen.add(path[-1])
            if path[-1] == dst:
                return path
            for nxt in self.m.location(path[-1]).exits:
                if nxt in ids and nxt not in seen:
                    order += 1
                    heapq.heappush(queue, (cost + self._edge_time(path[-1], nxt), order, [*path, nxt]))
        return None

    def _travel(self, src: str, dst: str) -> int | None:
        route = self._route(src, dst)
        if route is None:
            return None
        return sum(self._edge_time(a, b) for a, b in pairwise(route))

    def _edge_time(self, src: str, dst: str) -> int:
        return max(0, min(720, self.m.location(src).travel_minutes.get(dst,
                         self.m.location(dst).travel_minutes.get(src, 10))))

    def _stop_on_route(self, action: Action) -> bool:
        path = self._route(self.state.location, action.destination) if action.destination else None
        return bool(action.travel == "stroll" and path and len(path) > 2)

    async def _arrive(self, loc_id: str, turn: Turn, *, alone: bool = False, party: bool = False,
                      companions: list[str] | None = None, interval: dict | None = None) -> None:
        """The party is at loc_id now. On their own way (party), a companion goes where the
        investigator goes, a detour included, until the place it agreed to; an event that carries
        them off takes along only a companion it happens to be taking that way. Someone asked
        along who did not come is named, so the telling does not walk them in."""
        st, m = self.state, self.m
        step = self._resolution_step

        def commit() -> dict:
            src = st.location
            places = {i.id: self.world.place(i.id) for i in st.investigators}
            coming = {st.investigator.id} if alone else {who for who, place in places.items() if place == src}
            joined = [who for who, place in places.items() if src != loc_id and place == loc_id and who not in coming]
            places.update(dict.fromkeys(coming, loc_id))
            if src != loc_id and src not in places.values():
                self.world.rec.left[src] = self.world.rec.seq
            st.location = loc_id
            st.apart = {who: place for who, place in places.items() if place != loc_id}
            at = (interval or {}).get("end", turn.interval.get("end", self.world.now))
            start = (interval or {}).get("start", at)
            if src != loc_id:
                motions = {who: {"origin": src, "destination": loc_id, "start": start, "end": at} for who in coming}
                with self.world.at(at, start=start):
                    self.world.emit("move", f"调查员抵达{self.world.name(loc_id)}", participants=list(coming), place=loc_id,
                                    op=f"travel:{st.turn}:{src}:{loc_id}", motions=motions)
            if turn.interval:
                turn.interval["destination"] = loc_id
            for inv_id in joined:  # the moving group came to where another investigator already was
                turn.notes.append(f"{st.find(inv_id).name}就在{m.location(loc_id).name}，和大家会合了。")
            came: set[str] = set()
            for ent in list(self.world.rec.entities.values()):
                if ent.kind != "npc" or ent.at != src or ent.following not in coming:
                    continue
                path = self._route(src, ent.follow_to)
                if not alone and src != loc_id and (party or (path and loc_id in path)):
                    ready = self.world.available_at(ent.id)
                    if ready is not None and ready > start:
                        turn.notes.append(f"{ent.name}在本次出发时尚有已提交的行动，没有随行。")
                        continue
                    with self.world.at(at, start=start):
                        result = self.world.apply(Move(id=f"escort:{st.turn}:{ent.id}:{src}:{loc_id}", entity=ent.id, to=loc_id))
                    if result.ok:
                        came.add(ent.id)
                        turn.notes.append(f"{ent.name}与调查员一同抵达{m.location(loc_id).name}。")
                if alone or ent.at == ent.follow_to:
                    self.world.apply(Follow(id=f"escort-end:{st.turn}:{ent.id}", npc=ent.id))
            if party and not alone and src != loc_id:
                for nid in dict.fromkeys(companions or []):
                    ent = self.world.rec.entities.get(nid)
                    if ent is not None and ent.kind == "npc" and nid not in came and ent.at != loc_id:
                        turn.notes.append(f"{ent.name}没有一起来，人还在{self.world.name(ent.at)}；不要写成同行。")
            turn.arrived = loc_id
            turn.first_visit = loc_id not in st.visited
            if turn.first_visit:
                st.visited.append(loc_id)
                st.progress_at = st.turn
            loc = m.location(loc_id)
            st.view, st.view_kind = views.default(m, loc, st.minutes), ""
            self._here = self._present()
            if traces := world_view.traces(self.world, loc_id):
                self.visuals.look(f"location:{loc_id}", "；".join(traces))
            turn.foreshadow += signs.at(m, st, loc_id)
            scope = recipients.scope(st, self.world.observers(loc_id, at=at))
            return {"type": "move", "location": loc.id, "name": loc.name, "art": m.art.get(loc.art or "", ""),
                    "view": st.view, "audience": scope}

        async def arrive() -> None:
            notice = await step("commit", turn, commit)
            await step("notice", turn, lambda: self.emit(notice))
            if turn.first_visit:
                await step("showcase", turn, lambda: self.showcases.fire(f"visit:{loc_id}", audience=notice["audience"]))
            await self._events(turn, visit=loc_id, at=(interval or {}).get("end", turn.interval.get("end")))
            await step("plugins", turn, lambda: self.plugins.run("visit", turn, location=loc_id))

        await step(f"arrive:{loc_id}", turn, arrive)

    def _spoken(self, ids: list[str], npc: str) -> list[str]:
        """Of the clues a ruling gives, those the one talk() asks this turn (`npc`, _asks) knows: not
        handed over as a card the moment the subject comes up, but left to them to tell, a piece at a
        time in their own words (Cast.npc, Dialogue.told); the card comes when they have said it
        (_reply). The user, 2026-10-21: the gossip came as a card while the cook was still asking who
        died. Nobody asked, nothing is held back: a passed roll on someone talk() does not ask gives
        its clue as found (the review of 2026-10-22: 有些人油盐不进's c_fitz_talk, a 心理学 roll that
        passed, came to nothing). Nor what their manner gives away (a 心理学 gate): the investigator
        sees it, nobody tells it, a paper is handed over. Actual knowledge in the world record can
        make a new witness a teller. A native Clue.npc source absent from NPC.clues is instead
        something seen of that person, and is found directly."""
        st, m = self.state, self.m
        if not npc:
            return []
        sheet = m.npc(npc)

        def told(cid: str) -> bool:
            clue = m.clue(cid)
            gate = module_graph.disclosure_gate(m, clue, npc)
            observed = gate is not None and canonical(gate.skill) == "心理学"
            native_observation = clue.npc == npc and cid not in sheet.clues
            return (npc in module_graph.tellers(m, clue, record=self.world.rec)
                    and not native_observation and (not clue.handout or cid not in sheet.clues) and not observed)

        spoken = [c for c in ids if told(c)]
        unlocked = st.npc(npc).unlocked
        unlocked += [c for c in spoken if c not in unlocked]  # theirs to tell from now on
        return spoken

    def _asks(self, action: Action | None, turn: Turn) -> str:
        """Whom talk() asks this turn, as the turn stands: the one addressed (_addressed), here, and
        not already answered this turn (stepping in, before the journey); "" for nobody."""
        npc = self._addressed(action)
        if not npc or not any(n.id == npc for n in self.m.npcs_at(self.state.location)):
            return ""
        if f"talk:{npc}" not in turn.replies and any(nid == npc for nid, _ in turn.npc_lines):
            return ""
        return npc

    def _told_clues(self, npc_id: str, ids: list[str], *, present: bool = True) -> list[str]:
        """Of the clues a reply told (Dialogue.told), those currently known, authorized and available,
        not found before. The reply's pre-speech snapshot checks presence; its receipt may pass
        present=False after the speaker walks away without unsaying what it told here."""
        st, m = self.state, self.m
        clues = {c.id: c for c in m.clues}
        allowed = knowledge.disclosable_clues(m, st, npc_id, record=self.world.rec, present=present)
        return [cid for cid in dict.fromkeys(ids) if cid in allowed and cid in clues and cid not in st.clues
                and clues[cid].available(st.flags, st.clues)]

    def _valid_clues(self, ids: list[str], action: Action | None) -> list[str]:
        """Of the clues a ruling gives, those to be had here now (module/graph.at_hand): what lies here,
        and what comes from someone the world record has here, alive and not gone (2026-10-22: the
        clue about the truck's driver was given at an empty cab)."""
        st, m = self.state, self.m
        people = self.world.present(st.location)
        here = {c.id for c in module_graph.at_hand(m, st.location, people, record=self.world.rec)}
        known = {c.id for c in m.clues if c.available(st.flags, st.clues)}
        found = [c for c in dict.fromkeys(ids) if c in known and c in here and c not in st.clues]
        addressed = self._addressed(action)
        found = [cid for cid in found if module_graph.original_source_at_hand(
            m, m.clue(cid), st.location, people, record=self.world.rec)
            or addressed in module_graph.tellers(m, m.clue(cid), record=self.world.rec)]
        if action and (action.approach == "心理学" or (action.asked and canonical(action.asked.skill) == "心理学")):
            # An observation cannot unlock testimony behind a different social gate, even
            # when the arbiter mistakenly awards it. Explicit psychology clues still work.
            def observable(cid: str) -> bool:
                clue = m.clue(cid)
                subject = action.npc or clue.npc
                gate = module_graph.disclosure_gate(m, clue, subject) if subject else None
                return gate is None or canonical(gate.skill) == "心理学"

            found = [cid for cid in found if observable(cid)]
        return found

    async def _gain(self, ids: list[str], turn: Turn, action: Action | None, heard: str = "", *,
                    originals: set[str] | None = None) -> None:
        """Commit an authorized award and its costs. Callers may explicitly limit which awards
        came through the original discovery/material route; a summary still grants the clue,
        SAN/Mythos and its actual consequences, without the source's paper or personal gate."""
        st, m = self.state, self.m
        step = self._resolution_step
        for cid in ids:
            key = f"/clue:{cid}"
            if cid in st.clues and not self._resolution_pending(key):
                continue
            clue = m.clue(cid)
            if not clue.available(st.flags, st.clues) and not self._resolution_pending(key):
                continue

            def commit(clue=clue, cid=cid) -> dict:
                inv = st.investigator
                audience = self._narrative_audience(turn)
                sanity = None
                if clue.sanity:
                    ok, bad = clue.sanity.split("/")
                    trial = inv.model_copy(deep=True)
                    sanity = self._sanity(trial, ok, bad)
                    inv.__dict__.update(trial.__dict__)
                if clue.mythos:
                    inv.skills["克苏鲁神话"] = inv.value("克苏鲁神话") + clue.mythos
                st.clues.append(cid)
                knowledge.seed(m, st, self.world.rec)
                fact = self.world.rec.facts[f"clue:{cid}"]
                readers = audience if audience is not None else [i.id for i in st.investigators]
                fact.known_by = list(dict.fromkeys([*fact.known_by, *readers]))
                st.progress_at = st.turn
                turn.clues.append(cid)
                original = originals is None or cid in originals
                if clue.npc and not heard and original:
                    unlocked = st.npc(clue.npc).unlocked
                    if cid not in unlocked:
                        unlocked.append(cid)
                st.add("clue", f"〔{clue.title}〕{clue.text.strip()}", audience=audience)
                notices = [{"type": "clue", "id": cid, "title": clue.title, "text": clue.text.strip(), "heard": heard,
                            "audience": audience}]
                paper = ""
                if not heard and original and clue.handout and clue.handout not in st.handouts:
                    h = m.handout(clue.handout)
                    if audience is None:
                        st.handouts.append(h.id)
                        st.add("handout", h.title)
                        notices.append({"type": "handout", "id": h.id, "title": h.title, "kind": h.kind, "text": h.text,
                                        "audience": audience})
                    else:
                        paper = h.id
                if sanity is not None:
                    turn.sanity.append((inv.name, sanity))
                    st.add("sanity", sanity.describe(), who=inv.name, audience=audience)
                    notices.append({"type": "sanity", "who": inv.name, **_sanity_view(sanity),
                                    **self._madness(inv, sanity, turn), "audience": audience})
                return {"notices": notices, "paper": paper, "audience": audience}

            async def gain(cid: str = cid) -> None:
                acquired = await step("commit", turn, commit)
                for index, notice in enumerate(acquired["notices"]):
                    await step(f"notice:{index}", turn, lambda notice=notice: self.emit(notice))
                if acquired["paper"]:
                    await step("paper", turn, lambda cid=cid: self.present(
                        [{"ref": f"handout:{acquired['paper']}", "to": acquired["audience"]}],
                        key=f"clue:{st.turn}:{cid}:paper"))

            await step(key, turn, gain)
        if turn.clues:
            await step("clue-sheet", turn, lambda: self.emit({"type": "sheet"}))

    async def _flags(self, flags: list[str], turn: Turn, *, landed: bool = False, readers: list[str] | None = None) -> None:
        """Set story flags, and what hangs on them happens. `landed`: the world already set them (an
        agenda brought them about, world/agents.effects); only their consequences are left."""
        st, m = self.state, self.m
        step = self._resolution_step
        for flag in flags:
            key = f"/flag:{flag}"
            if flag not in m.flags or (flag in st.flags and not landed and not self._resolution_pending(key)):
                continue

            def commit(flag=flag) -> None:
                if flag not in st.flags:
                    st.flags.append(flag)
                st.progress_at = st.turn

            async def settle(flag=flag) -> None:
                scope = await step("audience", turn, lambda: recipients.scope(st, readers) if readers is not None
                                   else self._narrative_audience(turn))
                await step("commit", turn, commit)
                await step("notice", turn, lambda: self.emit({"type": "flag", "flag": flag, "meaning": m.flags[flag], "audience": scope}))
                await step("showcase", turn, lambda: self.showcases.fire(f"flag:{flag}", audience=scope))
                await self._events(turn)

            await step(key, turn, settle)

    async def _time(self, turn: Turn | None = None) -> None:
        st, m = self.state, self.m
        turn = turn or Turn()
        await self._recover_madness(turn)
        if st.minutes // 1440 != st.day:  # a new day: each investigator's daily SAN loss starts again
            st.day = st.minutes // 1440
            for inv in st.investigators:
                inv.new_day()
        await self._events(turn)
        await self._world(turn)
        await self._people()
        if not st.view_kind and (hour := views.default(m, m.location(st.location), st.minutes)) != st.view:
            await self._view(hour)  # night has fallen on the place: its picture for the night
        await self.plugins.due(turn)  # rule plugins' timers
        await self._wake_due(turn)
        await self.emit({"type": "time", "clock": st.clock, "phase": time_of_day(st.minutes)})

    async def _events(self, turn: Turn, *, visit: str = "", at: int | None = None) -> None:
        """Settle events whose actual conditions hold. Time, place and checks are independent gates."""
        st = self.state
        ordered = sorted(self.m.events, key=lambda e: parse_time(split_when(e.when)[0])
                         if split_when(e.when)[0] else st.minutes)
        for ev in ordered:
            if ev.id in self._firing_events:
                continue
            key = f"/event:{ev.id}"
            if ev.id in st.fired and not self._resolution_pending(key):
                continue
            if ev.after_rounds and not self._resolution_pending(key):
                gate = ev.after_rounds
                encounters = [*st.encounter_log, *([st.encounter] if st.encounter is not None else [])]
                if not any(enc.source_event == gate.event and enc.completed_rounds >= gate.completed for enc in encounters):
                    continue
            if ev.when.startswith("visit:"):
                if ev.when[6:] != visit:
                    continue
            else:
                scheduled, condition = split_when(ev.when)
                if scheduled and parse_time(scheduled) > st.minutes:
                    continue
                if condition and not holds(self.world, condition):
                    continue
            if ev.check and not self._resolution_pending(key):
                day = st.minutes // 1440
                previous = st.event_attempts.get(ev.id, {})
                if previous.get("day") == day and not previous["roll"]["passed"]:
                    continue

                def check(ev=ev, day=day) -> dict:
                    trial = self.rules.check(st.investigator, ev.check.skill,
                                             DIFFICULTY_ZH[ev.check.difficulty], ev.check.bonus, mark=False)
                    turn.checks.append(trial)
                    st.add("dice", trial.describe(), who=st.investigator.name)
                    notice = {"type": "dice", "who": st.investigator.name, **_check_view(trial)}
                    st.event_attempts[ev.id] = {"day": day, "roll": notice, "shown": False}
                    return notice

                notice = previous["roll"] if previous.get("day") == day else await self._resolution_step(
                    f"event-check:{ev.id}:{day}", turn, check)
                if not st.event_attempts[ev.id]["shown"]:
                    await self._resolution_step(f"event-check-notice:{ev.id}:{day}", turn,
                                                lambda notice=notice: self.emit(notice))
                    st.event_attempts[ev.id]["shown"] = True
                if not notice["passed"]:
                    continue
            if at is not None and ev.when.startswith("visit:"):
                await self._fire(ev, turn, at=at)
            else:
                await self._fire(ev, turn)

    def _present(self, *, at: int | None = None) -> tuple[str, ...]:
        return tuple(self.world.present(self.state.location, at=self.world.now if at is None else at))

    async def _people(self, *, at: int | None = None) -> None:
        """Who is here changed without the party moving (someone walked in, left, fell): the
        screens hear of it."""
        when = self.world.now if at is None else at
        scope = recipients.scope(self.state, self.world.observers(self.state.location, at=when))
        if (here := self._present(at=when)) != self._here:
            self._here = here
            await self.emit({"type": "people", "location": self.state.location, "ids": list(here), "audience": scope})
        for n in self.m.npcs:  # hurt or killed backstage: their face is drawn again (Codex, when it is here)
            if n.status != self._status.get(n.id, ""):
                self._status[n.id] = n.status
                if n.status and n.status != "失踪":
                    self.visuals.look(f"npc:{n.id}", f"{n.name}{n.status}")

    async def _view(self, key: str, kind: str = "", *, audience: list[str] | None = None, local: bool = True,
                    location: str = "") -> None:
        """The scene shows another picture of this place (engine/views.py)."""
        st = self.state
        if local and audience is None:
            audience = self._narrative_audience()
        if not local or key != st.view or kind != st.view_kind:
            if local:
                st.view, st.view_kind = key, kind
            scope = {"audience": audience} if audience is not None else {}
            await self.emit({"type": "view", "location": location or st.location, "key": key, **scope})

    async def _world(self, turn: Turn) -> None:
        """The rest of the world after what just happened: now, or once the turn is told."""
        self._moved_on = self._moved_on or bool(turn.arrived)
        if self.world_later and not turn.interlude:
            self._world_owed = True  # remember() starts it; the player does not wait for it
        else:
            before = set(self.state.flags)
            await self._live(turn)
            self._play(turn, self._dealing)
            await self._flags([f for f in self.state.flags if f not in before], turn, landed=True)

    async def _look(self) -> None:
        """After a long stretch of game time (a night, a journey) the director looks backstage, on
        the screens: a backstage adjustment of its own if one is called for (a rewrite, a plan, an
        arrangement), checked as its plans are. What the important NPCs do is decided when they
        wake (world/scheduler.py), not here. One call; without an answer nothing changes."""
        verdict = await self.cast.review(self.state, self._look_material()) if hasattr(self.cast, "review") else None
        self._debug("director", {"phase": "look", "answered": verdict is not None})
        if verdict is not None and verdict.backstage:
            problems = await self._backstage(list(verdict.backstage), verdict.reason)
            self._debug("director", {"phase": "look-backstage", "reason": verdict.reason, "problems": problems,
                                      "ops": [op.model_dump(mode="json") for op in verdict.backstage]})

    def _look_material(self) -> str:
        w, st, m = self.world, self.state, self.m
        found = "、".join(m.clue(c).title for c in st.clues) or "还没有"
        recent = "\n".join(f"- {e.who or e.kind}：{e.text[:120]}" for e in st.log[-8:] if e.kind in ("player", "keeper", "npc"))
        return ("【待审】没有\n"
                f"【调查员】在 {m.location(st.location).name}；已找到的线索：{found}\n【最近】\n{recent or '无'}\n"
                f"【世界】现在是第 {st.minutes} 分钟。\n{world_view.director_view(w).text}")

    async def _backstage(self, ops: list, reason: str) -> list[str]:
        """The director's own backstage work outside a plan (Game._look): tried on a copy and
        checked as a plan's is; what touches the investigators' scene waits for the keeper."""
        w = World(self.m.model_copy(deep=True), self.state.model_copy(deep=True))
        problems: list[str] = []
        for i, op in enumerate(ops):
            done = w.apply(op.model_copy(update={"actor": DIRECTOR, "id": f"review-trial:{i}"}))
            problems += [f"幕后第 {i + 1} 条（{op.kind}）：{p}" for p in done.problems]
        if problems:
            return problems
        raw = [op.model_dump(mode="json", exclude={"id"}) | {"actor": DIRECTOR} for op in ops]
        involved = [x for op in ops for x in stage.touches(self.world, op)]
        if involved:  # someone walks into their scene: the keeper plays it
            ident = f"review:{self.state.turn}:{digest(raw)[:8]}"
            stage.queue(self.world, ident, DIRECTOR, raw, involved, [x for op in ops for x in stage.leaves(self.world, op)])
            return []
        req = desk.submit(self.world, "director:review", raw, reads={}, reason=reason)
        landed = desk.land(self.world, req.id)
        return [] if landed.status == "landed" else list(landed.problems)

    def _engaged(self, action: Action | None) -> set[str]:
        """Who the investigators are dealing with in this action: a beat that would move them
        waits for the exchange to end (world/stage.py)."""
        if action is None or action.kind == "meta":
            return set()
        return {x for x in (action.npc, *(step.npc for step in action.before)) if x}

    def _play(self, turn: Turn, engaged: set[str]) -> list[str]:
        """Backstage work waiting for the stage plays now, and is told in this turn's narration
        (or the interlude). A beat that would take someone the investigators are dealing with out
        of the scene waits for that exchange to end unless the keeper cues it. Returns the story
        flags the beats set, whose consequences are left to _flags."""
        w = self.world
        before = set(self.state.flags)
        for beat in stage.waiting(w):
            if beat.cue == "hold" or (beat.cue == "auto" and engaged & set(beat.leaving)):
                continue
            since = w.rec.seq
            problems = stage.play(w, beat)
            self._debug("world", {"phase": "beat", "id": beat.id, "status": beat.status, "problems": problems})
            if not problems:
                turn.world += world_view.happened(w, since)
        return [f for f in self.state.flags if f not in before]

    async def _world_settled(self) -> None:
        """The world's life after the last turn, finished before anything else happens."""
        task, self._world_task = self._world_task, None
        if task is not None and not task.done():
            await self.emit({"type": "status", "text": "KP 正在构思…"})
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _live_later(self) -> None:
        """The minutes the last turn took, lived by the rest of the world after it was told. What
        happened in front of the party waits in self._late for the next narration or an interlude."""
        late = Turn()
        before = set(self.state.flags)
        start = self.world.rec.simulated
        try:
            await self._live(late, inline=False)
            self._play(late, self._dealing)
            if self.world.reviewing and start is not None and self.state.minutes - start >= self.minds.long:
                await self._look()  # a night, a journey: the director's look backstage
                self._play(late, self._dealing)
            await self._flags([f for f in self.state.flags if f not in before], late, landed=True)
        except asyncio.CancelledError:
            raise
        except CassetteError:
            raise
        except Exception as exc:  # the world's own life is a bonus: a failure costs nobody a turn
            self._debug("world", {"phase": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]})
            return
        for attr in ("events", "sanity", "notes", "hints", "world", "fight"):
            getattr(self._late, attr).extend(getattr(late, attr))
        if self.checkpoint:
            self.checkpoint()

    async def _live(self, turn: Turn, *, inline: bool = True) -> None:
        """Advance prepared work or react to new experienced events, within one turn's allowance."""
        st, rec = self.state, self.world.rec
        start = st.minutes if rec.simulated is None else rec.simulated
        if st.minutes < start:
            return
        pending = next(((key, work) for key, work in rec.npc_work.items()
                        if work["turn"] == st.turn and work["start"] == start and work["end"] == st.minutes), None)
        events_only = st.minutes <= start
        if events_only and pending is None and not scheduler.reactions_ready(self.world, moved_on=self._moved_on):
            return
        since = pending[1]["since"] if pending else rec.seq
        tick = (pending[0] if pending else f"reaction:{st.turn}:{st.minutes}:{rec.seq}" if events_only
                else f"tick:{start}:{st.minutes}")
        brain = _Minds(self, quiet=not inline) if self.brain is not None else None
        report = await scheduler.advance(self.world, brain, start, st.minutes, budget=self.minds, tick=tick,
                                         shared=True, events_only=events_only, moved_on=self._moved_on,
                                         checkpoint=self.checkpoint, held=self._held())
        self._moved_on = False
        self._debug("world", asdict(report))
        self.debug_world.append(report)
        del self.debug_world[:-30]
        self.world.rec.simulated = st.minutes
        turn.world += world_view.happened(self.world, since)
        if inline and self.turn_checkpoint:  # after a turn there is no turn record to keep
            self.turn_checkpoint(turn)

    async def _fire(self, ev: Event, turn: Turn, *, at: int | None = None) -> None:
        st = self.state
        key = f"/event:{ev.id}"
        if ev.id in st.fired and not self._resolution_pending(key):
            return
        step = self._resolution_step
        scheduled = split_when(ev.when)[0]
        timed = bool(scheduled)
        event_time = parse_time(scheduled) if scheduled else self.world.now if at is None else at
        if scheduled and not (turn.interval and turn.interval["start"] <= event_time <= turn.interval["end"]):
            event_time = max(event_time, self.world.now)
        place = ev.where or (ev.move_to if timed else "") or st.location
        relocate = bool(ev.move_to and (not timed or ev.where))
        needs_context = bool(ev.fight or ev.chase or relocate or ev.advance_to)
        witnessed = bool(self.world.observers(place, at=event_time))
        if needs_context and witnessed and not self._resolution_pending(key):
            existing = self.world_encounter()
            if self.world.place(st.investigator.id) != place or (existing is not None and existing.place != place):
                return  # this same unfired event remains executable when its real scene can take it
            event_time = max(event_time, self._actor_cursor())
        actor_witnessed = self.world.place_at(st.investigator.id, event_time) == place
        witnessed = bool(self.world.observers(place, at=event_time))
        witnesses: list[str] = []
        sanity_witnesses: list[str] = []
        victim = ""

        def audience() -> dict:
            return {} if set(witnesses) == {i.id for i in st.investigators} else {"audience": witnesses}

        def observers() -> dict:
            watching = self.world.observers(place, at=event_time)
            horrors = []
            if ev.sights:
                from .encounter import profiles

                horrors = [prof.id if (prof := profiles.profile(self.world, ident)) else ident for ident in ev.sights]
            aware = [who for who in watching if st.find(who).can_act and (not horrors or any(
                (seen := perception.get(st, who, horror)) is None or seen.visible for horror in horrors))]
            return {"witnesses": watching, "sanity": aware,
                    "victim": st.investigator.id if self.world.place_at(st.investigator.id, event_time) == place else ""}

        def commit() -> None:
            st.fired.append(ev.id)
            st.progress_at = st.turn
            if witnessed:
                if actor_witnessed:
                    turn.events.append(ev)
                entry = st.add("event", ev.read_aloud.strip() or ev.title, **audience())
                entry.time = format_time(event_time)
            else:
                with self.world.at(event_time):
                    self.world.apply(Occur(id=f"event:{ev.id}:away", actor="engine", place=place,
                                           participants=ev.present, text=ev.read_aloud.strip() or ev.text,
                                           visibility="public"))

        def sanity_for(inv: Investigator) -> dict | None:
            local = turn if actor_witnessed else Turn()
            if not inv.can_act:
                return None
            logged = len(st.log)
            with self.world.at(event_time):
                notice = self._lose_sanity(inv, ev.sanity, local)
            for entry in st.log[logged:]:
                entry.time = format_time(event_time)
                entry.audience = audience().get("audience")
            notice.update(audience())
            return notice

        def damage() -> dict | None:
            notice = self._wound_event(victim, ev.damage or "", f"event:{ev.id}:{st.turn}", turn) if victim else None
            if notice:
                turn.notes.append(f"{notice['who']}{notice['text']}")
            return notice

        def sets() -> None:
            for flag in ev.sets:
                if flag not in st.flags:
                    st.flags.append(flag)

        def appear() -> None:
            """Who the read-aloud puts before the investigators is there in the record too (2026-10-22:
            the truck's driver stepped down in the event, the record had him nowhere, and the keeper
            then told an empty cab): where the event takes the party, else where it is."""
            to = ev.move_to if relocate and self.encounter() is None else place
            for nid in ev.present:
                ent = self.world.rec.entities.get(nid)
                if ent is not None and ent.kind == "npc" and ent.at != to and ent.status not in ("死亡", "失踪"):
                    if self.world.place_at(nid, event_time) == to:
                        continue
                    available = self.world.available_at(nid)
                    if available is not None and available > event_time:
                        turn.notes.append(f"事件「{ev.title}」中的{ent.name}已有更晚的实际行动，没有倒放其位置。")
                        continue
                    with self.world.at(event_time):
                        self.world.apply(Move(id=f"event:{ev.id}:{nid}", entity=nid, to=to))

        async def fire() -> None:
            nonlocal place, witnessed, relocate, witnesses, sanity_witnesses, victim, actor_witnessed, event_time
            def context_value() -> dict:
                target = st.minutes
                if ev.advance_to and witnessed:
                    when = ev.advance_to if ev.advance_to.startswith("D") else f"D{st.minutes // 1440 + 1} {ev.advance_to}"
                    target = max(target, parse_time(when))
                return {"place": place, "witnessed": witnessed, "relocate": relocate, "target": target,
                        "actor_witnessed": actor_witnessed, "at": event_time,
                        "travellers": self.world.observers(place, at=event_time)}

            context = await step("context", turn, context_value)
            place, witnessed, relocate = context["place"], context["witnessed"], context["relocate"]
            actor_witnessed, event_time = context["actor_witnessed"], context["at"]
            if ev.advance_to and witnessed:
                # Save the absolute target before advancing: a resumed overnight event cannot add
                # another day. _time keeps day boundaries, the living world and its schedule intact.
                def advance() -> None:
                    travellers = context["travellers"]
                    start = max(self._actor_cursor(who) for who in travellers)
                    self._spend(max(0, context["target"] - start), turn, participants=travellers)

                await step("elapsed", turn, advance)
                event_time = max(event_time, context["target"])
                await step("time", turn, lambda: self._time(turn))
            # Freeze the people who actually experienced this occurrence, before it moves
            # anyone. A retry or arrival beside another investigator cannot add witnesses.
            observed = await step("audience", turn, observers)
            witnesses, sanity_witnesses, victim = observed["witnesses"], observed["sanity"], observed["victim"]
            if ev.present and self.world is not None:
                await step("present", turn, appear)
            if ev.effects:
                await step("effects", turn, lambda: self._event_effects(ev, turn, at=event_time))
            await step("commit", turn, commit)
            if not witnessed:
                await step("sets", turn, sets)
                return
            if not turn.told:  # at the opening the keeper tells it, once (the user, 2026-10-22: shown, then told again)
                await step("notice", turn, lambda: self.emit({"type": "event", "id": ev.id, "title": ev.title,
                                                             "text": ev.read_aloud.strip(), **audience()}))
            if ev.art and views.shown(self.m, ev.art):
                await step("view", turn, lambda: self._view(ev.art, "moment", audience=audience().get("audience"),
                                                            local=actor_witnessed, location=place))
            await step("showcase", turn, lambda: self.showcases.fire(f"event:{ev.id}", audience=audience().get("audience")))
            await step("named", turn, lambda: self.showcases.named(f"{ev.title}\n{ev.read_aloud}",
                                                                    audience=audience().get("audience")))
            if relocate and self.encounter() is None:
                await step("move", turn, lambda: self._arrive(ev.move_to, turn) if ev.move_to != st.location
                           or self._resolution_pending(f"arrive:{ev.move_to}") else None)
            elif relocate and ev.move_to != st.location:  # a fight never carries the party off
                await step("move-held", turn, lambda: turn.notes.append(
                    f"事件「{ev.title}」本要把调查员带到{self.m.location(ev.move_to).name}，遭遇还在进行，调查员没有被带走。"))
            if ev.sanity:
                for who in sanity_witnesses:
                    inv = st.find(who)
                    notice = await step(f"sanity:{inv.id}", turn, lambda inv=inv: sanity_for(inv))
                    if notice:
                        await step(f"sanity-notice:{inv.id}", turn, lambda notice=notice: self.emit(notice))
            if ev.damage:
                notice = await step("damage", turn, damage)
                if notice:
                    await step("damage-notice", turn, lambda: self.emit(notice))
            await step("sets", turn, sets)
            for flag in ev.sets:
                await step(f"flag-showcase:{flag}", turn, lambda flag=flag: self.showcases.fire(
                    f"flag:{flag}", audience=audience().get("audience")))
            if (ev.fight or ev.chase) and not ev.ending:  # a scripted attack or pursuit (engine/encounter)
                await self._scripted(ev, turn)
            if ev.ending:
                await step("check-ending", turn, lambda: self._check_ending(turn))
                if not st.ending or self._resolution_pending(f"/ending:{ev.ending}"):
                    await self._end(ev.ending, turn)

        self._firing_events.add(ev.id)
        try:
            await step(key, turn, fire)
        finally:
            self._firing_events.discard(ev.id)

    async def _check_ending(self, turn: Turn) -> None:
        st, m = self.state, self.m
        if st.ending:
            if self._resolution_pending(f"/ending:{st.ending}"):
                await self._end(st.ending, turn)
            return
        for flag, required in list(st.pending_story_flags.items()):
            if self._requirements(required):
                st.pending_story_flags.pop(flag, None)
                await self._flags([flag], turn)
        for end in m.endings:
            occurred = any(ev.ending == end.id and ev.id in st.fired for ev in m.events)
            flagged = end.condition.startswith("flag:") and end.condition[5:] in st.flags
            if self._requirements(end.requires) and (flagged or occurred):
                await self._end(end.id, turn)
                return

    def _requirements(self, required: list[str]) -> bool:
        return all(req[5:] in self.state.flags if req.startswith("flag:") else req in self.state.clues for req in required)

    async def _end(self, ending_id: str, turn: Turn) -> None:
        st, m = self.state, self.m
        end = m.ending(ending_id)  # the module's, or the engine's own (module/schema.PARTY_DEAD)
        assert end is not None, ending_id
        if not self._requirements(end.requires):
            return
        step = self._resolution_step

        def reward(inv: Investigator) -> str:
            gain = re.search(r"理智\s*\+\s*(\d*[dD]\d+|\d+)", end.reward)
            amount = end.sanity_gain or (gain.group(1) if gain else "")
            if not amount or not inv.can_act:
                return ""
            n = self.rules.dice.total(amount.upper()) if "D" in amount.upper() else int(amount)
            inv.san = min(inv.san_max, inv.san + n)
            return f"{inv.name} 理智 +{n}"

        async def settle() -> None:
            notes = []
            for inv in st.investigators:
                if note := await step(f"reward:{inv.id}", turn, lambda inv=inv: reward(inv)):
                    notes.append(note)

            def commit() -> dict:
                st.ending = end.id
                turn.ending = end.id
                st.add("ending", f"{end.title}：{end.text.strip()}")
                return {"type": "ending", "id": end.id, "title": end.title, "text": end.text.strip(), "reward": end.reward, "notes": notes}

            notice = await step("commit", turn, commit)
            await step("notice", turn, lambda: self.emit(notice))
            await step("showcase", turn, lambda: self.showcases.fire(f"ending:{end.id}"))

        await step(f"/ending:{end.id}", turn, settle)

    # -- nobody of them can act ----------------------------------------------------------------------
    async def _party_down(self, turn: Turn) -> None:
        """The existing keeper arranges rescue, treatment, waiting or a justified interruption."""
        from . import aftermath

        await aftermath.settle(self, turn)

    async def _wake_due(self, turn: Turn, *, since: int | None = None) -> None:
        """Investigators out cold whose time has come (rules/wounds.waking, from when they fell, or from
        `since` when that is not known) come to: at 0 HP with the day's first point of natural healing
        (p. 101). Each once, in its own op."""
        st, step = self.state, self._resolution_step
        for inv in st.investigators:
            def commit(inv=inv) -> dict | None:
                body = effects.body_of(self.world, inv.id)
                after = wounds.waking(body) if body is not None else None
                start = body.unconscious_at if body is not None and body.unconscious_at is not None else since
                if after is None or start is None or st.minutes < start + after:
                    return None
                woke = [effects.Heal(target=inv.id, amount=1, means="natural")] if body.hp <= 0 else []
                done = effects.commit(self.world, [*woke, effects.Wake(target=inv.id)], op_id=f"wake:{inv.id}:{start + after}",
                                      source="engine")
                if not done.ok or done.repeat:
                    return None
                text = f"醒了过来（昏迷了 {_span(st.minutes - start)}）"
                st.add("damage", text, who=inv.name)
                turn.notes.append(f"{inv.name}{text}，生命 {inv.hp}/{inv.hp_max}。")
                return {"type": "damage", "who": inv.name, "text": text}

            notice = await step(f"wake:{inv.id}/commit", turn, commit)
            if notice:
                await step(f"wake:{inv.id}/notice", turn, lambda notice=notice: self.emit(notice))

    def _npc_message(self, turn: Turn, npc_id: str) -> str:
        st, m = self.state, self.m
        inv = st.investigator
        action = turn.action
        assert action is not None
        remote = action.communication == "remote"
        said = (f"对你说：「{action.speech}」" if action.speech else
                f"在你面前：{action.visible}" if action.visible and not remote else "等候你的回应")
        where = self.world.name(self.world.place(npc_id)) if remote else m.location(st.location).name
        parts = [f"（系统：{st.clock}，你在{where}。{'通过远程渠道，' if remote else ''}{inv.name}（{inv.occupation}）{said}"]
        if action.approach and action.approach != "心理学":
            parts.append(f"方式：{action.approach}。")
        if remote and (settled := self._social_result(turn, npc_id)):
            parts.append(f"【已结算的演出边界，不是角色听到的话】{settled}")
        parts.append(self._npc_disclosure(turn, npc_id))
        parts.append("用你的口吻回答。）")
        return "".join(parts)

    def _social_result(self, turn: Turn, npc_id: str) -> str:
        """The actual social-check scope for the keeper to direct a post-roll performance."""
        action = turn.action
        if action is None or action.npc != npc_id:
            return ""
        social = {"说服", "话术", "恐吓", "魅惑"}
        result = ""
        passed = False
        if turn.primary is not None:
            c = turn.checks[turn.primary]
            if canonical(c.name) not in social:
                return ""
            passed = c.passed
            effect = ("本次交涉成功，让步限于这次请求和已授权内容" if c.passed else
                      "本次交涉未达成，已结算的后果仍成立，不额外追加未裁定的惩罚")
            result = f"检定：{c.name} {c.level.zh}，{effect}。"
        elif turn.earlier is not None:
            e = turn.earlier
            if canonical(e.skill) not in social:
                return ""
            passed = e.passed
            effect = ("本次交涉已成功，让步仍限于这次请求和已授权内容" if e.passed else
                      "本次请求仍未达成，既有后果继续成立，不另加惩罚")
            result = f"检定：同样的{e.skill}刚才已经试过，不重掷，结果照旧：{effect}。"
        if result and turn.ruling and turn.ruling.why:
            result += (f"本次检定范围：{turn.ruling.why}。这是演出边界，不是角色听到的说明；"
                       + ("兑现已经成功的这项让步，其他事项按自身条件处理。" if passed else
                          "这项请求没有达成，不另行完成同一个请求；其他事项按自身条件处理。"))
        return result

    def _npc_disclosure(self, turn: Turn, npc_id: str) -> str:
        """The same disclosure context for replies, initiative and encounter speech."""
        st, m = self.state, self.m
        remote = turn.action is not None and turn.action.npc == npc_id and turn.action.communication == "remote"
        available = knowledge.disclosable_clues(m, st, npc_id, record=self.world.rec, present=not remote)
        mine = [c.id for c in m.clues if c.id in available]
        if mine:
            return ("已解锁，现在可以说出（含此前已授权内容）：" + "；".join(f"{cid}「{m.clue(cid).title}」" for cid in mine)
                    + "。按本次问题和眼下处境交代相关内容；需要说清时一次说清，不按条数拖到后续回合。说出了要点就填进 told。")
        return "尚无可公开的门槛线索；仍可按你已知的日常信息正常回答。"

    def _encounter_message(self, turn: Turn, npc_id: str) -> str:
        """What an NPC in a fight or a chase is told when the keeper has him speak: where, what just
        happened (as the players saw it), what he is after. He answers in his own voice."""
        st, m = self.state, self.m
        enc = st.encounter
        p = enc.get(npc_id) if enc is not None else None
        goal = p.arrangement.goal if p is not None and p.arrangement is not None else m.npc(npc_id).goal
        seen = [e.text for e in st.log if e.turn == st.turn and e.kind in ("dice", "event")][-6:]  # as the table saw it
        kind = "追逐" if enc is not None and enc.mode == "chase" else "打斗"
        return (f"（系统：{st.clock}，{m.location(st.location).name}，正在{kind}。刚才：{'；'.join(seen) or '刚刚动起手来'}。"
                f"你此刻要的：{goal or '保住自己'}。守秘人让你此刻开口：说一两句符合处境的话，可以喊、劝、威胁、求饶；"
                "不描写别人的动作，不替调查员决定。" + self._npc_disclosure(turn, npc_id) + "用你的口吻回答。）")

    def _deeds(self, npc_id: str) -> str:
        """A performer's own records; the party's private log and summary are not its memory."""
        return world_view.npc_brief(self.world, npc_id, query="表演中可以引用的亲历与当面获知的经过")

    def _public_location(self, loc_id: str) -> str:
        loc = self.m.location(loc_id)
        status = {n: e.status for n, e in self.world.rec.entities.items() if e.kind == "npc" and e.status}
        people = "\n".join(f"{n.name}" + (f"（{status[n.id]}）" if n.id in status else "") + f"：{n.look}" for n in self.m.npcs_at(loc_id))
        return f"{loc.name}\n{loc.read_aloud.strip()}\n{people}"

    def _report(self, turn: Turn, *, omit_replies: set[str] | None = None) -> str:
        st, m = self.state, self.m
        inv = st.investigator
        loc = m.location(st.location)
        out = [f"【第 {st.turn} 轮｜{st.clock}（{time_of_day(st.minutes)}）｜{loc.name}】", f"调查员：{inv.name}（{inv.occupation}）", world.context(st)]
        if len(st.investigators) > 1:
            readers = self._narrative_audience(turn)
            names = "、".join(i.name for i in st.investigators if readers is None or i.id in readers)
            out.append(f"【本段接收者】{names}。共同正文只写这些人在当前现场能够感知的事；"
                       "其他地点的调查经过、未传达的私语和人物背景仍是主持资料，不替调查员获知或分享。"
                       "只有其中一人察觉或理解的内容，用 asides 单独交代。")
        people = [n.name if st.npc(n.id).met else f"未通名的人（{n.look or '暂无外观记录'}）" for n in m.npcs_at(st.location)]
        if st.ongoing:
            out.append(f"【上一段叙述停在】{st.ongoing}")
        out.append("【当前在场人物，以记录为准】" + ("、".join(people) or "没有其他人")
                   + "。人物未出现在当前地点不代表失踪，不推断未观察到的去向；先前台词和旁白不能替代位置记录。")
        out.append(f"【实际持有】{'、'.join(inv.inventory) or '未登记随身物品'}（专用工具与新取得物品以持有记录为准。）")
        if talk := self._party_talk():
            out.append("【调查员之间的交谈】（玩家都已看到；不复述，不替被问到的调查员回答、交出东西或行动）\n" + "\n".join(talk))
        if st.sky and st.minutes - st.sky_at < weather.LASTS:
            out.append(f"天气：{weather.KINDS[st.sky]}（你之前写到的）")
        a = turn.action
        if turn.interlude:
            out.append("（插曲：调查员这回没有新的行动，下面这些事接着上一段叙述发生。）")
        if a and a.kind == "meta":
            out.append(f"{kp_line(a.summary)}\n请以守秘人身份用一两句话简短回答，不推进剧情。")
            return "\n".join(out)
        if a:
            out.append(f"行动意图（是否达成以下列结算为准）：{a.summary}")
            if st.pending_turn.get("preparation", {}).get("blocked"):
                out.append("【本轮实际执行】仅完成出发前交谈，行程与目的地的后续行动没有执行。保留原来的目的，"
                           "按人物实际回答交代当前待定之处，不在原地登记抵达后的交付、痕迹或见闻。")
            for item in self._items_for(a):
                out.append(f"【眼前物件】{item.name}：{item.description}（配图卡已展示外观，接着动作讲，不另添隐藏标记或效果。）")
            if a.kind == "daily":
                out.append("【日常】接住玩家眼下的生活意图，写具体的人情和应答；不催查案、不强添线索或怪异。已触发的事件仍照实讲。")
        if turn.ruling and turn.ruling.reason:
            out.append("【仅供KP的裁定依据】" + turn.ruling.reason
                       + "\n这不是各分支都已发生：依实际成败兑现当前请求，把成立的现场变化写进scene。"
                       "以本轮实际结算和已展示台词继续；骰前的阻拦不能推翻骰后成功，私有依据不直接念给玩家。")
        if turn.ruling and turn.ruling.why:
            out.append("【主行动的公开裁定目标】" + turn.ruling.why
                       + "\n按已向玩家说明的范围承接最终结果，不因技能名称另改目标；"
                       "其他事件的骰不覆盖本次结果，尚未结算的附带行动也不自动成功或失败。")
            if turn.primary is not None:
                out.append("【该目标的最终主检定】" + turn.checks[turn.primary].describe())
            elif turn.earlier is not None:
                out.append("【该目标沿用的主检定】" + turn.earlier.text)
        if a and (settled := self._social_result(turn, a.npc)):
            out.append("【本次交涉已结算范围】" + settled)
        if len(turn.route) > 2:
            out.append("【沿途】" + " → ".join(m.location(pid).name for pid in turn.route))
            out += [f"路过{m.location(pid).name}：{m.location(pid).read_aloud.strip()}" for pid in turn.route[1:-1]]
            out.append("按行进顺序带出一两处路边生活，再衔接目的地；只是路过，没有进门调查、获得线索或与人交谈。")
        if turn.arrived:
            if turn.first_visit:
                out.append(f"【进入新地点】\n{self._public_location(turn.arrived)}")
            else:
                out.append(f"回到了 {m.location(turn.arrived).name}。")
            out += world_view.arrival(self.world, turn.arrived)  # people move: a place's own words may name who has gone
        if turn.checks:
            out += [f"检定：{c.describe()}" for c in turn.checks]
            if not turn.clues and not turn.events:
                out.append("【发现边界】本轮未结算新的模组线索条目，不等于成功没有效果。兑现本次检定确认的观察信息或行动成果，"
                           "未预写的日常细节可结合现场确定并记录；不得绕过既有秘密、线索或物品的取得条件，也不改变检定成败。")
        elif turn.earlier is not None:
            e = turn.earlier
            if e.passed:
                out.append(f"检定：不重掷。这件事刚才已经检定成功（{e.text}），结果照旧，这次没有新的收获。")
            else:
                out.append(
                    f"检定：不重掷。{inv.name}刚才已经这样试过，失败了（{e.text}），情况没变，结果照旧。"
                    "写出再试一次也不管用：要换个办法，或者拿到新的线索、筹码，局面才会松动。"
                )
        elif a and a.asked and a.kind != "move":
            why = turn.ruling.reason if turn.ruling else ""
            out.append(f"检定：玩家要求{a.asked.skill}检定，裁定这次不用掷" + (f"（{why}）" if why else "") + "。")
        elif a and a.kind not in ("move",) and not turn.fight:
            out.append("检定：无需检定")
        if turn.push:
            opts = []
            if turn.push.get("can_push"):
                opts.append("孤注一掷")
            if turn.push.get("luck") is not None:
                opts.append(f"花 {turn.push['luck']} 点幸运")
            out.append(f"（玩家可以选择：{'或'.join(opts)}，结果下一步揭晓；现在只写失败的样子。）")
        out += turn.fight  # a round of a fight or a chase, as settled (engine/encounter)
        from .encounter import views as encounter_views

        if "encounter_snapshot" not in turn.narrating:
            when = int(turn.interval["end"]) if turn.interval else None
            place = turn.interval.get("destination", "") if turn.interval else ""
            viewer = turn.interval.get("actor", inv.id) if turn.interval else inv.id
            turn.narrating["encounter_snapshot"] = encounter_views.settled_text(
                self.world, viewer=viewer, at=when, place=place, include_results=not bool(turn.fight))
        if snapshot := turn.narrating["encounter_snapshot"]:
            out.append(snapshot)
        out += self._found(turn)
        for who, s in turn.sanity:
            out.append(f"理智：{who} {s.describe()}" + (f"；疯狂发作表（日文原文，请用中文演出）：{s.bout}" if s.bout else ""))
        if turn.npc_lines:
            names = "、".join(m.npc(npc_id).name for npc_id, _ in turn.npc_lines)
            out.append(f"【对话已展示】{names}已回应。不要引用、概述或解释刚才的对话，也不重复检定卡；只叙述本轮另行发生的变化。")
            unpaired = list(turn.replies.items())
            for npc_id, line in turn.npc_lines:
                pair = next(((key, s) for key, s in unpaired if s.npc == npc_id and s.line == line), None)
                if pair is not None:
                    unpaired.remove(pair)
                    if omit_replies and pair[0] in omit_replies:
                        continue  # the actual tool receipt below carries this same reply and its audience
                said = pair[1] if pair is not None else None
                listeners = "全桌" if said is None or said.audience is None else "仅" + "、".join(st.find(i).name for i in said.audience)
                out.append(f"【{listeners}已听到的原话及动作｜{m.npc(npc_id).name}】{line}")
            out.append("以上动作已经演出，接着其完成后的状态叙述，不重新端茶、问同一问题或把已做完的事写成准备做；私下内容不转述给其他人。")
            if turn.joined:
                out.append(f"其中{'、'.join(m.npc(x).name for x in turn.joined)}是这一轮自己主动开口的。")
        if turn.performance:
            performer, line = turn.performance
            said = next((s for s in turn.replies.values() if s.npc == performer and s.line == line), None)
            listeners = "全桌" if said is None or said.audience is None else "仅" + "、".join(st.find(i).name for i in said.audience)
            out.append(f"【NPC表演原话｜{listeners}已听到，不复述】{m.npc(performer).name}：{line}")
        for ev in turn.events:
            if ev.read_aloud.strip():  # the screen showed it word for word (the event notice): go on from it
                out.append(f"眼前发生（原文已展示给玩家，不要复述，接着写之后的变化）：{ev.read_aloud.strip()}")
        if names := self._unknown(turn.events):  # a horror seen before it is known, or anyone in the fight (Creature.called, NPC.called)
            out.append("【称呼】" + "；".join(names) + "。调查员还不知道这是什么：只写看得见、听得见、闻得到的样子，不说出名字和来历")
        arrangements = [st.pending_turn.get("arrangement", {}),
                        st.pending_turn.get("prepared_action", {}).get("arrangement", {})]
        cued_world = {text for arranged in arrangements for text in arranged.get("cue_turn", {}).get("world", [])}
        for arranged in arrangements:
            for choice in arranged.get("plan", {}).get("beats", []):
                if arranged.get("cues", {}).get("steps", {}).get(f"cue-notice:{choice['id']}", {}).get("done"):
                    out.append(f"【裁定前已展示，不复述】{choice.get('text', '')}")
        out += [(f"【裁定前已实际发生】{text}。接着改变后的现场讲，不以本次尚未完成的请求推翻。"
                 if text in cued_world else f"眼前发生：{text}") for text in turn.world]
        out += [f"备注：{n}" for n in turn.notes]
        out += [f"规则提示（来自本局规则，这一轮的叙述要用一句话自然写到）：{h}" for h in turn.hints]
        if turn.ending:
            end = m.ending(turn.ending)
            assert end is not None
            out.append(f"【结局：{end.title}】{end.text.strip()}\n请用 150–250 字为这个结局收尾，不再留行动钩子。")
        elif turn.interlude:
            out.append("请用 60–150 字接着上一段写这些事，像“就在这时”一样自然地插进来，收在一个能行动的细节上。")
        elif turn.fight:
            out.append(tell(turn.fight))
        else:
            out.append("请叙述。")
        return "\n".join(out)

    def _told_by(self, turn: Turn) -> dict[str, str]:
        """The clues of this turn an NPC's shown line told (Said.told): clue id -> that NPC."""
        return {cid: said.npc for said in turn.replies.values() for cid in said.told if cid in turn.clues}

    def _found(self, turn: Turn) -> list[str]:
        """What the investigators came to know this turn, for the keeper: each clue in its own words.
        The players see no clue card (the user's call, 2026-10-22: no summaries handed to them): what
        no NPC said in the line already shown, and no paper they now hold says, the telling has to put
        into the story."""
        told, m = self._told_by(turn), self.m
        out, untold = [], []
        for cid in turn.clues:
            c = m.clue(cid)
            line = f"获得线索〔{c.title}〕：{c.text.strip()}"
            if cid in told:
                line += f"（{m.npc(told[cid]).name}刚才已经亲口说出，不要复述）"
            elif c.handout and (c.handout in self.state.handouts or c.handout in presentation.papers(self.state, None)):
                line += f"（手稿〔{m.handout(c.handout).title}〕的原文在玩家手里：交代怎么拿到、看到它，不复述全文）"
            else:
                untold.append(cid)
            out.append(line)
        if untold:
            out.append(f"【讲出发现】玩家看不到线索卡，{''.join(f'〔{m.clue(c).title}〕' for c in untold)}的内容只能从你的叙述里得知，"
                       "这一轮就讲出来：写调查员实际看到、读到、听到或想到的东西，原文里的人名、地点、时间和细节照实写具体，用故事里的说法；"
                       "不概括成结论，不点明这是线索，不添原文没有的内容。")
        return out

    def _unknown(self, events: list[Event]) -> list[str]:
        """Who of those the turn's events show, and of those in the fight going on, the players call
        otherwise than the keeper does, with what they call them (encounter/profiles.shown_name: a
        creature not identified, a person not met, someone who is not what they know him as), the
        keeper's notes in brackets left out (Keeper Rulebook p. 176: even seen close, it is a dark wet
        shape, not a deep one)."""
        from .encounter import profiles, telling
        from .encounter import views as encounter_views
        from .encounter.actions import Ctx

        keeper: dict[str, str] = {}
        players: dict[str, str] = {}
        for x in dict.fromkeys(x for ev in events for x in (*ev.sights, *ev.fight, *ev.chase)):
            if (p := profiles.profile(self.world, x)) is not None:
                keeper[p.id], players[p.id] = profiles.bare(p.name), profiles.shown_name(self.world, x)
        if (enc := self.encounter()) is not None:  # those in the current scene's fight
            ctx = Ctx.of(self, enc)
            keeper |= encounter_views.names(ctx, "keeper")
            players |= encounter_views.names(ctx, "players")
        return telling.name_rows(keeper, players)

    def _narrative_audience(self, turn: Turn | None = None) -> list[str] | None:
        """Readers of this actual scene; callers save this before delivering or awaiting."""
        st = self.state
        actor = (turn.interval.get("actor") if turn and turn.interval else None) or st.investigator.id
        when = int(turn.interval["end"]) if turn and turn.interval else self.world.now
        place = self.world.place_at(actor, when)
        readers = self.world.observers(place, at=when) if place else []
        # The acting player still receives their own fate when unconscious or dying.
        return recipients.scope(st, [actor, *readers])

    async def _narrate(self, report: str, on_delta=None, lore: str = "", turn: Turn | None = None, *,
                       _receipt: dict | None = None, _owner: Turn | None = None, _tools: bool = True,
                       _settled_only: bool = False) -> str:
        """The keeper tells it: first how things stand as the telling ends, then the words
        (agents/cast.Telling). The scene is compared with the record and lands as world operations
        (world/scene.py); a structured telling is saved and lands before its words are delivered.
        Plain answers without a world-result schema may still stream. Nothing reads
        the words, and nothing in them is cut or replaced (the user's call, 2026-10-20). The whole
        telling goes into the turn's journal (pending_turn["keeper"]) before it lands: a turn resumed
        after that lands it from there and does not ask the keeper again."""
        self.told = None  # while the keeper speaks there is nothing to tell again
        st = self.state
        sky = (st.sky, st.sky_at)
        receipt: dict = (_receipt if _receipt is not None else
                         st.pending_turn.setdefault("keeper", {}) if st.pending_turn else {})
        if "delivery_id" not in receipt:
            receipt["delivery_id"] = (f"keeper:{receipt['memory_id']}" if "memory_id" in receipt else
                                      f"keeper:{st.turn}:{st.logged}")
            if self.checkpoint:
                self.checkpoint()
        delivery = receipt["delivery_id"]
        if "audience" not in receipt:
            receipt["audience"] = self._narrative_audience(turn)
        started = False

        async def start() -> None:
            nonlocal started
            if not started:
                await self.emit({"type": "keeper_start", "delivery": delivery, "audience": receipt["audience"]})
                started = True

        async def delta(text: str) -> None:
            await start()
            await self.emit({"type": "keeper_delta", "text": text, "delivery": delivery, "audience": receipt["audience"]})
            if on_delta:
                await on_delta(text)

        if "text" not in receipt:
            await self.emit({"type": "status", "text": "KP 正在构思…"})
            receipt.update(await self._tell(report, lore, delta, turn, receipt=receipt, tools=_tools,
                                            settled_only=_settled_only))
            if started:  # the plain path already delivered its complete streamed answer
                receipt.update(body_started=True, body_shown=True, body_forwarded=True)
            if self.checkpoint:
                self.checkpoint()
        text = receipt["text"]
        if not receipt.get("landed"):  # actual world operations and their receipt, without an await between
            units = [scene.Unit.load(u) for u in receipt.get("units", [])]
            landed = scene.land(self.world, units)
            if any(u.key.startswith("introduce:") for u in landed):
                self.visuals.schedule()
            if len(landed) < len(receipt.get("units", [])):
                self._debug("scene", {"phase": "stale", "units": receipt["units"], "landed": [u.key for u in landed]})
                receipt["rested"] = {}
                receipt["scene_complete"] = False
                trial = World(self.m.model_copy(deep=True), st.model_copy(deep=True))
                problems = [problem for unit in units if unit not in landed
                            for problem in (scene.run(trial, unit) or [f"场面操作 {unit.key} 未落实"])]
                receipt["stale"] = {"landed": [u.dump() for u in landed], "problems": problems,
                                    "standing": scene.standing(self.world)}
                receipt["story_flags"] = []
                receipt["room"] = next((u.room for u in landed if u.room), "")
            else:
                receipt["story_flags"] = []
                for flag in receipt.get("flags", []):
                    if flag in st.flags:
                        continue
                    done = self.world.apply(SetFlag(id=f"keeper:{st.turn}:flag:{flag}", flag=flag, actor=KEEPER))
                    if done.ok:
                        receipt["story_flags"].append(flag)
            if not receipt.get("stale"):
                if not _settled_only:
                    st.ongoing = receipt.get("now", "")
                if not receipt.get("body_started") and not _settled_only:
                    receipt["audience"] = self._narrative_audience(turn)
            receipt["landed"] = True
            if self.checkpoint:
                self.checkpoint()
        if receipt.get("stale"):
            # A saved draft whose operations no longer hold is not a delivered story.
            # Keep the actual partial effects, then let this keeper correct only the telling.
            came = None
            if receipt.get("room"):
                came = await self._walked(receipt["room"], receipt, defer=False)
            following = receipt.setdefault("following_keeper", {})
            following.setdefault("delivery_id", f"{delivery}:stale")
            corrected = (self._report(turn) if turn is not None else report)
            corrected += ("\n【未展示草稿的场面未全部落实】\n"
                          + json.dumps(receipt["stale"], ensure_ascii=False)
                          + "\n上一份正文只是未展示草稿，不是已经发生的事实；只以上述实际落账与当前记录为准。"
                          "已成立操作保留，未成立操作不能当作完成；不重做原行动、骰点或人物配音。"
                          "据此写这一段实际经过；尚待意图按真实结果保留，不执行或预演后续行动。")
            if came is not None:
                corrected += ("\n【本次实际走入地点后已经结算的结果】\n" + self._report(came)
                              + "\n到访通知与检定已按原收件人展示，保留实际后果，叙述自然承接，不重复已显示的原文或骰点。")
            return await self._narrate(corrected, on_delta, lore=lore, turn=turn, _receipt=following,
                                       _owner=_owner, _tools=False)
        if not receipt.get("perceptions_landed"):
            changes = receipt.get("perceptions", [])
            if problems := perception.check_changes(self, changes):
                self._debug("perception", {"phase": "rejected", "problems": problems})
                st.presentation_issues["perception"] = {"action": {"perceptions": changes}, "problem": "；".join(problems)}
            else:
                perception.apply(self, changes)
                if changes:
                    st.presentation_issues.pop("perception", None)
            receipt["perception_problems"] = problems
            receipt["perceptions_landed"] = True
            if self.checkpoint:
                self.checkpoint()
        if receipt.get("rested") and not receipt.get("rested_landed"):
            with self._journal(receipt.setdefault("rest_journal", {}), self._keep):
                await self._recover_madness(turn or Turn(), rested=receipt["rested"])
            receipt["rested_landed"] = True
            if self.checkpoint:
                self.checkpoint()
        if receipt.get("room"):
            await self._walked(receipt["room"], receipt)
        if not _settled_only:
            await self._people(at=int(turn.interval["end"]) if turn and turn.interval else None)
        entry = next((e for e in reversed(st.log) if e.kind == "keeper" and e.delivery == delivery), None)
        if entry is None:
            entry = st.add("keeper", text, who="守秘人", delivery=delivery, audience=receipt["audience"])
            if self.checkpoint:
                self.checkpoint()
        if not receipt.get("body_started"):
            await self.emit({"type": "keeper_start", "delivery": delivery, "audience": receipt["audience"]})
            receipt["body_started"] = True
            if self.checkpoint:
                self.checkpoint()
        if not receipt.get("body_shown"):
            await self.emit({"type": "keeper_delta", "text": text, "delivery": delivery, "audience": receipt["audience"]})
            receipt["body_shown"] = True
            if self.checkpoint:
                self.checkpoint()
        if on_delta and not receipt.get("body_forwarded"):
            await on_delta(text)
            receipt["body_forwarded"] = True
            if self.checkpoint:
                self.checkpoint()
        if not receipt.get("body_ended"):
            await self.emit({"type": "keeper_end", "text": text, "delivery": delivery, "audience": receipt["audience"]})
            receipt["body_ended"] = True
            if self.checkpoint:
                self.checkpoint()
        if not receipt.get("asides_landed"):
            # The accepted telling and its recipients are saved together before delivery. A
            # resumed turn never registers a private observation a second time.
            for aside in receipt.get("asides", []):
                st.add("keeper", aside["text"], who="守秘人", audience=aside["to"])
            receipt["asides_landed"] = True
            if self.checkpoint:
                self.checkpoint()
        asides_shown = receipt.setdefault("asides_shown", [])
        for index, aside in enumerate(receipt.get("asides", [])):
            if index in asides_shown:
                continue
            await self.emit({"type": "aside", "audience": aside["to"], "text": aside["text"], "who": "守秘人"})
            asides_shown.append(index)
            if self.checkpoint:
                self.checkpoint()
        media_key = receipt.setdefault("media_key", f"keeper:{st.turn}:{st.logged}")
        receipt["media_unshown"] = await self.present(receipt.get("media", []), key=media_key,
                                                     shown=receipt.setdefault("media_shown", []))
        if self.checkpoint:
            self.checkpoint()
        if receipt.get("offer_idea") and not receipt.get("idea_offered"):
            if st.turn > 0 and not st.ending and not self.battling() and idea.pick(self.m, st) is not None:
                st.idea_offer = st.turn
                await self.emit({"type": "idea_offer", "text": idea.OFFER})
            receipt["idea_offered"] = True
            if self.checkpoint:
                self.checkpoint()
        await self.showcases.named(text, audience=receipt["audience"])  # first named to these readers
        if not _settled_only:
            await self._sky(text)
        self.told = Told(receipt.get("report", report), lore + receipt.get("note", ""), entry, sky,
                         receipt.get("memory"))
        if turn is not None and receipt.get("story_flags"):
            await self._scene_story(receipt, turn, on_delta, owner=_owner or turn)
        return text

    async def _scene_story(self, receipt: dict, turn: Turn, on_delta=None, *, owner: Turn) -> None:
        """Consequences of the keeper's completed facts, after its telling. Keep the new stage
        with that telling: it must not lose an event performance, replay the original action,
        or roll again when the connection breaks. This is an event continuation, not a review."""
        extra = TURN_RECORD.validate_python(receipt["following"]) if "following" in receipt else Turn(interlude=True)

        def keep(following: Turn) -> None:
            receipt["following"] = TURN_RECORD.dump_python(following, mode="json")
            self._keep(owner)

        with self._journal(receipt.setdefault("story", {}), keep):
            await self._flags(receipt["story_flags"], extra, landed=True)
        await self._perform_events(extra, keep)
        with self._journal(receipt["story"], keep):
            await self._check_ending(extra)
        # Event notices show read_aloud, not the event's background or performance.
        # Let the normal keeper tell any new scene once, with its own saved receipt.
        if extra.events or extra.sanity or extra.notes or extra.world:
            await self._narrate(self._report(extra), on_delta, lore=self._brief(), turn=extra,
                                _receipt=receipt.setdefault("following_keeper", {}), _owner=owner)
        turn.ending = turn.ending or extra.ending
        keep(extra)

    async def _keeper_reply(self, report: str, lore: Callable[[], str], delta: Callable[[str], Awaitable[None]] | None,
                            turn: Turn | None, receipt: dict, *, enabled: bool = True, **kwargs: Any) -> Any:
        """The same keeper may request an NPC performance before its final telling.

        Requests and actual replies share the existing telling receipt. A retry settles any
        saved request first, then presents its result; it never replays the action or asks an
        already-recorded voice again. Only the final telling can carry a next player action.
        """
        keeper = self.cast.keeper
        if ((not enabled and not receipt.get("calls")) or turn is None
                or not self.state.pending_turn or not _takes(keeper, "voices")):
            told = await keeper(self.state, report, on_delta=delta, lore=lore(), **kwargs)
            receipt.update(report=report, memory=report)
            return told
        own_report = receipt.setdefault("own_report", report == self._report(turn))
        if "voice_turn" in receipt:
            restored = TURN_RECORD.validate_python(receipt["voice_turn"])
            turn.__dict__.update(restored.__dict__)

        def keep(acted: Turn) -> None:
            receipt["voice_turn"] = TURN_RECORD.dump_python(acted, mode="json")
            if self.checkpoint:
                self.checkpoint()

        calls = receipt.setdefault("calls", [])
        remembering = _takes(keeper, "remember")
        if remembering and "memory_id" not in receipt:
            journal = self.state.pending_turn
            serial = journal.get("keeper_memory_serial", 0)
            journal["keeper_memory_serial"] = serial + 1
            receipt["memory_id"] = f"{self.state.turn}:{serial}"
            keep(turn)

        def memory_at() -> int:
            """Recover the last actual exchange, including a save taken while its words were delivered."""
            cursor = receipt.get("memory_at", -1)
            if remembering:
                prefix = f"【主持记录 {receipt['memory_id']}｜配音回执至 "
                last = self.state.transcripts.get("keeper", [])[-2:]
                if len(last) == 2 and last[0]["role"] == "user" and last[0]["content"].startswith(prefix):
                    header = last[0]["content"].split("\n", 1)[0]
                    index = header.removeprefix(prefix).removesuffix("】")
                    if index.isdecimal():
                        cursor = max(cursor, int(index))
            return cursor

        while True:
            for index, row in enumerate(calls):
                if row.get("done"):
                    continue
                npc_id, instruction = row["npc"], row["instruction"]
                if npc_id not in self._callable(turn) or self.battling():
                    row.update(done=True, result={"call": index, "npc": npc_id,
                                                 "private": row.get("private", False), "audience": [], "told": [],
                                                 "problem": "人物不在当前现场，未执行配音或操作"})
                    keep(turn)
                    continue
                if "before" not in row:
                    row["before"] = {"seq": self.world.rec.seq, "notes": len(turn.notes), "world": len(turn.world)}
                    keep(turn)
                private = row.get("private", False)
                action = turn.action
                addressed = self._addressed(action) == npc_id
                remote = addressed and action.communication == "remote"
                private = private or remote
                record = (partial(self._exchanged, npc_id, action, key=f"keeper:{index}", private=private) if addressed else
                          partial(self._initiated, npc_id, key=f"keeper:{index}", private=private))
                message = (f"（系统：{self.state.clock}，{self.m.location(self.state.location).name}。\n"
                           f"【本次可感知情境与演出范围】{instruction}\n"
                           "按你自己的知识、动机、口吻和实际持有物回应；这份安排不使你获知未亲历的玩家意图或他人秘密。"
                           "已结算部分照实兑现，尚未决定的态度、隐瞒、提议和真实打算仍由你结合处境表达。"
                           + self._npc_disclosure(turn, npc_id) + "）")
                if remote:
                    message = self._npc_message(turn, npc_id)
                said = await self._reply(
                    turn, f"keeper:{index}:{npc_id}", npc_id,
                    lambda npc_id=npc_id, message=message, private=private, remote=remote:
                        self.cast.npc(self.state, npc_id, message, **({"private": True} if private else {}),
                                      **({"communication": "remote"} if remote else {})),
                    keep, record=record, joined=not addressed, audience=[self.state.investigator.id] if private else None,
                    status=f"等待 {self.m.npc(npc_id).name} 回应…", communication="remote" if remote else "local")
                with self._journal(row.setdefault("settling", {}), keep):
                    await self._perform_events(turn, keep)
                    await self._resolution_step("world", turn, lambda: self._world(turn))
                    await self._resolution_step("people", turn, self._people)
                    await self._resolution_step("ending", turn, lambda: self._check_ending(turn))
                before = row["before"]
                dialogue = (f"talk:{self.state.turn}:{self.state.investigator.id}:{npc_id}:keeper:{index}" if addressed else
                            f"talk:{self.state.turn}:{npc_id}:keeper:{index}")
                effects = [{"id": entry.id, "kind": entry.kind, "text": entry.summary} for entry in self.world.rec.journal
                           if entry.seq > before["seq"] and entry.id != dialogue and not entry.undone]
                row.update(done=True, result={"call": index, "npc": npc_id, "line": said.line, "told": said.told,
                                             "private": private, "audience": said.audience, "effects": effects,
                                             "notes": turn.notes[before["notes"]:], "world": turn.world[before["world"]:]})
                keep(turn)
            voices = ([] if not enabled or self.battling() or self.fought_this_turn() else
                      self._callable(turn))
            extra = ""
            if intents := self._voice_intents(turn):
                extra += ("\n【骰前考虑的参与意图，尚未发生】\n" + json.dumps(intents, ensure_ascii=False)
                          + "\n按最终主检定、已发生的对白和实时记录重新决定是否参与及怎样参与，旧意图不构成已发现或已同意。")
            actual = ""
            current_report = report
            if calls:
                actual = "\n【配音实际执行回执】\n" + json.dumps([row["result"] for row in calls], ensure_ascii=False)
                actual += "\n台词已向实际听见者展示，不复述；未落实不当作成功，不替玩家接受新条件。"
                # Render the current action once, not one growing copy per voice. A held
                # round also retains its other investigators' original reports.
                current_report = self._report(turn, omit_replies={f"keeper:{i}:{row['npc']}" for i, row in enumerate(calls)})
                if not own_report:
                    current_report = report + "\n【本次行动更新】\n" + current_report
            # Tool observations belong to the keeper's conversation, not just an ephemeral
            # lore block: otherwise its next turn remembers a request without its result.
            prompt = current_report + actual
            kept_at = memory_at()
            remember = prompt
            if remembering:
                remember = f"【主持记录 {receipt['memory_id']}｜配音回执至 {len(calls)}】\n"
                if kept_at < 0:
                    remember += report
                fresh = [row["result"] for row in calls[max(0, kept_at):]]
                if fresh:
                    remember += "\n【新增实际配音回执】\n" + json.dumps(fresh, ensure_ascii=False)
                elif kept_at >= 0:
                    remember += "原行动与配音均按已记结果；本次只修正或收束主持输出，不重新执行。"
            # A tool-capable reply can contain world results or private material. Keep
            # its final body buffered until the caller has saved and landed that reply.
            told = await keeper(self.state, prompt, on_delta=None if voices or calls else delta,
                                lore=lore() + extra,
                                **({"voices": voices} if voices else {}),
                                **({"remember": remember} if remembering else {}), **kwargs)
            if error := getattr(told, "call_error", ""):
                raise PreparationError(error)
            receipt.update(report=prompt, memory=remember)
            # A rejected scene is removed from the conversation. Do not acknowledge its
            # observations until an actual exchange retains them, including after a retry.
            receipt["memory_at"] = memory_at()
            if (call := getattr(told, "call", None)) is None:
                return told
            calls.append(call.model_dump(mode="json"))
            keep(turn)  # a stable request exists before a voice, world operation or notification runs

    async def _tell(self, report: str, lore: str, delta: Callable[[str], Awaitable[None]] | None, turn: Turn | None,
                    *, receipt: dict | None = None, tools: bool = True, settled_only: bool = False) -> dict:
        """The keeper's telling and what of its scene lands, as the turn's journal keeps it. A scene
        that does not hold goes back once, with what did not hold and what could be written
        instead; failing again, the units that hold make up the scene, and the keeper writes the
        words to it, what did not hold as attempts. A keeper that answers in words alone (a test's,
        an old one's), or a turn told out of the story (a question to the KP, the ending) has no scene."""
        st = self.state
        keeper = self.cast.keeper
        receipt = {} if receipt is None else receipt
        initial_world = self._brief()
        initial_scene = ""

        async def ask(*, lore: str, voices: bool = True, **kwargs: Any) -> Any:
            uses_scene = bool(initial_scene and initial_scene in lore)
            fixed = lore.replace(initial_world, "", 1) if initial_world else lore
            if uses_scene:
                fixed = fixed.replace(initial_scene, "", 1)

            def current_lore() -> str:
                # The next model step must see the voice's actual effects, not the scene
                # and item list from before it acted. No historical facts are truncated.
                current = fixed + self._brief()
                if uses_scene:
                    current += self._scene_brief(self._scene_context(turn)) + "\n【个人知觉】\n" + perception.brief(st)
                return current

            if settled_only and _takes(keeper, "settled"):
                kwargs["settled"] = True
            return await self._keeper_reply(report, current_lore, delta, None if settled_only else turn, receipt,
                                            enabled=not settled_only and tools and voices and turn is not None and not (
                                                turn.fight or (turn.action is not None and turn.action.kind == "meta")),
                                            **kwargs)

        words = {"units": [], "flags": [], "room": "", "now": "", "note": "", "offer_idea": False, "asides": [],
                 "media": [], "perceptions": [], "scene_complete": False}
        continuing = bool(st.pending_turn.get("request", {}).get("remaining"))
        structured = not settled_only and (_staged(turn) or continuing) and _takes(keeper, "check")
        if not structured:
            told = await ask(lore=lore)
            if isinstance(told, str):
                return words | {"text": told}
            if settled_only:
                return words | {"text": told.text}
            # An ending cannot commit a new scene, but its addressed observations and
            # prepared media still belong to their readers. Do not reduce them to prose.
            return words | {"text": told.text, "offer_idea": told.offer_idea, "asides": told.asides,
                            "media": told.media, "perceptions": told.perceptions}
        ctx = self._scene_context(turn)
        brief = self._scene_brief(ctx) + "\n【个人知觉】\n" + perception.brief(st)
        initial_scene = brief
        delta = None  # structured final bodies are delivered only after their receipt lands
        plans: list[scene.Plan] = []

        def check(raw: Any) -> bool:
            plans.append(scene.plan(self.world, raw, self._scene_context(turn)))
            return not plans[-1].problems

        fix = ""
        p = scene.plan(self.world, {}, ctx)  # nothing changes: what a telling that never gave a scene is held to
        for attempt in (1, 2):
            asked = len(plans)
            told = await ask(lore=lore + brief, check=check, fix=fix)
            if isinstance(told, str):
                return words | {"text": told}
            control = {"continuation": (told.continuation.model_dump(mode="json")
                                         if getattr(told, "continuation", None) is not None else None),
                       "continuation_error": getattr(told, "continuation_error", "")}
            if told.scene is None or len(plans) == asked:  # no scene could be read
                if told.shown:  # already before the players: its words stand, the record as it was
                    return words | {"text": told.text, "offer_idea": told.offer_idea, "asides": told.asides,
                                    "media": told.media, "perceptions": told.perceptions} | control
                # Its words would say what the record never got ("the warden goes with you", nobody
                # following): asked again for the scene, and the second time held to the record as it is.
                self._debug("scene", {"phase": "missing", "attempt": attempt})
                p, fix = scene.plan(self.world, {}, ctx), scene.MISSING
                continue
            p = plans[-1]
            if not p.problems or told.shown:
                if p.problems:  # shown on a scene that held, then came back otherwise: what holds of it lands
                    self._debug("scene", {"phase": "kept", "problems": p.problems, "scene": told.scene})
                return self._staging(told.text, p, partial=bool(p.problems)) | {
                    "offer_idea": told.offer_idea, "asides": told.asides,
                    "media": told.media, "perceptions": told.perceptions} | control
            self._debug("scene", {"phase": "rejected", "attempt": attempt, "problems": p.problems, "scene": told.scene})
            fix = scene.feedback(p)
        self._debug("scene", {"phase": "settled", "held": [u.key for u in p.held], "problems": p.problems})
        settled = await ask(lore=lore + scene.settled(p), voices=False)
        text = settled if isinstance(settled, str) else settled.text
        return self._staging(text, p, partial=True)

    @staticmethod
    def _staging(text: str, p: scene.Plan, *, partial: bool) -> dict:
        """What the turn's journal keeps of a telling: its words, the units of its scene that land,
        where the investigators walked, what is going on as it ends (not when the scene was cut
        down: it may tell of what did not land), and the scene in words for telling it again."""
        return {"text": text, "units": [u.dump() for u in p.held], "flags": [] if partial else p.flags,
                "room": p.room, "now": "" if partial else p.now,
                "note": scene.kept(p), "rested": {} if partial else p.rested, "scene_complete": not partial}

    def _scene_context(self, turn: Turn | None) -> scene.Context:
        """What the keeper's scene is checked with: this turn's minutes so far (from the player's
        words, as the log has them), what the action lacks, whether the investigators acted, and
        the op ids not yet used this turn. Rest candidates come from executed individual receipts,
        including the completed actions held for this telling, never its prose or shared elapsed time."""
        st, rec = self.state, self.world.rec
        acted = turn is not None and turn.action is not None and not turn.interlude
        said = next((e for e in reversed(st.log) if e.kind == "player" and e.turn == st.turn), None)
        try:
            start = parse_time(said.time) if said is not None else st.minutes
        except ValueError:
            start = st.minutes
        request = st.pending_turn.get("request", {})
        if request.get("actor") == st.investigator.id:
            start = request.get("started", start)
        ids = f"keeper:{st.turn}"
        used = [int(n) for e in rec.journal if e.id.startswith(ids + ":") and (n := e.id.rsplit(":", 1)[1]).isdigit()]
        ruling = turn.ruling if turn is not None else None
        lacks = (getattr(ruling, "missing", None) or []) if ruling is not None else []
        rests = {}
        if acted:
            for receipt in [*(h.get("rest") for h in st.held), turn.rest]:
                if (receipt and not receipt["interrupted"] and not self.battling()
                        and receipt["start"] < receipt["end"] <= st.minutes
                        and any(i.id == receipt["who"] for i in st.investigators)):
                    rests[receipt["who"]] = (receipt["start"], receipt["end"])
        interval = ((turn.interval["start"], turn.interval["end"]) if acted and turn.interval else None)
        elapsed = interval[1] - interval[0] if interval else max(0, st.minutes - start)
        participants = self._travellers(turn.action.alone) if acted else []
        contacts = [said.npc for said in turn.replies.values()
                    if said.communication == "remote" and said.line] if turn is not None else []
        return scene.Context(travel=self._travel, elapsed=elapsed if acted else 0,
                             missing=[_lack(x) for x in lacks], walk=acted, ids=ids, start=max(used, default=-1) + 1,
                             rests=rests, interval=interval, participants=participants, contacts=contacts)

    def _scene_brief(self, ctx: scene.Context) -> str:
        """The scene as the record has it, for the keeper's to start from: where they are, who is
        with them and who walks with them (ids to write it with), and the minutes anyone could walk."""
        w, st = self.world, self.state
        party = {i.id for i in st.investigators}
        here = "、".join(f"{n}（{w.name(n)}{'，跟着调查员' if w.rec.entities[n].following in party else ''}）"
                        for n in w.present(st.location, at=ctx.interval[1] if ctx.interval else w.now)) or "没有别人"
        # What the action lacks is said once, in the report's notes (Game._lacking); the scene's check holds it too.
        flags = "；".join(f"{flag}：{meaning}" for flag, meaning in self.m.flags.items() if flag not in st.flags)
        items = world_view.item_refs(w, {st.location, st.investigator.id, *w.present(st.location)})
        madness = ""
        if any({"临时疯狂", "不定性疯狂"}.intersection(i.conditions) for i in st.investigators):
            madness = "临时疯狂是持续数小时的潜在状态，不能当作整个期间都在即时发作；不定性疯狂不因过夜自动治愈。"
            if ctx.resting:
                madness += "scene.rested 只写实际在安全处良好过夜、已经完成休息的调查员；想睡、等待、守夜、途中惊醒或紧张防袭不能算。"
        rest_scope = ""
        if ctx.rests:
            periods = "；".join(f"{who}（{format_time(start)}至{format_time(end)}，本人已执行{end - start}分钟休息）"
                               for who, (start, end) in ctx.rests.items())
            rest_scope = (f"本段可确认休息的调查员：{periods}。scene.rested只能从这些人中确认实际安全、充分休息者；"
                          "同地点、一起等待或全局过了一夜，不代表其他调查员也睡过；个人时段不因并行增量为0而消失。")
        return (f"\n\n【场面】先写 scene，是这段讲完时的样子，没变的照抄。现在：调查员在 {st.location}（{w.name(st.location)}），"
                f"在场 {here}。这一轮过了 {ctx.elapsed} 分钟，有人来去，路上的时间要在这里面。"
                + (f"本段实际远程交谈的联系人：{'、'.join(ctx.contacts)}；他们不在here，只可用communication=remote记实际听见的交谈。"
                   if ctx.contacts else "") +
                "本段真正做成的剧情事实写进 scene.flags，不等下一轮裁定员补记；未完成的决定和谎言不算事实。"
                + madness + rest_scope +
                "物件的拿起、交出、归还或放回地点都写进 holds；已有 item 用下表实际 id，不能用合称另造物件。"
                "新日常物件先在 things 写拿出者，交出或留下时再以同名写 holds。"
                f"放在本处的桌上、抽屉或门边时 holder 填 {st.location}，细部位置在事实或痕迹中交代；"
                "不靠 equipment.status 描述归还，也不把交给别人写成数量归零。"
                + (f"\n【本场面物件引用，仅供操作，不代表玩家已发现】\n{items}" if items else "")
                + (f"\n【尚未成立的剧情标记】{flags}" if flags else ""))

    async def _walked(self, room: str, receipt: dict, *, defer: bool = True) -> Turn:
        """The investigators walked to another room of the place in the telling (world/scene.py
        moved them already): what coming there brings (its events, its first sight, its
        foreshadowing) happens now, and is told next."""
        journal = receipt.setdefault("walk_journal", {})
        came = TURN_RECORD.validate_python(journal["turn"]) if "turn" in journal else Turn()

        def keep(acted: Turn) -> None:
            journal["turn"] = TURN_RECORD.dump_python(acted, mode="json")
            if self.checkpoint:
                self.checkpoint()

        if not receipt.get("walked"):
            with self._journal(journal, keep):
                await self._arrive(room, came)
            receipt["walked"] = True
            keep(came)
        if not defer:  # the current correction tells this receipt's visit, not a later action
            return came
        if receipt["delivery_id"] in self._walked_receipts:
            return came
        late = self._late
        for attr in ("events", "sanity", "hints", "world"):
            getattr(late, attr).extend(getattr(came, attr))
        late.notes += [n for n in came.notes if "一同抵达" not in n]
        if came.first_visit:
            loc = self.m.location(room)
            late.notes.append(f"上一段叙述里调查员已经走进{loc.name}，接着写，不必再写进门；这里初见的样子：{loc.read_aloud.strip()}")
        self._walked_receipts.add(receipt["delivery_id"])
        return came

    async def _sky(self, narration: str) -> None:
        """The keeper's words set the weather; unmentioned for a few hours, it passes."""
        st = self.state
        current = st.sky if st.minutes - st.sky_at < weather.LASTS else ""
        sky, mentioned = weather.sky(narration, current)
        if mentioned:
            st.sky_at = st.minutes
        if sky != st.sky:
            st.sky = sky
            await self.emit({"type": "sky", "sky": sky})

    # -- telling it again -------------------------------------------------------------------
    def retell_blocked(self) -> str:
        """Why the latest narration cannot be told again right now ("" when it can). Only one
        nothing has followed yet: once someone acts, or the keeper speaks on, the story has
        moved past it. Dice-bot rolls (.r, .en) stand outside the story and do not count."""
        told, log = self.told, self.state.log
        if told is None:
            return "暂无可重述内容：当前叙述尚未结束，或读档后尚未生成新的叙述。"
        at = next((i for i in range(len(log) - 1, -1, -1) if log[i] is told.entry), None)
        if at is None or any(e.kind != "dice" for e in log[at + 1 :]):
            return "后续行动已开始，上一段叙述不可重述。"
        return ""

    async def retell(self, nudge: str = "换个讲法", on_delta=None) -> str:
        """The latest narration told again in other words. The keeper gets the same report and
        the same lore, so the dice, the clues, the NPCs' lines and the events stay as they were:
        nothing is resolved or rolled again. The new telling takes the old one's place in the log
        and in the keeper's memory. `nudge` is one of RETELL, never the player's own words: the
        keeper knows the module's secrets."""
        why = self.retell_blocked() or ("" if nudge in RETELL else f"不认识的讲法：{nudge}")
        if why:
            raise ValueError(why)
        st, told = self.state, self.told
        assert told is not None
        self.told = None  # one telling at a time
        scope = {"audience": told.entry.audience, "delivery": told.entry.delivery}
        await self.emit({"type": "keeper_start", "again": True, **scope})

        async def delta(text: str) -> None:
            await self.emit({"type": "keeper_delta", "text": text, **scope})
            if on_delta:
                await on_delta(text)

        old = told.entry.text
        try:
            remembered = {"remember": told.memory} if _takes(self.cast.retell, "remember") else {}
            text = await self.cast.retell(st, told.report, old, extra=told.lore + _retold(old, nudge), on_delta=delta,
                                          **remembered)
        except Exception:
            self.told = told
            await self.emit({"type": "keeper_end", "text": old, **scope})  # the old telling stands
            raise
        told.entry.text = text
        if st.prologue == old:  # the opening told again
            st.prologue = text
        await self.emit({"type": "keeper_end", "text": text, **scope})
        shown = st.sky  # on the screens now, set by the old telling
        st.sky, st.sky_at = told.sky
        await self._sky(text)
        if st.sky == told.sky[0] != shown:  # the new words leave the sky as it was before the old ones
            await self.emit({"type": "sky", "sky": st.sky})
        st.heard = fog.heard(self.m, st)
        self.told = told
        return text


# The ways a player may ask for the latest narration again (Game.retell). Fixed on purpose: the
# keeper knows the module's secrets, and a wish typed in freely is a way to talk them out of it.
RETELL = {
    "换个讲法": "",
    "短一点": "篇幅砍到上一版的一半左右，只留最要紧的动作和细节。",
    "更克制": "写得更克制：少用形容词和比喻，不渲染情绪，让细节自己说话。",
    "多写细节": "多写细节：调查员看到、听到、闻到、摸到的具体东西，可以比上一版长一些。",
    "更阴森": "写得更阴森：压低光线和声音，让不对劲的地方一点点显出来。",
}


def _retold(old: str, nudge: str) -> str:
    """What rides along with a report told again, for that call only: every fact stays, the
    words change; the old telling, so the new one does not echo it; the player's choice."""
    ask = RETELL[nudge]
    return (
        "\n\n（系统：玩家想听这一段换个讲法。报告里的事一件不改：检定结果、线索原文、NPC 台词和事件都照旧，"
        "不添新的事实，也不要提“重讲”。上一版是这样写的，这次换个写法，别沿用它的句子：\n"
        f"「{old}」" + (f"\n这次{ask}" if ask else "") + "）"
    )


BACKSTAGE_LIMIT = 2  # plans being written at once
# Optional future preparations do not block play. Current scene dependencies always wait.
PLAY_WAIT = 0.0
# How long a player may wait for the scene their action needs (the user's call, 2026-10-20: up to
# five minutes is fine). Each invalid draft gets one repair. Stale reads rebase the package;
# service recovery waits PREPARE_PAUSE seconds times failures so far before retrying. A first
# picture gets PREPARE_DRAWS tries before the scene goes on without it.
PREPARE_LIMIT = 300.0
PREPARE_DRAWS = 2
PREPARE_PAUSE = 5.0
NOWHERE = (
    "这一轮人还在原地：调查员说要去的地方，没人告诉过他们在哪，从这儿也找不到路（或者没说清去哪）。"
    "不要写他们到了别处。要是当地人人都知道的地方，可以写有人给他们指了路、说出地名，下一轮就能去；"
    "要是他们还没发现的地方，别说出、确认或暗示它的任何事"
)
STALL_TURNS = 12  # turns without recorded progress before an internal pacing signal, never an automatic player hint
PUSH_HOW = 200  # characters of a push's stated way that are kept
ACT_CAP = 180  # minutes one action may take
WAIT_CAP = 720  # minutes one wait or rest may skip: a night's sleep




class _Minds:
    """The NPC model deciding for important NPCs backstage, with a word on the screens while it
    thinks, unless no one is waiting for it (Game.world_later)."""

    def __init__(self, game: Game, *, quiet: bool = False):
        self.game, self.quiet = game, quiet

    async def decide(self, ask):
        if not self.quiet:
            await self.game.emit({"type": "status", "text": "KP 正在构思…"})
        assert self.game.brain is not None
        return await self.game.brain.decide(ask)


def _staged(turn: Turn | None) -> bool:
    """A telling with a scene: the opening, a turn, an interlude; not an answer to a question put to
    the KP, nor the ending."""
    return turn is None or not (turn.ending or turn.fight or (turn.action is not None and turn.action.kind == "meta"))


def _takes(fn: Any, name: str) -> bool:
    """Whether a callable names this keyword: the keeper's scene goes only to a keeper that asks for
    it (a wrapper passing **kwargs on to one that does not would fail)."""
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _lack(gap: Any) -> str:
    """One thing an action lacks (Ruling.missing), as its name."""
    if isinstance(gap, str):
        return gap
    return str(getattr(gap, "name", "") or getattr(gap, "item", "") or getattr(gap, "text", "") or gap)


def _span(minutes: int) -> str:
    """A stretch of game time in words: 1 天, 约 3 小时, 40 分钟."""
    if minutes >= 1440 and minutes % 1440 == 0:
        return f"{minutes // 1440} 天"
    return f"约 {round(minutes / 60)} 小时" if minutes >= 60 else f"{max(0, minutes)} 分钟"


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _threshold(target: int, difficulty: str) -> int:
    return {"困难": target // 2, "极难": target // 5}.get(difficulty, target)


def push_choice(value: Any) -> tuple[str, str]:
    """A push offer's answer as (mode, how): "luck" or "drop", or {"choice": "push", "how": ...} (Keeper
    Rulebook p. 73): a push always says how it is made."""
    if isinstance(value, dict):
        mode, how = str(value.get("choice", "")), " ".join(str(value.get("how") or "").split())[:PUSH_HOW]
    else:
        mode, how = str(value), ""
    if mode not in ("push", "luck", "drop"):
        raise ValueError("请选择孤注一掷、花费幸运或放弃")
    if mode == "push" and not how:
        raise ValueError("孤注一掷要写明怎样做")
    return mode, how if mode == "push" else ""


def ai_choice(offer: dict, how: str = "") -> str | dict:
    """How an AI investigator answers a push offer: the push it said it would make with its action
    (agents/cast.Proposal.push, Keeper Rulebook p. 73), reviewed like a player's, when it said one
    and a push is allowed; otherwise luck when that is offered, else the failure stands."""
    how = " ".join(how.split())
    if how and offer.get("can_push"):
        return {"choice": "push", "how": how}
    return "luck" if offer.get("luck") is not None else "drop"


def _refused(text: str) -> commands.CommandResult:
    return commands.CommandResult("refused", text)


def _aim(action: Action) -> str:
    """Who or what an action is aimed at, as a check record keeps it (CheckRecord.aim)."""
    return action.npc or action.target.strip()


def _check_view(c: Check) -> dict[str, Any]:
    # Every candidate shares the units die; bonus or penalty dice add tens dice, and the
    # value is the candidate that counted (BCDice picks it). 100 is tens 00 with units 0.
    rolled = c.roll.candidates or [c.value]
    return {
        "skill": c.name,
        "target": c.target,
        "hard": c.target // 2,
        "extreme": c.target // 5,
        "difficulty": {Level.REGULAR: "常规", Level.HARD: "困难", Level.EXTREME: "极难"}.get(c.difficulty, "常规"),
        "bonus": c.bonus,
        "value": c.value,
        "candidates": c.roll.candidates,
        "tens": [(v // 10) % 10 * 10 for v in rolled],
        "units": rolled[0] % 10,
        "kept": rolled.index(c.value) if c.value in rolled else 0,
        "level": c.level.zh,
        "passed": c.passed,
        "pushed": c.pushed,
        "text": c.describe(),
        "throw": c.roll.throw,  # the physical dice's recording: every screen replays this very throw
    }


def _sanity_view(s: Sanity) -> dict[str, Any]:
    return {
        "text": s.describe(),
        "loss": s.loss,
        "before": s.before,
        "after": s.after,
        "temporary": s.temporary,
        "indefinite": s.indefinite,
        "permanent": s.permanent,
        "bout": s.bout,
        "duration_hours": s.duration_hours,
        "check": _check_view(s.check),
    }
