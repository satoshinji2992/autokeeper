from __future__ import annotations

import json
import shutil

import pytest
from textual.widgets import Button, Static

from autokeeper import config
from autokeeper.agents.cast import Action, Ruling
from autokeeper.agents.director import Improv
from autokeeper.config import EXAMPLE
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.harness.tools import ToolError, Workspace
from autokeeper.module import improv
from autokeeper.module.compiler import compile_module
from autokeeper.module.importer import validate_all
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.plugins.author import plugin_tool
from autokeeper.plugins.runtime import PluginError, execute, verify
from autokeeper.plugins.schema import Action as PluginAction
from autokeeper.plugins.schema import Call, Case, Plugin, check
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.plugins import PluginsModal

from .test_engine import make
from .test_net import TableCast

COUNT = '''def handle(event, ctx, api):
    state = dict(ctx["state"])
    state["count"] = state.get("count", 0) + 1
    return {"state": state, "effects": []}
'''


def counter(**changes):
    return Plugin(id="counter", name="计数", description="测试插件", code=COUNT,
                  tests=[Case(name="first", expected_state={"count": 1})]).model_copy(update=changes)


async def test_generated_plugin_tests_execute_and_source_survives_compilation(tmp_path):
    work = tmp_path / "module"
    shutil.copytree(EXAMPLE, work)
    tool = plugin_tool(Workspace(work, writable=("module.yaml", "plugins/*")))
    plugin = counter()
    assert not await verify(plugin)
    assert "通过 1 项测试" in await tool.run(plugin.model_dump_json())
    module = Module.load(work)
    assert module.plugins == ["counter"] and not module.extension_errors
    assert module.extensions["counter"].code == COUNT and not await validate_all(work)
    skill = compile_module(module, tmp_path / "skills")
    assert (skill / "assets/plugins/counter/plugin.py").read_text() == COUNT
    assert "自定义规则插件" in (skill / "SKILL.md").read_text()
    assert "api.roll" in (skill / "references/plugins.md").read_text()
    broken = plugin.model_copy(update={"code": "def handle(e, c, a):\n    return {'state':{},'effects':[]}"})
    with pytest.raises(ToolError, match="测试"):
        await tool.run(broken.model_copy(update={"version": "0.2.0"}).model_dump_json())
    assert Module.load(work).extensions["counter"].code == COUNT
    with pytest.raises(ToolError, match="version"):
        await tool.run(broken.model_dump_json())


async def test_effect_defaults_do_not_make_correct_generated_tests_fail():
    effect = {"kind": "sanity", "who": "investigator", "success": "0", "failure": "1D6"}
    code = f"def handle(e,c,a):\n    return {{'state':{{}},'effects':[{effect!r}]}}"
    plugin = counter(code=code, capabilities=["sanity"], tests=[Case(name="defaults", expected_effects=[effect])])
    assert not await verify(plugin)
    invalid = plugin.model_copy(update={"code": code.replace("'failure'", "'wrong_field'")})
    problems = await verify(invalid)
    assert any("wrong_field" in problem for problem in problems)


@pytest.mark.parametrize("code", ["import os", "open('file')", "x.__class__", "eval('1')"])
def test_plugins_cannot_import_or_access_private_runtime_objects(code):
    assert check(counter(code=COUNT + "\n" + code))


async def test_hanging_plugin_is_killed_and_does_not_hang_the_host():
    plugin = counter(code="def handle(e,c,a):\n    while True:\n        pass")

    async def no_requests(message):
        raise AssertionError("no RPC")

    with pytest.raises(PluginError, match="超时"):
        await execute(plugin, {"hook": "turn"}, {"state": {}}, no_requests, timeout=0.2)


async def test_receipts_and_state_survive_reload_without_repeating_effects(fake):
    module = Module.load(EXAMPLE)
    module.plugins = ["counter"]
    module.extensions = {"counter": counter()}
    game, _, _ = make(module, fake, {})
    await game.plugins.run("turn", Turn())
    assert game.state.plugin_data == {"counter": {"count": 1}}
    restored = GameState.model_validate_json(game.state.model_dump_json())
    game.state = restored
    await game.plugins.run("turn", Turn())
    assert restored.plugin_data["counter"]["count"] == 1
    game.state.turn += 1
    await game.plugins.run("turn", Turn())
    assert restored.plugin_data["counter"]["count"] == 2


async def test_plugin_dice_and_checks_use_the_hosts_actual_rules(fake):
    module = Module.load(EXAMPLE)
    who = module.pregens[0].id
    code = '''def handle(event, ctx, api):
    who = ctx["investigators"][0]["id"]
    dice = api.roll("2D10")
    result = api.check(who, "体质")
    loss = min(dice) if result["passed"] else max(dice)
    return {"state": {"loss": loss}, "effects": [{"kind":"attribute","who":who,"key":"体质","amount":-loss}]}
'''
    plugin = counter(code=code, capabilities=["dice", "attribute"], tests=[Case(
        name="success", dice=[2, 9], checks=[True], context={"investigators": [{"id": who}]},
        expected_state={"loss": 2}, expected_effects=[{"kind": "attribute", "who": who, "key": "体质", "amount": -2}],
    )])
    assert not await verify(plugin)
    module.extensions = {plugin.id: plugin}
    game, _, events = make(module, fake, {})
    before = game.state.investigator.characteristics["体质"]
    fake.queue_totals(3, 8).queue(95)
    await game.plugins.run("turn", Turn())
    assert game.state.plugin_data[plugin.id]["loss"] == 8
    assert game.state.investigator.characteristics["体质"] == before - 8
    assert any(event["type"] == "dice" and event["value"] == 95 for event in events)
    assert not fake.values and not fake.totals


async def test_catchup_periods_use_updated_attributes_and_do_not_repeat_after_reload(fake):
    module = Module.load(EXAMPLE)
    who = module.pregens[0].id
    code = '''def handle(e,c,a):
    state = dict(c["state"])
    results = list(state.get("results", []))
    results.append(a.check(c["investigators"][0]["id"], "体质")["passed"])
    state["results"] = results
    return {"state":state,"effects":[{"kind":"attribute","who":c["investigators"][0]["id"],"key":"体质","amount":-5}],"repeat":len(results)<2}
'''
    plugin = counter(code=code, capabilities=["dice", "attribute"], tests=[Case(
        name="first period", checks=[True], context={"investigators": [{"id": who}]},
        expected_state={"results": [True]}, expected_effects=[{"kind": "attribute", "who": who, "key": "体质", "amount": -5}],
    )])
    module.extensions = {plugin.id: plugin}
    game, _, events = make(module, fake, {})
    before = game.state.investigator.characteristics["体质"]
    fake.queue(before, before)
    await game.plugins.run("turn", Turn())
    assert game.state.plugin_data[plugin.id]["results"] == [True, False]
    assert game.state.investigator.characteristics["体质"] == before - 10
    await game.plugins.run("turn", Turn())
    assert sum(event["type"] == "dice" for event in events) == 2


async def test_bad_effects_are_rejected_before_any_state_or_sheet_is_committed(fake):
    module = Module.load(EXAMPLE)
    who = module.pregens[0].id
    effects = [{"kind": "attribute", "who": who, "key": "体质", "amount": -5},
               {"kind": "flag", "key": "not_declared"}]
    code = f"def handle(e,c,a):\n    return {{'state':{{'broken':True}},'effects':{effects!r}}}"
    bad = counter(id="bad", code=code, capabilities=["attribute", "flag"], tests=[Case(name="bad", expected_effects=effects)])
    module.extensions = {"bad": bad, "counter": counter()}
    game, _, events = make(module, fake, {})
    before = game.state.investigator.characteristics["体质"]
    await game.plugins.run("turn", Turn())
    assert game.state.investigator.characteristics["体质"] == before
    assert "bad" not in game.state.plugin_data and "bad" in game.state.plugin_errors
    assert game.state.plugin_data["counter"] == {"count": 1}
    assert any(event["type"] == "plugin_status" for event in events)


async def test_improvised_plugin_is_validated_installed_and_restored_from_the_save(fake):
    module = Module.load(EXAMPLE)
    plan = Improv(kind="展开", reason="缺少计数规则", plugins=[counter()])
    assert not improv.check(module, plan, at=module.start)
    game, _, _ = make(module, fake, {})
    await game._land("展开", plan)
    await game.plugins.run("turn", Turn())
    restored = GameState.model_validate_json(game.state.model_dump_json())
    fresh = Module.load(EXAMPLE)
    game2 = Game(fresh, restored, game.cast, game.rules)
    assert fresh.extensions["counter"].code == COUNT
    await game2.plugins.run("turn", Turn())
    assert restored.plugin_data["counter"]["count"] == 1
    assert improv.check(fresh, plan, at=fresh.start)  # an upgrade cannot silently replace the same version


async def test_declared_plugin_actions_are_available_to_the_turn_engine(fake):
    module = Module.load(EXAMPLE)
    plugin = counter(hooks=["action"], actions=[PluginAction(name="count", description="计数一次，无参数")])
    module.extensions = {plugin.id: plugin}
    game, _, _ = make(module, fake, {})
    action = Action(kind="investigate", summary="计数")
    ruling = Ruling(needs_roll=False, minutes=0, plugin_calls=[Call(plugin="counter", action="count")])
    await game.resolve(action, ruling)
    assert game.state.plugin_data["counter"] == {"count": 1}


async def test_mcp_keeper_can_invoke_a_plugin_twice_as_two_distinct_actions(monkeypatch, tmp_path, fake):
    from autokeeper import mcp_server as server

    module = Module.load(EXAMPLE)
    plugin = counter(hooks=["action"], actions=[PluginAction(name="count", description="计数")])
    module.extensions = {plugin.id: plugin}
    game, _, _ = make(module, fake, {})
    monkeypatch.setattr(server.S, "game", game)
    monkeypatch.setattr(server.S, "module", module)
    monkeypatch.setattr(config, "SAVES", tmp_path)
    assert server.list_plugins()[0]["actions"][0]["name"] == "count"
    for _ in range(2):
        result = await server.plugin_action("counter", "count")
        assert not result["error"]
    assert game.state.plugin_data["counter"] == {"count": 2}


async def test_updated_improvised_plugin_keeps_existing_state(fake):
    module = Module.load(EXAMPLE)
    module.extensions = {"counter": counter()}
    module.plugins = ["counter"]
    game, _, _ = make(module, fake, {})
    await game.plugins.run("turn", Turn())
    plan = Improv(kind="展开", reason="升级", plugins=[counter(version="0.2.0")])
    assert not improv.check(module, plan, at=module.start)
    await game._land("展开", plan)
    game.state.turn += 1
    await game.plugins.run("turn", Turn())
    assert game.state.plugin_data["counter"] == {"count": 2}
    assert game.state.plugin_versions["counter"] == "0.2.0"


async def test_a_save_keeps_its_plugin_source_even_if_the_module_is_upgraded(fake):
    module = Module.load(EXAMPLE)
    module.extensions = {"counter": counter()}
    module.plugins = ["counter"]
    game, _, _ = make(module, fake, {})
    await game.plugins.run("turn", Turn())
    restored = GameState.model_validate_json(game.state.model_dump_json())
    upgraded = Module.load(EXAMPLE)
    upgraded.extensions = {"counter": counter(version="9.0.0", code=COUNT.replace("+ 1", "+ 100"))}
    resumed = Game(upgraded, restored, game.cast, game.rules)
    assert upgraded.extensions["counter"].version == "0.1.0"
    restored.turn += 1
    await resumed.plugins.run("turn", Turn())
    assert restored.plugin_data["counter"] == {"count": 2}


async def test_switching_plugins_off_preserves_their_data_for_later(fake):
    module = Module.load(EXAMPLE)
    module.extensions = {"counter": counter()}
    game, cast, _ = make(module, fake, {})
    cast.cfg = config.Config(plugins=False)
    await game.plugins.run("turn", Turn())
    assert not game.state.plugin_data
    cast.cfg.plugins = True
    await game.plugins.run("turn", Turn())
    assert game.state.plugin_data["counter"] == {"count": 1}
    game.state.plugin_errors["counter"] = "已手动暂停"
    game.state.turn += 1
    await game.plugins.run("turn", Turn())
    assert game.state.plugin_data["counter"] == {"count": 1}


def test_missing_or_unsafe_plugin_is_an_import_error_but_the_base_module_still_loads(tmp_path):
    work = tmp_path / "module"
    shutil.copytree(EXAMPLE, work)
    module = Module.load(work)
    module.plugins = ["missing"]
    module.save(work)
    loaded = Module.load(work)
    assert "missing" in loaded.extension_errors and not loaded.extensions
    assert loaded.locations and not loaded.extensions
    assert "extensions" not in json.loads(loaded.model_dump_json())


def test_network_players_do_not_receive_plugin_code_or_hidden_state(fake):
    hub = Hub(EXAMPLE, cast=TableCast(), rules=Rules(dice=fake))
    plugin = counter(id="private_rule")
    hub.m.plugins = [plugin.id]
    hub.m.extensions = {plugin.id: plugin}
    hub.state.plugin_sources[plugin.id] = plugin.model_dump()
    hub.state.plugin_data[plugin.id] = {"hidden_count": 54321}
    hub.state.plugin_errors[plugin.id] = "private traceback"
    welcome = json.dumps(hub._welcome("player"), ensure_ascii=False)
    assert not any(secret in welcome for secret in ("private_rule", "hidden_count", "54321", "private traceback", "def handle"))


async def test_plugin_controls_pause_without_exposing_private_rules_or_resetting_state():
    module = Module.load(EXAMPLE)
    module.plugins = ["counter"]
    module.extensions = {"counter": counter()}
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.plugin_data["counter"] = {"secret": "幕后计数 54321"}
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:
        modal = PluginsModal(module, state)
        app.push_screen(modal)
        await pilot.pause()
        assert "54321" not in "".join(str(widget.render()) for widget in modal.query(Static))
        button = modal.query_one("#plugin-0", Button)
        button.press()
        await pilot.pause()
        assert "counter" in state.plugin_errors and state.plugin_data["counter"]["secret"] == "幕后计数 54321"
        button.press()
        await pilot.pause()
        assert "counter" not in state.plugin_errors
