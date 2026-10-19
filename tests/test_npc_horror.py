"""Hostile motives remain playable without becoming facts or damage by narration."""

from autokeeper.world import context, maintenance, scheduler
from autokeeper.world import ops as O
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step

from .test_world import fresh


async def test_a_hostile_npc_can_threaten_and_prepare_revenge_without_harming_by_narration():
    world = fresh(location="tower")
    player = world.st.investigator
    goal = "让记者停止调查，即使用威胁和报复"
    assert world.apply(O.Enlist(npc="warden", goals=[goal])).ok
    world.agent("warden").wake = world.now
    threat = "守钟人挡在楼梯前：再来打听，我就让你在镇上找不到住处。"
    mood = "认为记者会毁掉自己的生活，愤怒而执拗"
    private_plan = "去广场游说布道者，一起排挤记者"
    before = player.model_dump(mode="json")

    brain = Canned(lambda ask: Scene(decisions=[Decision(npc="warden", mood=mood, ops=[
        O.Relate(a="warden", b=player.id, rel="敌意", delta=40, note="把记者视为威胁"),
        O.Occur(text=threat, place="tower", participants=["warden", player.id]),
        O.SetPlan(npc="warden", steps=[Step(id="revenge", text=private_plan, due=world.now + 30,
                                             ops=[{"kind": "move", "entity": "warden", "to": "square"}])]),
    ])]))
    report = await scheduler.advance(world, brain, world.now, world.now + 1)

    assert report.calls == 1 and not report.refused
    assert goal in brain.asks[0].views["warden"]
    assert world.relation("warden", player.id, "敌意").value == 40
    assert world.agent("warden").mood == mood
    assert world.agent("warden").plan[-1].text == private_plan
    visible = context.keeper_view(world)
    assert threat in visible and mood not in visible and private_plan not in visible
    assert player.model_dump(mode="json") == before
    assert not maintenance.inspect(world)["issues"]


async def test_a_prepared_hostile_plan_executes_offscreen_without_a_continuous_model():
    world = fresh(location="office")
    assert world.apply(O.Enlist(npc="warden", goals=["联系同伙，阻止记者继续调查"])).ok
    assert world.apply(O.SetPlan(npc="warden", steps=[
        Step(id="meet", text="离开钟楼去找布道者商量报复", due=world.now + 1,
             ops=[{"kind": "move", "entity": "warden", "to": "square"}]),
    ])).ok

    report = await scheduler.advance(world, None, world.now, world.now + 2)

    assert not report.calls and not report.refused
    assert world.place("warden") == "square"
    assert world.agent("warden").plan[-1].status == "done"
    assert "报复" not in context.keeper_view(world)
    assert not maintenance.inspect(world)["issues"]
