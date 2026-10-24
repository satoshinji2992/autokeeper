"""Formal event flags and ending gates use the same effective-time record as plans."""

from autokeeper.engine.game import Turn
from autokeeper.module.schema import Ending, Event
from autokeeper.world import conditions
from autokeeper.world import ops as O

from .test_keeper_tools_execution import shut, table
from .test_scene import sandbox  # noqa: F401


async def test_a_future_flag_cannot_finish_an_earlier_scene_or_satisfy_its_ending_requirements(fake):
    game, _, cast = table(fake)
    start = game.state.minutes
    game.m.endings = [Ending(id="stopped", title="钟已停下", condition="flag:bell_stopped", text="调查暂告结束。")]
    try:
        with game.world.at(start + 120):
            assert game.world.apply(O.SetFlag(flag="bell_stopped", actor="keeper")).ok
        assert not game._requirements(["flag:bell_stopped"])
        await game._check_ending(Turn())
        assert game.state.ending is None
        game.state.minutes = start + 120
        assert game._requirements(["flag:bell_stopped"])
        await game._check_ending(Turn())
        assert game.state.ending == "stopped"
    finally:
        await shut([game], [cast])


async def test_a_refused_flag_stays_in_the_kp_report_without_a_success_notice(fake):
    game, _, cast = table(fake)
    start = game.state.minutes
    shown = []

    async def emit(event):
        shown.append(event)

    game.emit = emit
    try:
        with game.world.at(start + 120):
            assert game.world.apply(O.SetFlag(id="future-on", flag="bell_stopped", actor="keeper")).ok
        with game.world.at(start + 121):
            assert game.world.apply(O.SetFlag(id="future-off", flag="bell_stopped", on=False, actor="keeper")).ok
        before = game.state.model_dump_json()
        turn = Turn()
        await game._flags(["bell_stopped"], turn)
        assert any("未落实" in note for note in turn.notes)
        assert not any(event["type"] == "flag" for event in shown)
        assert game.state.model_dump_json() == before
    finally:
        await shut([game], [cast])


async def test_a_scheduled_events_flags_keep_the_events_actual_minute(fake):
    game, _, cast = table(fake)
    start = game.state.minutes
    try:
        game.state.minutes = start + 30
        event = Event(id="stop_bell", title="钟停了", when="", text="钟声中断。", sets=["bell_stopped"])
        await game._fire(event, Turn(), at=start + 10)
        assert conditions.value(game.world, "flag:bell_stopped", at=start + 9) is False
        assert conditions.value(game.world, "flag:bell_stopped", at=start + 10) is True
        flag, = (event for event in game.world.rec.events if event.op == "event:stop_bell:flag:bell_stopped")
        assert flag.t == start + 10
    finally:
        await shut([game], [cast])
