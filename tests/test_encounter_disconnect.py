"""A lost connection is not consent to a new encounter action or a pending throw."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import TURN_RECORD, Turn
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE

from .encounter_kit import attack
from .test_multiplayer_transport import sit, sockets
from .test_net import until
from .test_table_encounter_start import opening
from .test_turn_based_collection import round_hub as _round_hub

round_hub = _round_hub


@pytest.mark.parametrize("mode", ["fight", "chase"])
@pytest.mark.parametrize("kind", ["roll", "reaction", "redeclare"])
async def test_disconnected_encounter_choice_waits_and_reconnect_reuses_its_original_offer(round_hub, mode, kind):
    hub = round_hub
    hub.state.encounter.mode = mode
    task = None
    try:
        async with sockets(hub) as (http, url):
            a, b = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
            task = asyncio.create_task(hub._ask("zhouheng", hub.sits["zhouheng"], {"kind": kind}, timed=True))
            offer = (await until(a, lambda msg: msg["t"] == "offer"))["offer"]
            await a.close()
            await until(b, lambda msg: msg["t"] == "seats" and not msg["seats"][0]["online"])
            await asyncio.sleep(0.03)
            assert not task.done() and hub.asking["zhouheng"] == offer["respond_id"]
            a = await sit(http, url, "zhouheng")
            repeated = (await until(a, lambda msg: msg["t"] == "offer"))["offer"]
            assert repeated == offer and repeated["closes_in"] is None
            await a.send_json({"t": "respond", "id": repeated["respond_id"], "choice": "roll"})
            assert await asyncio.wait_for(task, 1) == "roll"
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("mode", ["fight", "chase"])
async def test_spectators_do_not_advance_collection_when_all_human_participants_disconnect(round_hub, mode):
    hub = round_hub
    hub.state.encounter.mode = mode
    task = None
    try:
        async with sockets(hub) as (http, url):
            a, b = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
            spectator = await http.ws_connect(url)
            await spectator.send_json({"t": "hello", "name": "watching"})
            await until(spectator, lambda msg: msg["t"] == "welcome")
            task = asyncio.create_task(hub._collect())
            await until(a, lambda msg: msg["t"] == "pool" and msg["open"])
            await a.close()
            await b.close()
            await asyncio.sleep(0.05)
            assert hub.clients and not task.done()
            assert set(hub._waiting_for()) == {"zhouheng", "lin"}
            assert hub.state.encounter.completed_rounds == 0 and "encounter" not in hub.state.round_plan
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("disconnect_before_offer", [False, True])
async def test_an_actual_pending_attack_never_rolls_on_disconnect_and_resumes_only_once(fake, disconnect_before_offer):
    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    game, cast, _events = await opening(module, fake, "fight", by_hand=True)
    game.state.round_plan["encounter"] = [game.state.encounter.id, "fight", 1]
    turn = Turn()
    await game._beat(turn, settle=True)
    pending = game.state.encounter.pending
    assert pending is not None and pending.kind == "roll"
    game.state.pending_turn.update(action=Action(kind="other", summary="冲突开始").model_dump(mode="json"),
                                   ruling=None, turn=TURN_RECORD.dump_python(turn, mode="json"), by_hand=True)
    hub = Hub(EXAMPLE, state=game.state, cast=cast, rules=game.rules)
    hub.started.set()
    task = None
    fake.queue(10, 90).queue_totals(1)

    try:
        async with sockets(hub) as (http, url):
            a = await sit(http, url, "zhouheng")
            spectator = await http.ws_connect(url)
            await spectator.send_json({"t": "hello", "name": "watching"})
            await until(spectator, lambda msg: msg["t"] == "welcome")
            if disconnect_before_offer:
                await a.close()
                await until(spectator, lambda msg: msg["t"] == "seats" and not msg["seats"][0]["online"])
            task = asyncio.create_task(hub._run("/retry", "zhouheng", ai=False))
            if disconnect_before_offer:
                await asyncio.sleep(0.05)
                assert not task.done() and hub.asking.get("zhouheng")
                assert list(fake.values) == [10, 90] and list(fake.totals) == [1]
                a = await sit(http, url, "zhouheng")
            offer = (await until(a, lambda msg: msg["t"] == "offer"))["offer"]
            await a.close()
            await asyncio.sleep(0.05)
            assert not task.done() and list(fake.values) == [10, 90] and list(fake.totals) == [1]
            assert hub.state.encounter.completed_rounds == 0 and hub.state.encounter.pending.id == pending.id
            a = await sit(http, url, "zhouheng")
            repeated = (await until(a, lambda msg: msg["t"] == "offer"))["offer"]
            assert repeated["respond_id"] == offer["respond_id"]
            for invalid in ("", "cancel"):
                await a.send_json({"t": "respond", "id": repeated["respond_id"], "choice": invalid})
                await until(a, lambda msg: msg["t"] == "notice")
                assert hub.asking["zhouheng"] == offer["respond_id"] and not task.done()
                assert list(fake.values) == [10, 90] and list(fake.totals) == [1]
            await a.send_json({"t": "respond", "id": repeated["respond_id"], "choice": "roll"})
            await asyncio.wait_for(task, 1)
            assert hub.state.encounter.completed_rounds == 1 and hub.state.encounter.pending is None
            assert not fake.values and not fake.totals
            await a.send_json({"t": "respond", "id": repeated["respond_id"], "choice": "roll"})
            await until(a, lambda msg: msg["t"] == "notice")
            assert hub.state.encounter.completed_rounds == 1
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()
        await game.close()


async def test_a_submitted_attack_still_generates_a_human_roll_when_its_owner_is_already_offline(fake):
    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    game, cast, _events = await opening(module, fake, "fight")
    game.state.round_plan["encounter"] = [game.state.encounter.id, "fight", 1]
    action = Action(kind="attack", summary="攻击守钟人",
                    declared={"plan": {"action": attack("warden").model_dump(mode="json")}, "beat": True})
    game.state.turn += 1
    game.state.pending_turn = dict(input=action.summary, action=action.model_dump(mode="json"),
                                   ruling=Ruling(needs_roll=False).model_dump(mode="json"), by_hand=False)
    hub = Hub(EXAMPLE, state=game.state, cast=cast, rules=game.rules)
    hub.started.set()
    task = None
    fake.queue(10, 90).queue_totals(1)
    try:
        async with sockets(hub) as (http, url):
            spectator = await http.ws_connect(url)
            await spectator.send_json({"t": "hello", "name": "watching"})
            await until(spectator, lambda msg: msg["t"] == "welcome")
            task = asyncio.create_task(hub._run("/retry", "zhouheng", ai=False))
            await asyncio.sleep(0.05)
            assert not task.done() and hub.asking.get("zhouheng")
            assert hub.state.encounter.pending.kind == "roll"
            assert list(fake.values) == [10, 90] and list(fake.totals) == [1]
            a = await sit(http, url, "zhouheng")
            offer = (await until(a, lambda msg: msg["t"] == "offer"))["offer"]
            await a.send_json({"t": "respond", "id": offer["respond_id"], "choice": "roll"})
            await asyncio.wait_for(task, 1)
            assert hub.state.encounter.completed_rounds == 1 and not fake.values and not fake.totals
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()
        await game.close()


@pytest.mark.parametrize("mode", ["fight", "chase"])
async def test_extra_pool_slots_never_replace_an_encounter_participants_one_action_or_confirmation(round_hub, mode):
    hub = round_hub
    hub.state.encounter.mode = mode
    task = None
    try:
        async with sockets(hub) as (http, url):
            a, b = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
            task = asyncio.create_task(hub._collect())
            await until(a, lambda msg: msg["t"] == "pool" and msg["open"])
            for text in ("守住门口", "再行动一次"):
                await a.send_json({"t": "act", "text": text})
                await until(a, lambda msg, text=text: msg["t"] == "pool" and text in msg["mine"]["now"] + msg["mine"]["next"])
            assert hub.pool == [("zhouheng", "守住门口")] and hub.early == [("zhouheng", "再行动一次")]
            assert hub._waiting_for() == ["lin"] and not task.done()
            await b.send_json({"t": "done"})
            await asyncio.wait_for(task, 1)
            assert hub.pool == [("zhouheng", "守住门口")]
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_an_offline_defender_is_asked_before_any_attack_dice_are_thrown(fake):
    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    game, cast, _events = await opening(module, fake, "fight", hostile=True)
    person = game.state.encounter.get("zhouheng")
    person.surprised, person.ask = False, "always"
    game.state.round_plan["encounter"] = [game.state.encounter.id, "fight", 1]
    action = Action(kind="other", summary="保持戒备",
                    declared={"plan": {"action": {"do": "defend", "summary": "保持戒备"}}, "beat": True})
    game.state.turn += 1
    game.state.pending_turn = dict(input=action.summary, action=action.model_dump(mode="json"),
                                   ruling=Ruling(needs_roll=False).model_dump(mode="json"), by_hand=False)
    hub = Hub(EXAMPLE, state=game.state, cast=cast, rules=game.rules)
    hub.started.set()
    task = None
    fake.queue(10, 90)
    try:
        async with sockets(hub) as (http, url):
            spectator = await http.ws_connect(url)
            await spectator.send_json({"t": "hello", "name": "watching"})
            await until(spectator, lambda msg: msg["t"] == "welcome")
            task = asyncio.create_task(hub._run("/retry", "zhouheng", ai=False))
            await asyncio.sleep(0.05)
            assert not task.done() and hub.asking.get("zhouheng")
            assert hub.state.encounter.pending.kind == "reaction" and list(fake.values) == [10, 90]
            a = await sit(http, url, "zhouheng")
            offer = (await until(a, lambda msg: msg["t"] == "offer"))["offer"]
            assert offer["kind"] == "reaction" and offer["closes_in"] is None
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()
        await game.close()


async def test_an_offline_human_keeps_an_encounter_retry_until_reconnecting(round_hub, monkeypatch):
    hub = round_hub
    text = "守住门口"
    hub.state.round_plan["encounter"] = ["sudden", "fight", 1]
    hub.state.pending_turn = {"input": text}
    calls = []

    async def run(value, seat, **kwargs):
        calls.append(value)
        if len(calls) == 1:
            raise RuntimeError("离线回合尚未完成")
        hub.state.pending_turn.clear()

    monkeypatch.setattr(hub, "_run", run)
    task = None
    try:
        async with sockets(hub) as (http, url):
            spectator = await http.ws_connect(url)
            await spectator.send_json({"t": "hello", "name": "watching"})
            await until(spectator, lambda msg: msg["t"] == "welcome")
            task = asyncio.create_task(hub._settle_round([("zhouheng", text)]))
            await asyncio.sleep(0.03)
            assert not task.done() and hub.retrying == "zhouheng" and calls == [text]
            a = await sit(http, url, "zhouheng")
            await until(a, lambda msg: msg["t"] == "error" and msg.get("retry"))
            await a.send_json({"t": "act", "text": "/retry"})
            await asyncio.wait_for(task, 1)
            assert calls == [text, "/retry"] and hub.retrying is None
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
