"""NPC intentions become actions only when the world can actually settle them."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper import paths
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.world import context, scheduler, stage
from autokeeper.world import ops as O
from autokeeper.world.agents import Canned, Decision, Scene, holds
from autokeeper.world.record import PLAYERS, Step
from autokeeper.world.store import World

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
    brain = Canned(lambda ask: Scene(decisions=[Decision(npc=npc) for npc in ask.npcs]))
    report = await scheduler.advance(w, brain, w.now, w.now + 1)
    assert "warden:help" in report.blocked
    assert "计划条件无效" in brain.asks[0].reasons["warden"]
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


async def test_an_unverified_legacy_plan_is_reconsidered_instead_of_completed_by_a_conversation():
    w = staged()
    w.agent("warden").plan = [Step(id="visit", text="去广场与调查员会合")]
    assert w.apply(O.Occur(actor="warden", text="守钟人答应稍后出发", place="tower")).ok
    assert not w.apply(O.AdvancePlan(npc="warden", step="visit", status="done", note="已经出门")).ok
    brain = Canned(lambda ask: Scene(decisions=[Decision(npc=npc) for npc in ask.npcs]))
    report = await scheduler.advance(w, brain, w.now, w.now + 1)
    assert "warden:visit" in report.blocked
    assert any("旧计划缺少完成依据" in why for ask in brain.asks for why in ask.reasons.values())
    assert w.place("warden") == "tower"


async def test_an_old_prepared_plan_can_still_execute_its_actual_operations():
    w = staged()
    w.agent("warden").plan = [Step(id="visit", text="去广场", ops=[
        {"kind": "move", "entity": "warden", "to": "square"},
    ])]
    report = await scheduler.advance(w, None, w.now, w.now + 1)
    assert not report.refused and w.place("warden") == "square"
    assert w.agent("warden").plan[-1].status == "done"


async def test_a_prepared_visit_and_handover_roll_back_together_if_the_recipient_is_absent():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="help", text="去广场送东西", ops=[
        {"kind": "move", "entity": "warden", "to": "square"},
        {"kind": "transfer", "item": "coat", "to": w.st.investigator.id},
    ])])).ok
    report = await scheduler.advance(w, None, w.now, w.now + 1)
    assert report.refused and "warden:help" in report.blocked
    assert w.place("warden") == "tower"
    assert w.agent("warden").plan[-1].status == "pending"
    assert not any(e.kind == "move" and e.actor == "warden" for e in w.rec.events)


async def test_a_refused_npc_action_cannot_be_followed_by_a_false_handover_description():
    w = staged(location="tower")
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="help", text="给调查员工具")])).ok
    brain = Canned(lambda ask: Scene(decisions=[Decision(npc="warden", ops=[
        O.Transfer(item="missing_lantern", to=w.st.investigator.id),
        O.Occur(text="守钟人把提灯递给调查员", place="tower"),
        O.AdvancePlan(npc="warden", step="help", status="done"),
    ])]))
    report = await scheduler.advance(w, brain, w.now, w.now + 1)
    assert report.refused and report.deferred
    assert not any("递给调查员" in e.text for e in w.rec.events)
    assert w.agent("warden").plan[-1].status == "pending"
    assert w.agent("warden").wake <= w.now + 1


async def test_an_invalid_scheduled_action_does_not_generate_its_successful_scene():
    w = staged()
    assert w.apply(O.Schedule(event="help", at=w.now + 1, text="守钟人在广场送来提灯", place="square",
                              participants=["warden"], actor="director", ops=[
                                  {"kind": "transfer", "item": "missing_lantern", "to": w.st.investigator.id},
                              ])).ok
    report = await scheduler.advance(w, None, w.now, w.now + 1)
    assert report.dropped == ["help"] and not report.fired
    assert w.place("warden") == "tower"
    assert not any("送来提灯" in e.text for e in w.rec.events if e.kind == "occur")


async def test_thomas_can_offer_help_and_show_a_supported_emotion_without_a_player_prompt():
    module = Module.load(paths.MODULES / "zhuishuren")
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "jinboer_jia"
    w = World(module, state)
    assert w.apply(O.Enlist(npc="thomas")).ok
    assert w.apply(O.Occur(text="调查员当面说：刚才有人想从窗边逃走。", actor=state.investigator.id,
                          place=state.location, participants=["thomas"])).ok
    observed = "托马斯的声音发紧：我陪您到公墓看看，总不能再让人进我家。"
    brain = Canned(lambda ask: Scene(decisions=[Decision(
        npc="thomas", mood="听说窗边有人逃走，担心叔叔的旧物再被偷，急于弄清情况",
        ops=[O.Occur(text=observed, place=state.location, participants=["thomas"]),
             O.Follow(npc="thomas", leader=state.investigator.id, destination="mudi")],
    )]))
    report = await scheduler.advance(w, brain, w.now, w.now + 1)
    assert report.calls == 1 and not report.refused
    assert not w.entity("thomas").following  # in front of the party: it waits for the keeper to play it
    assert report.staged and [stage.play(w, beat) for beat in stage.waiting(w)] == [[]]
    view = brain.asks[0].views["thomas"]
    assert "协助调查员" in view and "刚才有人想从窗边逃走" in view
    assert w.entity("thomas").following == state.investigator.id
    assert w.agent("thomas").mood in context.npc_brief(w, "thomas")
    assert observed in context.keeper_view(w, state.location)
    assert w.agent("thomas").mood not in context.keeper_view(w, state.location)


def test_replacing_a_completed_step_with_the_same_id_keeps_the_new_step_executable():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="离开钟楼", achieved="at:warden:square")])).ok
    assert w.apply(O.Move(entity="warden", to="square", actor="warden")).ok
    assert w.apply(O.AdvancePlan(npc="warden", step="visit", status="done")).ok
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="返回钟楼", achieved="at:warden:tower")])).ok
    assert w.apply(O.Move(entity="warden", to="tower", actor="warden")).ok
    assert w.apply(O.AdvancePlan(npc="warden", step="visit", status="done")).ok
    assert len([s for s in w.agent("warden").plan if s.id == "visit"]) == 1


async def test_npcs_in_different_places_think_concurrently_from_the_same_instant():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="help", text="想下一步")])).ok
    started = set()
    both_started = asyncio.Event()

    async def think(ask):
        npc = ask.npcs[0]
        started.add(npc)
        if len(started) == 2:
            both_started.set()
        await both_started.wait()
        assert not any("已完成并行行动" in text for text in ask.views.values())
        return Scene(decisions=[Decision(npc=npc, ops=[O.Remember(npc=npc, text="已完成并行行动")])])

    report = await asyncio.wait_for(scheduler.advance(w, Canned(think), w.now, w.now + 1), timeout=5)
    assert started == {"warden", "preacher"} and report.calls == 2
    assert all(any("已完成并行行动" in m.text for m in w.agent(npc).memory) for npc in started)


async def test_a_parallel_decision_is_deferred_when_an_earlier_commit_changed_its_evidence():
    w = staged()
    assert w.apply(O.Relate(a="warden", b="preacher", rel="信任", value=0,
                            known_by=["warden", "preacher"])).ok
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="help", text="想下一步")])).ok

    def think(ask):
        npc = ask.npcs[0]
        return Scene(decisions=[Decision(npc=npc, ops=[
            O.Relate(a="warden", b="preacher", rel="信任", delta=20) if npc == "warden"
            else O.Occur(text="布道者按旧的关系采取行动", place="square"),
        ])])

    report = await scheduler.advance(w, Canned(think), w.now, w.now + 1)
    assert report.calls == 2 and any("preacher：构思期间依据发生变化" in text for text in report.deferred)
    assert not any(e.text == "布道者按旧的关系采取行动" for e in w.rec.events)
    assert w.relation("warden", "preacher", "信任").value == 20


async def test_a_director_goal_change_during_a_decision_rejects_the_old_intention():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="think", text="想下一步")])).ok

    def think(ask):
        assert w.apply(O.Enlist(npc="warden", goals=["先救助受伤的人"])).ok
        return Scene(decisions=[Decision(npc="warden", ops=[
            O.Occur(text="守钟人继续执行已经取消的旧目标", place="tower"),
        ])])

    report = await scheduler.advance(w, Canned(think), w.now, w.now + 1, budget=scheduler.Budget(turn=1))
    assert any("agent:warden" in message for message in report.deferred)
    assert not any("已经取消的旧目标" in event.text for event in w.rec.events)
    assert w.agent("warden").goals == ["先救助受伤的人"]


async def test_a_prepared_plan_is_not_invalidated_by_writing_its_own_agent_version():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="think", text="想下一步")])).ok
    start = w.now

    def think(ask):
        return Scene(decisions=[Decision(npc="warden", mood="决定去广场帮忙", ops=[
            O.SetPlan(npc="warden", steps=[Step(id="walk", text="去广场", due=start + 5, ops=[
                {"kind": "move", "entity": "warden", "to": "square"},
            ])]),
        ])])

    first = await scheduler.advance(w, Canned(think), start, start + 1, budget=scheduler.Budget(turn=1))
    assert not first.refused and "agent:warden" not in w.agent("warden").reads
    second = await scheduler.advance(w, None, start + 1, start + 10)
    assert not second.refused and w.place("warden") == "square"
    assert w.agent("warden").plan[-1].status == "done"


async def test_a_person_leaving_during_a_decision_invalidates_the_present_company_context():
    w = staged(location="tower")
    assert w.apply(O.Move(entity="preacher", to="tower")).ok
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="think", text="与布道者谈话")])).ok

    def think(ask):
        assert "街头布道者" in ask.views["warden"]
        assert w.apply(O.Move(entity="preacher", to="square", actor="preacher")).ok
        return Scene(decisions=[Decision(npc="warden", ops=[
            O.Occur(text="守钟人继续对着已离开的人解释", place="tower"),
        ])])

    report = await scheduler.advance(w, Canned(think), w.now, w.now + 1, budget=scheduler.Budget(turn=1))
    assert any("entity:preacher" in message for message in report.deferred)
    assert not any("对着已离开的人" in event.text for event in w.rec.events)
