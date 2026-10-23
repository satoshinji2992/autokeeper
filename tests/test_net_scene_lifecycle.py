"""Saved scene work has one owner, including private mail and incapacitated encounter turns."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import aiohttp

from autokeeper.engine.encounter.plan import EncounterStart
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.net.scenes import SceneTable, SceneTables
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast, opened, pool_open, until
from .test_net_scenes import SceneCast, joined


def party():
    module = Module.load(EXAMPLE)
    a = module.pregens[0].model_copy(deep=True)
    b = a.model_copy(deep=True, update={"id": "lin", "name": "林秋"})
    state = Game.new(module, [a, b], human=[a.id, b.id], slot="scene-lifecycle")
    state.apart = {b.id: "office"}
    return state


async def test_restored_private_mail_is_consumed_only_by_its_scene(fake):
    state = party()
    who = state.investigators[0].id
    state.private_messages[f"{who}:saved-mail"] = {
        "who": who, "text": "请解释刚才的个人发现。", "context": "已保存的提问时现场。", "input_shown": True,
    }
    entered, release = [], asyncio.Event()

    class MailCast(SceneCast):
        async def private_keeper(self, state, inv_id, text, *, context=""):
            entered.append((inv_id, state.location))
            await release.wait()
            return {"text": "这是本人已经见到的结果。", "media": []}

    hub = Hub(EXAMPLE, state=state, cast=MailCast(), rules=Rules(dice=fake),
              scene_cast_factory=lambda _: MailCast(), scene_rules_factory=lambda _: Rules(dice=fake))
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            a, b = await joined(http, hub, who), await joined(http, hub, "lin")
            await until(a, pool_open)
            await until(b, pool_open)
            assert entered == [(who, "square")]
            release.set()
            reply = await until(a, lambda msg: msg["t"] == "event" and msg.get("ev", {}).get("type") == "aside")
            assert reply["ev"]["text"] == "这是本人已经见到的结果。"
            assert len([entry for entry in state.log if entry.kind == "keeper" and "本人已经见到" in entry.text]) == 1
    finally:
        release.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_restored_scene_with_no_conscious_pc_resumes_its_active_encounter(fake, monkeypatch):
    state = party()
    state.location = "tower"
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake),
              scene_cast_factory=lambda _: SceneCast(), scene_rules_factory=lambda _: Rules(dice=fake))
    start = EncounterStart(mode="fight", foes=["warden"], reason="守钟人动手了")
    assert await hub.game._encounter_open(start, Turn())
    down = state.investigators[0]
    down.hp, down.conditions = 0, ["重伤", "濒死", "昏迷"]
    down.injury.dying_since = 1
    # Exercise the same restored format a real paused table resumes from.
    restored = GameState.model_validate_json(state.model_dump_json())
    hub.state = restored
    tables = SceneTables(hub, lambda _: SceneCast(), lambda _: Rules(dice=fake))
    ident = tables.runtime.for_member(down.id)
    worker = SceneTable(tables, ident)
    hub.sits[down.id] = "down-player"
    hub.clients["down-player"] = object()  # only online presence is read by this isolated loop
    settled = []

    async def collect():
        pass

    async def settle():
        settled.append(worker.game.world_encounter().id)
        worker.halted = True

    monkeypatch.setattr(worker, "_collect", collect)
    monkeypatch.setattr(worker, "_settle", settle)
    try:
        assert not worker.state.investigator.can_act and restored.investigators[1].can_act
        assert worker.game.world_encounter() is not None
        await asyncio.wait_for(worker.run(), 1)
        assert settled == [state.encounter.id]
    finally:
        hub.clients.clear()
        await tables.close()
        await hub.game.close()


async def test_supervisor_waits_for_restored_encounters_when_every_pc_is_down(fake, monkeypatch):
    state = party()
    state.location = "tower"
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake),
              scene_cast_factory=lambda _: SceneCast(), scene_rules_factory=lambda _: Rules(dice=fake))
    assert await hub.game._encounter_open(EncounterStart(mode="fight", foes=["warden"], reason="守钟人动手了"), Turn())
    for person in state.investigators:
        person.hp, person.conditions = 0, ["昏迷"]
    tables = SceneTables(hub, lambda _: SceneCast(), lambda _: Rules(dice=fake))
    for person in state.investigators:
        cid = f"client-{person.id}"
        hub.sits[person.id] = cid
        hub.clients[cid] = object()
    settled = []

    async def collect(self):
        pass

    async def settle(self):
        settled.append(self.game.world_encounter().id)
        # The existing engine owns the real outcome; the supervisor must wait for it.
        state.ending = "party_dead"
        self.halted = True

    monkeypatch.setattr(SceneTable, "_collect", collect)
    monkeypatch.setattr(SceneTable, "_settle", settle)
    try:
        await asyncio.wait_for(tables.run(), 1)
        assert settled == [state.encounter.id] and state.ending == "party_dead"
    finally:
        hub.clients.clear()
        await tables.close()
        await hub.game.close()

