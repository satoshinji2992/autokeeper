"""The keeper registers the established scene before anybody rules the player's attempt."""

from __future__ import annotations

import pytest

from autokeeper import config
from autokeeper.agents.cast import Action, Cast, Initiative, Plan, Ruling
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Item, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import scene
from autokeeper.world.scene import Person

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401
from .test_keeper_plan import LOOK, PlanCast, make


@pytest.fixture
def module():
    return Module.load(EXAMPLE)


async def test_a_person_missing_from_the_event_record_is_here_before_the_arbiter_reads_it(module, fake):
    module.npc("preacher").location = ""  # no Event.present to fix it for the keeper
    action = Action(kind="talk", summary="周衡问刚从车上下来的布道者", npc="preacher", speech="请问您看到了什么？")
    cast = PlanCast({"问他": (action, Ruling(needs_roll=False))},
                    {"问他": Plan(action=action, present=["preacher"],
                                   initiative=[Initiative(npc="preacher", does="主动递上雨伞")])})
    game = make(module, fake, cast)
    seen = []
    original = cast.arbiter

    async def arbiter(state, attempted, at=None, **kwargs):
        seen.extend(game._present())
        return await original(state, attempted, at=at, **kwargs)

    cast.arbiter = arbiter
    parsed = await game.parse("问他")
    await game.rule(parsed)
    assert "preacher" in seen and module.npc("preacher").location == "tower"
    assert game.world.entity("preacher").at == "tower"
    assert [x.npc for x in game.initiative] == ["preacher"]
    assert any(e.actor == "keeper" and e.id.startswith("plan:") for e in game.world.rec.journal)
    await game.close()


async def test_the_keepers_preparation_does_not_teleport_someone_known_to_be_elsewhere(module, fake):
    cast = PlanCast({}, {"问他": Plan(action=LOOK, present=["preacher"])})
    game = make(module, fake, cast, location="office")
    game.state.pending_turn = {"input": "问他"}
    await game.parse("问他")
    assert game.world.entity("preacher").at == "square"
    assert game.state.pending_turn["arrangement"]["problems"]
    turn = await game.resolve(LOOK, Ruling(needs_roll=False))
    assert any("现场登记未落实" in note for note in turn.notes)
    await game.close()


async def test_a_claim_to_take_an_item_is_still_an_attempt_after_the_keeper_prepares_the_scene(module, fake):
    module.items.append(Item(id="key", name="钥匙", description="一把旧钥匙", where="tower", owner="warden", important=True))
    action = Action(kind="use", summary="周衡试图抢走守钟人的钥匙", target="钥匙")
    # An unrecognised result field is not an outlet to perform the player's attempted transfer.
    plan = Plan.model_validate({"action": action.model_dump(), "present": ["warden"],
                                "holds": [{"item": "key", "holder": module.pregens[0].id}]})
    cast = PlanCast({"抢钥匙": (action, Ruling(needs_roll=True, skill="格斗(斗殴)"))}, {"抢钥匙": plan})
    game = make(module, fake, cast)
    parsed = await game.parse("抢钥匙")
    ruling = await game.rule(parsed)
    assert ruling.needs_roll
    assert game.world.entity("key").at == "warden"
    assert "钥匙" not in game.state.investigator.inventory
    await game.close()


async def test_new_witnesses_can_join_in_this_turn_and_no_third_person_is_silently_dropped(module, fake):
    people = [Person(id=f"witness_{i}", name=f"旅客{i}", goal="替同伴收拾落在地上的行李") for i in range(3)]
    plan = Plan(action=LOOK, people=people,
                initiative=[Initiative(npc=p.id, does="主动扶起地上的行李") for p in people])
    cast = PlanCast({"看看": (LOOK, Ruling(needs_roll=False))}, {"看看": plan})
    game = make(module, fake, cast)
    action = await game.parse("看看")
    turn = await game.resolve(action, await game.rule(action))
    await game.talk(turn)
    assert {n for n, _ in cast.npc_calls} == {p.id for p in people}
    assert all(game.world.entity(p.id).at == "tower" for p in people)
    assert not game.world.rec.agents.keys() & {p.id for p in people}  # no resident NPC agents
    await game.close()


async def test_a_resumed_arrangement_uses_the_saved_plan_and_does_not_register_the_witness_twice(module, fake):
    cast = PlanCast({}, {"看看": Plan(action=LOOK, people=[Person(id="porter", name="搬运工")])})
    game = make(module, fake, cast)
    game.state.pending_turn = {"input": "看看"}
    await game._arrange("看看")
    count = len(game.world.rec.journal)
    cast.plans["看看"] = AssertionError("a completed plan is not asked again")
    await game._arrange("看看")
    assert cast.planned == ["看看"] and len(game.world.rec.journal) == count
    assert len([n for n in game.m.npcs if n.id == "porter"]) == 1
    await game.close()


async def test_only_invalid_registration_needs_a_second_keeper_answer(module, fake):
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    game = make(module, fake, cast, location="office")
    SCRIPT.extend([{"action": LOOK.model_dump(), "present": ["preacher"]},
                   {"action": LOOK.model_dump(), "present": []}])
    action = await game.parse("看看")
    assert action.summary == LOOK.summary and game.world.entity("preacher").at == "square"
    assert game.state.usage["plan"]["calls"] == 2
    assert "现场登记未落实" in SEEN[-1]["messages"][-1]["content"]
    await game.close()
    await cast.close()


async def test_a_question_to_the_keeper_cannot_register_people_in_the_world(module, fake):
    cast = PlanCast({}, {"规则问题": Plan(action=Action(kind="meta", summary="规则问题"),
                                          people=[Person(id="porter", name="搬运工")])})
    game = make(module, fake, cast)
    await game.parse("规则问题")
    assert game.world.entity("porter") is None
    await game.close()


@pytest.mark.parametrize("landed", [False, True], ids=["prepared-checkpoint", "landed-checkpoint"])
async def test_parse_reloaded_between_registration_checkpoints_does_not_repeat_turn_or_player_input(module, fake, landed):
    module.npc("preacher").location = ""
    original = module.model_copy(deep=True)
    plan = Plan(action=LOOK, present=["preacher"], people=[Person(id="porter", name="搬运工")])
    cast = PlanCast({}, {"看看": plan})
    game = make(module, fake, cast)
    game.state.pending_turn = {"input": "看看"}
    saved = []
    game.checkpoint = lambda: saved.append(game.state.model_dump_json())
    await game.parse("看看")
    snapshot = next(raw for raw in saved
                    if bool(GameState.model_validate_json(raw).pending_turn["arrangement"].get("landed")) is landed)

    resumed_cast = PlanCast({}, {"看看": AssertionError("a durable arrangement must not call the keeper again")})
    resumed = Game(original, GameState.model_validate_json(snapshot), resumed_cast, Rules(dice=fake))
    try:
        action = await resumed.parse("看看")
        assert action == LOOK and not resumed_cast.planned
        assert resumed.state.turn == game.state.turn == 1
        player_lines = [entry for entry in resumed.state.log if entry.kind == "player"]
        assert len(player_lines) == 1 and player_lines[0].turn == 1 and player_lines[0].text == "看看"
        assert resumed.world.entity("preacher").at == "tower"
        assert sum(n.id == "porter" for n in resumed.m.npcs) == len(resumed.state.improv) == 1
        operations = [entry for entry in resumed.world.rec.journal if entry.id.startswith("plan:")]
        assert len(operations) == 2 and {entry.turn for entry in operations} == {1}
        assert len({entry.id for entry in operations}) == 2
    finally:
        await resumed.close()
        await game.close()


async def test_an_ordinary_plan_without_registration_does_not_copy_or_save_the_world(module, fake, monkeypatch):
    cast = PlanCast({}, {"看看": Plan(action=LOOK)})
    game = make(module, fake, cast)
    game.state.pending_turn = {"input": "看看"}
    saved = []
    game.checkpoint = lambda: saved.append(game.state.model_dump_json())

    def unnecessary_copy(*args, **kwargs):
        raise AssertionError("an empty registration must not copy the full world through scene.land")

    monkeypatch.setattr(scene, "land", unnecessary_copy)
    try:
        action = await game.parse("看看")
        assert action == LOOK and cast.planned == ["看看"] and not cast.heard
        assert not saved  # graph.parse saves the completed action; registration has no change to save
        assert not game.world.rec.journal
    finally:
        await game.close()
