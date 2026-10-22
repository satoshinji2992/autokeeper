"""A decision's completion receipts follow its real effects, without weakening their evidence."""

from __future__ import annotations

import pytest

from autokeeper.engine.state import GameState
from autokeeper.world import ops as O
from autokeeper.world import scheduler, stage
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step
from autokeeper.world.store import World
from autokeeper.world.tools import Gateway

from .test_world import staged


def planned(*, location: str = "office", achieved: str = "") -> World:
    w = staged(location)
    assert w.apply(O.SetPlan(npc="warden", steps=[
        Step(id="visit", text="下楼检查广场", achieved=achieved, meet=False),
    ])).ok
    return w


def completion() -> dict:
    return {"kind": "step", "npc": "warden", "step": "visit", "status": "done", "note": "已经下楼检查过了"}


def move(to: str = "square") -> dict:
    return {"kind": "move", "entity": "warden", "to": to}


def land(w: World, ops: list[dict], prefix: str = "decision") -> scheduler.Report:
    report = scheduler.Report(tick=prefix)
    scheduler._land(w, ops, prefix=prefix, actor="warden", report=report)
    return report


@pytest.mark.parametrize("achieved", ["", "at:warden:square"])
def test_completion_before_its_real_action_lands_in_the_same_decision(achieved):
    w = planned(achieved=achieved)
    report = land(w, [completion(), move()])

    assert not report.refused and w.place("warden") == "square"
    assert w.agent("warden").plan[-1].status == "done"
    assert report.landed == ["decision:1", "decision:0"]  # identity is the input index, not execution order
    assert [e.kind for e in w.rec.events[-2:]] == ["move", "step"]


@pytest.mark.parametrize("extra", [[], [{"kind": "remember", "npc": "warden", "text": "我已经下楼检查过了"}]])
def test_a_completion_note_or_memory_without_actual_execution_is_still_refused(extra):
    w = planned()
    before = w.st.model_dump_json()
    report = land(w, [completion(), *extra])

    assert report.refused and not report.landed and w.st.model_dump_json() == before
    assert w.agent("warden").plan[-1].status == "pending"


@pytest.mark.parametrize("unrelated", [
    {"kind": "move", "entity": "warden", "to": "office"},
    {"kind": "occur", "text": "守钟人检查了塔顶的窗户", "place": "tower", "participants": ["warden"]},
])
def test_an_unrelated_action_cannot_satisfy_an_explicit_completion_condition(unrelated):
    w = planned(achieved="at:warden:square")
    assert w.apply(O.Enlist(npc="warden", places=["office"])).ok
    before = w.st.model_dump_json()
    report = land(w, [completion(), unrelated])

    assert any("完成条件尚未满足" in p for p in report.refused)
    assert w.st.model_dump_json() == before


def test_a_later_real_action_failure_rolls_back_the_effects_and_completion_together():
    w = planned(achieved="at:warden:square")
    before = w.st.model_dump_json()
    report = land(w, [completion(), move(), move("unknown_place")])

    assert report.refused and not report.landed and w.st.model_dump_json() == before
    assert not stage.waiting(w)


def test_reordering_completion_does_not_reorder_real_actions_or_expand_actor_permissions():
    w = planned(achieved="at:warden:square")
    before = w.st.model_dump_json()
    report = land(w, [completion(), {"kind": "move", "entity": "preacher", "to": "tower"}, move()])

    assert report.refused and w.st.model_dump_json() == before
    assert w.place("preacher") == "square"


def test_real_actions_keep_their_order_when_completion_is_deferred():
    w = planned(achieved="at:warden:tower")
    report = land(w, [completion(), move(), move("tower")])

    assert not report.refused and w.place("warden") == "tower"
    assert report.landed == ["decision:1", "decision:2", "decision:0"]
    assert w.agent("warden").plan[-1].status == "done"


def test_a_staged_completion_waits_for_play_and_recovery_does_not_repeat_it():
    w = planned(location="square", achieved="at:warden:square")
    report = land(w, [completion(), move()])

    assert not report.refused and len(report.staged) == 1 and not report.landed
    assert w.place("warden") == "tower" and w.agent("warden").plan[-1].status == "pending"
    restored = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    [beat] = stage.waiting(restored)
    assert [op["id"] for op in beat.ops] == ["decision:1", "decision:0"]
    assert not stage.play(restored, beat)
    assert restored.place("warden") == "square" and restored.agent("warden").plan[-1].status == "done"
    count = restored.rec.seq
    again = World(restored.m.model_copy(deep=True), GameState.model_validate_json(restored.st.model_dump_json()))
    assert not stage.play(again, again.rec.beats[0])
    assert again.rec.seq == count and len([e for e in again.rec.events if e.kind == "step"]) == 1


def test_a_staged_action_that_becomes_invalid_does_not_complete_the_plan():
    w = planned(location="square", achieved="at:warden:square")
    assert not land(w, [completion(), move()]).refused
    [beat] = stage.waiting(w)
    assert w.apply(O.Condition(entity="warden", status="死亡", actor="director")).ok

    assert stage.play(w, beat) and beat.status == "dropped"
    assert w.place("warden") == "tower" and w.agent("warden").plan[-1].status == "pending"


@pytest.mark.parametrize("checkpoint_status", ["ready", "done"])
async def test_interrupted_decision_recovers_the_saved_result_without_another_call_or_completion(checkpoint_status):
    w = planned(achieved="at:warden:square")
    w.agent("warden").wake = w.now
    w.agent("preacher").passive = True
    brain = Canned(lambda ask: Scene(decisions=[Decision(npc="warden", ops=[completion(), move()])]))
    saved = ""

    def checkpoint():
        nonlocal saved
        jobs = [job for work in w.rec.npc_work.values() for batch in work["batches"].values() for job in batch]
        if any(job["status"] == checkpoint_status for job in jobs):
            saved = w.st.model_dump_json()
            raise RuntimeError("disconnected")

    start, end = w.now, w.now + 1
    with pytest.raises(RuntimeError, match="disconnected"):
        await scheduler.advance(w, brain, start, end, checkpoint=checkpoint)
    restored = World(w.m.model_copy(deep=True), GameState.model_validate_json(saved))
    no_call = Canned(lambda ask: pytest.fail("a saved decision must not be requested again"))
    report = await scheduler.advance(restored, no_call, start, end)

    assert not report.refused and len(brain.asks) == 1 and not no_call.asks
    assert restored.place("warden") == "square" and restored.agent("warden").plan[-1].status == "done"
    assert len([e for e in restored.rec.events if e.kind == "step"]) == 1
    again = await scheduler.advance(restored, no_call, start, end)
    assert again.repeat and len([e for e in restored.rec.events if e.kind == "step"]) == 1


def test_gateway_preview_and_commit_use_the_same_order_and_replay_receipt():
    w = planned(achieved="at:warden:square")
    gateway = Gateway(w, "warden")
    ops = [completion(), move()]
    before = w.st.model_dump_json()
    preview = gateway.preview(ops)

    assert preview["ok"] and preview["plans"]["warden"][-1]["status"] == "done"
    assert w.st.model_dump_json() == before
    result = gateway.commit(ops, reads=preview["reads"], request_id="visit")
    assert result["ok"] and w.place("warden") == "square"
    assert [op["id"].rsplit(":", 1)[1] for op in result["ops"]] == ["1", "0"]
    seq = w.rec.seq
    again = Gateway(w, "warden").commit(ops, reads=preview["reads"], request_id="visit")
    assert again["ok"] and again["repeat"] and w.rec.seq == seq


def test_stage_commit_uses_the_same_completion_order_for_an_explicit_batch():
    w = planned(achieved="at:warden:square")
    raw = [{**op, "id": f"direct:{i}", "actor": "warden"} for i, op in enumerate([completion(), move()])]

    assert not stage.commit(w, "warden", raw)
    assert w.place("warden") == "square" and w.agent("warden").plan[-1].status == "done"


def commit_batch(w: World, ops: list[dict], entry: str) -> None:
    if entry == "scheduler":
        assert not land(w, ops).refused
    elif entry == "gateway":
        result = Gateway(w, "warden").settle(ops, request_id="replace-plan")
        assert result["ok"], result
    else:
        raw = [{**op, "id": f"replace-plan:{i}", "actor": "warden"} for i, op in enumerate(ops)]
        assert not stage.commit(w, "warden", raw)


@pytest.mark.parametrize("entry", ["scheduler", "gateway", "stage"])
@pytest.mark.parametrize("order", ["move-done-plan", "done-move-plan", "plan-done-move"])
def test_replacing_steps_is_a_completion_boundary_for_each_plan_version(entry, order):
    w = planned(achieved="at:warden:square")
    if order == "plan-done-move":
        ops = [
            {"kind": "plan", "npc": "warden", "steps": [
                {"id": "new_visit", "text": "下楼去广场", "achieved": "at:warden:square"},
            ]},
            {**completion(), "step": "new_visit"},
            move(),
        ]
    else:
        replace = {"kind": "plan", "npc": "warden", "steps": [
            {"id": "return", "text": "检查完返回钟楼", "achieved": "at:warden:tower"},
        ]}
        ops = [move(), completion(), replace] if order == "move-done-plan" else [completion(), move(), replace]

    commit_batch(w, ops, entry)

    assert w.place("warden") == "square"
    statuses = {s.id: s.status for s in w.agent("warden").plan}
    if order == "plan-done-move":
        assert statuses == {"visit": "dropped", "new_visit": "done"}
    else:
        assert statuses == {"visit": "done", "return": "pending"}


@pytest.mark.parametrize("entry", ["scheduler", "gateway", "stage"])
def test_changing_only_goals_does_not_prevent_same_batch_completion(entry):
    w = planned(achieved="at:warden:square")
    commit_batch(w, [completion(), {"kind": "plan", "npc": "warden", "goals": ["先下楼再回去"]}, move()], entry)

    assert w.place("warden") == "square" and w.agent("warden").plan[-1].status == "done"
    assert w.agent("warden").goals == ["先下楼再回去"]
