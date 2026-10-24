"""Scene execution owns its work; connection and delivery resources belong to the room."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.net.hub import Hub
from autokeeper.net.scenes import SceneTable, SceneTables
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import Occur

from .test_net import TableCast
from .test_net_scene_lifecycle import party
from .test_net_scenes import SceneCast


@pytest.fixture
async def scene_tables(fake):
    root = Hub(EXAMPLE, state=party(), cast=TableCast(), rules=Rules(dice=fake))
    tables = SceneTables(root, lambda _: SceneCast(), lambda _: Rules(dice=fake))
    root._scene_tables = tables
    for ident, scene in tables.runtime.scenes.items():
        if not scene.closed:
            tables.workers[ident] = SceneTable(tables, ident)
    try:
        yield root, tables
    finally:
        await tables.close()
        await root.game.close()


async def test_room_resources_remain_authoritative_after_replacement(scene_tables, monkeypatch):
    root, tables = scene_tables
    first, other = tables.for_member("zhouheng"), tables.for_member("lin")
    root.clients = {"returning": object(), "remote": object()}
    root.sits = {"zhouheng": "returning", "lin": "remote"}
    root.names = {"returning": "回桌的玩家"}
    root.faces = {"shared-face": "warden"}
    root.sent = {"shared-face": "picture-version-2"}
    root.sent_to = {"returning": {"shared-face": "digest-version-2"}}
    sent = []

    async def send(cid, payload):
        sent.append((cid, payload))

    monkeypatch.setattr(root, "_send", send)
    await first._pool()
    assert [cid for cid, _ in sent] == ["returning"]
    assert first._seat_of("returning") == "zhouheng"
    assert first.names["returning"] == "回桌的玩家"
    assert first.faces is other.faces is root.faces
    assert first.sent["shared-face"] == "picture-version-2"
    assert first.sent_to["returning"]["shared-face"] == "digest-version-2"


async def test_player_queues_follow_members_without_draining_other_scenes(scene_tables):
    root, tables = scene_tables
    first, other = tables.for_member("zhouheng"), tables.for_member("lin")
    # Reconnecting input always targets the room's current mailbox. Collection
    # still sees only this scene, so it cannot consume another scene's response.
    root.inbox["zhouheng"] = asyncio.Queue()
    root.choices["zhouheng"] = asyncio.Queue()
    root.inbox["lin"].put_nowait("另一场景尚未处理的输入")
    root.choices["zhouheng"].put_nowait("push")
    first.inbox["zhouheng"].put_nowait("/retry")
    assert first.inbox["zhouheng"].get_nowait() == "/retry"
    assert first.choices["zhouheng"].get_nowait() == "push"
    assert set(first.inbox) == set(first.choices) == {"zhouheng"}
    assert set(other.inbox) == {"lin"}
    assert other.inbox["lin"].get_nowait() == "另一场景尚未处理的输入"


async def test_scene_execution_queues_are_not_room_state(scene_tables):
    root, tables = scene_tables
    first, other = tables.for_member("zhouheng"), tables.for_member("lin")
    first.pool.append(("zhouheng", "检查窗户"))
    first.asking["zhouheng"] = "saved-offer"
    first.instant.put_nowait(("zhouheng", ".r 1d100"))
    first.plays.put_nowait(("player", "zhouheng", {"t": "command"}))
    first.private_pending.add("zhouheng:private-request")
    root.swaps.put_nowait(("host", {"preset": "replacement"}))
    root.rules.put_nowait(("host", "一条家规"))
    assert not root.pool and not other.pool
    assert not root.asking and not other.asking
    assert root.instant.empty() and other.instant.empty()
    assert root.plays.empty() and other.plays.empty()
    assert not root.private_pending and not other.private_pending
    # The supervisor alone consumes room-wide changes and updates every host.
    assert first.swaps.empty() and other.swaps.empty()
    assert first.rules.empty() and other.rules.empty()


async def test_scene_does_not_implicitly_inherit_new_room_execution_state(scene_tables):
    root, tables = scene_tables
    worker = tables.for_member("zhouheng")
    root.extra_execution = {"stage": "root-only"}
    assert not hasattr(worker, "extra_execution")
    root.over = True
    assert worker.over
    assert worker.game is not root.game and worker.cast is not root.cast
    assert worker.state.record is root.state.record


async def test_regroup_moves_original_declarations_without_new_ids_or_times(scene_tables, monkeypatch):
    root, tables = scene_tables
    first, other = tables.for_member("zhouheng"), tables.for_member("lin")
    root.sits.update(zhouheng="a", lin="b")
    first.open = other.open = True
    await root._on("a", {"t": "act", "text": "去办公室查文件"})
    await root._on("b", {"t": "act", "text": "在办公室等同伴"})
    accepted = {row.seat: (row.id, row.text, row.at) for worker in (first, other)
                for row in worker.state.declarations}

    async def wait_for_input(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(SceneTable, "run", wait_for_input)
    root.state.positions["zhouheng"] = "office"
    await tables.regroup()
    joined = tables.for_member("zhouheng")
    assert joined is tables.for_member("lin") and joined not in (first, other)
    assert {row.seat: (row.id, row.text, row.at) for row in joined.state.declarations} == accepted
    assert not first.state.declarations and not other.state.declarations
    assert joined.early == joined.queued_declarations()


async def test_returning_player_withdraws_ai_wait_that_has_not_started(scene_tables, monkeypatch):
    root, tables = scene_tables
    worker = tables.for_member("lin")
    began = asyncio.Event()

    async def unstarted(self, text, inv_id, **kwargs):
        self.state.pending_turn.update(input=text, stage="resolve", request={
            "actor": inv_id, "original": text, "remaining": text, "completed": [],
        }, arrangement={"plan": {"action": {}}, "units": [], "landed": True})
        self.scene.reservation = {"phase": "prepared", "start": self.scene.at, "end": self.scene.at + 10}
        began.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Hub, "_run", unstarted)
    root.takeovers.active.add("lin")
    running = asyncio.create_task(worker._run("再等十分钟", "lin", ai=True))
    try:
        await began.wait()
        root.sits["lin"] = "returning"
        await root.takeovers.returned("lin")
        await asyncio.wait_for(running, 1)
        assert not worker.state.pending_turn and not worker.scene.reservation
        assert not worker.halted
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_returning_player_retains_already_executed_ai_results(scene_tables, monkeypatch):
    root, tables = scene_tables
    worker = tables.for_member("lin")
    began, release = asyncio.Event(), asyncio.Event()

    async def committed(self, text, inv_id, **kwargs):
        landed = self.game.world.apply(Occur(id="already-spoken", actor=inv_id, text="已经说出口的话。",
                                            place=self.scene.place, participants=[]))
        assert landed.ok
        self.state.pending_turn.update(input=text, stage="resolve", resolution={
            "steps": {"speech": {"done": True, "value": "已经说出口的话。"}},
        })
        self.scene.reservation = {"phase": "running", "committed": self.scene.at}
        began.set()
        await release.wait()

    monkeypatch.setattr(Hub, "_run", committed)
    root.takeovers.active.add("lin")
    running = asyncio.create_task(worker._run("先告诉同伴我的发现", "lin", ai=True))
    try:
        await began.wait()
        seq = root.state.record.seq
        root.sits["lin"] = "returning"
        await root.takeovers.returned("lin")
        await asyncio.sleep(0)
        assert not running.done()
        assert worker.state.pending_turn["resolution"]["steps"]["speech"]["done"]
        assert worker.scene.reservation["phase"] == "running"
        release.set()
        await asyncio.wait_for(running, 1)
        assert root.state.record.seq == seq
        assert worker.state.pending_turn["resolution"]["steps"]["speech"]["value"] == "已经说出口的话。"
    finally:
        release.set()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
