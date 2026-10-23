"""Human replies cannot silently select or pay for a different failed-check option."""

from __future__ import annotations

import asyncio
import copy

import pytest

from autokeeper.agents.cast import Action, Failure, Ruling
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_multiplayer_transport import sit, sockets
from .test_net import TableCast, until


@pytest.fixture
async def reply_hub(fake):
    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    lead = module.pregens[0].model_copy(deep=True)
    lead.luck = 40
    state = Game.new(module, [lead], human=[lead.id])
    state.location = "tower"
    action = Action(kind="investigate", summary="检查锁孔", target="门锁")
    ruling = Ruling(needs_roll=True, skill="侦查", on_push_fail=Failure(risk="锁孔可能卡死"))
    state.pending_turn = dict(input=action.summary, by_hand=True, action=action.model_dump(mode="json"),
                              ruling=ruling.model_dump(mode="json"))
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    hub.started.set()
    yield hub
    await hub.game.close()


async def failed_offer(hub, ws, fake):
    fake.queue(70, 10)
    task = asyncio.create_task(hub._run("/retry", "zhouheng", ai=False))
    roll = (await until(ws, lambda msg: msg["t"] == "offer"))["offer"]
    await ws.send_json({"t": "respond", "id": roll["respond_id"], "choice": "roll"})
    offer = (await until(ws, lambda msg: msg["t"] == "offer"))["offer"]
    assert offer["kind"] == "push" and offer["can_push"] and offer["luck"] == 15
    return task, offer


@pytest.mark.parametrize("nested", [False, True])
async def test_invalid_replies_keep_the_offer_and_a_real_push_rerolls_without_spending_luck(reply_hub, fake, nested):
    hub, task = reply_hub, None
    try:
        async with sockets(hub) as (http, url):
            ws = await sit(http, url, "zhouheng")
            task, offer = await failed_offer(hub, ws, fake)
            original = copy.deepcopy(hub.state.pending_turn)
            for answer in ({}, {"choice": ""}, {"choice": "unknown"}, {"choice": "push"},
                           {"choice": "push", "how": "  "}, {"choice": {"choice": "push", "how": ["乱填"]}}):
                await ws.send_json({"t": "respond", "id": offer["respond_id"], **answer})
                await until(ws, lambda msg: msg["t"] == "notice")
                assert hub.asking["zhouheng"] == offer["respond_id"] and hub.offers["zhouheng"] == offer
                assert not task.done() and hub.state.investigator.luck == 40 and list(fake.values) == [10]
                assert hub.state.pending_turn == original and len(hub.state.checks) == 1
            how = "把球棒靠墙，双手对准螺纹重新检查锁孔"
            reply = {"choice": {"choice": "push", "how": how}} if nested else {"choice": "push", "how": how}
            await ws.send_json({"t": "respond", "id": offer["respond_id"], **reply})
            await asyncio.wait_for(task, 2)
            assert hub.state.investigator.luck == 40 and not fake.values
            assert hub.state.checks[-1].text.startswith("孤注一掷 ") and hub.state.checks[-1].passed
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("choice", ["luck", "drop"])
async def test_only_an_explicit_luck_reply_spends_luck_and_drop_accepts_failure(reply_hub, fake, choice):
    hub, task = reply_hub, None
    try:
        async with sockets(hub) as (http, url):
            ws = await sit(http, url, "zhouheng")
            task, offer = await failed_offer(hub, ws, fake)
            await ws.send_json({"t": "respond", "id": offer["respond_id"], "choice": choice})
            await asyncio.wait_for(task, 2)
            assert hub.state.investigator.luck == (25 if choice == "luck" else 40)
            assert list(fake.values) == [10] and not hub.state.checks[-1].text.startswith("孤注一掷 ")
            assert hub.state.checks[-1].passed == (choice == "luck")
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_unavailable_luck_and_an_invalid_value_bypassing_transport_never_choose_ai_fallback(reply_hub, fake):
    hub, task = reply_hub, None
    try:
        async with sockets(hub) as (http, url):
            ws = await sit(http, url, "zhouheng")
            task, offer = await failed_offer(hub, ws, fake)
            hub.state.investigator.luck = 3
            await ws.send_json({"t": "respond", "id": offer["respond_id"], "choice": "luck"})
            await until(ws, lambda msg: msg["t"] == "notice")
            assert hub.asking["zhouheng"] == offer["respond_id"] and not task.done()
            hub.state.investigator.luck = 40
            hub.choices["zhouheng"].put_nowait("push")  # another caller still cannot turn an invalid value into luck
            repeated = (await until(ws, lambda msg: msg["t"] == "offer"))["offer"]
            assert repeated["kind"] == "push" and hub.state.investigator.luck == 40 and list(fake.values) == [10]
            await ws.send_json({"t": "respond", "id": repeated["respond_id"], "choice": "drop"})
            await asyncio.wait_for(task, 2)
            assert hub.state.investigator.luck == 40 and list(fake.values) == [10]
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("kind,offer,reply,expected", [
    ("reaction", {"options": [{"id": "dodge"}]}, {"choice": "dodge", "ask": "always"},
     {"choice": "dodge", "ask": "always"}),
    ("reaction", {"options": [{"id": "dodge"}]}, {"choice": {"choice": "", "text": "用椅背隔开对手", "ask": "major"}},
     {"choice": "", "text": "用椅背隔开对手", "ask": "major"}),
    ("redeclare", {"options": [{"id": "stand"}]}, {"choice": "stand"}, "stand"),
    ("redeclare", {"options": [{"id": "keep"}, {"id": "pass"}]}, {"choice": "keep"}, "keep"),
    ("redeclare", {"options": [{"id": "keep"}, {"id": "pass"}]}, {"choice": "pass"}, "pass"),
    ("redeclare", {"options": [{"id": "keep"}, {"id": "pass"}]}, {"choice": "withdraw"}, "withdraw"),
    ("redeclare", {"options": [{"id": "keep"}, {"id": "pass"}]}, {"text": "改为守住出口"},
     {"choice": "", "text": "改为守住出口"}),
])
async def test_legal_reactions_and_redeclarations_keep_their_existing_contract(reply_hub, kind, offer, reply, expected):
    hub, task = reply_hub, None
    try:
        async with sockets(hub) as (http, url):
            ws = await sit(http, url, "zhouheng")
            task = asyncio.create_task(hub._ask("zhouheng", hub.sits["zhouheng"], {"kind": kind, **offer}, timed=False))
            shown = (await until(ws, lambda msg: msg["t"] == "offer"))["offer"]
            await ws.send_json({"t": "respond", "id": shown["respond_id"], **reply})
            assert await asyncio.wait_for(task, 1) == expected
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_unoffered_push_and_luck_cannot_settle_a_human_offer(reply_hub):
    hub, task = reply_hub, None
    try:
        async with sockets(hub) as (http, url):
            ws = await sit(http, url, "zhouheng")
            task = asyncio.create_task(hub._ask("zhouheng", hub.sits["zhouheng"], {
                "kind": "push", "can_push": False, "luck": None}, timed=False))
            offer = (await until(ws, lambda msg: msg["t"] == "offer"))["offer"]
            for reply in ({"choice": "push", "how": "再检查一次"}, {"choice": "luck"}):
                await ws.send_json({"t": "respond", "id": offer["respond_id"], **reply})
                await until(ws, lambda msg: msg["t"] == "notice")
                assert hub.asking["zhouheng"] == offer["respond_id"] and not task.done()
            await ws.send_json({"t": "respond", "id": offer["respond_id"], "choice": "drop"})
            assert await asyncio.wait_for(task, 1) == "drop"
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
