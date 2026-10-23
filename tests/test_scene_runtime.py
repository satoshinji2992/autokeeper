"""Real scoped Games/Graphs, one world, and causal independent time reservations."""

from __future__ import annotations

import asyncio

from autokeeper.agents.cast import Action, Plan, Ruling
from autokeeper.engine.game import Game
from autokeeper.engine.scenes import SceneRuntime
from autokeeper.engine.state import GameState
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import SetFlag

from .conftest import FakeDice
from .test_request_execution import RequestCast, module, run, telling


def made(fake, *, plans=None, rulings=None, tellings=None, state=None):
    m = module()
    if state is None:
        a = m.pregens[0].model_copy(deep=True)
        b = a.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
        state = Game.new(m, [a, b], human=[a.id, b.id])
        state.location, state.minutes, state.apart = "tower", 600, {"lin": "square"}
    events, casts = [], {}

    async def emit(ident, event):
        events.append((ident, event))

    def factory(ident):
        cast = RequestCast(plans or {}, rulings or {}, list(tellings or [telling()] * 10))
        casts[ident] = cast
        return cast

    runtime = SceneRuntime(state, m, factory, lambda ident: Rules(dice=FakeDice()), emit)
    return runtime, casts, events


async def spin_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0)


async def test_scoped_cursors_share_only_the_authoritative_world_and_save(fake):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    va, vb = runtime.state_view(a), runtime.state_view(b)
    try:
        ga, gb = runtime.game(a), runtime.game(b)
        assert ga.cast is not gb.cast and ga.rules is not gb.rules
        assert va.investigator.id == "zhouheng" and vb.investigator.id == "lin"
        assert va.location == "tower" and vb.location == "square"
        va.pending_turn["input"] = "甲的未完行动"
        va.transcripts["keeper"] = [{"role": "user", "content": "甲的现场"}]
        vb.pending_turn["input"] = "乙的未完行动"
        vb.encounter = None
        va.add("keeper", "只在钟楼听见", who="守秘人")
        assert not vb.log and not runtime.state.pending_turn
        assert va.record is vb.record is runtime.state.record
        assert va.investigators[0] is vb.investigators[0]
        assert runtime.state.log[-1].audience == ["zhouheng"]
        restored = GameState.model_validate_json(runtime.state.model_dump_json())
        again, _, _ = made(fake, state=restored)
        try:
            assert again.state_view(a).pending_turn["input"] == "甲的未完行动"
            assert again.state_view(b).pending_turn["input"] == "乙的未完行动"
            assert again.state_view(a).transcripts["keeper"][0]["content"] == "甲的现场"
            assert again.for_member("lin") == b
        finally:
            await again.close()
    finally:
        await runtime.close()


async def test_reconcile_closes_a_split_source_and_preserves_actual_audienced_experience(fake):
    runtime, _, _ = made(fake)
    try:
        runtime.state.positions["lin"] = "tower"
        joined = runtime.reconcile()
        assert len(joined) == 1
        original = joined[0]
        view = runtime.state_view(original)
        view.add("keeper", "你们一起看见门打开了", who="守秘人")
        view.add("keeper", "只告诉周衡的信", who="守秘人", audience=["zhouheng"])
        view.transcripts["private"] = [{"role": "assistant", "content": "私人会话不得复制"}]
        runtime.state.positions["lin"] = "square"
        split = runtime.reconcile()
        assert len(split) == 2 and runtime.scenes[original].closed
        a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
        assert a in split and b in split and a != b
        assert [entry.text for entry in runtime.state_view(a).log] == ["你们一起看见门打开了", "只告诉周衡的信"]
        assert [entry.text for entry in runtime.state_view(b).log] == ["你们一起看见门打开了"]
        assert not runtime.state_view(a).transcripts and not runtime.state_view(b).transcripts
        runtime.scenes[a].at = 610
        runtime.state.minutes = 720
        runtime.state.positions["lin"] = "tower"
        assert len(runtime.reconcile()) == 2  # actual different minutes cannot silently merge
        assert runtime.scenes[b].at == 600
    finally:
        await runtime.close()


async def test_two_hour_result_waits_while_other_scene_finishes_and_continues(fake):
    short = Action(kind="use", summary="问诊十分钟")
    next_short = Action(kind="use", summary="再调查十分钟")
    long = Action(kind="use", summary="查档两小时")
    runtime, casts, _ = made(fake, plans={"A": Plan(action=short), "A2": Plan(action=next_short), "B": Plan(action=long)},
                             rulings={short.summary: Ruling(needs_roll=False, minutes=10),
                                      next_short.summary: Ruling(needs_roll=False, minutes=10),
                                      long.summary: Ruling(needs_roll=False, minutes=120, set_flags=["bell_stopped"])})
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)
    ta = tb = None
    try:
        tb = asyncio.create_task(run(runtime.graph(b), gb, {"input": "B"}))
        await spin_until(lambda: runtime.scenes[b].reservation)
        ta = asyncio.create_task(run(runtime.graph(a), ga, {"input": "A"}))
        await asyncio.wait_for(ta, 3)
        assert runtime.scenes[a].at == 610 and runtime.scenes[b].at == 600
        assert "bell_stopped" not in runtime.state.flags and not tb.done()
        assert not casts[b].reports  # final research result is not generated or committed early
        await asyncio.wait_for(run(runtime.graph(a), ga, {"input": "A2"}), 3)
        assert runtime.scenes[a].at == 620 and not tb.done()
        assert runtime.pass_time(a)
        await asyncio.wait_for(tb, 3)
        assert runtime.scenes[a].at == runtime.scenes[b].at == runtime.state.minutes == 720
        assert "bell_stopped" in runtime.state.flags
        assert len([entry for entry in runtime.state.log if entry.kind == "player"]) == 3
        assert len({entry.turn for entry in runtime.state.log if entry.kind == "player"}) == 3
    finally:
        for task in (ta, tb):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (ta, tb) if task), return_exceptions=True)
        await runtime.close()


async def test_pending_roll_in_one_scene_does_not_block_zero_minute_other_scene(fake):
    short = Action(kind="use", summary="打个招呼")
    runtime, _, _ = made(fake, plans={"B": Plan(action=short)},
                         rulings={short.summary: Ruling(needs_roll=False)})
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    try:
        runtime.scenes[a].pending_turn = {"input": "等玩家闪避"}
        runtime.scenes[a].reservation = {"turn": 1, "start": 600, "end": 600, "resources": ["zhouheng"]}
        await asyncio.wait_for(run(runtime.graph(b), runtime.game(b), {"input": "B"}), 3)
        assert runtime.scenes[a].pending_turn["input"] == "等玩家闪避"
        assert runtime.scenes[b].at == 600
    finally:
        await runtime.close()


async def test_failed_world_transaction_keeps_other_awaiting_scene_references_live(fake, monkeypatch):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)
    inv, record, npc = gb.state.investigator, gb.world.rec, gb.world.rec.entities["warden"]
    entered, release = asyncio.Event(), asyncio.Event()

    async def later():
        entered.set()
        await release.wait()
        inv.luck -= 1
        npc.look = "保留了原对象"

    def broken(op, world, oid):
        world.st.investigators[1].luck = 1
        world.rec.entities["warden"].look = "应回滚"
        world.st.flags.append("应回滚")
        raise ValueError("离线注入失败")

    task = asyncio.create_task(later())
    try:
        await entered.wait()
        luck = inv.luck
        monkeypatch.setattr(SetFlag, "settle", broken)
        result = ga.world.apply(SetFlag(flag="bad"))
        assert not result.ok and "应回滚" not in runtime.state.flags
        assert gb.world.rec is record and runtime.state.find("lin") is inv
        assert gb.world.rec.entities["warden"] is npc and inv.luck == luck
        release.set()
        await task
        assert runtime.state.find("lin").luck == luck - 1
        assert ga.world.rec.entities["warden"].look == "保留了原对象"
    finally:
        release.set()
        await task
        await runtime.close()
