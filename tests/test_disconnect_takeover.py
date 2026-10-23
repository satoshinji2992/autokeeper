"""Opted-in temporary control restores progress, then yields to the returning human."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path

import aiohttp
import pytest
from textual.widgets import Checkbox

from autokeeper.agents.cast import Action, Proposal, Ruling
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.host import HostScreen

from .test_menus import canned  # noqa: F401
from .test_multiplayer_transport import sit, sockets
from .test_net import TableCast, opened, pool_open, until
from .test_net_scenes import joined
from .test_table_encounter_start import opening


def make_hub(fake, *, scenes=False, factory=None):
    m = Module.load(EXAMPLE)
    a = m.pregens[0].model_copy(deep=True)
    b = a.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [a, b], human=[a.id, b.id], slot="takeover")
    state.apart = {b.id: "office"}
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake), disconnect_ai=True,
              scene_cast_factory=factory if scenes else None, scene_rules_factory=lambda _: Rules(dice=fake))
    hub.takeovers.delay, hub.window = 0.03, 0.01
    hub.started.set()
    return hub


async def eventually(check):
    async with asyncio.timeout(3):
        while not check():
            await asyncio.sleep(0.005)


async def test_disconnected_remote_scene_continues_and_reconnect_returns_its_control(fake):
    calls = []

    class WaitCast(TableCast):
        async def intent(self, state, text):
            return Action(kind="wait", summary=text, wait_limit=10)

        async def arbiter(self, state, action, at=None, **kwargs):
            return Ruling(needs_roll=False, minutes=10)

        async def investigator(self, state, inv_id, situation, *, stances=None):
            calls.append((inv_id, situation))
            return Proposal(text="我在报社等十分钟")

    hub = make_hub(fake, scenes=True, factory=lambda _: WaitCast())
    # Restore-style tables start when all seats join, rather than creating startup timers.
    hub.started.clear()
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            start = hub._context("zhouheng").state.minutes
            await b.close()
            await a.send_json({"t": "act", "text": "我等十分钟"})
            await eventually(lambda: "lin" in hub.takeovers.active and hub._context("zhouheng").state.minutes >= start + 10
                              and hub._context("lin").state.minutes >= start + 10)
            assert hub._context("lin").state.minutes >= start + 10
            assert calls and all(who == "lin" for who, _ in calls)
            assert "临时代管" in calls[0][1]
            before = hub._context("lin").state.minutes
            b = await joined(http, hub, "lin")
            await eventually(lambda: "lin" not in hub.takeovers.active)
            await until(b, pool_open)
            assert hub._context("lin").state.minutes >= before
            assert "lin" in hub.state.human and hub.sits["lin"] is not None
            assert not hub._seats()[1]["ai_takeover"]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_reconnecting_during_grace_keeps_the_original_offer(fake):
    hub = make_hub(fake)
    task = await opened(hub)
    offered = None
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            hub.takeovers.delay = 1
            offered = asyncio.create_task(hub._ask("lin", hub.sits["lin"], {"kind": "roll"}, timed=False))
            first = (await until(b, lambda msg: msg["t"] == "offer"))["offer"]
            await b.close()
            b = await joined(http, hub, "lin")
            again = (await until(b, lambda msg: msg["t"] == "offer"))["offer"]
            assert first["respond_id"] == again["respond_id"] and "lin" not in hub.takeovers.active
            await b.send_json({"t": "respond", "id": again["respond_id"], "choice": "roll"})
            assert await asyncio.wait_for(offered, 1) == "roll"
            assert not hub.takeovers.tasks
            await a.close()
    finally:
        if offered is not None:
            offered.cancel()
            await asyncio.gather(offered, return_exceptions=True)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("kind,default,expected", [("roll", "", "roll"), ("push", "", "drop"),
                                                   ("reaction", "dodge", "dodge"), ("redeclare", "pass", "keep")])
async def test_takeover_answers_original_pending_choice_without_spending_luck(fake, kind, default, expected):
    hub = make_hub(fake)
    task = await opened(hub)
    offered = None
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            options = [{"id": "keep", "label": "保留"}, {"id": "pass", "label": "跳过"}] if kind == "redeclare" else [
                {"id": "dodge", "label": "闪避"}]
            offered = asyncio.create_task(hub._ask("lin", hub.sits["lin"], {
                "kind": kind, "default": default, "options": options, "luck": 20, "can_push": True}, timed=False))
            first = (await until(b, lambda msg: msg["t"] == "offer"))["offer"]
            luck = hub.state.find("lin").luck
            await b.close()
            assert await asyncio.wait_for(offered, 1) == expected
            assert hub.state.find("lin").luck == luck
            assert first["respond_id"] in hub.settled and "lin" not in hub.asking
            assert "lin" in hub.takeovers.active
            await a.close()
    finally:
        if offered is not None:
            offered.cancel()
            await asyncio.gather(offered, return_exceptions=True)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_all_humans_offline_pauses_a_delegated_choice_even_with_a_spectator(fake):
    hub = make_hub(fake)
    task = await opened(hub)
    offered = None
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            watch = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
            await watch.send_json({"t": "hello", "name": "观战者"})
            await until(watch, lambda msg: msg["t"] == "welcome")
            offered = asyncio.create_task(hub._ask("lin", hub.sits["lin"], {"kind": "roll"}, timed=False))
            await until(b, lambda msg: msg["t"] == "offer")
            await a.close()
            await b.close()
            await eventually(lambda: hub.takeovers.active == {"zhouheng", "lin"})
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(offered), 0.06)
            assert hub.clients and not hub.takeovers.online()
            a = await joined(http, hub, "zhouheng")
            assert await asyncio.wait_for(offered, 1) == "roll"
    finally:
        if offered is not None:
            offered.cancel()
            await asyncio.gather(offered, return_exceptions=True)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("phase", ["preparation", "proposal_ready"])
async def test_reconnecting_during_ai_preparation_discards_the_unstarted_proposal(fake, monkeypatch, phase):
    hub = make_hub(fake)
    entered, release = asyncio.Event(), asyncio.Event()
    executed = []

    async def proposal(*args, **kwargs):
        if phase == "preparation":
            entered.set()
            await release.wait()
        return Proposal(text="我去钟楼")

    async def nothing(*args, **kwargs):
        pass

    async def event(value):
        if phase == "proposal_ready" and value.get("type") == "said":
            entered.set()
            await release.wait()

    async def run(text, *args, **kwargs):
        executed.append(text)

    monkeypatch.setattr(hub.cast, "investigator", proposal)
    monkeypatch.setattr(hub, "_interlude", nothing)
    monkeypatch.setattr(hub, "_run", run)
    monkeypatch.setattr(hub, "_event", event)
    hub.sits["zhouheng"] = "another-human-present"
    hub.takeovers.active.add("lin")
    work = asyncio.create_task(hub._ai_turn(hub.state.find("lin")))
    try:
        await entered.wait()
        hub.sits["lin"] = "returning-player"
        await hub.takeovers.returned("lin")
        release.set()
        await asyncio.wait_for(work, 1)
        assert executed == [] and "lin" not in hub.takeovers.active
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        await hub.takeovers.close()
        await hub.game.close()


async def test_takeover_resumes_real_pending_attack_once_and_handback_restores_human_control(fake):
    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    game, cast, _ = await opening(module, fake, "fight", by_hand=True)
    other = game.state.investigators[0].model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    game.state.investigators.append(other)
    game.state.human.append(other.id)
    game.state.apart[other.id] = "office"
    game.state.round_plan["encounter"] = [game.state.encounter.id, "fight", 1]
    turn = Turn()
    await game._beat(turn, settle=True)
    pending = game.state.encounter.pending
    assert pending is not None and pending.kind == "roll"
    game.state.pending_turn.update(action=Action(kind="other", summary="冲突开始").model_dump(mode="json"),
                                   ruling=None, turn=TURN_RECORD.dump_python(turn, mode="json"), by_hand=True)
    hub = Hub(EXAMPLE, state=game.state, cast=cast, rules=game.rules, disconnect_ai=True)
    hub.started.set()
    hub.takeovers.delay = 0.02
    fake.queue(10, 90).queue_totals(1)
    work = None
    try:
        async with sockets(hub) as (http, url):
            a, _ = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
            work = asyncio.create_task(hub._run("/retry", "zhouheng", ai=False))
            offer = (await until(a, lambda msg: msg["t"] == "offer"))["offer"]
            await a.close()
            await asyncio.wait_for(work, 2)
            assert hub.state.encounter.completed_rounds == 1 and hub.state.encounter.pending is None
            assert not fake.values and not fake.totals
            assert hub.state.encounter.get("zhouheng").controller == "ai"
            a = await sit(http, url, "zhouheng")
            assert hub.state.encounter.get("zhouheng").controller == "human"
            await a.send_json({"t": "respond", "id": offer["respond_id"], "choice": "roll"})
            await until(a, lambda msg: msg["t"] == "notice")
            assert hub.state.encounter.completed_rounds == 1 and not fake.values
    finally:
        if work is not None:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
        await hub.takeovers.close()
        await hub.game.close()
        await game.close()


@pytest.mark.parametrize("enabled", [False, True])
async def test_host_form_keeps_the_chosen_disconnect_policy(canned, monkeypatch, enabled):  # noqa: F811
    from autokeeper.net import room

    monkeypatch.setattr(room, "recall", lambda: {"disconnect_ai": enabled})
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        await app.push_screen(HostScreen())
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.step == "room"
        checkbox = app.screen.query_one("#host-disconnect-ai", Checkbox)
        assert checkbox.value is enabled and str(checkbox.label) == "断线后由 AI 临时接管"
        if enabled and (evidence := os.environ.get("AUTOKEEPER_UI_EVIDENCE")):
            target = Path(evidence)
            target.mkdir(parents=True, exist_ok=True)
            app.save_screenshot(filename="disconnect-ai.svg", path=str(target))
