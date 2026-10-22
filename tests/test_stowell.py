"""《有些人油盐不进》 with no one stepping in: the town's clock and the original's 催促玩家 events run by
themselves (situation.yaml agendas, story.yaml events), and the town is lost after the fourth night."""

from __future__ import annotations

import pytest

from autokeeper import paths
from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.state import format_time, parse_time
from autokeeper.module.schema import Module
from autokeeper.module.simulate import drift, playthrough
from autokeeper.world import scheduler
from autokeeper.world.agents import Canned, Scene

from .test_engine import make

CLOCK = {"town_stage_2": "D3 05:00", "town_stage_3": "D4 05:00", "town_lost": "D5 05:00"}
# What each pressure event hangs on, and when the agenda step that sets it comes due.
PRESSURE = {
    "e_donald_forages": "D2 03:00", "e_armed_eviction": "D2 08:00", "e_flyer_girl": "D2 12:00", "e_glenda_call": "D2 18:30",
    "e_wandering_congregant": "D3 05:00", "e_gladys_flees": "D3 09:00", "e_organ_concert": "D3 19:00",
    "e_inspectors_gone": "D4 05:00", "e_police_sweep": "D4 09:00", "e_ferguson_fire": "D4 14:00",
    "e_leonard_cleaver": "D4 19:00", "e_last_night": "D5 05:00",
}
ESCALATION = {"jensen_roundup": "D3 20:00", "stanley_sweep": "D4 09:00", "bingham_steak_dinner": "D4 19:00"}


@pytest.fixture
def stowell() -> Module:
    return Module.load(paths.MODULES / "fallout3-youxierenyouyanbujin")


def test_the_town_slides_night_by_night_and_is_lost_with_no_one_stepping_in(stowell):
    run = playthrough(stowell)
    assert not run.issues and run.reach.endings["e_town_lost"] == "事件"  # an ending the clock alone brings
    world = drift(stowell, days=5)
    when = {d.step: format_time(d.t) for d in world.timeline}
    assert {s: when.get(s) for s in ("hart_second_night", "hart_third_night", "hart_fourth_night")} == dict(
        zip(("hart_second_night", "hart_third_night", "hart_fourth_night"), CLOCK.values(), strict=True))
    assert {s: when.get(s) for s in ESCALATION} == ESCALATION  # the opponents' steps that wait on the clock
    assert format_time(world.endings["e_town_lost"]) == "D5 05:00" and not world.lost
    flagged = {flag: format_time(d.t) for d in world.timeline for part in d.does.split("&")
               if (flag := part.strip().removeprefix("flag:")) != part.strip()}
    for event in (e for e in stowell.events if e.id in PRESSURE):
        assert flagged[event.when.removeprefix("flag:")] == PRESSURE[event.id], event.id


def test_once_the_telescope_is_destroyed_the_clock_and_the_pressure_stop(stowell):
    stowell.events = [e.model_copy(update={"sets": [*e.sets, "telescope_destroyed"]}) if e.id == "e_jensen_arrives" else e
                      for e in stowell.events]
    world = drift(stowell, days=5)
    assert not world.endings
    assert not {"town_stage_2", "town_stage_3", "town_lost", "donald_loose", "ferguson_fire"} & {
        part.strip()[5:] for d in world.timeline for part in d.does.split("&")}


WAIT = Action(kind="wait", summary="调查员躲在麦克德莫特家里不出门")


async def test_in_play_the_events_fire_on_time_and_the_ending_comes(stowell, fake):
    """The game, its world and its timed steps, with no model deciding anything: the party sits in
    an empty house for four days."""
    game, _, events = make(stowell, fake, {}, location="mcdermott_house")
    st = game.state
    st.fired += ["e_mccurdy_attack", "e_stanley_shoots", "e_jensen_arrives"]  # the opening fight is played elsewhere
    st.flags.append("mccurdy_dead")
    game.brain = Canned(lambda ask: Scene(decisions=[]))  # type: ignore[assignment]
    game.minds = scheduler.Budget(turn=2, skip=4)
    fake.queue(*[1] * 10)  # sanity rolls pass
    seen: dict[str, str] = {}
    while not st.ending and st.minutes < parse_time("D6 00:00"):
        await game.resolve(WAIT, Ruling(needs_roll=False, minutes=60, reason="", changes=[], rest=False))
        for e in events:
            if e.get("type") == "event" and e["id"] not in seen:
                seen[e["id"]] = st.clock
    assert st.ending == "e_town_lost" and set(CLOCK) <= set(st.flags)
    for ident, due in PRESSURE.items():
        # each lands in the hour after its step comes due, wherever the party is
        assert ident in seen and 0 <= parse_time(seen[ident]) - parse_time(due) <= 60, (ident, seen.get(ident), due)
    assert sum(e.get("type") == "event" and e["id"] == "e_glenda_call" for e in events) == 1  # once, as the original has it
