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


# -- chapter 10, 「主持游戏」 (Keeper Rulebook pp. 153–188; docs/rules-reference.md) ----------------------
def test_the_chapter_ten_lines_reach_the_prompts_that_obey_them():
    from autokeeper.agents.cast import ACTION_RULES, ARBITER_SYSTEM, INTENT_SYSTEM, PLAN_SYSTEM, PUSH_SYSTEM

    craft, core = skills.wrap("keeper-craft"), skills.wrap("coc7-core")
    assert "检定失败时写调查员没有找到，不写成这里什么都没有" in craft  # p. 170
    assert "是死是活，除非已经检查过或一望可知，不替玩家下结论" in craft  # pp. 170, 175
    assert "理智检定失败时写一下不由自主的反应" in craft and "之后怎么做仍由玩家决定" in craft  # pp. 129–130, 178
    assert "玩家替别的调查员说的动作、台词或决定，不记成那位调查员做了" in ACTION_RULES and ACTION_RULES in INTENT_SYSTEM  # p. 167
    assert "帮忙只做他力所能及的一步，不替调查员查明真相、做关键决定" in PLAN_SYSTEM  # pp. 156, 182
    assert "可以再提高一到两级，在 reason 里写明依据" in ARBITER_SYSTEM  # p. 165
    assert "只掷一次，关多就提高难度；已经为这件事掷过的，后面同样的关不再另掷" in ARBITER_SYSTEM  # p. 163
    assert "再失败的后果可以是一段幻觉" in PUSH_SYSTEM  # p. 165
    assert "掷幸运" in core and "只掷一次，用调查员较高的那一项" in core  # pp. 173, 172
    for text in (craft, core):
        assert "规则书第" not in text  # the pages stay in the code's comments and docs/rules-reference.md


def test_no_runtime_prompt_names_a_page():
    """Page numbers belong in the code's comments and docs/rules-reference.md, not in what a model reads
    every turn (the review of 2026-10-22). The rulebook passages a lookup quotes carry theirs (rules_lookup)."""
    import re

    from autokeeper.agents import cast, director, encounter

    prompts = {"ARBITER_SYSTEM": cast.ARBITER_SYSTEM, "PUSH_SYSTEM": cast.PUSH_SYSTEM, "KEEPER_SYSTEM": cast.KEEPER_SYSTEM,
               "PLAN_SYSTEM": cast.PLAN_SYSTEM, "AUTHOR_SYSTEM": cast.AUTHOR_SYSTEM, "DIRECTOR_SYSTEM": director.DIRECTOR_SYSTEM,
               "ENCOUNTER_PLAN_SYSTEM": encounter.ENCOUNTER_PLAN_SYSTEM, "ENCOUNTER_START_SYSTEM": encounter.ENCOUNTER_START_SYSTEM,
               "OTHER_DAMAGE": encounter.OTHER_DAMAGE, "ARBITER_ENCOUNTER": encounter.ARBITER_ENCOUNTER}
    for name, text in prompts.items():
        assert not re.search(r"第 ?\d+(?:[–-]\d+)? ?页|p\. ?\d", text), name


async def test_the_push_review_sees_whether_the_investigator_is_mad(module, monkeypatch):
    from autokeeper import config
    from autokeeper.agents.cast import Cast, PushRuling
    from autokeeper.engine.game import Game
    from autokeeper.harness import Reply

    cast = Cast(config.Config(), module)
    seen: list[str] = []

    async def send(key, session, state, prompt, **kw):
        seen.append(prompt)
        return Reply(text="", data=PushRuling(achievable=True, minutes=10).model_dump())

    monkeypatch.setattr(cast, "_send", send)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    pending = {"skill": "侦查", "target": 50, "difficulty": "常规", "value": 77, "action": "周衡翻找齿轮间"}
    await cast.repush(state, pending, "把每个齿轮都拆下来看")
    assert "状态：" not in seen[0]
    state.investigators[0].conditions.append("临时疯狂")
    await cast.repush(state, pending, "把每个齿轮都拆下来看")
    assert "状态：临时疯狂" in seen[1]


def test_the_arbiter_leaves_the_idea_roll_to_the_engine():
    """The Idea roll is the engine's (engine/idea.py, /灵感): coc7-core, read by the arbiter every turn,
    says so in two lines instead of the whole procedure, and asked for it in words the arbiter rolls
    nothing and gives nothing."""
    body = skills.body("coc7-core")
    section = body.split("## 灵感检定", 1)[1].split("\n## ", 1)[0].strip()
    assert "/灵感" in section and "裁定员不裁定灵感检定" in section and "不掷骰、不给线索" in section
    assert len(section.splitlines()) == 2 and "智力最高" not in section and "千钧一发" not in body
    assert "智力（灵感）" not in body  # no INT roll ruled for a flash of insight either
