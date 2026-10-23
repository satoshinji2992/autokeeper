"""Offline regressions for real split scenes, committed intervals and shared NPC time."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from autokeeper.agents.cast import Action, Failure, Plan, Ruling
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import TURN_RECORD, Game
from autokeeper.engine.state import GameState
from autokeeper.rules.coc import Rules
from autokeeper.world import scene
from autokeeper.world.ops import Occur

from .encounter_kit import begin, fight_game, play
from .test_request_execution import RequestCast, make, module, run, telling
from .test_scene import sandbox  # noqa: F401


async def test_parallel_serial_segments_keep_their_own_scene_budget_after_longer_work(fake):
    b20 = Action(kind="use", summary="林秋整理器材二十分钟")
    a5 = Action(kind="use", summary="周衡整理器材五分钟")
    a10 = Action(kind="use", summary="周衡接着检查十分钟")
    frames = []

    class Recorded(RequestCast):
        async def keeper(self, state, report, on_delta=None, lore="", *, check=None, fix=""):
            turn = TURN_RECORD.validate_python(state.pending_turn["turn"])
            ctx = game._scene_context(turn)
            frames.append((ctx.elapsed, ctx.interval))
            return await super().keeper(state, report, on_delta, lore, check=check, fix=fix)

    cast = Recorded({"B": Plan(action=b20), "A": Plan(action=a5, remaining="接着检查十分钟")}, {
        b20.summary: Ruling(needs_roll=False, minutes=20), a5.summary: Ruling(needs_roll=False, minutes=5),
        a10.summary: Ruling(needs_roll=False, minutes=10),
    }, [telling(next=Plan(action=a10)), telling()])
    game, graph = make(fake, cast, party=True)
    st, start = game.state, game.state.minutes
    try:
        game.begin_round()
        await game.arrange_party([("lin", "B"), ("zhouheng", "A")])
        st.active, game.holding = 1, True
        await run(graph, game, {"input": "B"})
        st.active, game.holding = 0, False
        await run(graph, game, {"input": "A"})
        assert frames == [(5, (start, start + 5)), (10, (start + 5, start + 15))]
        assert st.minutes == start + 20 and st.round_elapsed == {"lin": 20, "zhouheng": 15}
        assert st.round_plan["done"] == ["0", "1"] and not game.pending_party()
    finally:
        await game.close()


@pytest.mark.parametrize("alone,expected", [(True, {"lin": 20, "zhouheng": 10}),
                                             (False, {"lin": 30, "zhouheng": 30})])
@pytest.mark.parametrize("after_success", [False, True])
async def test_solo_and_group_travel_use_only_actual_travellers_cursors(fake, alone, expected, after_success):
    cast = RequestCast({}, {}, [])
    game, _ = make(fake, cast, party=True)
    st, start = game.state, game.state.minutes
    st.location = "square"
    try:
        game.begin_round()
        st.active = 1
        await game.resolve(Action(kind="use", summary="林秋检查器材"), Ruling(needs_roll=False, minutes=20))
        st.active, st.turn = 0, st.turn + 1
        action = Action(kind="move", summary="周衡去报社", destination="office", alone=alone,
                        arrival_after_success=after_success)
        turn = await game.resolve(action, Ruling(needs_roll=False))
        assert st.round_elapsed == expected and st.minutes == start + max(expected.values())
        assert turn.interval["start"] == start and turn.interval["end"] == start + expected["zhouheng"]
        assert game.world.place("zhouheng") == "office"
        assert game.world.place("lin") == ("square" if alone else "office")
        motion = next(event.motions["zhouheng"] for event in game.world.rec.events if "zhouheng" in event.motions)
        assert motion == {"origin": "square", "destination": "office",
                          "start": start if alone else start + 20, "end": start + expected["zhouheng"]}
        assert game.world.place_at("zhouheng", motion["start"] + 1) == ""
        assert game.world.place_at("zhouheng", motion["end"]) == "office"
    finally:
        await game.close()


async def test_same_npc_cannot_walk_out_and_back_using_two_people_in_one_window(fake):
    game, _ = make(fake, RequestCast({}, {}, []), party=True)
    st, start = game.state, game.state.minutes
    st.location = "square"
    try:
        game.begin_round()
        out = await game.resolve(Action(kind="use", summary="周衡整理器材"), Ruling(needs_roll=False, minutes=10))
        ctx = game._scene_context(out)
        planned = scene.plan(game.world, {"here": [], "left": [{"who": "preacher", "to": "office"}]}, ctx)
        assert not planned.problems and len(scene.land(game.world, planned.held)) == 1
        moved = next(event for event in game.world.rec.events if "preacher" in event.motions)
        assert moved.t == start + 10 and moved.motions["preacher"]["start"] == start
        st.active, st.turn = 1, st.turn + 1
        back = await game.resolve(Action(kind="use", summary="林秋也整理器材"), Ruling(needs_roll=False, minutes=10))
        denied = scene.plan(game.world, {"here": ["preacher"]}, game._scene_context(back))
        assert denied.problems and not denied.held and game.world.place("preacher") == "office"
        st.turn += 1
        st.pending_turn.clear()
        later = await game.resolve(Action(kind="use", summary="林秋再等十分钟"), Ruling(needs_roll=False, minutes=10))
        valid = scene.plan(game.world, {"here": ["preacher"]}, game._scene_context(later))
        assert not valid.problems and scene.land(game.world, valid.held)
        returned = [event for event in game.world.rec.events if "preacher" in event.motions][-1]
        assert returned.motions["preacher"]["start"] == start + 10 and returned.t == start + 20
    finally:
        await game.close()


async def test_later_arrival_is_not_witness_to_an_earlier_committed_occurrence(fake):
    game, _ = make(fake, RequestCast({}, {}, []), party=True)
    st, start = game.state, game.state.minutes
    st.location, st.apart = "square", {"lin": "office"}
    try:
        game.begin_round()
        st.active = 0
        await game.resolve(Action(kind="move", summary="周衡去报社", destination="office", alone=True),
                           Ruling(needs_roll=False, minutes=10))
        assert game.world.place("zhouheng") == "office" and st.minutes == start + 20
        with game.world.at(start + 5):
            seen = game.world.apply(Occur(id="early-office", text="窗里传来一声碰撞", place="office", visibility="public"))
        event = game.world.rec.event(seen.event)
        assert event.t == start + 5 and "lin" in event.witnesses and "zhouheng" not in event.witnesses
        restored = GameState.model_validate_json(st.model_dump_json())
        again = Game(game.m.model_copy(deep=True), restored, RequestCast({}, {}, []), Rules(dice=fake))
        try:
            assert again.world.observers("office", at=start + 5) == ["lin"]
            assert again.world.observers("office", at=start + 10) == ["zhouheng", "lin"]
        finally:
            await again.close()
    finally:
        await game.close()


async def test_remote_ordinary_action_and_return_keep_the_authoritative_encounter(fake):
    m = module()
    game, cast, _ = fight_game(m, fake, location="tower")
    st = game.state
    remote = st.investigator.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    st.investigators.append(remote)
    st.human.append(remote.id)
    st.apart[remote.id] = "square"
    await begin(game, ["warden"])
    enc = st.encounter
    st.active = 1
    cast.script["谈谈"] = (Action(kind="talk", summary="林秋向布道者问候", npc="preacher", speech="你好。"),
                             Ruling(needs_roll=False, minutes=1))
    cast.script["回去"] = (Action(kind="move", summary="林秋回钟楼", destination="tower", alone=True),
                             Ruling(needs_roll=False))
    app = turn_graph.build(game, InMemorySaver())
    try:
        _, offers = await play(app, "谈谈")
        assert not offers and st.encounter.id == enc.id and st.encounter.place == "tower"
        assert game.world.place("warden") == "tower" and game.world.place("zhouheng") == "tower"
        assert game.world.place("lin") == "square" and not game.battling() and game.world_encounter() is st.encounter
        _, offers = await play(app, "回去")
        assert not offers and game.world.place("lin") == "tower" and game.encounter().id == enc.id
        assert game.world.place("warden") == "tower" and game.world.place("zhouheng") == "tower"
        assert not any(kind == "act" for kind, *_ in cast.calls)
    finally:
        await game.close()


@pytest.mark.parametrize("mode", ["luck", "push", "drop"])
async def test_roll_choices_preserve_personal_interval_across_reload(fake, mode):
    action = Action(kind="investigate", summary="周衡核对记录", target="记录")
    cast = RequestCast({"查": Plan(action=action)}, {
        action.summary: Ruling(needs_roll=True, skill="侦查", minutes=5, on_fail=Failure(minutes=2)),
    }, [telling()])
    game, graph = make(fake, cast, party=True)
    games = [game]
    st, start = game.state, game.state.minutes
    try:
        game.begin_round()
        st.active = 1
        await game.resolve(Action(kind="use", summary="林秋整理器材"), Ruling(needs_roll=False, minutes=20))
        st.pending_turn.clear()
        st.active, st.turn = 0, st.turn + 1
        fake.queue(st.investigator.value("侦查") + 1, 5)
        _, offers = await run(graph, game, {"input": "查", "by_hand": True})
        assert offers[0]["kind"] == "roll"
        saved = GameState.model_validate_json(st.model_dump_json())
        game, graph = make(fake, cast, m=game.m, state=saved)
        games.append(game)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert offers[0]["kind"] == "roll"
        _, offers = await run(graph, game, Command(resume="roll"))
        assert offers[0]["kind"] == "push"
        choice = {"choice": "push", "how": "重新按日期对照"} if mode == "push" else mode
        _, offers = await run(graph, game, Command(resume=choice))
        assert not offers and not game.state.pending_turn
        assert game.state.minutes == start + 20
        assert game.state.round_elapsed["zhouheng"] == {"luck": 5, "push": 15, "drop": 7}[mode]
        assert len([entry for entry in game.state.log if entry.kind == "player" and entry.text == "查"]) == 1
    finally:
        for old in games:
            await old.close()


@pytest.mark.parametrize("after_success", [False, True])
async def test_committed_travel_interval_is_reused_after_arrival_disconnect(fake, after_success):
    game, _ = make(fake, RequestCast({}, {}, []), party=True)
    games, st = [game], game.state
    st.location = "square"
    start = st.minutes
    game.begin_round()
    st.active = 1
    await game.resolve(Action(kind="use", summary="林秋整理器材"), Ruling(needs_roll=False, minutes=20))
    st.pending_turn = {"input": "去报社", "active": 0}
    st.active, st.turn = 0, st.turn + 1
    action = Action(kind="move", summary="周衡独自去报社", destination="office", alone=True,
                    arrival_after_success=after_success)
    ruling = Ruling(needs_roll=False)
    saves = []

    def disconnect():
        steps = st.pending_turn.get("resolution", {}).get("steps", {})
        if any(key.endswith("arrive:office/commit") and row.get("done") for key, row in steps.items()):
            saves.append(st.model_dump_json())
            raise RuntimeError("抵达回执后断开")

    game.checkpoint = disconnect
    try:
        with pytest.raises(RuntimeError, match="抵达回执后断开"):
            await game.resolve(action, ruling)
        state = GameState.model_validate_json(saves[-1])
        game = Game(game.m.model_copy(deep=True), state, RequestCast({}, {}, []), Rules(dice=fake))
        games.append(game)
        turn = await game.resolve(action, ruling)
        assert state.minutes == start + 20 and state.round_elapsed == {"lin": 20, "zhouheng": 10}
        assert turn.interval["start"] == start and turn.interval["end"] == start + 10
        assert game.world.place("zhouheng") == "office" and game.world.place("lin") == "square"
        moves = [event for event in state.record.events if "zhouheng" in event.motions]
        assert len(moves) == 1 and moves[0].t == start + 10
    finally:
        for old in games:
            await old.close()


async def test_an_earlier_heard_claim_keeps_the_later_npc_commitment_intact(fake):
    game, _ = make(fake, RequestCast({}, {}, []), party=True)
    st, start = game.state, game.state.minutes
    st.location = "square"
    try:
        with game.world.at(start + 20):
            game.world.apply(Occur(id="later-preacher", actor="preacher", participants=["preacher"],
                                   text="他已经在稍后作出承诺", place="square", visibility="private"))
        game.begin_round()
        turn = await game.resolve(Action(kind="use", summary="周衡先检查五分钟"), Ruling(needs_roll=False, minutes=5))
        planned = scene.plan(game.world, {
            "observations": [{"witnesses": ["preacher"], "text": "他较早承诺相反的事情"}],
        }, game._scene_context(turn))
        assert not planned.problems and scene.land(game.world, planned.held)
        assert next(event for event in st.record.events if "较早承诺" in event.text).t == start + 5
        assert next(event for event in st.record.events if event.id == "ev:later-preacher").t == start + 20
        assert game.world.available_at("preacher") == start + 20
        # Two recorded claims are not a rewrite of a fact, a promise operation or an old action.
        assert not st.record.revisions and not st.record.promises
    finally:
        await game.close()


async def test_ordinary_remote_event_affects_only_its_actual_witnesses_without_leaking_into_active_report(fake):
    from autokeeper.engine.state import format_time
    from autokeeper.module.schema import Event

    game, _ = make(fake, RequestCast({}, {}, []), party=True)
    st, start = game.state, game.state.minutes
    st.location, st.apart = "square", {"lin": "office"}
    notices = []

    async def emit(event):
        notices.append(event)

    game.emit = emit
    game.m.events.append(Event(id="office-horror", title="窗里的影子", where="office", when=format_time(start + 5),
                               text="一道扭曲的影子贴过窗面。", sanity="0/1", read_aloud="一道扭曲的影子贴过窗面。"))
    before = {inv.id: inv.san for inv in st.investigators}
    fake.queue(99)
    try:
        game.begin_round()
        turn = await game.resolve(Action(kind="use", summary="周衡检查广场器材"), Ruling(needs_roll=False, minutes=20))
        assert "office-horror" in st.fired and st.find("lin").san == before["lin"] - 1
        assert st.find("zhouheng").san == before["zhouheng"] and not turn.events and not turn.sanity
        logs = [entry for entry in st.log if entry.kind in ("event", "sanity")]
        assert logs and all(entry.audience == ["lin"] and entry.time == format_time(start + 5) for entry in logs)
        assert all(event["audience"] == ["lin"] for event in notices if event["type"] in ("event", "sanity"))
    finally:
        await game.close()


async def test_remote_scripted_encounter_stays_unfired_then_begins_when_its_real_scene_is_taken(fake):
    from autokeeper.engine.state import format_time
    from autokeeper.module.schema import Event

    game, _ = make(fake, RequestCast({}, {}, []), party=True)
    st, start = game.state, game.state.minutes
    st.location, st.apart = "square", {"lin": "office"}
    game.m.events.append(Event(id="office-attack", title="守钟人拦路", where="office", when=format_time(start + 5),
                               text="守钟人堵住了门口。", present=["warden"], fight=["warden"], read_aloud="守钟人堵住了门口。"))
    try:
        game.begin_round()
        await game.resolve(Action(kind="use", summary="周衡检查广场器材"), Ruling(needs_roll=False, minutes=20))
        assert "office-attack" not in st.fired and st.encounter is None and game.world.place("warden") == "tower"
        st.pending_turn.clear()
        st.turn += 1
        await game.resolve(Action(kind="move", summary="周衡去报社", destination="office", alone=True), Ruling(needs_roll=False))
        assert "office-attack" in st.fired and st.encounter.place == "office"
        assert st.minutes == start + 30 and game.world.place("warden") == "office"
        assert next(entry for entry in st.log if entry.kind == "event").time == format_time(start + 30)
    finally:
        await game.close()


async def test_a_parallel_scene_registers_its_new_person_at_its_own_cursor(fake):
    b20 = Action(kind="use", summary="林秋检查二十分钟")
    a5 = Action(kind="use", summary="周衡和刚来的邻人整理器材")
    cast = RequestCast({"B": Plan(action=b20), "A": Plan(action=a5, people=[scene.Person(id="neighbour", name="邻人")])}, {
        b20.summary: Ruling(needs_roll=False, minutes=20), a5.summary: Ruling(needs_roll=False, minutes=5),
    }, [telling(scene={"here": ["warden", "neighbour"]})])
    game, graph = make(fake, cast, party=True)
    st, start = game.state, game.state.minutes
    try:
        game.begin_round()
        await game.arrange_party([("lin", "B"), ("zhouheng", "A")])
        st.active, game.holding = 1, True
        await run(graph, game, {"input": "B"})
        st.active, game.holding = 0, False
        _, offers = await run(graph, game, {"input": "A"})
        assert not offers and game.world.place("neighbour") == "tower"
        assert game.world.available_at("neighbour") == start
        assert game.world.place_at("neighbour", start - 1) == ""
        assert st.minutes == start + 20 and not st.unfinished
    finally:
        await game.close()


async def test_scripted_time_skip_advances_only_its_actual_scene_participants(fake):
    from autokeeper.engine.state import format_time
    from autokeeper.module.schema import Event

    game, _ = make(fake, RequestCast({}, {}, []), party=True)
    st, start = game.state, game.state.minutes
    st.location, st.apart = "square", {"lin": "office"}
    game.m.events.append(Event(id="scene-wait", title="等到约定时刻", where="square", when=format_time(start + 5),
                               text="钟声传来。", read_aloud="钟声传来。", advance_to=format_time(start + 60)))
    try:
        game.begin_round()
        turn = await game.resolve(Action(kind="use", summary="周衡等候钟声"), Ruling(needs_roll=False, minutes=5))
        assert st.minutes == start + 60 and st.round_elapsed == {"zhouheng": 60}
        assert turn.interval["end"] == start + 60 and game.world.place("lin") == "office"
        st.pending_turn.clear()
        st.active, st.turn = 1, st.turn + 1
        other = await game.resolve(Action(kind="use", summary="林秋查看报纸十分钟"), Ruling(needs_roll=False, minutes=10))
        assert other.interval["start"] == start and other.interval["end"] == start + 10
        assert st.minutes == start + 60 and st.round_elapsed == {"zhouheng": 60, "lin": 10}
    finally:
        await game.close()
