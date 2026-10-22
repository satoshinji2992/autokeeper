"""Insanity's clocks and completed rest: no models, no old-playthrough edits."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Narration, Ruling
from autokeeper.engine import audience
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState, parse_time
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Investigator, MadnessState, Rules
from autokeeper.world import scene

from .test_engine import FakeCast, make
from .test_resolution_recovery import reload_game, snapshots, turn_going


def test_temporary_hours_are_separate_from_the_bout_and_survive_reload(inv, fake):
    rules = Rules(dice=fake.queue(80, 30).queue_totals(6, 3))
    result = rules.sanity(inv, "0", "1D6", now=0)
    assert result.duration_hours == 3 and result.bout == "BMR 表结果"
    assert inv.madness.temporary_since == 0 and inv.madness.temporary_until == 180
    saved = Investigator.model_validate_json(inv.model_dump_json())
    saved.conditions += ["患病", "重伤"]
    saved.hp = 4
    assert not saved.recover_madness(2)
    assert "临时疯狂" in saved.conditions and saved.can_act
    assert not saved.recover_madness(179)
    assert saved.recover_madness(180) == ["临时疯狂已到期恢复"]
    assert saved.conditions == ["患病", "重伤"]
    assert saved.san == 54 and saved.san_lost_today == 6 and saved.hp == 4
    assert not saved.recover_madness(10000)


def test_indefinite_insanity_is_not_cleared_when_temporary_insanity_expires(inv, fake):
    rules = Rules(dice=fake.queue(80, 30).queue_totals(6, 10))
    result = rules.sanity(inv, "0", "1D6", now=600)
    assert fake.tables == ["BMR"] and result.duration_hours == 10
    assert inv.madness.temporary_until == 1200
    inv.add_condition("不定性疯狂")
    inv.backstory["恐惧症和躁狂症"] = "怕钟症"
    inv.recover_madness(1200)
    assert inv.conditions == ["不定性疯狂"]
    assert inv.backstory["恐惧症和躁狂症"] == "怕钟症" and inv.san == 54


def test_indefinite_insanity_has_no_hourly_expiry(inv, fake):
    result = Rules(dice=fake.queue(80, 99)).sanity(inv, "0", "12", now=20)
    assert result.indefinite and not result.temporary
    assert inv.madness.temporary_until is None
    inv.recover_madness(140)
    inv.recover_madness(100000, rested_since=140)
    assert inv.conditions == ["不定性疯狂"] and inv.san == 48


def test_unknown_old_tag_is_not_assigned_a_start_or_expiry(inv):
    inv.add_condition("临时疯狂")
    assert inv.madness == MadnessState()
    assert not inv.recover_madness(100000)
    assert "临时疯狂" in inv.conditions and "madness" not in inv.model_dump()


def test_a_new_trauma_during_rest_prevents_early_recovery(inv):
    inv.add_condition("临时疯狂")
    inv.madness = MadnessState(temporary_since=80, temporary_until=680)
    assert not inv.recover_madness(100, rested_since=50)
    assert inv.conditions == ["临时疯狂"]
    assert not inv.recover_madness(100, rested_since=100)  # no time actually spent asleep


def test_a_failed_duration_die_does_not_leave_an_unclocked_condition(inv, fake):
    fake.queue(80, 30).queue_totals(6)
    before = inv.model_dump_json()
    with pytest.raises(IndexError):  # no result returned for the duration die
        Rules(dice=fake).sanity(inv, "0", "1D6", now=800)
    assert inv.model_dump_json() == before


@pytest.fixture
def module():
    m = Module.load(EXAMPLE)
    m.events = []
    return m


async def test_game_time_expires_madness_for_every_investigator_and_announces_it(module, fake):
    game, _, events = make(module, fake, {})
    st = game.state
    inv = st.investigator
    friend = inv.model_copy(deep=True, update={"id": "friend", "name": "同行者"})
    st.investigators.append(friend)
    st.apart[friend.id] = "square"
    for who in st.investigators:
        who.conditions = ["临时疯狂", "患病"]
        who.madness = MadnessState(temporary_since=st.minutes, temporary_until=st.minutes + 60)
    st.minutes += 59
    await game._time()
    assert all("临时疯狂" in who.conditions for who in st.investigators)
    st.minutes += 1
    await game._time()
    assert all(who.conditions == ["患病"] for who in st.investigators)
    assert len([e for e in events if e["type"] == "status" and "到期恢复" in e.get("text", "")]) == 2
    assert len([e for e in st.log if "到期恢复" in e.text]) == 2
    recovery = [e for e in events if e["type"] == "status" and "到期恢复" in e.get("text", "")]
    assert {tuple(e["audience"]) for e in recovery} == {(inv.id,), (friend.id,)}
    for who in st.investigators:
        visible = [e for e in audience.log(st, who.id) if "到期恢复" in e.text]
        assert len(visible) == 1 and visible[0].audience == [who.id]
    await game._time()
    assert len([e for e in st.log if "到期恢复" in e.text]) == 2
    await game.close()


class RestKeeper(FakeCast):
    def __init__(self, who: str):
        super().__init__({})
        self.who = who

    async def keeper(self, state, report, on_delta=None, lore="", *, check=None, fix=""):
        here = [e.id for e in state.record.entities.values() if e.kind == "npc" and e.at == state.location]
        raw = {"rested": [self.who], "here": here}
        if check is not None:
            assert check(raw), fix
        return Narration(text="你在安全处安稳睡过一夜，直到天亮。", scene=raw)


async def test_completed_rest_lands_after_the_telling_and_can_resume_without_repeating_recovery(module, fake):
    game, _, events = make(module, fake, {})
    st, inv = game.state, game.state.investigator
    st.minutes = parse_time("D1 23:00")
    inv.conditions = ["临时疯狂", "不定性疯狂", "患病", "重伤"]
    inv.madness = MadnessState(temporary_since=st.minutes - 60, temporary_until=st.minutes + 600)
    inv.hp, inv.san = 4, 40
    game.cast = RestKeeper(inv.id)
    st.turn += 1
    st.add("player", "在安全处睡到天亮", who=inv.name)
    action = Action(kind="wait", summary="在安全处睡到天亮")
    ruling = Ruling(needs_roll=False, rest=True, minutes=360)
    st.pending_turn["input"] = action.summary
    turn = await game.resolve(action, ruling)
    assert st.clock == "D2 05:00" and "临时疯狂" in inv.conditions  # the plan and clock alone do not prove good rest

    async def interrupted(event):
        if event["type"] == "status" and "安全过夜休息" in event.get("text", ""):
            raise ConnectionError("recovery notification interrupted")
        events.append(event)

    game.emit = interrupted
    with pytest.raises(ConnectionError):
        await game._narrate("实际睡过一夜", turn=turn)
    saved = GameState.model_validate_json(st.model_dump_json())
    resumed = Game(module, saved, RestKeeper(inv.id), Rules(dice=fake))
    await resumed._narrate("实际睡过一夜", turn=turn)
    restored = saved.investigator
    assert restored.conditions == ["不定性疯狂", "患病", "重伤"]
    assert restored.hp == 4 and restored.san == 40
    assert len([e for e in saved.log if "安全过夜休息" in e.text]) == 1
    await game.close()
    await resumed.close()


@pytest.mark.parametrize(("resting", "elapsed"), [(False, 480), (True, 0)])
def test_a_scene_cannot_clear_madness_without_completed_rest_time(module, fake, resting, elapsed):
    game, _, _ = make(module, fake, {})
    inv = game.state.investigator
    inv.add_condition("临时疯狂")
    planned = scene.plan(game.world, {"rested": [inv.id]},
                         scene.Context(travel=game._travel, resting=resting, elapsed=elapsed))
    assert planned.problems and not planned.rested
    assert "临时疯狂" in inv.conditions


def test_a_ruled_interruption_is_not_a_completed_nights_rest(module, fake):
    game, _, _ = make(module, fake, {})
    game.state.add("player", "睡一晚", who=game.state.investigator.name)
    game.state.minutes += 480
    turn = Turn(action=Action(kind="wait", summary="睡一晚"),
                ruling=Ruling(needs_roll=False, rest=True, mishap="nightmare"))
    ctx = game._scene_context(turn)
    assert not ctx.resting
    assert scene.plan(game.world, {"rested": [game.state.investigator.id]}, ctx).problems


async def test_failed_duration_die_resumes_without_repeating_san_int_or_bout(module, fake):
    module.clue("c_diary").sanity = "0/1D6"
    action = Action(kind="investigate", summary="阅读骇人的日记", target="守钟人的柜子")
    ruling = Ruling(needs_roll=False, auto_clues=["c_diary"], minutes=5)
    game, _, _ = make(module, fake.queue(90, 5).queue_totals(5), {}, "tower")
    turn_going(game, action.summary)
    saved = snapshots(game)
    san = game.state.investigator.san
    total = fake.total

    def unavailable(expr):
        if expr == "1D10":
            raise ConnectionError("duration die unavailable")
        return total(expr)

    fake.total = unavailable
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    assert game.state.investigator.san == san and not game.state.investigator.conditions
    assert fake.tables == ["BMR"] and not fake.values and not fake.totals
    fake.total = total
    fake.queue_totals(4)
    resumed, _ = reload_game(module, saved[-1], fake)
    turn = await resumed.resolve(action, ruling)
    inv = resumed.state.investigator
    assert inv.san == san - 5 and inv.madness.temporary_until == resumed.state.minutes + 240
    assert turn.sanity[0][1].duration_hours == 4 and fake.tables == ["BMR"]
    assert not fake.totals
    await game.close()
    await resumed.close()
