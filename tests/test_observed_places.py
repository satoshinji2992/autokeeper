"""AI suggestions use sourced personal observations, rather than the world's live NPC coordinates."""

from __future__ import annotations

from autokeeper.agents.cast import Action
from autokeeper.engine.autoplay import situation
from autokeeper.engine.game import Game
from autokeeper.engine.perception import Appearance
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import Move, Occur
from autokeeper.world.store import World

from .test_engine import FakeCast


def table() -> World:
    module = Module.load(EXAMPLE)
    first = module.pregens[0].model_copy(deep=True)
    second = first.model_copy(update={"id": "other_reader", "name": "第二名记者"}, deep=True)
    state = Game.new(module, [first, second])
    state.location = "tower"
    state.visited = ["square", "tower", "office"]
    state.npc("warden").met = True
    return World(module, state)


def people(world: World, viewer: str | None) -> str:
    return next(line for line in situation(world.m, world.st, viewer).splitlines() if line.startswith("见过的人"))


def test_meeting_someone_does_not_grant_their_remote_position_without_a_source():
    world = table()
    world.st.location = "square"
    line = people(world, world.st.investigator.id)
    assert "不知道在哪" in line and "在钟楼" not in line


def test_a_private_move_never_updates_the_last_seen_place_and_the_source_survives_reload():
    world = table()
    who = world.st.investigator.id
    assert world.apply(Occur(text="调查员在钟楼和守钟人交谈。", place="tower")).ok
    known = world.st.record.sightings[who]["warden"].model_copy()
    world.st.location = "square"
    before = people(world, who)
    moved = world.apply(Move(entity="warden", to="office", visibility="private", actor="warden"))
    assert moved.ok and who not in world.st.record.event(moved.event).witnesses
    assert world.st.record.sightings[who]["warden"] == known and people(world, who) == before
    assert "上次在钟楼见过，当前位置不明" in before and "报社" not in before
    loaded = World(world.m.model_copy(deep=True), GameState.model_validate_json(world.st.model_dump_json()))
    assert people(loaded, who) == before and loaded.st.record.sightings[who]["warden"].event == known.event


def test_a_witnessed_departure_does_not_tell_its_destination_even_if_that_place_is_known():
    world = table()
    who = world.st.investigator.id
    moved = world.apply(Move(entity="warden", to="office", actor="warden", visibility="public"))
    assert moved.ok and who in world.st.record.event(moved.event).witnesses
    line = people(world, who)
    assert "已离开钟楼，去向不明" in line and "报社" not in line


def test_each_seat_keeps_what_they_saw_at_the_two_ends_of_the_same_move():
    world = table()
    first, second = [i.id for i in world.st.investigators]
    world.st.apart[second] = "office"
    moved = world.apply(Move(entity="warden", to="office", actor="warden", visibility="public"))
    assert moved.ok and {first, second}.issubset(world.st.record.event(moved.event).witnesses)
    assert "已离开钟楼，去向不明" in people(world, first)
    assert "就在这里" in people(world, second)
    # Where they stand now cannot retrospectively change the earlier witness list or memories.
    world.st.location = "square"
    world.st.apart.clear()
    assert world.apply(Move(entity="warden", to="tower", actor="warden", visibility="private")).ok
    assert "已离开钟楼，去向不明" in people(world, first)
    assert "上次在报社见过，当前位置不明" in people(world, second)
    assert "不知道在哪" in people(world, None)  # neither private perspective is the table's common record


def test_a_private_exchange_only_updates_the_named_participant_not_everyone_in_the_room():
    world = table()
    first, second = [i.id for i in world.st.investigators]
    done = world.apply(Occur(text="记者低声向守钟人询问钥匙。", actor=first, participants=["warden"],
                             place="tower", visibility="private"))
    event = world.st.record.event(done.event)
    assert first in event.witnesses and second not in event.witnesses
    world.st.location = "square"
    assert "上次在钟楼见过" in people(world, first)
    assert "不知道在哪" in people(world, second)


def test_a_personal_invisible_appearance_does_not_become_a_true_identity_sighting_later():
    world = table()
    first, second = [i.id for i in world.st.investigators]
    world.st.perceptions[first] = {"warden": Appearance(name="", visible=False, reason="无法看见")}
    assert world.apply(Occur(text="钟楼内响了一声钟鸣。", place="tower")).ok
    assert "warden" not in world.st.record.sightings.get(first, {})
    assert "warden" in world.st.record.sightings[second]
    world.st.location = "square"
    world.st.perceptions.clear()
    assert "不知道在哪" in people(world, first)
    assert "上次在钟楼见过" in people(world, second)


def test_a_person_who_is_here_can_be_seen_without_exposing_elsewhere_coordinates():
    world = table()
    first, second = [i.id for i in world.st.investigators]
    world.st.apart[second] = "office"
    assert "就在这里" in people(world, first)
    assert "不知道在哪" in people(world, second)


def test_an_unconscious_investigator_does_not_gain_a_new_same_room_sighting():
    world = table()
    first, second = world.st.investigators
    first.conditions.append("昏迷")
    assert world.apply(Occur(text="守钟人走近说话。", place="tower")).ok
    assert "就在这里" not in people(world, first.id)
    assert "不知道在哪" in people(world, first.id)
    assert "就在这里" in people(world, second.id)
    assert "warden" not in world.rec.sightings.get(first.id, {})


async def test_the_first_reply_that_leaves_is_remembered_as_a_departure_not_an_ongoing_presence(fake):
    world = table()
    state, module = world.st, world.m
    state.npc("warden").met = False
    game = Game(module, state, FakeCast({}), Rules(dice=fake))
    who = state.investigator.id
    try:
        # The NPC's inline action lands before Game records its first spoken reply.
        assert game.world.apply(Move(entity="warden", to="office", actor="warden")).ok
        state.npc("warden").met = True
        game._exchanged("warden", Action(kind="talk", summary="询问姓名", speech="您好，怎么称呼您？"),
                        "我是守钟人，有事改天说，我先走了。")
        assert "已离开钟楼，去向不明" in people(game.world, who)
        assert "报社" not in people(game.world, who)
    finally:
        await game.close()


def test_a_private_report_keeps_its_speaker_and_claim_without_becoming_an_objective_coordinate():
    world = table()
    first, second = world.st.investigators
    world.st.location = "square"
    world.st.add("speech", "我刚才在报社看到了老守钟人。", who=first.name, audience=[second.id])
    view = situation(world.m, world.st, second.id)
    assert first.name in view and "我刚才在报社看到了老守钟人" in view
    assert "不知道在哪" in people(world, second.id)  # the model sees the source, not a program-certified fact
    assert "我刚才在报社看到了老守钟人" not in situation(world.m, world.st, first.id)
