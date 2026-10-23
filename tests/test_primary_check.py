"""Social performance uses the action's check, never another check in the same turn."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Check as Gate
from autokeeper.module.schema import Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, make

SOCIAL = Action(kind="talk", summary="请守钟人陪同去广场", npc="warden", approach="魅惑")


@pytest.fixture
def module() -> Module:
    module = Module.load(EXAMPLE)
    module.events, module.endings, module.agendas, module.foreshadow = [], [], [], []
    module.flags["social_done"] = "守钟人已同意这次请求"
    return module


def followup(module: Module, *, skill="恐吓") -> None:
    module.events.append(Event(id="other_request", title="另一项请求", when="flag:social_done", where="tower",
                               check=Gate(skill=skill), text="同一现场另一项交谈的独立检定"))


def restored(turn: Turn) -> Turn:
    return TURN_RECORD.validate_json(TURN_RECORD.dump_json(turn))


def assert_success(game: Game, turn: Turn) -> None:
    text = game._social_result(turn, "warden")
    assert "本次交涉成功" in text and "本次交涉未达成" not in text
    report = game._report(turn)
    assert text not in game._npc_message(turn, "warden") and text in report
    if turn.ruling and turn.ruling.why:
        assert "【主行动的公开裁定目标】" + turn.ruling.why in report
        assert "【该目标的最终主检定】" + turn.checks[turn.primary].describe() in report
    assert not game._social_result(turn, "preacher")


async def test_primary_check_is_between_arrival_and_followup_event_checks_of_the_same_skill(module, fake):
    module.events.append(Event(id="on_arrival", title="楼梯口另一场交谈", when="visit:tower", where="tower",
                               check=Gate(skill="说服"), text="先问过楼梯口的人"))
    followup(module, skill="说服")
    game, _, _ = make(module, fake.queue(99, 1, 99), {}, location="square")
    action = SOCIAL.model_copy(update={"destination": "tower"})
    ruling = Ruling(needs_roll=True, skill="说服", minutes=0, set_flags=["social_done"],
                    why="确认守钟人是否愿意陪同去广场")
    turn = restored(await game.resolve(action, ruling))
    assert [(c.name, c.value) for c in turn.checks] == [("说服", 99), ("说服", 1), ("说服", 99)]
    assert turn.primary == 1 and turn.checks[turn.primary].passed
    assert "social_done" in game.state.flags
    assert_success(game, turn)
    assert not fake.values
    await game.close()


@pytest.mark.parametrize("mode", ["push", "luck"])
@pytest.mark.parametrize("interrupted", [False, True])
async def test_primary_push_or_luck_survives_a_followup_event_and_saved_resume(module, fake, mode, interrupted):
    followup(module)
    game, _, _ = make(module, fake.queue(18, *([1] if mode == "push" else []), 99), {}, location="tower")
    game.state.pending_turn = {"input": "请您陪我去广场"}
    ruling = Ruling(needs_roll=True, skill="恐吓", minutes=0, set_flags=["social_done"],
                    why="确认守钟人是否愿意陪同去广场")
    original = restored(await game.resolve(SOCIAL, ruling))
    assert original.primary == 0 and original.push
    before = game.state.investigator.luck
    choice = {"choice": "push", "how": "把风险和报酬具体说清，再恳请他陪同"} if mode == "push" else mode
    if interrupted:
        snapshots: list[str] = []
        game.checkpoint = lambda: snapshots.append(game.state.model_dump_json())

        async def disconnected(event):
            if event["type"] == ("dice" if mode == "push" else "luck"):
                raise ConnectionError("the final throw was saved before delivery failed")

        game.emit = disconnected
        with pytest.raises(ConnectionError):
            await game.push(choice)
        state = GameState.model_validate_json(snapshots[-1])
        assert state.pending_turn["push_resolution"]["turn"]["primary"] == 0
        await game.close()
        game = Game(module, state, FakeCast({}), Rules(dice=fake))
    extra = restored(await game.push(choice))
    assert extra.primary == 0 and len(extra.checks) == 2 and not extra.checks[-1].passed
    original.absorb(extra)
    turn = restored(original)
    assert turn.primary == (1 if mode == "push" else 0)
    assert len(turn.checks) == (3 if mode == "push" else 2)
    assert turn.checks[turn.primary].passed and not turn.checks[-1].passed
    assert game.state.investigator.luck == before - (3 if mode == "luck" else 0)
    assert_success(game, turn)
    assert not fake.values
    await game.close()


async def test_luck_replaces_the_primary_even_when_an_event_has_the_identical_throw(module, fake):
    module.events.append(Event(id="unrelated", title="同时另一场交谈", when=module.start_time, where="tower",
                               check=Gate(skill="恐吓"), text="另一项独立请求"))
    followup(module)
    game, _, _ = make(module, fake.queue(18, 18, 99), {}, location="tower")
    turn = await game.resolve(SOCIAL, Ruling(needs_roll=True, skill="恐吓", minutes=0, set_flags=["social_done"]))
    assert turn.primary == 0 and len(turn.checks) == 2 and turn.checks[0].roll == turn.checks[1].roll
    turn.absorb(await game.push("luck"))
    turn = restored(turn)
    assert turn.primary == 0 and len(turn.checks) == 3
    assert turn.checks[0].luck_spent == 3 and turn.checks[0].passed
    assert not turn.checks[1].luck_spent and not turn.checks[1].passed
    assert_success(game, turn)
    assert not fake.values
    await game.close()


async def test_a_dropped_check_keeps_its_primary_when_a_failure_event_passes(module, fake):
    from autokeeper.agents.cast import Failure

    followup(module)
    game, _, _ = make(module, fake.queue(18, 1), {}, location="tower")
    turn = await game.resolve(SOCIAL, Ruling(needs_roll=True, skill="恐吓", minutes=0,
                                           on_fail=Failure(flags=["social_done"])))
    extra = await game.push("drop")
    assert extra.primary is None and extra.checks[0].passed
    turn.absorb(extra)
    turn = restored(turn)
    assert turn.primary == 0 and not turn.checks[turn.primary].passed and turn.checks[-1].passed
    assert "本次交涉未达成" in game._social_result(turn, "warden")
    await game.close()


async def test_a_real_repeat_uses_earlier_even_when_a_new_event_rolls_the_same_skill(module, fake):
    followup(module)
    game, _, _ = make(module, fake.queue(18, 1), {}, location="tower")
    await game.resolve(SOCIAL, Ruling(needs_roll=True, skill="恐吓", minutes=0))
    await game.push("drop")
    turn = restored(await game.resolve(SOCIAL, Ruling(needs_roll=False, repeat=True, skill="恐吓", minutes=0,
                                                     now=["social_done"])))
    assert turn.primary is None and turn.earlier is not None and not turn.earlier.passed
    assert len(turn.checks) == 1 and turn.checks[0].passed
    assert "本次请求仍未达成" in game._social_result(turn, "warden")
    assert not fake.values
    await game.close()


async def test_an_event_alone_does_not_become_a_social_check_for_the_action(module, fake):
    followup(module)
    game, _, _ = make(module, fake.queue(1), {}, location="tower")
    turn = restored(await game.resolve(SOCIAL, Ruling(needs_roll=False, skill="恐吓", minutes=0,
                                                     now=["social_done"])))
    assert turn.primary is None and len(turn.checks) == 1 and turn.checks[0].passed
    assert not game._social_result(turn, "warden")
    await game.close()


async def test_nonsocial_goal_reaches_the_keeper_with_its_primary_result_without_telling_bystanders(module, fake):
    followup(module, skill="侦查")
    game, _, _ = make(module, fake.queue(1, 99), {}, location="tower")
    action = Action(kind="investigate", summary="借遮挡读清票据日期", target="票据")
    goal = "确认能否在旁人未看到票据内容时读清日期"
    ruling = Ruling(needs_roll=True, skill="侦查", why=goal, reason="折痕令票面字迹模糊", set_flags=["social_done"])
    turn = restored(await game.resolve(action, ruling))
    assert turn.primary == 0 and turn.checks[0].passed and not turn.checks[1].passed
    report = game._report(turn)
    assert "【主行动的公开裁定目标】" + goal in report
    assert "【该目标的最终主检定】" + turn.checks[0].describe() in report
    assert "【该目标的最终主检定】" + turn.checks[1].describe() not in report
    from autokeeper.agents.cast import Initiative

    assert goal not in game._joining(turn, Initiative(npc="preacher", does="继续收好讲义"))
    await game.close()


async def test_an_event_check_is_not_reported_as_the_unrolled_goals_result(module, fake):
    followup(module, skill="侦查")
    game, _, _ = make(module, fake.queue(1), {}, location="tower")
    turn = restored(await game.resolve(Action(kind="daily", summary="抄写已经看清的日期"),
                                       Ruling(needs_roll=False, why="抄下已经读到的日期", now=["social_done"])))
    assert turn.primary is None and len(turn.checks) == 1
    report = game._report(turn)
    assert "【主行动的公开裁定目标】抄下已经读到的日期" in report
    assert "【该目标的最终主检定】" not in report
    await game.close()
