"""Interrupted plugin settlement reuses its output and dice, including after reload."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.plugins import engine
from autokeeper.plugins.schema import Action as PluginAction
from autokeeper.plugins.schema import Call, Case, Output, Plugin

from .test_engine import make


def table(fake, monkeypatch, *, hook="turn", prompt=False, repeat=False):
    module = Module.load(EXAMPLE)
    module.flags["plugin_found"] = "规则已结算"
    clue = module.clue("c_notice")
    clue.handout, clue.sanity = "h_diary", "1/1"
    plugin = Plugin(id="recover", name="恢复测试", description="规则结算回归", hooks=[hook],
                    capabilities=["dice", "attribute", "flag", "clue", "prompt"],
                    code='def handle(e,c,a):\n    return {"state":{},"effects":[]}',
                    tests=[Case(name="base", expected_state={})])
    module.extensions = {plugin.id: plugin}
    game, _, _ = make(module, fake, {})
    game.plugins.verified.add(plugin.fingerprint)
    game.state.human = [game.state.investigator.id]
    calls = []

    async def execute(spec, event, context, request):
        calls.append(deepcopy(event))
        rolled = await request({"request": "roll", "expression": "1D6"})
        count = context["state"].get("count", 0) + 1
        effects = [{"kind": "attribute", "who": context["acting"], "key": "体质", "amount": -rolled[0]}]
        if prompt:
            effects.append({"kind": "prompt", "who": context["acting"], "key": "choice",
                            "text": "选一枚棋子", "options": ["黑", "白"]})
        elif not repeat:
            effects += [{"kind": "flag", "key": "plugin_found"}, {"kind": "clue", "key": "c_notice"}]
        return Output.model_validate({"state": {"count": count}, "effects": effects, "repeat": repeat and count < 2})

    monkeypatch.setattr(engine, "execute", execute)
    return game, plugin, calls


def reload(game, plugin, events, saved_json=None):
    saved = GameState.model_validate_json(saved_json or game.state.model_dump_json())

    async def emit(event):
        events.append(event)

    resumed = Game(game.m, saved, game.cast, game.rules, emit=emit)
    resumed.plugins.verified.add(plugin.fingerprint)
    return resumed


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize("at", ["plugin_effect", "sheet", "flag", "clue", "handout", "sanity"])
async def test_late_effects_resume_after_reload_without_reexecution_or_reroll(fake, monkeypatch, at, failure):
    game, plugin, calls = table(fake, monkeypatch)
    before = game.state.investigator.characteristics["体质"]
    san = game.state.investigator.san
    fake.queue_totals(4).queue(50)
    checkpoints = []
    game.checkpoint = lambda: checkpoints.append(game.state.model_dump_json())

    async def interrupted(event):
        if event["type"] == at:
            raise failure("显示中断")

    game.emit = interrupted
    with pytest.raises(failure):
        await game.plugins.run("turn", Turn())
    assert checkpoints and len(calls) == 1
    assert game.state.plugin_data[plugin.id] == {"count": 1}
    assert game.state.investigator.characteristics["体质"] == before - 4
    assert not game.state.plugin_receipts and plugin.id not in game.state.plugin_errors
    assert game._resolution is None

    events = []
    resumed = reload(game, plugin, events, checkpoints[-1])
    turn = Turn()
    await resumed.plugins.run("turn", turn)
    await resumed.plugins.run("turn", Turn())
    assert len(calls) == 1 and not fake.totals and not fake.values
    assert resumed.state.investigator.characteristics["体质"] == before - 4
    assert resumed.state.investigator.san == san - 1
    assert resumed.state.flags.count("plugin_found") == 1
    assert resumed.state.clues.count("c_notice") == 1
    assert resumed.state.handouts.count("h_diary") == 1
    assert len(resumed.state.plugin_log) == 1
    assert len([entry for entry in resumed.state.log if entry.kind == "sanity"]) == 1
    assert at in {event["type"] for event in events}
    assert "c_notice" in turn.clues and sum("体质" in note for note in turn.notes) == 1
    assert "_settlement" not in resumed.state.plugin_marks[plugin.id]
    assert resumed._resolution is None


async def test_execution_cancel_replays_cached_dice_instead_of_rolling_again(fake, monkeypatch):
    game, plugin, calls = table(fake, monkeypatch)
    original = engine.execute
    first = True

    async def cancelled(spec, event, context, request):
        nonlocal first
        if first:
            first = False
            assert await request({"request": "roll", "expression": "1D6"}) == [4]
            raise asyncio.CancelledError
        return await original(spec, event, context, request)

    monkeypatch.setattr(engine, "execute", cancelled)
    fake.queue_totals(4).queue(50)
    with pytest.raises(asyncio.CancelledError):
        await game.plugins.run("turn", Turn())
    assert not game.state.plugin_data and not fake.totals
    resumed = reload(game, plugin, [])
    await resumed.plugins.run("turn", Turn())
    assert len(calls) == 1 and resumed.state.plugin_data[plugin.id] == {"count": 1}
    assert not fake.totals and not fake.values


async def test_prompt_resume_keeps_the_original_id_and_rate_mark(fake, monkeypatch):
    game, plugin, calls = table(fake, monkeypatch, prompt=True)
    fake.queue_totals(3)

    async def interrupted(event):
        if event["type"] == "plugin_prompt":
            raise asyncio.CancelledError

    game.emit = interrupted
    with pytest.raises(asyncio.CancelledError):
        await game.plugins.run("turn", Turn())
    pid, serial = game.state.plugin_prompts[0]["id"], game.state.plugin_serial
    events = []
    resumed = reload(game, plugin, events)
    await resumed.plugins.run("turn", Turn())
    assert len(calls) == 1 and resumed.state.plugin_serial == serial
    assert len(resumed.state.plugin_prompts) == 1
    assert resumed.state.plugin_prompts[0]["id"] == pid
    assert resumed.state.plugin_marks[plugin.id]["asked"] == [0, 1]
    assert [event["id"] for event in events if event["type"] == "plugin_prompt"] == [pid]


async def test_due_timer_is_not_removed_until_its_late_effects_finish(fake, monkeypatch):
    game, plugin, calls = table(fake, monkeypatch, hook="timer")
    timer = {"plugin": plugin.id, "key": "alarm", "turn": game.state.turn}
    game.state.plugin_timers = [timer]
    fake.queue_totals(2).queue(50)

    async def interrupted(event):
        if event["type"] == "plugin_effect":
            raise asyncio.CancelledError

    game.emit = interrupted
    with pytest.raises(asyncio.CancelledError):
        await game.plugins.due(Turn())
    assert game.state.plugin_timers == [timer]
    resumed = reload(game, plugin, [])
    await resumed.plugins.due(Turn())
    await resumed.plugins.due(Turn())
    assert len(calls) == 1 and resumed.state.plugin_timers == []
    assert "plugin_found" in resumed.state.flags and "c_notice" in resumed.state.clues


async def test_repeated_periods_resume_the_current_period_only(fake, monkeypatch):
    game, plugin, calls = table(fake, monkeypatch, repeat=True)
    fake.queue_totals(2, 3)
    before = game.state.investigator.characteristics["体质"]
    seen = 0

    async def interrupted(event):
        nonlocal seen
        if event["type"] == "plugin_effect":
            seen += 1
            if seen == 2:
                raise asyncio.CancelledError

    game.emit = interrupted
    with pytest.raises(asyncio.CancelledError):
        await game.plugins.run("turn", Turn())
    assert game.state.plugin_data[plugin.id] == {"count": 2}
    resumed = reload(game, plugin, [])
    await resumed.plugins.run("turn", Turn())
    assert len(calls) == 2 and resumed.state.investigator.characteristics["体质"] == before - 5
    assert len(resumed.state.plugin_log) == 2 and not fake.totals


async def test_plugin_settlement_uses_the_enclosing_turn_journal(fake, monkeypatch):
    game, plugin, calls = table(fake, monkeypatch, hook="action")
    plugin.actions.append(PluginAction(name="count", description="执行一次结算"))
    game.plugins.verified.add(plugin.fingerprint)
    game.state.pending_turn = {"input": "执行一次结算"}
    action = Action(kind="investigate", summary="执行一次结算")
    ruling = Ruling(needs_roll=False, minutes=0, plugin_calls=[Call(plugin=plugin.id, action="count")])
    fake.queue_totals(4).queue(50)

    async def interrupted(event):
        if event["type"] == "plugin_effect":
            raise asyncio.CancelledError

    game.emit = interrupted
    with pytest.raises(asyncio.CancelledError):
        await game.resolve(action, ruling)
    assert not game.state.plugin_marks[plugin.id]["_settlement"]["resolution"]["steps"]
    assert any("plugin:" in key for key in game.state.pending_turn["resolution"]["steps"])
    events = []
    resumed = reload(game, plugin, events)
    turn = await resumed.resolve(action, ruling)
    assert resumed.state.pending_turn["resolution"]["complete"] and len(calls) == 1
    assert "c_notice" in turn.clues and "plugin_found" in resumed.state.flags
    assert "_settlement" not in resumed.state.plugin_marks[plugin.id]
    assert not fake.totals and not fake.values and resumed._resolution is None
