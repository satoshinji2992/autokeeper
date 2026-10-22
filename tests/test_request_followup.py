"""Only the final accepted scene can continue a request; saved handoffs never replay effects."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Plan, Ruling, Thing
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Event
from autokeeper.world import ops as O

from .test_request_execution import RequestCast, make, module, run, telling


async def test_flag_event_following_telling_returns_pending_instead_of_running_the_old_next(fake):
    original = "先整理桌面，再去广场。"
    first = Action(kind="use", summary="周衡整理桌面")
    proposed = Plan(action=Action(kind="move", summary="周衡前往广场", destination="square"))
    pending = "先听完守钟人对突然敲门声的解释，再决定是否去广场。"
    m = module()
    m.events = [Event(id="door_knocking", title="有人敲门", when="flag:bell_stopped", where="tower",
                      text="敲门声打断了原定行程。", read_aloud="门外响起急促的敲门声。",
                      performer="warden", performance="告诉来客你听见门外急促敲门，问他是否先留下。")]
    cast = RequestCast({original: Plan(action=first, remaining="去广场。")},
                       {first.summary: Ruling(needs_roll=False, minutes=1)}, [
                           telling(next=proposed, scene={"flags": ["bell_stopped"]}),
                           telling(pending=pending),
                       ])
    game, graph = make(fake, cast, m=m)
    try:
        events, offers = await run(graph, game, {"input": original})
        assert not offers and not cast.tellings
        assert game.state.turn == 1 and game.state.location == "tower" and not game.state.pending_turn
        assert game.state.flags == ["bell_stopped"] and game.state.fired == ["door_knocking"]
        assert cast.planned == [original] and [action.summary for action in cast.ruled] == [first.summary]
        assert len(cast.performances) == 1 and len(cast.reports) == 2
        assert len([event for event in events if event["type"] == "performance"]) == 1
        assert not any(event["type"] == "move" for event in events)
        note = game.state.unfinished[game.state.investigator.id]
        assert note["original"] == original and note["remaining"] == pending
        assert len(note["completed"]) == 1
    finally:
        await game.close()


async def test_a_partial_scene_with_a_real_distant_person_preserves_the_tail(fake):
    original = "先整理桌面，再去广场。"
    first = Action(kind="use", summary="周衡整理桌面")
    proposed = Plan(action=Action(kind="move", summary="周衡前往广场", destination="square"))
    # The cup is feasible; the preacher cannot cross the route from the office in one minute.
    scene = {"here": ["warden", "preacher"], "things": [{"name": "普通杯子", "holder": "warden"}]}
    cast = RequestCast({original: Plan(action=first, remaining="去广场。")},
                       {first.summary: Ruling(needs_roll=False, minutes=1)}, [
                           telling(next=proposed, scene=scene), telling(next=proposed, scene=scene),
                           telling(next=proposed),
                       ])
    game, graph = make(fake, cast)
    assert game.world.apply(O.Move(entity="preacher", to="office")).ok
    try:
        _, offers = await run(graph, game, {"input": original})
        assert not offers and not cast.tellings
        assert game.state.turn == 1 and game.state.location == "tower" and game.world.place("preacher") == "office"
        assert next(e for e in game.world.rec.entities.values() if e.name == "普通杯子").at == "warden"
        assert [action.summary for action in cast.ruled] == [first.summary]
        note = game.state.unfinished[game.state.investigator.id]
        assert note["remaining"] == "去广场。" and "场面未完整落实" in note["reason"]
        assert not game.state.pending_turn
    finally:
        await game.close()


async def test_a_validated_handover_that_goes_stale_before_landing_cannot_continue_after_reload(fake):
    original = "先接过盒子，再去广场。"
    first = Action(kind="use", summary="周衡接过盒子")
    proposed = Plan(action=Action(kind="move", summary="周衡前往广场", destination="square"))
    cast = RequestCast({original: Plan(action=first, remaining="去广场。")},
                       {first.summary: Ruling(needs_roll=False, minutes=1)}, [])
    game, graph = make(fake, cast)
    games, saved = [game], []
    who = game.state.investigator.id
    parcel = game.world.apply(O.Bring(name="空木盒", holder="warden", actor="warden")).data
    cast.tellings.append(telling(next=proposed, scene={"here": ["warden"],
                                                     "holds": [{"item": parcel, "holder": who}]}))

    def checkpoint() -> None:
        receipt = game.state.pending_turn.get("keeper", {})
        if receipt.get("units") and not receipt.get("landed"):
            assert receipt["scene_complete"]
            # A real intervening operation changes the giver before the saved scene can land.
            assert game.world.apply(O.Transfer(item=parcel, to="tower", actor="warden")).ok
            saved.append(game.state.model_dump_json())
            raise RuntimeError("场面已校验，交付前断线")

    game.checkpoint = checkpoint
    try:
        with pytest.raises(RuntimeError, match="交付前断线"):
            await run(graph, game, {"input": original})
        assert len(saved) == 1 and len(cast.reports) == 1 and not cast.tellings
        game, graph = make(fake, cast, m=game.m.model_copy(deep=True), state=GameState.model_validate_json(saved[0]))
        games.append(game)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and len(cast.reports) == 1 and cast.planned == [original]
        assert game.state.turn == 1 and game.state.location == "tower" and not game.state.pending_turn
        assert game.world.rec.entities[parcel].at == "tower" and "空木盒" not in game.state.investigator.inventory
        assert [action.summary for action in cast.ruled] == [first.summary]
        assert "场面未完整落实" in game.state.unfinished[who]["reason"]
    finally:
        for old in games:
            await old.close()


async def test_a_checkpointed_next_plan_survives_reload_without_replaying_the_prefix_or_allocating_an_extra_turn(fake):
    original = "先在桌上找到木尺，再原地等两分钟。"
    first = Action(kind="investigate", summary="周衡在桌上找木尺", target="桌面")
    final = Action(kind="wait", summary="周衡原地等两分钟")
    cast = RequestCast({original: Plan(action=first, remaining="原地等两分钟。")}, {
        first.summary: Ruling(needs_roll=True, skill="侦查", minutes=1, takes=[Thing(name="木尺")]),
        final.summary: Ruling(needs_roll=False, minutes=2),
    }, [telling(next=Plan(action=final)), telling()])
    game, graph = make(fake, cast)
    games, saved = [game], []
    start = game.state.minutes
    fake.queue(1)

    def checkpoint() -> None:
        request = game.state.pending_turn.get("request", {})
        if request.get("ready"):
            saved.append(game.state.model_dump_json())
            raise RuntimeError("下一段已安装并保存后断线")

    game.checkpoint = checkpoint
    try:
        with pytest.raises(RuntimeError, match="下一段已安装"):
            await run(graph, game, {"input": original})
        assert len(saved) == 1 and not fake.values
        state = GameState.model_validate_json(saved[0])
        assert state.turn == 1 and state.minutes == start + 1
        assert state.pending_turn["request"]["part"] == 1 and state.pending_turn["request"]["ready"]
        assert state.pending_turn["arrangement"]["at"] == 2
        assert len(state.pending_turn["request"]["completed"]) == 1
        assert state.investigator.inventory.count("木尺") == 1
        game, graph = make(fake, cast, m=game.m.model_copy(deep=True), state=state)
        games.append(game)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not cast.tellings and cast.planned == [original]
        assert game.state.turn == 2 and game.state.minutes == start + 3 and not game.state.pending_turn
        assert [action.summary for action in cast.ruled] == [first.summary, final.summary]
        assert len(game.state.checks) == 1 and game.state.checks[0].turn == 1 and game.state.checks[0].passed
        assert game.state.investigator.inventory.count("木尺") == 1
        assert len([e for e in game.world.rec.entities.values() if e.name == "木尺"]) == 1
        assert [entry.text for entry in game.state.log if entry.kind == "player"] == [original]
        assert len([entry for entry in game.state.log if entry.kind == "dice"]) == 1
    finally:
        for old in games:
            await old.close()
