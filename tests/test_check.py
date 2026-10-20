"""A telling is shown as the keeper writes it; the replay eval reads it against the record
(engine/check.py, eval/session.py)."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import check
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_engine import make


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


WAIT = Turn(action=Action(kind="wait", summary="周衡在广场上站了一会儿"), ruling=Ruling(needs_roll=False, minutes=10))


async def test_a_telling_is_streamed_and_kept_as_written(module, fake):
    game, cast, events = make(module, fake, {}, location="square")
    assert await game.narrate(WAIT) == "叙事"
    assert [e["text"] for e in events if e["type"] == "keeper_delta"] == ["叙事"]  # as it is written, not after a reading
    assert game.state.log[-1].text == "叙事" and cast.lores[-1].startswith("\n\n【幕后")  # the backstage rode along


async def test_what_an_npc_could_not_do_reaches_the_keeper_as_a_note(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    cast.unsettled = {"warden": ["lamp：引用、参与者或物件持有状态不符，不能宣称已经完成"]}
    note = game._unsettled("warden")
    assert note and "【未落实】老守钟人" in note[0] and "lamp" in note[0] and "不删改他说过的话" in note[0]
    assert game._unsettled("warden") == [] and cast.unsettled == {}  # said once, for this turn


def test_the_record_lists_the_people_near_and_what_they_carry(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    text = check.account(game.m, game.state, game.state.turn, {}, None, ["窗台上有一线细土"])
    assert "此刻就在调查员身边的人：老守钟人" in text and "窗台上有一线细土" in text
    assert "（照写不算凭空补的物证）" in text
