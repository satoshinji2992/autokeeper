"""One game: deterministic resolution around the agents.

The engine owns the facts: dice (BCDice), which clues are found, time, events,
sanity and endings. Agents interpret intentions, propose actions and narrate results.
The engine validates and commits world operations before their outcomes enter the story.
"""

from __future__ import annotations

import asyncio
import heapq
import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from typing import Any

from pydantic import TypeAdapter

from .. import weather
from ..agents import guard
from ..agents.cast import Action, Cast, Failure, Ruling
from ..agents.director import Improv
from ..harness.replay import CassetteError
from ..module import improv
from ..module.schema import Event, Item, Module
from ..plugins.engine import Manager as PluginManager
from ..rules import commands
from ..rules.bcdice import Level, Roll
from ..rules.catalog import canonical
from ..rules.coc import DIFFICULTY_ZH, Check, Investigator, Rules, Sanity
from ..world import adapters, desk, knowledge, rewrite, scene, scheduler, stage
from ..world import context as world_view
from ..world.agents import CastBrain, marked
from ..world.ops import Equip, Follow, Improvise, Move, Occur, Rewrite
from ..world.record import DIRECTOR
from ..world.store import World, digest
from . import diagnostics, fog, progress, views, world
from .showcase import IMPROV_MAX as IMPROV_SHOWCASES
from .showcase import Showcases
from .state import (
    KEEP_CHECKS,
    RECHECK_AFTER,
    CheckRecord,
    GameState,
    LogEntry,
    Progress,
    parse_time,
    time_of_day,
)
from .visuals import Visuals

Emit = Callable[[dict[str, Any]], Awaitable[None]]


async def _silent(_: dict[str, Any]) -> None:
    return None


@dataclass
class Turn:
    action: Action | None = None
    ruling: Ruling | None = None
    checks: list[Check] = field(default_factory=list)
    clues: list[str] = field(default_factory=list)
    sanity: list[tuple[str, Sanity]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    arrived: str | None = None
    first_visit: bool = False
    route: list[str] = field(default_factory=list)
    npc_lines: list[tuple[str, str]] = field(default_factory=list)
    performance: tuple[str, str] | None = None
    performed: list[str] = field(default_factory=list)
    push: dict | None = None
    earlier: CheckRecord | None = None  # a repeated attempt: this earlier check stands, nothing is rolled
    notes: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)  # a rule plugin's one line the keeper must weave in
    world: list[str] = field(default_factory=list)  # what the rest of the world did in front of the party (world/)
    ending: str | None = None
    interlude: bool = False  # no action of theirs: the director's scene comes in between turns

    def absorb(self, later: Turn) -> None:
        """What a later stage of the same turn settled (the push choice): its rolls, finds, events,
        lines and notes join this turn's, so the NPCs on stage and the keeper's report have them."""
        for name in ("checks", "sanity", "npc_lines", "notes", "hints", "world"):
            getattr(self, name).extend(getattr(later, name))
        self.clues += [c for c in later.clues if c not in self.clues]
        self.events += [e for e in later.events if all(e.id != x.id for x in self.events)]
        self.performed += [e for e in later.performed if e not in self.performed]
        self.performance = later.performance or self.performance
        if later.arrived:  # an event carried them off
            self.arrived, self.first_visit, self.route = later.arrived, later.first_visit, later.route
        self.ending = self.ending or later.ending


TURN_RECORD = TypeAdapter(Turn)


@dataclass
class Backstage:
    """A director's plan still being written after its turn moved on."""

    kind: str
    task: asyncio.Task
    at: str  # where the investigators were when it was asked for
    turn: int = 0


class PreparationError(RuntimeError):
    """A scene could not be prepared this time: no plan passed its checks, or its first picture
    did not come. Game._ready tries again before any roll or time settles."""


@dataclass
class Told:
    """The latest narration, kept so it can be told again: what the keeper was given (the
    report and that turn's lore), and what telling it left behind (its log entry, and the
    sky as it was before, since the words set the weather)."""

    report: str
    lore: str
    entry: LogEntry
    sky: tuple[str, int]


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


class Game:
    def __init__(self, module: Module, state: GameState, cast: Cast, rules: Rules, emit: Emit | None = None):
        self.m, self.state, self.cast, self.rules = module, state, cast, rules
        self.emit: Emit = emit or _silent
        self.checkpoint: Callable[[], None] | None = None
        self.turn_checkpoint: Callable[[Turn], None] | None = None
        self._resolution: dict | None = None
        self._resolution_scope: list[str] = []
        self._resolution_lock = asyncio.Lock()
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
        self.debug_world: list[scheduler.Report] = []  # recent scheduling decisions, including refused operations
        self.told: Told | None = None  # the latest narration, while it can still be told again (retell)
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
        """What the opening needs before the keeper speaks: the director's adaptation of the
        module to these investigators, with its first pictures. A screen runs it while the
        prelude plays (module/prelude.py), so no one waits on a silent game screen; start() then
        finds it done (its receipt in pending_turn)."""
        st = self.state
        if not st.pending_turn:
            st.pending_turn.update(input="/opening", opening=True)
            if self.checkpoint:
                self.checkpoint()
        # Opening adaptation must exist before the keeper introduces its people and premise.
        adapting = st.pending_turn.get("scene_package", {}).get("kind") == "开局改编"
        return await self._improvise("开局改编", required=True) if not st.improv or adapting else None

    async def start(self, on_delta=None) -> str:
        st, m = self.state, self.m
        if not st.pending_turn:
            st.pending_turn.update(input="/opening", opening=True)
        self.visuals.pause()
        if self.checkpoint:
            self.checkpoint()
        adapted = await self.prepare_opening()
        opening = Turn()
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
            report += "【开场时发生的事】\n" + "\n".join(happened) + "\n"
        report += (
            "请用 180–320 字、两到四个自然段引入故事（有玩家篇幅偏好时以偏好为准）："
            "先用来信、旅途、职业或已写的关系自然交代为何而来，再让调查员看见眼前的生活和地方。"
            "不要把钩子念成任务简报，不要一开口就列调查方向或制造恐怖。"
            "原作开场中已写明的人数、物件数量与必发事实必须保留；只有一位调查员时称‘你’，不要凭空加队友。"
            f"叙事停在当前地点“{loc.name}”能接话或行动的一刻，之后由玩家决定去哪、是否停留。"
            "到达别处、见到委托人、进屋或开始调查，若尚未发生就不能替玩家完成；也不能擅自编造调查员的经历与心情。"
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
    async def parse(self, text: str) -> Action:
        if text.startswith("/kp "):
            question = text[4:].strip()
            self.state.add("system", f"对守秘人：{question}", who=self.state.investigator.name)
            await self.emit({"type": "status", "text": "守秘人正在回应…"})
            return Action(kind="meta", summary=question)
        self.visuals.schedule()  # resumes unfinished illustration work after loading, including MCP play
        self.showcases.settle()  # someone acts: the opening's big picture, if still on its way, is not shown now
        self.showcases.prepare()
        st = self.state
        await self._world_settled()  # what the rest of the world did since the last turn, before this one
        await self._curtain(Action(kind="other", summary=text))
        st.turn += 1
        st.add("player", text, who=st.investigator.name)
        await self.emit({"type": "status", "text": "解析行动…"})
        action = await self.cast.intent(st, text)
        # What a dice command asks for is read off the command, never taken from the model.
        action = action.model_copy(update={"asked": commands.asked(text), "echo": ""})
        if action.kind != "meta" and (action.kind == "move" or action.destination):
            # A named destination in an actual movement request outranks an intermediate
            # exit chosen by the parser. Only match names the party can already use.
            names = fog.aliases(self.m)
            routing = text.replace(action.speech, "") if action.speech else text
            routing = fog.plain(re.sub(r'「[^」]*」|“[^”]*”|"[^"]*"', "", routing))
            mentioned = []
            for pid in fog.bearings(self.m, st):
                for name in names.get(pid, ()):
                    for match in re.finditer(r"(?:前往|赶往|回到|去|回|到)\s*" + re.escape(name), routing):
                        before = routing[max(0, match.start() - 4):match.start()]
                        if (not re.search(r"(?:不|别|不要|不想|没有|没打算)$", before)
                                and not re.match(r"之前|前先|前[，,]", routing[match.end():])):
                            mentioned.append((match.end(), pid))
            # The parser's own pick stands when the player named it ("先去书房再去客厅" goes to the
            # study first); otherwise the first place named, not a later stop of the plan.
            if mentioned and action.destination not in {pid for _, pid in mentioned}:
                action = action.model_copy(update={"destination": min(mentioned)[1]})
            if action.destination and not re.search(r"沿途|沿路|边走边|逛|慢慢|随便走走", routing):
                action = action.model_copy(update={"travel": "direct"})
        if (action.kind == "daily" and action.npc and not action.approach and not action.destination
                and re.fullmatch(r"[，。！？、\s]*(早上好|早安|你好|您好|午安|晚安|谢谢|多谢|辛苦了|今天忙不忙|平常这个时候忙不忙)[，。！？、\s]*", action.speech)):
            action = action.model_copy(update={"casual": True})
        if action.kind == "daily":
            st.progress_at = st.turn  # do not suggest the player is stuck while choosing everyday life
        return action

    async def rule(self, action: Action) -> Ruling | None:
        if action.kind in ("meta",):
            return None
        await self._curtain(action)  # finish a scene this action would otherwise overtake
        if self.state.pending_turn.get("preparation", {}).get("blocked"):
            return Ruling(needs_roll=False, reason="出发前的交谈或同行尚未确认")
        if action.kind == "move" and action.destination:
            return Ruling(needs_roll=False, minutes=0, reason="移动")
        if self._stop_on_route(action):
            return Ruling(needs_roll=False, minutes=0, reason="先走到沿途地点，之后由玩家决定是否继续")
        if (action.kind == "daily" and action.casual and action.npc and not action.asked
                and not action.destination and not action.approach
                and any(n.id == action.npc for n in self.m.npcs_at(self.state.location))):
            return Ruling(needs_roll=False, minutes=0, reason="普通闲聊，不必另作裁定")
        await self.emit({"type": "status", "text": "裁定中…"})
        heading = self._heading(action)
        journal = self.state.pending_turn
        draft = journal.get("draft_ruling", {})
        ruling = (Ruling.model_validate(draft["ruling"]) if draft.get("action") == action.ruled()
                  else await self.cast.arbiter(self.state, action, at=heading))
        if ruling.scope != "模组内":  # the module is silent here: the director fills the gap
            if journal:
                journal["draft_ruling"] = {"action": action.ruled(), "ruling": ruling.model_dump(mode="json")}
                if self.checkpoint:
                    self.checkpoint()
            kind = "展开" if ruling.scope == "可即兴" else "后果"
            applied = await self._improvise(kind, action=action, ruling=ruling, at=heading or self.state.location,
                                           required=True)
            if applied is not None:
                ruling = self._steer(ruling, applied)
        return self._no_reroll(action, ruling, heading or self.state.location)

    async def prepare(self, action: Action) -> None:
        """Explicit conversations and consent before travelling, checkpointed one at a time."""
        if not action.destination or action.kind == "meta" or not fog.findable(self.m, self.state, action.destination):
            return
        journal = self.state.pending_turn.setdefault("preparation", {"done": [], "lines": [], "notes": [], "blocked": False})
        present = {n.id for n in self.m.npcs_at(self.state.location)}
        along = [] if action.alone else list(dict.fromkeys(action.companions))
        requests = [(f"before:{i}", step.npc, step.speech, step.private) for i, step in enumerate(action.before)]
        said = {step.npc for step in action.before}  # the one spoken to hears the words with the invitation
        requests += [(f"companion:{nid}", nid, action.speech if nid == action.npc and nid not in said else "", False) for nid in along]
        place = self.m.location(action.destination).name
        for key, nid, speech, private in requests:
            if key in journal["done"]:
                continue
            if nid not in present:
                name = next((n.name for n in self.m.npcs if n.id == nid), nid)
                journal["notes"].append(f"{name}不在当前地点，尚未完成出发前的交谈；不能替他回应或同行。")
                journal["blocked"] = True
            else:
                ent = self.world.rec.entities[nid]
                # Along already: to this very place, or wherever they go (the keeper's scene, world/scene.py).
                agreed = ent.following == self.state.investigator.id and ent.follow_to in (action.destination, "")
                if speech or not agreed:
                    # Asked along: its answer says whether it comes (TravelDialogue), whatever else is said to it
                    # ("我陪您去" and no accompany left the man behind, replay turn 82); asked once, not again after.
                    asked = nid in along and not agreed
                    request = speech or f"请与我一同前往{place}。"
                    invite = f"调查员想请你一同前往{place}。" if asked and speech else ""
                    privacy = "这是避开其他人的私下交谈，其他人没有听到。" if private else ""
                    message = (f"（系统：出发前，调查员{self.state.investigator.name}对你说：「{request}」。{invite}{privacy}"
                               "是否同意由你决定，不替其他人答应。）")
                    line = await self.cast.npc(self.state, nid, message, travel=asked or not speech, private=private)
                    journal["lines"].append([nid, line])
                    journal["notes"] += self._unsettled(nid)
                    self.state.npc(nid).met = True
                    self.state.add("npc", line, who=self.m.npc(nid).name)
                    self._exchanged(nid, Action(kind="talk", summary=request, speech=request), line, private=private)
                    ent = self.world.rec.entities[nid]
                    actual = ent.following == self.state.investigator.id and ent.follow_to == action.destination
                    claimed = bool(getattr(self.cast, "claimed_accompany", lambda *_: False)(self.state, nid, action.destination))
                    if asked and not actual and not claimed:
                        journal["notes"].append(f"{self.m.npc(nid).name}尚未同意同行，调查员尚未出发；由玩家决定是否独行或另作安排。")
                        journal["blocked"] = True
                    if asked:
                        journal["done"].append(f"companion:{nid}")  # answered here
                    journal["done"].append(key)
                    if self.checkpoint:
                        self.checkpoint()
                    await self.emit({"type": "npc", "npc": nid, "name": self.m.npc(nid).name, "text": line,
                                     "portrait": self.m.art.get(self.m.npc(nid).portrait or "", "")})
                    continue
            journal["done"].append(key)
            if self.checkpoint:
                self.checkpoint()

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
            return next((r for r in reversed(mine) if r.skill == skill or not skill), None)
        last = next((r for r in reversed(st.checks) if r.who == inv.id), None)
        if not ruling.needs_roll or last is None or last.passed:
            return None
        same = (last.turn, last.at, last.skill, last.aim) == (self._acted_before(), at, skill, _aim(action))
        quiet = last.seen == self._seen() and st.minutes - last.minutes < RECHECK_AFTER
        # The arbiter judges what changed: more bonus dice or another difficulty is another attempt.
        changed = ruling.bonus > last.bonus or (bool(last.difficulty) and ruling.difficulty != last.difficulty)
        return last if same and quiet and not changed else None

    def _acted_before(self) -> int:
        """The turn of the acting investigator's previous action (0 if none is on record):
        companions acting in between do not make a repeat any less immediate."""
        st = self.state
        name = st.investigator.name
        return max((e.turn for e in st.log if e.kind == "player" and e.who == name and e.turn < st.turn), default=0)

    def _seen(self) -> int:
        """Clues found, flags set and events fired so far. It only grows: a different count
        means something new has come up."""
        st = self.state
        return len(st.clues) + len(st.flags) + len(st.fired)

    def _record(self, action: Action, check: Check) -> None:
        st = self.state
        st.checks.append(CheckRecord(
            turn=st.turn, who=st.investigator.id, at=st.location, skill=check.name, npc=action.npc,
            target=action.target.strip(), text=check.describe(), passed=check.passed, minutes=st.minutes,
            bonus=check.bonus, difficulty=next((zh for zh, level in DIFFICULTY_ZH.items() if level == check.difficulty), ""),
        ))  # fmt: skip
        del st.checks[:-KEEP_CHECKS]

    # -- the player's own roll -----------------------------------------------------------
    def roll_prompt(self, action: Action, ruling: Ruling) -> dict[str, Any]:
        """What a player sees before throwing their own dice (graph.ask_roll): the check the
        keeper asks for, the numbers to beat, and one line on why. Nothing it shows is
        anything the module keeps back."""
        inv = self.state.investigator
        skill = canonical(ruling.skill)
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
        """The arbiter's reason as far as a player may read it: its opening clauses, up to the
        first one that names a clue, a flag, an id, what success would bring or anything that
        echoes a secret. The reason is written for the keeper; this is its safe beginning."""
        st, m = self.state, self.m
        hidden = {f"clue:{c.id}": f"{c.title}。{c.text}" for c in m.clues if c.id not in st.clues}
        hidden |= {f"secret:{n.id}:{i}": s for n in m.npcs for i, s in enumerate(n.secrets)}
        hidden |= {f"flag:{k}": v for k, v in m.flags.items() if k not in st.flags}
        hidden |= {"truth": m.truth, "notes": m.keeper_notes}
        visible = {f for loc in m.locations for f in [loc.name, *loc.features]} | {n.name for n in m.npcs}  # on screen anyway
        titles = [c.title for c in m.clues if c.id not in st.clues and len(c.title) >= 2 and c.title not in visible]
        kept: list[str] = []
        for clause in _CLAUSE.split(ruling.reason):
            clause = clause.strip()
            if not clause:
                continue
            if _BACKSTAGE.search(clause) or any(t in clause for t in titles) or guard.leaks(clause, hidden, n=4, hits=1):
                break
            if kept and len("，".join([*kept, clause])) > WHY_LEN:
                break
            kept.append(clause)
        why = "，".join(kept)
        return why if len(why) <= WHY_LEN else why[: WHY_LEN - 1] + "…"

    def _led_to(self, won: list[str], done: list[str], passed: bool, npc: str = "", offer: dict | None = None) -> list[str]:
        """What a roll led to, written under it: the clues it won and who gave them up, what it
        set in motion, or that it came to nothing. Only what the investigator now knows."""
        m = self.m
        lines = []
        for cid in won:
            clue = m.clue(cid)
            owner = next((n for n in m.npcs if cid in n.clues), None)
            got = f"{owner.name}松了口：〔{clue.title}〕" if owner else f"获得线索〔{clue.title}〕"
            lines.append(got if passed else f"没过，可关键线索还是到手了：〔{clue.title}〕，代价看叙述")
        if done:
            lines.append("行动奏效了")  # flag descriptions are private notes, not player-facing outcomes
        if offer is not None:
            lines.append("还有一次机会：孤注一掷，或者花幸运")
        if not lines:
            name = next((n.name for n in m.npcs if n.id == npc), "")
            lines.append(f"{name}{'松动了' if passed else '不为所动'}" if name else "没有新的发现" if passed else "没有收获")
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
    async def _improvise(
        self, kind: str, *, action: Action | None = None, ruling: Ruling | None = None, at: str = "",
        patience: float | None = None, required: bool = False,
    ) -> improv.Applied | None:
        """Prepare a scene. Current actions wait; unrelated future work may stay backstage."""
        st = self.state
        saved = st.pending_turn.get("scene_package", {})
        if required and saved.get("kind") == kind and saved.get("at") == (at or st.location):
            if saved.get("applied"):
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
        task = asyncio.create_task(self._plan(kind, action=action, ruling=ruling, at=where))
        receipt = st.pending_turn.setdefault("scene_package", {}) if required and st.pending_turn else None
        if receipt is not None:
            if receipt.get("kind") != kind or receipt.get("at") != where:
                receipt.clear()
            if receipt.get("applied"):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return improv.Applied(**receipt["applied"])
            receipt.update(kind=kind, at=where)
            if receipt.get("plan"):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                async def cached():
                    restored = Improv.model_validate(receipt["plan"])
                    restored.reads = dict(receipt.get("reads", {}))
                    return restored
                task = asyncio.create_task(cached())
        if required:
            return await self._ready(kind, task, action=action, ruling=ruling, at=where, receipt=receipt)
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
                     at: str, receipt: dict | None) -> improv.Applied | None:
        """The scene a current action needs. A plan that fails its checks is written again, and a
        first picture that failed is drawn once more, for up to PREPARE_LIMIT seconds; then the
        scene goes on without its picture (drawn in the background), or, with no plan that fits,
        the action settles as the module has it. The player is never left at /retry."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PREPARE_LIMIT
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
                    self._debug("director", {"phase": "failed", "kind": kind, "error": str(exc)})
                    failures += 1
                    pause = min(PREPARE_PAUSE * failures, deadline - loop.time()) if exc.__cause__ else 0.0
                    if pause < 0:
                        break
                    await asyncio.sleep(pause)  # a service that failed outright gets a moment first
                    task = asyncio.create_task(self._plan(kind, action=action, ruling=ruling, at=at))
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
            task = asyncio.create_task(self._plan(kind, action=action, ruling=ruling, at=at))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._debug("director", {"phase": "gave_up", "kind": kind, "seconds": PREPARE_LIMIT})
        if receipt is not None:  # a resumed turn settles the same way
            receipt.clear()
            receipt.update(kind=kind, at=at, given_up=True)
            if self.checkpoint:
                self.checkpoint()
        return None

    async def _plan(self, kind: str, *, action: Action | None, ruling: Ruling | None, at: str) -> Improv | None:
        """Validate the whole package, including plugins and world actions, with one repair."""
        said = f"{action.summary}（{action.kind}，对象：{action.target or '无'}）" if action else ""
        problems: list[str] = []
        for _attempt in range(2):
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
            problems = improv.check(self.m, plan, at=at, actors={i.id for i in self.state.investigators})
            if kind == "推进" and plan.go:
                problems.append("推进只能呈现事件或机会，不能替玩家决定移动；go 必须留空。")
            if not problems:
                from ..plugins.runtime import verify

                for plugin in plan.plugins:
                    problems += [f"插件 {plugin.id}：{problem}" for problem in await verify(plugin)]
            if problems:
                continue
            plan.reads = reads
            backstage = self._trial(plan, at) if plan.backstage else []
            if not backstage and plan.backstage:
                backstage = await self._consistent(plan)
            if not backstage:
                self._debug("director", {"phase": "validated", "kind": kind, "plan": plan.model_dump(mode="json")})
                return plan
            problems = backstage
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

    async def _consistent(self, plan: Improv) -> list[str]:
        """A rewrite has to hold together with what the players met about the same people and
        places: what they heard or saw may turn out staged, never impossible. The record checks
        the words (world/rewrite.py); a model reads the meaning, and a rewrite of something the
        players met does not go in unchecked."""
        w = self.world
        at = knowledge.table(w.m, w.st, w.rec)
        rows, met = [], False
        for op in plan.backstage:
            if not isinstance(op, Rewrite):
                continue
            kind, _, ident = op.target.partition(":")
            fact = w.rec.facts.get(ident) if kind == "fact" else None
            event = w.rec.event(ident) if kind == "event" else None
            if fact is not None:
                old, about = fact.text, fact.about
                how = knowledge.how_known(w, fact, at) if fact.revealed is not None or knowledge.exposed(old, at) else ""
            elif event is not None:
                old, about = event.text, [*event.participants, event.place]
                how = "亲眼见到" if event.visibility == "public" or knowledge.exposed(old, at) else ""
            else:
                continue
            known = [f"- {f.text}（{knowledge.how_known(w, f, at) or '见过'}）" for f in rewrite.pins(w, about) if f.id != ident][-12:]
            if not old or not (known or how):
                continue
            met = met or bool(how)
            rows.append(f"【改写】原来：{old}" + (f"（玩家{how}）" if how else "")
                        + f"\n改成：{'（并无此事）' if op.retract else op.text}\n理由：{op.reason or '无'}"
                        + ("\n【玩家已经知道的】\n" + "\n".join(known) if known else ""))
        if not rows or not hasattr(self.cast, "consistent"):
            return []
        problems = await self.cast.consistent(self.state, "\n\n".join(rows))
        if problems is None:  # the check could not be made
            return ["这次没能核对改写；玩家见过、听过的事先不要改"] if met else []
        return [f"改写说不通：{p}" for p in problems]

    async def _land(self, kind: str, plan: Improv, at: str = "", *, receipt: dict | None = None,
                    draw: float | None = None) -> improv.Applied | None:
        """Commit module, world actions and plugin sources together, from a validated copy. Its
        first pictures are waited for, up to `draw` seconds when given; with 0 the scene goes on
        and they are drawn in the background."""
        where = at or self.state.location
        problems = improv.check(self.m, plan, at=where, actors={i.id for i in self.state.investigators})
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
        room = max(0, IMPROV_SHOWCASES - sum(len(p.get("plan", {}).get("showcases", [])) for p in self.state.improv))
        if len(plan.showcases) > room:  # big scenes stay rare: a few a game
            plan = plan.model_copy(update={"showcases": plan.showcases[:room]})
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
            receipt["applied"] = asdict(applied)
            if self.checkpoint:
                self.checkpoint()
        self._debug("director", {"phase": "committed", "kind": kind, "plan": plan.model_dump(mode="json")})
        for target, change in applied.looks:  # a place burnt, a man wounded: their picture is redrawn
            self.visuals.look(target, change)
        await self._people()  # someone it brings in stands here
        self.visuals.schedule()
        self.showcases.prepare()  # a big scene of the director's gets its picture ahead of its moment
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
                    or improv.check(self.m, plan, at=b.at, actors={i.id for i in self.state.investigators})):
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
        """The arbiter's ruling plus what the director just added: new clues to find (with
        their check), scenes that play out this turn, and a word for the keeper."""
        r = ruling.model_copy(deep=True)
        for cid in applied.give:
            clue = self.m.clue(cid)
            gate = clue.check or (self.m.npc(clue.npc).gates.get(cid) if clue.npc else None)
            if gate is None:
                r.auto_clues.append(cid)
                continue
            r.clues_on_success.append(cid)
            if not r.needs_roll:
                r.needs_roll, r.skill, r.difficulty = True, gate.skill, gate.difficulty
        r.now, r.go, r.note = list(applied.now), applied.go, applied.note
        return r

    async def _failed(self, failure: Failure, wanted: list[str], turn: Turn, action: Action | None, *,
                      pushed: bool = False) -> None:
        """A check that failed stays failed (the user's call, 2026-10-20: no clue for a failure, no
        fixed price). Only what the arbiter said a failure brings about, the module's own word
        first, is settled here (a clue the module lets a failure find, a flag, time, a wound),
        and the keeper is told what was tried, what was not found and what happened."""
        st, step = self.state, self._resolution_step
        clues = self._valid_clues([c for c in failure.clues if c not in st.clues], action)
        missed = [self.m.clue(c).title for c in wanted if c not in st.clues and c not in clues]
        flags = [f for f in failure.flags if f in self.m.flags and f not in st.flags]

        def time() -> None:
            st.minutes += max(0, min(failure.minutes, 240))

        await step("fail-time", turn, time)
        if flags:
            await step("fail-flags", turn, lambda: self._flags(flags, turn))
        if clues:
            await step("fail-clues", turn, lambda: self._gain(clues, turn, action))
        if failure.damage.strip():
            def wound() -> dict:
                inv = st.investigator
                trial = inv.model_copy(deep=True)
                hurt = self.rules.damage(trial, failure.damage.strip())
                inv.__dict__.update(trial.__dict__)
                st.add("damage", hurt.describe(), who=inv.name)
                return {"type": "damage", "who": inv.name, "text": hurt.describe()}

            notice = await step("fail-damage", turn, wound)
            await step("fail-damage-notice", turn, lambda: self.emit(notice))
        happened = [x for x in (failure.text.strip(), f"耽搁了 {failure.minutes} 分钟" if failure.minutes else "",
                                f"{st.investigator.name}受了伤" if failure.damage.strip() else "") if x]
        if not missed and not happened:
            return  # the roll's card says it: nothing was within reach and nothing else came of it
        note = ("孤注一掷也没有成功" if pushed else "这次检定失败") + (f"，没有取得：{'、'.join(missed)}" if missed else "")
        note += f"。实际发生：{'；'.join(happened)}" if happened else "。没有别的后果"
        await step("fail-note", turn, lambda: turn.notes.append(
            note + "。照实讲失败，不在叙述里补出没拿到的东西；之后可以有新的机会，这一轮不补偿。"))

    async def _nudge(self, turn: Turn) -> None:
        """Stuck for a while: resume the same lead even after its cooldown was saved."""
        st = self.state
        step = self._resolution_step
        eligible = await step("eligible", turn, lambda: not (turn.action and turn.action.kind == "daily")
                              and not st.ending and st.turn - st.progress_at >= STALL_TURNS)
        if not eligible:
            return

        async def plan() -> dict | None:
            applied = await self._improvise("推进", patience=0.0)
            return asdict(applied) if applied is not None else None

        applied = await step("plan", turn, plan)
        await step("cooldown", turn, lambda: setattr(st, "progress_at", st.turn))
        if applied is None:
            return
        await step("flags", turn, lambda: self._flags(applied["now"], turn))
        clues = await step("clues", turn, lambda: self._valid_clues(applied["give"], turn.action))
        await step("give", turn, lambda: self._gain(clues, turn, turn.action))
        if applied["note"]:
            await step("note", turn, lambda: turn.notes.append(f"导演的安排：{applied['note']}"))

    def _heading(self, action: Action) -> str | None:
        """Where an action happens when the investigator walks somewhere to do it ("I go up to
        the tomb door and check the seams"): a reachable place other than here, which they know
        of or can find from here (engine/fog.py)."""
        dst = action.destination
        if action.kind == "move" or not dst or dst == self.state.location or self._travel(self.state.location, dst) is None:
            return None
        return dst if fog.findable(self.m, self.state, dst) else None

    def _resolution_key(self, key: str) -> str:
        return key[1:] if key.startswith("/") else "/".join([*self._resolution_scope, key])

    def _resolution_pending(self, key: str) -> bool:
        row = (self._resolution or {}).get("steps", {}).get(self._resolution_key(key))
        return row is not None and not row.get("done")

    def _resolution_save(self, turn: Turn) -> None:
        if self._resolution is not None:
            self._resolution["turn"] = TURN_RECORD.dump_python(turn, mode="json")
            if self.checkpoint:
                self.checkpoint()

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
            try:
                value = work()
            finally:
                self.rules.dice = dice
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

    async def resolve(self, action: Action, ruling: Ruling | None) -> Turn:
        """Settle an action in durable stages, including partially completed resolution."""
        async with self._resolution_lock:
            return await self._resolve(action, ruling)

    async def _resolve(self, action: Action, ruling: Ruling | None) -> Turn:
        st = self.state
        identity = {"at": st.turn, "action": action.model_dump(mode="json"),
                    "ruling": ruling.model_dump(mode="json") if ruling else None}
        direct = not st.pending_turn
        saved = (st.pending_resolution or None) if direct else st.pending_turn.get("resolution")
        if saved is not None and saved.get("identity") != identity:
            raise ValueError("尚有未完成的行动结算，请先继续原回合")
        journal = saved or {"identity": identity, "steps": {}}
        if direct:
            st.pending_resolution = journal
        else:
            st.pending_turn["resolution"] = journal
        self._resolution = journal
        self._resolution_scope = []
        turn = TURN_RECORD.validate_python(journal["turn"]) if "turn" in journal else Turn(action=action, ruling=ruling)
        try:
            if not journal.get("complete"):
                await self._resolve_stages(action, ruling, turn)
                journal["complete"] = True
                self._resolution_save(turn)
            if direct:
                st.pending_resolution = {}
                if self.checkpoint:
                    self.checkpoint()
            return turn
        finally:
            self._resolution = None
            self._resolution_scope = []

    async def _resolve_stages(self, action: Action, ruling: Ruling | None, turn: Turn) -> None:
        st = self.state
        if action.kind == "meta":
            return

        def initial() -> bool:
            preparation = st.pending_turn.get("preparation", {})
            turn.npc_lines += [tuple(row) for row in preparation.get("lines", [])]
            turn.notes += preparation.get("notes", [])
            if preparation.get("blocked"):
                return False
            late, self._late = self._late, Turn()
            for attr in ("events", "clues", "sanity", "notes", "hints", "world"):
                getattr(turn, attr).extend(getattr(late, attr))
            turn.arrived, turn.first_visit = late.arrived, late.first_visit
            return ruling is not None

        step = self._resolution_step
        self._dealing = self._engaged(action)
        flagged = await step("beats", turn, lambda: self._play(self._late, self._dealing))
        if flagged:
            await step("beat-flags", turn, lambda: self._flags(flagged, self._late, landed=True))
        if not await step("initial", turn, initial) or ruling is None:
            return
        if ruling.missing:
            await step("missing", turn, lambda: self._lacking(ruling, turn))
        inv = st.investigator

        def travel() -> dict:
            heading = ruling.go or (action.destination if action.kind == "move" else self._heading(action))
            heading = None if heading == st.location else heading
            if heading and heading != ruling.go and not fog.findable(self.m, st, heading):
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
            st.minutes += minutes
            return {"heading": heading, "intended": intended, "stopping": stopping}

        moving = await step("travel", turn, travel)
        if moving:
            await step("arrival", turn, lambda: self._arrive(moving["heading"], turn, alone=action.alone, party=True,
                                                             companions=action.companions))

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

        async def focus() -> None:
            if key := views.after(self.m, st, action.kind, action.target):
                await self._view(key, "focus" if key == views.focus(self.m, self.m.location(st.location), action.target) else "")

        await step("focus", turn, focus)

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
                turn.checks.append(rolled)
                passed = rolled.passed
                self._record(action, rolled)
                st.add("dice", rolled.describe(), who=inv.name)
                event = {"type": "dice", "who": inv.name, **_check_view(rolled)}
                need = rolled.luck_needed()
                if not passed and (rolled.can_push or need is not None):
                    wanted = self._valid_clues(ruling.clues_on_success, action)
                    if wanted:
                        luck = need if need is not None and need <= inv.luck else None
                        turn.push = {"skill": rolled.name, "target": rolled.target,
                                     "need": _threshold(rolled.target, ruling.difficulty), "difficulty": ruling.difficulty,
                                     "bonus": ruling.bonus, "clues": wanted, "flags": ruling.set_flags,
                                     "can_push": rolled.can_push, "luck": luck, "left": inv.luck - (luck or 0),
                                     "risk": ruling.on_push_fail.text.strip(),  # said before the choice (7e)
                                     "on_fail": ruling.on_fail.model_dump(), "on_push_fail": ruling.on_push_fail.model_dump(),
                                     "npc": action.npc if action.kind in ("talk", "perform") else "", "value": rolled.value}
                        st.pending_push = turn.push
            return {"passed": passed, "event": event, "failed": not passed and turn.push is None and bool(turn.checks)}

        checked = await step("check", turn, check)
        passed = checked["passed"]
        if checked["event"]:
            await step("check-notice", turn, lambda: self.emit(checked["event"]))
        if checked.get("failed"):
            await self._failed(ruling.on_fail, self._valid_clues(ruling.clues_on_success, action), turn, action)

        def available() -> dict:
            gained = self._valid_clues(ruling.auto_clues, action)
            if passed:
                gained += [c for c in self._valid_clues(ruling.clues_on_success, action) if c not in gained]
            return {"known": list(st.flags), "gained": gained[: MAX_FOUND if action.target else 1]}

        found = await step("available", turn, available)
        if passed:
            await step("flags", turn, lambda: self._flags(ruling.set_flags, turn))

            def changes() -> None:
                adapters.settle_changes(self.world, ruling.changes, source="arbiter")
                self._equipment(ruling, turn)
                self._takes(ruling, turn)

            await step("changes", turn, changes)
        await step("gain", turn, lambda: self._gain(found["gained"], turn, action))
        if turn.checks:
            won = [c for c in turn.clues if c in ruling.clues_on_success]
            done = [f for f in ruling.set_flags if f in st.flags and f not in found["known"]]
            npc = action.npc if action.kind in ("talk", "perform") else ""
            await step("outcome", turn, lambda: self.emit({"type": "outcome", "who": inv.name,
                       "lines": self._led_to(won, done, passed, npc, offer=turn.push)}))
        if ruling.now:
            await step("now", turn, lambda: self._flags(ruling.now, turn))

        def elapsed() -> None:
            if ruling.note:
                turn.notes.append(f"导演的安排：{ruling.note}")
            resting = action.kind == "wait" or ruling.rest
            st.minutes += max(0, min(ruling.minutes, WAIT_CAP if resting else ACT_CAP))

        await step("elapsed", turn, elapsed)
        if passed:
            for index, call in enumerate(ruling.plugin_calls):
                await step(f"action-plugin:{index}", turn, lambda call=call: self.plugins.run("action", turn, **call.model_dump(mode="json")))
        await step("settled-plugins", turn, lambda: self.plugins.settled(turn))
        await step("turn-plugins", turn, lambda: self.plugins.run("turn", turn, action=action.model_dump(mode="json")))
        await step("time", turn, lambda: self._time(turn))
        if action.kind == "daily":
            await step("progress", turn, lambda: setattr(st, "progress_at", st.turn))
        if self.improvising:
            await step("nudge", turn, lambda: self._nudge(turn))
        await step("ending", turn, lambda: self._check_ending(turn))
        for item in self._items_for(action):
            await step(f"item:{item.id}", turn, lambda item=item: self.emit({"type": "item", "id": item.id, "name": item.name,
                       "text": item.description, "art": item.art}))
        if turn.checks or turn.earlier:
            await step("settle", turn, lambda: self._settle(st.checks[-1]))

    def _lacking(self, ruling: Ruling, turn: Turn) -> None:
        """What this action lacks and so cannot do (Ruling.missing) is kept for the turn: whoever
        registers a thing this turn cannot bring it out (world/ops.Bring), and the keeper hears it."""
        rec = self.world.rec
        rec.missing, rec.missing_turn = [x.strip() for x in ruling.missing if x.strip()], self.state.turn
        if rec.missing:
            turn.notes.append(f"这次行动缺{'、'.join(rec.missing)}，做不成。")

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

    async def push(self, mode: str) -> Turn:
        """Resume the saved choice without paying luck or rolling its push twice."""
        if mode not in ("push", "luck", "drop"):
            raise ValueError("请选择孤注一掷、花费幸运或放弃")
        async with self._resolution_lock:
            st = self.state
            direct = not st.pending_turn
            saved = (st.pending_resolution or None) if direct else st.pending_turn.get("push_resolution")
            identity = {"kind": "push", "at": st.turn, "mode": mode, "active": st.active}
            if saved is not None and saved.get("identity") != identity:
                raise ValueError("已有未完成的检定选择，请先继续原选择")
            if saved is None and not st.pending_push:
                return Turn()
            journal = saved or {"identity": identity, "pending": dict(st.pending_push), "steps": {}}
            if direct:
                st.pending_resolution = journal
            else:
                st.pending_turn["push_resolution"] = journal
            self._resolution, self._resolution_scope = journal, []
            turn = TURN_RECORD.validate_python(journal["turn"]) if "turn" in journal else Turn()
            try:
                if not journal.get("complete"):
                    await self._push_stages(mode, journal["pending"], turn)
                    journal["complete"] = True
                    self._resolution_save(turn)
                if direct:
                    st.pending_resolution = {}
                    if self.checkpoint:
                        self.checkpoint()
                return turn
            finally:
                self._resolution, self._resolution_scope = None, []

    async def _push_stages(self, mode: str, pending: dict, turn: Turn) -> None:
        st = self.state
        step = self._resolution_step
        record = st.checks[-1] if st.checks and st.checks[-1].turn == st.turn else None
        inv, npc = st.investigator, pending.get("npc", "")

        def initial() -> dict:
            st.pending_push = None
            return {"flags": list(st.flags), "minutes": st.minutes}

        start = await step("initial", turn, initial)
        known = start["flags"] if isinstance(start, dict) else start  # a choice saved before minutes were kept
        if mode == "drop":
            await self._failed(Failure.model_validate(pending.get("on_fail") or {}), pending["clues"], turn, None)
            await step("plugins", turn, lambda: self.plugins.settled(turn))
            won = [c for c in turn.clues if c in pending["clues"]]
            await step("outcome", turn, lambda: self.emit({"type": "outcome", "who": inv.name,
                       "lines": self._led_to(won, [], False, npc) if won else ["放弃了，换条路"]}))
        else:
            await self._pushed(mode, pending, turn, record, known)
        # What the failure cost settles as any turn's does: the clock moved on, so what was due by
        # now happens (scheduled events, the rest of the world, rule timers), and an ending it
        # brought about ends the game.
        if isinstance(start, dict) and st.minutes > start["minutes"]:
            await step("time", turn, lambda: self._time(turn))
        await step("ending", turn, lambda: self._check_ending(turn))
        await step("settle", turn, lambda: self._settle(record))

    async def _pushed(self, mode: str, pending: dict, turn: Turn, record: CheckRecord | None, known: list[str]) -> None:
        """The second chance: luck spent, or the pushed roll and what it brings."""
        st, step = self.state, self._resolution_step
        inv, npc = st.investigator, pending.get("npc", "")
        lucky = mode == "luck" and pending.get("luck") is not None

        def check() -> dict:
            if lucky:
                inv.luck -= pending["luck"]
                st.add("dice", f"花费幸运 {pending['luck']} 点，{pending['skill']} 检定改为成功", who=inv.name)
                self._settle(record, True, f"{record.text}，花 {pending['luck']} 点幸运改为成功" if record else "")
                return {"passed": True, "notice": {"type": "luck", "who": inv.name, "spent": pending["luck"], "left": inv.luck}}
            diff = DIFFICULTY_ZH.get(pending["difficulty"], Level.REGULAR)
            rolled = self.rules.check(inv, pending["skill"], diff, pending["bonus"], pushed=True, target=pending["target"])
            turn.checks.append(rolled)
            st.add("dice", rolled.describe(), who=inv.name)
            self._settle(record, rolled.passed, rolled.describe())
            return {"passed": rolled.passed, "notice": {"type": "dice", "who": inv.name, **_check_view(rolled)}}

        checked = await step("check", turn, check)
        passed = checked["passed"]
        await step("check-notice", turn, lambda: self.emit(checked["notice"]))
        if passed:
            await step("flags", turn, lambda: self._flags(pending.get("flags", []), turn))
            await step("gain", turn, lambda: self._gain(pending["clues"], turn, None))
        else:  # what was said before the choice now happens (Failure, Ruling.on_push_fail)
            await self._failed(Failure.model_validate(pending.get("on_push_fail") or {}), pending["clues"], turn, None, pushed=True)
        await step("plugins", turn, lambda: self.plugins.settled(turn))
        won = [c for c in turn.clues if c in pending["clues"]]
        done = [f for f in pending.get("flags", []) if f in st.flags and f not in known]
        lines = self._led_to(won, done, passed, npc)
        if lucky:
            lines.insert(0, f"花了 {pending['luck']} 点幸运，算作成功")
        elif not passed:
            lines.insert(0, f"孤注一掷也没成：{pending['risk']}" if pending.get("risk") else "孤注一掷也没成")
        await step("outcome", turn, lambda: self.emit({"type": "outcome", "who": inv.name, "lines": lines}))

    async def talk(self, turn: Turn) -> None:
        """NPC replies and stage performances, after the engine has resolved checks."""
        st, m = self.state, self.m
        action = turn.action
        speaking = (action and action.npc and (action.kind in ("talk", "perform")
                    or (action.kind == "daily" and (action.speech or action.casual))))
        if action and action.destination and not action.speech and action.npc in action.companions:
            speaking = False  # the invitation was already resolved before departure
        if action and action.approach == "心理学" and not action.speech:
            speaking = False  # watching someone's demeanour does not start a conversation
        if speaking:
            if any(nid == action.npc for nid, _ in turn.npc_lines):
                pass  # a resumed narration never asks an NPC to answer the same question twice
            elif not any(n.id == action.npc for n in m.npcs_at(st.location)):
                turn.notes.append(f"{action.npc} 不在这里")
            else:
                npc = m.npc(action.npc)
                await self.emit({"type": "status", "text": f"等待 {npc.name} 回应…"})
                line = await self.cast.npc(st, npc.id, self._npc_message(turn, npc.id))
                turn.npc_lines.append((npc.id, line))
                turn.notes += self._unsettled(npc.id)
                st.npc(npc.id).met = True
                st.add("npc", line, who=npc.name)
                self._exchanged(npc.id, action, line)
                if self.turn_checkpoint:
                    self.turn_checkpoint(turn)
                await self.emit({"type": "npc", "npc": npc.id, "name": npc.name, "text": line, "portrait": m.art.get(npc.portrait or "", "")})
        for ev in turn.events:
            if ev.performer and ev.id not in turn.performed:
                npc = m.npc(ev.performer)
                await self.emit({"type": "status", "text": f"{npc.name}登台了…"})
                text = await self.cast.perform(st, npc.id, ev.performance, self._deeds(npc.id))
                turn.performance = (npc.id, text)
                st.npc(npc.id).met = True
                st.add("npc", text, who=npc.name)
                turn.performed.append(ev.id)
                if self.turn_checkpoint:
                    self.turn_checkpoint(turn)
                await self.emit({"type": "performance", "npc": npc.id, "name": npc.name, "text": text, "portrait": m.art.get(npc.portrait or "", "")})
        # Important information heard in a zero-minute exchange can change a person's
        # next action now, using the same allowance as this turn's elapsed-time work.
        await self._world(turn)
        await self._people()
        if self.turn_checkpoint:
            self.turn_checkpoint(turn)

    def _exchanged(self, npc_id: str, action: Action, line: str, *, private: bool = False) -> None:
        """A conversation goes into the world record: everyone there saw it, an important NPC keeps
        it as a memory. Significant exchanges can wake its autonomous action this turn."""
        st, npc = self.state, self.m.npc(npc_id)
        inv = st.investigator
        said = action.speech or action.visible or "（没有逐字台词）"
        text = f"{inv.name}和{npc.name}说话：「{said}」{npc.name}答：「{line}」"
        self.world.apply(Occur(id=f"talk:{st.turn}:{inv.id}:{npc_id}", actor=inv.id, participants=[npc_id], place=st.location,
                               text=text, visibility="private" if private else "public", reactive=not action.casual, transcript=True))  # fmt: skip

    def _unsettled(self, npc_id: str) -> list[str]:
        """What an NPC's last reply meant to do and could not (agents/cast.Cast.unsettled): the
        words stand, the deed did not land, and the keeper tells how it really went."""
        failed = getattr(self.cast, "unsettled", {}).pop(npc_id, [])
        if not failed:
            return []
        name = self.m.npc(npc_id).name
        return [f"【未落实】{name}刚才的回应里有做不到的事：{'；'.join(failed)}。叙述里按实际情况交代（东西不在身上、去不了那里），"
                "不写成已经做到，也不删改他说过的话。"]

    async def narrate(self, turn: Turn, on_delta=None) -> str:
        if (turn.npc_lines and not turn.events and not turn.notes and not turn.ending
                and not turn.arrived and not turn.world and not turn.sanity and not turn.hints):
            self.told = None
            await self.emit({"type": "dialogue_end"})
            return ""  # the reply, clue cards and roll outcomes have already been shown
        return await self._narrate(self._report(turn), on_delta, lore=self._brief(), turn=turn)

    def _brief(self) -> str:
        """What the keeper keeps behind the screen for this telling (world/context.keeper_brief),
        set apart from what it rides along with (the report, a nudge to tell it again)."""
        brief = world_view.keeper_brief(self.world) if getattr(self.cast, "backstage", True) else ""
        return "\n\n" + brief if brief else ""

    async def remember(self) -> None:
        st = self.state
        st.tally()
        if self._world_owed and self._world_task is None:
            self._world_owed = False
            self._world_task = asyncio.create_task(self._live_later(), name="world")
        self.world.sync()  # before the log is folded: what the players were told is out
        self._recount()  # before the log is folded: it reads the lines as they stand
        st.heard = fog.heard(self.m, st)  # the places named in the log outlive its folding into memory
        recent = [e for e in st.log if e.kind != "dice"]
        if len(recent) < 48:
            return
        old, keep = st.log[:-24], st.log[-24:]
        text = "\n".join(f"[{e.time}] {e.who or e.kind}：{e.text}" for e in old if e.kind != "dice")
        await self.emit({"type": "status", "text": "记录员整理笔记…"})
        st.memory = await self.cast.chronicle(st, text)
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
                for inv in st.investigators:
                    s = self.rules.sanity(inv, ok, bad)
                    turn.sanity.append((inv.name, s))
                    st.add("sanity", s.describe(), who=inv.name)
                    await self.emit({"type": "sanity", "who": inv.name, **_sanity_view(s)})
                turn.notes.append("画得太像了——他本不该画得出来")
            if prompt.clue:
                await self._gain([prompt.clue], turn, None)
        return await self.narrate(turn)

    # -- helpers --------------------------------------------------------------------------
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
                      companions: list[str] | None = None) -> None:
        """The party is at loc_id now. On their own way (party), a companion goes where the
        investigator goes, a detour included, until the place it agreed to; an event that carries
        them off takes along only a companion it happens to be taking that way. Someone asked
        along who did not come is named, so the telling does not walk them in."""
        st, m = self.state, self.m
        step = self._resolution_step

        def commit() -> dict:
            src = st.location
            if src != loc_id:
                self.world.rec.left[src] = self.world.rec.seq
            st.location = loc_id
            came: set[str] = set()
            for ent in list(self.world.rec.entities.values()):
                if ent.kind != "npc" or ent.at != src or ent.following != st.investigator.id:
                    continue
                path = self._route(src, ent.follow_to)
                if not alone and src != loc_id and (party or (path and loc_id in path)):
                    result = self.world.apply(Move(id=f"escort:{st.turn}:{ent.id}:{src}:{loc_id}", entity=ent.id, to=loc_id))
                    if result.ok:
                        came.add(ent.id)
                        turn.notes.append(f"{ent.name}与调查员一同抵达{m.location(loc_id).name}。")
                if alone or ent.at == ent.follow_to:
                    self.world.apply(Follow(id=f"escort-end:{st.turn}:{ent.id}", npc=ent.id))
            if party and not alone and src != loc_id:
                for nid in dict.fromkeys(companions or []):
                    ent = self.world.rec.entities.get(nid)
                    if ent is not None and nid not in came and ent.at != loc_id:
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
            for f in m.foreshadow:
                if f.where == loc_id and f.id not in st.hinted:
                    st.hinted.append(f.id)
                    turn.notes.append(f"这里可以顺手露出一点端倪，一笔带过，不点破、不解释：{f.hint}")
            return {"type": "move", "location": loc.id, "name": loc.name, "art": m.art.get(loc.art or "", ""), "view": st.view}

        async def arrive() -> None:
            notice = await step("commit", turn, commit)
            await step("notice", turn, lambda: self.emit(notice))
            if turn.first_visit:
                await step("showcase", turn, lambda: self.showcases.fire(f"visit:{loc_id}"))
            for ev in m.events:
                if ev.when == f"visit:{loc_id}" and (ev.id not in st.fired or self._resolution_pending(f"/event:{ev.id}")):
                    await self._fire(ev, turn)
            await step("plugins", turn, lambda: self.plugins.run("visit", turn, location=loc_id))

        await step(f"arrive:{loc_id}", turn, arrive)

    def _valid_clues(self, ids: list[str], action: Action | None) -> list[str]:
        st, m = self.state, self.m
        here = {c.id for c in m.clues_at(st.location)}
        for n in m.npcs_at(st.location):
            here |= set(n.clues)
        known = {c.id for c in m.clues if c.available(st.flags, st.clues)}
        found = [c for c in dict.fromkeys(ids) if c in known and c in here and c not in st.clues]
        if action and (action.approach == "心理学" or (action.asked and canonical(action.asked.skill) == "心理学")):
            # An observation cannot unlock testimony behind a different social gate, even
            # when the arbiter mistakenly awards it. Explicit psychology clues still work.
            def observable(cid: str) -> bool:
                clue = m.clue(cid)
                gate = m.npc(clue.npc).gates.get(cid) if clue.npc else None
                return gate is None or canonical(gate.skill) == "心理学"

            found = [cid for cid in found if observable(cid)]
        return found

    async def _gain(self, ids: list[str], turn: Turn, action: Action | None) -> None:
        st, m = self.state, self.m
        step = self._resolution_step
        for cid in ids:
            key = f"/clue:{cid}"
            if cid in st.clues and not self._resolution_pending(key):
                continue
            clue = m.clue(cid)
            if not clue.available(st.flags, st.clues) and not self._resolution_pending(key):
                continue

            def commit(clue=clue, cid=cid) -> list[dict]:
                inv = st.investigator
                sanity = None
                if clue.sanity:
                    ok, bad = clue.sanity.split("/")
                    trial = inv.model_copy(deep=True)
                    sanity = self.rules.sanity(trial, ok, bad)
                    inv.__dict__.update(trial.__dict__)
                st.clues.append(cid)
                st.progress_at = st.turn
                turn.clues.append(cid)
                if clue.npc:
                    unlocked = st.npc(clue.npc).unlocked
                    if cid not in unlocked:
                        unlocked.append(cid)
                st.add("clue", f"〔{clue.title}〕{clue.text.strip()}")
                notices = [{"type": "clue", "id": cid, "title": clue.title, "text": clue.text.strip()}]
                if clue.handout and clue.handout not in st.handouts:
                    h = m.handout(clue.handout)
                    st.handouts.append(h.id)
                    st.add("handout", h.title)
                    notices.append({"type": "handout", "id": h.id, "title": h.title, "kind": h.kind, "text": h.text})
                if sanity is not None:
                    turn.sanity.append((inv.name, sanity))
                    st.add("sanity", sanity.describe(), who=inv.name)
                    notices.append({"type": "sanity", "who": inv.name, **_sanity_view(sanity)})
                return notices

            async def gain() -> None:
                notices = await step("commit", turn, commit)
                for index, notice in enumerate(notices):
                    await step(f"notice:{index}", turn, lambda notice=notice: self.emit(notice))

            await step(key, turn, gain)
        if turn.clues:
            await step("clue-sheet", turn, lambda: self.emit({"type": "sheet"}))

    async def _flags(self, flags: list[str], turn: Turn, *, landed: bool = False) -> None:
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
                await step("commit", turn, commit)
                await step("notice", turn, lambda: self.emit({"type": "flag", "flag": flag, "meaning": m.flags[flag]}))
                await step("showcase", turn, lambda: self.showcases.fire(f"flag:{flag}"))
                for ev in m.events:
                    if ev.when == f"flag:{flag}" and (ev.id not in st.fired or self._resolution_pending(f"/event:{ev.id}")):
                        await self._fire(ev, turn)

            await step(key, turn, settle)

    async def _time(self, turn: Turn | None = None) -> None:
        st, m = self.state, self.m
        turn = turn or Turn()
        due = sorted(
            (ev for ev in m.events if (ev.id not in st.fired or self._resolution_pending(f"/event:{ev.id}"))
             and re.fullmatch(r"D\d+ \d{1,2}:\d{2}", ev.when)),
            key=lambda e: parse_time(e.when),
        )
        for ev in due:
            if parse_time(ev.when) <= st.minutes:
                await self._fire(ev, turn)
        if st.minutes // 1440 != st.day:  # a new day: each investigator's daily SAN loss starts again
            st.day = st.minutes // 1440
            for inv in st.investigators:
                inv.new_day()
        await self._world(turn)
        await self._people()
        if not st.view_kind and (hour := views.default(m, m.location(st.location), st.minutes)) != st.view:
            await self._view(hour)  # night has fallen on the place: its picture for the night
        await self.plugins.due(turn)  # rule plugins' timers
        await self.emit({"type": "time", "clock": st.clock, "phase": time_of_day(st.minutes)})

    def _present(self) -> tuple[str, ...]:
        return tuple(n.id for n in self.m.npcs_at(self.state.location) if n.status not in ("死亡", "失踪"))

    async def _people(self) -> None:
        """Who is here changed without the party moving (someone walked in, left, fell): the
        screens hear of it."""
        if (here := self._present()) != self._here:
            self._here = here
            await self.emit({"type": "people", "location": self.state.location, "ids": list(here)})
        for n in self.m.npcs:  # hurt or killed backstage: their face is drawn again (Codex, when it is here)
            if n.status != self._status.get(n.id, ""):
                self._status[n.id] = n.status
                if n.status and n.status != "失踪":
                    self.visuals.look(f"npc:{n.id}", f"{n.name}{n.status}")

    async def _view(self, key: str, kind: str = "") -> None:
        """The scene shows another picture of this place (engine/views.py)."""
        st = self.state
        if key != st.view or kind != st.view_kind:
            st.view, st.view_kind = key, kind
            await self.emit({"type": "view", "location": st.location, "key": key})

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
        if not problems and any(isinstance(op, Rewrite) for op in ops):
            problems = await self._consistent(Improv(kind="推进", reason=reason or "幕后调整", backstage=ops))
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
        of the scene waits for that exchange to end, HOLD_TURNS turns at most. Returns the story
        flags the beats set, whose consequences are left to _flags."""
        w = self.world
        before = set(self.state.flags)
        for beat in stage.waiting(w):
            if engaged & set(beat.leaving) and self.state.turn - beat.turn < stage.HOLD_TURNS:
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
        for attr in ("events", "sanity", "notes", "hints", "world"):
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
                                         checkpoint=self.checkpoint)
        self._moved_on = False
        self._debug("world", asdict(report))
        self.debug_world.append(report)
        del self.debug_world[:-30]
        self.world.rec.simulated = st.minutes
        turn.world += world_view.happened(self.world, since)
        if inline and self.turn_checkpoint:  # after a turn there is no turn record to keep
            self.turn_checkpoint(turn)

    async def _fire(self, ev: Event, turn: Turn) -> None:
        st = self.state
        key = f"/event:{ev.id}"
        if ev.id in st.fired and not self._resolution_pending(key):
            return
        step = self._resolution_step

        def commit() -> None:
            st.fired.append(ev.id)
            st.progress_at = st.turn
            turn.events.append(ev)
            st.add("event", ev.read_aloud.strip() or ev.title)

        def sanity_for(inv: Investigator) -> dict | None:
            if not inv.can_act:
                return None
            ok, bad = ev.sanity.split("/")
            trial = inv.model_copy(deep=True)
            sanity = self.rules.sanity(trial, ok, bad)
            inv.__dict__.update(trial.__dict__)
            turn.sanity.append((inv.name, sanity))
            st.add("sanity", sanity.describe(), who=inv.name)
            return {"type": "sanity", "who": inv.name, **_sanity_view(sanity)}

        def damage() -> dict:
            inv = st.investigator
            trial = inv.model_copy(deep=True)
            hurt = self.rules.damage(trial, ev.damage)
            inv.__dict__.update(trial.__dict__)
            st.add("damage", hurt.describe(), who=inv.name)
            turn.notes.append(f"{inv.name}{hurt.describe()}")
            return {"type": "damage", "who": inv.name, "text": hurt.describe()}

        def sets() -> None:
            for flag in ev.sets:
                if flag not in st.flags:
                    st.flags.append(flag)

        async def fire() -> None:
            await step("commit", turn, commit)
            await step("notice", turn, lambda: self.emit({"type": "event", "id": ev.id, "title": ev.title, "text": ev.read_aloud.strip()}))
            if ev.art and views.shown(self.m, ev.art):
                await step("view", turn, lambda: self._view(ev.art, "moment"))
            await step("showcase", turn, lambda: self.showcases.fire(f"event:{ev.id}"))
            await step("named", turn, lambda: self.showcases.named(f"{ev.title}\n{ev.read_aloud}"))
            if ev.move_to:
                await step("move", turn, lambda: self._arrive(ev.move_to, turn) if ev.move_to != st.location
                           or self._resolution_pending(f"arrive:{ev.move_to}") else None)
            if ev.sanity:
                for inv in st.investigators:
                    notice = await step(f"sanity:{inv.id}", turn, lambda inv=inv: sanity_for(inv))
                    if notice:
                        await step(f"sanity-notice:{inv.id}", turn, lambda notice=notice: self.emit(notice))
            if ev.damage:
                notice = await step("damage", turn, damage)
                await step("damage-notice", turn, lambda: self.emit(notice))
            await step("sets", turn, sets)
            for flag in ev.sets:
                await step(f"flag-showcase:{flag}", turn, lambda flag=flag: self.showcases.fire(f"flag:{flag}"))
            if ev.ending:
                await step("check-ending", turn, lambda: self._check_ending(turn))
                if not st.ending or self._resolution_pending(f"/ending:{ev.ending}"):
                    await self._end(ev.ending, turn)

        await step(key, turn, fire)

    async def _check_ending(self, turn: Turn) -> None:
        st, m = self.state, self.m
        if st.ending:
            if self._resolution_pending(f"/ending:{st.ending}"):
                await self._end(st.ending, turn)
            return
        for end in m.endings:
            if end.condition.startswith("flag:") and end.condition[5:] in st.flags:
                await self._end(end.id, turn)
                return

    async def _end(self, ending_id: str, turn: Turn) -> None:
        st, m = self.state, self.m
        end = next(e for e in m.endings if e.id == ending_id)
        step = self._resolution_step

        def reward(inv: Investigator) -> str:
            gain = re.search(r"理智\s*\+\s*(\d*[dD]\d+|\d+)", end.reward)
            if not gain or not inv.can_act:
                return ""
            n = self.rules.dice.total(gain.group(1).upper()) if "D" in gain.group(1).upper() else int(gain.group(1))
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

    def _npc_message(self, turn: Turn, npc_id: str) -> str:
        st, m = self.state, self.m
        inv = st.investigator
        action = turn.action
        assert action is not None
        said = f"对你说：「{action.speech}」" if action.speech else f"在你面前：{action.visible}" if action.visible else "等候你的回应"
        parts = [f"（系统：{st.clock}，{m.location(st.location).name}。{inv.name}（{inv.occupation}）{said}"]
        if action.approach and action.approach != "心理学":
            parts.append(f"方式：{action.approach}。")
        social = {"说服", "话术", "恐吓", "魅惑"}
        checks = [c for c in turn.checks if canonical(c.name) in social and canonical(c.name) == action.approach]
        if checks:
            c = checks[-1]
            effect = "本次交涉成功，让步限于这次请求和已授权内容" if c.passed else "本次交涉未达成，不因此增加敌意或新的惩罚"
            parts.append(f"检定：{c.name} {c.level.zh}，{effect}。")
        elif turn.earlier is not None and turn.earlier.skill in social and turn.earlier.skill == action.approach:
            e = turn.earlier
            effect = "本次交涉已成功，让步仍限于这次请求和已授权内容" if e.passed else "本次请求仍未达成，不增加新的敌意或惩罚"
            parts.append(f"检定：同样的{e.skill}刚才已经试过，不重掷，结果照旧：{effect}。")
        # Permission persists across turns; an empty *new* clue list must not make the NPC
        # retract something it already disclosed earlier in the conversation.
        npc = m.npc(npc_id)
        available = set(st.npc(npc_id).unlocked) | {c for c in npc.clues if c not in npc.gates and m.clue(c).available(st.flags, st.clues)}
        available |= {c for c in turn.clues if m.clue(c).npc == npc_id or c in npc.clues}
        mine = [c.id for c in m.clues if c.id in available]
        if mine:
            parts.append("已解锁，现在可以说出（含此前已授权内容）：" + "；".join(f"{cid}「{m.clue(cid).title}」" for cid in mine) + "。")
        else:
            parts.append("尚无可公开的门槛线索；仍可按你已知的日常信息正常回答。")
        parts.append("用你的口吻回答。）")
        return "".join(parts)

    def _deeds(self, npc_id: str) -> str:
        """A performer's own records; the party's private log and summary are not its memory."""
        return world_view.npc_brief(self.world, npc_id, query="表演中可以引用的亲历与当面获知的经过")

    def _public_location(self, loc_id: str) -> str:
        loc = self.m.location(loc_id)
        status = {n: e.status for n, e in self.world.rec.entities.items() if e.kind == "npc" and e.status}
        people = "\n".join(f"{n.name}" + (f"（{status[n.id]}）" if n.id in status else "") + f"：{n.look}" for n in self.m.npcs_at(loc_id))
        return f"{loc.name}\n{loc.read_aloud.strip()}\n{people}"

    def _report(self, turn: Turn) -> str:
        st, m = self.state, self.m
        inv = st.investigator
        loc = m.location(st.location)
        out = [f"【第 {st.turn} 轮｜{st.clock}（{time_of_day(st.minutes)}）｜{loc.name}】", f"调查员：{inv.name}（{inv.occupation}）", world.context(st)]
        people = [n.name if st.npc(n.id).met else f"未通名的人（{n.look or '暂无外观记录'}）" for n in m.npcs_at(st.location)]
        if st.ongoing:
            out.append(f"【上一段叙述停在】{st.ongoing}")
        out.append("【当前在场人物，以记录为准】" + ("、".join(people) or "没有其他人")
                   + "。人物未出现在当前地点不代表失踪，不推断未观察到的去向；先前台词和旁白不能替代位置记录。")
        out.append(f"【实际持有】{'、'.join(inv.inventory) or '未登记随身物品'}（专用工具与新取得物品以持有记录为准。）")
        if st.sky and st.minutes - st.sky_at < weather.LASTS:
            out.append(f"天气：{weather.KINDS[st.sky]}（你之前写到的）")
        a = turn.action
        if turn.interlude:
            out.append("（插曲：调查员这回没有新的行动，下面这些事接着上一段叙述发生。）")
        if a and a.kind == "meta":
            out.append(f"玩家在问游戏外的问题：{a.summary}\n请以守秘人身份用一两句话简短回答，不推进剧情。")
            return "\n".join(out)
        if a:
            out.append(f"行动意图（是否达成以下列结算为准）：{a.summary}")
            for item in self._items_for(a):
                out.append(f"【眼前物件】{item.name}：{item.description}（配图卡已展示外观，接着动作讲，不另添隐藏标记或效果。）")
            if a.kind == "daily":
                out.append("【日常】接住玩家眼下的生活意图，写具体的人情和应答；不催查案、不强添线索或怪异。已触发的事件仍照实讲。")
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
                out.append("【发现边界】本轮没有结算新线索或新物证。只兑现已有资料支持的行动效果；"
                           "不能另加可追查的脚印、可拾取碎片、隐藏物件或陌生人的行踪来凑发现。")
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
        elif a and a.kind not in ("move",):
            out.append("检定：无需检定")
        if turn.push:
            opts = []
            if turn.push.get("can_push"):
                opts.append("孤注一掷")
            if turn.push.get("luck") is not None:
                opts.append(f"花 {turn.push['luck']} 点幸运")
            out.append(f"（玩家可以选择：{'或'.join(opts)}，结果下一步揭晓；现在只写失败的样子。）")
        for cid in turn.clues:
            c = m.clue(cid)
            out.append(f"获得线索〔{c.title}〕：{c.text.strip()}")
        for who, s in turn.sanity:
            out.append(f"理智：{who} {s.describe()}" + (f"；疯狂发作表（日文原文，请用中文演出）：{s.bout}" if s.bout else ""))
        if turn.npc_lines:
            names = "、".join(m.npc(npc_id).name for npc_id, _ in turn.npc_lines)
            out.append(f"【对话已展示】{names}已回应。不要引用、概述或解释刚才的对话，也不重复线索卡与检定卡；只叙述本轮另行发生的变化。")
        if turn.performance:
            out.append(f"NPC 表演（已展示给玩家）：{m.npc(turn.performance[0]).name} 刚刚说完这一段书。")
        for ev in turn.events:
            if ev.read_aloud.strip():
                out.append(f"眼前发生：{ev.read_aloud.strip()}")
        out += [f"眼前发生：{text}" for text in turn.world]
        out += [f"备注：{n}" for n in turn.notes]
        out += [f"规则提示（来自本局规则，这一轮的叙述要用一句话自然写到）：{h}" for h in turn.hints]
        if turn.ending:
            end = next(e for e in m.endings if e.id == turn.ending)
            out.append(f"【结局：{end.title}】{end.text.strip()}\n请用 150–250 字为这个结局收尾，不再留行动钩子。")
        elif turn.interlude:
            out.append("请用 60–150 字接着上一段写这些事，像“就在这时”一样自然地插进来，收在一个能行动的细节上。")
        else:
            out.append("请叙述。")
        return "\n".join(out)

    async def _narrate(self, report: str, on_delta=None, lore: str = "", turn: Turn | None = None) -> str:
        """The keeper tells it: first how things stand as the telling ends, then the words
        (agents/cast.Telling). The scene is compared with the record and lands as world operations
        (world/scene.py); the words stream to the player once it holds (Game._tell). Nothing reads
        the words, and nothing in them is cut or replaced (the user's call, 2026-10-20). The whole
        telling goes into the turn's journal (pending_turn["keeper"]) before it lands: a turn resumed
        after that lands it from there and does not ask the keeper again."""
        self.told = None  # while the keeper speaks there is nothing to tell again
        st = self.state
        sky = (st.sky, st.sky_at)
        receipt: dict = st.pending_turn.setdefault("keeper", {}) if st.pending_turn else {}

        async def delta(text: str) -> None:
            await self.emit({"type": "keeper_delta", "text": text})
            if on_delta:
                await on_delta(text)

        await self.emit({"type": "keeper_start"})
        if "text" in receipt:  # told before the turn was cut short: shown again, not asked again
            await delta(receipt["text"])
        else:
            receipt.update(await self._tell(report, lore, delta, turn))
            if self.checkpoint:
                self.checkpoint()
        text = receipt["text"]
        if not receipt.get("landed"):  # the record and the log together, with no await between
            landed = scene.land(self.world, [scene.Unit.load(u) for u in receipt.get("units", [])])
            if len(landed) < len(receipt.get("units", [])):
                self._debug("scene", {"phase": "stale", "units": receipt["units"], "landed": [u.key for u in landed]})
            st.ongoing = receipt.get("now", "")
            st.add("keeper", text, who="守秘人")
            receipt["landed"] = True
            if self.checkpoint:
                self.checkpoint()
        if receipt.get("room") and not receipt.get("walked"):
            await self._walked(receipt["room"])
            receipt["walked"] = True
            if self.checkpoint:
                self.checkpoint()
        await self._people()
        entry = next((e for e in reversed(st.log) if e.kind == "keeper" and e.text == text), None) or st.add("keeper", text, who="守秘人")
        await self.emit({"type": "keeper_end", "text": text})
        await self.showcases.named(text)  # a creature the narration names for the first time
        await self._sky(text)
        self.told = Told(report, lore + receipt.get("note", ""), entry, sky)
        return text

    async def _tell(self, report: str, lore: str, delta: Callable[[str], Awaitable[None]], turn: Turn | None) -> dict:
        """The keeper's telling and what of its scene lands, as the turn's journal keeps it. A scene
        that does not hold goes back once, with what did not hold and what could be written
        instead; failing again, the units that hold make up the scene, and the keeper writes the
        words to it, what did not hold as attempts. A keeper that answers in words alone (a test's,
        an old one's), or a turn told out of the story (a question to the KP, the ending) has no scene."""
        st = self.state
        keeper = self.cast.keeper
        words = {"units": [], "room": "", "now": "", "note": ""}
        if not _staged(turn) or not _takes(keeper, "check"):
            return words | {"text": await keeper(st, report, on_delta=delta, lore=lore)}
        ctx = self._scene_context(turn)
        brief = self._scene_brief(ctx)
        plans: list[scene.Plan] = []

        def check(raw: Any) -> bool:
            plans.append(scene.plan(self.world, raw, ctx))
            return not plans[-1].problems

        fix = ""
        for attempt in (1, 2):
            told = await keeper(st, report, on_delta=delta, lore=lore + brief, check=check, fix=fix)
            if isinstance(told, str):
                return words | {"text": told}
            if told.scene is None or not plans:  # no scene could be read: the words stand, nothing changes
                if not told.shown:
                    await delta(told.text)
                return words | {"text": told.text}
            p = plans[-1]
            if not p.problems or told.shown:
                if not told.shown:  # written words first: shown now that the scene holds
                    await delta(told.text)
                if p.problems:  # shown on a scene that held, then came back otherwise: what holds of it lands
                    self._debug("scene", {"phase": "kept", "problems": p.problems, "scene": told.scene})
                return self._staging(told.text, p, partial=bool(p.problems))
            self._debug("scene", {"phase": "rejected", "attempt": attempt, "problems": p.problems, "scene": told.scene})
            fix = scene.feedback(p)
        self._debug("scene", {"phase": "settled", "held": [u.key for u in p.held], "problems": p.problems})
        text = await keeper(st, report, on_delta=delta, lore=lore + scene.settled(p))
        return self._staging(text, p, partial=True)

    @staticmethod
    def _staging(text: str, p: scene.Plan, *, partial: bool) -> dict:
        """What the turn's journal keeps of a telling: its words, the units of its scene that land,
        where the investigators walked, what is going on as it ends (not when the scene was cut
        down: it may tell of what did not land), and the scene in words for telling it again."""
        return {"text": text, "units": [u.dump() for u in p.held], "room": p.room, "now": "" if partial else p.now,
                "note": scene.kept(p)}

    def _scene_context(self, turn: Turn | None) -> scene.Context:
        """What the keeper's scene is checked with: this turn's minutes so far (from the player's
        words, as the log has them), what the action lacks, whether the investigators acted, and
        the op ids not yet used this turn."""
        st, rec = self.state, self.world.rec
        acted = turn is not None and turn.action is not None and not turn.interlude
        said = next((e for e in reversed(st.log) if e.kind == "player" and e.turn == st.turn), None)
        try:
            start = parse_time(said.time) if said is not None else st.minutes
        except ValueError:
            start = st.minutes
        ids = f"keeper:{st.turn}"
        used = [int(n) for e in rec.journal if e.id.startswith(ids + ":") and (n := e.id.rsplit(":", 1)[1]).isdigit()]
        ruling = turn.ruling if turn is not None else None
        lacks = (getattr(ruling, "missing", None) or []) if ruling is not None else []
        return scene.Context(travel=self._travel, elapsed=max(0, st.minutes - start) if acted else 0,
                             missing=[_lack(x) for x in lacks], walk=acted, ids=ids, start=max(used, default=-1) + 1)

    def _scene_brief(self, ctx: scene.Context) -> str:
        """The scene as the record has it, for the keeper's to start from: where they are, who is
        with them and who walks with them (ids to write it with), and the minutes anyone could walk."""
        w, st = self.world, self.state
        party = {i.id for i in st.investigators}
        here = "、".join(f"{n}（{w.name(n)}{'，跟着调查员' if w.rec.entities[n].following in party else ''}）"
                        for n in w.present(st.location)) or "没有别人"
        lack = f"这次行动缺的{'、'.join(ctx.missing)}不能在 scene 里变出来。" if ctx.missing else ""
        return (f"\n\n【场面】先写 scene，是这段讲完时的样子，没变的照抄。现在：调查员在 {st.location}（{w.name(st.location)}），"
                f"在场 {here}。这一轮过了 {ctx.elapsed} 分钟，有人来去，路上的时间要在这里面。{lack}")

    async def _walked(self, room: str) -> None:
        """The investigators walked to another room of the place in the telling (world/scene.py
        moved them already): what coming there brings (its events, its first sight, its
        foreshadowing) happens now, and is told next."""
        came = Turn()
        await self._arrive(room, came)
        late = self._late
        for attr in ("events", "sanity", "hints", "world"):
            getattr(late, attr).extend(getattr(came, attr))
        late.notes += [n for n in came.notes if "一同抵达" not in n]
        if came.first_visit:
            loc = self.m.location(room)
            late.notes.append(f"上一段叙述里调查员已经走进{loc.name}，接着写，不必再写进门；这里初见的样子：{loc.read_aloud.strip()}")

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
        await self.emit({"type": "keeper_start", "again": True})

        async def delta(text: str) -> None:
            await self.emit({"type": "keeper_delta", "text": text})
            if on_delta:
                await on_delta(text)

        old = told.entry.text
        try:
            text = await self.cast.retell(st, told.report, old, extra=told.lore + _retold(old, nudge), on_delta=delta)
        except Exception:
            self.told = told
            await self.emit({"type": "keeper_end", "text": old})  # the old telling stands
            raise
        told.entry.text = text
        if st.prologue == old:  # the opening told again
            st.prologue = text
        await self.emit({"type": "keeper_end", "text": text})
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
# five minutes is fine). Plans that fail are written again until then; a first picture gets
# PREPARE_DRAWS tries before the scene goes on without it; PREPARE_PAUSE seconds (times the
# failures so far) pass before asking a director whose call failed outright again.
PREPARE_LIMIT = 300.0
PREPARE_DRAWS = 2
PREPARE_PAUSE = 5.0
NOWHERE = (
    "这一轮人还在原地：调查员说要去的地方，没人告诉过他们在哪，从这儿也找不到路（或者没说清去哪）。"
    "不要写他们到了别处。要是当地人人都知道的地方，可以写有人给他们指了路、说出地名，下一轮就能去；"
    "要是他们还没发现的地方，别说出、确认或暗示它的任何事"
)
STALL_TURNS = 12  # turns without a clue, flag, event or new place before the director sends a lead
MAX_FOUND = 2  # clues one action can turn up; a look around without a target, one
ACT_CAP = 180  # minutes one action may take
WAIT_CAP = 720  # minutes one wait or rest may skip: a night's sleep


WHY_LEN = 40  # characters of the arbiter's reason a roll prompt may show
_CLAUSE = re.compile(r"[，,；;。！!？?\n]")
# Backstage talk in a reason: ids and schema words, clues, gates, flags, rewards, dice mechanics.
_BACKSTAGE = re.compile(r"[A-Za-z_]{2,}|线索|门槛|解锁|标记|获得|取得|拿到|得到|找到|发现|给出|交出|模组|剧情|难度|检定|骰")


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
    return turn is None or not (turn.ending or (turn.action is not None and turn.action.kind == "meta"))


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


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _threshold(target: int, difficulty: str) -> int:
    return {"困难": target // 2, "极难": target // 5}.get(difficulty, target)


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
        "check": _check_view(s.check),
    }
