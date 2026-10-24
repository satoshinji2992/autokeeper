"""World conditions use effective game time, independently of submission order."""

from __future__ import annotations

import pytest

from autokeeper.engine.game import Turn
from autokeeper.module.schema import NPC
from autokeeper.world import ops as O
from autokeeper.world import scheduler
from autokeeper.world.agents import holds
from autokeeper.world.record import Step
from autokeeper.world.tools import Gateway

from .test_scene_runtime import made
from .test_world import staged


async def test_a_future_flag_cannot_start_a_fixed_plan_at_an_earlier_boundary():
    w = staged("office")
    start = w.now
    assert w.apply(O.SetPlan(npc="warden", actor="keeper", steps=[Step(
        id="only_after_bell", text="钟停后去广场", due=start, when="flag:bell_stopped", meet=False,
        ops=[{"kind": "move", "entity": "warden", "to": "square"}], achieved="at:warden:square",
    )])).ok
    with w.at(start + 120):
        assert w.apply(O.SetFlag(flag="bell_stopped", actor="keeper")).ok
    scheduler.collect(w)
    assert not any("钟停后去广场" in need.text for need in scheduler.pending(w))
    w.st.minutes = start + 10
    await scheduler.advance(w, start, start + 10, tick="before-bell-stops")
    assert w.place("warden") == "tower"
    assert not any(event.kind == "move" and event.actor == "warden" for event in w.rec.events)
    w.st.minutes = start + 120
    await scheduler.advance(w, start + 10, start + 120, tick="when-bell-stops")
    assert w.place("warden") == "square"
    move, = (event for event in w.rec.events if event.kind == "move" and event.actor == "warden")
    assert move.t == start + 120


@pytest.mark.parametrize("operation,before,after", [
    (O.SetFlag(flag="bell_stopped", actor="keeper"), "!flag:bell_stopped", "flag:bell_stopped"),
    (O.Transfer(item="coat", to="zhouheng", actor="keeper"), "owner:coat:warden", "owner:coat:zhouheng"),
    (O.Learn(fact="f_key", who=["preacher"], actor="warden", visibility="private"),
     "!knows:preacher:f_key", "knows:preacher:f_key"),
    (O.Occur(text="信使把钥匙的位置告诉布道者。", place="square", participants=["preacher"], facts=["f_key"],
             actor="engine", visibility="private"), "!knows:preacher:f_key", "knows:preacher:f_key"),
    (O.Fulfil(promise="p1", actor="keeper"), "promise:p1:active", "promise:p1:fulfilled"),
    (O.Move(entity="warden", to="square", actor="keeper"), "at:warden:tower", "at:warden:square"),
    (O.Condition(entity="warden", status="死亡", actor="keeper"), "!gone:warden", "gone:warden"),
])
def test_six_world_condition_kinds_read_their_state_at_the_effective_time(operation, before, after):
    w = staged("office")
    start = w.now
    with w.at(start + 120):
        assert w.apply(operation).ok
    with w.at(start):
        assert holds(w, before)
        assert not holds(w, after)
    with w.at(start + 119):
        assert holds(w, before)
        assert not holds(w, after)
    with w.at(start + 120):
        assert not holds(w, before)
        assert holds(w, after)


@pytest.mark.parametrize("operation,negated", [
    (O.AddFact(fact="later_fact", text="后来才确立的事实", known_by=["warden"], actor="keeper"),
     "!knows:preacher:later_fact"),
    (O.MakePromise(promise="later_promise", by="warden", to="zhouheng", text="后来才作出的承诺", actor="keeper"),
     "!promise:later_promise:fulfilled"),
    (O.Bring(item="later_hanger", name="折叠衣架", holder="zhouheng", actor="keeper"),
     "!owner:later_hanger:warden"),
])
def test_negating_a_condition_about_an_object_not_yet_created_does_not_make_it_true(operation, negated):
    w = staged("office")
    start = w.now
    with w.at(start + 120):
        assert w.apply(operation).ok
    with w.at(start):
        assert not holds(w, negated)
        assert not holds(w, negated + " & !flag:bell_stopped")
    with w.at(start + 120):
        assert holds(w, negated)


@pytest.mark.parametrize("on", [False, True])
def test_an_earlier_write_cannot_replace_or_reassert_a_later_committed_condition(on):
    w = staged("office")
    start = w.now
    with w.at(start + 120):
        assert w.apply(O.SetFlag(id="future-on", flag="bell_stopped", actor="keeper")).ok
    before = w.st.model_dump_json()
    with w.at(start + 30):
        result = w.apply(O.SetFlag(id="earlier-write", flag="bell_stopped", on=on, actor="engine"))
        assert not result.ok
    assert w.st.model_dump_json() == before and "bell_stopped" in w.st.flags
    with w.at(start + 60):
        assert not holds(w, "flag:bell_stopped")
    with w.at(start + 120):
        assert holds(w, "flag:bell_stopped")


def test_scoped_flag_query_reports_the_value_at_the_asking_scenes_time():
    w = staged("office")
    start = w.now
    with w.at(start + 120):
        assert w.apply(O.SetFlag(flag="bell_stopped", actor="keeper")).ok
    result = Gateway(w, "keeper").query(scope=["flag:bell_stopped"], compact=True)
    assert result["flags"] == {"bell_stopped": False}
    with w.at(start + 120):
        result = Gateway(w, "keeper").query(scope=["flag:bell_stopped"], compact=True)
        assert result["flags"] == {"bell_stopped": True}


async def test_a_past_scene_tool_cannot_overwrite_a_flag_already_committed_in_the_future(fake):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)
    try:
        ga.state.minutes = 720
        assert ga.world.apply(O.SetFlag(id="leading-scene-bell", flag="bell_stopped", actor="keeper")).ok
        assert gb.state.minutes == 600
        gateway = Gateway(gb.world, "keeper", scene_context=gb._scene_context(Turn()))
        before = gb.state.record.model_dump_json()
        result = gateway.settle([{"kind": "flag", "flag": "bell_stopped", "on": False}],
                                request_id="lagging-scene-cancel-bell")
        assert not result["ok"]
        assert gb.state.record.model_dump_json() == before
        with gb.world.at(720):
            assert holds(gb.world, "flag:bell_stopped")
    finally:
        await runtime.close()


def test_repeated_explicit_syncs_at_the_same_time_use_the_last_value():
    w = staged("office")
    start, seq = w.now, w.rec.seq
    w.st.flags.append("bell_stopped")
    w.sync()
    assert holds(w, "flag:bell_stopped")
    w.st.flags.remove("bell_stopped")
    w.sync()
    assert w.now == start and w.rec.seq == seq
    assert holds(w, "!flag:bell_stopped")
    assert not holds(w, "flag:bell_stopped")


def test_an_uncreated_recipient_does_not_make_a_negated_ownership_condition_true():
    w = staged("office")
    start = w.now
    with w.at(start + 120):
        w.m.npcs.append(NPC(id="later_keeper", name="后来的保管人", role="保管物件", location="square"))
        w.sync()
    with w.at(start):
        assert not holds(w, "owner:coat:later_keeper")
        assert not holds(w, "!owner:coat:later_keeper")
    with w.at(start + 120):
        assert holds(w, "!owner:coat:later_keeper")
