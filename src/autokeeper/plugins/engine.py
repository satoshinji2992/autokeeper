from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any

from ..engine import effects
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
    wounds: list[dict] = field(default_factory=list)  # engine/effects.Wound, rolled; landed in one world op at commit

    def dump(self) -> dict:
        return {
            "party": [person.model_dump(mode="json") for person in self.party],
            "events": self.events, "flags": self.flags, "clues": self.clues, "notes": self.notes, "hints": self.hints,
            "world": [[key, fact.model_dump(mode="json")] for key, fact in self.world],
            "timers": [effect.model_dump(mode="json") for effect in self.timers],
            "shows": [effect.model_dump(mode="json") for effect in self.shows],
            "prompts": [[who, effect.model_dump(mode="json")] for who, effect in self.prompts],
            "wounds": self.wounds,
        }

    @classmethod
    def load(cls, saved: dict) -> Prepared:
        from ..engine.state import WorldFact
        from ..rules.coc import Investigator

        return cls(
            party=[Investigator.model_validate(person) for person in saved["party"]],
            **{key: saved[key] for key in ("events", "flags", "clues", "notes", "hints")},
            world=[(key, WorldFact.model_validate(fact)) for key, fact in saved["world"]],
            timers=[Effect.model_validate(effect) for effect in saved["timers"]],
            shows=[Effect.model_validate(effect) for effect in saved["shows"]],
            prompts=[(who, Effect.model_validate(effect)) for who, effect in saved["prompts"]],
            wounds=list(saved.get("wounds", [])),
        )


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
            active = game.state.plugin_marks.get(plugin.id, {}).get("_settlement")
            if hook == "start" and plugin.id in game.state.plugin_data and not active:
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
                active = game.state.plugin_marks.get(plugin.id, {}).get("_settlement")
                # A disconnected display or an interrupted engine effect is not broken plugin
                # code. Keep its journal and let the original action resume the late effects.
                if not isinstance(exc, PluginError) or (active and active.get("committed")):
                    raise
                game.state.plugin_marks.get(plugin.id, {}).pop("_settlement", None)
                game.state.plugin_errors[plugin.id] = str(exc)[:1000]
                await game.emit({"type": "plugin_status", "plugin": plugin.id, "ok": False})
        await self._unattended()

    async def _invoke(self, plugin: Plugin, event: dict, turn: Turn) -> None:
        from ..engine.game import TURN_RECORD

        game, state = self.game, self.game.state
        marks = state.plugin_marks.setdefault(plugin.id, {})
        active = marks.get("_settlement")
        if active and active["event"] != event:
            raise RuntimeError(f"规则插件“{plugin.name}”仍有未完成的结算，请先继续原操作")
        batch = active["batch"] if active else hashlib.sha256(json.dumps([
            plugin.fingerprint, event, state.turn, state.minutes, state.location,
        ], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if batch in state.plugin_receipts:
            return
        if not active:
            active = marks["_settlement"] = {"batch": batch, "event": event, "resolution": {"steps": {}}}
        # Commands and prompts also run outside resolve(). Give their nested flags, clues and
        # notifications the same durable stage journal, stored in existing host-only marks.
        outer, scope = game._resolution, game._resolution_scope
        if outer is None:
            game._resolution = active["resolution"]
            game._resolution_scope = []
            if saved := game._resolution.get("turn"):
                restored = TURN_RECORD.validate_python(saved)
                for part in fields(turn):
                    setattr(turn, part.name, getattr(restored, part.name))
        try:
            game._resolution_save(turn)
            for step in range(8):
                token = f"{batch}/{step}"
                if token in state.plugin_receipts:
                    continue
                repeat = await game._resolution_step(
                    f"plugin:{token}", turn, lambda token=token: self._once(plugin, event, turn, token, batch),
                )
                state.plugin_receipts = [*state.plugin_receipts, token, *([] if repeat else [batch])][-256:]
                if not repeat:
                    marks.pop("_settlement", None)
                else:
                    active.pop("committed", None)
                game._resolution_save(turn)
                if not repeat:
                    return
            raise PluginError("单事件连续结算超过 8 段，插件已暂停")
        finally:
            game._resolution, game._resolution_scope = outer, scope

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
        step = game._resolution_step
        checks, serial = [], 0
        context = await step("context", turn, lambda: self._context(plugin))

        async def request(message):
            nonlocal serial
            number = serial
            serial += 1

            def roll():
                if message["request"] == "roll":
                    count, sides = dice_expression(message.get("expression"))
                    answer = [game.rules.dice.total(f"1D{sides}") for _ in range(count)]
                    return {"request": message, "answer": answer}
                identity = self._who(message.get("who"))
                if (identity not in {inv.id for inv in state.investigators}
                        or message.get("difficulty", "常规") not in DIFFICULTY_ZH):
                    raise PluginError("检定的人物或难度不存在")
                inv = state.find(identity)
                check = game.rules.check(inv, str(message.get("skill", "")),
                                         DIFFICULTY_ZH[message.get("difficulty", "常规")], mark=False)
                return {"request": message,
                        "answer": {"passed": check.passed, "target": check.target,
                                   "value": check.value, "text": check.describe()},
                        "check": {"type": "dice", "who": inv.name, **_check_view(check), "text": check.describe()}}

            saved = await step(f"request:{number}", turn, roll)
            if saved["request"] != message:
                raise PluginError("恢复时插件的掷骰请求发生变化，已停止结算")
            if saved.get("check"):
                checks.append(saved["check"])
            return saved["answer"]

        async def execute_once():
            result = await execute(plugin, event, context, request)
            return {"output": result.model_dump(mode="json"), "checks": checks}

        executed = await step("execute", turn, execute_once)
        result = Output.model_validate(executed["output"])
        checks = executed["checks"]

        def prepare():
            ready = self._prepare(plugin, result, scope=token)
            return {"ready": ready.dump(), "timers": self._timers(plugin, ready.timers)}

        prepared = await step("prepare", turn, prepare)
        ready = Prepared.load(prepared["ready"])
        line = "；".join(ready.notes)[:200]

        def commit():
            # This phase and its receipt are checkpointed together before any notification.
            # All rule rolls and validation already happened in the persisted prepare phase.
            # Its wounds land through engine/effects in one world op; a commit retried after that op
            # keeps the bodies it left, since the party copies were taken before the wounds.
            oid = f"plugin:{token}"
            settled = ready.wounds and any(x.id == oid for x in game.world.rec.journal)
            bodies = {i.id: (i.hp, list(i.conditions), i.injury.model_copy(deep=True)) for i in state.investigators}
            state.investigators = ready.party
            if settled:
                for inv in state.investigators:
                    if inv.id in bodies:
                        inv.hp, inv.conditions, inv.injury = bodies[inv.id]
            elif ready.wounds:
                done = effects.commit(game.world, effects.EFFECTS.validate_python(ready.wounds), op_id=oid,
                                      source=f"plugin:{plugin.id}")
                if not done.ok:
                    raise PluginError("伤害没有结算：" + "；".join(done.problems))
            state.plugin_data[plugin.id] = result.state
            state.plugin_versions[plugin.id] = plugin.version
            state.plugin_marks[plugin.id]["_settlement"]["committed"] = True
            if result.status:
                state.plugin_status[plugin.name] = result.status
            for key, fact in ready.world:
                state.world[key] = fact
            state.plugin_timers = prepared["timers"]
            turn.notes += ready.notes
            turn.notes += [f"{ev['who']}：{ev['text']}" for ev in checks]
            turn.hints += [f"〔{plugin.name}〕{hint}" for hint in ready.hints]
            if line:
                state.plugin_log = [*state.plugin_log,
                                    {"turn": state.turn, "name": plugin.name, "line": line}][-PLUGIN_LOG_KEEP:]
            if result.panels is not None:
                if result.panels:
                    state.plugin_panels[plugin.name] = [panel.model_dump(mode="json") for panel in result.panels]
                else:
                    state.plugin_panels.pop(plugin.name, None)

        await step("commit", turn, commit)
        if line:
            await step("effect", turn, lambda: game.emit(
                {"type": "plugin_effect", "plugin": plugin.id, "name": plugin.name, "line": line}))
        if result.panels is not None:
            await step("panel", turn, lambda: game.emit(
                {"type": "plugin_panel", "plugin": plugin.id, "name": plugin.name}))
        for show in ready.shows[:1]:
            def mark_show():
                marks = state.plugin_marks[plugin.id]
                if marks.get("shown") == state.turn:
                    return False
                marks["shown"] = state.turn
                return True

            if await step("show:mark", turn, mark_show):
                await step("show:emit", turn, lambda show=show: game.emit(
                    {"type": "plugin_show", "plugin": plugin.id, "name": plugin.name, "title": show.title,
                     "shape": show.shape, "frames": [frame.model_dump(mode="json") for frame in show.frames]}))
        for i, (who, prompt) in enumerate(ready.prompts):
            await step(f"prompt:{i}", turn, lambda who=who, prompt=prompt: self._ask(plugin, who, prompt, event, turn))
        for i, ev in enumerate(checks + ready.events):
            def log_event(ev=ev):
                if ev["type"] in ("dice", "sanity", "damage"):
                    state.add(ev["type"], ev.get("text", "") or f"{ev.get('skill', '检定')}：{ev.get('value', '')}",
                              who=ev.get("who", ""))

            await step(f"event:{i}:log", turn, log_event)
            await step(f"event:{i}:emit", turn, lambda ev=ev: game.emit(ev))
        await step("flags", turn, lambda: game._flags(ready.flags, turn))
        await step("clues", turn, lambda: game._gain(ready.clues, turn, None))
        return result.repeat

    def _prepare(self, plugin: Plugin, output: Output, *, scope: str = "") -> Prepared:
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
        hurt: set[tuple[str, str]] = set()
        bodies: dict[str, effects.Body] = {}  # each investigator's as the wounds so far leave it
        for index, effect in enumerate(output.effects):
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
                # A wound through engine/effects: its injury id is the blow it is (key, when the plugin
                # says which one: the engine's damage notices carry theirs), so a blow the engine
                # already took off is never taken off again.
                injury = effect.key.strip() or f"plugin:{plugin.id}:{scope}:{index}"
                live = game.state.find(inv.id)
                if injury in live.injury.injuries or (inv.id, injury) in hurt:
                    out.notes.append(f"{inv.name}的这次伤害已经结算过，没有重复扣除")
                    continue
                hurt.add((inv.id, injury))
                wound = effects.harm(game, inv.id, effect.amount, injury=injury)
                done = effects.wound(bodies.get(inv.id) or effects.Body.of(live), wound)
                bodies[inv.id] = done.body
                said = done.describe()  # what it will do, for the words; the op lands it
                out.wounds.append(wound.model_dump(mode="json"))
                out.events.append({"type": "damage", "who": inv.name, "text": said, "injury": injury})
                out.notes.append(f"{inv.name}{said}")
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
    def _timers(self, plugin: Plugin, timers: list[Effect]) -> list[dict]:
        from ..engine.state import parse_time

        st = self.game.state
        pending = list(st.plugin_timers)
        for effect in timers:
            pending = [t for t in pending if (t["plugin"], t["key"]) != (plugin.id, effect.key)]
            due: dict[str, Any] = {"plugin": plugin.id, "key": effect.key}
            if effect.kind == "schedule":
                due["minutes"] = parse_time(effect.at)
            elif effect.minutes:
                due["minutes"] = st.minutes + effect.minutes
            elif effect.rounds:
                due["turn"] = st.turn + effect.rounds
            else:  # neither: the timer is called off
                continue
            if sum(t["plugin"] == plugin.id for t in pending) >= TIMERS:
                raise PluginError(f"一个插件最多同时有 {TIMERS} 个计时")
            pending.append(due)
        return pending

    async def due(self, turn: Turn) -> None:
        """Timers whose game minute or round has come fire a "timer" event, once."""
        st = self.game.state
        ready = [t for t in st.plugin_timers
                 if ("minutes" in t and t["minutes"] <= st.minutes) or ("turn" in t and t["turn"] <= st.turn)]
        if not ready:
            return
        for t in ready:
            await self.run("timer", turn, plugin=t["plugin"], key=t["key"])
            # The plugin may replace its own key with a new timer. Remove only the original
            # due entry, after the entire invocation (including flags and clues) completed.
            if t in st.plugin_timers:
                st.plugin_timers.remove(t)
            if self.game.checkpoint:
                self.game.checkpoint()

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
        """Commit the question before notifying its player, preserving its id on retry."""
        st = self.game.state
        step = self.game._resolution_step

        def commit():
            marks = st.plugin_marks.setdefault(plugin.id, {})
            if event.get("hook") == "answer":
                chain = int(event.get("step", 0)) + 1
                if chain > STEPS:
                    return {"limit": True}
            else:
                chain = 1
                asked = marks.get("asked", [st.turn, 0])
                asked = asked if asked[0] == st.turn else [st.turn, 0]
                if asked[1] >= PROMPTS_PER_TURN:
                    return {}
                marks["asked"] = [st.turn, asked[1] + 1]
            st.plugin_serial += 1
            place = len(st.plugin_prompts)
            old = [p for p in st.plugin_prompts if p["who"] == who and p["plugin"] == plugin.id]
            for previous in old:
                place = min(place, st.plugin_prompts.index(previous))
                st.plugin_prompts.remove(previous)
            if sum(p["who"] == who for p in st.plugin_prompts) > QUEUED:
                return {"closed": [p["id"] for p in old]}
            st.plugin_prompts.insert(place, {"id": f"p{st.plugin_serial}", "plugin": plugin.id,
                                             "name": plugin.name, "who": who, "key": prompt.key,
                                             "spec": prompt.model_dump(mode="json"), "chain": chain,
                                             "open": False, "deadline": 0.0})
            if chain == 1 and not turn.interlude:
                turn.notes.append(f"〔{plugin.name}〕给{st.find(who).name}出了一道题，玩家会自己作答；叙述不要替他回答，也别说出答案")
            return {"closed": [p["id"] for p in old], "created": True}

        result = await step("commit", turn, commit)
        if result.get("limit"):
            await step("limit", turn, lambda: self.game.emit(
                {"type": "plugin_effect", "plugin": plugin.id, "name": plugin.name, "line": "这个小游戏到此为止"}))
        for i, pid in enumerate(result.get("closed", [])):
            await step(f"close:{i}", turn, lambda pid=pid: self.game.emit({"type": "plugin_closed", "id": pid, "who": who}))
        if result.get("created"):
            await self._promote(who, turn)

    async def _promote(self, who: str, turn: Turn | None = None) -> None:
        st = self.game.state
        turn = turn or self.game._late
        mine = [p for p in st.plugin_prompts if p["who"] == who]
        identity = mine[0]["id"] if mine else "empty"

        def commit():
            mine = [p for p in st.plugin_prompts if p["who"] == who]
            if not mine or any(p["open"] for p in mine):
                return None
            prompt = mine[0]
            spec = Effect.model_validate(prompt["spec"])
            prompt["open"], prompt["deadline"] = True, time.time() + spec.timeout
            return prompt

        opened = await self.game._resolution_step(f"promote:{who}:{identity}:commit", turn, commit)
        if opened:
            await self.game._resolution_step(f"promote:{who}:{identity}:emit", turn,
                                             lambda: self.game.emit(public_prompt(opened)))

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
            return f"「{plugin.name}」已暂停"
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
