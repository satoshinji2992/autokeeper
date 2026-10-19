"""One game: deterministic resolution around the agents.

The engine owns the facts: dice (BCDice), which clues are found, time, events,
sanity and endings. Agents only ever turn words into structure (intent, arbiter)
or structure into words (NPCs, keeper).
"""

from __future__ import annotations

import asyncio
import heapq
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from .. import weather
from ..agents import guard
from ..agents.cast import Action, Cast, Ruling
from ..agents.director import Improv
from ..harness.replay import CassetteError
from ..module import improv
from ..module.schema import Event, Item, Module
from ..plugins.engine import Manager as PluginManager
from ..rules import commands
from ..rules.bcdice import Level
from ..rules.catalog import canonical
from ..rules.coc import DIFFICULTY_ZH, Check, Investigator, Rules, Sanity
from . import fog, progress, world
from .state import KEEP_CHECKS, RECHECK_AFTER, CheckRecord, GameState, LogEntry, Progress, parse_time, time_of_day
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
    ending: str | None = None
    interlude: bool = False  # no action of theirs: the director's scene comes in between turns


@dataclass
class Backstage:
    """A director's plan still being written after its turn moved on."""

    kind: str
    task: asyncio.Task
    at: str  # where the investigators were when it was asked for
    turn: int = 0


@dataclass
class Told:
    """The latest narration, kept so it can be told again: what the keeper was given (the
    report and that turn's lore), and what telling it left behind (its log entry, and the
    sky as it was before, since the words set the weather)."""

    report: str
    lore: str
    entry: LogEntry
    sky: tuple[str, int]


class Game:
    def __init__(self, module: Module, state: GameState, cast: Cast, rules: Rules, emit: Emit | None = None):
        self.m, self.state, self.cast, self.rules = module, state, cast, rules
        self.emit: Emit = emit or _silent
        self.checkpoint: Callable[[], None] | None = None
        self.turn_checkpoint: Callable[[Turn], None] | None = None
        if state.improv:  # a loaded game brings its own branches
            improv.restore(module, state.improv)
        # How long a turn waits for the director before the keeper goes on without it. None
        # waits for the plan (autoplay and replays: no one is waiting, and replays must not
        # depend on timing); a screen sets PLAY_WAIT.
        self.director_wait: float | None = None
        self.backstage: list[Backstage] = []
        self._late = Turn()  # what landed from backstage, told in the next narration
        self.told: Told | None = None  # the latest narration, while it can still be told again (retell)
        # The players' 故事至今 (engine/progress.py), written in the background when the story
        # has moved. Off in autoplay and replays, where nobody reads it; a screen and the Hub
        # turn it on.
        self.write_progress = False
        self._recounting: asyncio.Task | None = None
        self.listener: Emit = self.emit  # the screen's own: background work outlives a turn's stream
        self.visuals = Visuals(module, state, getattr(cast, "cfg", None), self.emit)
        self.plugins = PluginManager(self)
        self.visuals.schedule()

    async def close(self) -> None:
        """Stop work owned by this game before the model sessions and screen disappear."""
        for pending in self.backstage:
            pending.task.cancel()
        recounting = [self._recounting] if self._recounting else []
        for task in recounting:
            task.cancel()
        await asyncio.gather(*(p.task for p in self.backstage), *recounting, return_exceptions=True)
        self.backstage.clear()
        self._recounting = None
        await self.visuals.close()

    async def replace_cast(self, cast: Cast) -> None:
        """A model switch also replaces the game's outstanding background work."""
        await self.close()
        self.cast = cast
        self.visuals.cfg = getattr(cast, "cfg", None)
        self.visuals.tasks.clear()
        self.visuals.schedule()

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
        return state

    async def start(self, on_delta=None) -> str:
        st, m = self.state, self.m
        st.pending_turn = {"input": "/opening", "opening": True}
        self.visuals.pause()
        if self.checkpoint:
            self.checkpoint()
        # The director may rework the module around these investigators. At a screen the
        # opening does not wait for it: the adaptation lands at the start of a later turn.
        adapted = await self._improvise("开局改编", patience=0.0) if not st.improv else None
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
        text = await self._narrate(report, on_delta)
        st.prologue = text  # the 背景 page keeps it after the log has moved on
        st.progress.marks = progress.marks(st)  # milestones count from here
        st.heard = fog.heard(m, st)  # the places the opening named reach the screens at a shared table
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
        self.visuals.schedule()  # resumes unfinished illustration work after loading, including MCP play
        st = self.state
        st.turn += 1
        st.add("player", text, who=st.investigator.name)
        await self.emit({"type": "status", "text": "解析行动…"})
        action = await self.cast.intent(st, text)
        # What a dice command asks for is read off the command, never taken from the model.
        action = action.model_copy(update={"asked": commands.asked(text)})
        if (action.kind == "daily" and action.npc and not action.approach and not action.destination
                and re.fullmatch(r"[，。！？、\s]*(早上好|早安|你好|您好|午安|晚安|谢谢|多谢|辛苦了|今天忙不忙|平常这个时候忙不忙)[，。！？、\s]*", action.speech)):
            action = action.model_copy(update={"casual": True})
        if action.kind == "daily":
            st.progress_at = st.turn  # do not suggest the player is stuck while choosing everyday life
        if action.echo and action.kind != "meta":  # something on screen at once, while the rest is worked out
            await self.emit({"type": "echo", "text": action.echo})
        return action

    async def rule(self, action: Action) -> Ruling | None:
        await self._curtain()  # finished plans land before this action is ruled
        if action.kind in ("meta",):
            return None
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
        ruling = await self.cast.arbiter(self.state, action, at=heading)
        if ruling.scope != "模组内":  # the module is silent here: the director fills the gap
            kind = "展开" if ruling.scope == "可即兴" else "后果"
            if kind != "展开" or self._may_expand(action, heading):
                waiting = len(self.backstage)
                applied = await self._improvise(kind, action=action, ruling=ruling, at=heading or self.state.location)
                if applied is not None:
                    ruling = self._steer(ruling, applied)
                elif len(self.backstage) > waiting:
                    ruling = ruling.model_copy(update={"note": LATER})
        return self._no_reroll(action, ruling, heading or self.state.location)

    def _may_expand(self, action: Action, heading: str | None) -> bool:
        """The director opens a new line (展开) in one place at most once in EXPAND_ROUNDS rounds.
        The arbiter finds plenty unwritten (a question to someone not there, a fence to climb),
        and without a cap one room was rewritten turn after turn: 29 of 31 director calls in
        an 80-turn game. Moving on is never held back, to a place the module has or one it never
        wrote; stuck-pushes, consequences and the opening adaptation are not 展开 at all."""
        st = self.state
        if action.kind == "move" or heading:
            return True
        last = st.expanded.get(st.location)
        acting = max(1, sum(1 for inv in st.investigators if inv.can_act))  # a round: each of them acts once
        return last is None or st.turn - last >= EXPAND_ROUNDS * acting

    # -- no re-rolls ------------------------------------------------------------------
    def _no_reroll(self, action: Action, ruling: Ruling, at: str) -> Ruling:
        """CoC does not repeat a check: a failure may be pushed once, not rolled again while
        nothing has changed. The arbiter sees the recent checks and marks a repeat; the engine
        catches the plain re-roll even when the arbiter does not."""
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
        return last if same and quiet else None

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
        self, kind: str, *, action: Action | None = None, ruling: Ruling | None = None, at: str = "", patience: float | None = None
    ) -> improv.Applied | None:
        """Ask the director for a plan and write it into this game's module. A plan that takes
        longer than the turn can wait keeps being written backstage and lands at the start of
        a later turn (_curtain); meanwhile, and without a usable plan, the turn goes on."""
        st = self.state
        if not self.improvising:
            return None
        if len(self.backstage) >= BACKSTAGE_LIMIT:  # enough is being written already
            return None
        await self.emit({"type": "status", "text": "守秘人在想…"})
        where = at or st.location
        if kind == "展开":  # asked for, whatever comes of it (_may_expand)
            st.expanded[where] = st.turn
        task = asyncio.create_task(self._plan(kind, action=action, ruling=ruling, at=where))
        wait = self.director_wait if patience is None or self.director_wait is None else min(patience, self.director_wait)
        if wait is None:
            plan = await task
        else:
            try:
                plan = await asyncio.wait_for(asyncio.shield(task), wait)
            except TimeoutError:
                self.backstage.append(Backstage(kind, task, where, self.state.turn))
                return None
        return await self._land(kind, plan) if plan is not None else None

    async def _plan(self, kind: str, *, action: Action | None, ruling: Ruling | None, at: str) -> Improv | None:
        """The director's plan, checked, with one chance to fix it; None without a usable one."""
        said = f"{action.summary}（{action.kind}，对象：{action.target or '无'}）" if action else ""
        problems: list[str] = []
        for _ in range(2):
            try:
                plan = await self.cast.director(self.state, kind, action=said, reason=ruling.reason if ruling else "", at=at, problems=problems)
            except CassetteError:  # a replay that drifted must fail loudly
                raise
            except Exception:  # improvising is a bonus; a failed call must not cost the turn
                return None
            if plan is None or plan.kind == "不需要":
                return None
            plan.kind = kind  # type: ignore[assignment]
            problems = improv.check(self.m, plan, at=at)
            if not problems:
                from ..plugins.runtime import verify

                for plugin in plan.plugins:
                    problems += [f"插件 {plugin.id}：{problem}" for problem in await verify(plugin)]
            if not problems:
                return plan
        return None

    async def _land(self, kind: str, plan: Improv) -> improv.Applied:
        applied = improv.apply(self.m, plan)
        for plugin in plan.plugins:
            self.state.plugin_sources[plugin.id] = plugin.model_dump(mode="json")
        self.state.improv.append({"turn": self.state.turn, "kind": kind, "plan": plan.model_dump()})
        self.visuals.schedule()
        await self.emit({"type": "improv", "kind": kind, "summary": applied.summary, "reason": plan.reason})
        return applied

    def ready(self) -> bool:
        """A plan is finished backstage and can take the stage."""
        return any(b.task.done() for b in self.backstage)

    async def interlude(self) -> Turn | None:
        """Between turns: a plan the director has finished plays out at once, told in a short
        narration of its own, so no one waits for their next action to see it."""
        if self.state.ending or not self.ready():
            return None
        await self._curtain()
        turn, self._late = self._late, Turn()
        if not (turn.events or turn.clues or turn.notes or turn.arrived or turn.sanity):
            return None
        turn.interlude = True
        await self._time(turn)
        await self._check_ending(turn)
        await self.narrate(turn)
        await self.remember()
        return turn

    async def _curtain(self) -> None:
        """Plans finished backstage take the stage before the next action is ruled: their places
        and people exist from now on, their scenes play out, and the next narration tells it."""
        st, late = self.state, self._late
        for b in [b for b in self.backstage if b.task.done()]:
            self.backstage.remove(b)
            plan = None if b.task.cancelled() or b.task.exception() else b.task.result()
            if (plan is None or st.location != b.at or st.turn > b.turn + 1
                    or improv.check(self.m, plan, at=b.at)):
                continue
            applied = await self._land(b.kind, plan)
            if applied.go and applied.go != st.location and st.location == b.at:  # they were on their way
                minutes = self._travel(st.location, applied.go)
                if minutes is not None:
                    st.minutes += minutes
                    await self._arrive(applied.go, late)
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

    async def _fail_forward(self, ids: list[str], turn: Turn, *, pushed: bool = False) -> None:
        """Do not sit on clues, do not hand them out either. A core clue missed once stays where
        it is, and the keeper shows how close they came; missed again, or after a pushed roll
        failed, it turns up at a price the keeper has to tell (time lost, noise made, something
        given away)."""
        st = self.state
        core = [c for c in ids if self.m.clue(c).core and c not in st.clues]
        if not core:
            return
        for c in core:
            st.missed[c] = st.missed.get(c, 0) + 1
        due = [c for c in core if pushed or st.missed[c] >= 2]
        if not due:
            turn.notes.append("检定没过：要找的东西还在原处，写出他差一点就发现的感觉（哪里不对劲、还没摸到门道），不说出内容；换个办法或再仔细查一次还有机会")
            return
        st.minutes += 20
        await self._gain(due, turn, turn.action)
        turn.notes.append("检定又没过，但关键线索不能卡住：线索照样到手，叙事里要写出代价（多花了时间、弄出了动静或留下了把柄）")

    async def _nudge(self, turn: Turn) -> None:
        """Stuck for a while: the director sends a lead."""
        st = self.state
        if turn.action and turn.action.kind == "daily":
            return
        if st.ending or st.turn - st.progress_at < STALL_TURNS:
            return
        applied = await self._improvise("推进")
        st.progress_at = st.turn  # whatever came of it, do not ask again next turn
        if applied is None:
            return
        await self._flags(applied.now, turn)
        await self._gain(self._valid_clues(applied.give, turn.action), turn, turn.action)
        if applied.note:
            turn.notes.append(f"导演的安排：{applied.note}")

    def _heading(self, action: Action) -> str | None:
        """Where an action happens when the investigator walks somewhere to do it ("I go up to
        the tomb door and check the seams"): a reachable place other than here, which they know
        of or can find from here (engine/fog.py)."""
        dst = action.destination
        if action.kind == "move" or not dst or dst == self.state.location or self._travel(self.state.location, dst) is None:
            return None
        return dst if fog.findable(self.m, self.state, dst) else None

    async def resolve(self, action: Action, ruling: Ruling | None) -> Turn:
        st, _m = self.state, self.m
        turn = Turn(action=action, ruling=ruling)
        late, self._late = self._late, Turn()  # what the director's late plans brought on stage
        turn.events += late.events
        turn.clues += late.clues
        turn.sanity += late.sanity
        turn.notes += late.notes
        turn.arrived, turn.first_visit = late.arrived, late.first_visit
        inv = st.investigator
        if action.kind == "meta" or ruling is None:
            return turn

        # movement: a plain move, walking somewhere to do something there (ruled there), or the
        # director sending them somewhere new. A place they have not heard of is found only from
        # next door: a name guessed from across town takes no one anywhere (engine/fog.py).
        heading = ruling.go or (action.destination if action.kind == "move" else self._heading(action))
        heading = None if heading == st.location else heading
        if heading and heading != ruling.go and not fog.findable(self.m, st, heading):
            heading = None
        if not heading and not ruling.go and (action.kind == "move" or action.destination) and action.destination != st.location:
            turn.notes.append(NOWHERE)  # the keeper must not walk them there in words either
        if heading:
            intended = heading
            stopping = self._stop_on_route(action) and heading != ruling.go
            if stopping:
                heading = self._route(st.location, heading)[1]
                st.journey = intended
            minutes = self._travel(st.location, heading)
            if minutes is None:
                turn.notes.append(f"去不了“{heading}”")
            else:
                turn.route = self._route(st.location, heading) or []
                st.minutes += minutes
                await self._arrive(heading, turn)
                if intended == heading:
                    st.journey = ""
                if stopping:
                    turn.notes.append(f"路途暂歇，尚未抵达{self.m.location(intended).name}；只描写眼前，不能替玩家继续赶路或执行目的地的行动。")
                    await self.plugins.run("turn", turn, action=action.model_dump(mode="json"))
                    await self._time(turn)
                    await self._check_ending(turn)
                    return turn

        # the check: rolled once; a repeat is not rolled at all and gains nothing new
        passed = not ruling.needs_roll and not ruling.repeat
        if ruling.repeat:
            turn.earlier = e = self._earlier(action, ruling, st.location)
            if e is not None:  # tried again just now, to the same effect: the latest attempt
                st.checks.remove(e)
                st.checks.append(e)
                e.turn = st.turn
                await self.emit({"type": "repeat", "who": inv.name, "skill": e.skill, "text": e.text, "passed": e.passed})
        elif action.asked and not (ruling.needs_roll and ruling.skill) and action.kind != "move":
            await self.emit({"type": "waived", "who": inv.name, "skill": canonical(action.asked.skill)})
        elif ruling.needs_roll and ruling.skill:
            check = self.rules.check(inv, ruling.skill, DIFFICULTY_ZH.get(ruling.difficulty, Level.REGULAR), ruling.bonus)
            turn.checks.append(check)
            passed = check.passed
            self._record(action, check)
            st.add("dice", check.describe(), who=inv.name)
            await self.emit({"type": "dice", "who": inv.name, **_check_view(check)})
            need = check.luck_needed()
            if not passed and (check.can_push or need is not None):
                wanted = self._valid_clues(ruling.clues_on_success, action)
                if wanted:
                    luck = need if need is not None and need <= inv.luck else None
                    turn.push = {
                        "skill": check.name,
                        "target": check.target,
                        "need": _threshold(check.target, ruling.difficulty),
                        "difficulty": ruling.difficulty,
                        "bonus": ruling.bonus,
                        "clues": wanted,
                        "flags": ruling.set_flags,
                        "can_push": check.can_push,
                        "luck": luck,
                        "left": inv.luck - (luck or 0),  # luck after paying for it
                        "core": any(self.m.clue(c).core for c in wanted),
                        # missed once before: dropping it now finds it at a price (_fail_forward)
                        "due": any(self.m.clue(c).core and st.missed.get(c, 0) for c in wanted),
                        "npc": action.npc if action.kind in ("talk", "perform") else "",
                        "value": check.value,
                    }
                    st.pending_push = turn.push
            if not passed and turn.push is None:  # no second chance offered: do not sit on core clues
                await self._fail_forward(self._valid_clues(ruling.clues_on_success, action), turn)

        known = set(st.flags)
        gained = self._valid_clues(ruling.auto_clues, action)
        if passed:
            gained += [c for c in self._valid_clues(ruling.clues_on_success, action) if c not in gained]
            await self._flags(ruling.set_flags, turn)
            world.apply(self.m, st, ruling.changes, source="arbiter")
        await self._gain(gained[: MAX_FOUND if action.target else 1], turn, action)  # one look around does not empty a room
        if turn.checks:  # what the roll led to, written under it
            won = [c for c in turn.clues if c in ruling.clues_on_success]
            done = [f for f in ruling.set_flags if f in st.flags and f not in known]
            npc = action.npc if action.kind in ("talk", "perform") else ""
            await self.emit({"type": "outcome", "who": inv.name, "lines": self._led_to(won, done, passed, npc, offer=turn.push)})
        if ruling.now:  # the director's scene plays out, whatever the dice said
            await self._flags(ruling.now, turn)
        if ruling.note:
            turn.notes.append(f"导演的安排：{ruling.note}")

        # A night's sleep is legitimate; anything else that claims hours is capped. The arbiter
        # says when it is a rest: "先回去睡一觉" said to a partner parses as talk, not wait.
        resting = action.kind == "wait" or ruling.rest
        st.minutes += max(0, min(ruling.minutes, WAIT_CAP if resting else ACT_CAP))
        if passed:
            for call in ruling.plugin_calls:
                await self.plugins.run("action", turn, **call.model_dump(mode="json"))
        await self.plugins.run("turn", turn, action=action.model_dump(mode="json"))
        await self._time(turn)
        if action.kind == "daily":
            st.progress_at = st.turn
        if self.improvising:
            await self._nudge(turn)
        await self._check_ending(turn)
        for item in self._items_for(action):
            await self.emit({"type": "item", "id": item.id, "name": item.name, "text": item.description, "art": item.art})
        if turn.checks or turn.earlier:
            self._settle(st.checks[-1])
        return turn

    async def push(self, mode: str) -> Turn:
        """Resolve a pending push or luck spend: mode is 'push', 'luck' or 'drop'."""
        st = self.state
        pending, st.pending_push = st.pending_push, None
        turn = Turn()
        if not pending:
            return turn
        record = st.checks[-1] if st.checks and st.checks[-1].turn == st.turn else None  # the check on offer
        inv, npc, known = st.investigator, pending.get("npc", ""), set(st.flags)
        if mode == "drop":
            await self._fail_forward(pending["clues"], turn)
            won = [c for c in turn.clues if c in pending["clues"]]
            await self.emit({"type": "outcome", "who": inv.name, "lines": self._led_to(won, [], False, npc) if won else ["放弃了，换条路"]})
            self._settle(record)
            return turn
        diff = DIFFICULTY_ZH.get(pending["difficulty"], Level.REGULAR)
        lucky = mode == "luck" and pending.get("luck") is not None
        if lucky:
            inv.luck -= pending["luck"]
            passed = True
            st.add("dice", f"花费幸运 {pending['luck']} 点，{pending['skill']} 检定改为成功", who=inv.name)
            await self.emit({"type": "luck", "who": inv.name, "spent": pending["luck"], "left": inv.luck})
            self._settle(record, True, f"{record.text}，花 {pending['luck']} 点幸运改为成功" if record else "")
        else:
            check = self.rules.check(inv, pending["skill"], diff, pending["bonus"], pushed=True, target=pending["target"])
            turn.checks.append(check)
            passed = check.passed
            st.add("dice", check.describe(), who=inv.name)
            await self.emit({"type": "dice", "who": inv.name, **_check_view(check)})
            self._settle(record, check.passed, check.describe())
            if not passed:
                turn.notes.append("孤注一掷失败：兑现检定前已明确的风险，后果与行动相称，不另加无关惩罚")
        if passed:
            await self._flags(pending.get("flags", []), turn)
            await self._gain(pending["clues"], turn, None)
        else:
            await self._fail_forward(pending["clues"], turn, pushed=True)  # a pushed roll was the last chance: the clue comes, at a price
        won = [c for c in turn.clues if c in pending["clues"]]
        done = [f for f in pending.get("flags", []) if f in st.flags and f not in known]
        lines = self._led_to(won, done, passed, npc)
        if lucky:
            lines.insert(0, f"花了 {pending['luck']} 点幸运，算作成功")
        elif not passed:
            lines.insert(0, "孤注一掷也没成：后果会找上门")
        await self.emit({"type": "outcome", "who": inv.name, "lines": lines})
        await self._check_ending(turn)
        self._settle(record)
        return turn

    async def talk(self, turn: Turn) -> None:
        """NPC replies and stage performances, after the engine has resolved checks."""
        st, m = self.state, self.m
        action = turn.action
        speaking = (action and action.npc and (action.kind in ("talk", "perform")
                    or (action.kind == "daily" and (action.speech or action.casual))))
        if speaking:
            if any(nid == action.npc for nid, _ in turn.npc_lines):
                pass  # a resumed narration never asks an NPC to answer the same question twice
            elif not any(n.id == action.npc for n in m.npcs_at(st.location)):
                turn.notes.append(f"{action.npc} 不在这里")
            else:
                npc = m.npc(action.npc)
                await self.emit({"type": "status", "text": f"{npc.name}正在开口…"})
                line = await self.cast.npc(st, npc.id, self._npc_message(turn, npc.id))
                turn.npc_lines.append((npc.id, line))
                st.npc(npc.id).met = True
                st.add("npc", line, who=npc.name)
                if self.turn_checkpoint:
                    self.turn_checkpoint(turn)
                await self.emit({"type": "npc", "npc": npc.id, "name": npc.name, "text": line, "portrait": m.art.get(npc.portrait or "", "")})
        for ev in turn.events:
            if ev.performer and ev.id not in turn.performed:
                npc = m.npc(ev.performer)
                await self.emit({"type": "status", "text": f"{npc.name}登台了…"})
                text = await self.cast.perform(st, npc.id, ev.performance, self._deeds())
                turn.performance = (npc.id, text)
                st.npc(npc.id).met = True
                st.add("npc", text, who=npc.name)
                turn.performed.append(ev.id)
                if self.turn_checkpoint:
                    self.turn_checkpoint(turn)
                await self.emit({"type": "performance", "npc": npc.id, "name": npc.name, "text": text, "portrait": m.art.get(npc.portrait or "", "")})

    async def narrate(self, turn: Turn, on_delta=None) -> str:
        if (turn.action and turn.action.kind == "daily" and turn.npc_lines and not turn.events
                and not turn.clues and not turn.notes and not turn.ending and not turn.arrived
                and not turn.checks and not (turn.ruling and (turn.ruling.changes or turn.ruling.set_flags))):
            return ""  # their reply is the scene; no second model retells a greeting
        return await self._narrate(self._report(turn), on_delta)

    async def remember(self) -> None:
        st = self.state
        st.tally()
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
        why = "正在整理，稍等" if self._writing() else progress.refused(self.state)
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
            item for item in self.m.items if world.owner(self.m, self.state, item.id) in (self.state.location, self.state.investigator.id)
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

    async def _arrive(self, loc_id: str, turn: Turn) -> None:
        st, m = self.state, self.m
        st.location = loc_id
        turn.arrived = loc_id
        turn.first_visit = loc_id not in st.visited
        if turn.first_visit:
            st.visited.append(loc_id)
            st.progress_at = st.turn
        loc = m.location(loc_id)
        await self.emit({"type": "move", "location": loc.id, "name": loc.name, "art": m.art.get(loc.art or "", "")})
        for ev in m.events:
            if ev.id not in st.fired and ev.when == f"visit:{loc_id}":
                await self._fire(ev, turn)
        await self.plugins.run("visit", turn, location=loc_id)

    def _valid_clues(self, ids: list[str], action: Action | None) -> list[str]:
        st, m = self.state, self.m
        here = {c.id for c in m.clues_at(st.location)}
        for n in m.npcs_at(st.location):
            here |= set(n.clues)
        known = {c.id for c in m.clues if c.available(st.flags, st.clues)}
        return [c for c in dict.fromkeys(ids) if c in known and c in here and c not in st.clues]

    async def _gain(self, ids: list[str], turn: Turn, action: Action | None) -> None:
        st, m = self.state, self.m
        for cid in ids:
            if cid in st.clues:
                continue
            clue = m.clue(cid)
            if not clue.available(st.flags, st.clues):
                continue
            st.clues.append(cid)
            st.progress_at = st.turn
            turn.clues.append(cid)
            if clue.npc:
                unlocked = st.npc(clue.npc).unlocked
                if cid not in unlocked:
                    unlocked.append(cid)
            st.add("clue", f"〔{clue.title}〕{clue.text.strip()}")
            await self.emit({"type": "clue", "id": cid, "title": clue.title, "text": clue.text.strip()})
            if clue.handout and clue.handout not in st.handouts:
                h = m.handout(clue.handout)
                st.handouts.append(h.id)
                st.add("handout", h.title)
                await self.emit({"type": "handout", "id": h.id, "title": h.title, "kind": h.kind, "text": h.text})
            if clue.sanity:
                ok, bad = clue.sanity.split("/")
                inv = st.investigator
                s = self.rules.sanity(inv, ok, bad)
                turn.sanity.append((inv.name, s))
                st.add("sanity", s.describe(), who=inv.name)
                await self.emit({"type": "sanity", "who": inv.name, **_sanity_view(s)})
        if turn.clues:
            await self.emit({"type": "sheet"})

    async def _flags(self, flags: list[str], turn: Turn) -> None:
        st, m = self.state, self.m
        for f in flags:
            if f in m.flags and f not in st.flags:
                st.flags.append(f)
                st.progress_at = st.turn
                await self.emit({"type": "flag", "flag": f, "meaning": m.flags[f]})
                for ev in m.events:
                    if ev.id not in st.fired and ev.when == f"flag:{f}":
                        await self._fire(ev, turn)

    async def _time(self, turn: Turn | None = None) -> None:
        st, m = self.state, self.m
        turn = turn or Turn()
        due = sorted(
            (ev for ev in m.events if ev.id not in st.fired and re.fullmatch(r"D\d+ \d{1,2}:\d{2}", ev.when)),
            key=lambda e: parse_time(e.when),
        )
        for ev in due:
            if parse_time(ev.when) <= st.minutes:
                await self._fire(ev, turn)
        await self.emit({"type": "time", "clock": st.clock, "phase": time_of_day(st.minutes)})

    async def _fire(self, ev: Event, turn: Turn) -> None:
        st, _m = self.state, self.m
        st.fired.append(ev.id)
        st.progress_at = st.turn
        turn.events.append(ev)
        st.add("event", ev.read_aloud.strip() or ev.title)
        await self.emit({"type": "event", "id": ev.id, "title": ev.title, "text": ev.read_aloud.strip()})
        if ev.move_to and ev.move_to != st.location:
            await self._arrive(ev.move_to, turn)
        if ev.sanity:
            ok, bad = ev.sanity.split("/")
            for inv in st.investigators:
                if not inv.can_act:
                    continue
                s = self.rules.sanity(inv, ok, bad)
                turn.sanity.append((inv.name, s))
                st.add("sanity", s.describe(), who=inv.name)
                await self.emit({"type": "sanity", "who": inv.name, **_sanity_view(s)})
        if ev.damage:  # the one who acted pays for it
            inv = st.investigator
            hurt = self.rules.damage(inv, ev.damage)
            st.add("damage", hurt.describe(), who=inv.name)
            turn.notes.append(f"{inv.name}{hurt.describe()}")
            await self.emit({"type": "damage", "who": inv.name, "text": hurt.describe()})
        for f in ev.sets:
            if f not in st.flags:
                st.flags.append(f)
        if ev.ending and not st.ending:
            await self._check_ending(turn)
            if not st.ending:
                await self._end(ev.ending, turn)

    async def _check_ending(self, turn: Turn) -> None:
        st, m = self.state, self.m
        if st.ending:
            return
        for end in m.endings:
            if end.condition.startswith("flag:") and end.condition[5:] in st.flags:
                await self._end(end.id, turn)
                return

    async def _end(self, ending_id: str, turn: Turn) -> None:
        st, m = self.state, self.m
        end = next(e for e in m.endings if e.id == ending_id)
        st.ending = end.id
        turn.ending = end.id
        notes = []
        for inv in st.investigators:
            gain = re.search(r"理智\s*\+\s*(\d*[dD]\d+|\d+)", end.reward)
            if gain and inv.can_act:
                n = self.rules.dice.total(gain.group(1).upper()) if "D" in gain.group(1).upper() else int(gain.group(1))
                inv.san = min(inv.san_max, inv.san + n)
                notes.append(f"{inv.name} 理智 +{n}")
        st.add("ending", f"{end.title}：{end.text.strip()}")
        await self.emit({"type": "ending", "id": end.id, "title": end.title, "text": end.text.strip(), "reward": end.reward, "notes": notes})

    def _npc_message(self, turn: Turn, npc_id: str) -> str:
        st, m = self.state, self.m
        inv = st.investigator
        action = turn.action
        assert action is not None
        said = f"对你说：「{action.speech}」" if action.speech else f"找上你：{action.summary}"  # a dice command says nothing
        parts = [f"（系统：{st.clock}，{m.location(st.location).name}。{inv.name}（{inv.occupation}）{said}"]
        if action.approach:
            parts.append(f"方式：{action.approach}。")
        mine = [c for c in turn.clues if m.clue(c).npc == npc_id or c in m.npc(npc_id).clues]
        if turn.checks:
            c = turn.checks[-1]
            parts.append(f"检定：{c.name} {c.level.zh}，{'你被打动了或被拆穿了' if c.passed else '你没有被打动'}。")
        elif turn.earlier is not None:
            e = turn.earlier
            moved = "你已经被打动了或被拆穿了" if e.passed else "你还是没有被打动"
            parts.append(f"检定：同样的{e.skill}刚才已经试过，不重掷，结果照旧：{moved}。")
        if mine:
            parts.append("已解锁，现在可以说出：" + "；".join(f"{cid}「{m.clue(cid).title}」" for cid in mine) + "。")
        else:
            parts.append("这一轮没有新解锁的线索，别说出门槛线索的内容。")
        parts.append("用你的口吻回答。）")
        return "".join(parts)

    def _deeds(self) -> str:
        st = self.state
        rows = [f"[{e.time}] {e.who}：{e.text}" for e in st.log if e.kind == "player"]
        rows += [f"[{e.time}] 线索：{e.text[:40]}" for e in st.log if e.kind == "clue"]
        head = f"（更早的经过：{st.memory}）\n" if st.memory else ""
        return head + "\n".join(sorted(rows)[-30:])

    def _public_location(self, loc_id: str) -> str:
        loc = self.m.location(loc_id)
        people = "\n".join(f"{n.name}：{n.look}" for n in self.m.npcs_at(loc_id))
        return f"{loc.name}\n{loc.read_aloud.strip()}\n{people}"

    def _report(self, turn: Turn) -> str:
        st, m = self.state, self.m
        inv = st.investigator
        loc = m.location(st.location)
        out = [f"【第 {st.turn} 轮｜{st.clock}（{time_of_day(st.minutes)}）｜{loc.name}】", f"调查员：{inv.name}（{inv.occupation}）", world.context(st)]
        if st.sky and st.minutes - st.sky_at < weather.LASTS:
            out.append(f"天气：{weather.KINDS[st.sky]}（你之前写到的）")
        a = turn.action
        if turn.interlude:
            out.append("（插曲：调查员这回没有新的行动，下面这些事接着上一段叙述发生。）")
        if a and a.kind == "meta":
            out.append(f"玩家在问游戏外的问题：{a.summary}\n请以守秘人身份用一两句话简短回答，不推进剧情。")
            return "\n".join(out)
        if a:
            out.append(f"行动：{a.summary}")
            for item in self._items_for(a):
                out.append(f"【眼前物件】{item.name}：{item.description}（配图卡已展示外观，接着动作讲，不另添隐藏标记或效果。）")
            if a.kind == "daily":
                out.append("【日常】接住玩家眼下的生活意图，写具体的人情和应答；不催查案、不强添线索或怪异。已触发的事件仍照实讲。")
            if a.echo:
                out.append(f"屏幕上已经先写了一句：「{a.echo}」，从这里接着往下写，不要重复它。")
        if len(turn.route) > 2:
            out.append("【沿途】" + " → ".join(m.location(pid).name for pid in turn.route))
            out += [f"路过{m.location(pid).name}：{m.location(pid).read_aloud.strip()}" for pid in turn.route[1:-1]]
            out.append("按行进顺序带出一两处路边生活，再衔接目的地；只是路过，没有进门调查、获得线索或与人交谈。")
        if turn.arrived:
            if turn.first_visit:
                out.append(f"【进入新地点】\n{self._public_location(turn.arrived)}")
            else:
                out.append(f"回到了 {m.location(turn.arrived).name}。")
        if turn.checks:
            out += [f"检定：{c.describe()}" for c in turn.checks]
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
        for npc_id, line in turn.npc_lines:
            out.append(f"NPC 台词（已展示给玩家）：{m.npc(npc_id).name}：{line}")
        if turn.performance:
            out.append(f"NPC 表演（已展示给玩家）：{m.npc(turn.performance[0]).name} 刚刚说完这一段书。")
        for ev in turn.events:
            if ev.read_aloud.strip():
                out.append(f"眼前发生：{ev.read_aloud.strip()}")
        out += [f"备注：{n}" for n in turn.notes]
        if turn.ending:
            end = next(e for e in m.endings if e.id == turn.ending)
            out.append(f"【结局：{end.title}】{end.text.strip()}\n请用 150–250 字为这个结局收尾，不再留行动钩子。")
        elif turn.interlude:
            out.append("请用 60–150 字接着上一段写这些事，像“就在这时”一样自然地插进来，收在一个能行动的细节上。")
        else:
            out.append("请叙述。")
        return "\n".join(out)

    async def _narrate(self, report: str, on_delta=None, lore: str = "") -> str:
        self.told = None  # while the keeper speaks there is nothing to tell again
        sky = (self.state.sky, self.state.sky_at)
        await self.emit({"type": "keeper_start"})

        async def delta(text: str) -> None:
            await self.emit({"type": "keeper_delta", "text": text})
            if on_delta:
                await on_delta(text)

        text = await self.cast.keeper(self.state, report, on_delta=delta, lore=lore)
        entry = self.state.add("keeper", text, who="守秘人")
        await self.emit({"type": "keeper_end", "text": text})
        await self._sky(text)
        self.told = Told(report, lore, entry, sky)
        return text

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
            return "现在没有能重讲的段落：守秘人还没讲完，或者读档以后还没讲过新的一段"
        at = next((i for i in range(len(log) - 1, -1, -1) if log[i] is told.entry), None)
        if at is None or any(e.kind != "dice" for e in log[at + 1 :]):
            return "只能重讲刚讲完的那一段，之后已经有人行动了"
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
EXPAND_ROUNDS = 3  # rounds before the same place may 展开 again (Game._may_expand)
# A turn at a screen does not wait for the director: measured, a plan takes 9 s and more
# (DeepSeek) or 14 s (Opus), so any wait shorter than that only made turns slower. Plans
# land in an interlude as soon as they are ready (Game.interlude).
PLAY_WAIT = 0.0
LATER = "还在准备，这一轮只写行动直接、合理的结果，不要编新的人物、地点和线索，收在一个悬念上"
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
