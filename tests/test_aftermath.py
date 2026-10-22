"""A fallen party is a keeper's scene: actual aid, time, events and resumable dice."""

from __future__ import annotations

import pytest

from autokeeper.agents.aftermath import Aftercare, Aftermath
from autokeeper.engine import aftermath, effects
from autokeeper.engine.game import PreparationError, Turn
from autokeeper.engine.state import format_time
from autokeeper.module.schema import Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.world.ops import Schedule

from .test_engine import make


@pytest.fixture
def module():
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    m.npc("warden").stats.update({"急救": 70, "医学": 70})
    return m


def down(game, *, dying=True, stabilized=False):
    inv = game.state.investigator
    inv.hp = 1 if stabilized else 0 if dying else 2
    inv.conditions = ["重伤", "昏迷", "濒死"] if dying else ["昏迷"]
    inv.injury.unconscious_at = game.state.minutes
    inv.injury.injuries = ["ambush"]
    if stabilized:
        inv.injury.stabilized_at = game.state.minutes
    return inv


def plan(*steps, why="守钟人愿意救助倒下的调查员"):
    return Aftermath(reason=why, steps=[Aftercare(**s) for s in steps])


def script(cast, *plans):
    calls = []
    async def choose(st, report, problems=None):
        calls.append(report)
        return plans[min(len(calls) - 1, len(plans) - 1)]
    cast.aftermath = choose
    return calls


def care(inv, kind="first_aid", **kw):
    return {"kind": kind, "who": "warden", "target": inv.id, "reason": "守钟人取出绷带施救", **kw}


async def test_an_actual_helper_stabilizes_then_treats_instead_of_running_con_until_death(module, fake):
    game, cast, events = make(module, fake, {}, location="tower")
    inv = down(game)
    start = game.state.minutes
    calls = script(cast, plan(care(inv, repeat=True), care(inv, "medicine", minutes=60)))
    fake.queue(10, 10).queue_totals(2)
    await game._party_down(Turn())
    assert inv.can_act and inv.hp == 3 and "重伤" in inv.conditions
    assert "濒死" not in inv.conditions and not game.state.ending
    assert game.state.minutes == start + 60 and game.state.aftercare_seconds == 6
    assert len(calls) == 1 and "急救 70，医学 70" in calls[0]
    assert not fake.values and not fake.totals
    assert len([e for e in events if e["type"] == "damage"]) == 2
    await game.close()


async def test_a_present_npc_can_refuse_and_the_dying_roll_still_decides_death(module, fake):
    game, cast, events = make(module, fake, {}, location="tower")
    inv = down(game)
    start = game.state.minutes
    calls = script(cast, plan({"kind": "wait", "minutes": 1, "reason": "守钟人是袭击者，拒绝救治"}, why="在场者拒绝救助"))
    fake.queue(90)
    await game._party_down(Turn())
    assert "死亡" in inv.conditions and game.state.ending == "party_dead"
    assert game.state.minutes == start + 1 and not fake.values and len(calls) == 1
    assert any(":06）" in e.get("text", "") for e in events)
    await game.close()


async def test_a_failed_single_aid_attempt_costs_a_round_and_con_before_replanning(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    inv = down(game)
    calls = script(cast, plan(care(inv, repeat=False)))
    fake.queue(90, 90)  # failed aid, then failed CON; no second free attempt
    await game._party_down(Turn())
    assert game.state.ending == "party_dead" and len(calls) == 1 and not fake.values
    await game.close()


async def test_successful_aid_still_costs_the_other_dying_investigator_a_round(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    inv = down(game)
    other = inv.model_copy(deep=True, update={"id": "other", "name": "同伴"})
    game.state.investigators.append(other)
    script(cast, plan(care(inv), care(inv, "medicine", minutes=60)))
    fake.queue(10, 90, 10).queue_totals(2)  # aid succeeds; the other dies; Medicine succeeds
    await game._party_down(Turn())
    assert inv.can_act and "死亡" in other.conditions and not game.state.ending and not fake.values
    await game.close()


async def test_waiting_stops_at_a_scheduled_arrival_instead_of_skipping_an_hour(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    inv = down(game, dying=False)
    start = game.state.minutes
    game.world.rec.entities["warden"].at = "office"
    module.events.append(Event(id="rescue", title="守钟人赶回", when=format_time(start + 1), where="tower",
                               text="守钟人回到钟楼。", read_aloud="门开了。", present=["warden"]))
    calls = script(cast, plan({"kind": "wait", "minutes": 60, "reason": "等守钟人回来"}), plan(care(inv)))
    fake.queue(10)
    await game._party_down(Turn())
    assert inv.can_act and game.state.minutes == start + 1 and game.state.aftercare_seconds == 6
    assert game.state.fired == ["rescue"] and len(calls) == 2 and not fake.values
    await game.close()


async def test_deferred_world_work_still_lands_scheduled_rescue_before_aftercare_continues(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    inv = down(game, dying=False)
    start = game.state.minutes
    game.world_later = True
    game.world.rec.entities["warden"].at = "office"
    game.world.apply(Schedule(id="call-for-aid", event="return", at=start + 1, text="守钟人赶回查看倒地的人",
                              place="tower", participants=["warden"]))
    calls = script(cast, plan({"kind": "wait", "minutes": 60, "reason": "等守钟人回来"}), plan(care(inv)))
    fake.queue(10)
    await game._party_down(Turn())
    assert inv.can_act and game.state.minutes == start + 1 and len(calls) == 2
    assert game.world.rec.scheduled["return"].status == "done" and game.world.rec.entities["warden"].at == "tower"
    assert game.world_later and not game._world_owed
    await game.close()


async def test_a_due_waking_hands_control_back_without_requesting_another_arrangement(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    inv = down(game, dying=False)
    inv.injury.unconscious_at -= 60
    calls = script(cast, plan({"kind": "finish", "reason": "不应再调用此安排"}))
    await game._party_down(Turn())
    assert inv.can_act and not calls and game.state.ending is None
    await game.close()


async def test_a_plugin_deadline_changing_the_situation_interrupts_a_long_wait(module, fake):
    module.flags["alarm"] = "插件发出的警报"
    game, _cast, _ = make(module, fake, {}, location="tower")
    down(game, dying=False)
    start = game.state.minutes
    game.state.plugin_timers.append({"minutes": start + 5})

    async def due(turn):
        if game.state.minutes >= start + 5 and game.state.plugin_timers:
            game.state.plugin_timers.clear()
            await game._flags(["alarm"], turn)

    game.plugins.due = due
    completed = await aftermath.advance(game, Turn(), 60, "wait-for-aid")
    assert not completed and game.state.minutes == start + 5 and "alarm" in game.state.flags
    await game.close()


@pytest.mark.parametrize("status,conditions", [("失踪", []), ("", ["昏迷"]), ("", ["永久疯狂"])])
async def test_unavailable_npc_cannot_help_even_when_its_old_place_and_unseeded_body_look_healthy(module, fake, status, conditions):
    game, _cast, _ = make(module, fake, {}, location="tower")
    inv = down(game)
    ent = game.world.rec.entities["warden"]
    ent.status, ent.conditions = status, conditions
    turn = Turn()
    applied = await aftermath.perform(game, turn, Aftercare(**care(inv)), "missing-helper")
    assert not applied and inv.hp == 0 and "濒死" in inv.conditions and not fake.values
    text = aftermath.report(game, turn)
    assert (status or conditions[0]) in text
    await game.close()


async def test_stabilized_patient_is_not_closed_out_and_a_failed_hourly_con_allows_more_aid(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    inv = down(game, stabilized=True)
    start = game.state.minutes
    calls = script(cast, plan({"kind": "wait", "minutes": 120, "reason": "暂时看护伤者"}),
                   plan(care(inv, repeat=True), care(inv, "medicine", minutes=60)))
    fake.queue(90, 10, 10).queue_totals(2)
    await game._party_down(Turn())
    assert inv.can_act and inv.hp == 3 and not game.state.ending and not fake.values
    assert len(calls) == 2 and game.state.minutes == start + 120
    assert any(e.kind == "condition" and "再度恶化" in e.text for e in game.world.rec.events)
    await game.close()


async def test_impossible_transport_is_not_charged_and_does_not_teleport(module, fake):
    game, _cast, _ = make(module, fake, {}, location="tower")
    down(game, dying=False)
    start = game.state.minutes
    turn = Turn()
    moved = await aftermath.perform(game, turn, Aftercare(kind="move", who="warden", target="players", to="office",
                                                         minutes=0, reason="背着调查员去报社"), "transport")
    assert not moved and game.state.minutes == start and game.state.location == "tower"
    assert game.world.rec.entities["warden"].at == "tower"
    assert any("至少需要20分钟" in n for n in turn.notes)
    await game.close()


async def test_a_room_move_can_take_less_than_a_full_minute(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    inv = down(game, dying=False)
    script(cast, plan({"kind": "move", "who": "warden", "target": "players", "to": "square", "minutes": 0,
                       "reason": "把调查员从楼梯口背到门外广场"}, care(inv)))
    fake.queue(10)
    await game._party_down(Turn())
    assert game.state.location == "square" and game.world.rec.entities["warden"].at == "square" and inv.can_act
    await game.close()


async def test_interrupted_aid_notice_resumes_without_another_roll_or_another_six_seconds(module, fake):
    game, cast, events = make(module, fake, {}, location="tower")
    inv = down(game, dying=False)
    calls = script(cast, plan(care(inv)))
    game._resolution = {"steps": {}}
    turn = Turn()
    emit = game.emit
    async def broken(event):
        if event["type"] == "damage":
            raise ConnectionError("通知断线")
        await emit(event)
    game.emit = broken
    fake.queue(10)
    with pytest.raises(ConnectionError):
        await game._party_down(turn)
    assert inv.can_act and not fake.values
    game.emit = emit
    await game._party_down(turn)
    assert len(calls) == 1 and game.state.aftercare_seconds == 6
    assert len([e for e in events if e["type"] == "damage"]) == 1
    await game.close()


async def test_interrupted_death_notice_is_delivered_before_the_ending_and_not_rerolled(module, fake):
    game, cast, events = make(module, fake, {}, location="tower")
    down(game)
    script(cast, plan({"kind": "wait", "minutes": 1, "reason": "现场无人愿意施救"}))
    game._resolution = {"steps": {}}
    turn, emit = Turn(), game.emit
    async def broken(event):
        if event["type"] == "damage":
            raise ConnectionError("濒死通知断线")
        await emit(event)
    game.emit = broken
    fake.queue(90)
    with pytest.raises(ConnectionError):
        await game._party_down(turn)
    game.emit = emit
    await game._party_down(turn)
    assert not fake.values and game.state.ending == "party_dead"
    assert [e["type"] for e in events if e["type"] in ("damage", "ending")] == ["damage", "ending"]
    await game.close()


async def test_execution_guard_can_resume_at_a_new_phase_instead_of_replaying_32_old_plans(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    inv.conditions = ["永久疯狂"]
    calls = script(cast, plan({"kind": "wait", "minutes": 1, "reason": "短暂等待"}))
    game._resolution = {"steps": {}}
    start, turn = game.state.minutes, Turn()
    with pytest.raises(PreparationError):
        await game._party_down(turn)
    assert len(calls) == 32 and game.state.minutes == start + 32
    more = script(cast, plan({"kind": "finish", "reason": "永久疯狂，无法恢复此次调查"}))
    await game._party_down(turn)
    assert len(more) == 1 and game.state.minutes == start + 32 and game.state.ending == "party_unfit"
    await game.close()


async def test_legacy_cast_does_not_silently_decide_that_a_present_npc_refuses_rescue(module, fake):
    game, _cast, _ = make(module, fake, {}, location="tower")
    inv = down(game)
    start = game.state.minutes
    with pytest.raises(PreparationError, match="尚未判断现场人物是否施救"):
        await game._party_down(Turn())
    assert "死亡" not in inv.conditions and game.state.minutes == start and not game.state.ending
    await game.close()


def test_medicine_effect_removes_dying_and_remembers_the_injury(module, fake):
    game, _cast, _ = make(module, fake, {}, location="tower")
    inv = down(game, stabilized=True)
    done = effects.commit(game.world, [effects.Heal(target=inv.id, amount=2, means="medicine", injury="ambush"),
                                       effects.Wake(target=inv.id)], op_id="hospital", source="engine")
    assert done.ok and inv.hp == 3 and inv.can_act and "medicine:ambush" in inv.injury.aided
    assert effects.body_of(game.world, inv.id).stabilized_at is None
