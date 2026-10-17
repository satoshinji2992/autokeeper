"""One game: deterministic resolution around the agents.

The engine owns the facts: dice (BCDice), which clues are found, time, events,
sanity and endings. Agents only ever turn words into structure (intent, arbiter)
or structure into words (NPCs, keeper).
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .. import skills
from ..agents.cast import Action, Cast, Ruling
from ..module.compiler import skill_name
from ..module.schema import Event, Module
from ..rules import commands
from ..rules.bcdice import Level
from ..rules.coc import DIFFICULTY_ZH, Check, Investigator, Rules, Sanity
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


class Game:
    def __init__(self, module: Module, state: GameState, cast: Cast, rules: Rules, emit: Emit | None = None):
        self.m, self.state, self.cast, self.rules = module, state, cast, rules
        self.emit: Emit = emit or _silent

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
        loc = m.location(st.location)
        await self.emit({"type": "move", "location": loc.id, "name": loc.name, "art": m.art.get(loc.art or "", "")})
        await self._time()
        if m.opening:
            st.add("opening", m.opening.strip())
            await self.emit({"type": "opening", "text": m.opening.strip()})
        party = "、".join(f"{i.name}（{i.occupation}）" for i in st.investigators)
        report = (
            f"【开场｜{st.clock}｜{loc.name}】\n调查员：{party}\n"
            f"开场钩子（玩家已经知道）：{m.hook.strip()}\n"
            f"【进入地点】\n{self._location_file(loc.id)}\n"
            "请用 120–220 字开场：交代调查员为什么来到这里，描写眼前的地点，结尾落在一个能行动的细节上。"
        )
        return await self._narrate(report, on_delta)

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
        return await self.cast.intent(st, text)

    async def rule(self, action: Action) -> Ruling | None:
        if action.kind in ("meta",):
            return None
        if action.kind == "move" and action.destination:
            return Ruling(needs_roll=False, minutes=0, reason="移动")
        await self.emit({"type": "status", "text": "裁定中…"})
        return await self.cast.arbiter(self.state, action, at=self._heading(action))

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
        inv = st.investigator
        if action.kind == "meta" or ruling is None:
            return turn

        # movement: a plain move, or walking somewhere to do something there (ruled there)
        heading = action.destination if action.kind == "move" else self._heading(action)
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
            if not passed and (check.can_push or check.luck_needed() is not None):
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
                        "luck": check.luck_needed() if check.luck_needed() is not None and check.luck_needed() <= inv.luck else None,
                        "value": check.value,
                    }
                    st.pending_push = turn.push

        gained = self._valid_clues(ruling.auto_clues, action)
        if passed:
            gained += [c for c in self._valid_clues(ruling.clues_on_success, action) if c not in gained]
            await self._flags(ruling.set_flags, turn)
        await self._gain(gained, turn, action)

        st.minutes += max(0, min(ruling.minutes, 180))
        await self._time(turn)
        await self._check_ending(turn)
        return turn

    async def push(self, mode: str) -> Turn:
        """Resolve a pending push or luck spend: mode is 'push', 'luck' or 'drop'."""
        st = self.state
        pending, st.pending_push = st.pending_push, None
        turn = Turn()
        if not pending or mode == "drop":
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
                st.add("npc", line, who=npc.name)
                await self.emit({"type": "npc", "npc": npc.id, "name": npc.name, "text": line, "portrait": m.art.get(npc.portrait or "", "")})
        for ev in turn.events:
            if ev.performer:
                npc = m.npc(ev.performer)
                await self.emit({"type": "status", "text": f"{npc.name}登台了…"})
                text = await self.cast.perform(st, npc.id, ev.performance, self._deeds())
                turn.performance = (npc.id, text)
                st.add("npc", text, who=npc.name)
                await self.emit({"type": "performance", "npc": npc.id, "name": npc.name, "text": text, "portrait": m.art.get(npc.portrait or "", "")})

    async def narrate(self, turn: Turn, on_delta=None) -> str:
        report = self._report(turn)
        lore = await self._lore(turn)
        if lore:
            report += (
                "\n\n【模组知识库检索（LightRAG，只给守秘人参考：不要照念，不要透露玩家还没发现的东西）】\n"
                + lore[:3000]
            )
        return await self._narrate(report, on_delta)

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
        a = turn.action
        if a and a.kind == "meta":
            out.append(f"玩家在问游戏外的问题：{a.summary}\n请以守秘人身份用一两句话简短回答，不推进剧情。")
            return "\n".join(out)
        if a:
            out.append(f"行动：{a.summary}")
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
        else:
            out.append("请叙述。")
        return "\n".join(out)

    async def _narrate(self, report: str, on_delta=None) -> str:
        await self.emit({"type": "keeper_start"})

        async def delta(text: str) -> None:
            await self.emit({"type": "keeper_delta", "text": text})
            if on_delta:
                await on_delta(text)

        text = await self.cast.keeper(self.state, report, on_delta=delta)
        self.state.add("keeper", text, who="守秘人")
        await self.emit({"type": "keeper_end", "text": text})
        return text


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
