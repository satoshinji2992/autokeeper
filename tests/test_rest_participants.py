"""Safe-rest confirmation is limited to each investigator's executed rest interval."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Narration, Ruling
from autokeeper.engine.game import TURN_RECORD, Game
from autokeeper.engine.state import GameState, parse_time
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import MadnessState, Rules
from autokeeper.world import scene

from .test_engine import FakeCast


class RestKeeper(FakeCast):
    def __init__(self, targets: list[str]):
        super().__init__({})
        self.targets = targets

    async def keeper(self, state, report, on_delta=None, lore="", *, check=None, fix=""):
        self.reports.append(report)
        raw = {"rested": self.targets,
               "here": [e.id for e in state.record.entities.values() if e.kind == "npc" and e.at == state.location]}
        if check:
            assert check(raw), fix
        return Narration(text="休息的人安稳睡到天亮，守夜的人仍清醒着。", scene=raw)


def made(fake, *, remote: bool = False):
    module = Module.load(EXAMPLE)
    module.events, module.endings, module.agendas, module.foreshadow = [], [], [], []
    first = module.pregens[0].model_copy(deep=True)
    second = first.model_copy(deep=True, update={"id": "watcher", "name": "守夜同伴"})
    state = Game.new(module, [first, second], human=[first.id, second.id])
    state.location, state.minutes = "square", parse_time("D1 23:00")
    if remote:
        state.apart[second.id] = "tower"
    for inv in state.investigators:
        inv.conditions = ["临时疯狂"]
        inv.madness = MadnessState(temporary_since=state.minutes - 30, temporary_until=state.minutes + 570)
    cast, events = RestKeeper([]), []

    async def emit(event):
        events.append(event)

    return Game(module, state, cast, Rules(dice=fake), emit=emit), cast, events


async def execute(game: Game, index: int, *, rest: bool, minutes: int = 480, mishap: str = ""):
    state = game.state
    state.pending_turn.clear()  # the preceding completed turn has handed its report and rest receipt to held
    state.active = index
    game._focus()
    state.turn += 1
    action = Action(kind="wait", summary="本人实际睡眠" if rest else "本人清醒守夜")
    state.pending_turn = {"input": action.summary}
    state.add("player", action.summary, who=state.investigator.name)
    turn = await game.resolve(action, Ruling(needs_roll=False, rest=rest, minutes=minutes, mishap=mishap))
    return TURN_RECORD.validate_json(TURN_RECORD.dump_json(turn))


@pytest.mark.parametrize("remote", [False, True])
async def test_one_sleeping_actor_cannot_authorize_a_nonparticipating_companion(remote, fake):
    game, cast, _ = made(fake, remote=remote)
    first, second = game.state.investigators
    turn = await execute(game, 0, rest=True)
    ctx = game._scene_context(turn)
    invalid = scene.plan(game.world, {"rested": [first.id, second.id]}, ctx)

    assert invalid.problems and not invalid.rested and set(ctx.rests) == {first.id}
    assert second.id not in game._scene_brief(ctx).split("本段可确认休息的调查员：", 1)[1].split("。", 1)[0]
    cast.targets = [first.id]
    await game.narrate(turn)
    assert first.conditions == [] and second.conditions == ["临时疯狂"]
    await game.close()


async def test_long_awake_wait_advances_time_without_authorizing_sleep_recovery(fake):
    game, _, _ = made(fake)
    before = game.state.minutes
    turn = await execute(game, 0, rest=False)
    ctx = game._scene_context(turn)

    assert game.state.minutes == before + 480 and turn.rest is None and not ctx.resting
    assert scene.plan(game.world, {"rested": [game.state.investigator.id]}, ctx).problems
    assert all(inv.conditions == ["临时疯狂"] for inv in game.state.investigators)
    await game.close()


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("second_rests", [False, True])
async def test_last_combined_telling_uses_each_executed_rest_even_with_zero_global_increment(fake, remote, second_rests):
    game, cast, _ = made(fake, remote=remote)
    first, second = game.state.investigators
    start = game.state.minutes
    game.begin_round()
    game.holding = True
    first_turn = await execute(game, 0, rest=True)
    await game.narrate(first_turn)
    assert first.conditions == ["临时疯狂"] and game.state.held[0]["rest"] == first_turn.rest
    second_turn = await execute(game, 1, rest=second_rests)
    assert game.state.minutes == start + 480  # the second person's own 480 minutes overlap the first's
    if second_rests:
        assert second_turn.rest == {"who": second.id, "start": start, "end": start + 480, "interrupted": False}
    ctx = game._scene_context(second_turn)
    assert ctx.elapsed == 0
    expected = {first.id, second.id} if second_rests else {first.id}
    assert set(ctx.rests) == expected
    if not second_rests:
        assert scene.plan(game.world, {"rested": [second.id]}, ctx).problems
    cast.targets = list(expected)
    game.holding = False
    await game.narrate(second_turn)

    assert first.conditions == []
    assert second.conditions == ([] if second_rests else ["临时疯狂"])
    assert not game.state.held
    await game.close()


async def test_individual_rest_starts_protect_new_trauma_without_using_the_last_players_log_time(fake):
    game, cast, _ = made(fake)
    first, second = game.state.investigators
    start = game.state.minutes
    game.begin_round()
    game.holding = True
    turn = await execute(game, 0, rest=True)
    await game.narrate(turn)
    # This trauma is during the first person's sleep but before the second's sleep.
    for inv in (first, second):
        inv.madness = MadnessState(temporary_since=start + 30, temporary_until=start + 630)
    watching = await execute(game, 1, rest=False, minutes=60)
    await game.narrate(watching)
    sleeping = await execute(game, 1, rest=True)
    assert sleeping.rest["start"] == start + 60 and sleeping.rest["end"] == start + 540
    cast.targets = [first.id, second.id]
    game.holding = False
    await game.narrate(sleeping)

    assert first.conditions == ["临时疯狂"] and second.conditions == []
    await game.close()


async def test_interrupted_rest_is_preserved_as_ineligible_in_its_execution_receipt(fake):
    game, _, _ = made(fake)
    turn = await execute(game, 0, rest=True, mishap="interrupted_rest")

    assert turn.rest["interrupted"] is True
    ctx = game._scene_context(turn)
    assert not ctx.rests and scene.plan(game.world, {"rested": [game.state.investigator.id]}, ctx).problems
    await game.close()


async def test_saved_timing_receipt_does_not_repeat_parallel_effort_or_shift_the_rest_start(fake):
    game, _, _ = made(fake)
    state, start = game.state, game.state.minutes
    game.begin_round()
    state.turn += 1
    action = Action(kind="wait", summary="本人实际睡眠")
    ruling = Ruling(needs_roll=False, rest=True, minutes=480)
    state.pending_turn = {"input": action.summary}
    state.add("player", action.summary, who=state.investigator.name)
    frozen = []

    def checkpoint():
        step = state.pending_turn.get("resolution", {}).get("steps", {}).get("action-end", {})
        if step.get("done") and not state.pending_turn["resolution"]["steps"].get("elapsed", {}).get("done"):
            frozen.append(state.model_dump_json())
            raise ConnectionError("timing saved before elapsed")

    game.checkpoint = checkpoint
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    restored = GameState.model_validate_json(frozen[-1])
    resumed = Game(game.m.model_copy(deep=True), restored, RestKeeper([]), Rules(dice=fake))
    turn = await resumed.resolve(action, ruling)

    assert restored.minutes == start + 480 and restored.round_elapsed[restored.investigator.id] == 480
    assert turn.rest == {"who": restored.investigator.id, "start": start, "end": start + 480, "interrupted": False}
    await game.close()
    await resumed.close()


async def test_combined_rest_notification_resume_keeps_original_personal_recipients(fake):
    game, cast, _ = made(fake, remote=True)
    start = game.state.minutes
    game.begin_round()
    game.holding = True
    await game.narrate(await execute(game, 0, rest=True))
    turn = await execute(game, 1, rest=True)
    ids = [inv.id for inv in game.state.investigators]
    cast.targets = ids
    game.holding = False

    async def disconnect(event):
        if event["type"] == "status" and "安全过夜休息" in event.get("text", ""):
            raise ConnectionError("recovery delivery interrupted")

    game.emit = disconnect
    with pytest.raises(ConnectionError):
        await game.narrate(turn)
    saved = GameState.model_validate_json(game.state.model_dump_json())
    saved_turn = TURN_RECORD.validate_json(TURN_RECORD.dump_json(turn))
    delivered = []

    async def emit(event):
        delivered.append(event)

    resumed = Game(game.m.model_copy(deep=True), saved, RestKeeper(ids), Rules(dice=fake), emit=emit)
    await resumed.narrate(saved_turn)
    notices = [e for e in delivered if e["type"] == "status" and "安全过夜休息" in e.get("text", "")]

    assert {tuple(e["audience"]) for e in notices} == {(ident,) for ident in ids}
    assert len(notices) == 2 and all(inv.conditions == [] for inv in saved.investigators)
    assert len([e for e in saved.log if "安全过夜休息" in e.text]) == 2
    assert saved.minutes == start + 480 and not saved.held
    await game.close()
    await resumed.close()
