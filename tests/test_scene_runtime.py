"""Real scoped Games/Graphs, one world, and causal independent time reservations."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Plan, Ruling
from autokeeper.engine.game import Game
from autokeeper.engine.scenes import SceneRuntime
from autokeeper.engine.state import GameState
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import SetFlag

from .conftest import FakeDice
from .test_request_execution import RequestCast, module, run, telling


def made(fake, *, plans=None, rulings=None, tellings=None, state=None, m=None):
    if m is None:
        m = module()
        m.location("tower").within = None  # these tests exercise two independent hosting regions
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


async def test_the_whole_party_scene_stays_public_for_shared_memory_and_recap(fake):
    from autokeeper.engine import audience

    runtime, _, _ = made(fake)
    try:
        runtime.state.positions["lin"] = "tower"
        ident, = runtime.reconcile()
        view = runtime.state_view(ident)
        public = view.add("keeper", "大家听见钟声停了。", who="守秘人")
        secret = view.add("keeper", "仅向一人交代的消息。", audience=["zhouheng"])
        assert public.audience is None and audience.shared(public)
        assert public in audience.log(runtime.state) and secret not in audience.log(runtime.state)
        assert secret.audience == ["zhouheng"]
    finally:
        await runtime.close()


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
        runtime.scenes[a].member_at["zhouheng"] = 610
        runtime.state.minutes = 720
        runtime.state.positions["lin"] = "tower"
        merged = runtime.reconcile()
        assert len(merged) == 1  # one hosting region keeps one KP; global max is not its actual time
        assert runtime.scenes[merged[0]].at == 610
        assert runtime.scenes[merged[0]].member_at == {"zhouheng": 610, "lin": 600}
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
        result = ga.world.apply(SetFlag(flag="bell_stopped"))
        assert not result.ok and "应回滚" not in runtime.state.flags
        assert "离线注入失败" in "；".join(result.problems)
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


async def test_prepared_time_ticket_restores_without_new_input_log_or_relative_deadline(fake):
    long = Action(kind="use", summary="查档两小时")
    runtime, _, _ = made(fake, plans={"B": Plan(action=long)},
                         rulings={long.summary: Ruling(needs_roll=False, minutes=120)})
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    task = asyncio.create_task(run(runtime.graph(b), runtime.game(b), {"input": "B"}))
    try:
        await spin_until(lambda: runtime.scenes[b].reservation.get("blocked"))
        runtime.scenes[b].busy = True
        saved = runtime.state.model_dump_json()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        restored, casts, _ = made(fake, state=GameState.model_validate_json(saved))
        try:
            assert not restored.scenes[b].busy and restored.scenes[b].reservation["end"] == 720
            assert restored.pass_time(a)
            await asyncio.wait_for(run(restored.graph(b), restored.game(b), {"input": "/retry"}), 3)
            assert not casts[b].planned and not casts[b].ruled
            assert restored.scenes[b].at == 720
            assert len([entry for entry in restored.state.log if entry.kind == "player"]) == 1
        finally:
            await restored.close()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


async def test_group_movement_updates_only_actual_members_and_preserves_other_scene(fake):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)
    try:
        assert runtime.pass_time(b)
        ga.state.turn = runtime.allocate_turn()
        action = Action(kind="move", summary="独自去报社", destination="office", alone=True)
        ruling = Ruling(needs_roll=False)
        runtime.reserve(a, action, ruling)
        await runtime.wait(a)
        await ga.resolve(action, ruling)
        assert ga.world.place("zhouheng") == "office" and gb.world.place("lin") == "square"
        assert runtime.scenes[b].place == "square"
        assert ga.state.apart["lin"] == "square"
    finally:
        await runtime.close()


async def test_failed_parse_does_not_erase_other_scene_log_deeds_or_monotonic_turn_ids(fake):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    va, vb = runtime.state_view(a), runtime.state_view(b)
    before = va.turn
    va.turn = runtime.allocate_turn()
    va.add("player", "失败请求", who=va.investigator.name)
    failed_id = va.turn
    vb.turn = runtime.allocate_turn()
    vb.add("player", "另一现场已成立", who=vb.investigator.name)
    va.rollback_parse(before, 0, 0)
    assert [entry.text for entry in runtime.state.log] == ["另一现场已成立"]
    assert all(deed.turn != failed_id for deed in runtime.state.deeds)
    assert any(deed.turn == vb.turn for deed in runtime.state.deeds)
    assert runtime.allocate_turn() > vb.turn
    await runtime.close()


async def test_revoked_pass_rechecks_the_synchronous_time_commit_before_spending(fake):
    from autokeeper.engine.game import Turn
    from autokeeper.engine.timing import SceneTimeBoundary

    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga = runtime.game(a)
    try:
        assert runtime.pass_time(b)
        ga.state.turn = runtime.allocate_turn()
        runtime.reserve(a, Action(kind="use", summary="搜查"), Ruling(needs_roll=False, minutes=20))
        await runtime.wait(a)
        runtime.revoke_pass(b)  # accepted during a prior await, before any real time mutation
        try:
            ga._spend(20, Turn())
        except SceneTimeBoundary as boundary:
            assert boundary.end == 620
        else:
            raise AssertionError("不能越过刚收到的同伴声明")
        assert runtime.scenes[a].at == runtime.scenes[b].at == 600
    finally:
        await runtime.close()


async def test_resource_conflict_retains_both_attempts_and_revalidates_the_waiting_result(fake):
    from autokeeper.engine.timing import SceneResourceConflict
    from autokeeper.world.ops import Move

    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)
    try:
        ga.state.turn = runtime.allocate_turn()
        gb.state.turn = runtime.allocate_turn()
        ga.state.pending_turn["input"] = "甲致电守钟人"
        gb.state.pending_turn["input"] = "乙也致电守钟人"
        action = Action(kind="talk", summary="致电守钟人", npc="warden", communication="remote")
        runtime.reserve(a, action, Ruling(needs_roll=False))
        runtime.reserve(b, action, Ruling(needs_roll=False))
        try:
            await runtime.wait(a)
        except SceneResourceConflict as conflict:
            assert {attempt["original"] for attempt in conflict.details["attempts"]} == {"甲致电守钟人", "乙也致电守钟人"}
        else:
            raise AssertionError("不能按网络顺序静默完成共同人物的冲突")
        # Represents an actual shared KP/arbiter handoff; the engine never acknowledges it on its own.
        runtime.resolve_resource_conflict(a)
        await runtime.wait(a)
        result = ga.world.apply(Move(entity="warden", to="square"))
        assert result.ok
        runtime.release(a)
        try:
            await runtime.wait(b)
        except SceneResourceConflict as conflict:
            assert conflict.details["changed"] and conflict.details["resources"] == ["warden"]
        else:
            raise AssertionError("对方已改变共同人物，旧裁定不能直接兑现")
        assert gb.state.pending_turn["input"] == "乙也致电守钟人"
        assert "resolution" not in gb.state.pending_turn
    finally:
        await runtime.close()


async def test_scoped_presence_projects_committed_movements_at_local_time(fake):
    from autokeeper.world.ops import Move

    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)
    try:
        with ga.world.at(610, start=600):
            assert ga.world.apply(Move(entity="warden", to="square")).ok
        assert "warden" not in gb.world.present("square")
        runtime.scenes[b].at = 605
        assert "warden" not in gb.world.present("square") and "warden" not in gb.world.present("tower")
        runtime.scenes[b].at = 610
        assert "warden" in gb.world.present("square")
    finally:
        await runtime.close()


@pytest.mark.parametrize("by_hand", [False, True])
@pytest.mark.parametrize("recover", [False, True])
async def test_joint_kp_order_then_real_commit_refreshes_the_other_graph_ruling(fake, by_hand, recover):
    from autokeeper.agents.cast import PartyPlan, PartyStep
    from autokeeper.engine.timing import SceneResourceConflict

    action = Action(kind="talk", summary="打电话请教守钟人", npc="warden", communication="remote", speech="请问现在方便吗？")
    runtime, casts, _ = made(fake, plans={"甲私下原话": Plan(action=action), "乙私下原话": Plan(action=action)},
                             rulings={action.summary: Ruling(needs_roll=False, minutes=5)})
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)

    async def voice(*args, **kwargs):
        return "现在方便。"

    casts[a].npc = casts[b].npc = voice
    seen, contexts = [], []
    tasks = []

    async def joint(state, inputs):
        # The KP explicitly chooses B first, overriding the earlier network/global-turn order.
        assert {item.text for item in inputs} == {"甲私下原话", "乙私下原话"}
        return PartyPlan(steps=[PartyStep(token=sid, plan=Plan(action=action), related=[a if sid == b else b],
                                         coordination="两次来电，先接林秋的，再按通话实际结果处理周衡的请求")
                                for sid in (b, a)])

    async def first_rule(state, asked, at=None, coordination="", **kwargs):
        seen.append(("B", len(runtime.state.record.events)))
        contexts.append(coordination)
        return Ruling(needs_roll=by_hand, skill="侦查" if by_hand else "", set_flags=["bell_stopped"])

    async def later_rule(state, asked, at=None, coordination="", **kwargs):
        seen.append(("A", "bell_stopped" in runtime.state.flags))
        contexts.append(coordination)
        return Ruling(needs_roll=False, missing=["先前通话已改变局势"])

    try:
        # Persist both original stages before either result may settle.
        ta = asyncio.create_task(run(runtime.graph(a), ga, {"input": "甲私下原话"}))
        tasks.append(ta)
        await spin_until(lambda: runtime.scenes[a].reservation)
        tb = asyncio.create_task(run(runtime.graph(b), gb, {"input": "乙私下原话", "by_hand": by_hand}))
        tasks.append(tb)
        await spin_until(lambda: ta.done())
        assert isinstance(ta.exception(), SceneResourceConflict) and not tb.done()
        casts[a].party_plan, casts[a].arbiter, casts[b].arbiter = joint, later_rule, first_rule
        await runtime.prepare_conflict(a)
        if recover:
            saved = runtime.state.model_dump_json()
            tb.cancel()
            await asyncio.gather(tb, return_exceptions=True)
            await runtime.close()
            # A model task that was already scheduled can finish while cancellation is
            # delivered, after the saved crash boundary. It committed no result or roll;
            # count the new process's preparation below, not that discarded computation.
            seen.clear()
            contexts.clear()
            runtime, casts, _ = made(fake, state=GameState.model_validate_json(saved))
            ga, gb = runtime.game(a), runtime.game(b)
            casts[a].npc = casts[b].npc = voice
            casts[a].arbiter, casts[b].arbiter = later_rule, first_rule
            tb = asyncio.create_task(run(runtime.graph(b), gb, {"input": "/retry"}))
            tasks.append(tb)
        again = asyncio.create_task(run(runtime.graph(a), ga, {"input": "/retry"}))
        tasks.append(again)
        if by_hand:
            from langgraph.types import Command

            _, offers = await asyncio.wait_for(tb, 3)
            assert offers and offers[0]["kind"] == "roll" and not again.done()
            assert "bell_stopped" not in runtime.state.flags
            gb.rules.dice.queue(10)
            await asyncio.wait_for(run(runtime.graph(b), gb, Command(resume=True)), 3)
        await asyncio.wait_for(asyncio.gather(again, tb), 3)
        assert [entry[0] for entry in seen] == ["B", "A"]
        assert seen[-1] == ("A", True)  # re-ruled after B's actual result, never the original ruling
        assert all("甲私下原话" not in context and "乙私下原话" not in context for context in contexts)
        assert all("列表先后不预定胜者" in context for context in contexts)
        assert all("请问现在方便吗" not in context for context in contexts)
        assert len([entry for entry in runtime.state.log if entry.kind == "player"]) == 2
        assert casts[a].planned == ([] if recover else ["甲私下原话"])
        assert casts[b].planned == ([] if recover else ["乙私下原话"])
        assert not ga.state.pending_turn and not gb.state.pending_turn
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.close()


@pytest.mark.parametrize("incomplete", [False, True])
async def test_missing_joint_plan_never_acknowledges_or_discards_either_attempt(fake, incomplete):
    from autokeeper.agents.cast import PartyPlan, PartyStep
    from autokeeper.engine.game import PreparationError
    from autokeeper.engine.timing import SceneResourceConflict

    runtime, casts, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)
    action = Action(kind="talk", summary="致电守钟人", npc="warden", communication="remote")
    try:
        for game, sid, original in ((ga, a, "原话甲"), (gb, b, "原话乙")):
            game.state.turn = runtime.allocate_turn()
            game.state.pending_turn["input"] = original
            runtime.reserve(sid, action, Ruling(needs_roll=False))
        with pytest.raises(SceneResourceConflict):
            await runtime.wait(a)

        async def unavailable(state, inputs):
            return PartyPlan(steps=[PartyStep(token=a, plan=Plan(action=action))]) if incomplete else None

        casts[a].party_plan = unavailable
        with pytest.raises(PreparationError):
            await runtime.prepare_conflict(a)
        assert not runtime.state.scene_handoffs
        assert ga.state.pending_turn["input"] == "原话甲" and gb.state.pending_turn["input"] == "原话乙"
        assert not runtime.scenes[a].reservation.get("joint_reviewed")
        assert not casts[a].ruled and not casts[b].ruled
    finally:
        await runtime.close()


async def test_failed_parse_keeps_private_reply_and_handout_delivered_during_its_model_await(fake):
    runtime, _, _ = made(fake)
    a = runtime.for_member("zhouheng")
    view = runtime.state_view(a)
    before = view.turn
    view.turn = runtime.allocate_turn()
    view.add("player", "准备失败的行动", who=view.investigator.name)
    view.add("keeper", "另一任务刚交回的私信", who="守秘人", audience=["zhouheng"])
    view.add("handout", "另一任务交付的材料", who="守秘人", audience=["zhouheng"])
    view.rollback_parse(before, 0, 0)
    assert [entry.kind for entry in runtime.state.log] == ["keeper", "handout"]
    assert [entry.kind for entry in view.log] == ["keeper", "handout"]
    assert any(deed.kind == "handout" for deed in runtime.state.deeds)
    assert not any(deed.kind == "act" for deed in runtime.state.deeds)
    assert view.logged == runtime.state.logged == 2
    await runtime.close()


async def test_rooms_and_floors_in_one_region_keep_one_kp_without_sharing_hearing(fake):
    from autokeeper.engine.scenes import hosting_region

    m = module()
    m.location("office").within = "tower"  # a room, within a floor, within the same hosting region
    runtime, _, _ = made(fake, m=m)
    try:
        scene = runtime.scenes[runtime.for_member("zhouheng")]
        assert scene.id == runtime.for_member("lin")
        assert scene.region == hosting_region(m, "office") == "square"
        game = runtime.game(scene.id)
        cast = game.cast
        runtime.state.positions["lin"] = "office"
        scene.member_at.update(zhouheng=600, lin=605)
        scene.at = 605
        assert runtime.reconcile() == [scene.id]
        assert runtime.game(scene.id).cast is cast
        assert runtime.state.positions == {"zhouheng": "tower", "lin": "office"}
        assert game.world.observers("tower") == ["zhouheng"]
        assert game.world.observers("office") == ["lin"]
        game.state.add("keeper", "钟楼里低声说的话", who="守秘人")
        assert runtime.state.log[-1].audience == ["zhouheng"]
        # Changing which room the same host describes does not physically move the other player.
        game.state.active = 1
        game._focus()
        assert game.state.location == "office" and game.world.place("zhouheng") == "tower"
        assert runtime.reconcile() == [scene.id]
    finally:
        await runtime.close()


@pytest.mark.parametrize("recover", [False, True])
async def test_real_party_round_split_keeps_the_idle_players_own_time_after_round_cleanup(fake, recover):
    m = module()
    m.location("tower").within = None
    m.location("tower").travel_minutes["square"] = 0
    m.location("square").travel_minutes["office"] = 10
    a = m.pregens[0].model_copy(deep=True)
    b = a.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [a, b], human=[a.id, b.id])
    state.location, state.minutes = "tower", 600
    go = Action(kind="move", summary="周衡独自前往报社", destination="office", alone=True)
    stay = Action(kind="use", summary="林秋在原地记下门牌")
    runtime, _, _ = made(fake, state=state, m=m, plans={"走": Plan(action=go), "记": Plan(action=stay)},
                         rulings={stay.summary: Ruling(needs_roll=False)})
    original = runtime.for_member("zhouheng")
    game = runtime.game(original)
    try:
        game.begin_round()
        await game.arrange_party([("zhouheng", "走"), ("lin", "记")])
        await run(runtime.graph(original), game, {"input": "走"})
        game.state.active = 1
        await run(runtime.graph(original), game, {"input": "记"})
        assert game.state.round_elapsed == {"zhouheng": 10, "lin": 0}
        game.end_round()
        assert not game.state.round_elapsed and game.state.round_spent is None
        assert runtime.scenes[original].member_at == {"zhouheng": 610, "lin": 600}
        if recover:
            saved = runtime.state.model_dump_json()
            await runtime.close()
            runtime, _, _ = made(fake, state=GameState.model_validate_json(saved), m=m)
        else:
            runtime.reconcile()
        sid_a, sid_b = runtime.for_member("zhouheng"), runtime.for_member("lin")
        assert sid_a != sid_b and runtime.scenes[original].closed
        assert runtime.scenes[sid_a].at == 610 and runtime.scenes[sid_b].at == 600
        assert runtime.state.positions == {"zhouheng": "office", "lin": "tower"}
        assert runtime.state.minutes == 610
    finally:
        await runtime.close()


async def test_retired_and_closed_hosts_release_their_owned_dice_and_cast(fake):
    runtime, casts, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    closed = []

    class OwnedDice(FakeDice):
        def __init__(self, ident):
            super().__init__()
            self.ident = ident

        def close(self):
            closed.append(("dice", self.ident))

    runtime.rules_factory = lambda ident: Rules(dice=OwnedDice(ident))
    ga, gb = runtime.game(a), runtime.game(b)

    async def cast_a():
        closed.append(("cast", a))

    async def cast_b():
        closed.append(("cast", b))

    casts[a].close, casts[b].close = cast_a, cast_b
    runtime.graph(a)
    await runtime.retire(a)
    assert a not in runtime._games and a not in runtime._graphs
    assert closed == [("cast", a), ("dice", a)] and runtime._games[b] is gb
    await runtime.close()
    assert closed == [("cast", a), ("dice", a), ("cast", b), ("dice", b)]
    assert ga is not gb and not runtime._games
