"""Backstage: rewrites that keep what the players met, director requests, and NPCs on game time."""

from __future__ import annotations

import json

from autokeeper.world import context, desk, scheduler
from autokeeper.world import ops as O
from autokeeper.world.agents import Canned, Decision, Scene, vet
from autokeeper.world.record import Step
from autokeeper.world.scheduler import Budget

from .test_world import fresh, staged

SECRET = "fact:npc:warden:secrets:0"


# -- rewrites -----------------------------------------------------------------------------------------
def test_a_revealed_fact_is_frozen():
    w = staged(location="tower")
    w.st.clues.append("c_gears")
    w.sync()
    r = w.apply(O.Rewrite(target="fact:clue:c_gears", text="齿轮背后什么都没有"))
    assert not r.ok and "玩家已经知道" in r.problems[0]


def test_words_the_players_saw_are_frozen_even_without_a_mark():
    w = staged(location="tower")
    w.st.add("keeper", "守钟人低声说，他在钟里藏了东西，求你别声张。")
    r = w.apply(O.Rewrite(target=SECRET, text="他什么都没藏"))
    assert not r.ok and "出现过" in r.problems[0]


def test_a_found_clue_keeps_an_explanation():
    w = staged(location="office")
    w.st.clues.append("c_archive")
    w.sync()
    r = w.apply(O.Rewrite(target="fact:conclusion:k_tower", text="钟声多一下是守钟人自己敲的", explains=[]))
    assert not r.ok and "c_archive" in r.problems[0]
    r = w.apply(O.Rewrite(target="fact:conclusion:k_tower", text="钟里锁着守钟人的亡子，多出的钟声是他在敲", reason="更悲伤"))
    assert r.ok and w.rec.facts["conclusion:k_tower"].explains == ["c_gears", "c_diary", "c_archive"]
    assert w.apply(O.Rewrite(target="fact:conclusion:k_tower", retract=True)).problems  # nothing else explains c_archive


def test_a_rewrite_updates_what_rests_on_it_and_is_logged():
    w = staged()
    w.apply(O.Learn(fact="npc:warden:secrets:0", who=["warden"], how="自己藏的", actor="engine"))
    w.apply(O.Occur(text="守钟人半夜擦拭他在钟里藏了东西的那块铜板", place="tower", participants=["warden"]))
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="hide", text="把东西挪走")], reads={SECRET: 1}))
    req = desk.submit(w, "展开", [{"kind": "move", "entity": "warden", "to": "square"}], reads=desk.snapshot(w, [SECRET]))
    r = w.apply(O.Rewrite(target=SECRET, text="他在钟里藏了亡子的怀表", reason="让守钟人更有人情味", goals={"warden": ["守住儿子的怀表"]},
                          memories={"warden": "我把儿子的怀表藏进了钟里"}))  # fmt: skip
    assert r.ok, r.problems
    rev = w.rec.revisions[-1]
    assert (rev.old, rev.new, rev.old_version, rev.new_version) == ("他在钟里藏了东西", "他在钟里藏了亡子的怀表", 1, 2)
    assert "agent:warden" in rev.touched and "thread:th1" in rev.touched and any(t.startswith("event:") for t in rev.touched)
    a = w.agent("warden")
    assert a.goals == ["守住儿子的怀表"] and "我把儿子的怀表藏进了钟里" in [m.text for m in a.memory]
    assert any("亡子的怀表" in e.text for e in w.rec.events if e.kind == "occur")  # private history follows
    assert w.m.npc("warden").secrets == ["他在钟里藏了亡子的怀表"]  # and so does this game's module
    assert "亡子的怀表" in context.npc_view(w, "warden").text and "他在钟里藏了东西" not in context.npc_view(w, "warden").text
    assert scheduler.fresh(w, a.reads) == [SECRET]  # the plan rests on the old version
    assert desk.land(w, req.id).status == "stale"
    assert w.undo() == [] and w.m.npc("warden").secrets == ["他在钟里藏了东西"]  # recoverable


def test_events_the_players_lived_through_cannot_be_rewritten():
    w = staged(location="tower")
    w.apply(O.Occur(text="钟突然停了一下", place="tower"))
    public = w.rec.events[-1]
    assert not w.apply(O.Rewrite(target=f"event:{public.id}", text="钟一直在走")).ok
    w.st.location = "office"
    w.apply(O.Occur(text="守钟人在齿轮间里自言自语", place="tower", participants=["warden"]))
    private = w.rec.events[-1]
    r = w.apply(O.Rewrite(target=f"event:{private.id}", text="守钟人在齿轮间里给钟上油", reason="减少疑点"))
    assert r.ok and w.rec.event(private.id).text == "守钟人在齿轮间里给钟上油" and w.rec.event(private.id).version == 2
    assert w.agent("warden").memory[-1].stale  # its memory of the old version


def test_the_consistency_hook_sees_the_pins_and_can_refuse():
    w = staged(location="tower")
    w.apply(O.AddFact(fact="f_seen", text="守钟人左手缺了两根手指", about=["warden"], public=True))
    seen = []

    def checker(world, op, pins):
        seen.append([f.id for f in pins])
        return ["和玩家看到的手不符"] if "双手完好" in op.text else []

    w.checker = checker
    assert not w.apply(O.Rewrite(target=SECRET, text="他双手完好，钟里藏着别人的手指")).ok
    assert "f_seen" in seen[0]
    assert w.apply(O.Rewrite(target=SECRET, text="他在钟里藏了自己的两根手指")).ok


def test_players_cannot_be_told_through_a_rewrite():
    w = staged()
    r = w.apply(O.Rewrite(target=SECRET, text="另一件事", known_by=["players"]))
    assert not r.ok and "learn" in r.problems[0]


# -- director requests ----------------------------------------------------------------------------------
def test_requests_are_merged_checked_for_staleness_and_land_whole():
    w = staged()
    ops = [{"kind": "move", "entity": "warden", "to": "square"}]
    reads = desk.snapshot(w, [SECRET, "agent:warden"])
    one = desk.submit(w, "展开", ops, reads=reads)
    assert desk.submit(w, "展开", ops, reads=reads) is one  # the same request twice is one
    newer = desk.submit(w, "展开", [{"kind": "move", "entity": "warden", "to": "office"}], reads=reads, key=one.key)
    assert one.status == "dropped" and newer.status == "pending" and desk.pending(w) == [newer]
    assert desk.land(w, newer.id).status == "landed" and w.entity("warden").at == "office"
    assert desk.land(w, newer.id).status == "landed"  # landing again settles nothing twice
    w.apply(O.Rewrite(target=SECRET, text="他在钟里藏了一封信"))
    late = desk.submit(w, "推进", [{"kind": "move", "entity": "warden", "to": "tower"}], reads=reads)
    out = desk.land(w, late.id)
    assert out.status == "stale" and out.moved == [SECRET]
    old = desk.submit(w, "后果", [{"kind": "move", "entity": "preacher", "to": "office"}], reads={}, ttl=1)
    w.st.turn += 2
    assert desk.land(w, old.id).status == "expired"
    half = desk.submit(w, "后果", [{"kind": "move", "entity": "preacher", "to": "office"}, {"kind": "flag", "flag": "nope"}], reads={})
    out = desk.land(w, half.id)
    assert out.status == "dropped" and w.entity("preacher").at == "square"  # the first op was undone with it


# -- NPCs on game time ------------------------------------------------------------------------------------
def crowd(location: str = "office"):
    """Four important NPCs in four places, each with a step that wants a decision now."""
    w = fresh(location)
    from autokeeper.module.schema import NPC, Location

    w.m.locations.append(Location(id="inn", name="客栈", read_aloud="", exits=["square"]))
    w.m.npcs += [NPC(id="clerk", name="店员", role="报社店员", location="office"), NPC(id="cook", name="厨子", role="客栈厨子", location="inn")]
    w.sync()
    for npc in ("warden", "preacher", "clerk", "cook"):
        w.apply(O.Enlist(npc=npc))
        w.apply(O.SetPlan(npc=npc, steps=[Step(id="go", text="做点事", due=w.now)]))
    return w


def nothing(ask):
    return Scene(decisions=[Decision(npc=n, wait=600) for n in ask.npcs])


async def test_a_player_turn_spends_at_most_its_budget():
    w = crowd(location="tower")  # the warden is onscreen
    brain = Canned(nothing)
    report = await scheduler.advance(w, brain, w.now, w.now + 10, budget=Budget(turn=2))
    assert report.calls == 2 == len(brain.asks) and report.chunks == 1
    assert len(report.woke) == 2 and len(report.deferred) == 1  # the third waits; the warden is with the party
    assert not any("warden" in x for x in report.woke + report.deferred)


async def test_a_long_skip_is_simulated_in_chunks_on_its_own_budget():
    w = crowd(location="tower")
    brain = Canned(nothing)
    start = w.now
    w.st.minutes += 8 * 60
    report = await scheduler.advance(w, brain, start, w.st.minutes, budget=Budget(skip=4, chunk=240))
    assert report.chunks == 2 and report.calls == 3 and not report.deferred
    w.st.minutes += 3 * 1440
    report = await scheduler.advance(w, None, start + 480, w.st.minutes, budget=Budget(chunks=12))
    assert report.chunks == 12 and report.calls == 0
    assert (await scheduler.advance(w, brain, start + 480, w.st.minutes)).repeat  # the same span twice: nothing twice


async def test_npcs_in_one_place_are_one_offscreen_scene():
    w = crowd(location="tower")
    w.apply(O.Move(entity="clerk", to="square"))
    w.apply(O.Move(entity="cook", to="square"))
    w.apply(O.Move(entity="preacher", to="square"))

    def scene(ask):
        assert set(ask.npcs) == {"clerk", "cook", "preacher"}
        assert "齿轮间的钥匙" not in ask.views["clerk"]
        return Scene(summary="三个人在广场上吵了起来", decisions=[
            Decision(npc="clerk", ops=[O.Relate(a="clerk", b="cook", rel="敌对", delta=-20, note="吵架")]),
            Decision(npc="cook", ops=[O.Relate(a="clerk", b="cook", rel="盟友", value=99)]),  # not its own view to change
        ])  # fmt: skip

    brain = Canned(scene)
    report = await scheduler.advance(w, brain, w.now, w.now + 30, budget=Budget(turn=1))
    assert report.calls == 1 and w.relation("clerk", "cook", "敌对").value == -20
    assert w.relation("clerk", "cook", "盟友") is None and any("只能改变自己" in x for x in report.refused)
    scene_event = w.rec.events[-1]
    assert scene_event.text == "三个人在广场上吵了起来" and set(scene_event.participants) == {"clerk", "cook", "preacher"}


async def test_prepared_steps_land_without_a_call_while_the_plan_is_fresh():
    w = staged(location="office")
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="walk", text="去广场", due=w.now + 60, ops=[{"kind": "move", "entity": "warden", "to": "square"}])],
                      reads={SECRET: 1}))  # fmt: skip
    report = await scheduler.advance(w, None, w.now, w.now + 90)
    assert report.calls == 0 and w.entity("warden").at == "square" and w.agent("warden").plan[-1].status == "done"
    w2 = staged(location="office")
    w2.apply(O.SetPlan(npc="warden", steps=[Step(id="walk", text="去广场", due=w2.now + 60, ops=[{"kind": "move", "entity": "warden", "to": "square"}])],
                       reads={SECRET: 1}))  # fmt: skip
    w2.apply(O.Rewrite(target=SECRET, text="他在钟里藏了一封信"))
    report = await scheduler.advance(w2, None, w2.now, w2.now + 90)
    assert w2.entity("warden").at == "tower" and any("计划依据的事变了" in x for x in report.deferred)


async def test_a_step_whose_condition_never_comes_is_blocked_and_wakes_its_npc():
    w = fresh(location="office")
    w.apply(O.Enlist(npc="warden"))
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="wait", text="等钟停", due=w.now, when="flag:bell_stopped")]))
    brain = Canned(nothing)
    report = await scheduler.advance(w, brain, w.now, w.now + 60, budget=Budget(grace=120))
    assert not report.blocked and brain.asks == []
    report = await scheduler.advance(w, brain, w.now + 60, w.now + 200, budget=Budget(grace=120))
    assert report.blocked == ["warden:wait"] and "计划受阻" in brain.asks[0].reasons["warden"]


async def test_scheduled_events_land_or_fall_through_when_what_they_read_changed():
    w = staged(location="office")
    w.apply(O.Schedule(event="sermon", at=w.now + 60, text="布道者去钟楼布道", place="tower", participants=["preacher"], actor="preacher",
                       ops=[{"kind": "move", "entity": "preacher", "to": "tower"}]))  # fmt: skip
    w.apply(O.Schedule(event="gossip", at=w.now + 60, text="守钟人去报社", actor="warden", reads={SECRET: 1},
                       ops=[{"kind": "move", "entity": "warden", "to": "office"}]))  # fmt: skip
    w.apply(O.Rewrite(target=SECRET, text="他在钟里藏了一封信"))
    report = await scheduler.advance(w, None, w.now, w.now + 120)
    assert report.fired == ["sermon"] and report.dropped == ["gossip"]
    assert w.entity("preacher").at == "tower" and w.entity("warden").at == "tower"


def test_an_npc_can_only_do_what_it_can_reach():
    w = staged(location="office")
    assert vet(w, "preacher", O.Move(entity="warden", to="square"))
    assert vet(w, "preacher", O.Transfer(item="coat", to="preacher"))  # the warden has it, in the tower
    assert vet(w, "preacher", O.Learn(fact="f_key", who=["warden"]))  # not beside it
    assert vet(w, "preacher", O.Occur(text="x", facts=["f_key"]))  # it does not know that
    assert vet(w, "warden", O.Transfer(item="coat", to="tower")) == []  # put it down where it stands
    assert vet(w, "warden", O.Move(entity="warden", to="square")) == []


async def test_the_cast_brain_asks_one_stateless_session_per_group_and_reads_its_scene():
    from types import SimpleNamespace

    from autokeeper.world.agents import Ask, CastBrain

    sent = []

    class Cast:
        sessions = {"mind:preacher+warden": "session"}  # opened before: no model is made here

        async def _send(self, key, session, state, prompt, **kw):
            sent.append((key, prompt))
            return SimpleNamespace(data={"summary": "两人碰了个头", "decisions": [
                {"npc": "warden", "notice": "布道者来了", "judge": "别让他上楼",
                 "ops": [{"kind": "relate", "a": "warden", "b": "preacher", "rel": "戒备", "delta": -10}]}]})  # fmt: skip

    w = staged()
    ask = Ask(npcs=["warden", "preacher"], views={"warden": "守钟人的资料", "preacher": "布道者的资料"}, known={}, reasons={}, t=w.now)
    scene = await CastBrain(Cast(), w.st).decide(ask)
    assert sent[0][0] == "mind:preacher+warden" and "守钟人的资料" in sent[0][1] and "布道者的资料" in sent[0][1]
    assert scene.summary == "两人碰了个头" and scene.decisions[0].ops[0].rel == "戒备"
    schema = json.dumps(Scene.model_json_schema(), ensure_ascii=False)
    assert '"tag"' not in schema and '"key"' not in schema  # the adapters' bookkeeping is not offered to the model


def test_npcs_the_module_marks_important_are_enlisted_once():
    from autokeeper.world.agents import marked

    w = fresh()
    w.m.npc("warden").important = True
    assert [r.ok for r in w.apply_all(marked(w))] == [True]
    assert list(w.rec.agents) == ["warden"] and w.agent("warden").goals == ["不让人上齿轮间"]
    assert marked(w) == []
