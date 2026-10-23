"""Prepared material returns to the keeper; it cannot manufacture a check on the old NPC."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.agents.cast import Action, Failure, KeeperCall, Plan, Ruling
from autokeeper.agents.director import Improv, NewClue, NewNPC
from autokeeper.engine import graph
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, run_turn

REQUEST = "请布道者留意广场，我去钟楼找人问公开登记册。"
BEFORE = {"npc": "preacher", "speech": "请您先留意广场，我去查登记册。"}
ORIGINAL = Action(kind="talk", summary=REQUEST, npc="preacher", destination="tower", before=[BEFORE])
REMAINDER = Action(kind="talk", summary="去钟楼向新见到的档案员申请核对公开登记册", npc="archivist",
                   destination="tower", speech="能让我核对一下公开的出入登记吗？", approach="魅惑", before=[BEFORE])
PACKAGE = Improv(kind="展开", reason="准备登记册的实际保管人", npcs=[
    NewNPC(id="archivist", name="档案员", role="登记册保管人", location="tower"),
], clues=[NewClue(id="c_registry", title="出入登记", text="登记册记载了看守实际离开的时刻。", where="tower",
                  npc="archivist", skill="说服")], give=["c_registry"])


class PreparedCast(FakeCast):
    improvising = True

    def __init__(self, *, pause=False, restored=False, choice=False):
        super().__init__({})
        self.plan_calls = []
        self.director_calls = 0
        self.pause, self.restored, self.choice = pause, restored, choice

    async def plan(self, state, text, *, check=None):
        assert not self.restored, "a restored prepared action must not be planned again"
        self.plan_calls.append(text)
        action = ORIGINAL.model_copy(deep=True) if len(self.plan_calls) == 1 else REMAINDER.model_copy(deep=True)
        if self.choice and len(self.plan_calls) > 1:
            action = Action(kind="move", summary="先到钟楼，看清登记处提出的新条件再由玩家决定", destination="tower")
        plan = Plan(action=action)
        if check:
            assert not check(plan)
        return plan

    async def director(self, state, kind, **kw):
        assert not self.restored
        self.director_calls += 1
        return PACKAGE.model_copy(deep=True)

    async def arbiter(self, state, action, at=None, **kw):
        self.ruled.append(action.model_copy(deep=True))
        if action.npc != "archivist":
            return Ruling(needs_roll=False, scope="可即兴", reason="托付不需检定；登记处尚缺保管人的资料")
        if self.pause:
            raise ConnectionError("interrupted after the action handoff")
        assert at == "tower"
        return Ruling(needs_roll=True, skill="魅惑", why="确认档案员是否允许核对公开登记册", minutes=2,
                      clues_on_success=["c_registry"],
                      on_push_fail=Failure(risk="对方可能收起登记册并请你离开"))

    async def npc(self, state, npc_id, message, **kwargs):
        self.npc_calls.append((npc_id, message))
        if npc_id == "archivist":
            self.told[npc_id] = ["c_registry"]
        return "我会留意广场。" if npc_id == "preacher" else "你可以看，这一栏记着看守离开的时刻。"


def made(fake, cast, state=None):
    module = Module.load(EXAMPLE)
    module.events = []
    state = state or Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = state.location or "square"
    events = []

    async def emit(event):
        events.append(event)

    return Game(module, state, cast, Rules(dice=fake), emit=emit), events


async def test_prepared_material_replans_and_rules_the_real_target_without_replaying_the_prefix(fake):
    cast = PreparedCast()
    cast.voice_script.append(KeeperCall(npc="archivist", instruction="本次核对登记册的请求已经成功，按实际开放的记录回应。"))
    game, _ = made(fake, cast)
    fake.queue(1)
    start = game.state.minutes
    try:
        app = graph.build(game, InMemorySaver())
        output, _ = await run_turn(app, REQUEST, {"configurable": {"thread_id": "new-stage"}})
        assert len(cast.plan_calls) == 2 and cast.director_calls == 1
        assert "已经完成的出发前步骤" in cast.plan_calls[1] and "我会留意广场" in cast.plan_calls[1]
        assert [a.npc for a in cast.ruled] == ["preacher", "archivist"]
        assert [npc for npc, _ in cast.npc_calls] == ["preacher", "archivist"]
        record = game.state.checks[-1]
        assert record.npc == "archivist" and record.skill == "魅惑"
        assert game.state.location == "tower" and game.state.minutes - start == 12
        assert "c_registry" in game.state.clues
        public = [event for event in output if event["type"] == "ruling"][-1]
        assert public["why"] == "确认档案员是否允许核对公开登记册"
        assert public["on_push_fail"]["risk"] == "对方可能收起登记册并请你离开"
    finally:
        await game.close()


async def test_interruption_after_the_prepared_action_keeps_both_identity_and_completed_conversation(fake):
    cast = PreparedCast(pause=True)
    game, _ = made(fake, cast)
    game.state.pending_turn.update(input=REQUEST, active=0)
    action = await game.parse(REQUEST)
    await game.prepare(action)
    start = game.state.minutes
    with pytest.raises(ConnectionError):
        await game.rule(action)
    saved = GameState.model_validate_json(game.state.model_dump_json())
    assert saved.pending_turn["action"]["npc"] == "archivist"
    assert saved.minutes == start and not saved.checks
    await game.close()

    restored = PreparedCast(restored=True)
    game, _ = made(fake, restored, saved)
    try:
        action = Action.model_validate(game.state.pending_turn["action"])
        ruling = await game.rule(action)
        assert action.npc == "archivist" and ruling.skill == "魅惑"
        assert not restored.plan_calls and not restored.director_calls and not restored.npc_calls
        assert game.state.minutes == start
        assert "我会留意广场" in str(game.state.pending_turn["preparation"]["turn"])
    finally:
        await game.close()


async def test_a_new_condition_can_be_presented_without_automatically_rolling_its_clue_gate(fake):
    cast = PreparedCast(choice=True)
    game, _ = made(fake, cast)
    game.state.pending_turn.update(input=REQUEST, active=0)
    try:
        action = await game.parse(REQUEST)
        await game.prepare(action)
        ruling = await game.rule(action)
        assert action.kind == "move" and action.npc == "" and action.destination == "tower"
        assert not ruling.needs_roll and not ruling.auto_clues and not ruling.clues_on_success
        turn = await game.resolve(action, ruling)
        assert game.state.location == "tower" and not turn.checks and not game.state.clues
        assert len(cast.ruled) == 1  # plain travel needs no second rules call
        assert [npc for npc, _ in cast.npc_calls] == ["preacher"]
    finally:
        await game.close()
