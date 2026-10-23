"""Remote speech has a real contact, a private receipt, and no physical arrival."""

from __future__ import annotations

import json

import pytest

from autokeeper.agents.cast import Action, Plan, Ruling
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context, scene
from autokeeper.world.ops import Move, Occur

from .test_scene import SCRIPT, SEEN, ctx, made, sandbox  # noqa: F401


def remote_action(npc="clerk"):
    return Action(kind="talk", summary="PRIVATE_REQUEST_52311", npc=npc, communication="remote",
                  speech="请问今天几点关门？", visible="HIDDEN_HAND_MOVEMENT_82174")


def register(game):
    planned = game._planned_scene(Plan(action=remote_action(), people=[scene.Person(
        id="clerk", name="值班员", role="办事员", location="office", knows=["今天下午五点关门"])]))
    assert not planned.problems and scene.land(game.world, planned.held)
    return planned


async def test_a_remote_person_is_registered_at_the_real_existing_location_and_survives_reload(fake):
    game, _ = made("square", fake=fake)
    games = [game]
    try:
        planned = register(game)
        assert game.world.place("clerk") == "office" and "clerk" not in game._present()
        assert not any(step.get("kind") == "move" for u in planned.held for step in u.steps)
        saved = GameState.model_validate_json(game.state.model_dump_json())
        resumed, _ = made(module=Module.load(EXAMPLE), state=saved, fake=fake)
        games.append(resumed)
        assert resumed.m.npc("clerk").location == "office" and resumed.world.place("clerk") == "office"
        assert "clerk" not in resumed._present()
    finally:
        for old in games:
            await old.close()


async def test_an_unknown_person_location_is_rejected_without_inventing_a_place(fake):
    game, _ = made("square", fake=fake)
    try:
        planned = game._planned_scene(Plan(action=remote_action(), people=[scene.Person(
            id="clerk", name="值班员", location="unregistered_building")]))
        assert planned.problems and not planned.held
        assert "clerk" not in game.world.rec.entities and "unregistered_building" not in game.world.rec.entities
    finally:
        await game.close()


async def test_remote_reply_receipt_and_observation_reach_only_the_actual_two_sides(fake):
    module = Module.load(EXAMPLE)
    own = module.pregens[0].model_copy(deep=True)
    other = own.model_copy(update={"id": "bystander", "name": "同室同伴"}, deep=True)
    state = Game.new(module, [own, other])
    state.location = "square"
    game, events = made(module=module, state=state, fake=fake)
    try:
        register(game)
        SCRIPT.append({"text": "今天下午五点关门。"})
        turn = await game.resolve(remote_action(), Ruling(needs_roll=False))
        await game.talk(turn)
        assert not SCRIPT and game.world.place("clerk") == "office"
        assert own.id not in game.world.rec.entities["office"].observed_by
        prompt = json.dumps(SEEN[-1]["messages"], ensure_ascii=False)
        assert "请问今天几点关门" in prompt and "你实际在报社" in prompt
        assert "PRIVATE_REQUEST_52311" not in prompt and "HIDDEN_HAND_MOVEMENT_82174" not in prompt
        assert "看不见另一端房间" in prompt
        said = turn.replies["talk:clerk"]
        assert said.communication == "remote" and said.audience == [own.id]
        assert not any("说完就走" in note for note in turn.notes)
        assert next(e for e in events if e["type"] == "npc")["audience"] == [own.id]
        exchange = next(e for e in game.world.rec.events if e.op.startswith("talk:") and "clerk" in e.participants)
        assert exchange.visibility == "private" and set(exchange.witnesses) == {own.id, "clerk"}
        assert "今天下午五点关门" not in context.npc_brief(game.world, "preacher")
        observation = {"observations": [{"witnesses": ["clerk"], "communication": "remote", "text": "调查员答应五点前来。"}]}
        planned = scene.plan(game.world, observation, game._scene_context(turn))
        assert not planned.problems and scene.land(game.world, planned.held)
        observed = next(e for e in game.world.rec.events if e.text == "调查员答应五点前来。")
        assert set(observed.witnesses) == {own.id, "clerk"}
        assert "调查员答应五点前来" in context.npc_brief(game.world, "clerk")
        assert "调查员答应五点前来" not in context.npc_brief(game.world, "preacher")
    finally:
        await game.close()


async def test_remote_observations_need_an_actual_reply_not_just_a_declared_channel(fake):
    game, _ = made("square", fake=fake)
    try:
        register(game)
        turn = Turn(action=remote_action())
        raw = {"observations": [{"witnesses": ["clerk"], "communication": "remote", "text": "调查员答应明天再来。"}]}
        unspoken = scene.plan(game.world, raw, game._scene_context(turn))
        assert unspoken.problems and not unspoken.held
        local = scene.plan(game.world, {"observations": [{"witnesses": ["clerk"], "text": "看见调查员藏起文件。"}]},
                           game._scene_context(turn))
        assert local.problems and not local.held
    finally:
        await game.close()


async def test_remote_reply_delivery_resumes_after_reload_without_asking_again(fake):
    game, events = made("square", fake=fake)
    games, saved = [game], []
    try:
        register(game)
        SCRIPT.append({"text": "今天下午五点关门。"})
        turn = await game.resolve(remote_action(), Ruling(needs_roll=False))

        def disconnect(acted):
            saved.append((game.state.model_dump_json(), TURN_RECORD.dump_json(acted)))
            raise ConnectionError("reply already saved")

        game.turn_checkpoint = disconnect
        with pytest.raises(ConnectionError, match="reply already saved"):
            await game.talk(turn)
        state = GameState.model_validate_json(saved[0][0])
        resumed, shown = made(module=Module.load(EXAMPLE), state=state, fake=fake)
        games.append(resumed)
        kept = TURN_RECORD.validate_json(saved[0][1])
        await resumed.talk(kept)
        assert len(SEEN) == 1 and not SCRIPT
        assert resumed.world.place("clerk") == "office"
        assert len([e for e in [*events, *shown] if e["type"] == "npc"]) == 1
        assert resumed._scene_context(kept).contacts == ["clerk"]
    finally:
        for old in games:
            await old.close()


async def test_read_only_here_can_remain_at_its_proven_earlier_position_but_cannot_move(fake):
    game, _ = made("square", fake=fake)
    try:
        start = game.state.minutes
        with game.world.at(start + 20):
            assert game.world.apply(Occur(id="later-talk", actor="preacher", participants=["preacher"],
                                          place="square", text="稍后说过的话", visibility="private")).ok
        earlier = scene.Context(travel=game._travel, interval=(start, start), ids="earlier")
        kept = scene.plan(game.world, {"here": ["preacher"]}, earlier)
        assert not kept.problems and not kept.held
        moving = scene.plan(game.world, {"here": [], "left": [{"who": "preacher", "to": "office"}]}, earlier)
        assert moving.problems and not moving.held
        with game.world.at(start + 25):
            assert game.world.apply(Move(id="really-later", entity="warden", to="square")).ok
        not_there = scene.plan(game.world, {"here": ["preacher", "warden"]}, earlier)
        assert not_there.problems and game.world.place_at("warden", start) == "tower"
    finally:
        await game.close()


async def test_a_new_remote_contact_requires_a_real_location_instead_of_defaulting_here(fake):
    game, _ = made("square", fake=fake)
    try:
        planned = game._planned_scene(Plan(action=remote_action(), people=[scene.Person(id="clerk", name="值班员")]))
        assert any("须登记实际所在" in problem for problem in planned.problems)
        assert not planned.held and "clerk" not in game.world.rec.entities
    finally:
        await game.close()


async def test_keeper_can_voice_the_remote_contact_then_commit_only_its_heard_observation(fake):
    game, events = made("square", fake=fake)
    try:
        register(game)
        game.state.pending_turn = {"input": "打电话问值班员"}
        fake.queue(10)
        turn = await game.resolve(remote_action(), Ruling(needs_roll=True, skill="话术"))
        await game.talk(turn)
        assert not turn.replies
        SCRIPT.extend([
            {"kind": "npc", "npc": "clerk", "instruction": "HIDDEN_INSTRUCTION_83015", "private": False, "telling": None},
            {"text": "今天下午五点关门。"},
            {"kind": "tell", "npc": "", "instruction": "", "private": False,
             "telling": {"text": "通话结束。", "scene": {"here": ["preacher"], "observations": [
                 {"witnesses": ["clerk"], "communication": "remote", "text": "调查员表示五点前来。"}]}}},
        ])
        await game.narrate(turn)
        assert not SCRIPT and len(SEEN) == 3
        npc_prompt = json.dumps(SEEN[1]["messages"], ensure_ascii=False)
        assert "HIDDEN_INSTRUCTION_83015" not in npc_prompt and "HIDDEN_HAND_MOVEMENT_82174" not in npc_prompt
        assert game.world.place("clerk") == "office" and turn.replies["keeper:0:clerk"].communication == "remote"
        assert "调查员表示五点前来" in context.npc_brief(game.world, "clerk")
        assert not any("未落实" in note for note in turn.notes)
        assert next(e for e in events if e["type"] == "npc")["audience"] == ["zhouheng"]
    finally:
        await game.close()


@pytest.mark.parametrize("rolled,passed", [(1, True), (95, False)])
async def test_remote_post_roll_voice_receives_the_settled_social_scope_without_private_intent(fake, rolled, passed):
    game, _events = made("square", fake=fake)
    try:
        register(game)
        game.state.pending_turn = {"input": "打电话请求查阅登记"}
        fake.queue(rolled)
        turn = await game.resolve(remote_action(), Ruling(needs_roll=True, skill="说服", why="本次仅请求允许查阅登记"))
        assert turn.primary is not None and turn.checks[turn.primary].passed is passed
        await game.talk(turn)
        assert not turn.replies
        SCRIPT.extend([
            {"kind": "npc", "npc": "clerk", "instruction": "UNSPOKEN_INTENT_48591", "private": False, "telling": None},
            {"text": "你可以查阅登记。" if passed else "这次不能允许你查阅登记。"},
            {"kind": "tell", "telling": {"text": "通话结束。", "scene": {"here": ["preacher"]}}},
        ])
        await game.narrate(turn)
        assert not SCRIPT and len(SEEN) == 3
        npc_prompt = json.dumps(SEEN[1]["messages"], ensure_ascii=False)
        assert "本次仅请求允许查阅登记" in npc_prompt
        assert ("本次交涉成功" if passed else "本次交涉未达成") in npc_prompt
        assert "已结算的演出边界，不是角色听到的话" in npc_prompt
        for hidden in ("UNSPOKEN_INTENT_48591", "PRIVATE_REQUEST_52311", "HIDDEN_HAND_MOVEMENT_82174"):
            assert hidden not in npc_prompt
        assert game.world.place("clerk") == "office"
        assert turn.replies["keeper:0:clerk"].audience == [game.state.investigator.id]
    finally:
        await game.close()
