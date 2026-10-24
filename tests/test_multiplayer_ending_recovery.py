"""A saved ending is not finished until its settled turn has been narrated."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Narration, Ruling
from autokeeper.engine.encounter.plan import EncounterStart
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.harness.spend import BudgetExceeded
from autokeeper.module.schema import Event, Module
from autokeeper.net.hub import Hub
from autokeeper.net.scenes import SceneTables
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast


class FinalCast(TableCast):
    def __init__(self, *, fail=False, entered=None, release=None):
        super().__init__()
        self.fail = fail
        self.entered, self.release = entered, release
        self.rulings = self.tellings = 0

    async def arbiter(self, state, action, at=None, **kwargs):
        self.rulings += 1
        return Ruling(needs_roll=False, minutes=1)

    async def keeper(self, state, report, on_delta=None, lore=""):
        self.tellings += 1
        if self.fail:
            raise BudgetExceeded("offline final telling interruption")
        if self.entered is not None:
            self.entered.set()
            await self.release.wait()
        return "调查结束，众人离开了广场。"


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("interrupt_again", [False, True])
async def test_play_restores_ending_telling_before_announcing_over(fake, monkeypatch, independent, interrupt_again):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [lead, friend], human=[lead.id, friend.id])
    first = Hub(EXAMPLE, state=state, cast=FinalCast(fail=True), rules=Rules(dice=fake))
    first.m.events = [Event(id="final", title="调查结束", when=module.start_time,
                            text="调查结束。", ending="party_dead")]
    first.m.endings, first.m.foreshadow, first.m.agendas = [], [], []
    resumed, work = None, None
    entered, release, retry = asyncio.Event(), asyncio.Event(), asyncio.Event()
    try:
        await first.game.arrange_party([(lead.id, "离开广场")])
        with pytest.raises(BudgetExceeded):
            await first._run("离开广场", lead.id, ai=True)
        assert state.ending and state.pending_turn["stage"] == "narrate"
        saved = GameState.model_validate_json(state.model_dump_json())
        casts = []

        def factory(_):
            cast = FinalCast(fail=interrupt_again, entered=entered, release=release)
            casts.append(cast)
            return cast

        cast = FinalCast(fail=interrupt_again, entered=entered, release=release)
        resumed = Hub(EXAMPLE, state=saved, cast=cast, rules=Rules(dice=fake),
                      scene_cast_factory=factory if independent else None,
                      scene_rules_factory=lambda _: Rules(dice=fake))
        resumed.m.events, resumed.m.endings, resumed.m.foreshadow, resumed.m.agendas = [], [], [], []
        messages = []

        async def broadcast(message):
            messages.append(message)

        async def send(cid, message):
            messages.append(message)
            if message.get("retry"):
                retry.set()

        monkeypatch.setattr(resumed, "_broadcast", broadcast)
        monkeypatch.setattr(resumed, "_send", send)
        resumed.clients["player"] = object()
        resumed.sits[lead.id] = "player"
        resumed.started.set()
        before = saved.turn, saved.minutes, len(saved.checks)
        work = asyncio.create_task(resumed.play())
        if interrupt_again:
            await asyncio.wait_for(retry.wait(), 2)
            assert not resumed.over and not work.done()
            for host in casts if independent else [cast]:
                host.fail = False
            await resumed._on("player", {"t": "act", "text": "/retry"})
        await asyncio.wait_for(entered.wait(), 2)
        assert not resumed.over and not work.done()
        assert not any(message["t"] == "over" for message in messages)
        await resumed._on("player", {"t": "act", "text": "再去书房搜查"})
        assert messages[-1]["t"] == "error" and "本局已结束" in messages[-1]["text"]
        release.set()
        await asyncio.wait_for(work, 2)
        hosts = casts if independent else [cast]
        assert sum(host.tellings for host in hosts) == (2 if interrupt_again else 1)
        assert sum(host.rulings for host in hosts) == 0
        assert (saved.turn, saved.minutes, len(saved.checks)) == before
        assert resumed.over
        assert messages[-1]["t"] == "over"
        assert any(message.get("ev", {}).get("type") == "keeper_end" for message in messages)
        if independent:
            assert not any(scene.pending_turn for scene in saved.scenes.values() if not scene.closed)
        else:
            assert not saved.pending_turn
    finally:
        release.set()
        if work is not None and not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
        await first.game.close()
        if resumed is not None:
            resumed.clients.clear()
            if resumed._scene_tables is not None:
                await resumed._scene_tables.close()
            await resumed.game.close()


async def test_play_keeps_settling_an_existing_encounter_when_every_investigator_is_down(fake, monkeypatch):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    state = Game.new(module, [lead], human=[lead.id])
    state.location = "tower"
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    try:
        assert await hub.game._encounter_open(
            EncounterStart(mode="fight", foes=["warden"], reason="守钟人动手了"), Turn())
        state.investigator.hp, state.investigator.conditions = 0, ["昏迷"]
        assert not state.pending_turn and not state.investigator.can_act
        settled, messages = [], []

        async def send(cid, message):
            messages.append(message)

        async def broadcast(message):
            messages.append(message)

        async def settle():
            settled.append(hub.game.world_encounter().id)
            state.ending = "party_dead"

        monkeypatch.setattr(hub, "_send", send)
        monkeypatch.setattr(hub, "_broadcast", broadcast)
        monkeypatch.setattr(hub, "_settle", settle)
        hub.clients["player"] = object()
        hub.sits[lead.id] = "player"
        hub.started.set()
        await asyncio.wait_for(hub.play(), 2)
        assert settled == [state.encounter.id]
        assert messages[-1]["t"] == "over" and hub.over
    finally:
        hub.clients.clear()
        await hub.game.close()


@pytest.mark.parametrize("independent", [False, True])
async def test_ending_flushes_held_results_and_reload_retries_only_the_failed_scene(fake, monkeypatch, independent):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [lead, friend], human=[lead.id, friend.id])
    state.apart = {friend.id: "office"} if independent else {}
    state.ending = "party_dead"
    seed = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))

    def held(who, where, readers):
        return {"turn": 1, "who": state.find(who).name, "actor": who, "place": where,
                "audience": readers, "report": f"{who} 已经完成的调查结果。", "rest": None}

    try:
        if independent:
            tables = SceneTables(seed, lambda _: TableCast(), lambda _: Rules(dice=fake))
            for who, where in ((lead.id, "square"), (friend.id, "office")):
                tables.runtime.scenes[tables.runtime.for_member(who)].held = [held(who, where, [who])]
            await tables.close()
        else:
            state.held = [held(lead.id, "square", None)]
        state = GameState.model_validate_json(state.model_dump_json())
    finally:
        await seed.game.close()

    calls = []
    fail = True

    class SettledCast(TableCast):
        async def keeper(self, state, report, on_delta=None, lore="", *, check=None, fix="", settled=False):
            assert settled  # These receipts cannot start world operations or new actions.
            calls.append(state.investigator.id)
            if fail and state.investigator.id == lead.id:
                raise BudgetExceeded("offline one-scene final interruption")
            return Narration(text=f"{state.investigator.name}先前的调查结果。", scene={"room": "tower"})

    async def reopen(saved):
        hub = Hub(EXAMPLE, state=saved, cast=SettledCast(), rules=Rules(dice=fake),
                  scene_cast_factory=(lambda _: SettledCast()) if independent else None,
                  scene_rules_factory=lambda _: Rules(dice=fake))
        messages = []

        async def broadcast(message):
            messages.append(message)

        monkeypatch.setattr(hub, "_broadcast", broadcast)
        hub.started.set()
        try:
            await asyncio.wait_for(hub.play(), 2)
            assert messages[-1]["t"] == "over" and hub.over
            return GameState.model_validate_json(saved.model_dump_json())
        finally:
            if hub._scene_tables is not None:
                await hub._scene_tables.close()
            await hub.game.close()

    state = await reopen(state)
    assert sorted(calls) == (sorted([lead.id, friend.id]) if independent else [lead.id])
    assert sum(entry.kind == "keeper" for entry in state.log) == int(independent)
    fail = False
    calls.clear()
    before = state.turn, state.minutes, len(state.checks), dict(state.positions), state.record.seq
    state = await reopen(state)
    assert calls == [lead.id]
    assert (state.turn, state.minutes, len(state.checks), dict(state.positions), state.record.seq) == before
    assert sum(entry.kind == "keeper" for entry in state.log) == (2 if independent else 1)
    calls.clear()
    await reopen(state)
    assert not calls  # Delivered scenes do not reappear on another reconnect.
