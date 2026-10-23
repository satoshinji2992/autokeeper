"""Ordinary local discoveries and SAN costs use the existing addressed delivery boundary."""

from __future__ import annotations

import json

from autokeeper.engine import presentation
from autokeeper.engine.game import Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Clue, Handout
from autokeeper.world import knowledge
from autokeeper.world.record import PLAYERS

from .test_net_private import private_hub as _private_hub

private_hub = _private_hub


def add_local_material(hub):
    hub.m.events, hub.m.endings, hub.m.showcases = [], [], []
    hub.m.handouts.append(Handout(id="qa_local_paper", title="现场材料", text="QA_LOCAL_PAPER_93624"))
    hub.m.clues.append(Clue(id="qa_local_clue", title="现场记录", text="QA_LOCAL_CLUE_93624",
                           where=hub.state.location, handout="qa_local_paper", sanity="0/0"))


async def test_local_original_and_sanity_are_delivered_only_to_actual_observers(private_hub, fake):
    hub, messages = private_hub
    add_local_material(hub)
    hub.state.apart["chen"] = "tower"
    hub.state.record.entities["chen"].at = "tower"
    fake.queue(10)
    await hub.game._gain(["qa_local_clue"], Turn(), None)
    hub.game.world.sync()
    fact = hub.state.record.facts["clue:qa_local_clue"]
    assert {"zhouheng", "lin"} <= set(fact.known_by)
    assert not knowledge.sees(fact, "chen") and not knowledge.sees(fact, PLAYERS)
    assert "qa_local_clue" in hub.state.clues and "qa_local_paper" not in hub.state.handouts
    assert set(presentation.papers(hub.state, "zhouheng")) == {"qa_local_paper"}
    assert set(presentation.papers(hub.state, "lin")) == {"qa_local_paper"}
    assert not presentation.papers(hub.state, "chen")
    assert "QA_LOCAL_PAPER_93624" not in json.dumps(hub._book("chen"), ensure_ascii=False)
    assert "QA_LOCAL_PAPER_93624" not in json.dumps(hub._welcome("c"), ensure_ascii=False)
    delivered = [(cid, msg) for cid, msg in messages if msg.get("ev", {}).get("type") in ("presentation", "sanity")]
    assert {cid for cid, _ in delivered} == {"a", "b"}
    assert all(set(msg["ev"]["audience"]) == {"zhouheng", "lin"} for _, msg in delivered)
    assert all(entry.audience == ["zhouheng", "lin"] for entry in hub.state.log if entry.kind in ("clue", "sanity"))
    saved = GameState.model_validate_json(hub.state.model_dump_json())
    assert not presentation.papers(saved, "chen") and presentation.papers(saved, "zhouheng")
    assert not fake.values


async def test_full_party_original_keeps_the_shared_handout_path(private_hub, fake):
    hub, _messages = private_hub
    add_local_material(hub)
    fake.queue(10)
    await hub.game._gain(["qa_local_clue"], Turn(), None)
    assert "qa_local_paper" in hub.state.handouts and not hub.state.presentations
    assert "QA_LOCAL_PAPER_93624" in json.dumps(hub._book("chen"), ensure_ascii=False)


async def test_sanity_scope_follows_the_affected_investigator_and_preserves_own_fate(private_hub, fake):
    hub, messages = private_hub
    hub.state.apart["chen"] = "tower"
    hub.state.record.entities["chen"].at = "tower"
    fake.queue(10)
    notice = hub.game._lose_sanity(hub.state.find("chen"), "0/0", Turn())
    await hub.game.emit(notice)
    assert notice["audience"] == ["chen"] and hub.state.log[-1].audience == ["chen"]
    assert [cid for cid, msg in messages if msg.get("ev", {}).get("type") == "sanity"] == ["c"]
