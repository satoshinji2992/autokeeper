"""NPC intentions become actions only when the world can actually settle them."""

from __future__ import annotations

import pytest

from autokeeper.world import ops as O
from autokeeper.world import scheduler
from autokeeper.world.agents import holds
from autokeeper.world.record import PLAYERS, Step

from .test_world import staged


@pytest.mark.parametrize("condition", [
    "at:tafi:square", "at:warden:gongmu", "at:warden:square|", "after:tomorrow", "flag:not_declared",
])
def test_invented_plan_conditions_are_rejected(condition):
    w = staged()
    result = w.apply(O.SetPlan(npc="warden", steps=[Step(id="help", text="去帮忙", when=condition)]))
    assert not result.ok and any("条件" in problem for problem in result.problems)
    assert not holds(w, condition)


def test_conditions_support_real_player_ids_and_or_without_inventing_aliases():
    w = staged(location="square")
    when = f"at:{w.st.investigator.id}:tower | at:{PLAYERS}:square & !flag:bell_stopped"
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="help", text="等调查员到场", when=when)])).ok
    assert holds(w, when)
    w.st.location = "office"
    assert not holds(w, when)
    w.st.location = "tower"
    assert holds(w, when)


async def test_an_invalid_condition_from_an_old_save_wakes_the_npc_to_replan():
    w = staged()
    w.agent("warden").plan = [Step(id="help", text="去会合", when="at:tafi:jinboer_jia|at:tafi:gongmu")]
    report = await scheduler.advance(w, w.now, w.now + 1)
    assert "warden:help" in report.blocked
    assert any(n.npc == "warden" and "计划条件无效" in n.text for n in scheduler.pending(w))
    assert w.agent("warden").plan[0].status == "blocked"


def test_saying_a_visit_is_done_does_not_move_an_npc():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[
        Step(id="visit", text="与调查员去广场", achieved="at:warden:square"),
    ])).ok
    result = w.apply(O.AdvancePlan(npc="warden", step="visit", status="done", note="已经一起出了门"))
    assert not result.ok and w.place("warden") == "tower"
    assert w.agent("warden").plan[-1].status == "pending"
    assert w.apply(O.Move(entity="warden", to="square", actor="warden")).ok
    assert w.apply(O.AdvancePlan(npc="warden", step="visit", status="done")).ok


def test_an_old_plan_cannot_complete_using_unrelated_past_actions():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="去广场")])).ok
    assert w.apply(O.Occur(text="守钟人检查了门锁", actor="warden", place="tower")).ok
    w.st.minutes += 30
    result = w.apply(O.AdvancePlan(npc="warden", step="visit", status="done", note="已经出门"))
    assert not result.ok


async def test_an_old_prepared_plan_can_still_execute_its_actual_operations():
    w = staged()
    w.agent("warden").plan = [Step(id="visit", text="去广场", ops=[
        {"kind": "move", "entity": "warden", "to": "square"},
    ])]
    report = await scheduler.advance(w, w.now, w.now + 1)
    assert not report.refused and w.place("warden") == "square"
    assert w.agent("warden").plan[-1].status == "done"


async def test_a_prepared_visit_and_handover_roll_back_together_if_the_recipient_is_absent():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="help", text="去广场送东西", ops=[
        {"kind": "move", "entity": "warden", "to": "square"},
        {"kind": "transfer", "item": "coat", "to": w.st.investigator.id},
    ])])).ok
    report = await scheduler.advance(w, w.now, w.now + 1)
    assert report.refused and "warden:help" in report.blocked
    assert w.place("warden") == "tower"
    assert w.agent("warden").plan[-1].status == "pending"
    assert not any(e.kind == "move" and e.actor == "warden" for e in w.rec.events)


async def test_an_invalid_scheduled_action_does_not_generate_its_successful_scene():
    w = staged()
    assert w.apply(O.Schedule(event="help", at=w.now + 1, text="守钟人在广场送来提灯", place="square",
                              participants=["warden"], actor="director", ops=[
                                  {"kind": "transfer", "item": "missing_lantern", "to": w.st.investigator.id},
                              ])).ok
    report = await scheduler.advance(w, w.now, w.now + 1)
    assert report.dropped == ["help"] and not report.fired
    assert w.place("warden") == "tower"
    assert not any("送来提灯" in e.text for e in w.rec.events if e.kind == "occur")


def test_replacing_a_completed_step_with_the_same_id_keeps_the_new_step_executable():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="离开钟楼", achieved="at:warden:square")])).ok
    assert w.apply(O.Move(entity="warden", to="square", actor="warden")).ok
    assert w.apply(O.AdvancePlan(npc="warden", step="visit", status="done")).ok
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="返回钟楼", achieved="at:warden:tower")])).ok
    assert w.apply(O.Move(entity="warden", to="tower", actor="warden")).ok
    assert w.apply(O.AdvancePlan(npc="warden", step="visit", status="done")).ok
    assert len([s for s in w.agent("warden").plan if s.id == "visit"]) == 1
