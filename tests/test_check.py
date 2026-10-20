"""A telling is read against the record before anyone sees it (engine/check.py, Game._verified)."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import check
from autokeeper.engine.check import Problem
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_engine import FakeCast, make


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


class Checking(FakeCast):
    """A keeper whose tellings come from a list, and a reader whose findings do too."""

    checking = True

    def __init__(self, tellings, findings):
        super().__init__({})
        self.tellings, self.findings = list(tellings), list(findings)
        self.read: list[str] = []
        self.retold: list[str] = []
        self.amended: list[tuple[str, str]] = []

    async def keeper(self, state, report, on_delta=None, lore=""):
        self.reports.append(report)
        return self.tellings.pop(0)

    async def retell(self, state, report, told, *, extra="", on_delta=None):
        self.retold.append(extra)
        return self.tellings.pop(0)

    async def check(self, state, material):
        self.read.append(material)
        return self.findings.pop(0) if self.findings else []

    def amend(self, state, report, told, text):
        self.amended.append((told, text))


WAIT = Turn(action=Action(kind="wait", summary="周衡在广场上站了一会儿"), ruling=Ruling(needs_roll=False, minutes=10))


async def test_a_telling_that_holds_is_shown_as_written(module, fake):
    game, _, events = make(module, fake, {}, location="square")
    game.cast = cast = Checking(["午后的广场空荡荡的。"], [[]])
    assert await game.narrate(WAIT) == "午后的广场空荡荡的。"
    assert "【记录：这一轮结束时】" in cast.read[0] and "午后的广场" in cast.read[0]
    assert not cast.retold
    deltas = [e["text"] for e in events if e["type"] == "keeper_delta"]
    assert deltas == ["午后的广场空荡荡的。"]  # shown once it was read


async def test_someone_from_next_door_written_in_comes_over(module, fake):
    game, _, _ = make(module, fake, {}, location="square")  # the warden is up in the tower, next door
    game.cast = cast = Checking(["守钟人从钟楼下来，站到你身边。"], [[Problem(kind="在场", claim="守钟人从钟楼下来，站到你身边", record="老守钟人在钟楼")]])
    assert await game.narrate(WAIT) == "守钟人从钟楼下来，站到你身边。"
    assert game.world.place("warden") == "square" and not cast.retold


async def test_what_does_not_hold_is_told_again_knowing_why(module, fake):
    game, _, _ = make(module, fake, {}, location="square")
    lamp = Problem(kind="物品", claim="你掏出手电照了照", record="随身物品里没有手电")
    game.cast = cast = Checking(["你掏出手电照了照告示栏。", "你凑近告示栏看了看。"], [[lamp], []])
    assert await game.narrate(WAIT) == "你凑近告示栏看了看。"
    assert "你掏出手电照了照" in cast.retold[0] and "随身物品里没有手电" in cast.retold[0]
    assert game.state.log[-1].text == "你凑近告示栏看了看。"


async def test_a_sentence_that_still_does_not_hold_is_left_out(module, fake):
    game, _, _ = make(module, fake, {}, location="square")
    lamp = Problem(kind="物品", claim="你掏出手电照了照", record="随身物品里没有手电")
    telling = "风从广场上刮过。你掏出手电照了照告示栏。纸角翻了起来。"
    game.cast = cast = Checking([telling, telling], [[lamp], [lamp]])
    shown = await game.narrate(WAIT)
    assert shown == "风从广场上刮过。纸角翻了起来。"
    assert cast.amended == [(telling, shown)]  # the keeper remembers what was shown


def test_the_record_lists_the_people_near_and_what_they_carry(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    text = check.account(game.m, game.state, game.state.turn, {}, None, ["窗台上有一线细土"])
    assert "此刻就在调查员身边的人：老守钟人" in text and "窗台上有一线细土" in text
    assert "（照写不算凭空补的物证）" in text
