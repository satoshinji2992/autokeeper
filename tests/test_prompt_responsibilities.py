"""The actual role calls preserve local improvisation, preparation and bounded NPC agency."""

from __future__ import annotations

from autokeeper import config, paths, skills
from autokeeper.agents.cast import Action, Cast, Ruling
from autokeeper.engine.game import Game, Turn
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules
from autokeeper.world import context
from autokeeper.world import ops as O

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401


def table(fake) -> tuple[Game, Cast]:
    module = Module.load(paths.EXAMPLE)
    module.events, module.endings, module.foreshadow, module.showcases = [], [], [], []
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location, state.turn = "square", 1
    cast = Cast(config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES},
                              minds=0, visual_limit=0, imagegen=False), module)
    return Game(module, state, cast, Rules(dice=fake)), cast


async def test_runtime_keeper_uses_its_skill_and_lands_an_unwritten_daily_scene_without_preparation(fake):
    game, cast = table(fake)
    who = game.state.investigator.id
    line = "摊主将热茶递到你手里，告诉你他每天在广场卖茶。"
    game.state.add("player", "我向茶摊要一杯热茶。", who=game.state.investigator.name)
    turn = Turn(action=Action(kind="daily", summary="周衡向茶摊要一杯热茶"), ruling=Ruling(needs_roll=False))
    SCRIPT.append({"scene": {
        "people": [{"id": "tea_vendor", "name": "卖茶人", "role": "广场茶摊摊主"}],
        "here": ["preacher", "tea_vendor"],
        "things": [{"name": "热茶", "holder": "tea_vendor"}],
        "holds": [{"item": "热茶", "holder": who}],
        "observations": [{"witnesses": ["tea_vendor"], "text": "刚给周衡递了一杯热茶，说自己每天在广场卖茶。"}],
    }, "text": line})
    try:
        assert await game.narrate(turn) == line
        assert len(SEEN) == 1 and game.state.usage["keeper"]["calls"] == 1
        assert "director" not in game.state.usage
        sent = SEEN[0]
        assert skills.wrap("keeper-craft") in sent["system"]
        assert "由你结合当前动机和记录就地确定并提交 scene" in sent["system"]
        assert "持续日程与规则由备场工具准备" in sent["system"]
        assert "顺着安排讲" not in sent["system"] and "系统未安排等待或时限时，不自行添加" not in sent["system"]
        assert "系统允许补充时" not in sent["system"]
        assert set(sent["schema"]["properties"]) >= {"scene", "text", "asides", "media"}
        assert not {"rewrite", "schedule"} & set(sent["schema"]["properties"])
        assert game.world.place("tea_vendor") == "square"
        assert next(e for e in game.world.rec.entities.values() if e.name == "热茶").at == who
        assert "刚给周衡递了一杯热茶" in context.npc_brief(game.world, "tea_vendor")
    finally:
        await game.close()
        await cast.close()


async def test_runtime_preparer_returns_material_for_hosting_without_moving_the_party(fake):
    game, cast = table(fake)
    SCRIPT.append({"kind": "展开", "reason": "调查员需要一个可持续拜访的诊所", "locations": [{
        "id": "clinic", "name": "街角诊所", "read_aloud": "门边挂着诊所的铜牌。", "exits": ["square"],
    }]})
    try:
        before = game.state.investigator.model_dump_json()
        prepared = await cast.director(game.state, "展开", action="去街角找一家诊所", at="square")
        assert prepared is not None and prepared.locations[0].id == "clinic"
        assert game.state.location == "square" and not any(loc.id == "clinic" for loc in game.m.locations)
        assert game.state.investigator.model_dump_json() == before
        assert len(SEEN) == 1
        sent = SEEN[0]
        assert "守秘人结合当前行动与局势继续主持" in sent["system"] and "照着演" not in sent["system"]
        assert set(sent["schema"]["properties"]) >= {"locations", "events", "backstage"}
        assert game.state.usage["director"]["calls"] == 1
    finally:
        await game.close()
        await cast.close()


async def test_runtime_npc_keeps_unresolved_agency_and_keeper_directs_the_settled_request(fake):
    game, cast = table(fake)
    clue = game.m.clue("c_diary")
    assert game.world.apply(O.Learn(fact=f"clue:{clue.id}", who=["preacher"], actor="engine")).ok
    action = Action(kind="talk", summary="周衡问布道者日记的内容", npc="preacher", speech="日记里写了什么？")
    claim = "日记的事我没听说过。"
    goals = ["按自己的安排结束布道"]
    SCRIPT.append({"text": claim, "deceptions": [{
        "claim": claim, "intent": "先回避这次追问，继续在广场布道。", "motive": "不想卷进守钟人的麻烦。",
    }], "intentions": {"goals": goals, "reason": "来访者打断布道，想先处理自己的安排。"}})
    try:
        unresolved = Turn(action=action, ruling=Ruling(needs_roll=False))
        await game._reply(unresolved, "declined", "preacher",
                          lambda: cast.npc(game.state, "preacher", game._npc_message(unresolved, "preacher")), lambda _: None)
        assert game.state.clues == [] and unresolved.replies["declined"].line == claim
        assert game.world.agent("preacher").goals == goals
        assert any(f.kind == "secret" and "回避这次追问" in f.text for f in game.world.rec.facts.values())
        first = SEEN[0]
        assert "许可本身不代表你已经答应透露" in first["system"]
        assert "未决部分仍由你按自身动机" in first["system"]
        assert game.m.truth.strip() not in first["system"]

        fake.queue(1)
        private_reason = "幕后依据仅供主持：其他人物尚不知道调查员读过那封信。"
        settled = Turn(action=action, ruling=Ruling(needs_roll=True, skill="说服", why="说出你读到的日记内容",
                                                  reason=private_reason),
                       primary=0, checks=[game.rules.check(game.state.investigator, "说服")])
        instruction = "说出你读到的日记内容，这项让步已经成立；其他打算仍按你自己的处境决定。"
        game.state.pending_turn = {"input": action.speech}
        SCRIPT.extend([
            {"kind": "npc", "npc": "preacher", "instruction": instruction},
            {"text": clue.text, "told": [clue.id]},
            {"kind": "tell", "telling": {"scene": {}, "text": ""}},
        ])
        assert await game.narrate(settled) == ""
        assert game.state.clues == [clue.id] and game.state.handouts == []
        assert game.world.agent("preacher").goals == goals
        assert len(SEEN) == 4 and game.state.usage["npc"]["calls"] == game.state.usage["keeper"]["calls"] == 2
        keeper_prompt = SEEN[1]["messages"][-1]["content"]
        assert "本次检定范围：说出你读到的日记内容" in keeper_prompt and "兑现已经成功的这项让步" in keeper_prompt
        assert private_reason in keeper_prompt
        npc_prompt = SEEN[2]["messages"][-1]["content"]
        assert instruction in npc_prompt and private_reason not in npc_prompt
        assert clue.text in SEEN[-1]["messages"][-1]["content"] and not SCRIPT
    finally:
        await game.close()
        await cast.close()
