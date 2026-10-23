"""Background world work cannot carry a request past a new visible decision unnoticed."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, KeeperCall, Narration, Plan, Ruling
from autokeeper.engine.state import GameState
from autokeeper.world import ops as O
from autokeeper.world import scheduler
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step

from .test_request_execution import RequestCast, make, module, run, telling

HELP = "布道者当面喊：救救我，有人在追我！先听我说！"
PRIVATE = "布道者在报社核对私人账目。"
PENDING = "先听布道者解释求救，再决定是否继续等到20:35。"
ORIGINAL = "先向守钟人确认时间，没有新情况就等到20:35；有人求救先停下来听。"


class BarrierCast(RequestCast):
    voice_requested = False

    async def keeper(self, state, report, on_delta=None, lore="", *, check=None, fix="", voices=None):
        if not self.voice_requested and voices and "warden" in voices:
            self.voice_requested = True
            return Narration(text="", call=KeeperCall(npc="warden", instruction="根据刚才的实际检定回答来访者。"))
        return await super().keeper(state, report, on_delta, lore, check=check, fix=fix)


def setup(fake, *, private=False):
    m = module()
    m.npc("preacher").important = True
    first = Action(kind="talk", summary="周衡向守钟人确认时间", npc="warden", speech="能确认一下时间吗？")
    wait = Action(kind="wait", summary="没有新情况就等到20:35")
    cast = BarrierCast({ORIGINAL: Plan(action=first, remaining="无新情况就等到20:35；有人求救先停下来听。")}, {
        first.summary: Ruling(needs_roll=True, skill="说服", minutes=5),
        wait.summary: Ruling(needs_roll=False, minutes=10, rest=True),
    }, [telling(next=Plan(action=wait)), telling() if private else telling(pending=PENDING)])
    game, graph = make(fake, cast, m=m)
    game.world_later = True
    w = game.world
    w.rec.simulated = game.state.minutes
    target = "office" if private else "tower"
    occurred = {"kind": "occur", "place": target, "text": PRIVATE if private else HELP}
    if private:
        occurred["visibility"] = "private"
    assert w.apply(O.SetPlan(npc="preacher", steps=[Step(
        id="arrive", text="去报社处理私事" if private else "跑上钟楼求救", due=w.now + 1,
        ops=[{"kind": "move", "entity": "preacher", "to": target}, occurred],
    )])).ok
    w.agent("preacher").wake = w.now + 3

    async def think(ask):
        await asyncio.sleep(0)  # let the main graph meet the real running world task
        return Scene(decisions=[Decision(npc=n, wait=600) for n in ask.npcs])

    brain = Canned(think)
    game.brain, game.minds = brain, scheduler.Budget(turn=4, skip=4)
    fake.queue(1)
    return game, graph, cast, brain


def assert_not_repeated(game, cast):
    st = game.state
    assert len(st.checks) == 1 and st.checks[0].passed and st.checks[0].turn == 1
    assert [e.text for e in st.log if e.kind == "player"] == [ORIGINAL]
    assert len([e for e in st.log if e.kind == "dice"]) == 1
    assert len(cast.npc_calls) == 1 and len([e for e in st.log if e.kind == "npc"]) == 1
    assert cast.planned == [ORIGINAL]


async def test_visible_background_arrival_is_told_and_returns_the_decision_before_the_old_wait(fake):
    game, graph, cast, brain = setup(fake)
    start = game.state.minutes
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        st = game.state
        assert not offers and not st.pending_turn and not fake.values
        assert st.turn == 1 and st.minutes == start + 5 and game.world.place("preacher") == "tower"
        assert st.unfinished[st.investigator.id]["remaining"] == PENDING
        assert len(cast.reports) == 2 and HELP not in cast.reports[0] and HELP in cast.reports[1]
        assert len(brain.asks) == 1 and len(cast.ruled) == 1
        assert len([e for e in game.world.rec.events if e.text == HELP]) == 1
        assert_not_repeated(game, cast)
    finally:
        await game.close()


async def test_unseen_private_background_work_adds_no_keeper_call_or_interruption(fake):
    game, graph, cast, brain = setup(fake, private=True)
    start = game.state.minutes
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        st = game.state
        assert not offers and not st.pending_turn and not st.unfinished and not fake.values
        assert st.turn == 2 and st.minutes == start + 15 and game.world.place("preacher") == "office"
        assert len(cast.reports) == 2 and not any(PRIVATE in report for report in cast.reports)
        assert len(brain.asks) == 1 and len(cast.ruled) == 2
        assert_not_repeated(game, cast)
    finally:
        await game.close()


@pytest.mark.parametrize("cut", ["before_task", "scheduler_done", "after_play", "settled", "following_ready", "following_landed"])
async def test_world_barrier_and_following_keeper_resume_from_saved_receipts(fake, cut):
    game, graph, cast, brain = setup(fake)
    games, saved = [game], []
    start = game.state.minutes

    def checkpoint():
        receipt = game.state.pending_turn.get("request_world")
        if receipt is None:
            return
        steps = receipt.get("work", {}).get("steps", {})
        follow = game.state.pending_turn.get("keeper", {}).get("following_keeper", {})
        hit = {
            "before_task": not receipt.get("work"),
            "scheduler_done": bool(game.world.rec.ticks) and not steps.get("live", {}).get("done"),
            "after_play": steps.get("play", {}).get("done") and not steps.get("flags", {}).get("done"),
            "settled": receipt.get("settled") and not receipt.get("complete"),
            "following_ready": "keeper_depth" in receipt and not follow.get("text"),
            "following_landed": follow.get("landed") and not receipt.get("complete"),
        }[cut]
        if hit:
            if not saved:
                saved.append(game.state.model_dump_json())
            raise RuntimeError("屏障检查点断线")

    game.checkpoint = checkpoint
    try:
        with pytest.raises(RuntimeError):
            await run(graph, game, {"input": ORIGINAL})
        assert len(saved) == 1 and not fake.values
        before = GameState.model_validate_json(saved[0])
        assert before.turn == 1 and before.minutes == start + 5
        assert before.pending_turn["request_world"]["since"] >= 0
        assert before.pending_turn["request_world"]["dealing"] == ["warden"]
        assert "ready" not in before.pending_turn["request"]
        old_module = game.m.model_copy(deep=True)
        # The dying process cannot continue mutating its snapshot's game after loading.
        await game.close()
        game, graph = make(fake, cast, m=old_module, state=before)
        games.append(game)
        game.world_later = True
        game.brain, game.minds = brain, scheduler.Budget(turn=4, skip=4)
        _, offers = await run(graph, game, {"input": "/retry"})
        st = game.state
        assert not offers and not st.pending_turn and not cast.tellings and not fake.values
        assert st.turn == 1 and st.minutes == start + 5 and game.world.place("preacher") == "tower"
        assert st.unfinished[st.investigator.id]["remaining"] == PENDING
        assert len(brain.asks) == 1 and len(cast.reports) == 2 and len(cast.ruled) == 1
        assert len([e for e in game.world.rec.events if e.text == HELP]) == 1
        assert len([e for e in st.log if e.kind == "keeper" and HELP in e.text]) <= 1
        assert_not_repeated(game, cast)
    finally:
        for old in games:
            await old.close()


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("minutes", [0, 5])
async def test_narrated_save_before_remember_recovers_world_debt_without_an_in_memory_owed_flag(fake, private, minutes):
    game, graph, cast, brain = setup(fake, private=private)
    games, saved = [game], []
    start = game.state.minutes
    if minutes == 0:
        first, wait = cast.rulings
        cast.rulings[first] = cast.rulings[first].model_copy(update={"minutes": 0})
        cast.rulings[wait] = cast.rulings[wait].model_copy(update={"minutes": 15})
        step = game.world.agent("preacher").plan[0].model_copy(update={"due": start})
        assert game.world.apply(O.SetPlan(npc="preacher", steps=[step])).ok

    def checkpoint():
        journal = game.state.pending_turn
        if journal.get("narrated") and "request_world" not in journal:
            assert game._world_owed and game._world_task is None
            saved.append(game.state.model_dump_json())
            raise RuntimeError("讲述已保存，remember之前断线")

    game.checkpoint = checkpoint
    try:
        with pytest.raises(RuntimeError, match="remember之前"):
            await run(graph, game, {"input": ORIGINAL})
        assert len(saved) == 1 and len(brain.asks) == 0 and not fake.values
        state = GameState.model_validate_json(saved[0])
        assert state.record.simulated == start and state.minutes == start + minutes
        assert state.pending_turn["narrated"] and "request_world" not in state.pending_turn
        assert state.pending_turn["keeper"]["calls"][0]["done"]
        old_module = game.m.model_copy(deep=True)
        await game.close()
        game, graph = make(fake, cast, m=old_module, state=state)
        games.append(game)
        game.world_later = True
        game.brain, game.minds = brain, scheduler.Budget(turn=4, skip=4)
        assert not game._world_owed and game._world_task is None
        _, offers = await run(graph, game, {"input": "/retry"})
        st = game.state
        assert not offers and not st.pending_turn and not fake.values and not cast.tellings
        assert len(brain.asks) <= 1 and len(cast.reports) == 2
        assert_not_repeated(game, cast)
        if private:
            assert st.turn == 2 and st.minutes == start + 15 and not st.unfinished
            assert game.world.place("preacher") == "office"
            assert not any(PRIVATE in report for report in cast.reports)
        else:
            assert st.turn == 1 and st.minutes == start + minutes and game.world.place("preacher") == "tower"
            assert st.unfinished[st.investigator.id]["remaining"] == PENDING
            assert HELP in cast.reports[-1]
    finally:
        for old in games:
            await old.close()


async def test_inline_world_play_does_not_acquire_a_continuation_background_barrier(fake):
    game, graph, cast, brain = setup(fake, private=True)
    game.world_later = False
    barriers = []

    def checkpoint():
        if "request_world" in game.state.pending_turn:
            barriers.append(game.state.pending_turn["request_world"])

    game.checkpoint = checkpoint
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        assert not offers and not barriers and len(cast.reports) == 2 and len(brain.asks) == 1
        assert game.state.turn == 2 and not game.state.unfinished and not game.state.pending_turn
        assert game.world.place("preacher") == "office"
        assert not any(PRIVATE in report for report in cast.reports)
        assert_not_repeated(game, cast)
    finally:
        await game.close()
