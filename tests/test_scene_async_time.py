"""Independent scene pacing stays invisible; actual meetings still have a boundary."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import aiohttp
import pytest

from autokeeper.agents.cast import Action, Ruling, TimeHandoff
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Event

from .test_net import opened, pool_open, until
from .test_net_scenes import joined
from .test_review_multiplayer_lifecycle import make_table
from .test_scene_runtime import made
from .test_scene_time_handoff import ClockCast, make_hub


async def test_independent_work_finishes_while_the_other_player_has_an_unanswered_roll(fake):
    fake.values.append(30)
    approvals = []

    class Independent(ClockCast):
        async def scene_time(self, state, end):
            approvals.append(end)
            return TimeHandoff(advance=True, reason="不应为独立行动请求许可。")

    hub = make_hub(fake, lambda _: Independent())
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            await b.send_json({"t": "act", "text": "我要掷骰侦查"})
            offer = (await until(b, lambda message: message["t"] == "offer"))["offer"]
            await a.send_json({"t": "act", "text": "办理长行动"})
            await until(a, lambda message: message.get("ev", {}).get("type") == "keeper_end")
            assert hub._context("zhouheng").state.minutes == 620
            assert hub._context("lin").state.minutes == 600
            assert approvals == [] and list(fake.values) == [30]
            assert hub._context("lin").state.pending_turn
            await b.send_json({"t": "respond", "id": offer["respond_id"], "choice": "roll"})
            await until(b, lambda message: message.get("ev", {}).get("type") == "keeper_end")
            assert not fake.values and approvals == []
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("clock_event", [False, True])
async def test_independent_wait_is_not_sliced_by_a_remote_clock_but_obeys_local_events(fake, clock_event):
    runtime, _, _ = made(fake)
    ident, other = runtime.for_member("zhouheng"), runtime.for_member("lin")
    game = runtime.game(ident)
    try:
        if clock_event:
            runtime.module.events.append(Event(id="appeal", title="有人求救", when="D1 10:15", where="tower",
                                               text="楼下有人求救，需要调查员回应。", read_aloud="楼下传来求救声。"))
        action = Action(kind="wait", summary="周衡在钟楼等两小时", wait_limit=120)
        ruling = Ruling(needs_roll=False, minutes=120)
        game.wait_horizon = lambda actor: runtime.incoming_boundary(ident)
        runtime.reserve(ident, action, ruling)
        assert runtime.blockers(ident) == []
        await runtime.wait(ident)
        turn = await game.resolve(action, ruling)
        assert game.state.minutes == (615 if clock_event else 720)
        assert runtime.scenes[other].at == 600 and not runtime.scenes[other].notices
        assert ("appeal" in runtime.state.fired) is clock_event
        assert len(turn.events) == int(clock_event)
    finally:
        await runtime.close()


async def test_a_pass_in_an_unrelated_scene_does_not_silently_advance_its_clock(fake):
    runtime, _, _ = made(fake)
    ident, other = runtime.for_member("zhouheng"), runtime.for_member("lin")
    game = runtime.game(ident)
    try:
        assert runtime.pass_time(other)
        runtime.reserve(ident, Action(kind="use", summary="周衡整理记录"), Ruling(needs_roll=False, minutes=20))
        await runtime.wait(ident)
        game._spend(20, Turn())
        assert game.state.minutes == 620
        assert runtime.scenes[other].at == 600 and not runtime.scenes[other].notices
    finally:
        await runtime.close()


async def test_authorized_wait_yields_to_an_incoming_party_not_to_independent_work(fake):
    hub = make_hub(fake, lambda _: ClockCast())
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, "zhouheng"), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            source, destination = hub._context("zhouheng"), hub._context("lin")
            runtime = hub._scene_tables.runtime
            runtime.reserve(source.ident, Action(kind="use", summary="周衡独自整理记录"),
                            Ruling(needs_roll=False, minutes=120))
            assert destination._wait_horizon("lin") is None
            source.scene.reservation.clear()
            runtime.reserve(source.ident, Action(kind="move", summary="周衡到广场会合", destination="square"),
                            Ruling(needs_roll=False))
            assert destination._wait_horizon("lin") == 620
            assert runtime.blockers(source.ident) == [{"scene": destination.ident, "kind": "time", "at": 600,
                                                       "until": 600}]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("offset", [None, -1, 0])
async def test_a_missing_or_elapsed_meeting_boundary_never_starts_a_clock_consultation(fake, offset, monkeypatch):
    hub, tables = make_table(fake)
    worker = tables.for_member("zhouheng")
    hub.sits["zhouheng"], hub.clients["a"] = "a", object()
    worker.open = True
    called = []

    async def clock(state, end):
        called.append(end)
        return TimeHandoff(advance=True, reason="不应为无效边界授予许可。")

    monkeypatch.setattr(worker.cast, "scene_time", clock, raising=False)
    try:
        assert worker._time_available()
        worker.request_time(None if offset is None else worker.scene.at + offset)
        await asyncio.sleep(0)
        assert called == [] and worker.time_task is None
        assert worker.scene.passed_until is None
    finally:
        hub.clients.clear()
        await tables.close()
        await hub.game.close()
