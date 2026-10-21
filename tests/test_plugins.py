from __future__ import annotations

import json
import shutil

import pytest
from textual.widgets import Button, Static

from autokeeper import config, paths
from autokeeper.agents.cast import Action, Ruling
from autokeeper.agents.director import Improv
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.harness.tools import ToolError, Workspace
from autokeeper.module import improv
from autokeeper.module.compiler import compile_module
from autokeeper.module.importer import validate_all
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.plugins.author import plugin_tool
from autokeeper.plugins.runtime import PluginError, execute, verify
from autokeeper.plugins.schema import Action as PluginAction
from autokeeper.plugins.schema import Call, Case, Plugin, check
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.plugins import PluginsModal

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
    assert (skill / "assets/plugins/counter/plugin.py").read_text(encoding="utf-8") == COUNT
    assert "自定义规则插件" in (skill / "SKILL.md").read_text(encoding="utf-8")
    assert "api.roll" in (skill / "references/plugins.md").read_text(encoding="utf-8")
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


async def test_the_arbiter_may_name_the_investigator_and_a_slip_does_not_pause_the_rules(fake):
    """《有些人油盐不进》: the arbiter called dodge_escape with who="贝蒂·康西丁" where the rule
    wants the id; the luck check found nobody, the plugin was paused and five later calls did
    nothing. A name is the same person; an action the plugin lacks is a note, not a breakdown."""
    module = Module.load(EXAMPLE)
    inv = module.pregens[0]
    code = '''def handle(event, ctx, api):
    state = ctx["state"]
    if event.get("action") == "dodge":
        result = api.check(event["arguments"]["who"], "幸运")
        state["escaped"] = state.get("escaped", 0) + (1 if result["passed"] else 0)
    return {"state": state, "effects": []}
'''
    plugin = counter(code=code, hooks=["action"], capabilities=["dice"], initial={},
                     actions=[PluginAction(name="dodge", description="闪避成功后掷幸运脱身。arguments: who（调查员 id）")],
                     tests=[Case(name="dodge", event={"hook": "action", "action": "dodge", "arguments": {"who": inv.id}},
                                 checks=[True], context={"investigators": [{"id": inv.id}]}, expected_state={"escaped": 1})])
    assert not await verify(plugin)
    module.extensions = {plugin.id: plugin}
    game, _, _ = make(module, fake, {})
    fake.queue(1)  # the luck check passes
    named = Ruling(needs_roll=False, minutes=0, plugin_calls=[Call(plugin="counter", action="dodge", arguments={"who": inv.name})])
    turn = await game.resolve(Action(kind="other", summary="冲向侧门"), named)
    assert game.state.plugin_data["counter"]["escaped"] == 1 and "counter" not in game.state.plugin_errors
    wrong = Ruling(needs_roll=False, minutes=0, plugin_calls=[Call(plugin="counter", action="run_away")])
    turn = await game.resolve(Action(kind="other", summary="再跑"), wrong)
    assert "counter" not in game.state.plugin_errors and any("run_away" in n for n in turn.notes)


# -- sandbox: typical escape attempts are refused --------------------------------------------
ESCAPES = {
    "import a module": "import os",
    "import inside a function body": "def f():\n        import socket",
    "dunder attribute": 'ctx["x"] = {}.__class__',
    "subclasses chain": 'ctx["x"] = ().__class__.__bases__',
    "generator frame": 'g = (i for i in [1])\n    ctx["x"] = g.gi_frame',
    "frame globals": 'ctx["x"] = ctx.f_globals',
    "str.format traversal": 'ctx["x"] = "{0.__class__}".format(api)',
    "getattr": 'ctx["x"] = getattr(api, "roll")',
    "eval": 'eval("1")',
    "exec": 'exec("x = 1")',
    "compile": 'compile("1", "x", "eval")',
    "open a file": 'open("/etc/passwd")',
    "type introspection": 'ctx["x"] = type(1)',
    "object base": 'ctx["x"] = object()',
    "dunder name": 'ctx["x"] = __builtins__',
}


@pytest.mark.parametrize("snippet", list(ESCAPES.values()), ids=list(ESCAPES))
def test_escape_attempts_are_refused_before_a_plugin_runs(snippet):
    body = COUNT.replace('    return', f"    {snippet}\n    return")
    assert check(counter(code=body)), f"未拦截：{snippet}"


def test_the_audit_hook_refuses_file_network_subprocess_and_import():
    """The worker's third wall: even if a builtin leaked, these events are refused. The second
    wall is the allow-list (open/__import__/socket are simply absent for plugin code)."""
    from autokeeper.plugins import worker

    for event in ("open", "import", "os.system", "socket.connect", "subprocess.Popen", "ctypes.dlopen", "shutil.rmtree"):
        with pytest.raises(PermissionError):
            worker.audit(event, ())
    worker.audit("compile", ())  # the worker compiles the plugin itself; that is allowed


def test_the_worker_has_no_open_import_or_type_even_fed_code_that_skips_the_ast_check():
    """Feed the worker code the AST check would reject, straight into the subprocess: the
    allow-listed builtins mean open/__import__/type are not defined, so nothing dangerous runs."""
    import subprocess
    import sys
    from pathlib import Path

    from autokeeper.plugins import worker as worker_mod

    path = str(Path(worker_mod.__file__).resolve())
    for attempt in ("open('/etc/passwd')", "__import__('os')", "type(1)"):
        probe = f"def handle(e, c, a):\n    {attempt}\n    return {{'state': {{}}, 'effects': []}}"
        payload = json.dumps({"code": probe, "event": {"hook": "turn"}, "context": {"state": {}}})
        done = subprocess.run([sys.executable, "-I", "-S", path], input=payload + "\n",
                              capture_output=True, text=True, env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        message = json.loads(done.stdout.strip().splitlines()[-1])
        assert "error" in message and "NameError" in message["error"], (attempt, message)


async def test_a_memory_blowup_does_not_bring_down_the_host():
    pytest.importorskip("resource")  # the cap is a POSIX resource limit; the wall-clock kill guards elsewhere
    plugin = counter(code="def handle(e, c, a):\n    big = [0] * 500000000\n    return {'state': {}, 'effects': []}")

    async def no_requests(message):
        raise AssertionError("no RPC")

    with pytest.raises(PluginError):
        await execute(plugin, {"hook": "turn"}, {"state": {}}, no_requests, timeout=5)


# -- public status, a recognisable line, and the recent-effects log --------------------------
async def test_a_public_status_and_a_compact_effect_line_reach_the_story_and_the_ctrl_k_log(fake):
    module = Module.load(EXAMPLE)
    code = ('def handle(event, ctx, api):\n'
            '    return {"state": {}, "status": "风寒：第 2 天", "effects": [{"kind": "note", "text": "夜里更冷了"}]}')
    plugin = counter(code=code, capabilities=["note"], tests=[Case(
        name="tick", expected_state={}, expected_effects=[{"kind": "note", "text": "夜里更冷了"}])])
    assert not await verify(plugin)
    module.extensions = {plugin.id: plugin}
    game, _, events = make(module, fake, {})
    await game.plugins.run("turn", Turn())
    assert game.state.plugin_status[plugin.name] == "风寒：第 2 天"
    assert game.state.plugin_log and game.state.plugin_log[-1]["name"] == plugin.name
    assert "夜里更冷了" in game.state.plugin_log[-1]["line"]
    effect = next(e for e in events if e["type"] == "plugin_effect")
    assert effect["name"] == plugin.name and "夜里更冷了" in effect["line"]


# -- house rules from a sentence -------------------------------------------------------------
class AuthorCast:
    """A canned author: hands back prepared plugins in order (None once exhausted), recording
    the request and the problems it was told to fix. Stands in for cast.author_plugin in tests."""

    def __init__(self, *plugins):
        self.plugins = list(plugins)
        self.seen: list[tuple[str, list | None]] = []

    async def author_plugin(self, state, request, problems=None):
        self.seen.append((request, problems))
        return self.plugins.pop(0) if self.plugins else None


async def test_a_house_rule_is_tested_and_installed_into_this_save_only(fake):
    from autokeeper.plugins.author import house_rule

    module = Module.load(EXAMPLE)
    before = module.model_dump_json()
    cast = AuthorCast(counter(id="meals", name="吃饭", description="每过一夜要吃一顿饭。"))
    ok, message = await house_rule(cast, module, Game.new(module, [module.pregens[0].model_copy(deep=True)]), "每过一夜要吃饭")
    assert ok and "吃饭" in message
    assert module.plugins == ["meals"] and "meals" in module.extensions
    assert not (EXAMPLE / "plugins" / "meals").exists()  # never written into the module's files
    assert Module.load(EXAMPLE).model_dump_json() == before  # the module on disk is untouched


async def test_an_installed_house_rule_rides_the_save_and_runs(fake):
    from autokeeper.plugins.author import house_rule

    module = Module.load(EXAMPLE)
    cast = AuthorCast(counter(id="meals"))
    game, _, _ = make(module, fake, {})
    ok, _ = await house_rule(cast, module, game.state, "每过一夜要吃饭")
    assert ok and game.state.plugin_sources["meals"]
    restored = GameState.model_validate_json(game.state.model_dump_json())
    fresh = Module.load(EXAMPLE)
    resumed = Game(fresh, restored, game.cast, game.rules)
    assert fresh.extensions["meals"].id == "meals"  # the Manager rebuilt it from the save
    await resumed.plugins.run("turn", Turn())
    assert restored.plugin_data["meals"] == {"count": 1}


async def test_a_failing_house_rule_is_retried_then_explained(fake):
    from autokeeper.plugins.author import house_rule

    broken = counter(id="bad", code="def handle(e, c, a):\n    return {'state': {}, 'effects': []}")  # count never set
    good = counter(id="bad")

    retry = AuthorCast(broken, good)  # first draft fails its tests, the rewrite passes
    m1 = Module.load(EXAMPLE)
    ok, _ = await house_rule(retry, m1, Game.new(m1, [m1.pregens[0].model_copy(deep=True)]), "加个计数")
    assert ok and len(retry.seen) == 2 and retry.seen[1][1]  # the second try was handed the problems

    fail = AuthorCast(broken, broken)  # both drafts fail
    m2 = Module.load(EXAMPLE)
    ok2, message = await house_rule(fail, m2, Game.new(m2, [m2.pregens[0].model_copy(deep=True)]), "加个计数")
    assert not ok2 and "没能装上" in message and not m2.plugins


async def test_a_house_rule_cannot_silently_replace_a_rule_of_the_same_version(fake):
    from autokeeper.plugins.author import house_rule

    module = Module.load(EXAMPLE)
    module.extensions = {"counter": counter()}
    module.plugins = ["counter"]
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    changed = counter(code=COUNT.replace("+ 1", "+ 2"))  # same id and version, different code
    ok, message = await house_rule(AuthorCast(changed, changed), module, state, "改计数")
    assert not ok and "version" in message
    assert module.extensions["counter"].code == COUNT  # the installed rule is unchanged


# -- custom actions are discoverable ---------------------------------------------------------
def test_command_suggester_and_f5_know_the_custom_actions():
    from autokeeper.tui.modals.plugins import action_hints
    from autokeeper.tui.widgets.panels import command_suggester

    module = Module.load(EXAMPLE)
    plugin = counter(hooks=["action"], actions=[PluginAction(name="hide", description="躲起来避开追兵。arguments: who")])
    module.extensions = {plugin.id: plugin}
    module.plugins = [plugin.id]
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    hints = action_hints(module, state)
    assert hints == ["躲起来避开追兵"]
    suggester = command_suggester(module.pregens[0], hints)
    assert "躲起来避开追兵" in suggester._suggestions
    state.plugin_errors["counter"] = "已手动暂停"  # a paused rule's actions drop out
    assert action_hints(module, state) == []


# -- the Ctrl+K window explains each rule in player terms ------------------------------------
async def test_ctrl_k_explains_rules_without_exposing_private_state():
    module = Module.load(EXAMPLE)
    plugin = counter(hooks=["action"], description="追踪体温，太冷会受伤。",
                     actions=[PluginAction(name="warm", description="靠近火堆取暖时调用。arguments: who")])
    module.plugins = ["counter"]
    module.extensions = {"counter": plugin}
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.plugin_data["counter"] = {"secret_counter": 4321}  # private: must not show
    state.plugin_status["计数"] = "体温：偏低"
    state.plugin_log = [{"turn": 1, "name": "计数", "line": "陈墨冻得发抖"}]
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(90, 40)) as pilot:
        modal = PluginsModal(module, state, manage=True)
        app.push_screen(modal)
        await pilot.pause()
        shown = "".join(str(w.render()) for w in modal.query(Static))
        assert "追踪体温，太冷会受伤。" in shown  # what it does
        assert "靠近火堆取暖时调用" in shown  # a custom action a player can try
        assert "体温：偏低" in shown and "陈墨冻得发抖" in shown  # public status and recent effect
        assert "4321" not in shown  # the private counter never leaks


async def test_ctrl_k_shows_a_paused_rule_reason_in_plain_words():
    module = Module.load(EXAMPLE)
    module.plugins = ["counter"]
    module.extensions = {"counter": counter()}
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.plugin_errors["counter"] = "插件执行超时"
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(90, 30)) as pilot:
        modal = PluginsModal(module, state, manage=True)
        app.push_screen(modal)
        await pilot.pause()
        shown = "".join(str(w.render()) for w in modal.query(Static))
        assert "已暂停" in shown and "插件执行超时" in shown
        assert "Traceback" not in shown


async def test_ctrl_k_house_rule_box_installs_through_the_drafter():
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])

    async def drafter(text, progress):
        await progress("写规则…")
        from autokeeper.plugins.author import install

        install(module, state, counter(id="meals", name="吃饭", description="每夜一餐。"))
        return True, "已装上「吃饭」"

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(90, 30)) as pilot:
        modal = PluginsModal(module, state, manage=True, drafter=drafter)
        app.push_screen(modal)
        await pilot.pause()
        from textual.widgets import Input

        box = modal.query_one("#house-rule", Input)
        box.value = "每过一夜要吃饭"
        box.post_message(Input.Submitted(box, box.value))  # as if the player pressed Enter
        await pilot.pause()
        await modal.workers.wait_for_complete()
        await pilot.pause()
        shown = "".join(str(w.render()) for w in modal.query(Static))
        assert "吃饭" in shown and "meals" in module.plugins


# -- a LAN table sees the same public information, redacted ----------------------------------
def test_network_players_get_redacted_public_plugin_info_but_no_id_or_source(fake):
    hub = Hub(EXAMPLE, cast=TableCast(), rules=Rules(dice=fake))
    plugin = counter(id="private_rule", name="家规", description="玩家看得见的说明。",
                     hooks=["action"], actions=[PluginAction(name="try_it", description="合适时调用。")])
    hub.m.plugins = [plugin.id]
    hub.m.extensions = {plugin.id: plugin}
    hub.state.plugin_sources[plugin.id] = plugin.model_dump()
    hub.state.plugin_data[plugin.id] = {"hidden": 54321}
    hub.state.plugin_status["家规"] = "进行中"
    hub.state.plugin_log = [{"turn": 1, "name": "家规", "line": "发生了一件事"}]
    welcome = json.dumps(hub._welcome("player"), ensure_ascii=False)
    assert not any(secret in welcome for secret in ("private_rule", "hidden", "54321", "def handle"))
    info = hub._book()["plugin_info"]
    assert info and info[0]["name"] == "家规" and info[0]["description"] == "玩家看得见的说明。"
    assert info[0]["actions"] == [["try_it", "合适时调用。"]] and "id" not in info[0]
    view = hub._view()  # the public status and recent effects are shared, keyed by name
    assert view["plugin_status"] == {"家规": "进行中"} and view["plugin_log"][0]["name"] == "家规"


async def test_a_lan_house_rule_is_drafted_and_installed_by_the_host(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")

    class HostCast(TableCast, AuthorCast):
        def __init__(self, *plugins):
            TableCast.__init__(self)
            AuthorCast.__init__(self, *plugins)

    hub = Hub(EXAMPLE, cast=HostCast(counter(id="meals", name="一日三餐", description="每夜一餐。")), rules=Rules(dice=fake))
    hub.rules.put_nowait(("host", "每过一夜要吃饭"))
    await hub._house_rules()
    assert "meals" in hub.m.extensions and "meals" in hub.m.plugins
    assert hub.state.plugin_sources["meals"]
    info = {i["name"] for i in hub._book()["plugin_info"]}
    assert "一日三餐" in info  # the new rule reaches the table's redacted book, by name
