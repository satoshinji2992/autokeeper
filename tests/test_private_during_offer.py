"""Real sockets can clarify an offer without answering it or changing its settlement."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_multiplayer_transport import sit, sockets
from .test_net import TableCast, until
from .test_presentation import private_paper


@pytest.fixture
async def mail_hub(fake):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    other = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    st = Game.new(m, [lead, other], human=[lead.id, other.id])
    st.pending_turn = {"input": "原行动", "request": {"actor": lead.id}}
    hub = Hub(EXAMPLE, state=st, cast=TableCast(), rules=Rules(dice=fake))
    hub.started.set()
    yield hub
    if hub.private_task is not None:
        hub.private_task.cancel()
        await asyncio.gather(hub.private_task, return_exceptions=True)
    await hub.game.close()


async def drain_to_welcome(ws):
    await ws.send_json({"t": "hello", "name": "audit"})
    seen = []
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), 1)
        seen.append(msg)
        if msg["t"] == "welcome":
            return seen


@pytest.mark.parametrize("kind", ["roll", "reaction", "push"])
async def test_private_input_and_reply_arrive_while_the_original_choice_remains_open(mail_hub, monkeypatch, kind):
    hub = mail_hub
    calls = []

    async def reply(state, who, text, *, context):
        calls.append(context)
        return {"text": "这是提问时的规则说明，原行动尚未结算。"}

    monkeypatch.setattr(hub.cast, "private_keeper", reply, raising=False)
    async with sockets(hub) as (http, url):
        a, b = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
        spectator = await http.ws_connect(url)
        await drain_to_welcome(spectator)
        original = copy.deepcopy(hub.state.pending_turn)
        record, when = hub.state.record.model_dump_json(), hub.state.minutes
        task = asyncio.create_task(hub._ask("zhouheng", hub.sits["zhouheng"], {"kind": kind, "skill": "妙手", "target": 1}, timed=False))
        try:
            offer = (await until(a, lambda m: m["t"] == "offer"))["offer"]
            await a.send_json({"t": "kp_private", "request": "clarify", "text": "这个数值是否正确？"})
            await until(a, lambda m: m.get("ev", {}).get("type") == "private_input")
            aside = await until(a, lambda m: m.get("ev", {}).get("type") == "aside")
            assert aside["ev"]["audience"] == ["zhouheng"] and "提问时" in aside["ev"]["text"]
            assert not task.done() and hub.asking["zhouheng"] == offer["respond_id"]
            assert hub.state.pending_turn == original and hub.state.minutes == when
            assert hub.state.record.model_dump_json() == record
            assert len(calls) == 1 and '"target": 1' in calls[0]
            for ws in (b, spectator):
                assert "这个数值是否正确" not in json.dumps(await drain_to_welcome(ws), ensure_ascii=False)
            await a.send_json({"t": "respond", "id": offer["respond_id"], "choice": "roll"})
            assert await asyncio.wait_for(task, 1) == "roll"
            assert "zhouheng" not in hub.offers
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_a_player_may_answer_before_the_private_model_returns_without_a_late_state_overwrite(mail_hub, monkeypatch):
    hub = mail_hub
    began, release = asyncio.Event(), asyncio.Event()
    contexts = []
    hub.state.add("keeper", "本人提问前已知的现场。", audience=["zhouheng"])
    hub.state.add("keeper", "同伴专属秘密。", audience=["lin"])

    async def reply(state, who, text, *, context):
        contexts.append(context)
        began.set()
        await release.wait()
        return {"text": "你提问时还在等待原检定。"}

    monkeypatch.setattr(hub.cast, "private_keeper", reply, raising=False)
    async with sockets(hub) as (http, url):
        a = await sit(http, url, "zhouheng")
        task = asyncio.create_task(hub._ask("zhouheng", hub.sits["zhouheng"], {"kind": "roll", "target": 1}, timed=False))
        offer = (await until(a, lambda m: m["t"] == "offer"))["offer"]
        await a.send_json({"t": "kp_private", "request": "slow", "text": "请说明规则。"})
        await until(a, lambda m: m.get("ev", {}).get("type") == "private_input")
        await asyncio.wait_for(began.wait(), 1)
        await a.send_json({"t": "kp_private", "request": "slow", "text": "请说明规则。"})
        await drain_to_welcome(a)
        await a.send_json({"t": "respond", "id": offer["respond_id"], "choice": "roll"})
        assert await asyncio.wait_for(task, 1) == "roll"
        hub.state.pending_turn = {"input": "下一行动", "settled": True}
        hub.state.minutes += 17
        hub.state.active = 1
        hub.state.add("keeper", "问题之后才发生的事。", audience=["zhouheng"])
        pending, minutes = copy.deepcopy(hub.state.pending_turn), hub.state.minutes
        release.set()
        await until(a, lambda m: m.get("ev", {}).get("type") == "aside")
        assert hub.state.pending_turn == pending and hub.state.minutes == minutes and hub.state.active == 1
        assert "本人提问前已知" in contexts[0]
        assert "同伴专属秘密" not in contexts[0] and "问题之后才发生" not in contexts[0]
        assert len(contexts) == 1
        assert hub.state.private_messages["zhouheng:slow"]["delivered"]


async def test_saved_private_reply_recovers_after_disconnect_without_recalling_the_model_or_losing_the_action(mail_hub, monkeypatch, fake):
    hub = mail_hub
    calls = []

    async def reply(state, who, text, *, context):
        calls.append(context)
        return {"text": "已保存的本人规则答复。"}

    async def disconnected(ev):
        if ev["type"] == "aside":
            raise OSError("connection stopped at reply delivery")
        await hub._event(ev)

    monkeypatch.setattr(hub.cast, "private_keeper", reply, raising=False)
    hub.game.receive_private_message("zhouheng", "检定规则？", request="saved", offer={"kind": "roll", "target": 1})
    hub.game.emit = disconnected
    with pytest.raises(OSError):
        await hub.game.private_message("zhouheng", "检定规则？", request="saved")
    restored = GameState.model_validate_json(hub.state.model_dump_json())
    resumed = Hub(EXAMPLE, state=restored, cast=hub.cast, rules=Rules(dice=fake))
    resumed.started.set()
    async with sockets(resumed) as (http, url):
        a = await sit(http, url, "zhouheng")
        task = asyncio.create_task(resumed._ask("zhouheng", resumed.sits["zhouheng"], {"kind": "roll", "target": 1}, timed=False))
        offer = (await until(a, lambda m: m["t"] == "offer"))["offer"]
        await until(a, lambda m: m.get("ev", {}).get("type") == "aside")
        assert len(calls) == 1 and resumed.state.pending_turn == {"input": "原行动", "request": {"actor": "zhouheng"}}
        assert not task.done() and resumed.asking["zhouheng"] == offer["respond_id"]
        await a.send_json({"t": "kp_private", "request": "saved", "text": "检定规则？"})
        await drain_to_welcome(a)
        assert len(calls) == 1 and len([e for e in restored.log if e.kind == "keeper"]) == 1
        await a.send_json({"t": "respond", "id": offer["respond_id"], "choice": "roll"})
        assert await asyncio.wait_for(task, 1) == "roll"
    if resumed.private_task is not None:
        await resumed.private_task
    await resumed.game.close()


async def test_private_attachments_are_preserved_until_the_action_finishes(mail_hub, monkeypatch, tmp_path):
    hub = mail_hub
    paper = private_paper(hub, tmp_path)
    calls = []

    async def reply(state, who, text, *, context):
        calls.append(text)
        return {"text": "这是给你的私人附件。", "media": [paper]}

    monkeypatch.setattr(hub.cast, "private_keeper", reply, raising=False)
    async with sockets(hub) as (http, url):
        a = await sit(http, url, "zhouheng")
        task = asyncio.create_task(hub._ask("zhouheng", hub.sits["zhouheng"], {"kind": "roll"}, timed=False))
        offer = (await until(a, lambda m: m["t"] == "offer"))["offer"]
        await a.send_json({"t": "kp_private", "request": "with-paper", "text": "请私下给附件。"})
        await until(a, lambda m: "附件已保存" in m.get("ev", {}).get("text", ""))
        receipt = hub.state.private_messages["zhouheng:with-paper"]
        assert receipt["reply_shown"] and receipt["media"] and not receipt.get("delivered")
        assert not hub.state.presentations and not task.done()
        await a.send_json({"t": "respond", "id": offer["respond_id"], "choice": "roll"})
        assert await asyncio.wait_for(task, 1) == "roll"
        hub.state.pending_turn.clear()  # the original action has now finished
        hub._start_privates()
        shown = await until(a, lambda m: m.get("ev", {}).get("type") == "presentation")
        assert shown["ev"]["audience"] == ["zhouheng"]
        assert len(calls) == 1 and receipt["delivered"]
