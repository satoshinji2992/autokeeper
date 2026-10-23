"""Less common rule entry points keep personal mental receipts off the shared channel."""

from __future__ import annotations

import json

import pytest

from autokeeper import cards
from autokeeper.agents.cast import Sketch
from autokeeper.engine import audience
from autokeeper.engine.game import Turn
from autokeeper.module.schema import SketchPrompt

from .test_net_private import private_hub as _private_hub

private_hub = _private_hub


@pytest.mark.parametrize(("command", "kind"), [(".st", "sheet"), (".ti", "madness"), (".sc 1/1d6", "refused")])
async def test_character_sheet_madness_table_and_sanity_command_reply_stay_with_requester(private_hub, command, kind):
    hub, messages = private_hub
    inv = hub.state.investigator
    inv.san, inv.skills["克苏鲁神话"] = 47, 13
    reply = await hub.game.command(command)
    assert reply.kind == kind and inv.san == 47
    results = [(cid, msg["ev"]) for cid, msg in messages if msg.get("ev", {}).get("type") == "command"]
    assert len(results) == 1 and results[0][0] == "a" and results[0][1]["audience"] == [inv.id]
    if command == ".st":
        assert "理智 47" in results[0][1]["text"] and "克苏鲁神话13" in results[0][1]["text"]


async def test_plain_dice_command_still_reaches_the_table(private_hub, fake):
    hub, messages = private_hub
    fake.queue_totals(4)
    await hub.game.command(".r 1D6")
    assert {cid for cid, msg in messages if msg.get("ev", {}).get("type") == "command"} == {"a", "b", "c", "watch"}


async def test_sketch_horror_settles_actual_observers_but_each_person_gets_only_their_san_receipt(private_hub, fake):
    hub, messages = private_hub
    game, st = hub.game, hub.state
    hub.m.events, hub.m.endings, hub.m.showcases = [], [], []
    hub.m.sketches = [SketchPrompt(id="qa_sketch", ask="画出所见图案", target="交错螺旋", sanity="1/1")]
    st.apart["chen"] = "tower"
    st.record.entities["chen"].at = "tower"
    before = {inv.id: inv.san for inv in st.investigators}

    async def sketch(*_args):
        return Sketch(description="三道交错螺旋", resemblance=0.9)

    hub.cast.sketch = sketch
    fake.queue(20, 20)
    assert await game.sketch(b"offline-png", "qa_sketch") == "叙事"
    assert not fake.values
    for inv in st.investigators:
        assert inv.san == before[inv.id] - int(inv.id != "chen")
    receipts = [(cid, msg["ev"]) for cid, msg in messages if msg.get("ev", {}).get("type") == "sanity"]
    assert [(cid, ev["audience"]) for cid, ev in receipts] == [("a", ["zhouheng"]), ("b", ["lin"])]
    assert not [e for e in audience.log(st, "chen") if e.kind == "sanity"]
    assert len([e for e in audience.log(st, "lin") if e.kind == "sanity"]) == 1
    assert any(msg.get("ev", {}).get("type") == "keeper_end" for _, msg in messages)  # Scene telling still occurs.


async def test_mishap_card_and_san_are_personal_while_visible_injury_stays_public(private_hub, fake, monkeypatch):
    hub, messages = private_hub
    card = cards.MishapCard(id="qa_mishap", deck="mishap", name="独有梦魇", text="PRIVATE_CARD_7381", hp="1", sanity="1/1")
    monkeypatch.setattr(cards, "find", lambda _ident: card)
    inv, turn, journal = hub.state.investigator, Turn(), {}
    before = (inv.hp, inv.san)
    fake.queue(20)
    with hub.game._journal(journal, lambda _turn: None):
        assert await hub.game._mishap(card.id, turn, "mishap") == card.name
        assert await hub.game._mishap(card.id, turn, "mishap") == card.name
    assert (inv.hp, inv.san) == (before[0] - 1, before[1] - 1) and not fake.values
    private = [(cid, msg["ev"]) for cid, msg in messages if msg.get("ev", {}).get("type") in ("sanity", "card")]
    assert len(private) == 2 and all(cid == "a" and ev["audience"] == [inv.id] for cid, ev in private)
    assert {cid for cid, msg in messages if msg.get("ev", {}).get("type") == "damage"} == {"a", "b", "c", "watch"}
    assert "独有梦魇" in json.dumps(hub._welcome("a")["log"], ensure_ascii=False)
    for client in ("b", "c", "watch"):
        restored = hub._welcome(client)
        assert "独有梦魇" not in json.dumps(restored["log"], ensure_ascii=False)
        assert not restored["state"]["drawn"]
    assert "PRIVATE_CARD_7381" in hub.game._report(turn)  # The KP retains full authority and context.
