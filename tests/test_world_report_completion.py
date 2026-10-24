"""A private work report can finish a report step, never manufacture a physical outcome."""

from __future__ import annotations

import pytest

from autokeeper.engine.state import GameState
from autokeeper.world import context, scheduler, stage
from autokeeper.world import ops as O
from autokeeper.world.agents import meeting
from autokeeper.world.record import Step
from autokeeper.world.store import World
from autokeeper.world.tools import Gateway

from .test_world import staged

NOTE = "按日期复核了本人账面，整理出两笔需要下次核对的差额；尚未调查差额来源"


def planned(location="office") -> World:
    w = staged(location)
    result = Gateway(w, "warden").settle([{
        "kind": "plan", "npc": "warden", "steps": [
            {"id": "review", "text": "回顾来访者的问题并整理本人账面", "completion": "report"},
        ],
    }], request_id="install-report-plan")
    assert result["ok"], result
    w.agent("preacher").passive = True
    return w


def done(note=NOTE):
    return {"kind": "step", "npc": "warden", "step": "review", "status": "done", "note": note}


def physical(w):
    return {
        "entities": {key: value.model_dump(mode="json") for key, value in w.rec.entities.items()},
        "facts": {key: value.model_dump(mode="json") for key, value in w.rec.facts.items()},
        "flags": list(w.st.flags), "clues": list(w.st.clues), "handouts": list(w.st.handouts),
        "items": dict(w.st.item_owners), "legacy_facts": dict(w.st.world),
        "players": [i.model_dump(mode="json") for i in w.st.investigators],
        "player_log": [e.model_dump(mode="json") for e in w.st.log],
    }


@pytest.mark.parametrize("entry", ["gateway", "scheduler", "stage"])
def test_report_finishes_privately_without_any_world_or_player_result(entry):
    w = planned(location="tower")  # even beside the investigators, this report is not a public occurrence
    before, since = physical(w), w.rec.seq
    if entry == "gateway":
        gateway = Gateway(w, "warden")
        preview = gateway.preview([done()])
        assert preview["ok"] and w.agent("warden").plan[-1].status == "pending"
        result = gateway.commit([done()], reads=preview["reads"], request_id="report-done")
        assert result["ok"], result
    elif entry == "scheduler":
        report = scheduler.Report(tick="report-done")
        assert scheduler._land(w, [done()], prefix="report-done", actor="warden", report=report)
        assert not report.refused and not report.staged
    else:
        assert not stage.commit(w, "warden", [{**done(), "id": "report-done", "actor": "warden"}])
    assert physical(w) == before and not context.happened(w, since)
    step = w.agent("warden").plan[-1]
    assert step.completion == "report" and step.status == "done" and step.note == NOTE
    [event] = [e for e in w.rec.events if e.seq > since]
    assert event.kind == "step" and "报告完成" in event.text
    assert event.visibility == "private" and event.witnesses == ["warden"]
    assert not event.facts and not event.trace
    assert NOTE not in context.keeper_view(w)
    assert not any(NOTE in memory.text for memory in w.agent("preacher").memory)


@pytest.mark.parametrize("extra", [
    {"achieved": "at:warden:square"},
    {"ops": [{"kind": "move", "entity": "warden", "to": "square"}]},
])
def test_report_cannot_declare_physical_criteria_or_fixed_operations(extra):
    w = staged()
    before = w.st.model_dump_json()
    result = Gateway(w, "warden").settle([{
        "kind": "plan", "npc": "warden", "steps": [
            {"id": "review", "text": "整理账面", "completion": "report", **extra},
        ],
    }], request_id="invalid-report-plan")
    assert not result["ok"] and any("report" in p and "state" in p for p in result["problems"])
    assert w.st.model_dump_json() == before


@pytest.mark.parametrize("note", ["", "  \n "])
def test_report_requires_a_note_and_does_not_use_its_old_note_as_evidence(note):
    w = planned()
    w.agent("warden").plan[-1].note = NOTE
    before = w.st.model_dump_json()
    result = Gateway(w, "warden").settle([done(note)], request_id="empty-note")
    assert not result["ok"] and any("具体 note" in p for p in result["problems"])
    assert w.st.model_dump_json() == before


def test_another_npc_cannot_report_for_the_owner_or_impersonate_staff():
    w = planned()
    before = w.st.model_dump_json()
    assert not Gateway(w, "preacher").settle([done()], request_id="others-report")["ok"]
    assert not Gateway(w, "preacher").settle([{**done(), "actor": "keeper"}], request_id="spoofed-staff")["ok"]
    assert stage.commit(w, "preacher", [{**done(), "actor": "preacher", "id": "wrong-owner"}])
    result = w.apply(O.AdvancePlan(**{k: v for k, v in done().items() if k != "kind"}, actor="preacher"))
    assert not result.ok and any("自己的" in p for p in result.problems)
    assert w.st.model_dump_json() == before


@pytest.mark.parametrize("actor", O.STAFF)
def test_existing_staff_can_record_private_work_without_creating_external_facts(actor):
    w = planned(location="tower")
    before, since = physical(w), w.rec.seq
    result = Gateway(w, actor).settle([done()], request_id=f"staff-record:{actor}")
    assert result["ok"], result
    assert w.agent("warden").plan[-1].status == "done" and w.agent("warden").plan[-1].note == NOTE
    assert physical(w) == before and not context.happened(w, since)
    [event] = [e for e in w.rec.events if e.seq > since]
    assert "报告完成" in event.text and event.visibility == "private" and not event.facts
    assert event.witnesses == ["warden"] and NOTE not in context.keeper_view(w)


@pytest.mark.parametrize("achieved", ["", "at:warden:square", "owner:coat:square"])
def test_report_event_cannot_prove_a_separate_state_step(achieved):
    w = planned()
    assert w.apply(O.SetPlan(npc="warden", steps=[
        Step(id="review", text="整理账面", completion="report"),
        Step(id="actual", text="下楼把雨衣放到广场", achieved=achieved, meet=False),
    ])).ok
    assert Gateway(w, "warden").settle([done()], request_id="report-first")["ok"]
    before = w.st.model_dump_json()
    result = Gateway(w, "warden").settle([{
        "kind": "step", "npc": "warden", "step": "actual", "status": "done", "note": "已经下楼交物",
    }], request_id="fake-physical-completion")
    assert not result["ok"] and w.st.model_dump_json() == before
    assert w.place("warden") == "tower" and w.rec.entities["coat"].at == "warden"


async def test_explicit_report_survives_save_and_is_not_blocked_as_an_unverified_legacy_state_plan():
    w = planned()
    # The explicit type is authoritative even without an installation sequence;
    # do not guess it from words, and do not rewrite plans missing the type.
    w.agent("warden").plan[-1].started = 0
    w = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    assert not meeting(w, "warden", w.agent("warden").plan[-1])
    before = physical(w)
    report = await scheduler.advance(w, w.now, w.now + 1)
    assert not report.refused and not report.blocked
    assert any(n.npc == "warden" and n.kind == "due" for n in scheduler.pending(w))
    assert w.agent("warden").plan[-1].status == "pending" and physical(w) == before
    assert Gateway(w, "keeper").settle([done()], request_id="keeper-review")["ok"]
    assert w.agent("warden").plan[-1].status == "done" and physical(w) == before


def test_report_receipt_keeps_real_operation_order_and_plan_replacement_boundary():
    w = planned()
    result = Gateway(w, "warden").settle([
        done(),
        {"kind": "move", "entity": "warden", "to": "square"},
        {"kind": "move", "entity": "warden", "to": "tower"},
        {"kind": "plan", "npc": "warden", "steps": [{"id": "next", "text": "稍后再核账", "completion": "report"}]},
    ], request_id="report-then-replan")
    assert result["ok"], result
    assert [op["id"].rsplit(":", 1)[1] for op in result["ops"]] == ["1", "2", "0", "3"]
    assert w.place("warden") == "tower"
    assert [(s.id, s.status) for s in w.agent("warden").plan] == [("review", "done"), ("next", "pending")]


def test_missing_completion_type_keeps_state_rules_and_does_not_infer_report_from_words():
    w = staged()
    step = Step.model_validate({"id": "review", "text": "整理账面，回顾工作，思考下一步", "meet": False})
    assert step.completion == "state"
    assert w.apply(O.SetPlan(npc="warden", steps=[step])).ok
    result = Gateway(w, "warden").settle([done()], request_id="implicit-report-refused")
    assert not result["ok"] and any("completion=report" in p for p in result["problems"])


def test_report_type_reaches_the_existing_character_and_planning_contexts():
    w = planned(location="tower")
    for text in (context.npc_view(w, "warden").text, context.npc_brief(w, "warden"), context.director_view(w).text):
        assert "completion=report" in text and "不证明外部结果" in text
    assert "report" not in context.keeper_view(w)


def test_step_completion_is_exposed_in_existing_decision_and_tool_schemas():
    w = planned()
    schemas = [Gateway(w, "keeper").schema()["operations"], Gateway(w, "warden").schema()["operations"]]
    for schema in schemas:
        completion = schema["$defs"]["Step"]["properties"]["completion"]
        assert completion["enum"] == ["state", "report"] and completion["default"] == "state"
    with pytest.raises(ValueError):
        Step.model_validate({"id": "review", "text": "整理账面", "completion": "auto"})


@pytest.mark.parametrize("reload", [False, True])
def test_completed_report_note_reaches_only_its_owner_after_replanning_and_reload(reload):
    w = planned(location="tower")
    assert Gateway(w, "warden").settle([done()], request_id="completed-private-report")["ok"]
    assert Gateway(w, "warden").settle([{
        "kind": "plan", "npc": "warden", "steps": [
            {"id": "next", "text": "下次核对账面差额的来源", "completion": "report"},
        ],
    }], request_id="next-private-task")["ok"]
    if reload:
        w = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    for text in (context.npc_view(w, "warden").text, context.npc_brief(w, "warden")):
        assert NOTE in text and "本人工作报告，未经外部证实" in text
    for text in (context.npc_view(w, "preacher").text, context.npc_brief(w, "preacher"), context.keeper_view(w)):
        assert NOTE not in text
    assert not any(NOTE in fact.text for fact in w.rec.facts.values())
