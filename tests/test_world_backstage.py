"""Backstage: rewrites that keep what the players met, director requests, and NPCs on game time."""

from __future__ import annotations

from autokeeper.world import context, desk, scheduler
from autokeeper.world import ops as O
from autokeeper.world.agents import vet
from autokeeper.world.record import Step

from .test_world import fresh, staged

SECRET = "fact:npc:warden:secrets:0"


# -- rewrites -----------------------------------------------------------------------------------------
def test_a_found_clue_keeps_its_words():
    w = staged(location="tower")
    w.st.clues.append("c_gears")
    w.sync()
    r = w.apply(O.Rewrite(target="fact:clue:c_gears", text="齿轮背后什么都没有", reason="齿轮是伪造的"))
    assert not r.ok and "原文不能改" in r.problems[0]


def test_what_the_players_heard_can_turn_out_a_lie_and_stays_what_they_heard():
    w = staged(location="square")
    w.st.add("npc", "守钟人？他在钟里藏了东西，我亲眼见过。", who="布道者")
    w.sync()
    assert w.rec.facts["npc:warden:secrets:0"].revealed is not None
    r = w.apply(O.Rewrite(target=SECRET, text="他什么都没藏，钟里的东西是布道者放的"))
    assert not r.ok and "听布道者说的" in r.problems[0] and "reason" in r.problems[0]
    r = w.apply(O.Rewrite(target=SECRET, text="他什么都没藏，钟里的东西是布道者放的", reason="布道者想把调查员引向钟楼"))
    assert r.ok, r.problems
    secret = w.rec.facts["npc:warden:secrets:0"]
    told = w.rec.facts["told:npc:warden:secrets:0:1"]
    assert secret.revealed is None and "players" not in secret.known_by
    assert (told.text, told.true, told.revealed is not None) == ("他在钟里藏了东西", False, True)
    w.sync()  # the new truth was never shown, so it stays backstage
    assert w.rec.facts["npc:warden:secrets:0"].revealed is None
    assert "他在钟里藏了东西（听布道者说的）" in context.keeper_view(w)
    view = context.director_view(w).text
    assert "布道者放的" in view.split("【玩家还不知道的")[1].split("【")[0]
    assert "他在钟里藏了东西" in view.split("不能改）】")[1].split("【")[0]
    assert w.undo() == [] and w.rec.facts["npc:warden:secrets:0"].revealed is not None  # recoverable


def test_words_the_players_saw_need_a_reason_to_be_rewritten():
    w = staged(location="tower")
    w.st.add("keeper", "守钟人低声说，他在钟里藏了东西，求你别声张。")
    r = w.apply(O.Rewrite(target=SECRET, text="他什么都没藏"))
    assert not r.ok and "叙述里说过" in r.problems[0]
    assert w.apply(O.Rewrite(target=SECRET, text="他什么都没藏，那是说给布道者听的", reason="守钟人在演给偷听的布道者看")).ok
    assert w.rec.facts["told:npc:warden:secrets:0:1"].text == "他在钟里藏了东西"


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


def test_an_event_the_players_lived_through_keeps_its_words():
    w = staged(location="tower")
    w.apply(O.Occur(text="钟突然停了一下", place="tower"))
    public = w.rec.events[-1]
    assert not w.apply(O.Rewrite(target=f"event:{public.id}", text="钟一直在走")).ok  # no reason given
    assert not w.apply(O.Rewrite(target=f"event:{public.id}", retract=True, reason="没这回事")).ok
    r = w.apply(O.Rewrite(target=f"event:{public.id}", text="是守钟人故意卡住了齿轮", reason="守钟人想吓走调查员",
                          memories={"warden": "我卡住了齿轮，让钟停了一下"}))
    assert r.ok, r.problems
    assert w.rec.event(public.id).text == "钟突然停了一下" and w.rec.event(public.id).version == 1
    behind = w.rec.facts[f"behind:{public.id}:1"]
    assert behind.revealed is None and behind.known_by == ["warden"] and "卡住了齿轮" in behind.text
    assert "我卡住了齿轮" in context.npc_view(w, "warden").text
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


async def test_prepared_steps_land_without_a_call_while_the_plan_is_fresh():
    w = staged(location="office")
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="walk", text="去广场", due=w.now + 60, ops=[{"kind": "move", "entity": "warden", "to": "square"}])],
                      reads={SECRET: 1}))  # fmt: skip
    await scheduler.advance(w, w.now, w.now + 90)
    assert w.entity("warden").at == "square" and w.agent("warden").plan[-1].status == "done"
    w2 = staged(location="office")
    w2.apply(O.SetPlan(npc="warden", steps=[Step(id="walk", text="去广场", due=w2.now + 60, ops=[{"kind": "move", "entity": "warden", "to": "square"}])],
                       reads={SECRET: 1}))  # fmt: skip
    w2.apply(O.Rewrite(target=SECRET, text="他在钟里藏了一封信"))
    await scheduler.advance(w2, w2.now, w2.now + 90)
    w2.st.minutes += 90
    assert w2.entity("warden").at == "tower" and any(n.npc == "warden" and n.kind == "stale" for n in scheduler.pending(w2))


async def test_scheduled_events_land_or_fall_through_when_what_they_read_changed():
    w = staged(location="office")
    w.apply(O.Schedule(event="sermon", at=w.now + 60, text="布道者去钟楼布道", place="tower", participants=["preacher"], actor="preacher",
                       ops=[{"kind": "move", "entity": "preacher", "to": "tower"}]))  # fmt: skip
    w.apply(O.Schedule(event="gossip", at=w.now + 60, text="守钟人去报社", actor="warden", reads={SECRET: 1},
                       ops=[{"kind": "move", "entity": "warden", "to": "office"}]))  # fmt: skip
    w.apply(O.Rewrite(target=SECRET, text="他在钟里藏了一封信"))
    report = await scheduler.advance(w, w.now, w.now + 120)
    assert report.fired == ["sermon"] and report.dropped == ["gossip"]
    assert w.entity("preacher").at == "tower" and w.entity("warden").at == "tower"


async def test_an_arrangement_does_not_pull_a_companion_away_from_the_investigators():
    w = staged(location="tower")
    me = w.st.investigator.id
    assert w.apply(O.Follow(npc="warden", actor="warden", leader=me, destination="square")).ok
    w.apply(O.Schedule(event="meeting", at=w.now + 60, text="守钟人和布道者在报社碰头", place="office",
                       participants=["warden", "preacher"], actor="director"))
    report = await scheduler.advance(w, w.now, w.now + 120)
    assert report.fired == ["meeting"]
    assert w.entity("warden").at == "tower" and w.entity("warden").following == me  # still walking with them
    assert w.entity("preacher").at == "office"


def test_an_npc_can_only_do_what_it_can_reach():
    w = staged(location="office")
    assert vet(w, "preacher", O.Move(entity="warden", to="square"))
    assert vet(w, "preacher", O.Transfer(item="coat", to="preacher"))  # the warden has it, in the tower
    assert vet(w, "preacher", O.Learn(fact="f_key", who=["warden"]))  # not beside it
    assert vet(w, "preacher", O.Occur(text="x", facts=["f_key"]))  # it does not know that
    assert vet(w, "warden", O.Transfer(item="coat", to="tower")) == []  # put it down where it stands
    assert vet(w, "warden", O.Move(entity="warden", to="square")) == []


def test_npcs_the_module_marks_important_are_enlisted_once():
    from autokeeper.world.agents import marked

    w = fresh()
    w.m.npc("warden").important = True
    assert [r.ok for r in w.apply_all(marked(w))] == [True]
    assert list(w.rec.agents) == ["warden"] and w.agent("warden").goals == ["不让人上齿轮间"]
    assert marked(w) == []


async def test_a_scheduled_scene_brings_its_people_who_learn_of_it_only_when_it_happens():
    w = staged(location="tower")
    before = len(w.agent("preacher").memory)
    assert w.apply(O.Schedule(event="visit", at=w.now + 60, text="布道者走进报社，打听那个记者", place="office",
                              participants=["preacher"], actor="director")).ok  # fmt: skip
    assert len(w.agent("preacher").memory) == before  # an arrangement is not something that happened to him
    report = await scheduler.advance(w, w.now, w.now + 90)
    assert report.fired == ["visit"] and w.entity("preacher").at == "office"
    assert any("打听那个记者" in m.text for m in w.agent("preacher").memory)


def test_a_plan_can_wait_for_an_hour_of_the_night():
    from autokeeper.engine.state import parse_time
    from autokeeper.world.agents import condition_problems, holds

    w = staged(location="office")
    assert condition_problems(w, "hour:22-6 & at:warden:tower") == []
    assert condition_problems(w, "hour:6-6")  # not a span
    w.st.minutes = parse_time("D1 21:00")
    assert not holds(w, "hour:22-6")
    w.st.minutes = parse_time("D1 23:00")
    assert holds(w, "hour:22-6 & at:warden:tower")
