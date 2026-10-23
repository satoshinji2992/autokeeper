"""Regressions for scene cancellation, failed-turn cleanup and the shared room lifecycle."""

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


async def test_abandoned_turn_releases_its_reservation_and_unblocks_the_other_scene(fake):
    hub, tables = make_table(fake)
    first, other = tables.for_member("zhouheng"), tables.for_member("lin")
    assert first is not other
    tables.runtime.pass_time(other.ident)
    try:
        with pytest.raises(ValueError, match="malformed model content") as error:
            await first._run("检查桌上的文件", "zhouheng", ai=True)
        assert first.state.pending_turn and first.scene.reservation
        settled_at, recorded = first.scene.at, first.state.record.seq
        await first._unattended(first.state.investigator, error.value)
        assert first.cast.failures == 3 and not first.state.pending_turn
        assert first.scene.at == settled_at and first.state.record.seq == recorded
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


async def test_failed_table_command_releases_its_reservation(fake):
    hub, tables = make_table(fake)
    worker = tables.for_member("zhouheng")
    said = "/kp 解释目前的情况"
    worker.state.pending_turn = {"input": said}
    tables.runtime.reserve(worker.ident, Action(kind="meta", summary=said), Ruling(needs_roll=False))

    async def interrupted():
        raise ValueError("offline fake: command interrupted")

    try:
        await worker._safely(interrupted(), "主持提问", said)
        assert not worker.state.pending_turn and not worker.scene.reservation
        assert tables.runtime.pass_time(worker.ident)
    finally:
        await tables.close()
        await hub.game.close()


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


async def test_closing_a_scene_cancels_its_uncommitted_preparation(fake):
    hub, tables = make_table(fake)
    worker = tables.for_member("zhouheng")
    started = asyncio.Event()

    async def preparation():
        started.set()
        await asyncio.Event().wait()

    worker.preparing = task = asyncio.create_task(preparation())
    await started.wait()
    revision = worker.planning_revision
    worker.halted = True
    try:
        await worker.run()
        assert task.cancelled() and worker.preparing is None
        assert worker.planning_revision > revision
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await tables.close()
        await hub.game.close()


@pytest.mark.parametrize("phase", ["thinking", "ready"])
async def test_an_uncommitted_round_cannot_grant_a_time_cut(fake, phase):
    hub, tables = make_table(fake)
    worker = tables.for_member("zhouheng")
    worker.open = True
    hub.sits["zhouheng"] = "player-a"
    called = []

    async def scene_time(state, until):
        called.append(until)

    worker.cast.scene_time = scene_time
    worker.phase = phase
    try:
        worker.request_time(worker.scene.at + 20)
        await asyncio.sleep(0)
        assert not called and worker.time_task is None
        assert worker.scene.passed_until is None
    finally:
        await worker._stop_time()
        await tables.close()
        await hub.game.close()


@pytest.mark.parametrize("kind", ["act", "join", "done", "undo"])
async def test_a_previous_rounds_delayed_message_cannot_change_the_current_scene(fake, monkeypatch, kind):
    hub, tables = make_table(fake)
    worker = tables.for_member("zhouheng")
    hub.sits["zhouheng"] = "player-a"
    hub.round_id, worker.round_id = "another-scene-round", "current-scene-round"
    worker.open, worker.phase = True, "collecting"
    worker.pool = [("zhouheng", "本轮已提交的行动")]
    sent = []

    async def send(cid, payload):
        sent.append(payload)

    monkeypatch.setattr(hub, "_send", send)
    try:
        await hub._on("player-a", {"t": kind, "text": "迟到的旧行动", "round": "previous-scene-round"})
        assert worker.pool == [("zhouheng", "本轮已提交的行动")]
        assert not worker.done and not worker.joining
        assert worker.planning_revision == 0
        if kind == "act":
            assert any(message["t"] == "action_rejected" and message["text"] == "迟到的旧行动" for message in sent)
        # Validate after scene routing: the root room may have a different live round id.
        await hub._on("player-a", {"t": kind, "text": "本轮修订的行动", "round": worker.round_id})
        if kind == "act":
            assert worker.pool == [("zhouheng", "本轮修订的行动")]
        elif kind == "join":
            assert worker.joining == {"zhouheng"}
        elif kind == "done":
            assert worker.done == {"zhouheng"}
        else:
            assert not worker.pool
    finally:
        await tables.close()
        await hub.game.close()
