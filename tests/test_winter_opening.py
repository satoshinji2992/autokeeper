"""《冬雪之下》的抵达仍按原作发生，接待与调查则留给玩家选择。"""

from autokeeper import paths
from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.state import parse_time
from autokeeper.module import prelude
from autokeeper.module.schema import Module

from .test_engine import make


def test_winter_prelude_bridges_the_public_background_to_the_lodging():
    module = Module.load(paths.MODULES / "dongxuezhixia")
    assert not prelude.problems(module, module.prelude)
    assert module.prelude[-1].art == module.location(module.start).art
    # The prelude has not already greeted anyone or resolved an investigation.
    text = "\n".join(frame.text for frame in module.prelude)
    assert "拉特格" in text and "安特医生" in text
    assert not any(clue.text in text for clue in module.clues)


async def test_winter_arrival_keeps_the_bodies_without_forcing_checkin_or_another_encounter(fake):
    module = Module.load(paths.MODULES / "dongxuezhixia")
    fake.queue_totals(5, 5)  # the existing start hook: 100 - 2D10, with the first worsening a day away
    game, cast, events = make(module, fake, {})
    try:
        await game.start()
        assert game.state.location == module.start == "ratger_house"
        assert game.state.minutes == parse_time(module.start_time) == parse_time("D1 14:00")
        assert game.state.visited == ["ratger_house"]
        assert game.state.fired == ["e_arrival"]
        assert not game.state.clues and not game.state.handouts
        assert "met_erie" not in game.state.flags
        assert not any(event["type"] == "event" for event in events)

        report = cast.reports[-1]
        assert "两具蒙着白布的尸体" in report
        # The travel material and welcome no longer repeat the event's body-truck passage.
        assert report.count("两具蒙着白布的尸体") == 1
        assert "我是多琳·拉特格" in report

        # A player who wants the doctor can leave directly; there is no prerequisite tea,
        # room assignment, map or new caller to finish before taking over the scene.
        await game.resolve(
            Action(kind="move", summary="我向房东道谢，直接去安特医生的诊所", destination="clinic"),
            Ruling(needs_roll=False, minutes=0),
        )
        assert game.state.location == "clinic"
        assert "main_street" not in game.state.visited
        assert "e_market_erie" not in game.state.fired
        assert not game.state.handouts
    finally:
        await game.close()
