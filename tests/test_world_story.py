"""A scripted playthrough with a canned NPC brain: the world goes on while the party is away.

Day one at the clock tower, the warden lends the reporter his raincoat; the reporter promises
it back by eight the next morning. Played twice: once the coat goes back before the party
leaves, once it does not. The party spends the night at the newspaper office; then walks to
the square. The brain decides only from each NPC's own view, as a model would.
"""

from __future__ import annotations

from autokeeper.engine.state import WorldChange, parse_time
from autokeeper.world import adapters, context, index, knowledge, scheduler
from autokeeper.world import ops as O
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import PLAYERS, Step

from .test_world import fresh

SERMON = "布道者当众说：有个记者借了守钟人的雨衣，到期也不还"


def mind(ask) -> Scene:
    """Each NPC reads only its own view: its promises, its memories, why it woke."""
    out = []
    for npc in ask.npcs:
        view, why = ask.views[npc], ask.reasons[npc]
        if npc == "warden" and "约定的时间到了" in why and "active" in view:
            out.append(Decision(npc=npc, notice="雨衣没还回来", judge="这记者靠不住", ops=[
                O.Break(promise="coat", text="过了八点雨衣还没还"),
                O.Relate(a="warden", b="zhouheng", rel="信任", delta=-40, note="借了雨衣不还"),
                O.Move(entity="warden", to="square"),
                O.Occur(text="守钟人在广场上向布道者抱怨，记者借了他的雨衣不还", place="square", participants=["warden", "preacher"]),
            ]))  # fmt: skip
        elif npc == "warden" and "fulfilled" in view and "按时还了" not in view:
            out.append(Decision(npc=npc, notice="雨衣按时还了", judge="这人守信", ops=[
                O.Relate(a="warden", b="zhouheng", rel="信任", delta=30, note="借的雨衣按时还了"),
                O.SetPlan(npc="warden", steps=[Step(id="diary", text="记者再来时，把日记给他看", when="at:zhouheng:tower")]),
            ]))  # fmt: skip
        elif npc == "preacher" and "雨衣不还" in view and "借东西不还" not in view:
            at = ask.t + 60
            out.append(Decision(npc=npc, notice="守钟人在抱怨记者", judge="正好拿来布道", ops=[
                O.Relate(a="preacher", b="zhouheng", rel="信任", delta=-20, note="听说他借东西不还"),
                O.SetPlan(npc="preacher", steps=[Step(id="sermon", text="布道时点名那个记者", due=at,
                                                      ops=[{"kind": "occur", "text": SERMON, "place": "square"}])]),
            ]))  # fmt: skip
        else:
            out.append(Decision(npc=npc, wait=720))
    return Scene(decisions=out)


async def play(kept: bool):
    w = fresh(location="tower")
    st, me = w.st, w.st.investigator.id
    st.clues.append("c_notice")  # found on the square before climbing the tower
    w.sync()
    for op in [O.Enlist(npc="warden", goals=["守住钟楼"], actor="director"), O.Enlist(npc="preacher", actor="director")]:
        assert w.apply(op).ok
    # Turn 1, at the tower: the warden lends the coat, the reporter promises it back by eight.
    st.turn = 1
    adapters.settle_changes(w, [WorldChange(kind="transfer", key="lend", text="雨衣先借你。", item="coat", owner=me, npc="warden")],
                            source="warden", spoken="（递过雨衣）雨衣先借你。")  # fmt: skip
    assert w.apply(O.MakePromise(id="t1:promise", promise="coat", by=me, to="warden", text="明早八点前把雨衣还到钟楼",
                                 due=parse_time("D2 08:00"), actor="arbiter")).ok  # fmt: skip
    if kept:  # turn 2: changed their mind, gave it back before leaving
        st.turn = 2
        assert w.apply(O.Transfer(id="t2:back", item="coat", to="warden", actor=me)).ok
        assert w.apply(O.Fulfil(id="t2:kept", promise="coat", actor=me)).ok
    # Turn 3: they walk to the newspaper office and sleep there.
    st.turn = 3
    left = st.minutes
    st.location, st.minutes = "office", st.minutes + 10
    brain = Canned(mind)
    night = scheduler.Budget(skip=4)
    start, st.minutes = st.minutes, parse_time("D2 09:00")
    overnight = await scheduler.advance(w, brain, start, st.minutes, budget=night)
    # Turn 4: they walk to the square in the morning.
    st.turn = 4
    st.location, st.minutes = "square", st.minutes + 10
    w.sync()
    # Different places think from the same instant: the preacher learns of the
    # warden's committed complaint on his next decision, then needs an hour to prepare.
    morning = await scheduler.advance(w, brain, st.minutes - 10, st.minutes + 200, budget=scheduler.Budget(chunk=70))
    st.minutes += 200
    return w, brain, overnight, morning, left


async def test_npcs_act_after_the_players_leave():
    w, brain, overnight, morning, left = await play(kept=False)
    acted = [e for e in w.rec.events if e.actor in ("warden", "preacher") and e.t > left]
    assert acted and all(e.visibility == "private" for e in acted if e.t < parse_time("D2 09:10"))
    assert w.entity("warden").at == "square"  # he went down to the square in the night
    assert overnight.calls <= 4 and len(brain.asks) == overnight.calls + morning.calls and overnight.chunks == 5
    assert any("约定的时间到了" in a.reasons.get("warden", "") for a in brain.asks)
    # Back in the square, the keeper sees him there; it never sees his plans.
    view = context.keeper_view(w, "square")
    assert "老守钟人" in view and "打算" not in view and w.m.truth not in view


async def test_an_earlier_promise_changes_what_happens_later():
    broken, *_ = await play(kept=False)
    kept, *_ = await play(kept=True)
    assert broken.rec.promises["coat"].status == "broken" and kept.rec.promises["coat"].status == "fulfilled"
    assert broken.relation("warden", "zhouheng", "信任").value == -40
    assert kept.relation("warden", "zhouheng", "信任").value == 30
    # The broken promise travelled: the preacher heard of it and, next morning, said it in public.
    assert broken.relation("preacher", "zhouheng", "信任").value == -20
    sermon = [e for e in broken.rec.events if e.text == SERMON]
    assert len(sermon) == 1 and sermon[0].visibility == "public"  # the party was on the square
    assert SERMON in context.keeper_view(broken, "square")
    assert not any(e.text == SERMON for e in kept.rec.events)
    assert kept.entity("warden").at == "tower" and kept.agent("warden").plan[-1].id == "diary"


async def test_new_relations_can_be_retrieved():
    w, *_ = await play(kept=False)
    me = w.st.investigator.id
    assert {(r.a, r.kind, r.value) for r in index.relations(w, me)} == {("warden", "信任", -40), ("preacher", "信任", -20)}
    near = index.neighbours(w, "preacher")
    assert near["warden"] == 1 and near[me] == 1
    hits = await index.Recall().search(w, "谁说记者借了雨衣不还", viewer="preacher")
    assert any("抱怨" in h.text for h in hits)
    assert "听说他借东西不还" in context.npc_view(w, "preacher").text
    assert index.relations(w, "preacher", viewer=PLAYERS) == []  # the table has not been told how he feels


async def test_after_a_rewrite_everything_already_public_still_holds():
    w, *_ = await play(kept=False)
    w.sync()
    before_facts = {f.id: f.text for f in w.rec.facts.values() if f.revealed is not None}
    before_events = [(e.id, e.text) for e in w.rec.events if e.visibility == "public"]
    clues = list(w.st.clues)
    assert "clue:c_notice" in before_facts
    # Frozen: what they found, what they saw, a found clue's explanation.
    assert not w.apply(O.Rewrite(target="fact:clue:c_notice", text="告示是布道者贴的", actor="director")).ok
    sermon = next(e for e in w.rec.events if e.text == SERMON)
    assert not w.apply(O.Rewrite(target=f"event:{sermon.id}", text="布道者讲了别的", actor="director")).ok
    assert not w.apply(O.Rewrite(target="fact:conclusion:k_motive", text="守钟人什么都不知道", explains=[], actor="director")).ok
    # Open: the warden's secret and the private complaint, for a better story.
    assert w.apply(O.Rewrite(target="fact:npc:warden:secrets:0", text="他在钟里藏了亡子的怀表，钟一停，表也就停了",
                             goals={"warden": ["让钟一直走下去"]}, reason="给守钟人的遮掩一个人情的理由", actor="director")).ok  # fmt: skip
    complaint = next(e for e in w.rec.events if "抱怨" in e.text)
    assert w.apply(O.Rewrite(target=f"event:{complaint.id}", text="守钟人在广场上向布道者打听，那个记者到底是什么来路",
                             reason="守钟人不是爱嚼舌的人", actor="director")).ok  # fmt: skip
    w.sync()
    assert {f.id: f.text for f in w.rec.facts.values() if f.revealed is not None} == before_facts
    assert [(e.id, e.text) for e in w.rec.events if e.visibility == "public"] == before_events
    assert w.st.clues == clues and w.rec.facts["npc:warden:secrets:0"].revealed is None
    assert [r.target for r in w.rec.revisions] == ["fact:npc:warden:secrets:0", f"event:{complaint.id}"]
    # The new version is what the backstage now reads; the old one is gone from it.
    assert w.m.npc("warden").secrets == ["他在钟里藏了亡子的怀表，钟一停，表也就停了"]
    assert "亡子的怀表" in context.npc_view(w, "warden").text
    assert "打听" in w.rec.event(complaint.id).text and not any(
        "抱怨" in m.text for m in w.agent("preacher").memory if not m.stale)  # fmt: skip
    assert knowledge.sync(w.m, w.st, w.rec) == []
