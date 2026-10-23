"""An answered choice keeps its own actor through possessions, discoveries and chase movement."""

# Imported pytest fixtures intentionally share names with injected test parameters.
# ruff: noqa: F811

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Ruling
from autokeeper.engine.encounter.plan import DeclaredAction, EncounterTurnPlan
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import knowledge
from autokeeper.world.ops import Bring

from .test_encounter_chase import change, chased, open_door, put
from .test_encounter_redeclarations import pending_redeclaration, stale_game  # noqa: F401


def elsewhere(game, location="office"):
    """A real unrelated seat outside the encounter, made the live focus."""
    lead = game.state.investigator
    old = game.world.place(lead.id)
    other = lead.model_copy(update={"id": "other", "name": "异地队友"}, deep=True)
    game.state.investigators.append(other)
    game.world.rec.entities[other.id] = game.world.rec.entities[lead.id].model_copy(update={"id": other.id, "name": other.name})
    game.state.active, game.state.location, game.state.apart = 1, location, {lead.id: old}
    return lead, other


async def revised_other(game, cast, ruling):
    pending = await pending_redeclaration(game)
    lead, other = elsewhere(game)

    async def decide(state, action, **kwargs):
        assert state.investigator.id == lead.id and game.state.investigator.id == other.id
        return ruling

    cast.arbiter = decide
    cast.modes["act"] = EncounterTurnPlan(action=DeclaredAction(do="other", summary="处理手边的事"))
    return pending, lead, other


@pytest.mark.parametrize("already_lying", [False, True])
async def test_revised_pickup_lands_in_the_owners_hands_and_actual_room(stale_game, already_lying):
    game, cast, _events = stale_game
    if already_lying:
        assert game.world.apply(Bring(name="手帕", place="tower", actor="keeper")).ok
    pending, lead, other = await revised_other(game, cast, Ruling(needs_roll=False, takes=[{"name": "手帕"}]))
    turn = await game.respond(pending.id, {"text": "我改为捡起脚边的手帕"}, who=lead.id)
    held = [item for item in game.world.rec.entities.values() if item.kind == "item" and item.name == "手帕"]
    assert len(held) == 1 and held[0].at == lead.id
    assert lead.inventory == ["手帕"] and not other.inventory
    assert game.world.place(lead.id) == "tower" and game.world.place(other.id) == "office"
    assert next(event for event in reversed(game.world.rec.events) if "手帕" in event.text).place == "tower"
    assert turn.interval["actor"] == lead.id and game._narrative_audience(turn) == [lead.id]


async def test_revised_gear_uses_the_owners_item_despite_identical_remote_inventory(stale_game):
    game, cast, _ = stale_game
    game.state.investigator.inventory.append("怀表")
    pending, lead, other = await revised_other(
        game, cast, Ruling(needs_roll=False, equipment=[{"item": "怀表", "equipment": "concealed"}]))
    await game.respond(pending.id, {"text": "把自己的怀表收好"}, who=lead.id)
    held = [item for item in game.world.rec.entities.values() if item.name == "怀表"]
    assert len(held) == 1 and held[0].at == lead.id and held[0].equipment == "concealed"
    assert other.inventory == ["怀表"] and game.state.investigator.id == other.id


async def test_revised_discovery_checks_the_owners_room_and_charges_only_the_owner(stale_game, fake):
    game, cast, events = stale_game
    clue = game.m.clue("c_diary")
    clue.handout, clue.sanity, clue.mythos = None, "1/1", 1
    pending, lead, other = await revised_other(game, cast, Ruling(needs_roll=False, auto_clues=[clue.id]))
    before = lead.san, other.san, lead.value("克苏鲁神话"), other.value("克苏鲁神话")
    fake.queue(20)
    await game.respond(pending.id, {"text": "核对手边日记的记载"}, who=lead.id)
    assert (lead.san, other.san) == (before[0] - 1, before[1])
    assert (lead.value("克苏鲁神话"), other.value("克苏鲁神话")) == (before[2] + 1, before[3])
    assert knowledge.clue_known(game.state, clue.id, lead.id)
    assert not knowledge.clue_known(game.state, clue.id, other.id)
    assert all(event.get("audience") == [lead.id] for event in events if event.get("type") in {"clue", "sanity"})
    assert game.state.investigator.id == other.id and not fake.values


async def test_revised_relationship_and_promise_name_the_actual_actor(stale_game):
    game, cast, _ = stale_game
    pending, lead, other = await revised_other(game, cast, Ruling(needs_roll=False, changes=[
        {"kind": "relationship", "key": "helped", "npc": "warden", "text": "调查员出手稳住架子，守钟人愿意帮忙。"},
        {"kind": "promise", "key": "come-back", "npc": "warden", "text": "调查员答应稍后回来。"},
    ]))
    await game.respond(pending.id, {"text": "扶住架子并约好再来"}, who=lead.id)
    assert game.world.rec.promises["come-back"].by == lead.id
    assert game.world.relation("warden", lead.id, "交情") is not None
    assert game.world.relation("warden", other.id, "交情") is None
    assert game.state.world["helped"].who == lead.id and game.state.world["come-back"].who == lead.id


async def test_scene_result_reaches_actual_copresent_observers_but_not_remote_focus(stale_game):
    game, cast, events = stale_game
    clue = game.m.clue("c_diary")
    clue.handout, clue.sanity, clue.mythos = None, "", 0
    pending, lead, other = await revised_other(
        game, cast, Ruling(needs_roll=False, auto_clues=[clue.id], result_scope="scene"))
    witness = lead.model_copy(update={"id": "witness", "name": "身边队友"}, deep=True)
    game.state.investigators.append(witness)
    game.state.apart[witness.id] = "tower"
    await game.respond(pending.id, {"text": "把日记翻开给身边的人看"}, who=lead.id)
    assert knowledge.clue_known(game.state, clue.id, lead.id)
    assert knowledge.clue_known(game.state, clue.id, witness.id)
    assert not knowledge.clue_known(game.state, clue.id, other.id)
    assert next(event for event in events if event.get("type") == "clue")["audience"] == [lead.id, witness.id]


async def test_rejoining_the_action_owners_scene_preserves_an_unrelated_focused_seat(stale_game):
    game, cast, _ = stale_game
    pending, lead, other = await revised_other(
        game, cast, Ruling(needs_roll=False, rejoin=[{"who": "third", "how": "carried"}]))
    third = lead.model_copy(update={"id": "third", "name": "需要搬运的队友"}, deep=True)
    game.state.investigators.append(third)
    game.state.apart[third.id] = "square"
    await game.respond(pending.id, {"text": "把受伤队友接回来"}, who=lead.id)
    assert game.world.place(third.id) == "tower" and game.world.place(lead.id) == "tower"
    assert game.world.place(other.id) == "office"


async def test_unable_rejoining_actor_at_the_global_default_place_does_not_raise(stale_game):
    game, cast, _ = stale_game
    pending, lead, other = await revised_other(
        game, cast, Ruling(needs_roll=False, rejoin=[{"who": "other", "how": "walked"}]))
    other.add_condition("昏迷")
    turn = await game.respond(pending.id, {"text": "请队友自己回来"}, who=lead.id)
    assert game.world.place(other.id) == "office" and any("没法自己走回来" in note for note in turn.notes)


async def test_saved_discovery_does_not_charge_or_reinterpret_again_after_notice_disconnect(stale_game, fake):
    game, cast, _ = stale_game
    clue = game.m.clue("c_diary")
    clue.handout, clue.sanity, clue.mythos = None, "1/1", 1
    pending, lead, other = await revised_other(game, cast, Ruling(needs_roll=False, auto_clues=[clue.id]))
    game.state.pending_turn = {"input": "回应中"}
    emit = game.emit

    async def cut(event):
        if event.get("type") == "clue":
            raise ConnectionError("线索已结算，投递断开")
        await emit(event)

    game.emit = cut
    fake.queue(20)
    with pytest.raises(ConnectionError):
        await game.respond(pending.id, {"text": "核对日记"}, who=lead.id)
    saved = GameState.model_validate_json(game.state.model_dump_json())
    after = saved.find(lead.id).san, saved.find(lead.id).value("克苏鲁神话")
    calls = len(cast.calls)
    restored = Game(game.m.model_copy(deep=True), saved, cast, Rules(dice=fake), emit=emit)
    cast.world = restored.world
    try:
        for _ in range(2):
            await restored.respond(pending.id, {"text": "核对日记"}, who=lead.id)
        assert (saved.find(lead.id).san, saved.find(lead.id).value("克苏鲁神话")) == after
        assert len(cast.calls) == calls and not fake.values
        assert not knowledge.clue_known(saved, clue.id, other.id)
        assert saved.encounter.completed_rounds == 1
    finally:
        await restored.close()


async def test_chase_turns_to_fight_without_moving_the_unrelated_focused_seat(fake):
    from autokeeper.engine.encounter import chase

    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    game, _cast, events = await chased(module, fake)
    try:
        open_door(game)
        put(game, warden="office_door", zhouheng="office_door")
        lead, other = elsewhere(game, "tower")
        previous_left = dict(game.world.rec.left)
        turn = Turn()
        await game._unit("actual-chase-catch", turn, lambda work: chase.to_fight(work, "office_door"))
        assert game.state.encounter.mode == "fight"
        assert game.world.place(lead.id) == "office" and game.world.place(other.id) == "tower"
        assert game.world.rec.left.get("tower") == previous_left.get("tower")
        assert all(other.id not in event.get("audience", []) for event in events if event.get("type") == "fight_start")
    finally:
        await game.close()


async def test_revised_chase_action_can_switch_to_fighting_while_focus_is_elsewhere(fake):
    from autokeeper.engine.encounter.plan import AttackPick

    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    game, cast, events = await chased(module, fake)
    try:
        open_door(game)
        put(game, warden="office_door", zhouheng="office_door")
        await game._declare(Turn(), game.state.investigator.id, DeclaredAction(
            do="shoot", attacks=[AttackPick(target="warden", with_="missing_weapon")], summary="原先开枪"), "words", None)
        pending = await pending_redeclaration(game)
        lead, other = elsewhere(game, "tower")
        cast.modes["act"] = EncounterTurnPlan(action=DeclaredAction(do="stand", summary="回身迎战"))
        turn = await game.respond(pending.id, {"text": "改为回身迎战"}, who=lead.id)
        assert game.state.encounter.mode == "fight" and game.state.encounter.completed_rounds == 1
        assert game.world.place(lead.id) == "office" and game.world.place(other.id) == "tower"
        assert game.state.investigator.id == other.id and turn.interval["actor"] == lead.id
        assert all(other.id not in event.motions for event in game.world.rec.events)
        assert all(event.get("audience") == [lead.id] for event in events if event.get("type") == "move")
    finally:
        await game.close()


async def test_chase_ending_moves_only_the_actual_escapees_and_replays_idempotently(fake):
    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    game, _cast, events = await chased(module, fake)
    try:
        put(game, zhouheng="fog_square", warden="office_door")
        change(game, lambda enc: setattr(enc.get("zhouheng"), "presence", "escaped"))
        lead, other = elsewhere(game, "tower")
        turn = Turn()
        eid = game.state.encounter.id
        await game._encounter_end("fled", turn)
        assert game.world.place(lead.id) == "square" and game.world.place(other.id) == "tower"
        assert game.state.encounter_log[-1].ended.places[lead.id] == "square"
        assert all(event.get("audience") == [lead.id] for event in events if event.get("type") == "move")
        settled = game.state.model_dump_json()
        await game._encounter_end("fled", turn, eid=eid)
        assert game.state.model_dump_json() == settled
    finally:
        await game.close()
