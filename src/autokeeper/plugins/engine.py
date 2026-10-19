from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..rules.catalog import CHARACTERISTICS, canonical
from ..rules.coc import DIFFICULTY_ZH, max_of
from .runtime import PluginError, dice_expression, execute, verify
from .schema import PROMPTS_PER_TURN, STEPS, Effect, Output, Plugin, answer_problem, default_answer

if TYPE_CHECKING:
    from ..engine.game import Game, Turn

PLUGIN_LOG_KEEP = 80  # public plugin-effect lines a save keeps for the Ctrl+K window
TARGETED = ("action", "answer", "timer", "command")  # hooks addressed to one plugin, not to all
QUEUED = 3  # prompts that may wait behind a player's open one
TIMERS = 8  # timers one plugin may keep running


@dataclass
class Prepared:
    """A plugin output checked against the game, ready to commit."""

    party: list
    events: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    clues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    world: list[tuple[str, Any]] = field(default_factory=list)
    timers: list[Effect] = field(default_factory=list)
    shows: list[Effect] = field(default_factory=list)
    prompts: list[tuple[str, Effect]] = field(default_factory=list)  # (who, prompt)


class Manager:
    def __init__(self, game: Game):
        self.game = game
        self.verified: set[str] = set()
        # The hub says which human seats nobody sits in: their prompts are answered like an AI's.
        self.away: Callable[[str], bool] | None = None
        self._answering = False
        for identity, saved in game.state.plugin_sources.items():
            try:
                plugin = Plugin.model_validate(saved)
                if plugin.id != identity:
                    raise ValueError("存档插件 id 不一致")
                game.m.extensions[identity] = plugin
                if identity not in game.m.plugins:
                    game.m.plugins.append(identity)
                for key, meaning in plugin.flags.items():
                    game.m.flags.setdefault(key, meaning)
            except ValueError:
                game.state.plugin_errors[identity] = "存档插件格式错误"

    def _who(self, value: Any) -> Any:
        """An investigator as the arbiter or a plugin named them: the id, the full name, or the
        name before its bracket (贝蒂·康西丁 → betty). Anything else is left as it is."""
        for inv in self.game.state.investigators:
            if value in (inv.id, inv.name, inv.name.split("（")[0]):
                return inv.id
        return value

    def listening(self, hook: str) -> bool:
        return any(hook in p.hooks and p.id not in self.game.state.plugin_errors for p in self.game.m.extensions.values())

    async def run(self, hook: str, turn: Turn, **details) -> None:
        game = self.game
        if not getattr(getattr(game.cast, "cfg", None), "plugins", True):
            return
        args = details.get("arguments")
        if hook == "action" and isinstance(args, dict) and "who" in args:  # the arbiter writes names; rules use ids
            details = {**details, "arguments": {**args, "who": self._who(args["who"])}}
        for plugin in list(game.m.extensions.values()):
            if hook == "start" and plugin.id in game.state.plugin_data:
                continue
            if hook in TARGETED and details.get("plugin") != plugin.id:
                continue
            if plugin.id in game.state.plugin_errors:
                continue
            starting = plugin.id not in game.state.plugin_data and hook != "start" and "start" in plugin.hooks
            if hook not in plugin.hooks and not starting:
                continue
            try:
                if plugin.fingerprint not in self.verified:
                    if problems := await verify(plugin):
                        raise PluginError("；".join(problems))
                    self.verified.add(plugin.fingerprint)
                    game.state.plugin_sources[plugin.id] = plugin.model_dump(mode="json")
                if starting:
                    await self._invoke(plugin, {"hook": "start"}, turn)
                if hook in plugin.hooks:
                    if hook == "action" and details.get("action") not in {action.name for action in plugin.actions}:
                        # the arbiter's slip, not the plugin's: say so and leave the rules running
                        turn.notes.append(f"规则插件没有“{details.get('action')}”这个动作，这次按普通行动处理")
                        continue
                    await self._invoke(plugin, {"hook": hook, **details}, turn)
            except Exception as exc:
                game.state.plugin_errors[plugin.id] = str(exc)[:1000]
                await game.emit({"type": "plugin_status", "plugin": plugin.id, "ok": False})
        await self._unattended()

    async def _invoke(self, plugin: Plugin, event: dict, turn: Turn) -> None:
        state = self.game.state
        batch = hashlib.sha256(json.dumps([
            plugin.fingerprint, event, state.turn, state.minutes, state.location,
        ], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if batch in state.plugin_receipts:
            return
        for step in range(8):
            token = f"{batch}/{step}"
            if token in state.plugin_receipts:
                continue
            if not await self._once(plugin, event, turn, token, batch):
                return
        raise PluginError("单事件连续结算超过 8 段，插件已暂停")

    def _context(self, plugin: Plugin) -> dict:
        game, state = self.game, self.game.state
        prefs = getattr(game.cast, "prefs", None)
        return {
            "state": state.plugin_data.get(plugin.id, plugin.initial), "previous_version": state.plugin_versions.get(plugin.id, ""),
            "turn": state.turn, "minutes": state.minutes, "location": state.location,
            "flags": list(state.flags), "clues": list(state.clues),
            "investigators": [{key: value for key, value in inv.model_dump(mode="json").items()
                               if key in ("id", "name", "characteristics", "hp", "san", "inventory", "conditions")}
                              for inv in state.investigators],
            "acting": state.investigator.id if state.investigators else "",
            "npcs": [{"id": n.id, "name": n.name} for n in game.m.npcs_at(state.location)],
            "avoid": list(getattr(prefs, "lines", ()) or ()),  # the players' no-go subjects (prefs.py)
        }

    async def _once(self, plugin: Plugin, event: dict, turn: Turn, token: str, batch: str) -> bool:
        from ..engine.game import _check_view

        game, state = self.game, self.game.state
        checks = []

        async def request(message):
            if message["request"] == "roll":
                count, sides = dice_expression(message.get("expression"))
                return [game.rules.dice.total(f"1D{sides}") for _ in range(count)]
            identity = self._who(message.get("who"))
            if identity not in {inv.id for inv in state.investigators} or message.get("difficulty", "常规") not in DIFFICULTY_ZH:
                raise PluginError("检定的人物或难度不存在")
            inv = state.find(identity)
            check = game.rules.check(inv, str(message.get("skill", "")), DIFFICULTY_ZH[message.get("difficulty", "常规")], mark=False)
            checks.append({"type": "dice", "who": inv.name, **_check_view(check), "text": check.describe()})
            return {"passed": check.passed, "target": check.target, "value": check.value, "text": check.describe()}

        result = await execute(plugin, event, self._context(plugin), request)
        ready = self._prepare(plugin, result)
        # All references, capabilities, sheet changes and rule effects are checked before committing.
        state.investigators = ready.party
        state.plugin_data[plugin.id] = result.state
        state.plugin_versions[plugin.id] = plugin.version
        state.plugin_receipts = [*state.plugin_receipts, token, *([] if result.repeat else [batch])][-256:]
        if result.status:  # the plugin's opt-in public one-liner, keyed by name so a LAN table shares it
            state.plugin_status[plugin.name] = result.status
        for key, fact in ready.world:
            state.world[key] = fact
        self._time(plugin, ready.timers)
        notes = ready.notes
        turn.notes += notes
        turn.notes += [f"{ev['who']}：{ev['text']}" for ev in checks]
        turn.hints += [f"〔{plugin.name}〕{hint}" for hint in ready.hints]
        # A compact, recognisable line in the story when the rule does something public, and a
        # short public record for the Ctrl+K window (no counters, no id, safe at a shared table).
        if line := "；".join(notes)[:200]:
            state.plugin_log = [*state.plugin_log, {"turn": state.turn, "name": plugin.name, "line": line}][-PLUGIN_LOG_KEEP:]
            await game.emit({"type": "plugin_effect", "plugin": plugin.id, "name": plugin.name, "line": line})
        if result.panels is not None:  # public, keyed by name like the status
            if result.panels:
                state.plugin_panels[plugin.name] = [p.model_dump(mode="json") for p in result.panels]
            else:
                state.plugin_panels.pop(plugin.name, None)
            await game.emit({"type": "plugin_panel", "plugin": plugin.id, "name": plugin.name})
        marks = state.plugin_marks.setdefault(plugin.id, {})
        for show in ready.shows[:1]:  # one show a turn per plugin; more are dropped
            if marks.get("shown") == state.turn:
                break
            marks["shown"] = state.turn
            await game.emit({"type": "plugin_show", "plugin": plugin.id, "name": plugin.name, "title": show.title,
                             "shape": show.shape, "frames": [f.model_dump(mode="json") for f in show.frames]})
        for who, prompt in ready.prompts:
            await self._ask(plugin, who, prompt, event, turn)
        for ev in checks + ready.events:
            if ev["type"] in ("dice", "sanity", "damage"):
                state.add(ev["type"], ev.get("text", "") or f"{ev.get('skill', '检定')}：{ev.get('value', '')}", who=ev.get("who", ""))
            await game.emit(ev)
        await game._flags(ready.flags, turn)
        await game._gain(ready.clues, turn, None)
        if game.checkpoint:
            game.checkpoint()
        return result.repeat

    def _prepare(self, plugin: Plugin, output: Output) -> Prepared:
        from ..engine.game import _sanity_view
        from ..engine.state import WorldFact

        game = self.game
        party = [inv.model_copy(deep=True) for inv in game.state.investigators]
        people = {inv.id: inv for inv in party}
        npcs = {n.id: n for n in game.m.npcs} | {n.name: n for n in game.m.npcs}
        out = Prepared(party)
        for effect in output.effects:
            if effect.kind in ("attribute", "condition", "damage", "sanity", "item") and effect.who not in people:
                raise PluginError(f"效果涉及不存在的调查员 {effect.who}")
            if effect.kind == "prompt" and effect.who and self._who(effect.who) not in people:
                raise PluginError(f"prompt 问的调查员 {effect.who} 不存在")
            if effect.kind == "attitude" and effect.npc not in npcs:
                raise PluginError(f"attitude 涉及不存在的 NPC {effect.npc}")
            if effect.kind == "flag" and effect.key not in game.m.flags:
                raise PluginError(f"未声明的剧情标记 {effect.key}")
            if effect.kind == "clue" and effect.key not in game._valid_clues([effect.key], None):
                raise PluginError(f"线索 {effect.key} 不在当前位置或尚未解锁")
            if effect.kind == "attribute" and canonical(effect.key) not in CHARACTERISTICS:
                raise PluginError("attribute 只接受中文基础属性，不直接改 HP 或 SAN")
            if effect.kind == "condition" and (not effect.key or len(effect.key) > 80 or effect.amount not in (-1, 1)):
                raise PluginError("condition 需要状态名，amount 为 1 或 -1")
            if effect.kind == "damage" and effect.amount < 0:
                raise PluginError("伤害不能为负数")
            if effect.kind == "sanity":
                try:
                    if any(not 0 <= max_of(expr) <= 100 for expr in (effect.success, effect.failure)):
                        raise ValueError
                except ValueError:
                    raise PluginError("理智损失表达式非法或超过 100") from None
        if sum(effect.kind == "hint" for effect in output.effects) > 1:
            raise PluginError("一次只能给守秘人一句 hint")
        if sum(effect.kind == "prompt" for effect in output.effects) > 1:
            raise PluginError("一次只能问一个 prompt")
        for effect in output.effects:
            inv = people.get(effect.who)
            if effect.kind == "note":
                out.notes.append(effect.text)
            elif effect.kind == "flag":
                out.flags.append(effect.key)
            elif effect.kind == "clue":
                out.clues.append(effect.key)
            elif effect.kind == "attribute":
                key = canonical(effect.key)
                before = inv.characteristics[key]
                inv.characteristics[key] = max(0, min(999, before + effect.amount))
                inv.hp = min(inv.hp, inv.hp_max)
                out.notes.append(f"{inv.name}的{key} {before} → {inv.characteristics[key]}")
                out.events.append({"type": "sheet"})
            elif effect.kind == "condition":
                if effect.amount == 1:
                    inv.add_condition(effect.key)
                elif effect.key in inv.conditions:
                    inv.conditions.remove(effect.key)
                out.events.append({"type": "sheet"})
            elif effect.kind == "damage":
                result = game.rules.damage(inv, effect.amount)
                out.events.append({"type": "damage", "who": inv.name, "text": result.describe()})
                out.notes.append(f"{inv.name}{result.describe()}")
            elif effect.kind == "sanity":
                result = game.rules.sanity(inv, effect.success, effect.failure)
                out.events.append({"type": "sanity", "who": inv.name, **_sanity_view(result)})
                out.notes.append(f"{inv.name}{result.describe()}")
            elif effect.kind == "item":
                name = effect.key.strip()
                if effect.amount == 1 and name not in inv.inventory:
                    inv.inventory.append(name)
                    out.notes.append(f"{inv.name}拿到了{name}")
                elif effect.amount == -1 and name in inv.inventory:
                    inv.inventory.remove(name)
                    out.notes.append(f"{inv.name}交出了{name}")
                out.events.append({"type": "sheet"})
            elif effect.kind == "hint":
                out.hints.append(effect.text.strip())
            elif effect.kind == "attitude":
                npc = npcs[effect.npc]
                warmer = "更亲近" if effect.amount > 0 else "更疏远"
                text = f"（规则：{plugin.name}）{npc.name}对调查员{warmer}了一些" + (f"：{effect.text}" if effect.text else "")
                key = f"rule_{npc.id}_{hashlib.sha256(plugin.id.encode()).hexdigest()[:6]}"  # no plugin id at a shared table
                out.world.append((key, WorldFact(kind="relationship", text=text, who=game.state.investigator.id, npc=npc.id,
                                                 turn=game.state.turn, source="plugin")))
            elif effect.kind in ("timer", "schedule"):
                out.timers.append(effect)
            elif effect.kind == "show":
                out.shows.append(effect)
            elif effect.kind == "prompt":
                out.prompts.append((self._who(effect.who) if effect.who else game.state.investigator.id, effect))
        return out

    # -- timers ---------------------------------------------------------------------------
    def _time(self, plugin: Plugin, timers: list[Effect]) -> None:
        from ..engine.state import parse_time

        st = self.game.state
        for effect in timers:
            st.plugin_timers = [t for t in st.plugin_timers if (t["plugin"], t["key"]) != (plugin.id, effect.key)]
            due: dict[str, Any] = {"plugin": plugin.id, "key": effect.key}
            if effect.kind == "schedule":
                due["minutes"] = parse_time(effect.at)
            elif effect.minutes:
                due["minutes"] = st.minutes + effect.minutes
            elif effect.rounds:
                due["turn"] = st.turn + effect.rounds
            else:  # neither: the timer is called off
                continue
            if sum(t["plugin"] == plugin.id for t in st.plugin_timers) >= TIMERS:
                raise PluginError(f"一个插件最多同时有 {TIMERS} 个计时")
            st.plugin_timers.append(due)

    async def due(self, turn: Turn) -> None:
        """Timers whose game minute or round has come fire a "timer" event, once."""
        st = self.game.state
        ready = [t for t in st.plugin_timers
                 if ("minutes" in t and t["minutes"] <= st.minutes) or ("turn" in t and t["turn"] <= st.turn)]
        if not ready:
            return
        st.plugin_timers = [t for t in st.plugin_timers if t not in ready]
        for t in ready:
            await self.run("timer", turn, plugin=t["plugin"], key=t["key"])

    async def settled(self, turn: Turn) -> None:
        """After a check and when clues turn up: the roll and clue hooks."""
        st, m = self.game.state, self.game.m
        if self.listening("roll"):
            for check in list(turn.checks):
                await self.run("roll", turn, who=st.investigator.id, skill=check.name, passed=check.passed,
                               level=check.level.zh, value=check.value, target=check.target)
        if self.listening("clue"):
            for cid in list(turn.clues):
                await self.run("clue", turn, clue=cid, title=m.clue(cid).title)

    # -- prompts --------------------------------------------------------------------------
    async def _ask(self, plugin: Plugin, who: str, prompt: Effect, event: dict, turn: Turn) -> None:
        """Open a prompt for one investigator. Answers to a plugin's own game may chain up to
        STEPS; anything else counts against PROMPTS_PER_TURN. One prompt is open per player: the
        same plugin's newer one replaces its older one, another plugin's waits its turn."""
        st = self.game.state
        marks = st.plugin_marks.setdefault(plugin.id, {})
        if event.get("hook") == "answer":
            chain = int(event.get("step", 0)) + 1
            if chain > STEPS:
                await self.game.emit({"type": "plugin_effect", "plugin": plugin.id, "name": plugin.name, "line": "这个小游戏到此为止"})
                return
        else:
            chain = 1
            asked = marks.get("asked", [st.turn, 0])
            asked = asked if asked[0] == st.turn else [st.turn, 0]
            if asked[1] >= PROMPTS_PER_TURN:
                return
            marks["asked"] = [st.turn, asked[1] + 1]
        st.plugin_serial += 1
        place = len(st.plugin_prompts)  # a newer question takes its older one's place in the line
        for old in [p for p in st.plugin_prompts if p["who"] == who and p["plugin"] == plugin.id]:
            place = min(place, st.plugin_prompts.index(old))
            st.plugin_prompts.remove(old)
            await self.game.emit({"type": "plugin_closed", "id": old["id"], "who": who})
        if sum(p["who"] == who for p in st.plugin_prompts) > QUEUED:
            return
        st.plugin_prompts.insert(place, {"id": f"p{st.plugin_serial}", "plugin": plugin.id, "name": plugin.name, "who": who,
                                         "key": prompt.key, "spec": prompt.model_dump(mode="json"), "chain": chain,
                                         "open": False, "deadline": 0.0})
        if chain == 1 and not turn.interlude:
            turn.notes.append(f"〔{plugin.name}〕给{st.find(who).name}出了一道题，玩家会自己作答；叙述不要替他回答，也别说出答案")
        await self._promote(who)

    async def _promote(self, who: str) -> None:
        st = self.game.state
        mine = [p for p in st.plugin_prompts if p["who"] == who]
        if not mine or any(p["open"] for p in mine):
            return
        prompt = mine[0]
        spec = Effect.model_validate(prompt["spec"])
        prompt["open"], prompt["deadline"] = True, time.time() + spec.timeout
        await self.game.emit(public_prompt(prompt))

    def open_prompts(self) -> list[dict]:
        return [p for p in self.game.state.plugin_prompts if p["open"]]

    async def answer(self, pid: str, value: Any = None, *, by: str = "player", who: str | None = None) -> str:
        """An answer to an open prompt, given as a new "answer" event to the plugin that asked.
        `by` is player, skip (the player passed), timeout or ai; all but player take the
        default (an AI may also choose at random, if the prompt says so). Returns why an
        answer was refused, "" when it went in."""
        game, st = self.game, self.game.state
        prompt = next((p for p in st.plugin_prompts if p["id"] == pid and p["open"]), None)
        if prompt is None:
            return "这道题已经结束了"
        if who is not None and who != prompt["who"]:
            return "这道题不是问你的"
        spec = Effect.model_validate(prompt["spec"])
        if by == "player":
            if problem := answer_problem(spec, value):
                return f"这个回答不行：{problem}"
        elif by == "ai" and spec.ai == "random":
            value = self._pick(spec)
        else:
            value = default_answer(spec)
        st.plugin_prompts.remove(prompt)
        await game.emit({"type": "plugin_closed", "id": pid, "who": prompt["who"]})
        if by != "player":
            await game.emit({"type": "plugin_answered", "plugin": prompt["plugin"], "name": prompt["name"],
                             "who": st.find(prompt["who"]).name, "by": by, "text": shown(spec, value)})
        await self._promote(prompt["who"])
        await self.run("answer", game._late, plugin=prompt["plugin"], prompt=prompt["key"], value=value, by=by,
                       who=prompt["who"], step=prompt["chain"], serial=pid)
        if game.checkpoint:
            game.checkpoint()
        return ""

    def _pick(self, spec: Effect) -> Any:
        """A random answer for an AI seat, thrown with the host's dice."""
        dice = self.game.rules.dice

        def one(n: int) -> int:
            return dice.total(f"1D{n}") - 1 if n > 1 else 0

        if spec.style in ("choice", "pick"):
            pick = spec.options[one(len(spec.options))]
            return pick if spec.style == "choice" else [pick]
        if spec.style == "order":
            left, out = list(spec.options), []
            while left:
                out.append(left.pop(one(len(left))))
            return out
        if spec.style == "number":
            return spec.low + one(spec.high - spec.low + 1) if spec.high - spec.low < 999 else spec.low
        if spec.style == "board":
            row = one(len(spec.board))
            return [row, one(len(spec.board[row]))]
        return default_answer(spec)

    def _absent(self, who: str) -> bool:
        st = self.game.state
        return who not in st.human or bool(self.away and self.away(who))

    async def _unattended(self) -> None:
        """Prompts for an AI seat (autoplay too) or an empty human seat are answered at once."""
        if self._answering:
            return
        self._answering = True
        try:
            for _ in range(STEPS * 4):
                due = next((p for p in self.open_prompts() if self._absent(p["who"])), None)
                if due is None:
                    break
                await self.answer(due["id"], by="ai")
        finally:
            self._answering = False

    async def expire(self, now: float | None = None) -> None:
        """Prompts nobody answered in time: the default stands."""
        now = time.time() if now is None else now
        for prompt in [p for p in self.open_prompts() if p["deadline"] <= now]:
            await self.answer(prompt["id"], by="timeout")

    # -- slash commands ---------------------------------------------------------------------
    async def command(self, name: str, text: str = "", who: str | None = None) -> str:
        """/占卜 and the like: a "command" event for the plugin that declared it. Returns why it
        could not run, "" when it did."""
        game, st = self.game, self.game.state
        name = name.lstrip("/")
        plugin = next((p for p in game.m.extensions.values() if any(c.name == name for c in p.commands)), None)
        if plugin is None:
            return f"不认识的命令：/{name}"
        if plugin.id in st.plugin_errors:
            return f"「{plugin.name}」暂停了，Ctrl+K 里可以重新启用"
        st.plugin_serial += 1
        await self.run("command", game._late, plugin=plugin.id, command=name, text=text[:200],
                       who=who or st.investigator.id, serial=f"c{st.plugin_serial}")
        if game.checkpoint:
            game.checkpoint()
        return "" if plugin.id not in st.plugin_errors else f"「{plugin.name}」出了问题，已经暂停"


def public_prompt(prompt: dict) -> dict:
    """A prompt as the player's screen gets it: what is asked and how to answer. The default
    and the AI policy stay on the host."""
    spec = prompt["spec"]
    left = max(0, int(prompt["deadline"] - time.time())) if prompt["deadline"] else spec.get("timeout", 180)
    return {"type": "plugin_prompt", "id": prompt["id"], "name": prompt["name"], "who": prompt["who"],
            "title": spec.get("title", ""), "text": spec.get("text", ""), "style": spec.get("style", "choice"),
            "options": spec.get("options", []), "board": spec.get("board", []), "low": spec.get("low", 0),
            "high": spec.get("high", 9), "timeout": left}


def shown(spec: Effect, value: Any) -> str:
    """An answer in a few plain words, for the story."""
    if spec.style == "board" and isinstance(value, list) and len(value) == 2:
        row, col = value
        cell = spec.board[row][col] if row < len(spec.board) and col < len(spec.board[row]) else ""
        return f"第 {row + 1} 行第 {col + 1} 格" + (f"（{cell}）" if cell.strip() else "")
    if isinstance(value, list):
        return "、".join(map(str, value)) or "什么也没选"
    return str(value) if str(value) else "（空）"
