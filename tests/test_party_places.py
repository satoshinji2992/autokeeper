"""Ordinary sequential split scenes keep physical positions when the acting seat changes."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling, Sketch
from autokeeper.engine import graph
from autokeeper.engine.encounter.model import EncounterState
from autokeeper.engine.encounter.plan import EncounterStart
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.engine.perception import Appearance
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Clue, Creature, Ending, Event, Item, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import context, knowledge, scene
from autokeeper.world.ops import Bring, Follow, Occur

from .test_engine import FakeCast


class PlaceCast(FakeCast):
    def __init__(self) -> None:
        super().__init__({})
        self.scenes: list[tuple[str, str, str, str]] = []

    async def intent(self, state, text):
        self.scenes.append((state.investigator.id, state.location, state.ongoing, state.journey))
        return await super().intent(state, text)


def made(fake, *, third: bool = False) -> tuple[Game, PlaceCast, list[dict]]:
    module = Module.load(EXAMPLE)
    module.events, module.endings, module.foreshadow = [], [], []
    lead = module.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    party = [lead, friend]
    if third:
        party.append(lead.model_copy(update={"id": "chen", "name": "陈明"}, deep=True))
    module.items += [Item(id="public_tower_tool", name="塔内黄铜尺", description="刻度清楚", where="tower"),
                     Item(id="public_square_tool", name="广场木直尺", description="木制", where="square")]
    state = Game.new(module, party, human=[i.id for i in party])
    state.location, state.turn = "square", 1
    state.visited = [loc.id for loc in module.locations]
    state.npc("warden").met = state.npc("preacher").met = True
    cast, events = PlaceCast(), []

    async def emit(event: dict) -> None:
        events.append(event)

    game = Game(module, state, cast, Rules(dice=fake), emit=emit)  # type: ignore[arg-type]
    return game, cast, events


async def test_solo_travel_leaves_the_other_player_and_next_talk_uses_their_actual_scene(fake):
    game, cast, events = made(fake)
    st = game.state
    first, second = [i.id for i in st.investigators]
    cast.script["独去钟楼"] = (Action(kind="move", summary="周衡独自去钟楼", destination="tower", alone=True),
                               Ruling(needs_roll=False))
    cast.script["向布道者道谢"] = (Action(kind="talk", summary="林秋向布道者道谢", npc="preacher", speech="谢谢。"),
                                  Ruling(needs_roll=False))
    app = graph.build(game)
    try:
        await app.ainvoke({"input": "独去钟楼"})
        assert st.location == "tower" and st.apart == {second: "square"}
        assert game.world.place(first) == "tower" and game.world.place(second) == "square"
        assert "square" not in game.world.rec.left  # someone is still there
        st.ongoing, st.journey = "周衡正与守钟人争执。", "office"
        before, moves = st.minutes, len([e for e in events if e["type"] == "move"])
        st.active = 1
        await app.ainvoke({"input": "向布道者道谢"})
        assert cast.scenes[-1] == (second, "square", "", "")
        assert st.location == "square" and st.apart == {first: "tower"}
        assert st.minutes == before and len([e for e in events if e["type"] == "move"]) == moves
        assert cast.npc_calls[-1][0] == "preacher"
        assert "广场" in cast.npc_calls[-1][1] and "钟楼" not in cast.npc_calls[-1][1]
        assert game.world.place("warden") == "tower" and game.world.place("preacher") == "square"
    finally:
        await game.close()


@pytest.mark.parametrize("alone", [False, True])
async def test_real_arrival_moves_only_its_group_and_keeps_a_third_players_remote_position(fake, alone):
    game, _, _ = made(fake, third=True)
    st = game.state
    first, second, third = [i.id for i in st.investigators]
    st.apart[third] = "office"
    try:
        await game._arrive("tower", Turn(), alone=alone, party=True)
        expected = {first: "tower", second: "square" if alone else "tower", third: "office"}
        assert {i.id: game.world.place(i.id) for i in st.investigators} == expected
        assert st.apart == {who: place for who, place in expected.items() if place != "tower"}
        await game._arrive("office", Turn(), alone=alone, party=True)
        assert third not in st.apart and game.world.place(third) == "office"
        assert game.world.place(second) == ("square" if alone else "office")
    finally:
        await game.close()


async def test_focus_is_read_only_for_world_history_npc_memories_and_visit_effects(fake):
    game, _, events = made(fake, third=True)
    st = game.state
    first, second, third = [i.id for i in st.investigators]
    st.apart = {second: "tower", third: "office"}
    assert game.world.apply(Occur(text="钟楼里守钟人向林秋解释齿轮声。", place="tower", participants=["warden"])).ok
    assert game.world.apply(Occur(text="广场上传来钟声。", place="square", participants=["preacher"])).ok
    st.ongoing, st.journey, st.view_kind = "广场上争论还没结束。", "office", "custom"
    before_record, before_npcs = st.record.model_dump_json(), {k: v.model_dump_json() for k, v in st.npcs.items()}
    before_time, before_visits, before_fired = st.minutes, list(st.visited), list(st.fired)
    st.active = 1
    try:
        game._focus()
        assert st.location == "tower" and st.apart == {first: "square", third: "office"}
        assert not st.ongoing and not st.journey and not st.view_kind
        assert game._here == ("warden",)
        assert (st.minutes, st.visited, st.fired) == (before_time, before_visits, before_fired)
        assert st.record.model_dump_json() == before_record and not events
        assert {k: v.model_dump_json() for k, v in st.npcs.items()} == before_npcs
        game._focus()
        assert st.record.model_dump_json() == before_record  # repeated focus is also inert
    finally:
        await game.close()


async def test_a_companion_follows_its_actual_departing_leader_when_another_group_member_acts(fake):
    game, _, _ = made(fake)
    st = game.state
    st.location = "tower"
    leader = st.investigators[0].id
    assert game.world.apply(Follow(npc="warden", leader=leader, destination="office", actor="warden")).ok
    st.active = 1
    try:
        await game._arrive("office", Turn(), party=True)
        assert not st.apart and all(game.world.place(i.id) == "office" for i in st.investigators)
        assert game.world.place("warden") == "office" and not game.world.entity("warden").following
    finally:
        await game.close()


async def test_remote_item_positions_visibility_and_npc_company_use_each_actual_place(fake):
    game, _, _ = made(fake)
    st, w = game.state, game.world
    first, second = [i.id for i in st.investigators]
    st.apart[second] = "tower"
    parcel = w.apply(Bring(name="林秋的纸包", holder=second, actor="keeper")).data
    try:
        assert w.place(second) == w.place(parcel) == "tower"
        assert knowledge.item_known(w, "public_tower_tool", second)
        assert not knowledge.item_known(w, "public_tower_tool", first)
        assert knowledge.item_known(w, "public_square_tool", first)
        assert not knowledge.item_known(w, "public_square_tool", second)
        tower = context.npc_view(w, "warden").text
        square = context.npc_view(w, "preacher").text
        tower_company = next(line for line in tower.splitlines() if line.startswith("【你在】"))
        square_company = next(line for line in square.splitlines() if line.startswith("【你在】"))
        assert "林秋" in tower_company and "周衡" not in tower_company
        assert "周衡" in square_company and "林秋" not in square_company
        assert "林秋" in context._concerns(w, "warden") and "周衡" not in context._concerns(w, "warden")
        assert "调查员就在身边" in context.npc_brief(w, "warden")
        st.active = 1
        game._focus()
        assert w.place(first) == "square" and w.place(second) == "tower" and w.place(parcel) == "tower"
        assert not knowledge.item_known(w, "public_tower_tool", first)
    finally:
        await game.close()


async def test_new_witnesses_are_local_and_switching_focus_does_not_rewrite_their_old_audience(fake):
    game, _, _ = made(fake)
    st, w = game.state, game.world
    first, second = [i.id for i in st.investigators]
    st.apart[second] = "tower"
    first_event = w.apply(Occur(text="广场上布道者敲了一下木箱。", place="square", participants=["preacher"]))
    old = w.rec.event(first_event.event).model_dump_json()
    st.active = 1
    try:
        game._focus()
        assert w.rec.event(first_event.event).model_dump_json() == old
        new = w.apply(Occur(text="守钟人把齿轮停了下来。", place="tower", participants=["warden"]))
        assert second in w.rec.event(new.event).witnesses and first not in w.rec.event(new.event).witnesses
        assert first in w.rec.event(first_event.event).witnesses and second not in w.rec.event(first_event.event).witnesses
    finally:
        await game.close()


async def test_a_scene_cannot_hand_an_object_to_an_investigator_at_another_place(fake):
    game, _, _ = made(fake)
    st, w = game.state, game.world
    first, second = [i.id for i in st.investigators]
    st.apart[second] = "tower"
    parcel = w.apply(Bring(name="待转交的封口纸包", holder=first, actor="keeper")).data
    before = w.domain()
    try:
        plan = scene.plan(w, {"here": ["preacher"], "holds": [{"item": parcel, "holder": second}]},
                          scene.Context(travel=game._travel, elapsed=0))
        assert any("不在一处" in problem for problem in plan.problems)
        scene.land(w, plan.held)
        assert w.entity(parcel).at == first and w.domain() == before
    finally:
        await game.close()


async def test_reload_and_pending_retry_preserve_the_original_seat_and_physical_places(fake):
    game, cast, _ = made(fake)
    st = game.state
    first, second = [i.id for i in st.investigators]
    st.apart[second] = "tower"
    cast.script["林秋查看塔墙"] = (Action(kind="investigate", summary="林秋查看塔墙", target="塔墙"),
                                 Ruling(needs_roll=False))
    st.active = 1
    st.pending_turn = {"input": "林秋查看塔墙", "active": 1, "by_hand": False}
    game._focus()
    saved = GameState.model_validate_json(st.model_dump_json())
    await game.close()
    loaded = Game(game.m.model_copy(deep=True), saved, cast, Rules(dice=fake))  # type: ignore[arg-type]
    saved.active = 0  # a UI selection cannot reassign an unfinished receipt
    try:
        await graph.build(loaded).ainvoke({"input": "/retry"})
        assert cast.scenes[-1][:2] == (second, "tower")
        assert saved.location == "tower" and saved.apart == {first: "square"} and not saved.pending_turn
    finally:
        await loaded.close()


async def test_a_remote_action_finishes_without_abandoning_the_encounter_or_acquiring_an_incorrect_pending_turn(fake):
    from autokeeper.engine.encounter.plan import DeclaredAction, EncounterTurnPlan

    from .encounter_kit import Keeper, arrange, begin

    game, _, _ = made(fake)
    st = game.state
    first, second = [investigator.id for investigator in st.investigators]
    st.apart[second] = "tower"
    cast = Keeper({
        "我看看塔墙": (Action(kind="use", summary="林秋查看塔墙", target="塔墙"), Ruling(needs_roll=False, minutes=1)),
        "回到广场": (Action(kind="move", summary="林秋独自回到广场", destination="square", alone=True),
                    Ruling(needs_roll=False)),
    }, act=EncounterTurnPlan(action=DeclaredAction(do="defend", summary="周衡继续防守")),
       arrange=EncounterTurnPlan(npcs=[arrange("preacher", DeclaredAction(do="defend", summary="布道者戒备"))]))
    game.cast, cast.world = cast, game.world
    app = graph.build(game)
    try:
        await begin(game, ["preacher"])
        original = st.encounter.model_dump_json()
        encounter_id = st.encounter.id
        st.active = 1
        await app.ainvoke({"input": "我看看塔墙"})
        assert not st.pending_turn and cast.ruled[-1].summary == "林秋查看塔墙"
        assert st.encounter.model_dump_json() == original and game.encounter() is None
        assert game.world.place(first) == game.world.place("preacher") == "square"
        assert game.world.place(second) == game.world.place("warden") == "tower"
        await app.ainvoke({"input": "回到广场"})
        assert not st.pending_turn and game.encounter().id == encounter_id
        assert st.encounter.place == "square" and game.world.place("preacher") == "square"
        assert game.world.place(first) == game.world.place(second) == "square"
        assert game.world.place("warden") == "tower"
        st.active = 0
        await app.ainvoke({"input": "原现场继续防守"})
        assert not st.pending_turn and st.encounter.id == encounter_id
        assert any(receipt.actor == first and receipt.phase == "committed" for receipt in st.encounter.receipts)

        st.active, st.apart = 1, {second: "tower"}
        st.pending_turn = {"input": "原现场尚未完成的动作", "active": 0, "resolution": {"steps": {"kept": True}}}
        pending = st.model_dump_json()
        with pytest.raises(ValueError, match="上一回合尚未讲完"):
            game._focus()
        assert st.model_dump_json() == pending  # neither the encounter nor its receipt is discarded
    finally:
        await game.close()


async def test_switching_a_pending_ordinary_turn_to_another_location_is_refused_without_mutation(fake):
    game, _, _ = made(fake)
    st = game.state
    st.apart[st.investigators[1].id] = "tower"
    st.pending_turn = {"input": "原动作", "active": 0, "action": {"kind": "other", "summary": "原动作"}}
    st.active = 1
    before = st.model_dump_json()
    try:
        with pytest.raises(ValueError, match="上一回合尚未讲完"):
            game._focus()
        assert st.model_dump_json() == before
    finally:
        await game.close()


async def test_an_already_ended_encounter_does_not_prevent_an_ordinary_remote_focus(fake):
    game, _, events = made(fake)
    st = game.state
    first, second = [i.id for i in st.investigators]
    st.apart[second] = "tower"
    st.encounter = EncounterState(id="finished-fight", mode="fight", phase="ended", place="square",
                                  started_turn=1, participants=[])
    ended = st.encounter.model_dump_json()
    st.active = 1
    try:
        game._focus()
        assert st.location == "tower" and st.apart == {first: "square"}
        assert st.encounter.model_dump_json() == ended and not events
    finally:
        await game.close()


async def test_a_local_event_only_charges_sanity_to_conscious_witnesses_and_damage_to_its_local_actor(fake):
    game, _, events = made(fake, third=True)
    st = game.state
    first, second, third = st.investigators
    st.apart[second.id] = "tower"
    third.conditions.append("昏迷")
    event = Event(id="local_accident", title="木箱裂开", when="now", where="square", text="", sanity="1/1", damage="1",
                  read_aloud="木箱裂开，一股古怪的影子从裂口里钻出。")
    health = [i.hp for i in st.investigators]
    fake.queue(10)
    try:
        await game._fire(event, Turn())
        assert [i.san for i in st.investigators] == [54, 55, 55]
        assert [i.hp for i in st.investigators] == [health[0] - 1, *health[1:]]
        shown = next(e for e in events if e["type"] == "event")
        assert shown["audience"] == [first.id]
        assert next(e for e in st.log if e.kind == "event").audience == [first.id]
        assert [e["who"] for e in events if e["type"] == "sanity"] == [first.name]
        assert not fake.values and second.id not in st.sightings and third.id not in st.sightings
    finally:
        await game.close()


async def test_an_event_does_not_hurt_the_active_seat_when_that_person_is_elsewhere(fake):
    game, _, events = made(fake)
    st = game.state
    st.apart[st.investigators[1].id] = "tower"
    st.active = 1
    event = Event(id="splinter", title="木片飞出", when="now", where="square", text="", damage="1", read_aloud="木片飞出。")
    health = [i.hp for i in st.investigators]
    try:
        await game._fire(event, Turn())
        assert [i.hp for i in st.investigators] == health
        assert not [e for e in events if e["type"] == "damage"]
        assert next(e for e in events if e["type"] == "event")["audience"] == [st.investigators[0].id]
    finally:
        await game.close()


async def test_event_witnesses_survive_relocation_and_retry_without_including_people_at_the_destination(fake):
    game, cast, _ = made(fake)
    st = game.state
    first, second = st.investigators
    st.apart[second.id] = "tower"
    event = Event(id="dragged_to_tower", title="钟楼门内的影子", when="now", where="square", move_to="tower", text="",
                  sanity="1/1", read_aloud="黑影横过广场，你跟着它退进钟楼。")
    journal: dict = {}
    turn = Turn()
    st.pending_turn = {"input": "等待", "active": 0, "event_journal": journal}
    interrupted = False

    async def fail_after_sanity(notice: dict) -> None:
        nonlocal interrupted
        if notice["type"] == "sanity" and not interrupted:
            interrupted = True
            raise ConnectionError("notice interrupted")

    game.emit = fail_after_sanity
    fake.queue(10)
    with pytest.raises(ConnectionError, match="notice interrupted"):
        with game._journal(journal, lambda _: None):
            await game._fire(event, turn)
    assert st.location == "tower" and not st.apart and [i.san for i in st.investigators] == [54, 55]
    saved = GameState.model_validate_json(st.model_dump_json())
    saved_turn = TURN_RECORD.validate_python(TURN_RECORD.dump_python(turn, mode="json"))
    await game.close()
    notices: list[dict] = []

    async def collect(notice: dict) -> None:
        notices.append(notice)

    loaded = Game(game.m.model_copy(deep=True), saved, cast, Rules(dice=fake), emit=collect)  # type: ignore[arg-type]
    try:
        with loaded._journal(saved.pending_turn["event_journal"], lambda _: None):
            await loaded._fire(event, saved_turn)
        assert [i.san for i in saved.investigators] == [54, 55] and not fake.values
        assert len(saved_turn.sanity) == 1 and [e["who"] for e in notices if e["type"] == "sanity"] == [first.name]
        assert [e.audience for e in saved.log if e.kind == "event"] == [[first.id]]
        assert second.id not in saved.sightings
    finally:
        await loaded.close()


@pytest.mark.parametrize("unaware", ["remote", "unconscious", "unseen"])
async def test_first_sight_only_costs_actual_participants_who_can_perceive_the_horror(fake, unaware):
    game, _, events = made(fake)
    st = game.state
    first, second = st.investigators
    if unaware == "remote":
        st.apart[second.id] = "tower"
    elif unaware == "unconscious":
        second.conditions.append("昏迷")
    else:
        st.perceptions[second.id] = {"hound": Appearance(visible=False, reason="隔墙看不见")}
    game.m.creatures.append(Creature(id="hound", name="影犬", description="从门框里钻出的黑影", sanity="1/1",
                                    stats={"生命": 10, "敏捷": 40}, attacks=["扑咬 20%，伤害 1D3"]))
    fake.queue(10)
    try:
        assert await game._encounter_open(EncounterStart(mode="fight", foes=["hound"], reason="黑影扑来"), Turn())
        assert [i.san for i in st.investigators] == [54, 55] and not fake.values
        assert st.sightings == {first.id: ["hound"]}
        assert [e["who"] for e in events if e["type"] == "sanity"] == [first.name]
        assert next(e for e in events if e["type"] == "reveal")["audience"] == [first.id]
    finally:
        await game.close()


async def test_a_creature_sighting_hidden_from_one_present_player_does_not_charge_them_event_sanity(fake):
    game, _, _ = made(fake)
    st = game.state
    first, second = st.investigators
    game.m.creatures.append(Creature(id="hound", name="影犬", description="门边的黑影", sanity="1/1",
                                    stats={"生命": 10, "敏捷": 40}))
    st.perceptions[second.id] = {"hound": Appearance(visible=False, reason="隔墙看不见")}
    event = Event(id="shadow_seen", title="看见黑影", when="now", where="square", text="", sanity="1/1", sights=["hound"],
                  read_aloud="门边有一团黑影。")
    fake.queue(10)
    try:
        await game._fire(event, Turn())
        assert [i.san for i in st.investigators] == [54, 55] and not fake.values
        assert first.san_lost_today == 1 and second.san_lost_today == 0
    finally:
        await game.close()


async def test_viewing_a_sketch_only_charges_sanity_to_people_who_are_actually_there(fake):
    game, cast, _ = made(fake)
    st = game.state
    st.apart[st.investigators[1].id] = "tower"
    prompt = next(p for p in game.m.sketches if p.id == "sk_coil")
    prompt.sanity, prompt.clue = "1/1", ""

    async def draw(state, png, asked):
        return Sketch(description="同样的线条", resemblance=1)

    cast.sketch = draw
    fake.queue(10)
    try:
        await game.sketch(b"sketch", "sk_coil")
        assert [i.san for i in st.investigators] == [54, 55] and not fake.values
    finally:
        await game.close()


async def test_learning_relayed_material_still_costs_its_actual_recipient_sanity(fake):
    game, _, _ = made(fake)
    st = game.state
    st.apart[st.investigators[1].id] = "tower"
    game.m.clues.append(Clue(id="c_relayed", title="朋友来电转述的碑文", where="tower", text="逐字转述的碑文。",
                            sanity="1/1"))
    fake.queue(10)
    try:
        await game._gain(["c_relayed"], Turn(), None, heard="朋友从钟楼来电，逐字念给周衡听。")
        assert [i.san for i in st.investigators] == [54, 55] and "c_relayed" in st.clues
        assert not fake.values
    finally:
        await game.close()


async def test_the_endings_party_reward_still_reaches_investigators_at_other_places(fake):
    game, _, _ = made(fake)
    st = game.state
    st.apart[st.investigators[1].id] = "tower"
    game.m.endings.append(Ending(id="peaceful_end", title="平静结束", condition="flag:bell_stopped", text="事情结束了。",
                                sanity_gain="2"))
    try:
        await game._end("peaceful_end", Turn())
        assert st.ending == "peaceful_end" and [i.san for i in st.investigators] == [57, 57]
    finally:
        await game.close()
