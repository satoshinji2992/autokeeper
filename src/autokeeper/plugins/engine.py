from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from ..rules.catalog import CHARACTERISTICS, canonical
from ..rules.coc import DIFFICULTY_ZH, max_of
from .runtime import PluginError, dice_expression, execute, verify
from .schema import Output, Plugin

if TYPE_CHECKING:
    from ..engine.game import Game, Turn


class Manager:
    def __init__(self, game: Game):
        self.game = game
        self.verified: set[str] = set()
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
            if hook == "action" and details.get("plugin") != plugin.id:
                continue
            if plugin.id in game.state.plugin_errors:
                continue
            try:
                if plugin.fingerprint not in self.verified:
                    if problems := await verify(plugin):
                        raise PluginError("；".join(problems))
                    self.verified.add(plugin.fingerprint)
                    game.state.plugin_sources[plugin.id] = plugin.model_dump(mode="json")
                if plugin.id not in game.state.plugin_data and hook != "start" and "start" in plugin.hooks:
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

    async def _once(self, plugin: Plugin, event: dict, turn: Turn, token: str, batch: str) -> bool:
        from ..engine.game import _check_view

        game, state = self.game, self.game.state
        context = {
            "state": state.plugin_data.get(plugin.id, plugin.initial), "previous_version": state.plugin_versions.get(plugin.id, ""),
            "turn": state.turn, "minutes": state.minutes, "location": state.location,
            "flags": list(state.flags), "clues": list(state.clues),
            "investigators": [{key: value for key, value in inv.model_dump(mode="json").items()
                               if key in ("id", "name", "characteristics", "hp", "san", "inventory", "conditions")}
                              for inv in state.investigators],
        }
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

        result = await execute(plugin, event, context, request)
        party, events, flags, clues, notes = self._prepare(plugin, result)
        # All references, capabilities, sheet changes and rule effects are checked before committing.
        state.investigators = party
        state.plugin_data[plugin.id] = result.state
        state.plugin_versions[plugin.id] = plugin.version
        state.plugin_receipts = [*state.plugin_receipts, token, *([] if result.repeat else [batch])][-256:]
        turn.notes += notes
        turn.notes += [f"{event['who']}：{event['text']}" for event in checks]
        for event in checks + events:
            if event["type"] in ("dice", "sanity", "damage"):
                state.add(event["type"], event.get("text", "") or f"{event.get('skill', '检定')}：{event.get('value', '')}", who=event.get("who", ""))
            await game.emit(event)
        await game._flags(flags, turn)
        await game._gain(clues, turn, None)
        if game.checkpoint:
            game.checkpoint()
        return result.repeat

    def _prepare(self, plugin: Plugin, output: Output):
        from ..engine.game import _sanity_view

        game = self.game
        party = [inv.model_copy(deep=True) for inv in game.state.investigators]
        people = {inv.id: inv for inv in party}
        flags, clues, events, notes = [], [], [], []
        for effect in output.effects:
            if effect.kind in ("attribute", "condition", "damage", "sanity") and effect.who not in people:
                raise PluginError(f"效果涉及不存在的调查员 {effect.who}")
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
        for effect in output.effects:
            inv = people.get(effect.who)
            if effect.kind == "note":
                notes.append(effect.text)
            elif effect.kind == "flag":
                flags.append(effect.key)
            elif effect.kind == "clue":
                clues.append(effect.key)
            elif effect.kind == "attribute":
                key = canonical(effect.key)
                before = inv.characteristics[key]
                inv.characteristics[key] = max(0, min(999, before + effect.amount))
                inv.hp = min(inv.hp, inv.hp_max)
                notes.append(f"{inv.name}的{key} {before} → {inv.characteristics[key]}")
                events.append({"type": "sheet"})
            elif effect.kind == "condition":
                if effect.amount == 1:
                    inv.add_condition(effect.key)
                elif effect.key in inv.conditions:
                    inv.conditions.remove(effect.key)
                events.append({"type": "sheet"})
            elif effect.kind == "damage":
                result = game.rules.damage(inv, effect.amount)
                events.append({"type": "damage", "who": inv.name, "text": result.describe()})
                notes.append(f"{inv.name}{result.describe()}")
            elif effect.kind == "sanity":
                result = game.rules.sanity(inv, effect.success, effect.failure)
                events.append({"type": "sanity", "who": inv.name, **_sanity_view(result)})
                notes.append(f"{inv.name}{result.describe()}")
        return party, events, flags, clues, notes
