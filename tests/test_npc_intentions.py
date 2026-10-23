"""A conversation can change real intentions without treating a promise as a decision."""

from __future__ import annotations

from autokeeper import config, paths
from autokeeper.agents.cast import Cast, Dialogue
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules
from autokeeper.tui.library import write_save
from autokeeper.world import context, scheduler
from autokeeper.world import ops as O
from autokeeper.world.record import Step
from autokeeper.world.tools import Gateway

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401
from .test_npc_acts import setup


async def test_a_conversation_replaces_the_real_goal_and_retires_old_work_across_save_load(fake):
    game, cast = setup(fake)
    w = game.world
    assert w.apply(O.Enlist(npc="warden", goals=["等到夜里独自守钟"])).ok
    assert w.apply(O.SetPlan(npc="warden", steps=[Step(id="wait_old", text="夜里独自检查钟楼")])).ok
    SCRIPT.append({"text": "你说得对。我稍后到广场找人帮忙。", "intentions": {
        "goals": ["请街坊一起查看钟楼"], "reason": "调查员说明独自检查的风险，决定先找帮手",
        "steps": [{"id": "get_help", "text": "去广场找帮手", "due": w.now + 15,
                   "ops": [{"kind": "move", "entity": "warden", "to": "square"}]}],
    }})
    await cast.npc(game.state, "warden", "一个人上楼不安全，先找帮手吧。")
    a = w.agent("warden")
    assert a.goals == ["请街坊一起查看钟楼"] and "独自检查" in a.goal_reason and a.goal_source
    assert {s.id: s.status for s in a.plan} == {"wait_old": "dropped", "get_help": "pending"}
    assert w.place("warden") == "tower"  # planning is not execution
    assert "请街坊一起查看钟楼" in context.npc_brief(w, "warden")
    assert "【目标变化依据】" in context.npc_view(w, "warden").text
    brief = context.keeper_brief(w)
    assert "当前想要：请街坊一起查看钟楼" in brief and "改变依据：调查员说明独自检查的风险" in brief
    assert "想要：等到夜里独自守钟" not in brief
    assert all(e.visibility == "private" and set(e.witnesses) <= {"warden"} for e in w.rec.events if e.kind == "plan")
    write_save(game.state, game.m)
    state = GameState.model_validate_json(paths.save(game.state.slot).read_text(encoding="utf-8"))
    await game.close()

    module = Module.load(paths.EXAMPLE)
    restored_cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    restored = Game(module, state, restored_cast, Rules(dice=fake))
    assert restored.world.agent("warden").goals == ["请街坊一起查看钟楼"]
    SCRIPT.append({"text": "等我去广场叫几个人。"})
    await restored_cast.npc(state, "warden", "你接下来有什么打算？")
    assert "请街坊一起查看钟楼" in SEEN[-1]["messages"][-1]["content"]
    state.location = "office"  # the party left; the fixed action now happens offscreen
    report = await scheduler.advance(restored.world, None, state.minutes, state.minutes + 15)
    assert not report.refused and restored.world.place("warden") == "square"
    assert next(s for s in restored.world.agent("warden").plan if s.id == "get_help").status == "done"
    await restored.close()


async def test_an_ordinary_npc_can_register_intentions_in_one_atomic_batch_without_a_worker(fake):
    game, cast = setup(fake, location="square")
    assert game.world.agent("preacher") is None
    SCRIPT.append({"text": "我把讲坛收好就去问问。", "intentions": {
        "goals": ["弄清钟楼出了什么事"], "steps": [{"id": "pack", "text": "先收起讲坛"}],
        "reason": "听到调查员描述异响，决定暂时停下布道",
    }})
    await cast.npc(game.state, "preacher", "钟楼传来奇怪的声音。")
    a = game.world.agent("preacher")
    assert a.goals == ["弄清钟楼出了什么事"] and a.passive
    assert a.plan[-1].id == "pack" and not cast.unsettled
    journal = [entry for entry in game.world.rec.journal if entry.actor == "preacher"]
    assert [entry.kind for entry in journal] == ["enlist", "plan"]
    assert journal[0].id.rsplit(":", 1)[0] == journal[1].id.rsplit(":", 1)[0]
    await game.close()


async def test_goal_only_change_preserves_pending_work_and_explicit_empty_plan_cancels_it(fake):
    game, cast = setup(fake)
    w = game.world
    w.apply(O.Enlist(npc="warden", goals=["守钟"]))
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="wait", text="等到夜里")]))
    before = w.agent("warden").plan[0].model_dump()
    SCRIPT.append({"text": "我只是不再怕他了。", "intentions": {"goals": [], "reason": "决定不再为他守秘密"}})
    await cast.npc(game.state, "warden", "不必替他担心。")
    assert w.agent("warden").goals == [] and w.agent("warden").plan[0].model_dump() == before
    SCRIPT.append({"text": "今晚不用再等了。", "intentions": {"steps": [], "reason": "消息已经送到，取消等待"}})
    await cast.npc(game.state, "warden", "消息已经送到了。")
    assert w.agent("warden").plan[0].status == "dropped" and not cast.unsettled
    assert "当前没有目标" in context.npc_brief(w, "warden")
    assert "当前想要：无" in context.keeper_brief(w)
    await game.close()


async def test_same_turn_distinct_replies_update_once_without_restarting_completed_work(fake):
    game, cast = setup(fake)
    w = game.world
    w.apply(O.Enlist(npc="warden", goals=["把工具搬到广场"]))
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="visit", text="到广场", achieved="at:warden:square")]))
    w.apply(O.Move(entity="warden", to="square", actor="warden"))
    w.apply(O.AdvancePlan(npc="warden", step="visit", status="done"))
    game.state.location = "square"
    SCRIPT.append({"text": "先找几个邻居。", "intentions": {"goals": ["找邻居"], "reason": "听取调查员建议"}})
    await cast.npc(game.state, "warden", "先去找邻居。")
    SCRIPT.append({"text": "不，还是先去请医生。", "intentions": {"goals": ["找医生"], "reason": "得知有人受伤"}})
    await cast.npc(game.state, "warden", "有人受伤了。")
    entries = [entry for entry in w.rec.journal if entry.kind == "plan" and entry.actor == "warden"]
    assert len(entries) == 2 and entries[0].id != entries[1].id
    assert w.agent("warden").goals == ["找医生"]
    assert next(step for step in w.agent("warden").plan if step.id == "visit").status == "done"
    assert game.state.npc("warden").lines == 2 and len(SEEN) == 2
    await game.close()


async def test_a_false_promise_does_not_change_real_goals_or_plans(fake):
    game, cast = setup(fake)
    game.world.apply(O.Enlist(npc="warden", goals=["继续守钟"]))
    game.world.apply(O.SetPlan(npc="warden", steps=[Step(id="stay", text="留在钟楼")]))
    before = game.world.agent("warden").model_dump(include={"goals", "plan", "goal_reason", "goal_source"})
    SCRIPT.append({"text": "好，我稍后去找你。", "deceptions": [{
        "claim": "好，我稍后去找你。", "intent": "仍留在钟楼，不去会合。", "motive": "不想与来访者争执。",
    }]})
    await cast.npc(game.state, "warden", "稍后去广场见吧。")
    assert game.world.agent("warden").model_dump(include={"goals", "plan", "goal_reason", "goal_source"}) == before
    await game.close()


def test_invalid_new_plan_does_not_leave_an_enlisted_record_or_changed_goal():
    module = Module.load(paths.EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    from autokeeper.world.store import World

    world = World(module, state)
    dialogue = Dialogue(text="我去找帮手。", intentions={"goals": ["找帮手"], "reason": "听到异响", "steps": [
        {"id": "invalid", "text": "去不存在的地方", "when": "at:preacher:nowhere"},
    ]})
    before = state.model_dump_json()
    result = Gateway(world, "preacher").settle(Cast._intention_ops(world, "preacher", dialogue), request_id="bad-plan")
    assert not result["ok"] and state.model_dump_json() == before and world.agent("preacher") is None


def test_self_registration_cannot_grant_other_characters_goals_or_unknown_resources():
    from .test_world import staged

    world = staged()
    for op in [O.Enlist(npc="preacher", goals=["服从守钟人"]), O.Enlist(npc="warden", places=["office"]),
               O.Enlist(npc="warden", resources=["一队巡警"])]:
        assert not Gateway(world, "warden").preview([op.model_dump(mode="json")])["ok"]


def test_recording_seven_or_more_npcs_is_not_limited_by_worker_capacity():
    from autokeeper.module.schema import NPC
    from autokeeper.world.store import World

    module = Module.load(paths.EXAMPLE)
    module.npcs += [NPC(id=f"bystander_{n}", name=f"街坊{n}", role="街坊", location="square") for n in range(9)]
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    world = World(module, state)
    for n in range(9):
        assert world.apply(O.Enlist(npc=f"bystander_{n}", goals=["等雨停"], passive=True)).ok
    assert len(world.rec.agents) == 9 and all(a.passive for a in world.rec.agents.values())
