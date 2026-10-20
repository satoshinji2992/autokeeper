"""What wakes an important NPC backstage (world/scheduler.py): not a conversation it has already
answered, not what it does on meeting the investigators (agents.meeting), and planning anew only
when time jumps or the party moves on."""

from __future__ import annotations

import pytest

from autokeeper.module.schema import Agenda, Module, Move
from autokeeper.paths import EXAMPLE
from autokeeper.world import agents, context, scheduler
from autokeeper.world import ops as O
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step

from .test_world import fresh, staged


def nothing(ask):
    return Scene(decisions=[Decision(npc=n, wait=600) for n in ask.npcs])


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


async def test_what_it_does_on_meeting_the_investigators_never_wakes_it():
    w = staged()
    greet = Step(id="greet", text="有人上门就请他们进屋喝茶", meet=True)
    assert w.apply(O.SetPlan(npc="warden", steps=[greet])).ok
    w.agent("warden").seen = w.rec.seq  # nothing new has happened to it
    brain = Canned(nothing)
    report = await scheduler.advance(w, brain, w.now, w.now + 30)
    assert not any("warden" in ask.npcs for ask in brain.asks) and not any(x.startswith("warden") for x in report.woke)
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


async def test_a_conversation_it_has_answered_does_not_wake_it_again():
    w = staged(location="tower")
    me = w.st.investigator.id
    w.agent("warden").seen = w.rec.seq
    assert w.apply(O.Occur(id="talk:1:x:warden", actor=me, participants=["warden"], place="tower",
                           text="周衡和守钟人说话：「钟怎么了？」守钟人答：「没事。」", transcript=True)).ok
    assert w.apply(O.Occur(actor=me, participants=["warden"], place="tower", text="周衡撬开了齿轮间的门")).ok
    brain = Canned(nothing)
    await scheduler.advance(w, brain, w.now, w.now, events_only=True)
    [ask] = brain.asks
    assert "撬开了齿轮间的门" in ask.reasons["warden"]  # what it saw done, not what it was told and answered


async def test_one_with_nothing_left_to_do_plans_again_when_the_party_moves_on():
    w = fresh()
    assert w.apply(O.Enlist(npc="warden", goals=["守住钟楼"])).ok
    brain = Canned(nothing)
    await scheduler.advance(w, brain, w.now, w.now + 10)
    assert brain.asks == []
    await scheduler.advance(w, brain, w.now + 10, w.now + 20)
    assert brain.asks == []  # minutes go by in the same place
    await scheduler.advance(w, brain, w.now + 20, w.now + 80, moved_on=True)  # an hour on, somewhere else
    assert len(brain.asks) == 1 and "还没有接下来的安排" in brain.asks[0].reasons["warden"]


async def test_what_stopped_a_decision_is_what_it_hears_when_it_wakes():
    w = staged()
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="help", text="想下一步")])).ok
    first = Canned(lambda ask: Scene(decisions=[Decision(npc="warden", ops=[O.Move(entity="warden", to="nowhere_known")])]))
    await scheduler.advance(w, first, w.now, w.now + 1)
    assert "不知道" in w.agent("warden").refused
    later = Canned(nothing)
    await scheduler.advance(w, later, w.now + 1, w.now + 2)
    assert "上次没做成" in later.asks[0].reasons["warden"] and "不知道" in later.asks[0].reasons["warden"]
    assert w.agent("warden").refused == ""  # decided again: it is behind it


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
