"""Condition values keep their effective game time, not the latest mutable world snapshot."""

from __future__ import annotations

import pytest

from autokeeper.engine import effects
from autokeeper.engine.state import GameState
from autokeeper.module.schema import NPC
from autokeeper.world import conditions, scheduler
from autokeeper.world import ops as O
from autokeeper.world.agents import holds
from autokeeper.world.record import Step
from autokeeper.world.store import World

from .test_world import staged


def test_current_values_and_boolean_composition_keep_normal_condition_behavior():
    w = staged("office")
    assert holds(w, "")
    assert holds(w, "!flag:bell_stopped & owner:coat:warden")
    assert holds(w, "knows:warden:f_key & !knows:preacher:f_key")
    assert holds(w, "promise:p1:active & !gone:warden")
    assert holds(w, "at:warden:tower | flag:bell_stopped")
    assert not holds(w, "gone:warden | flag:bell_stopped")
    assert w.apply(O.SetFlag(flag="bell_stopped")).ok
    assert holds(w, "flag:bell_stopped") and not holds(w, "!flag:bell_stopped")
    assert w.rec.conditions["flag:bell_stopped"][0].seed
    assert not w.rec.conditions["flag:bell_stopped"][-1].seed


async def test_future_flag_does_not_start_past_work_but_runs_when_its_time_arrives():
    w = staged("office")
    start = w.now
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(
        id="after_stop", text="钟停后到广场", when="flag:bell_stopped", due=start, meet=False,
        ops=[{"kind": "move", "entity": "warden", "to": "square"}], achieved="at:warden:square",
    )])).ok
    with w.at(start + 120):
        assert w.apply(O.SetFlag(flag="bell_stopped", actor="keeper")).ok
    scheduler.collect(w)
    assert not [need for need in scheduler.pending(w) if "钟停后到广场" in need.text]
    w.st.minutes = start + 10
    await scheduler.advance(w, start, start + 10, tick="past-before-stop")
    assert w.place("warden") == "tower"
    w.st.minutes = start + 120
    await scheduler.advance(w, start + 10, start + 120, tick="bell-has-stopped")
    assert w.place("warden") == "square"
    actual = [event for event in w.rec.events if event.kind == "move" and event.actor == "warden"]
    assert len(actual) == 1 and actual[0].t == start + 120


@pytest.mark.parametrize("same_value", [False, True])
def test_old_write_cannot_replace_or_backdate_a_future_assignment_and_rolls_back_its_event(same_value):
    w = staged("office")
    start = w.now
    with w.at(start + 120):
        assert w.apply(O.SetFlag(id="future", flag="bell_stopped")).ok
    before = w.st.model_dump_json()
    with w.at(start + 20):
        refused = w.apply(O.SetFlag(id="too-early", flag="bell_stopped", on=same_value))
        assert not refused.ok and any("未来条件" in problem for problem in refused.problems)
    assert w.st.model_dump_json() == before
    with w.at(start + 60):
        assert holds(w, "!flag:bell_stopped")
    with w.at(start + 120):
        assert holds(w, "flag:bell_stopped")


def test_late_submission_of_an_unrelated_past_action_does_not_backdate_shared_future_values():
    w = staged("office")
    start = w.now
    with w.at(start + 120):
        assert w.apply(O.SetFlag(flag="bell_stopped")).ok
    with w.at(start + 30):
        assert w.apply(O.Transfer(item="coat", to="tower", actor="keeper")).ok
    with w.at(start + 60):
        assert holds(w, "owner:coat:tower")
        assert holds(w, "!flag:bell_stopped")
    assert [row.t for row in w.rec.conditions["flag:bell_stopped"]] == [start, start + 120]


def test_unknown_future_objects_do_not_become_known_negatives_but_true_or_branch_can_still_hold():
    w = staged("office")
    start = w.now
    with w.at(start + 120):
        w.m.npcs.append(NPC(id="later_witness", name="晚到的证人", role="来报信的人", location="square"))
        w.sync()
        assert w.apply(O.AddFact(fact="later_fact", text="证人后来才带来的口信", known_by=["later_witness"])).ok
    with w.at(start):
        for condition in ("at:later_witness:square", "!at:later_witness:square", "gone:later_witness",
                          "!gone:later_witness", "knows:warden:later_fact", "!knows:warden:later_fact"):
            assert not holds(w, condition)
        assert holds(w, "!gone:later_witness | !flag:bell_stopped")
        assert not holds(w, "!gone:later_witness & !flag:bell_stopped")
    with w.at(start + 120):
        assert holds(w, "at:later_witness:square & !gone:later_witness")
        assert holds(w, "knows:later_witness:later_fact & !knows:warden:later_fact")


@pytest.mark.parametrize("operation,condition", [
    (O.SetFlag(flag="bell_stopped"), "flag:bell_stopped"),
    (O.Learn(fact="f_key", who=["preacher"], actor="warden", visibility="private"), "knows:preacher:f_key"),
    (O.Transfer(item="coat", to="tower", actor="keeper"), "owner:coat:tower"),
    (O.Fulfil(promise="p1", actor="keeper"), "promise:p1:fulfilled"),
    (O.Condition(entity="warden", status="死亡", actor="keeper"), "gone:warden"),
])
def test_condition_history_is_in_the_same_undo_transaction_as_its_operation(operation, condition):
    w = staged("office")
    before = w.domain()
    assert w.apply(operation).ok and holds(w, condition)
    assert not w.undo()
    assert w.domain() == before
    assert not holds(w, condition)


def test_failed_operation_retains_no_history_and_history_survives_save_without_event_or_undo_text():
    w = staged("office")
    start = w.now
    before = w.st.model_dump_json()
    assert not w.apply(O.SetFlag(flag="not_declared")).ok
    assert w.st.model_dump_json() == before
    with w.at(start + 10):
        assert w.apply(O.SetFlag(flag="bell_stopped")).ok
    expected = w.rec.conditions["flag:bell_stopped"][-1].model_dump()
    w.rec.events.clear()
    for entry in w.rec.journal:
        entry.undo = None
    loaded = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    assert loaded.rec.conditions["flag:bell_stopped"][-1].model_dump() == expected
    with loaded.at(start + 9):
        assert holds(loaded, "!flag:bell_stopped")
    with loaded.at(start + 10):
        assert holds(loaded, "flag:bell_stopped")


def test_explicit_current_sync_records_fixture_or_import_changes_without_reseeding_history_on_reload():
    w = staged("office")
    start = w.now
    w.st.minutes += 15
    w.rec.facts["f_key"].known_by.append("preacher")
    w.sync()
    with w.at(start):
        assert holds(w, "!knows:preacher:f_key")
    assert holds(w, "knows:preacher:f_key")
    history = w.rec.conditions["knows:preacher:f_key"]
    loaded = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    assert loaded.rec.conditions["knows:preacher:f_key"] == history
    assert conditions.value(loaded, "knows:preacher:f_key", at=start) is None


@pytest.mark.parametrize("trigger,when", [
    (O.SetFlag(flag="bell_stopped", actor="keeper"), "flag:bell_stopped"),
    (O.Move(entity="warden", to="square", actor="keeper"), "at:warden:square"),
])
async def test_long_calendar_span_visits_condition_and_motion_boundaries_before_the_grace_deadline(trigger, when):
    w = staged("tower")
    start = w.now
    assert w.apply(O.SetPlan(npc="preacher", actor="keeper", steps=[Step(
        id="react_on_time", text="条件成立就去报社", when=when, due=start, meet=False,
        ops=[{"kind": "move", "entity": "preacher", "to": "office"}], achieved="at:preacher:office",
    )])).ok
    with w.at(start + 60):
        assert w.apply(trigger).ok
    w.st.minutes = start + 200
    report = await scheduler.advance(w, start, start + 200, tick="span-containing-condition-change")
    assert not report.refused and w.place("preacher") == "office"
    actual = [event for event in w.rec.events if event.kind == "move" and event.actor == "preacher"]
    assert len(actual) == 1 and actual[0].t == start + 60


@pytest.mark.parametrize("operation", [
    O.Move(entity="warden", to="office", actor="keeper"),
    O.Effects(effects=[effects.Relocate(who="warden", place="office")], actor="engine"),
])
def test_late_movement_cannot_replace_a_future_motion_or_reverse_its_origin(operation):
    w = staged("office")
    start = w.now
    with w.at(start + 120):
        assert w.apply(O.Move(entity="warden", to="square", actor="keeper")).ok
    before = w.st.model_dump_json()
    with w.at(start + 30):
        result = w.apply(operation)
        assert not result.ok and any("position:warden" in problem for problem in result.problems)
    assert w.st.model_dump_json() == before
    with w.at(start):
        assert holds(w, "at:warden:tower")
    with w.at(start + 120):
        assert holds(w, "at:warden:square")


def test_implicit_future_knowledge_conflict_rolls_back_observation_images_and_their_module_aliases():
    w = staged("tower")
    start = w.now
    assert w.apply(O.AddFact(fact="future_testimony", text="后来才送来的口供", fact_kind="secret", actor="engine")).ok
    with w.at(start + 120):
        assert w.apply(O.Learn(fact="future_testimony", who=["warden"], actor="engine", visibility="private")).ok
    before = w.st.model_dump_json()
    artwork = {name: dict(getattr(w.m, name)) for name in ("art", "art_colors", "pictures")}
    with w.at(start + 30):
        result = w.apply(O.Occur(text="口供在众人面前交到老人手里。", place="tower", participants=["warden"],
                                facts=["future_testimony"], actor="engine", visibility="public"))
        assert not result.ok and any("knows:warden:future_testimony" in problem for problem in result.problems)
    assert w.st.model_dump_json() == before
    assert {name: dict(getattr(w.m, name)) for name in artwork} == artwork
