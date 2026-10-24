"""What wakes an important NPC backstage (world/scheduler.py): not a conversation it has already
answered, not what it does on meeting the investigators (agents.meeting), and planning anew only
when time jumps or the party moves on."""

from __future__ import annotations

import pytest

from autokeeper.module.schema import Agenda, Module, Move
from autokeeper.paths import EXAMPLE
from autokeeper.world import agents, context, scheduler
from autokeeper.world import ops as O
from autokeeper.world.record import Step

from .test_world import fresh, staged


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


async def test_what_it_does_on_meeting_the_investigators_never_wakes_it():
    w = staged()
    greet = Step(id="greet", text="有人上门就请他们进屋喝茶", meet=True)
    assert w.apply(O.SetPlan(npc="warden", steps=[greet])).ok
    report = await scheduler.advance(w, w.now, w.now + 30)
    assert not [n for n in scheduler.pending(w, ["warden"]) if n.kind == "due"]
    assert not report.blocked
    assert w.agent("warden").plan[0].status == "pending"  # character material: never done, never blocked
    assert "请他们进屋喝茶" not in context.npc_view(w, "warden").text  # not a backstage step


def test_its_voice_hears_what_it_does_on_meeting_them_while_they_are_there():
    w = staged(location="tower")
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="greet", text="有人上门就请他们进屋喝茶", meet=True),
                                                  Step(id="oil", text="给齿轮上油", meet=False)])).ok
    brief = context.npc_brief(w, "warden")
    assert "【见到调查员时，你会】有人上门就请他们进屋喝茶" in brief and "【你正打算】给齿轮上油" in brief
    away = staged(location="office")
    assert away.apply(O.SetPlan(npc="warden", steps=[Step(id="greet", text="有人上门就请他们进屋喝茶", meet=True)])).ok
    assert "见到调查员时" not in context.npc_brief(away, "warden")


def test_a_step_from_before_the_label_reads_it_from_the_module_then_from_its_words():
    w = staged()
    w.m.agendas = [Agenda(npc="warden", steps=[Move(id="door", text="应门，把人领到钟楼下", meet=True),
                                               Move(id="watch", text="夜里盯着广场", meet=False)])]
    old = [Step(id="door", text="应门，把人领到钟楼下"), Step(id="watch", text="夜里盯着广场"),
           Step(id="ask", text="等调查员来问起钟声时，讲那晚的事"), Step(id="tail", text="悄悄跟着调查员，看他们去了哪")]
    assert [agents.meeting(w, "warden", s) for s in old] == [True, False, True, False]
    timed = Step(id="ask", text="等调查员来问起钟声时，讲那晚的事", due=w.now + 30)
    assert agents.meeting(w, "warden", timed) is False  # unlabeled with a time: backstage, as before the label
    later = Step(id="later", text="傍晚问调查员进展", due=w.now + 30, meet=True)
    assert agents.meeting(w, "warden", later) and not agents.meets(w, "warden", later)  # its time has not come
    done = Step(id="go", text="下楼", meet=True, ops=[{"kind": "move", "entity": "warden", "to": "square"}])
    assert agents.meeting(w, "warden", done) is False  # an outcome of its own is always backstage work


def test_the_module_marks_its_meeting_steps_when_the_game_starts(module):
    module.agendas = [Agenda(npc="warden", goals=["守住钟楼"], steps=[
        Move(id="door", text="应门招呼来人", meet=True),
        Move(id="lock", text="半夜锁上齿轮间", when="D1 23:00", does="at:warden:tower"),
    ])]
    w = fresh()
    w.m = module
    for op in agents.marked(w):
        assert w.apply(op).ok
    door, lock = w.agent("warden").plan
    assert door.meet is True and not door.ops and lock.meet is None and lock.ops


def test_what_it_did_on_meeting_them_is_not_put_to_it_again():
    """Thomas answered the door and settled their bags: that is done, not a standing order for every
    later talk (the review of 2026-10-20). Done only when its voice says it played it."""
    from autokeeper.world.adapters import reply_ops
    from autokeeper.world.tools import Gateway

    w = staged(location="tower")
    later = Step(id="later", text="傍晚问调查员进展", due=w.now + 30, meet=True)
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="greet", text="有人上门就请他们进屋喝茶", meet=True), later])).ok
    assert "喝茶（greet）" in context.npc_brief(w, "warden")
    groups, problems = reply_ops(w, "warden", played=["greet", "later", "nothing"])  # not yet time for later; no such step
    assert not problems and groups == [[{"kind": "step", "npc": "warden", "step": "greet", "status": "done"}]]
    assert Gateway(w, "warden").settle(groups[0], request_id="reply:1:warden:0:0").get("ok")
    assert w.agent("warden").plan[0].status == "done" and "喝茶" not in context.npc_brief(w, "warden")


async def test_a_step_at_an_hour_waits_for_its_condition_as_well(module):
    """「D1 22:00 & flag:bell_stopped」: at ten, and only once the bell has stopped. The compiler cut
    it down to the hour, and the warden went at ten with the bell still going (the review of 2026-10-20)."""
    from autokeeper.engine.state import parse_time

    from .test_world import fresh

    module.agendas = [Agenda(npc="warden", goals=["守住钟楼"], steps=[
        Move(id="go_down", text="钟一停就下楼去广场", when="D1 22:00 & flag:bell_stopped", does="at:warden:square")])]
    w = fresh("office")  # out of the party's sight: his going lands, it does not wait for the keeper to tell it
    w.m = module
    for op in agents.marked(w):
        assert w.apply(op).ok
    [step] = w.agent("warden").plan
    assert step.due == parse_time("D1 22:00") and step.when == "flag:bell_stopped" and step.ops
    w.st.minutes = parse_time("D1 22:30")
    await scheduler.advance(w, parse_time("D1 21:50"), w.now)
    assert w.place("warden") == "tower"  # ten has come, the bell has not stopped
    assert w.apply(O.SetFlag(flag="bell_stopped", actor="engine")).ok
    await scheduler.advance(w, w.now, w.now + 10)
    assert w.place("warden") == "square"
    assert agents.split_when("D1 22:00 | flag:x") == ("", "D1 22:00 | flag:x")  # no hour to wait for: audit says so
