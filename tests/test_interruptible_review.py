"""Independent review of editable declaration cancellation and scene-local admission."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from autokeeper.agents.cast import Action, PartyPlan, PartyStep, Plan
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.net.scenes import SceneTable, SceneTables
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import AddFact, Condition, Rewrite

from .test_net import TableCast


async def reached(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


class PausingPlanner(TableCast):
    def __init__(self):
        super().__init__()
        self.calls: list[list] = []
        self.gates: list[asyncio.Event] = []
        self.cancelled = 0

    async def prepare_party_plan(self, state, inputs):
        self.calls.append(list(inputs))
        gate = asyncio.Event()
        self.gates.append(gate)
        try:
            await gate.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return PartyPlan(steps=[PartyStep(token=row.token, plan=Plan(action=Action(kind="investigate", summary=row.text)))
                                for row in inputs])


def make_hub(fake, monkeypatch):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [lead, friend], human=[lead.id, friend.id], slot="interrupt-review")
    cast = PausingPlanner()
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    hub.clients = {"a": object(), "b": object()}
    hub.sits = {lead.id: "a", friend.id: "b"}
    hub.names = {"a": "甲", "b": "乙"}
    hub.started.set()
    sent = []

    async def send(cid, msg):
        sent.append((cid, deepcopy(msg)))

    monkeypatch.setattr(hub, "_send", send)
    return hub, cast, sent


async def stop_collecting(task):
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_joining_player_holds_ready_draft_then_enters_revised_batch(fake, monkeypatch):
    hub, cast, _ = make_hub(fake, monkeypatch)
    collecting = asyncio.create_task(hub._collect())
    try:
        await reached(lambda: hub.open)
        await hub._on("a", {"t": "act", "text": "查看告示栏"})
        await reached(lambda: len(cast.calls) == 1)
        await hub._on("b", {"t": "join"})
        cast.gates[0].set()
        await reached(lambda: hub.phase == "ready")
        assert hub.open and not hub.state.round_plan.get("input")
        await hub._on("b", {"t": "act", "text": "留意路口"})
        await reached(lambda: len(cast.calls) == 2)
        cast.gates[1].set()
        await asyncio.wait_for(collecting, 3)
        assert hub.state.round_plan["input"] == [["zhouheng", "查看告示栏"], ["lin", "留意路口"]]
        assert hub.phase == "settling" and not hub.open
    finally:
        await stop_collecting(collecting)
        await hub.game.close()


async def test_scene_revision_cancels_only_its_own_draft(fake, monkeypatch):
    root, _, _ = make_hub(fake, monkeypatch)
    root.state.apart = {"lin": "office"}
    casts = {}

    def factory(ident):
        casts[ident] = PausingPlanner()
        return casts[ident]

    tables = SceneTables(root, factory, lambda ident: Rules(dice=fake))
    root._scene_tables = tables
    for ident in tables.runtime.scenes:
        tables.workers[ident] = SceneTable(tables, ident)
    a, b = tables.for_member("zhouheng"), tables.for_member("lin")
    a_cast, b_cast = casts[a.ident], casts[b.ident]
    tasks = [asyncio.create_task(worker._collect()) for worker in (a, b)]
    try:
        await reached(lambda: a.open and b.open)
        await root._on("a", {"t": "act", "text": "查看广场"})
        await root._on("b", {"t": "act", "text": "查阅报纸"})
        await reached(lambda: len(a_cast.calls) == len(b_cast.calls) == 1)
        await root._on("a", {"t": "act", "text": "改查路口"})
        await reached(lambda: len(a_cast.calls) == 2)
        assert a_cast.cancelled == 1 and b_cast.cancelled == 0 and len(b_cast.calls) == 1
        b_cast.gates[0].set()
        await asyncio.wait_for(tasks[1], 3)
        assert b.state.round_plan["input"] == [["lin", "查阅报纸"]]
        a_cast.gates[1].set()
        await asyncio.wait_for(tasks[0], 3)
        assert a.state.round_plan["input"] == [["zhouheng", "改查路口"]]
        assert not root.state.record.events
    finally:
        for task in tasks:
            await stop_collecting(task)
        await tables.close()
        await root.game.close()


async def test_local_world_change_revises_only_the_affected_scene(fake, monkeypatch):
    root, _, _ = make_hub(fake, monkeypatch)
    root.state.apart = {"lin": "office"}
    casts = {}

    def factory(ident):
        casts[ident] = PausingPlanner()
        return casts[ident]

    tables = SceneTables(root, factory, lambda ident: Rules(dice=fake))
    root._scene_tables = tables
    for ident in tables.runtime.scenes:
        tables.workers[ident] = SceneTable(tables, ident)
    a, b = tables.for_member("zhouheng"), tables.for_member("lin")
    a_cast, b_cast = casts[a.ident], casts[b.ident]
    tasks = [asyncio.create_task(worker._collect()) for worker in (a, b)]
    try:
        await reached(lambda: a.open and b.open)
        await root._on("a", {"t": "act", "text": "查看广场"})
        await root._on("b", {"t": "act", "text": "查阅报纸"})
        await reached(lambda: len(a_cast.calls) == len(b_cast.calls) == 1)
        assert b.game.world.apply(AddFact(fact="new-office-note", text="档案架已经整理过。", about=["office"])).ok
        a.wake.set()
        b.wake.set()
        await reached(lambda: len(b_cast.calls) == 2)
        assert a_cast.cancelled == 0 and len(a_cast.calls) == 1 and b_cast.cancelled == 1
        a_cast.gates[0].set()
        b_cast.gates[1].set()
        await asyncio.wait_for(asyncio.gather(*tasks), 3)
    finally:
        for task in tasks:
            await stop_collecting(task)
        await tables.close()
        await root.game.close()


async def test_sealed_batch_rejects_late_action_without_queueing_it(fake, monkeypatch):
    hub, cast, sent = make_hub(fake, monkeypatch)
    original_pool = hub._pool
    late_sent = False

    async def pool():
        nonlocal late_sent
        if hub.phase == "settling" and not late_sent:
            late_sent = True
            await hub._on("a", {"t": "act", "text": "已经封轮后的行动"})
        await original_pool()

    monkeypatch.setattr(hub, "_pool", pool)
    collecting = asyncio.create_task(hub._collect())
    try:
        await reached(lambda: hub.open)
        await hub._on("a", {"t": "act", "text": "先看告示栏"})
        await reached(lambda: len(cast.calls) == 1)
        cast.gates[0].set()
        await asyncio.wait_for(collecting, 3)
        assert late_sent and hub.state.round_plan["input"] == [["zhouheng", "先看告示栏"]]
        assert not hub.early and not hub.queued_declarations()
        assert any(cid == "a" and msg.get("t") == "action_rejected" for cid, msg in sent)
    finally:
        await stop_collecting(collecting)
        await hub.game.close()


async def test_cancelling_collection_cleans_up_its_pending_model(fake, monkeypatch):
    hub, cast, _ = make_hub(fake, monkeypatch)
    collecting = asyncio.create_task(hub._collect())
    try:
        await reached(lambda: hub.open)
        await hub._on("a", {"t": "act", "text": "查看告示栏"})
        await reached(lambda: len(cast.calls) == 1)
        collecting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await collecting
        assert hub.preparing is None and cast.cancelled == 1
        assert not hub.open and not hub.state.round_plan.get("input")
        assert hub.queued_declarations() == [("zhouheng", "查看告示栏")]
    finally:
        await stop_collecting(collecting)
        await hub.game.close()


async def test_reconnected_human_is_not_executed_from_an_unsealed_ai_placeholder(fake, monkeypatch):
    hub, cast, _ = make_hub(fake, monkeypatch)
    hub.sits["lin"] = None
    collecting = asyncio.create_task(hub._collect())
    try:
        await reached(lambda: hub.open)
        await hub._on("a", {"t": "act", "text": "查看广场"})
        await reached(lambda: len(cast.calls) == 1)
        await hub._sit("b", "lin")
        await reached(lambda: len(cast.calls) == 2)
        assert cast.cancelled == 1
        cast.gates[1].set()
        await asyncio.wait_for(collecting, 3)
        await asyncio.wait_for(hub._settle(), 3)
        assert not cast.ai_turns
    finally:
        await stop_collecting(collecting)
        await hub.game.close()


async def test_reconnecting_after_seal_still_reclaims_an_unstarted_ai_action(fake, monkeypatch):
    hub, cast, _ = make_hub(fake, monkeypatch)
    hub.sits["lin"] = None
    collecting = asyncio.create_task(hub._collect())
    try:
        await reached(lambda: hub.open)
        await hub._on("a", {"t": "act", "text": "查看广场"})
        await reached(lambda: len(cast.calls) == 1)
        cast.gates[0].set()
        await asyncio.wait_for(collecting, 3)
        assert ["lin", None] in hub.state.round_plan["input"]
        await hub._sit("b", "lin")
        await asyncio.wait_for(hub._settle(), 3)
        assert not cast.ai_turns
    finally:
        await stop_collecting(collecting)
        await hub.game.close()


def test_private_keeper_reply_invalidates_its_scene_preparation(fake, monkeypatch):
    hub, _, _ = make_hub(fake, monkeypatch)
    stamp = hub._preparation_stamp()
    hub.state.add("keeper", "更正刚才的说明：侧门没有上锁。", who="守秘人", audience=["zhouheng"])
    assert hub._preparation_stamp() != stamp


@pytest.mark.parametrize("position_field", ["positions", "apart"])
def test_scene_preparation_stamp_tracks_actual_member_positions(fake, monkeypatch, position_field):
    hub, _, _ = make_hub(fake, monkeypatch)
    setattr(hub.state, position_field, {})
    stamp = hub._preparation_stamp()
    getattr(hub.state, position_field)["lin"] = "office"
    assert hub.game.world.place("lin") == "office"
    assert hub._preparation_stamp() != stamp


async def test_one_keeper_draft_tracks_both_rooms_its_participants_occupy(fake, monkeypatch):
    root, _, _ = make_hub(fake, monkeypatch)
    root.m.location("office").within = "tower"
    root.state.location = "tower"
    root.state.apart = {"lin": "office"}
    tables = SceneTables(root, lambda ident: PausingPlanner(), lambda ident: Rules(dice=fake))
    try:
        assert len(tables.runtime.scenes) == 1
        ident = next(iter(tables.runtime.scenes))
        worker = SceneTable(tables, ident)
        tables.workers[ident] = worker
        assert worker.state.location == "tower"
        assert worker.game.world.place("lin") == "office"
        assert worker.game.world.apply(AddFact(fact="location:office:keeper", text="档案员把旧报纸放在架上。",
                                                about=["office"], visibility="private")).ok
        stamp = worker._preparation_stamp()
        changed = worker.game.world.apply(Rewrite(target="fact:location:office:keeper", text="档案员已把旧报纸收进库房。"))
        assert changed.ok, changed.problems
        assert worker._preparation_stamp() != stamp
    finally:
        await tables.close()
        await root.game.close()


async def test_remote_investigator_condition_does_not_invalidate_an_unrelated_scene(fake, monkeypatch):
    root, _, _ = make_hub(fake, monkeypatch)
    root.state.apart = {"lin": "office"}
    tables = SceneTables(root, lambda ident: PausingPlanner(), lambda ident: Rules(dice=fake))
    root._scene_tables = tables
    for ident in tables.runtime.scenes:
        tables.workers[ident] = SceneTable(tables, ident)
    local, remote = tables.for_member("zhouheng"), tables.for_member("lin")
    try:
        assert local.state.location == "square" and remote.game.world.place("lin") == "office"
        stamp = local._preparation_stamp()
        assert remote.game.world.apply(Condition(entity="lin", add="疲倦", visibility="private")).ok
        assert "疲倦" in remote.state.find("lin").conditions
        assert local._preparation_stamp() == stamp
    finally:
        await tables.close()
        await root.game.close()
