"""The keeper's craft as the prompts carry it (skills/keeper-craft, agents/cast, the chapter-10 review
of 2026-10-21): what each new line asks of the models, and the context that lets them do it. Scripted
agents and FakeDice, no model calls."""

from __future__ import annotations

import pytest

from autokeeper import skills
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Creature, Event, Module, Showcase
from autokeeper.paths import EXAMPLE

from .test_engine import make

COIL = Creature(id="coil", name="钟藤", called="缠在齿轮上的东西", description="缠在齿轮背后、还在动的东西", sanity="0/1D3")


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.creatures.append(COIL.model_copy())
    return m


# -- horror shown by what is sensed (guide items 21–22; Keeper Rulebook pp. 175–177, 179) ---------------
def test_the_keeper_shows_a_horror_by_the_senses_and_leaves_naming_it_to_the_players():
    craft = skills.wrap("keeper-craft")
    assert "先写调查员感到的：局部、具体的形状、声音、气味、温度和留下的痕迹" in craft
    assert "不说出它的名字和来历，不解释，也不替玩家合理化，由玩家自己辨认" in craft
    assert "调查员认出它以前，用系统给的叫法或看得见的样子称呼它" in craft


async def test_a_horror_an_event_shows_comes_to_the_keeper_with_what_the_players_would_call_it(module, fake):
    game, *_ = make(module, fake, {}, location="tower")
    seen = Event(id="e_coil", title="齿轮后面", when="visit:tower", text="钟藤醒了", read_aloud="齿轮背后有什么在动。", sights=["coil"])
    report = game._report(Turn(events=[seen]))
    assert "【称呼】钟藤（玩家所见：缠在齿轮上的东西）" in report and "不说出名字和来历" in report
    game.state.identified.append("coil")  # once they know it, its name is theirs too
    assert "【称呼】" not in game._report(Turn(events=[seen]))
    await game.close()


async def test_a_creatures_big_picture_comes_when_the_telling_shows_it_by_what_it_is_called(module, fake):
    module.showcases = [Showcase(id="s_coil", kind="怪物", when="creature:coil", title="齿轮背后", prompt="齿轮背后缠着的东西")]
    game, *_ = make(module, fake, {}, location="tower")
    await game.showcases.named("你举起提灯，齿轮之间那团缠在齿轮上的东西慢慢收紧了。")
    assert "s_coil" in game.state.shown
    await game.close()
