"""After the telling, the record catches up with it, or the next telling corrects it (Game._reconcile)."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Reconcile, Slip
from autokeeper.engine.game import Told, Turn
from autokeeper.engine.state import LogEntry
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context

from .test_engine import FakeCast, make


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


class Checking(FakeCast):
    def __init__(self, answer):
        super().__init__({})
        self.answer, self.checked = answer, []

    async def reconcile(self, state, record, telling):
        self.checked.append((record, telling))
        return self.answer


def told(game, text: str) -> Told:
    entry = LogEntry(turn=game.state.turn, time=game.state.clock, kind="keeper", text=text)
    return Told(report="", lore="", entry=entry, sky=("", 0))


async def test_someone_written_in_from_next_door_comes_along(module, fake):
    game, _, _ = make(module, fake, {}, location="square")  # the warden is up in the tower, next door
    game.cast = Checking(Reconcile(slips=[Slip(kind="在场", who="warden", text="守钟人跟在你身后")], now="守钟人正催你快走"))
    await game._reconcile(told(game, "你走在广场上，守钟人跟在你身后。"))
    assert game.world.place("warden") == "square" and not game._late.notes
    assert game.state.ongoing == "守钟人正催你快走"
    assert "【上一段叙述停在】守钟人正催你快走" in game._report(Turn())
    assert "在场的人" in game.cast.checked[0][0] and "守钟人跟在你身后" in game.cast.checked[0][1]


async def test_someone_who_cannot_be_here_is_corrected_in_the_next_telling(module, fake):
    game, _, _ = make(module, fake, {}, location="office")  # the tower is two places away
    game.cast = Checking(Reconcile(slips=[Slip(kind="在场", who="warden", text="守钟人站在门口")]))
    await game._reconcile(told(game, "守钟人站在报社门口。"))
    assert game.world.place("warden") == "tower"
    assert any("老守钟人" in n and "不在场" in n for n in game._late.notes)


async def test_evidence_the_telling_made_up_becomes_something_they_saw(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    game.cast = Checking(Reconcile(slips=[Slip(kind="物证", text="楼梯上一串湿脚印")]))
    await game._reconcile(told(game, "楼梯上留着一串湿脚印。"))
    assert "在钟楼看到：楼梯上一串湿脚印" in context.keeper_view(game.world)


async def test_only_a_screen_turns_the_check_on(module, fake):
    game, _, _ = make(module, fake, {}, location="square")
    game.cast = checking = Checking(Reconcile())
    game.told = told(game, "广场上很安静。")
    await game.remember()
    assert game._world_task is None and not checking.checked
    game.reconcile = True
    await game.remember()
    await game._world_task
    assert len(checking.checked) == 1
