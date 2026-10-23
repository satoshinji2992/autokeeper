"""Failed checks retain their costs without presenting pending scene consequences as history."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Failure, Ruling
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Clue, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, make

ACTION = Action(kind="investigate", summary="查看柜门的锁舌", target="柜门")
NARRATIVE = "店员叫来巡夜人，要求调查员到值班室补记陈述，同行的人留在原处。"
RISK = "巡夜人来把调查员带到值班室，同行的人留在原处。"


@pytest.fixture
def module() -> Module:
    module = Module.load(EXAMPLE)
    module.events, module.endings, module.agendas, module.foreshadow = [], [], [], []
    module.flags["latch_bent"] = "柜门的锁舌被弄弯"
    module.clues.append(Clue(id="c_dropped_paper", title="掉出的工单", text="柜缝掉出一张工单。", where="tower"))
    return module


def ruling(failure: Failure) -> Ruling:
    return Ruling(needs_roll=True, skill="侦查", minutes=0, on_fail=failure, on_push_fail=failure)


@pytest.mark.parametrize("mode", ["drop", "push"])
async def test_pending_narrative_is_kept_for_the_keeper_without_a_completed_public_outcome(module, fake, mode):
    failure = Failure(text=NARRATIVE, risk=RISK)
    game, cast, events = make(module, fake.queue(99, *([99] if mode == "push" else [])), {}, location="tower")
    await game.resolve(ACTION, ruling(failure))
    choice = {"choice": "push", "how": "把锁舌边缘再看仔细一些"} if mode == "push" else "drop"
    extra = await game.push(choice)

    outcome = [e for e in events if e["type"] == "outcome"][-1]["lines"]
    if mode == "push":
        assert outcome[0] == "孤注一掷也没成" and "检定失败" in outcome
    assert all(RISK not in line and NARRATIVE not in line for line in outcome)
    assert game.state.location == "tower" and not game.state.apart
    assert game.state.checks[-1].passed is False
    await game.narrate(extra)
    report = cast.reports[-1]
    assert "【须在本轮场面演出落实的失败后果】" + NARRATIVE in report
    assert "这是须承担的失败代价，不因尚未演完而取消" in report
    assert "未演出的回应、行程或交付不代表已经完成" in report
    assert "实际发生：" + NARRATIVE not in report
    await game.close()


@pytest.mark.parametrize("mode", ["drop", "push"])
async def test_structured_failure_costs_are_settled_and_reported_separately_from_the_scene(module, fake, mode):
    failure = Failure(text=NARRATIVE, risk=RISK, damage="1D3", sanity="1/1", minutes=7,
                      flags=["latch_bent"], clues=["c_dropped_paper"])
    values = [99, *([99] if mode == "push" else []), 1]
    game, cast, events = make(module, fake.queue(*values).queue_totals(2), {}, location="tower")
    before, hp, san = game.state.minutes, game.state.investigator.hp, game.state.investigator.san
    await game.resolve(ACTION, ruling(failure))
    choice = {"choice": "push", "how": "抵着柜框再看一次"} if mode == "push" else "drop"
    extra = await game.push(choice)

    assert game.state.investigator.hp == hp - 2 and game.state.investigator.san == san - 1
    assert game.state.minutes == before + 7 + (10 if mode == "push" else 0)
    assert "latch_bent" in game.state.flags and "c_dropped_paper" in game.state.clues
    assert len([e for e in events if e["type"] == "damage"]) == 1
    assert len([e for e in events if e["type"] == "sanity"]) == 1
    assert len([e for e in events if e["type"] == "clue" and e["id"] == "c_dropped_paper"]) == 1
    await game.narrate(extra)
    report = cast.reports[-1]
    settled = next(n for n in extra.notes if "【已结算的失败结果】" in n)
    assert "耽搁了 7 分钟" in settled and "已设置标记：latch_bent" in settled
    assert "已取得线索：掉出的工单" in settled
    assert "这些结果已入账，只描写，不再次结算" in report
    assert "【须在本轮场面演出落实的失败后果】" + NARRATIVE in report
    assert all(RISK not in line for e in events if e["type"] == "outcome" for line in e["lines"])
    assert not fake.values and not fake.totals
    await game.close()


async def test_failed_damage_validation_is_not_presented_as_settled_injury(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    hp = game.state.investigator.hp
    turn = Turn()
    await game._failed(Failure(damage="not a dice expression", text=NARRATIVE), [], turn, ACTION)

    assert game.state.investigator.hp == hp
    report = game._report(turn)
    assert "伤害式无法使用" in report and "【已结算的失败结果】" not in report
    assert "【须在本轮场面演出落实的失败后果】" + NARRATIVE in report
    await game.close()


async def test_failure_time_note_uses_the_actual_capped_increment(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    before = game.state.minutes
    turn = Turn()
    await game._failed(Failure().model_copy(update={"minutes": 999}), [], turn, ACTION)

    assert game.state.minutes == before + 240
    assert any("耽搁了 240 分钟" in note for note in turn.notes)
    assert not any("999" in note for note in turn.notes)
    await game.close()


async def test_failure_time_note_keeps_personal_duration_when_the_global_increment_is_smaller(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    who = game.state.investigator.id
    companion = game.state.investigator.model_copy(deep=True, update={"id": "colleague", "name": "同伴"})
    game.state.investigators.append(companion)
    game.state.round_spent = 12
    game.state.round_elapsed = {who: 0, companion.id: 12}
    before = game.state.minutes
    turn = Turn()
    await game._failed(Failure(minutes=15), [], turn, ACTION)

    assert game.state.minutes == before + 3
    assert game.state.round_elapsed[who] == 15
    assert any("耽搁了 15 分钟" in note for note in turn.notes)
    assert turn.interval["start"] == before - 12 and turn.interval["end"] == before + 3
    await game.close()


async def test_zero_sanity_loss_is_reported_as_the_actual_check_without_inventing_a_shock(module, fake):
    game, _, events = make(module, fake.queue(1), {}, location="tower")
    san = game.state.investigator.san
    turn = Turn()
    await game._failed(Failure(sanity="0/1"), [], turn, ACTION)

    [notice] = [e for e in events if e["type"] == "sanity"]
    assert notice["loss"] == 0 and game.state.investigator.san == san
    assert notice["text"] in game._report(turn)
    assert "理智受到冲击" not in game._report(turn)
    await game.close()


@pytest.mark.parametrize("interrupt_at", ["damage", "outcome"])
async def test_failed_push_recovery_keeps_settled_costs_once_and_pending_narrative_intact(module, fake, interrupt_at):
    failure = Failure(text=NARRATIVE, risk=RISK, damage="1D3", sanity="1/1", minutes=7,
                      flags=["latch_bent"], clues=["c_dropped_paper"])
    game, _, _ = make(module, fake.queue(99, 99, 1).queue_totals(2), {}, location="tower")
    game.state.pending_turn = {"input": ACTION.summary}
    await game.resolve(ACTION, ruling(failure))
    before, hp, san = game.state.minutes, game.state.investigator.hp, game.state.investigator.san
    snapshots = []
    game.checkpoint = lambda: snapshots.append(game.state.model_dump_json())

    async def disconnect(event):
        if event["type"] == interrupt_at:
            raise ConnectionError("delivery disconnected after the preceding state was saved")

    game.emit = disconnect
    choice = {"choice": "push", "how": "抵着柜框仔细检查锁舌"}
    with pytest.raises(ConnectionError):
        await game.push(choice)
    saved = GameState.model_validate_json(snapshots[-1])
    await game.close()
    resumed = Game(module, saved, FakeCast({}), Rules(dice=fake))
    extra = await resumed.push(choice)

    assert resumed.state.investigator.hp == hp - 2 and resumed.state.investigator.san == san - 1
    assert resumed.state.minutes == before + 17
    assert resumed.state.flags.count("latch_bent") == 1 and resumed.state.clues.count("c_dropped_paper") == 1
    assert len([e for e in resumed.state.log if e.kind == "dice"]) == 2
    assert len([e for e in resumed.state.log if e.kind == "damage"]) == 1
    assert len([e for e in resumed.state.log if e.kind == "sanity"]) == 1
    assert len([note for note in extra.notes if "【须在本轮场面演出落实的失败后果】" in note]) == 1
    assert "耽搁了 7 分钟" in "\n".join(extra.notes) and NARRATIVE in resumed._report(extra)
    assert resumed.state.location == "tower" and not resumed.state.apart
    assert not fake.values and not fake.totals
    final = resumed.state.model_dump_json()
    await resumed.push(choice)
    assert resumed.state.model_dump_json() == final
    await resumed.close()
