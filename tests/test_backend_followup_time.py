"""A changed world invalidates finished scene-time consultations, unchanged state does not."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Ruling, TimeHandoff
from autokeeper.net import scenes as net_scenes
from autokeeper.world.ops import SetFlag

from .test_review_multiplayer_lifecycle import make_table


@pytest.mark.parametrize("mode", ["declined", "failure"])
async def test_world_change_invalidates_finished_time_consultation(fake, monkeypatch, mode):
    hub, tables = make_table(fake)
    quiet, active = tables.for_member("zhouheng"), tables.for_member("lin")
    hub.sits["zhouheng"], hub.clients["a"] = "a", object()
    quiet.open = True
    monkeypatch.setattr(net_scenes, "TIME_RETRY_WAIT", (0, 0))
    calls = []

    async def clock(state, until):
        calls.append(state.record.seq)
        if mode == "failure" and "bell_stopped" not in state.flags:
            raise TimeoutError("offline unavailable")
        return TimeHandoff(advance="bell_stopped" in state.flags, reason="钟停后可以略过")

    async def send(*args):
        pass

    monkeypatch.setattr(hub, "_send", send)
    monkeypatch.setattr(quiet.cast, "scene_time", clock, raising=False)
    active.state.pending_turn = {"input": "前往广场会合"}
    tables.runtime.reserve(active.ident, Action(kind="move", summary="前往广场会合", destination=quiet.scene.place),
                           Ruling(needs_roll=False, minutes=10))
    active.scene.reservation["blocked"] = tables.runtime.blockers(active.ident)
    until = tables.runtime.time_requested(quiet.ident)
    assert until == quiet.scene.at + 20
    try:
        quiet.request_time(until)
        await quiet.time_task
        before_calls = len(calls)
        assert before_calls == (3 if mode == "failure" else 1)
        for _ in range(5):
            quiet.request_time(until)
            await asyncio.sleep(0)
        assert len(calls) == before_calls
        assert quiet.scene.passed_until is None

        before_seq = quiet.state.record.seq
        assert hub.game.world.apply(SetFlag(flag="bell_stopped")).ok
        assert quiet.state.record.seq > before_seq
        assert "bell_stopped" in quiet.state.flags
        quiet.request_time(until)
        await quiet.time_task
        assert len(calls) == before_calls + 1
        assert quiet.scene.passed_until == until
        assert tables.runtime.time_requested(quiet.ident) is None
    finally:
        hub.clients.clear()
        await tables.close()
        await hub.game.close()
