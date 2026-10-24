"""Opted-in temporary control restores progress, then yields to the returning human."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from textual.widgets import Button, Checkbox

from autokeeper.agents.cast import Action, Proposal, Ruling
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.net.takeover import Takeovers
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


async def test_returning_player_can_reclaim_a_delegated_seat_from_the_actual_lobby(fake):
    from autokeeper.tui.screens.lobby import LobbyScreen
    from autokeeper.tui.screens.net import NetGameScreen

    hub = make_hub(fake)
    hub.takeovers.active.add("lin")
    app = AutoKeeperApp(theme="amber")
    try:
        async with sockets(hub) as (http, url):
            keeper = await sit(http, url, "zhouheng")
            async with app.run_test(size=(120, 40)) as pilot:
                lobby = LobbyScreen(url, "回来的人")
                await app.push_screen(lobby)
                async with asyncio.timeout(5):
                    while lobby.welcome is None:
                        await pilot.pause(0.05)
                row = next(s for s in lobby.seats if s["investigator"] == "lin")
                assert row["human"] and row["ai_takeover"] and not row["online"]
                assert not lobby.query_one("#sit", Button).disabled
                await pilot.press("enter")
                async with asyncio.timeout(5):
                    while not isinstance(app.screen, NetGameScreen):
                        await pilot.pause(0.05)
                assert app.screen.seat == "lin" and "lin" not in hub.takeovers.active
                assert hub.sits["lin"] is not None
            await keeper.close()
    finally:
        await hub.takeovers.close()
        await hub.game.close()


async def test_ai_only_room_runs_for_spectators_and_resumes_when_they_return(fake, monkeypatch):
    cast = TableCast()
    hub = Hub(EXAMPLE, seats=["AI"], cast=cast, rules=Rules(dice=fake), disconnect_ai=True)
    assert hub.state.human == []
    executed = []

    async def interlude():
        pass

    async def run(text, inv_id, **kwargs):
        executed.append((inv_id, text))

    monkeypatch.setattr(hub, "_interlude", interlude)
    monkeypatch.setattr(hub, "_run", run)
    pending = None
    try:
        async with sockets(hub) as (http, url):
            watch = await http.ws_connect(url)
            await watch.send_json({"t": "hello", "name": "观战者"})
            await until(watch, lambda msg: msg["t"] == "welcome")
            investigator = hub.state.investigators[0]
            await asyncio.wait_for(hub._ai_turn(investigator), 1)
            assert len(executed) == len(cast.ai_turns) == 1
            await watch.close()
            await eventually(lambda: not hub.clients)
            pending = asyncio.create_task(hub._ai_turn(investigator))
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(pending), 0.03)
            assert len(executed) == len(cast.ai_turns) == 1
            watch = await http.ws_connect(url)
            await watch.send_json({"t": "hello", "name": "返回的观战者"})
            await until(watch, lambda msg: msg["t"] == "welcome")
            await asyncio.wait_for(pending, 1)
            assert len(executed) == len(cast.ai_turns) == 2
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await hub.takeovers.close()
        await hub.game.close()


def pacing_room():
    away = SimpleNamespace(id="away", at=0, reservation={}, closed=False)
    live = SimpleNamespace(id="live", at=0, reservation={"end": 30}, closed=False)
    runtime = SimpleNamespace(scenes={"away": away, "live": live}, _pulse=asyncio.Event(), changed=asyncio.Event())
    hub = SimpleNamespace(state=SimpleNamespace(human=["lin", "zhouheng"]),
                          sits={"lin": None, "zhouheng": "connected"}, wake=asyncio.Event(),
                          _scene_tables=SimpleNamespace(runtime=runtime, workers={}))
    takeovers = Takeovers(hub, True)
    takeovers.active.add("lin")

    def world_changed():
        old, runtime._pulse = runtime._pulse, asyncio.Event()
        old.set()

    return takeovers, away, live, world_changed


async def test_takeover_without_time_progress_waits_for_new_room_activity():
    takeovers, _, _, world_changed = pacing_room()
    await takeovers.pace("away", ["lin"])
    again = asyncio.create_task(takeovers.pace("away", ["lin"]))
    try:
        # A world notification alone does not turn a zero-minute proposal into progress.
        world_changed()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(again), 0.03)
        takeovers.pulse()
        await asyncio.wait_for(again, 1)
    finally:
        again.cancel()
        await asyncio.gather(again, return_exceptions=True)
        await takeovers.close()


async def test_takeover_may_keep_catching_up_while_its_game_time_advances():
    takeovers, away, _, world_changed = pacing_room()
    await takeovers.pace("away", ["lin"])
    for minute in (10, 20):
        away.at = minute
        world_changed()
        await asyncio.wait_for(takeovers.pace("away", ["lin"]), 1)
    away.at = 30
    world_changed()
    caught_up = asyncio.create_task(takeovers.pace("away", ["lin"]))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(caught_up), 0.03)
    finally:
        caught_up.cancel()
        await asyncio.gather(caught_up, return_exceptions=True)
        await takeovers.close()


async def test_new_outstanding_time_request_allows_one_more_takeover_attempt():
    takeovers, _, live, world_changed = pacing_room()
    await takeovers.pace("away", ["lin"])
    pending = asyncio.create_task(takeovers.pace("away", ["lin"]))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(pending), 0.03)
        live.reservation["end"] = 40
        world_changed()
        await asyncio.wait_for(pending, 1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(takeovers.pace("away", ["lin"]), 0.03)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await takeovers.close()


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
            b = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
            await b.send_json({"t": "hello", "name": "lin"})
            await b.send_json({"t": "sit", "investigator": "lin"})
            seated = await until(b, lambda msg: msg["t"] == "seated")
            await eventually(lambda: "lin" not in hub.takeovers.active)
            # A fast handoff can open the window before the seating response.
            # Its included snapshot is authoritative; no extra pool event is owed.
            if not seated["pool"]["open"]:
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

    async def table_input(*args, **kwargs):
        if phase == "proposal_ready":
            entered.set()
            await release.wait()

    async def run(text, *args, **kwargs):
        executed.append(text)

    monkeypatch.setattr(hub.cast, "investigator", proposal)
    monkeypatch.setattr(hub, "_interlude", nothing)
    monkeypatch.setattr(hub, "_run", run)
    monkeypatch.setattr(hub, "_event", nothing)
    monkeypatch.setattr(hub, "_table_input", table_input)
    hub.sits["zhouheng"] = "another-human-present"
    hub.takeovers.active.add("lin")
    work = asyncio.create_task(hub._ai_turn(hub.state.find("lin")))
    try:
        await asyncio.wait_for(entered.wait(), 2)
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
