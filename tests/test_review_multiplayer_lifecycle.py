"""Offline audit cases: strict expected failures track unresolved scene lifecycle defects."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.net.hub import Hub
from autokeeper.net.scenes import SceneTable, SceneTables
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .conftest import FakeDice
from .test_net import TableCast
from .test_net_scene_lifecycle import party


class FailingNarrator(TableCast):
    def __init__(self):
        super().__init__()
        self.failures = 0

    async def intent(self, state, text):
        return Action(kind="use", summary=text)

    async def arbiter(self, state, action, at=None, **kwargs):
        return Ruling(needs_roll=False, minutes=1)

    async def keeper(self, state, report, on_delta=None, lore=""):
        self.failures += 1
        raise ValueError("offline fake: malformed model content")


def make_table(fake: FakeDice) -> tuple[Hub, SceneTables]:
    hub = Hub(EXAMPLE, state=party(), cast=TableCast(), rules=Rules(dice=fake))
    hub.m.events.clear()
    hub.m.endings.clear()
    hub.m.foreshadow.clear()
    hub.m.agendas.clear()
    tables = SceneTables(hub, lambda _: FailingNarrator(), lambda _: Rules(dice=fake))
    hub._scene_tables = tables
    for ident, scene in tables.runtime.scenes.items():
        if not scene.closed:
            tables.workers[ident] = SceneTable(tables, ident)
    return hub, tables


@pytest.mark.xfail(
    strict=True, raises=AssertionError,
    reason="P1 audit: abandoning a failed turn leaves an orphan time/resource reservation that survives reload",
)
async def test_abandoned_turn_releases_its_reservation_and_unblocks_the_other_scene(fake):
    hub, tables = make_table(fake)
    first, other = tables.for_member("zhouheng"), tables.for_member("lin")
    assert first is not other
    tables.runtime.pass_time(other.ident)
    try:
        with pytest.raises(ValueError, match="malformed model content") as error:
            await first._run("检查桌上的文件", "zhouheng", ai=True)
        assert first.state.pending_turn and first.scene.reservation
        await first._unattended(first.state.investigator, error.value)
        assert first.cast.failures == 3 and not first.state.pending_turn
        restored = type(hub.state).model_validate_json(hub.state.model_dump_json())
        assert not first.scene.reservation, "Abandoned action must not continue reserving time or shared resources"
        assert not restored.scenes[first.ident].reservation
        assert tables.runtime.pass_time(first.ident)
        tables.runtime.revoke_pass(other.ident)
        other.state.pending_turn = {"input": "随后继续调查二十分钟"}
        tables.runtime.reserve(other.ident, Action(kind="use", summary="随后继续调查二十分钟"),
                               Ruling(needs_roll=False, minutes=20))
        await asyncio.wait_for(tables.runtime.wait(other.ident), 1)
    finally:
        await tables.close()
        await hub.game.close()


@pytest.mark.xfail(
    strict=True, raises=AssertionError,
    reason="P2 audit: the root finishes the game but scene copies still queue growth commands and new actions",
)
async def test_finished_scene_runs_growth_commands_and_rejects_new_actions(fake, monkeypatch):
    hub, tables = make_table(fake)
    worker = tables.for_member("zhouheng")
    hub.sits["zhouheng"] = "player-a"
    hub.over = True
    hub.state.ending = "investigators_win"
    messages, completed = [], []

    async def send(cid, payload):
        messages.append(payload)

    async def afterwards(seat, text):
        completed.append((seat, text))

    monkeypatch.setattr(hub, "_send", send)
    monkeypatch.setattr(hub, "_afterwards", afterwards)
    monkeypatch.setattr(worker, "_afterwards", afterwards)
    try:
        await hub._on("player-a", {"t": "act", "text": ".en"})
        await hub._on("player-a", {"t": "act", "text": "继续调查"})
        assert completed == [("zhouheng", ".en")]
        assert worker.instant.empty() and not worker.early
        assert any(message.get("text") == "本局已结束。" for message in messages)
    finally:
        await tables.close()
        await hub.game.close()


@pytest.mark.xfail(
    strict=True, raises=AssertionError,
    reason="P2 audit: a cancelled time proposal remains deduplicated after the interrupting declaration is undone",
)
async def test_cancelled_time_proposal_can_resume_after_input_is_undone(fake, monkeypatch):
    hub, tables = make_table(fake)
    first, other = tables.for_member("zhouheng"), tables.for_member("lin")
    hub.sits["zhouheng"] = "player-a"
    hub.clients["player-a"] = object()
    first.open = True
    began = asyncio.Event()
    calls = []

    async def scene_time(state, until):
        calls.append(until)
        began.set()
        await asyncio.Event().wait()

    async def send(*args):
        pass

    monkeypatch.setattr(first.cast, "scene_time", scene_time, raising=False)
    monkeypatch.setattr(hub, "_send", send)
    other.state.pending_turn = {"input": "耗时行动"}
    tables.runtime.reserve(other.ident, Action(kind="use", summary="耗时行动"), Ruling(needs_roll=False, minutes=20))
    other.scene.reservation["blocked"] = tables.runtime.blockers(other.ident)
    until = tables.runtime.time_requested(first.ident)
    assert until == 860
    try:
        first.request_time(until)
        await asyncio.wait_for(began.wait(), 1)
        await hub._on("player-a", {"t": "act", "text": "误输入的行动"})
        await hub._on("player-a", {"t": "undo"})
        await asyncio.gather(first.time_task, return_exceptions=True)
        assert first._time_available() and not first.pool and not first.state.queued_actions
        first.request_time(until)
        await asyncio.sleep(0)
        assert calls == [until, until], "An interrupted proposal without a decision must remain eligible for retry"
    finally:
        await first._stop_time()
        hub.clients.clear()
        await tables.close()
        await hub.game.close()
