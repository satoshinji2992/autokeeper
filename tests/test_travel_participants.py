"""Physical travel and its time use the seats that actually chose the shared journey."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, BeforeTravel, Plan, Ruling
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.engine.timing import SceneTimeBoundary
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import Follow, Move, Occur

from .test_party_places import made
from .test_request_execution import RequestCast


async def travelling(fake, actions: list[Action], *, hosted: bool = True):
    game, _cast, events = made(fake, third=True)
    cast = RequestCast({str(n): Plan(action=action) for n, action in enumerate(actions)}, {}, [])
    game.cast, cast.world = cast, game.world
    game.table_turn_based = hosted
    game.begin_round()
    await game.arrange_party([(inv.id, str(n)) for n, inv in enumerate(game.state.investigators[:len(actions)])])
    game.state.pending_turn = {"input": "0", "active": 0, "party_token": "0"}
    return game, cast, events


def move(**kwargs) -> Action:
    return Action(kind="move", summary="前往报社", destination="office", **kwargs)


@pytest.mark.parametrize("after_success", [False, True])
async def test_two_declared_travellers_leave_a_third_seat_and_its_clock_in_place(fake, after_success):
    action = move(companions=["lin"], arrival_after_success=after_success)
    game, _cast, _events = await travelling(fake, [action, move(), Action(kind="wait", summary="留守照料")])
    st, start = game.state, game.state.minutes
    try:
        turn = await game.resolve(action, Ruling(needs_roll=False))
        assert {i.id: game.world.place(i.id) for i in st.investigators} == {
            "zhouheng": "office", "lin": "office", "chen": "square"}
        assert st.round_elapsed == {"zhouheng": 10, "lin": 10} and st.minutes == start + 10
        assert turn.interval["participants"] == game._scene_context(turn).participants == ["zhouheng", "lin"]
        assert set(next(e.motions for e in st.record.events if e.motions)) == {"zhouheng", "lin"}
        assert "square" not in game.world.rec.left
    finally:
        await game.close()


@pytest.mark.parametrize("own", [Action(kind="wait", summary="留守"), move(alone=True),
                                      move(arrival_after_success=True), move(travel="stroll"),
                                      move(before=[BeforeTravel(npc="preacher", speech="稍候")])])
async def test_an_invitation_does_not_supply_another_humans_same_journey_declaration(fake, own):
    action = move(companions=["lin"])
    game, _cast, _events = await travelling(fake, [action, own])
    try:
        turn = await game.resolve(action, Ruling(needs_roll=False))
        assert game.world.place("lin") == game.world.place("chen") == "square"
        assert turn.interval["participants"] == ["zhouheng"]
        assert "没有参加这段旅程" in "".join(turn.notes)
    finally:
        await game.close()


async def test_no_own_plan_or_a_later_move_cannot_authorize_the_first_pending_action(fake):
    action = move(companions=["lin"])
    game, _cast, _events = await travelling(fake, [action, move()])
    try:
        own = game.state.round_plan["steps"][1]
        game.state.round_plan["steps"].append({"token": "2", "plan": own["plan"]})
        game.state.round_plan["input"].append(["lin", "later"])
        own.pop("plan")
        turn = await game.resolve(action, Ruling(needs_roll=False))
        assert game.world.place("lin") == "square" and turn.interval["participants"] == ["zhouheng"]
    finally:
        await game.close()


async def test_an_explicit_group_does_not_sweep_up_a_third_scheduled_traveller(fake):
    action = move(companions=["lin"])
    game, _cast, _events = await travelling(fake, [action, move(), move()])
    try:
        turn = await game.resolve(action, Ruling(needs_roll=False))
        assert game.world.place("lin") == "office" and game.world.place("chen") == "square"
        assert turn.interval["participants"] == ["zhouheng", "lin"]
    finally:
        await game.close()


async def test_a_time_only_arrival_receipt_does_not_imply_other_seats_consent(fake):
    game, _cast, _events = await travelling(fake, [move()])
    try:
        await game._arrive("office", Turn(), party=True,
                           interval={"start": game.state.minutes, "end": game.state.minutes + 10})
        assert game.world.place("zhouheng") == "office"
        assert game.world.place("lin") == game.world.place("chen") == "square"
    finally:
        await game.close()


@pytest.mark.parametrize("alone,remote", [(True, False), (False, True)])
async def test_solo_or_a_different_origin_prevents_shared_travel(fake, alone, remote):
    action = move(companions=["lin"], alone=alone)
    game, _cast, _events = await travelling(fake, [action, move()])
    if remote:
        game.state.apart["lin"] = "tower"
    try:
        turn = await game.resolve(action, Ruling(needs_roll=False))
        assert game.world.place("lin") == ("tower" if remote else "square")
        assert turn.interval["participants"] == ["zhouheng"]
    finally:
        await game.close()


async def test_npcs_follow_the_actual_departing_leader_and_not_a_staying_teammate(fake):
    action = move(companions=["lin"])
    game, _cast, _events = await travelling(fake, [action, move(), Action(kind="wait", summary="留守")])
    assert game.world.apply(Move(entity="warden", to="square", actor="keeper")).ok
    assert game.world.apply(Follow(npc="preacher", leader="lin", destination="office", actor="preacher")).ok
    assert game.world.apply(Follow(npc="warden", leader="chen", destination="office", actor="warden")).ok
    try:
        await game.resolve(action, Ruling(needs_roll=False))
        assert game.world.place("preacher") == "office" and not game.world.entity("preacher").following
        assert game.world.place("warden") == "square" and game.world.entity("warden").following == "chen"
    finally:
        await game.close()


@pytest.mark.parametrize("mentioned", [False, True])
async def test_agreed_npc_company_waits_for_committed_work_and_survives_a_new_batch(fake, mentioned):
    action = move(companions=["preacher"] if mentioned else [])
    game, _, _ = await travelling(fake, [action])
    start, actor = game.state.minutes, game.state.investigator.id
    assert game.world.apply(Follow(npc="preacher", leader=actor, destination="office", actor="preacher")).ok
    await game.prepare(action)
    # Another scene commits this real action while this scene still holds its earlier cursor.
    with game.world.at(start + 4):
        assert game.world.apply(Occur(id="companion-ready", actor="preacher", participants=[actor],
                                     place="square", text="已经打开车门，等调查员上车。")).ok
    try:
        turn = await game.resolve(action, Ruling(needs_roll=False))
        assert game.world.place("preacher") == game.world.place(actor) == "office"
        assert not game.world.entity("preacher").following
        assert game.state.minutes == start + 14  # The ten-minute journey starts after the committed action.
        moves = [event for event in game.world.rec.events if "preacher" in event.motions]
        assert len(moves) == 1
        assert moves[0].motions["preacher"] == {
            "origin": "square", "destination": "office", "start": start + 4, "end": start + 14}
        assert game.world.place_at("preacher", start + 4) == game.world.place_at(actor, start + 4) == ""
        assert any(event.id == "ev:companion-ready" for event in game.world.rec.events)
        assert not any("没有随行" in note or "没有一起来" in note for note in turn.notes)
    finally:
        await game.close()


@pytest.mark.parametrize("after_success", [False, True])
async def test_saved_npc_company_arrival_retimes_the_original_journey_only_once(fake, after_success):
    action = move(arrival_after_success=after_success)
    game, cast, _ = await travelling(fake, [action])
    actor, start = game.state.investigator.id, game.state.minutes
    assert game.world.apply(Follow(npc="preacher", leader=actor, destination="office", actor="preacher")).ok
    saved = []

    def cut_before_arrival():
        steps = game.state.pending_turn.get("resolution", {}).get("steps", {})
        travelled = (any(key.endswith("entry/elapsed") and row.get("done") for key, row in steps.items())
                     if after_success else steps.get("travel", {}).get("done"))
        if travelled:
            saved.append(game.state.model_dump_json())
            raise ConnectionError("行程已计时，抵达尚未提交")

    game.checkpoint = cut_before_arrival
    with pytest.raises(ConnectionError):
        await game.resolve(action, Ruling(needs_roll=False))
    restored = GameState.model_validate_json(saved[-1])
    resumed = Game(game.m, restored, cast, Rules(dice=fake))
    resumed.table_turn_based = True
    with resumed.world.at(start + 4):
        assert resumed.world.apply(Occur(id="companion-ready", actor="preacher", participants=[actor],
                                        place="square", text="打开车门，等调查员上车。")).ok
    try:
        await resumed.resolve(action, Ruling(needs_roll=False))
        await resumed.resolve(action, Ruling(needs_roll=False))
        assert resumed.world.place("preacher") == resumed.world.place(actor) == "office"
        assert restored.minutes == start + 14 and restored.round_elapsed == {actor: 14}
        moves = [event for event in restored.record.events if actor in event.motions or "preacher" in event.motions]
        assert len(moves) == 2 and all(
            motion["start"] == start + 4 and motion["end"] == start + 14
            for event in moves for motion in event.motions.values())
    finally:
        await resumed.close()
        await game.close()


async def test_retiming_agreed_company_obeys_the_scene_time_boundary_without_double_travel(fake):
    action = move()
    game, _, _ = await travelling(fake, [action])
    actor, start = game.state.investigator.id, game.state.minutes
    assert game.world.apply(Follow(npc="preacher", leader=actor, destination="office", actor="preacher")).ok
    with game.world.at(start + 4):
        assert game.world.apply(Occur(id="companion-ready", actor="preacher", place="square", text="现在出发。")).ok
    permitted, waits = {"end": start + 10}, []

    def commit(end, people):
        if end > permitted["end"]:
            raise SceneTimeBoundary(end)

    async def wait(end):
        waits.append(end)
        permitted["end"] = end

    game.time_commit, game.time_wait = commit, wait
    try:
        await game.resolve(action, Ruling(needs_roll=False))
        await game.resolve(action, Ruling(needs_roll=False))
        assert waits == [start + 14] and game.state.round_elapsed == {actor: 14}
        assert game.world.place("preacher") == game.world.place(actor) == "office"
        assert len([event for event in game.world.rec.events if "preacher" in event.motions]) == 1
    finally:
        await game.close()


async def test_explicit_solo_departure_still_leaves_an_agreed_companion_behind(fake):
    action = move(alone=True)
    game, _, _ = await travelling(fake, [action])
    actor, start = game.state.investigator.id, game.state.minutes
    assert game.world.apply(Follow(npc="preacher", leader=actor, destination="office", actor="preacher")).ok
    with game.world.at(start + 4):
        assert game.world.apply(Occur(id="companion-ready", actor="preacher", place="square", text="现在出发。")).ok
    try:
        await game.resolve(action, Ruling(needs_roll=False))
        assert game.world.place(actor) == "office" and game.world.place("preacher") == "square"
        assert game.state.minutes == start + 10 and not game.world.entity("preacher").following
        assert not any("preacher" in event.motions for event in game.world.rec.events)
    finally:
        await game.close()


async def test_a_teammates_later_original_move_costs_no_second_journey_and_keeps_its_other_intent(fake):
    action, own = move(companions=["lin"]), move(npc="preacher", speech="请谈谈钟声")
    game, _cast, _events = await travelling(fake, [action, own, Action(kind="wait", summary="留守")])
    st, start = game.state, game.state.minutes
    try:
        await game.resolve(action, Ruling(needs_roll=False))
        game.complete_party_action()
        assert game.pending_party() == [("lin", "1"), ("chen", "2")]
        st.pending_turn.clear()
        st.active, st.turn = 1, st.turn + 1
        game._focus()
        st.pending_turn = {"input": "1", "active": 1, "party_token": "1"}
        turn = await game.resolve(own, await game.rule(own))
        assert st.minutes == start + 10 and st.round_elapsed == {"zhouheng": 10, "lin": 10}
        assert turn.action.speech == "请谈谈钟声" and turn.action.npc == "preacher"
        assert len([e for e in st.record.events if "lin" in e.motions]) == 1
    finally:
        await game.close()


@pytest.mark.parametrize("after_success", [False, True])
async def test_reload_reuses_the_frozen_travellers_before_arrival_without_double_time(fake, after_success):
    action = move(companions=["lin"], arrival_after_success=after_success)
    game, cast, _events = await travelling(fake, [action, move(), Action(kind="wait", summary="留守")])
    games, saves = [game], []
    start = game.state.minutes

    def interrupted():
        steps = game.state.pending_turn.get("resolution", {}).get("steps", {})
        committed = (any(key.endswith("entry/elapsed") and row.get("done") for key, row in steps.items())
                     if after_success else steps.get("travel", {}).get("done"))
        if committed:
            saves.append(game.state.model_dump_json())
            raise ConnectionError("旅程计时已保存，抵达尚未提交")

    game.checkpoint = interrupted
    try:
        with pytest.raises(ConnectionError):
            await game.resolve(action, Ruling(needs_roll=False))
        state = GameState.model_validate_json(saves[-1])
        # The execution receipt, rather than a fresh scan of later batch progress, owns this journey.
        state.round_plan["done"].append("1")
        resumed = Game(game.m.model_copy(deep=True), state, cast, Rules(dice=fake))
        resumed.table_turn_based = True
        games.append(resumed)
        turn = await resumed.resolve(action, Ruling(needs_roll=False))
        assert resumed.world.place("lin") == "office" and resumed.world.place("chen") == "square"
        assert state.minutes == start + 10 and state.round_elapsed == {"zhouheng": 10, "lin": 10}
        assert turn.interval["participants"] == ["zhouheng", "lin"]
        assert len([e for e in state.record.events if "lin" in e.motions]) == 1
    finally:
        for instance in games:
            await instance.close()


@pytest.mark.parametrize("subset", [False, True])
async def test_single_player_keeps_default_ai_company_but_an_explicit_subset_stays_a_subset(fake, subset):
    action = move(companions=["lin"] if subset else [])
    game, _cast, _events = await travelling(fake, [action], hosted=False)
    game.state.human = ["zhouheng"]
    try:
        await game.resolve(action, Ruling(needs_roll=False))
        assert game.world.place("lin") == "office"
        assert game.world.place("chen") == ("square" if subset else "office")
    finally:
        await game.close()


async def test_an_autonomous_ai_teammates_own_wait_is_respected_at_a_hosted_table(fake):
    action = move()
    game, _cast, _events = await travelling(fake, [action, Action(kind="wait", summary="留守")])
    game.state.human = ["zhouheng", "chen"]
    try:
        await game.resolve(action, Ruling(needs_roll=False))
        assert game.world.place("lin") == game.world.place("chen") == "square"
    finally:
        await game.close()
