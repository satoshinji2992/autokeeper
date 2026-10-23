"""Mail owns its delivery callback while the ordinary turn graph binds its stream emitter."""

from __future__ import annotations

import asyncio
import copy

from autokeeper.agents.cast import Action, Ruling

from .test_multiplayer_transport import sit, sockets
from .test_net import until
from .test_presentation import private_paper
from .test_private_during_offer import drain_to_welcome
from .test_private_during_offer import mail_hub as _mail_hub

mail_hub = _mail_hub


async def test_private_reply_and_attachment_do_not_borrow_the_interleaved_graph_emitter(
    mail_hub, monkeypatch, tmp_path,
):
    hub = mail_hub
    question = "请说明原选择，并把这封信只给我。"
    paper = private_paper(hub, tmp_path)
    began, release = asyncio.Event(), asyncio.Event()
    calls, failures = [], []
    action = Action(kind="investigate", summary="查看门外的动静", target="门外")
    hub.state.pending_turn.update(by_hand=True, action=action.model_dump(mode="json"),
                                  ruling=Ruling(needs_roll=True, skill="侦查").model_dump(mode="json"))

    async def reply(state, who, text, *, context):
        calls.append(context)
        began.set()
        await release.wait()
        return {"text": "原选择仍未回答；这是提问时记录的说明。", "media": [paper]}

    monkeypatch.setattr(hub.cast, "private_keeper", reply, raising=False)
    monkeypatch.setattr(hub, "_failed", lambda what, exc: failures.append((what, str(exc))))
    main = None
    try:
        async with sockets(hub) as (http, url):
            a, b = await sit(http, url, "zhouheng"), await sit(http, url, "lin")
            spectator = await http.ws_connect(url)
            await drain_to_welcome(spectator)
            await a.send_json({"t": "kp_private", "request": "interleaved", "text": question})
            await until(a, lambda message: message.get("ev", {}).get("type") == "private_input")
            await asyncio.wait_for(began.wait(), 1)
            context = calls[0]
            main = asyncio.create_task(hub._run("/retry", "zhouheng", ai=False))
            offer = (await until(a, lambda message: message["t"] == "offer"))["offer"]
            graph_emit = hub.game.emit  # Real graph._bind has now replaced the mutable turn callback.
            pending, record = copy.deepcopy(hub.state.pending_turn), hub.state.record.model_dump_json()
            body = (hub.state.investigator.hp, hub.state.investigator.luck, hub.state.minutes)
            release.set()
            await asyncio.wait_for(hub.private_task, 1)
            assert not failures
            assert hub.game.emit is graph_emit and not main.done()
            assert hub.asking["zhouheng"] == offer["respond_id"]
            aside = await until(a, lambda message: message.get("ev", {}).get("type") == "aside")
            assert aside["ev"]["audience"] == ["zhouheng"]
            assert hub.state.pending_turn == pending and hub.state.record.model_dump_json() == record
            receipt = hub.state.private_messages["zhouheng:interleaved"]
            assert receipt["context"] == context and receipt["reply_shown"] and receipt["media_deferred"]

            # Reuse the cached reply and deliver its attachment outside the runnable context,
            # while that same graph still waits for the original offer.
            await hub.game.private_message("zhouheng", question, request="interleaved")
            shown = await until(a, lambda message: message.get("ev", {}).get("type") == "presentation")
            assert shown["ev"]["audience"] == ["zhouheng"] and receipt["delivered"]
            assert len(calls) == 1 and hub.game.emit is graph_emit and not main.done()
            assert hub.asking["zhouheng"] == offer["respond_id"] and hub.state.pending_turn == pending
            assert (hub.state.investigator.hp, hub.state.investigator.luck, hub.state.minutes) == body
            assert hub.state.record.model_dump_json() == record

            # A new letter's immediate acknowledgement also runs after graph._bind,
            # outside any runnable context, and must keep the same open offer.
            second_question = "原选择还在等我确认吗？"
            await a.send_json({"t": "kp_private", "request": "already-bound", "text": second_question})
            await until(a, lambda message: message.get("ev", {}).get("type") == "private_input"
                        and message["ev"]["text"] == second_question)
            await until(a, lambda message: message.get("ev", {}).get("type") == "aside")
            await asyncio.wait_for(hub.private_task, 1)
            assert not failures and len(calls) == 2 and hub.game.emit is graph_emit
            assert hub.state.private_messages["zhouheng:already-bound"]["waiting"]
            assert hub.asking["zhouheng"] == offer["respond_id"] and hub.state.pending_turn == pending
            for ws in (b, spectator):
                messages = await drain_to_welcome(ws)
                assert not any(message.get("ev", {}).get("type") in ("private_input", "aside", "presentation")
                               for message in messages)
    finally:
        if main is not None:
            main.cancel()
            await asyncio.gather(main, return_exceptions=True)
