"""Encounter rounds wait for relevant players, not their real-world reading speed."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.engine.encounter.model import EncounterState, Participant
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_multiplayer_transport import sit, sockets
from .test_net import TableCast, until


def encounter(mode="fight"):
    return EncounterState(id="sudden", mode=mode, place="tower", started_turn=0, participants=[
        Participant(id=who, kind="investigator", side="party", controller="human")
        for who in ("zhouheng", "lin")])


@pytest.fixture
async def round_hub(fake):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    party = [lead, lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True),
             lead.model_copy(update={"id": "remote", "name": "异地同伴"}, deep=True)]
    st = Game.new(m, party, human=[inv.id for inv in party])
    st.location, st.apart, st.encounter = "tower", {"remote": "office"}, encounter()
    hub = Hub(EXAMPLE, state=st, cast=TableCast(), rules=Rules(dice=fake))
    hub.window = 0.01
    hub.started.set()
    yield hub
    await hub.game.close()


@pytest.mark.parametrize("mode", ["fight", "chase"])
@pytest.mark.parametrize("focused_remote", [False, True])
async def test_reading_an_encounter_never_times_out_and_a_remote_nonparticipant_does_not_hold_the_round(round_hub, mode, focused_remote):
    hub = round_hub
    hub.state.encounter.mode = mode
    if focused_remote:
        hub.state.active, hub.state.location = 2, "office"
        hub.state.apart = {"zhouheng": "tower", "lin": "tower"}
        assert hub.game.encounter() is None and hub.game.world_encounter() is not None
    before = (hub.state.minutes, hub.state.encounter.completed_rounds)
    async with sockets(hub) as (http, url):
        a, b = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
        remote = await sit(http, url, "remote")
        task = asyncio.create_task(hub._collect())
        await until(a, lambda msg: msg["t"] == "pool" and msg["open"])
        await a.send_json({"t": "act", "text": "守住门口"})
        pool = await until(a, lambda msg: msg["t"] == "pool" and msg["count"] == 1)
        assert pool["closes_in"] is None and pool["open_to"] == ["lin"]
        if focused_remote:
            await remote.send_json({"t": "act", "text": "翻阅报纸"})
            await until(remote, lambda msg: msg["t"] == "pool" and msg["count"] == 2)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.06)
        assert (hub.state.minutes, hub.state.encounter.completed_rounds) == before
        await b.send_json({"t": "done"})
        await asyncio.wait_for(task, 1)
        assert hub.pool == [("zhouheng", "守住门口"), *([("remote", "翻阅报纸")] if focused_remote else [])]
        assert "lin" in hub.done
        assert hub.state.round_plan["encounter"] == ["sudden", mode, 1]


@pytest.mark.parametrize("choice,revised", [("keep", ""), ("", "挣脱束缚"), ("pass", "")])
async def test_queued_noncombat_intent_requires_its_owners_new_encounter_choice(round_hub, choice, revised):
    hub = round_hub
    hub.early = [("zhouheng", "给家里打电话")]
    async with sockets(hub) as (http, url):
        a, b = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
        task = asyncio.create_task(hub._collect())
        offer = (await until(a, lambda msg: msg["t"] == "offer"))["offer"]
        assert offer["kind"] == "redeclare" and offer["closes_in"] is None
        assert not hub.pool and hub.early == [("zhouheng", "给家里打电话")]
        await b.send_json({"t": "act", "text": "退到门边"})
        await until(b, lambda msg: msg["t"] == "pool" and msg["count"] == 1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.06)
        await a.send_json({"t": "respond", "id": offer["respond_id"], "choice": choice, "text": revised})
        await asyncio.wait_for(task, 1)
        if choice == "pass":
            assert hub.pool == [("lin", "退到门边")] and "zhouheng" in hub.done
            assert hub.early == [("zhouheng", "给家里打电话")]
        else:
            assert ("zhouheng", revised or "给家里打电话") in hub.pool and not hub.early


@pytest.mark.parametrize("kind", ["reaction", "redeclare"])
async def test_active_encounter_choices_wait_for_the_players_explicit_answer(round_hub, monkeypatch, kind):
    hub = round_hub
    monkeypatch.setattr("autokeeper.net.hub.REACTION_WAIT", 0.01)
    async with sockets(hub) as (http, url):
        a = await sit(http, url, "zhouheng")
        task = asyncio.create_task(hub._ask("zhouheng", hub.sits["zhouheng"], {
            "kind": kind, "options": [{"id": "dodge", "label": "闪避"}], "default": "dodge"}, timed=True))
        offer = (await until(a, lambda msg: msg["t"] == "offer"))["offer"]
        assert offer["closes_in"] is None
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.06)
        await a.send_json({"t": "respond", "id": offer["respond_id"], "choice": "dodge"})
        assert await asyncio.wait_for(task, 1) == "dodge"


async def test_disconnect_keeps_that_encounter_players_collection_wait_until_reconnect(round_hub):
    hub = round_hub
    async with sockets(hub) as (http, url):
        a, b = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
        task = asyncio.create_task(hub._collect())
        await until(a, lambda msg: msg["t"] == "pool" and msg["open"])
        await a.send_json({"t": "act", "text": "掩护同伴"})
        await until(a, lambda msg: msg["t"] == "pool" and msg["count"] == 1)
        await b.close()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.06)
        assert hub._waiting_for() == ["lin"]
        b = await sit(http, url, "lin")
        await b.send_json({"t": "done"})
        await asyncio.wait_for(task, 1)
        assert hub.pool == [("zhouheng", "掩护同伴")]


async def test_a_sudden_encounter_defers_the_unstarted_rest_of_the_original_batch(round_hub, monkeypatch, fake):
    hub = round_hub
    hub.state.encounter = None
    order = [("zhouheng", "查看走廊"), ("lin", "等候来信")]
    await hub.game.arrange_party(order)
    calls = []

    async def run(text, seat, **kwargs):
        calls.append((seat, text))
        hub.state.encounter = encounter()

    monkeypatch.setattr(hub, "_run", run)
    await hub._settle_round(order)
    assert calls == order[:1]
    assert hub.early == order[1:] and hub.state.queued_actions == order[1:]
    assert hub.game.pending_party() is None
    restored = Hub(EXAMPLE, state=GameState.model_validate_json(hub.state.model_dump_json()), cast=TableCast(), rules=Rules(dice=fake))
    assert restored.early == order[1:] and restored.game.pending_party() is None
    await restored.game.close()


async def test_a_resume_finishes_only_the_started_action_before_reconfirming_the_old_batch_remainder(round_hub, monkeypatch):
    hub = round_hub
    hub.state.encounter = None
    order = [("zhouheng", "查看走廊"), ("lin", "等候来信")]
    await hub.game.arrange_party(order)
    hub.state.pending_turn = {"input": order[0][1], "party_token": "0"}
    hub.state.encounter = encounter()  # the first action already caused this encounter before the save
    calls = []

    async def resume(text, seat, **kwargs):
        calls.append((seat, text))
        hub.state.pending_turn.clear()

    monkeypatch.setattr(hub, "_run", resume)
    await hub._settle_round(order)
    assert calls == order[:1] and hub.early == order[1:]
    assert hub.game.pending_party() is None


@pytest.mark.parametrize("mode", ["fight", "chase"])
async def test_explicit_passes_allow_the_confirmed_npc_round_even_with_a_remote_investigation(round_hub, monkeypatch, mode):
    hub = round_hub
    hub.state.encounter.mode = mode
    hub.state.active, hub.state.location = 2, "office"
    hub.state.apart = {"zhouheng": "tower", "lin": "tower"}
    hub.state.round_plan["encounter"] = ["sudden", mode, 1]
    hub.done = {"zhouheng", "lin"}
    hub.sits = {"zhouheng": "a", "lin": "b", "remote": "c"}
    hub.pool = [("remote", "整理手边报纸")]
    calls = []

    async def run(text, seat, **kwargs):
        calls.append((seat, text))

    monkeypatch.setattr(hub, "_run", run)
    await hub._settle()
    assert calls == [("remote", "整理手边报纸"), ("zhouheng", "/encounter")]


async def test_reading_wall_clock_does_not_run_the_world_interlude(round_hub, monkeypatch):
    hub = round_hub
    calls = []

    async def interlude():
        calls.append("world progression")

    monkeypatch.setattr(hub, "_interlude", interlude)
    async with sockets(hub) as (http, url):
        a, b = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
        task = asyncio.create_task(hub._collect())
        await until(a, lambda msg: msg["t"] == "pool" and msg["open"])
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 1.1)
        assert not calls and hub.state.encounter.completed_rounds == 0
        await a.send_json({"t": "done"})
        await b.send_json({"t": "done"})
        await asyncio.wait_for(task, 1)
