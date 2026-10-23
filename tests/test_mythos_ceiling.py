"""Knowledge lowers SAN's ceiling; that adjustment is not another horror exposure."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from autokeeper.engine.game import Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Clue
from autokeeper.rules.coc import Investigator
from autokeeper.world import knowledge

from .test_net_private import private_hub as _private_hub

private_hub = _private_hub


@pytest.mark.parametrize(("san", "mythos", "points", "expected"), [
    (98, 0, 4, (95, 4)),
    (40, 10, 3, (40, 13)),
    (1, 98, 1, (0, 99)),
    (1, 98, 10, (0, 99)),
    (0, 99, 0, (0, 99)),
    (60, 0, 0, (60, 0)),
])
def test_mythos_growth_limits_san_without_daily_loss_or_an_extra_bout(inv, san, mythos, points, expected):
    inv.san, inv.san_lost_today = san, 3
    inv.skills["克苏鲁神话"] = mythos
    before_day = inv.san_start_of_day
    inv.learn_mythos(points)
    assert (inv.san, inv.value("克苏鲁神话")) == expected
    assert inv.san_lost_today == 3 and inv.san_start_of_day == before_day
    assert not {"临时疯狂", "不定性疯狂"} & set(inv.conditions)
    assert ("永久疯狂" in inv.conditions) is (expected[0] == 0)
    assert inv.can_act is (expected[0] != 0)
    assert inv.madness.temporary_since is None and inv.madness.temporary_until is None


@pytest.mark.parametrize("mythos", [-1, 100])
def test_formal_investigators_reject_out_of_range_mythos_and_runtime_maximum_never_goes_negative(inv, mythos):
    data = inv.model_dump()
    with pytest.raises(ValidationError, match="0 到 99"):
        Investigator.model_validate(data | {"skills": {"克苏鲁神话": mythos}})
    inv.skills["克苏鲁神话"] = 100  # Invalid unchecked mutation cannot turn a later reward negative.
    assert inv.san_max == 0


def test_a_negative_mythos_award_is_not_applied(inv):
    before = inv.model_dump()
    with pytest.raises(ValueError, match="不能为负数"):
        inv.learn_mythos(-1)
    assert inv.model_dump() == before
    with pytest.raises(ValidationError):
        Clue(id="invalid", title="非法增长", text="", where="tower", mythos=-1)


async def test_private_then_public_exposure_limits_each_recipient_and_notifies_only_that_person(private_hub, fake):
    hub, messages = private_hub
    hub.m.clues.append(Clue(id="qa_mythos", title="禁忌知识", text="PRIVATE_MYTHOS_746", where=hub.state.location, mythos=4))
    hub.m.events, hub.m.endings, hub.m.showcases = [], [], []
    for inv in hub.state.investigators:
        inv.san, inv.san_start_of_day = 98, 98
        inv.skills["克苏鲁神话"] = 0
    first = Turn()
    await hub.game._gain(["qa_mythos"], first, None, heard="warden", scope={"audience": ["zhouheng"]})
    assert [i.san for i in hub.state.investigators] == [95, 98, 98]
    assert not knowledge.clue_known(hub.state, "qa_mythos", "lin")
    receipts = [(cid, msg["ev"]) for cid, msg in messages
                if msg.get("ev", {}).get("type") == "status" and "理智上限" in msg["ev"].get("text", "")]
    assert len(receipts) == 1 and receipts[0][0] == "a" and receipts[0][1]["audience"] == ["zhouheng"]
    assert "98 → 95" in receipts[0][1]["text"] and "不计入本日理智损失" in receipts[0][1]["text"]
    assert not first.sanity and not fake.values and not fake.tables
    for cid in ("b", "c", "watch"):
        welcome = hub._welcome(cid)
        assert "PRIVATE_MYTHOS_746" not in json.dumps(welcome, ensure_ascii=False)
        assert "理智上限 95" not in json.dumps(welcome, ensure_ascii=False)
    second = Turn()
    await hub.game._gain(["qa_mythos"], second, None, heard="warden", scope={"audience": None})
    assert all(i.san == 95 and i.value("克苏鲁神话") == 4 and i.san_lost_today == 0 for i in hub.state.investigators)
    assert not second.sanity and not fake.values and not fake.tables
    notices = [(cid, msg["ev"]) for cid, msg in messages
               if msg.get("ev", {}).get("type") == "status" and "理智上限" in msg["ev"].get("text", "")]
    assert [(cid, ev["audience"]) for cid, ev in notices] == [("a", ["zhouheng"]), ("b", ["lin"]), ("c", ["chen"])]
    assert not [e for e in hub.state.log if e.kind == "sanity"]
    # Knowledge/cost receipts survive a save independently of any recent log text.
    restored = GameState.model_validate_json(hub.state.model_dump_json())
    assert restored.clue_settled["qa_mythos"] == ["zhouheng", "lin", "chen"]


async def test_original_horror_cost_and_ceiling_adjustment_are_reported_as_distinct_personal_effects(private_hub, fake):
    hub, messages = private_hub
    hub.m.clues.append(Clue(id="qa_both", title="禁忌知识", text="", where=hub.state.location, mythos=10, sanity="1/1"))
    inv = hub.state.investigator
    inv.san, inv.san_start_of_day = 98, 98
    fake.queue(20)
    turn = Turn()
    await hub.game._gain(["qa_both"], turn, None, scope={"audience": [inv.id]})
    assert inv.san == 89 and inv.san_lost_today == 1 and not inv.conditions and not fake.values
    assert len(turn.sanity) == 1 and turn.sanity[0][1].loss == 1
    personal = [(cid, msg["ev"]) for cid, msg in messages
                if msg.get("ev", {}).get("type") in ("sanity", "status") and cid == "a"]
    assert any(ev["type"] == "sanity" and ev["after"] == 97 for _, ev in personal)
    assert any(ev["type"] == "status" and "97 → 89" in ev["text"] for _, ev in personal)
    assert all(ev.get("audience") == [inv.id] for _, ev in personal)
