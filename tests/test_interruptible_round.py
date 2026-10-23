"""Editable multiplayer preparation over real hosts, without external model calls."""
from __future__ import annotations

import asyncio

from autokeeper.engine.state import GameState
from autokeeper.net.hub import Hub
from autokeeper.net.scenes import SceneTable, SceneTables
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_multiplayer_transport import transport_hub as _transport_hub
from .test_net import TableCast
from .test_net_scene_lifecycle import party

transport_hub = _transport_hub


async def wait_for(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(0.001)


class PreparationGate:
    def __init__(self, game):
        self.original = game.prepare_party
        self.release = asyncio.Event()
        self.calls = []
        self.cancelled = 0

    async def __call__(self, order, **kwargs):
        draft = await self.original(order, **kwargs)
        self.calls.append(list(order))
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return draft


async def collect(hub):
    task = asyncio.create_task(hub._collect())
    await wait_for(lambda: hub.open)
    return task


async def test_new_declarations_interrupt_and_only_the_latest_batch_executes(transport_hub, monkeypatch):
    hub = transport_hub
    hub.sits.update(zhouheng="a", lin="b")
    gate = PreparationGate(hub.game)
    monkeypatch.setattr(hub.game, "prepare_party", gate)
    task = await collect(hub)
    try:
        await hub._on("a", {"t": "act", "text": "检查告示栏"})
        await wait_for(lambda: len(gate.calls) == 1)
        assert not hub.state.round_plan and not hub.state.pending_turn
        await hub._on("b", {"t": "act", "text": "留意楼梯"})
        await wait_for(lambda: len(gate.calls) == 2)
        await hub._on("a", {"t": "act", "text": "改为检查门锁"})
        await wait_for(lambda: len(gate.calls) == 3)
        assert gate.cancelled == 2 and len(hub.pool) == 2
        gate.release.set()
        await asyncio.wait_for(task, 3)
        assert not hub.open and hub.phase == "settling"
        assert set(map(tuple, hub.state.round_plan["input"])) == {("zhouheng", "改为检查门锁"), ("lin", "留意楼梯")}
        await hub._settle()
        actions = [entry.text for entry in hub.state.log if entry.kind == "player"]
        assert actions.count("改为检查门锁") == 1 and actions.count("留意楼梯") == 1
        assert "检查告示栏" not in actions
        assert not hub.state.pending_turn
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()


async def test_join_reserves_a_place_even_when_the_draft_is_ready_and_late_input_is_rejected(transport_hub, monkeypatch):
    hub = transport_hub
    hub.sits.update(zhouheng="a", lin="b")
    messages = []

    async def send(cid, message):
        messages.append((cid, message))

    monkeypatch.setattr(hub, "_send", send)
    task = await collect(hub)
    try:
        await hub._on("b", {"t": "join"})
        await hub._on("a", {"t": "act", "text": "检查门锁"})
        await wait_for(lambda: hub.phase == "ready")
        assert hub.open and not hub.state.round_plan
        await hub._on("b", {"t": "act", "text": "守住楼梯"})
        await asyncio.wait_for(task, 3)
        await hub._on("a", {"t": "act", "text": "锁轮后再拿走钥匙"})
        assert not hub.early and not hub.state.queued_actions
        assert messages[-1][1]["t"] == "action_rejected"
        assert messages[-1][1]["text"] == "锁轮后再拿走钥匙"
        assert set(map(tuple, hub.state.round_plan["input"])) == {("zhouheng", "检查门锁"), ("lin", "守住楼梯")}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()


async def test_cancelled_window_restores_its_latest_declarations_from_save(transport_hub, fake, monkeypatch):
    hub = transport_hub
    hub.sits.update(zhouheng="a", lin="b")
    gate = PreparationGate(hub.game)
    monkeypatch.setattr(hub.game, "prepare_party", gate)
    task = await collect(hub)
    restored = None
    try:
        await hub._on("a", {"t": "act", "text": "检查门锁"})
        await wait_for(lambda: bool(gate.calls))
        await hub._on("a", {"t": "act", "text": "改为查看窗框"})
        await wait_for(lambda: len(gate.calls) == 2)
        saved = GameState.model_validate_json(hub.state.model_dump_json())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert gate.cancelled == 2 and not saved.round_plan
        restored = Hub(EXAMPLE, state=saved, cast=TableCast(), rules=Rules(dice=fake))
        restored.sits.update(zhouheng="a", lin="b")
        await asyncio.wait_for(restored._collect(), 3)
        assert restored.state.round_plan["input"] == [["zhouheng", "改为查看窗框"]]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()
        if restored:
            await restored.game.close()


async def test_split_scenes_prepare_independently_and_route_join_to_its_own_scene(fake, monkeypatch):
    hub = Hub(EXAMPLE, state=party(), cast=TableCast(), rules=Rules(dice=fake))
    hub.m.events.clear()
    tables = SceneTables(hub, lambda _: TableCast(), lambda _: Rules(dice=fake))
    hub._scene_tables = tables
    for ident, scene in tables.runtime.scenes.items():
        if not scene.closed:
            tables.workers[ident] = SceneTable(tables, ident)
    a, b = tables.for_member("zhouheng"), tables.for_member("lin")
    hub.sits.update(zhouheng="a", lin="b")
    gates = [PreparationGate(worker.game) for worker in (a, b)]
    for worker, gate in zip((a, b), gates, strict=True):
        monkeypatch.setattr(worker.game, "prepare_party", gate)
    tasks = [await collect(worker) for worker in (a, b)]
    try:
        await hub._on("a", {"t": "act", "text": "查看书房"})
        await hub._on("b", {"t": "join"})
        await hub._on("b", {"t": "act", "text": "留意广场"})
        await wait_for(lambda: all(g.calls for g in gates))
        await hub._on("b", {"t": "act", "text": "改为守住街口"})
        await wait_for(lambda: len(gates[1].calls) == 2)
        assert len(gates[0].calls) == 1 and gates[0].cancelled == 0
        gates[1].release.set()
        await asyncio.wait_for(tasks[1], 3)
        assert a.open and not tasks[0].done()
        gates[0].release.set()
        await asyncio.wait_for(tasks[0], 3)
        assert a.state.round_plan["input"] == [["zhouheng", "查看书房"]]
        assert b.state.round_plan["input"] == [["lin", "改为守住街口"]]
        assert not hub.state.round_plan
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await tables.close()
        await hub.game.close()


async def test_failed_preparation_stays_editable_and_retries_without_executing(transport_hub, monkeypatch):
    hub = transport_hub
    hub.sits.update(zhouheng="a", lin="b")
    original = hub.game.prepare_party
    attempts = 0

    async def fail_once(order, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("offline preparation error")
        return await original(order, **kwargs)

    monkeypatch.setattr(hub.game, "prepare_party", fail_once)
    task = await collect(hub)
    try:
        await hub._on("a", {"t": "act", "text": "检查门锁"})
        await wait_for(lambda: bool(hub.preparation_error))
        assert hub.open and not hub.state.pending_turn and not hub.state.round_plan
        await hub._on("a", {"t": "act", "text": "/retry"})
        await asyncio.wait_for(task, 3)
        assert attempts == 2 and not hub.state.checks
        assert hub.state.round_plan["input"] == [["zhouheng", "检查门锁"]]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()


async def test_network_input_keeps_drafts_until_acceptance_and_never_queues_while_sealed(transport_hub):
    from textual.widgets import Input

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.net import NetGameScreen

    from .test_net import FakeLink

    hub = transport_hub
    link = FakeLink()
    app = AutoKeeperApp(theme="amber")
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            screen = NetGameScreen(hub.m, hub.state.model_copy(deep=True), link, "zhouheng", [])
            await app.push_screen(screen)
            await pilot.pause()
            packet = {"t": "pool", "open": True, "capacity": 2, "count": 0, "phase": "thinking",
                      "round": "r1", "open_to": ["zhouheng", "lin"], "mine": {"now": [], "next": []}}
            await screen.on_msg(packet)
            command = screen.query_one("#command", Input)
            command.value = "检查门锁"
            command.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert link.sent[-1] == {"t": "act", "text": "检查门锁", "round": "r1"}
            assert command.value == "检查门锁"
            await screen.on_msg(packet | {"count": 1, "mine": {"now": ["检查门锁"], "next": []}})
            assert command.value == ""
            await screen.on_msg(packet | {"open": False, "phase": "settling", "open_to": []})
            sent = len(link.sent)
            command.value = "然后去地下室"
            await pilot.press("enter")
            await pilot.pause()
            assert len(link.sent) == sent
            assert command.value == "然后去地下室"
            assert not screen.held
            await screen.on_msg(packet | {"round": "r2"})
            screen.action_join_round()
            await pilot.pause()
            assert link.sent[-1] == {"t": "join", "round": "r2"}
            assert command.value == "然后去地下室"  # A new window never auto-submits a draft.
    finally:
        await hub.game.close()
