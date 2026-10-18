"""One game: deterministic resolution around the agents.

The engine owns the facts: dice (BCDice), which clues are found, time, events,
sanity and endings. Agents only ever turn words into structure (intent, arbiter)
or structure into words (NPCs, keeper).
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .. import skills, weather
from ..agents.cast import Action, Cast, Ruling
from ..agents.director import Improv
from ..harness.replay import CassetteError
from ..module import improv
from ..module.compiler import skill_name
from ..module.schema import Event, Module
from ..rules import commands
from ..rules.bcdice import Level
from ..rules.coc import DIFFICULTY_ZH, Check, Investigator, Rules, Sanity
from . import fog
from .state import GameState, parse_time, time_of_day

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
    npc_lines: list[tuple[str, str]] = field(default_factory=list)
    performance: tuple[str, str] | None = None
    push: dict | None = None
    notes: list[str] = field(default_factory=list)
    ending: str | None = None
    interlude: bool = False  # no action of theirs: the director's scene comes in between turns


@dataclass
class Backstage:
    """A director's plan still being written after its turn moved on."""

    kind: str
    task: asyncio.Task
    at: str  # where the investigators were when it was asked for


class Game:
    def __init__(self, module: Module, state: GameState, cast: Cast, rules: Rules, emit: Emit | None = None):
        self.m, self.state, self.cast, self.rules = module, state, cast, rules
        self.emit: Emit = emit or _silent
        if state.improv:  # a loaded game brings its own branches
            improv.restore(module, state.improv)
        # How long a turn waits for the director before the keeper goes on without it. None
        # waits for the plan (autoplay and replays: no one is waiting, and replays must not
        # depend on timing); a screen sets PLAY_WAIT.
        self.director_wait: float | None = None
        self.backstage: list[Backstage] = []
        self._late = Turn()  # what landed from backstage, told in the next narration

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
        # The director may rework the module around these investigators. At a screen the
        # opening does not wait for it: the adaptation lands at the start of a later turn.
        adapted = await self._improvise("开局改编", patience=0.0) if not st.improv else None
        loc = m.location(st.location)
        await self.emit({"type": "move", "location": loc.id, "name": loc.name, "art": m.art.get(loc.art or "", "")})
        opening = Turn()
        await self._time(opening)
        if m.opening:
            st.add("opening", m.opening.strip())
            await self.emit({"type": "opening", "text": m.opening.strip()})
        if adapted is not None:
            await self._flags(adapted.now, opening)
        party = "、".join(f"{i.name}（{i.occupation}）" for i in st.investigators)
        report = (
            f"【开场｜{st.clock}｜{loc.name}】\n调查员：{party}\n"
            f"开场钩子（玩家已经知道）：{m.hook.strip()}\n"
            f"【进入地点】\n{self._location_file(loc.id)}\n"
        )
        if adapted is not None and adapted.note:
            report += f"导演为这几位调查员改编了模组：{adapted.note}\n"
        happened = [e.read_aloud.strip() or e.title for e in opening.events]
        if happened:
            report += "【开场时发生的事】\n" + "\n".join(happened) + "\n"
        report += "请用 120–220 字开场：交代调查员为什么来到这里，描写眼前的地点，结尾落在一个能行动的细节上。"
        text = await self._narrate(report, on_delta)
        st.heard = fog.heard(m, st)  # the places the opening named reach the screens at a shared table
        return text

    # -- dice commands ------------------------------------------------------------------
    async def command(self, text: str) -> commands.CommandResult:
        st = self.state
        st.turn += 1
        inv = st.investigator
        result = commands.run(text, inv, self.rules)
        st.add("dice", result.text, who=inv.name)
        payload: dict[str, Any] = {"type": "command", "kind": result.kind, "text": result.text, "who": inv.name}
        if result.kind == "check":
            payload = {"type": "dice", "who": inv.name, **_check_view(result.data["check"])}
        elif result.kind == "sanity":
            payload = {"type": "sanity", "who": inv.name, **_sanity_view(result.data["sanity"])}
        await self.emit(payload)
        await self.emit({"type": "sheet"})
        return result

    # -- the turn pipeline --------------------------------------------------------------
    async def parse(self, text: str) -> Action:
        st = self.state
        st.turn += 1
        st.add("player", text, who=st.investigator.name)
        await self.emit({"type": "status", "text": "解析行动…"})
        action = await self.cast.intent(st, text)
        if action.echo and action.kind != "meta":  # something on screen at once, while the rest is worked out
            await self.emit({"type": "echo", "text": action.echo})
        return action

    async def rule(self, action: Action) -> Ruling | None:
        await self._curtain()  # finished plans land before this action is ruled
        if action.kind in ("meta",):
            return None
        if action.kind == "move" and action.destination:
            return Ruling(needs_roll=False, minutes=0, reason="移动")
        await self.emit({"type": "status", "text": "裁定中…"})
        heading = self._heading(action)
        ruling = await self.cast.arbiter(self.state, action, at=heading)
        if ruling.scope != "模组内":  # the module is silent here: the director fills the gap
            kind = "展开" if ruling.scope == "可即兴" else "后果"
            waiting = len(self.backstage)
            applied = await self._improvise(kind, action=action, ruling=ruling, at=heading or self.state.location)
            if applied is not None:
                ruling = self._steer(ruling, applied)
            elif len(self.backstage) > waiting:
                ruling = ruling.model_copy(update={"note": LATER})
        return ruling

    # -- improvisation ----------------------------------------------------------------
    async def _improvise(
        self, kind: str, *, action: Action | None = None, ruling: Ruling | None = None, at: str = "", patience: float | None = None
    ) -> improv.Applied | None:
        """Ask the director for a plan and write it into this game's module. A plan that takes
        longer than the turn can wait keeps being written backstage and lands at the start of
        a later turn (_curtain); meanwhile, and without a usable plan, the turn goes on."""
        st = self.state
        if not self.improvising or (kind != "开局改编" and sum(p["kind"] != "开局改编" for p in st.improv) >= IMPROV_LIMIT):
            return None
        if len(self.backstage) >= BACKSTAGE_LIMIT:  # enough is being written already
            return None
        await self.emit({"type": "status", "text": "守秘人在即兴…"})
        where = at or st.location
        task = asyncio.create_task(self._plan(kind, action=action, ruling=ruling, at=where))
        wait = self.director_wait if patience is None or self.director_wait is None else min(patience, self.director_wait)
        if wait is None:
            plan = await task
        else:
            try:
                plan = await asyncio.wait_for(asyncio.shield(task), wait)
            except TimeoutError:
                self.backstage.append(Backstage(kind, task, where))
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
                return plan
        return None

    async def _land(self, kind: str, plan: Improv) -> improv.Applied:
        applied = improv.apply(self.m, plan)
        self.state.improv.append({"turn": self.state.turn, "kind": kind, "plan": plan.model_dump()})
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
            if plan is None or improv.check(self.m, plan, at=b.at):  # the story moved on since: let it go
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
        the tomb door and check the seams"): a reachable place other than here."""
        dst = action.destination
        if action.kind == "move" or not dst or dst == self.state.location or self._travel(self.state.location, dst) is None:
            return None
        return dst

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
        # director sending them somewhere new
        heading = ruling.go or (action.destination if action.kind == "move" else self._heading(action))
        heading = None if heading == st.location else heading
        if heading:
            minutes = self._travel(st.location, heading)
            if minutes is None:
                turn.notes.append(f"去不了“{heading}”")
            else:
                st.minutes += minutes
                await self._arrive(heading, turn)

        # the check
        passed = not ruling.needs_roll
        if ruling.needs_roll and ruling.skill:
            check = self.rules.check(inv, ruling.skill, DIFFICULTY_ZH.get(ruling.difficulty, Level.REGULAR), ruling.bonus)
            turn.checks.append(check)
            passed = check.passed
            st.add("dice", check.describe(), who=inv.name)
            await self.emit({"type": "dice", "who": inv.name, **_check_view(check)})
            need = check.luck_needed()
            if not passed and (check.can_push or need is not None):
                wanted = self._valid_clues(ruling.clues_on_success, action)
                if wanted:
                    turn.push = {
                        "skill": check.name,
                        "target": check.target,
                        "difficulty": ruling.difficulty,
                        "bonus": ruling.bonus,
                        "clues": wanted,
                        "flags": ruling.set_flags,
                        "can_push": check.can_push,
                        "luck": need if need is not None and need <= inv.luck else None,
                        "value": check.value,
                    }
                    st.pending_push = turn.push
            if not passed and turn.push is None:  # no second chance offered: do not sit on core clues
                await self._fail_forward(self._valid_clues(ruling.clues_on_success, action), turn)

        gained = self._valid_clues(ruling.auto_clues, action)
        if passed:
            gained += [c for c in self._valid_clues(ruling.clues_on_success, action) if c not in gained]
            await self._flags(ruling.set_flags, turn)
        await self._gain(gained[: MAX_FOUND if action.target else 1], turn, action)  # one look around does not empty a room
        if ruling.now:  # the director's scene plays out, whatever the dice said
            await self._flags(ruling.now, turn)
        if ruling.note:
            turn.notes.append(f"导演的安排：{ruling.note}")

        # A night's sleep is legitimate; anything else that claims hours is capped. The arbiter
        # says when it is a rest: "先回去睡一觉" said to a partner parses as talk, not wait.
        resting = action.kind == "wait" or ruling.rest
        st.minutes += max(0, min(ruling.minutes, WAIT_CAP if resting else ACT_CAP))
        await self._time(turn)
        if self.improvising:
            await self._nudge(turn)
        await self._check_ending(turn)
        return turn

    async def push(self, mode: str) -> Turn:
        """Resolve a pending push or luck spend: mode is 'push', 'luck' or 'drop'."""
        st = self.state
        pending, st.pending_push = st.pending_push, None
        turn = Turn()
        if not pending:
            return turn
        if mode == "drop":
            await self._fail_forward(pending["clues"], turn)
            return turn
        inv = st.investigator
        diff = DIFFICULTY_ZH.get(pending["difficulty"], Level.REGULAR)
        if mode == "luck" and pending.get("luck") is not None:
            inv.luck -= pending["luck"]
            passed = True
            st.add("dice", f"花费幸运 {pending['luck']} 点，{pending['skill']} 检定改为成功", who=inv.name)
            await self.emit({"type": "luck", "who": inv.name, "spent": pending["luck"], "left": inv.luck})
        else:
            check = self.rules.check(inv, pending["skill"], diff, pending["bonus"], pushed=True, target=pending["target"])
            turn.checks.append(check)
            passed = check.passed
            st.add("dice", check.describe(), who=inv.name)
            await self.emit({"type": "dice", "who": inv.name, **_check_view(check)})
            if not passed:
                turn.notes.append("孤注一掷失败：后果一定发生，而且要狠")
        if passed:
            await self._flags(pending.get("flags", []), turn)
            await self._gain(pending["clues"], turn, None)
        else:
            await self._fail_forward(pending["clues"], turn, pushed=True)  # a pushed roll was the last chance: the clue comes, at a price
        await self._check_ending(turn)
        return turn

    async def talk(self, turn: Turn) -> None:
        """NPC replies and stage performances, after the engine has resolved checks."""
        st, m = self.state, self.m
        action = turn.action
        if action and action.kind in ("talk", "perform") and action.npc:
            if not any(n.id == action.npc for n in m.npcs_at(st.location)):
                turn.notes.append(f"{action.npc} 不在这里")
            else:
                npc = m.npc(action.npc)
                await self.emit({"type": "status", "text": f"{npc.name}正在开口…"})
                line = await self.cast.npc(st, npc.id, self._npc_message(turn, npc.id))
                turn.npc_lines.append((npc.id, line))
                st.npc(npc.id).met = True
                st.add("npc", line, who=npc.name)
                await self.emit({"type": "npc", "npc": npc.id, "name": npc.name, "text": line, "portrait": m.art.get(npc.portrait or "", "")})
        for ev in turn.events:
            if ev.performer:
                npc = m.npc(ev.performer)
                await self.emit({"type": "status", "text": f"{npc.name}登台了…"})
                text = await self.cast.perform(st, npc.id, ev.performance, self._deeds())
                turn.performance = (npc.id, text)
                st.npc(npc.id).met = True
                st.add("npc", text, who=npc.name)
                await self.emit({"type": "performance", "npc": npc.id, "name": npc.name, "text": text, "portrait": m.art.get(npc.portrait or "", "")})

    async def narrate(self, turn: Turn, on_delta=None) -> str:
        lore = await self._lore(turn)
        if lore:
            lore = "\n\n【模组知识库检索（LightRAG，只给守秘人参考：不要照念，不要透露玩家还没发现的东西）】\n" + lore[:3000]
        return await self._narrate(self._report(turn), on_delta, lore=lore)

    async def _lore(self, turn: Turn) -> str:
        """GraphRAG context for this turn, keyed by what the structured action touched."""
        a = turn.action
        lore = getattr(self.cast, "lore", None)
        if a is None or lore is None or a.kind in ("move", "wait"):
            return ""
        m, st = self.m, self.state
        keywords = [a.target, m.location(st.location).name]
        if a.npc and any(n.id == a.npc for n in m.npcs):
            keywords.append(m.npc(a.npc).name)
        await self.emit({"type": "status", "text": "检索模组知识库…"})
        try:
            return await lore(st, a.summary, keywords=[k for k in keywords if k])
        except Exception as exc:  # retrieval is an enhancement; never break a turn over it
            await self.emit({"type": "status", "text": f"知识库检索失败：{exc.__class__.__name__}"})
            return ""

    async def remember(self) -> None:
        st = self.state
        st.heard = fog.heard(self.m, st)  # the places named in the log outlive its folding into memory
        recent = [e for e in st.log if e.kind != "dice"]
        if len(recent) < 48:
            return
        old, keep = st.log[:-24], st.log[-24:]
        text = "\n".join(f"[{e.time}] {e.who or e.kind}：{e.text}" for e in old if e.kind != "dice")
        await self.emit({"type": "status", "text": "记录员整理笔记…"})
        st.memory = await self.cast.chronicle(st, text)
        st.log = keep

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
    def _travel(self, src: str, dst: str) -> int | None:
        if dst == src:
            return 0
        ids = {loc.id for loc in self.m.locations}
        if dst not in ids:
            return None
        if dst in self.m.location(src).exits:
            return 10
        seen, queue = {src: 0}, deque([src])
        while queue:
            cur = queue.popleft()
            for nxt in self.m.location(cur).exits:
                if nxt in ids and nxt not in seen:
                    seen[nxt] = seen[cur] + 1
                    queue.append(nxt)
        return seen[dst] * 15 if dst in seen else None

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

    def _valid_clues(self, ids: list[str], action: Action | None) -> list[str]:
        st, m = self.state, self.m
        here = {c.id for c in m.clues_at(st.location)}
        for n in m.npcs_at(st.location):
            here |= set(n.clues)
        known = {c.id for c in m.clues}
        return [c for c in dict.fromkeys(ids) if c in known and c in here and c not in st.clues]

    async def _gain(self, ids: list[str], turn: Turn, action: Action | None) -> None:
        st, m = self.state, self.m
        for cid in ids:
            if cid in st.clues:
                continue
            clue = m.clue(cid)
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
        said = action.speech or action.summary
        parts = [f"（系统：{st.clock}，{m.location(st.location).name}。{inv.name}（{inv.occupation}）对你说：「{said}」"]
        if action.approach:
            parts.append(f"方式：{action.approach}。")
        mine = [c for c in turn.clues if m.clue(c).npc == npc_id or c in m.npc(npc_id).clues]
        if turn.checks:
            c = turn.checks[-1]
            parts.append(f"检定：{c.name} {c.level.zh}，{'你被打动了或被拆穿了' if c.passed else '你没有被打动'}。")
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

    def _location_file(self, loc_id: str) -> str:
        name = skill_name(self.m)
        try:
            return skills.reference(name, f"references/locations/{loc_id}.md")
        except FileNotFoundError:
            loc = self.m.location(loc_id)
            return f"{loc.name}\n{loc.read_aloud}\n守秘人须知：{loc.keeper}"

    def _report(self, turn: Turn) -> str:
        st, m = self.state, self.m
        inv = st.investigator
        loc = m.location(st.location)
        out = [f"【第 {st.turn} 轮｜{st.clock}（{time_of_day(st.minutes)}）｜{loc.name}】", f"调查员：{inv.name}（{inv.occupation}）"]
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
            if a.echo:
                out.append(f"屏幕上已经先写了一句：「{a.echo}」，从这里接着往下写，不要重复它。")
        if turn.arrived:
            if turn.first_visit:
                out.append(f"【进入新地点，以下是守秘人资料】\n{self._location_file(turn.arrived)}")
            else:
                out.append(f"回到了 {m.location(turn.arrived).name}。")
        if turn.checks:
            out += [f"检定：{c.describe()}" for c in turn.checks]
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
            out.append(f"事件〔{ev.title}〕：{ev.read_aloud.strip()}\n守秘人须知：{ev.text.strip()}")
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
        await self.emit({"type": "keeper_start"})

        async def delta(text: str) -> None:
            await self.emit({"type": "keeper_delta", "text": text})
            if on_delta:
                await on_delta(text)

        text = await self.cast.keeper(self.state, report, on_delta=delta, lore=lore)
        self.state.add("keeper", text, who="守秘人")
        await self.emit({"type": "keeper_end", "text": text})
        await self._sky(text)
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


IMPROV_LIMIT = 16  # plans per game besides the opening one, so a game cannot sprawl forever
BACKSTAGE_LIMIT = 2  # plans being written at once
# A turn at a screen does not wait for the director: measured, a plan takes 9 s and more
# (DeepSeek) or 14 s (Opus), so any wait shorter than that only made turns slower. Plans
# land in an interlude as soon as they are ready (Game.interlude).
PLAY_WAIT = 0.0
LATER = "还在准备，这一轮只写行动直接、合理的结果，不要编新的人物、地点和线索，收在一个悬念上"
STALL_TURNS = 12  # turns without a clue, flag, event or new place before the director sends a lead
MAX_FOUND = 2  # clues one action can turn up; a look around without a target, one
ACT_CAP = 180  # minutes one action may take
WAIT_CAP = 720  # minutes one wait or rest may skip: a night's sleep


def _check_view(c: Check) -> dict[str, Any]:
    return {
        "skill": c.name,
        "target": c.target,
        "difficulty": {Level.REGULAR: "常规", Level.HARD: "困难", Level.EXTREME: "极难"}.get(c.difficulty, "常规"),
        "bonus": c.bonus,
        "value": c.value,
        "candidates": c.roll.candidates,
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
