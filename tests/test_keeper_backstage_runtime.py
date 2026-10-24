"""Prepared time and pending world judgments meet the real KP's single tool session.

These are offline execution/recovery checks. The scripted writer does not assess
whether a live model can create a convincing encounter or perform good dialogue.
"""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper import config, paths
from autokeeper.agents.cast import Cast
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import Game
from autokeeper.engine.scenes import SceneRuntime
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules
from autokeeper.world import ops as O
from autokeeper.world import scheduler
from autokeeper.world.record import Step

from .test_keeper_memory_runtime import words
from .test_keeper_tools_execution import final, role_calls, shut, voice
from .test_keeper_world_tools import begin, tool
from .test_request_execution import run
from .test_scene import SCRIPT, WRITER, sandbox  # noqa: F401

PRIVATE_TASK = "决定是否把证人安置到教堂阁楼，不能只按原计划等待。"
PRIVATE_MESSAGE = "证人偷偷托人转告：送信的人已经背叛了我们。"
PRIVATE_PROMISE = "在天亮前替证人安排藏身处。"
CHANGED_GOAL = "先保护证人，暂停广场上的布道。"
BODY = "你收好手边的清点册，窗外已经泛白。"


def table(fake, *, module=None, state=None, party=False):
    """Use normal model-role settings, without the old tests' minds=0 shortcut."""
    if module is None:
        module = Module.load(paths.EXAMPLE)
        module.events, module.endings, module.foreshadow, module.agendas, module.showcases = [], [], [], [], []
    if state is None:
        first = module.pregens[0].model_copy(deep=True)
        people = [first]
        if party:
            people += [first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True),
                       first.model_copy(update={"id": "chen", "name": "陈明"}, deep=True)]
        state = Game.new(module, people, human=[person.id for person in people])
        state.location, state.minutes = "tower", 20 * 60 + 20
    cast = Cast(config.Config(roles={role: WRITER for role in config.DEFAULT_ROLES},
                              visual_limit=0, imagegen=False), module)
    game = Game(module, state, cast, Rules(dice=fake))
    return game, turn_graph.build(game, InMemorySaver()), cast


def enlist(game, npc="preacher"):
    assert game.world.apply(O.Enlist(npc=npc, goals=["维持眼下的安排"], actor="engine")).ok


def fixed_work(game):
    """Two prepared changes finish in private, without deciding NPC intentions."""
    now = game.state.minutes
    game.world.rec.simulated = now
    enlist(game)
    assert game.world.apply(O.SetPlan(npc="preacher", steps=[Step(
        id="go_to_office", text="布道者去办公室整理讲稿", due=now + 30,
        achieved="at:preacher:office", meet=False,
        ops=[{"kind": "move", "entity": "preacher", "to": "office", "visibility": "private"}],
    )], actor="engine")).ok
    assert game.world.apply(O.Schedule(event="office_lamp", at=now + 60, place="office",
                                       participants=["preacher"], text="布道者关好办公室的灯。",
                                       ops=[{"kind": "remember", "npc": "preacher", "text": "灯已经关好。"}],
                                       actor="keeper", visibility="private")).ok


def judgments(game):
    """All three are facts/tasks for the omniscient KP, not public narration."""
    now = game.state.minutes
    game.world.rec.simulated = now
    enlist(game)
    assert game.world.apply(O.SetPlan(npc="preacher", steps=[Step(
        id="protect_witness", text=PRIVATE_TASK, due=now + 1, completion="report", meet=False,
    )], actor="engine")).ok
    assert game.world.apply(O.MakePromise(promise="protect_before_dawn", by="preacher", to="warden",
                                          text=PRIVATE_PROMISE, due=now + 1, public=False,
                                          actor="preacher", visibility="private")).ok
    assert game.world.apply(O.Occur(id="witness_message", text=PRIVATE_MESSAGE, place="square",
                                   participants=["preacher"], actor="engine", visibility="private")).ok


def arrange(game, *, future=90):
    return tool("world_commit", operations=[
        {"kind": "plan", "npc": "preacher", "goals": [CHANGED_GOAL], "steps": [
            {"id": "prepare_hiding_place", "text": "先核对藏身处的钥匙", "due": game.state.minutes + future,
             "completion": "report", "meet": False},
        ]},
    ])


def assert_no_independent_world_model(game):
    assert not game.state.usage.get("mind", {}).get("calls")
    assert not any(key.startswith("mind:") for key in game.cast.sessions)
    assert not role_calls("Scene") and not role_calls("Review")


async def test_overnight_fixed_plan_and_schedule_need_no_world_or_director_model(fake):
    game, graph, cast = table(fake)
    fixed_work(game)
    now = game.state.minutes
    SCRIPT.extend([*begin(minutes=600), final(BODY)])
    try:
        events, offers = await run(graph, game, {"input": "我整理行李，然后等到天亮。"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert game.state.minutes == now + 600 and game.state.day == 1
        assert game.world.place("preacher") == "office"
        assert game.world.agent("preacher").plan[0].status == "done"
        assert game.world.rec.scheduled["office_lamp"].status == "done"
        assert len([event for event in game.world.rec.events if event.text == "布道者关好办公室的灯。"]) == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("HostedReply")) == 1
        assert_no_independent_world_model(game)
        assert [event["text"] for event in events if event["type"] == "keeper_delta"] == [BODY]
        assert "布道者关好办公室的灯。" not in json.dumps(events, ensure_ascii=False)
    finally:
        await shut([game], [cast])


async def test_due_plan_promise_and_private_message_are_judged_by_the_same_keeper(fake):
    game, graph, cast = table(fake)
    judgments(game)
    SCRIPT.extend([*begin(minutes=30), arrange(game, future=120), final(BODY)])
    try:
        events, offers = await run(graph, game, {"input": "我在钟楼等半个钟头。"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        hosted = role_calls("HostedReply")
        assert len(hosted) == 2
        first = words(hosted[0]["messages"])
        assert PRIVATE_TASK in first and PRIVATE_PROMISE in first and PRIVATE_MESSAGE in first
        assert game.world.agent("preacher").goals == [CHANGED_GOAL]
        assert any(step.id == "prepare_hiding_place" and step.status == "pending"
                   for step in game.world.agent("preacher").plan)
        assert '"status": "committed"' in words(hosted[-1]["messages"])
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert_no_independent_world_model(game)
        exposed = json.dumps([event for event in events if event["type"] in {
            "keeper_start", "keeper_delta", "keeper_end", "npc", "aside", "presentation",
        }], ensure_ascii=False)
        assert PRIVATE_TASK not in exposed and PRIVATE_PROMISE not in exposed and PRIVATE_MESSAGE not in exposed
        assert [event["text"] for event in events if event["type"] == "keeper_delta"] == [BODY]
    finally:
        await shut([game], [cast])


async def test_present_npc_can_still_offer_help_without_being_addressed(fake):
    game, graph, cast = table(fake)
    judgments(game)
    who = game.state.investigator.id
    SCRIPT.extend([
        *begin(minutes=30),
        arrange(game, future=120),
        voice("warden", "看到来访者独自整理行李，主动拿擦手布给他；不用提任何异地消息。"),
        {"text": "我来搭把手，这块布给你。", "items": [{"name": "擦手布", "to": who}]},
        final("老人递来擦手布，帮你拢好了散开的行李。"),
    ])
    try:
        events, offers = await run(graph, game, {"input": "我不打扰老人，自己整理一下行李。"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert len(role_calls("Dialogue")) == 1 and len(role_calls("HostedReply")) == 3
        assert game.state.investigator.inventory.count("擦手布") == 1
        spoken, = (event for event in events if event["type"] == "npc")
        assert spoken["text"] == "我来搭把手，这块布给你。"
        own_context = words(role_calls("Dialogue")[0]["messages"])
        assert PRIVATE_MESSAGE not in own_context and PRIVATE_TASK not in own_context
        assert_no_independent_world_model(game)
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("after_tool", [False, True])
async def test_keeper_timeout_then_reload_keeps_pending_judgment_and_does_not_repeat_time(fake, after_tool):
    game, graph, cast = table(fake)
    judgments(game)
    fixed_work(game)
    # Restore the discretionary plan after the fixed-work helper installed its own.
    assert game.world.apply(O.SetPlan(id="restore-protect-witness", npc="preacher", steps=[Step(
        id="protect_witness", text=PRIVATE_TASK, due=game.state.minutes + 1,
        completion="report", meet=False,
    )], actor="engine")).ok
    start = game.state.minutes
    games, casts = [game], [cast]
    SCRIPT.extend(begin(minutes=600))
    if after_tool:
        SCRIPT.append(arrange(game, future=1200))
    SCRIPT.append(RuntimeError("scripted KP request interrupted"))
    try:
        with pytest.raises(RuntimeError, match="scripted KP request interrupted"):
            await run(graph, game, {"input": "先等到天亮。"})
        assert not SCRIPT and game.world.rec.scheduled["office_lamp"].status == "done"
        assert any(PRIVATE_TASK in need.text for need in scheduler.pending(game.world))
        if after_tool:
            assert game.world.agent("preacher").goals == [CHANGED_GOAL]
        else:
            assert any(step.id == "protect_witness" and step.status == "pending"
                       for step in game.world.agent("preacher").plan)
        restored = GameState.model_validate_json(game.state.model_dump_json())
        module = game.m.model_copy(deep=True)
        await shut([game], [cast])
        game, graph, cast = table(fake, module=module, state=restored)
        games.append(game)
        casts.append(cast)
        SCRIPT.extend([final(BODY)] if after_tool else [arrange(game), final(BODY)])
        events, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert game.state.turn == 1 and game.state.minutes == start + 600
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1
        assert len(role_calls("HostedReply")) == 3
        assert PRIVATE_TASK in words(role_calls("HostedReply")[-2]["messages"])
        assert game.world.agent("preacher").goals == [CHANGED_GOAL]
        assert len([event for event in game.world.rec.events if event.text == "布道者关好办公室的灯。"]) == 1
        assert [event["text"] for event in events if event["type"] == "keeper_delta"] == [BODY]
        assert_no_independent_world_model(game)
    finally:
        await shut(games, casts)


async def test_split_scene_tells_only_acknowledge_their_own_present_npc_work(fake):
    initial, _, first_cast = table(fake, party=True)
    state, module = initial.state, initial.m
    module.location("tower").within = None
    state.apart = {"lin": "square", "chen": "square"}
    await shut([initial], [first_cast])
    casts = []

    async def emit(ident, event):
        pass

    def factory(ident):
        cast = Cast(config.Config(roles={role: WRITER for role in config.DEFAULT_ROLES},
                                  visual_limit=0, imagegen=False), module)
        casts.append(cast)
        return cast

    runtime = SceneRuntime(state, module, factory, lambda _: Rules(dice=fake), emit)
    a, b = runtime.for_member(state.investigators[0].id), runtime.for_member("lin")
    game = runtime.game(a)
    now = game.state.minutes
    try:
        for npc, task in (("warden", "检查钟楼的备用灯油"), ("preacher", PRIVATE_TASK)):
            enlist(game, npc)
            assert game.world.apply(O.SetPlan(npc=npc, steps=[Step(
                id=f"{npc}_decision", text=task, due=now, completion="report", meet=False,
            )], actor="engine")).ok
        game.world.rec.simulated = now
        SCRIPT.extend([*begin(), final("你合上了钟楼里的清点册。")])
        _, offers = await run(runtime.graph(a), game, {"input": "我合上清点册。"})
        assert not offers and not SCRIPT
        pending = scheduler.pending(game.world)
        assert any(need.npc == "preacher" and PRIVATE_TASK in need.text for need in pending)
        assert not any(need.npc == "warden" for need in pending)
        assert game.world.agent("warden").plan[0].status == "pending"  # attention is not task completion
        all_before = {key for key in state.record.backstage}

        other = runtime.game(b)
        SCRIPT.extend([*begin(), arrange(other), final("你在广场上合好了自己的本子。")])
        _, offers = await run(runtime.graph(b), other, {"input": "我把自己的本子合好。"})
        assert not offers and not SCRIPT
        assert other.world.agent("preacher").goals == [CHANGED_GOAL]
        assert not scheduler.pending(other.world)
        assert all_before.issubset(state.record.backstage)
        assert not any(need.npc == "warden" for need in scheduler.pending(other.world))
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 2
        assert len(role_calls("HostedReply")) == 3
        assert_no_independent_world_model(game)
        assert_no_independent_world_model(other)
    finally:
        await runtime.close()
        for cast in casts:
            await cast.close()


async def test_saved_final_does_not_acknowledge_a_newer_message_it_never_read(fake):
    game, graph, cast = table(fake)
    judgments(game)
    games, casts, saved = [game], [cast], []
    SCRIPT.extend([*begin(minutes=30), final(BODY)])

    def interrupt():
        receipt = game.state.pending_turn.get("keeper", {})
        if "text" in receipt and not receipt.get("landed") and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError("scripted final saved before delivery")

    game.checkpoint = interrupt
    try:
        with pytest.raises(RuntimeError, match="final saved before delivery"):
            await run(graph, game, {"input": "等半小时再整理清点册。"})
        assert len(saved) == 1 and not SCRIPT
        module = game.m.model_copy(deep=True)
        state = GameState.model_validate_json(saved[0])
        await shut([game], [cast])
        game, graph, cast = table(fake, module=module, state=state)
        games.append(game)
        casts.append(cast)
        old, = (need for need in scheduler.pending(game.world) if need.kind == "affected" and PRIVATE_MESSAGE in need.text)
        old_version = old.version
        new_message = "证人又送来口信：现在不能再去教堂。"
        assert game.world.apply(O.Occur(id="second_witness_message", text=new_message, place="square",
                                       participants=["preacher"], actor="engine", visibility="private")).ok
        await scheduler.advance(game.world, game.state.minutes, game.state.minutes,
                                events_only=True, tick="while-final-is-saved")
        current, = (need for need in scheduler.pending(game.world) if new_message in need.text)
        assert current.id != old.id and old.version == old_version
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert len(role_calls("HostedReply")) == 1
        pending, = (need for need in scheduler.pending(game.world) if need.id == current.id)
        assert pending.version == current.version and pending.status == "pending"
        assert not any(need.id == old.id for need in scheduler.pending(game.world))
        SCRIPT.extend([*begin(), final("你把清点册放回了包里。")])
        _, offers = await run(graph, game, {"input": "我把清点册收进包里。"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert new_message in words(role_calls("HostedReply")[-1]["messages"])
        assert not any(need.id == current.id for need in scheduler.pending(game.world))
        assert_no_independent_world_model(game)
    finally:
        await shut(games, casts)
