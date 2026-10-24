"""Deterministic world time and durable work for the same keeper."""

from __future__ import annotations

import pytest

from autokeeper.engine.state import GameState
from autokeeper.world import ops as O
from autokeeper.world import scheduler, stage
from autokeeper.world.record import Step
from autokeeper.world.store import World

from .test_world import staged


def report_plan(w: World, npc: str = "warden", **kwargs):
    assert w.apply(O.SetPlan(npc=npc, steps=[Step(id="review", text="整理本人账目", completion="report",
                                               **{"meet": False, **kwargs})])).ok


def tickets(needs):
    return {need.id: need.version for need in needs}


async def test_due_judgment_is_persistent_and_reading_it_is_not_acknowledgment():
    w = staged("office")
    report_plan(w, due=w.now)
    start = w.now
    report = await scheduler.advance(w, start, start + 1)
    [need] = [n for n in scheduler.pending(w) if n.npc == "warden" and n.kind == "due"]
    assert need.id in report.pending and w.agent("warden").plan[-1].status == "pending"
    restored = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    assert tickets(scheduler.pending(restored)) == tickets(scheduler.pending(w))
    await scheduler.advance(restored, start + 1, start + 2)
    assert restored.rec.backstage[need.id].version == need.version
    assert scheduler.acknowledge(restored, {need.id: need.version}) == [need.id]
    await scheduler.advance(restored, start + 2, start + 200)
    assert not [n for n in scheduler.pending(restored) if n.id == need.id]
    assert restored.agent("warden").plan[-1].status == "pending"  # considered is not completed


async def test_revising_one_event_updates_its_ticket_and_an_old_ack_cannot_consume_it():
    w = staged("office")
    assert w.apply(O.Occur(id="first", actor="engine", place="tower", participants=["warden"],
                          text="钟停了", visibility="private")).ok
    scheduler.collect(w)
    [old] = [n for n in scheduler.pending(w, ["warden"]) if n.kind == "affected" and "钟停了" in n.text]
    original = tickets([old])
    event = next(event for event in w.rec.events if event.op == "first")
    assert w.apply(O.Rewrite(target=f"event:{event.id}", text="有人故意停下了钟", reason="调整幕后", actor="keeper")).ok
    scheduler.collect(w)
    current = w.rec.backstage[old.id]
    assert current.version == original[old.id] + 1 and "故意停下" in current.text
    assert scheduler.acknowledge(w, original) == []
    assert current in scheduler.pending(w)
    scheduler.acknowledge(w, tickets([current]))
    scheduler.collect(w)
    assert not [need for need in scheduler.pending(w) if need.id == current.id]


async def test_new_events_have_separate_tickets_and_acknowledging_one_never_consumes_another():
    w = staged("office")
    assert w.apply(O.Occur(id="first", actor="engine", place="tower", participants=["warden"], text="钟停了")).ok
    scheduler.collect(w)
    [old] = [n for n in scheduler.pending(w, ["warden"]) if n.kind == "affected" and "钟停了" in n.text]
    original = tickets([old])
    assert w.apply(O.Occur(id="second", actor="engine", place="tower", participants=["warden"], text="窗外有人敲门")).ok
    scheduler.collect(w)
    fresh = [need for need in scheduler.pending(w, ["warden"]) if "敲门" in need.text]
    assert len(fresh) == 1 and "敲门" in fresh[0].text
    assert scheduler.acknowledge(w, original) == [old.id]
    assert fresh[0] in scheduler.pending(w)


async def test_person_selection_does_not_consume_other_peoples_changes():
    w = staged("office")
    for npc, place in (("warden", "tower"), ("preacher", "square")):
        assert w.apply(O.Occur(actor="engine", place=place, participants=[npc], text=f"{npc}听见响动")).ok
    await scheduler.advance(w, w.now, w.now, events_only=True)
    own = scheduler.pending(w, ["warden"])
    assert own and all(n.npc == "warden" for n in own)
    scheduler.acknowledge(w, tickets(own))
    assert all(n.npc == "preacher" for n in scheduler.pending(w))


async def test_fixed_plan_runs_without_a_decision_and_without_a_periodic_wake():
    w = staged("office")
    start = w.now
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="到广场", due=start + 10, meet=False,
        ops=[{"kind": "move", "entity": "warden", "to": "square"}], achieved="at:warden:square")])).ok
    report = await scheduler.advance(w, start, start + 15)
    assert not report.refused and w.place("warden") == "square"
    assert w.agent("warden").plan[-1].status == "done"
    assert next(e for e in w.rec.events if e.kind == "move").t == start + 10
    assert not [n for n in scheduler.pending(w) if n.npc == "warden" and n.kind == "due"]
    assert not hasattr(w.agent("warden"), "wake")


async def test_prepared_plan_and_arrangement_at_different_times_run_in_calendar_order():
    w = staged("office")
    start = w.now
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="到广场", due=start + 5, meet=False,
        ops=[{"kind": "move", "entity": "warden", "to": "square"}], achieved="at:warden:square")])).ok
    assert w.apply(O.Schedule(event="later", at=start + 10, actor="keeper", text="布道者在广场碰见守钟人", place="square",
                              participants=["warden", "preacher"])).ok
    report = await scheduler.advance(w, start, start + 60)
    assert report.fired == ["later"]
    actual = [(e.kind, e.t) for e in w.rec.events if e.kind in {"move", "occur"}]
    assert actual[-2:] == [("move", start + 5), ("occur", start + 10)]


async def test_due_work_affecting_players_waits_for_the_keeper_and_replay_is_idempotent():
    w = staged("square")
    start = w.now
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="到广场", due=start + 1, meet=False,
        ops=[{"kind": "move", "entity": "warden", "to": "square"}], achieved="at:warden:square")])).ok
    report = await scheduler.advance(w, start, start + 2)
    assert len(report.staged) == 1 and w.place("warden") == "tower"
    [beat] = stage.waiting(w)
    assert not stage.play(w, beat)
    seq = w.rec.seq
    assert (await scheduler.advance(w, start, start + 2)).repeat
    assert w.rec.seq == seq and not stage.waiting(w)


@pytest.mark.parametrize("interrupt_after", [1, 2])
async def test_interrupted_calendar_keeps_its_receipts_and_never_repeats_a_fixed_result(interrupt_after):
    w = staged("office")
    start = w.now
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="到广场", due=start + 1, meet=False,
        ops=[{"kind": "move", "entity": "warden", "to": "square"}], achieved="at:warden:square")])).ok
    saved, calls = "", 0

    def checkpoint():
        nonlocal saved, calls
        calls += 1
        if calls == interrupt_after:
            saved = w.st.model_dump_json()
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        await scheduler.advance(w, start, start + 2, checkpoint=checkpoint)
    assert w.clock is None
    restored = World(w.m.model_copy(deep=True), GameState.model_validate_json(saved))
    report = await scheduler.advance(restored, start, start + 2)
    assert not report.refused and restored.place("warden") == "square"
    assert len([e for e in restored.rec.events if e.kind == "move"]) == 1
    assert not restored.rec.schedule_work


async def test_stale_fixed_work_and_overdue_conditions_remain_visible_until_kp_considers_them():
    w = staged("office")
    start = w.now
    secret = "fact:npc:warden:secrets:0"
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="leave", text="离开钟楼", due=start, meet=False,
        ops=[{"kind": "move", "entity": "warden", "to": "square"}])], reads={secret: w.version(secret)})).ok
    assert w.apply(O.Rewrite(target=secret, text="藏着另一件东西", reason="调整幕后")).ok
    await scheduler.advance(w, start, start + 1)
    assert w.place("warden") == "tower"
    assert any(n.npc == "warden" and n.kind == "stale" for n in scheduler.pending(w))
    assert w.apply(O.SetPlan(npc="preacher", steps=[Step(id="wait", text="等钟停", due=start,
                                                       when="flag:bell_stopped", meet=False)])).ok
    await scheduler.advance(w, start + 1, start + scheduler.GRACE)
    w.st.minutes = start + scheduler.GRACE
    assert w.agent("preacher").plan[-1].status == "blocked"
    assert any(n.npc == "preacher" and n.kind == "blocked" for n in scheduler.pending(w))


async def test_due_promise_is_not_lost_even_when_no_keeper_reply_arrives():
    w = staged("tower")
    assert w.apply(O.MakePromise(promise="help", by="warden", to="players", text="稍后拿来账本", due=w.now + 1)).ok
    await scheduler.advance(w, w.now, w.now + 5)
    w.st.minutes += 5
    [need] = [n for n in scheduler.pending(w) if n.kind == "promise"]
    assert need.source == "promise:help"
    restored = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    await scheduler.advance(restored, restored.now + 5, restored.now + 10)
    assert restored.rec.backstage[need.id].status == "pending"


async def test_a_meeting_reaction_is_character_material_not_backstage_work():
    w = staged("office")
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="greeting", text="领调查员进屋", meet=True)])).ok
    await scheduler.advance(w, w.now, w.now + 300, moved_on=True)
    assert not [n for n in scheduler.pending(w) if n.npc == "warden" and n.kind == "due"]
    assert w.agent("warden").plan[-1].status == "pending"


async def test_schedule_bookkeeping_and_attention_receipts_do_not_break_operation_undo():
    w = staged("office")
    assert w.apply(O.Move(entity="warden", to="square", id="undo-me")).ok
    report_plan(w)
    await scheduler.advance(w, w.now, w.now + 1)
    scheduler.acknowledge(w, tickets(scheduler.pending(w)))
    assert not w.undo()
    assert w.agent("warden").plan == []
