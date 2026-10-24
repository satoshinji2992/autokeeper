"""Keeper-initiated fights/chases use ordinary rules, audiences and recovery receipts."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from autokeeper.agents.cast import KeeperToolCall
from autokeeper.engine.encounter.plan import EncounterStart
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Creature, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import ops as O

from .encounter_kit import arrange, attack, begin, fight_game
from .test_encounter_recovery import Breaking


@pytest.fixture
def module() -> Module:
    result = Module.load(EXAMPLE)
    result.events, result.endings = [], []
    return result


def start(*, mode="fight", foe="warden") -> dict:
    return EncounterStart(mode=mode, foes=[foe], quarry="party" if mode == "chase" else "", pace=6,
                          reason="对方突然开始追捕" if mode == "chase" else "守钟人突然挥拳",
                          npcs=[arrange(foe, attack("zhouheng"))]).model_dump(mode="json", by_alias=True)


def saved_call(game, raw):
    row = {"tool": "encounter", "encounter": raw, "encounter_journal": {}}
    game.state.pending_turn = {"id": "kp-encounter", "keeper": {"calls": [row]}}
    return row


def keeping(game):
    def keep(turn):
        game.state.pending_turn["keeper"]["voice_turn"] = TURN_RECORD.dump_python(turn, mode="json")
        if game.checkpoint:
            game.checkpoint()

    return keep


@pytest.mark.parametrize("payload", [{"tool": "encounter_schema"}, {"tool": "encounter", "encounter": start()}])
def test_encounter_tool_asks_for_its_full_schema_only_on_demand(payload):
    assert KeeperToolCall.model_validate(payload).model_dump(exclude_defaults=True) == payload
    assert "EncounterStart" not in json.dumps(KeeperToolCall.model_json_schema())


@pytest.mark.parametrize("payload", [
    {"tool": "encounter"}, {"tool": "encounter", "encounter": {}},
    {"tool": "encounter_schema", "encounter": start()},
    {"tool": "world_query", "encounter": start()},
    {"tool": "encounter", "encounter": start(), "query": "替代参数"},
])
def test_encounter_tool_cannot_mix_arguments_or_host_identity(payload):
    with pytest.raises(ValidationError):
        KeeperToolCall.model_validate(payload)


async def test_a_hostile_npc_can_open_a_fight_without_spending_the_players_action(module, fake):
    game, _, notices = fight_game(module, fake)
    raw = start()
    row, turn = saved_call(game, raw), Turn()
    hp = game.state.investigator.hp
    try:
        result = await game._keeper_encounter(raw, turn, row["encounter_journal"], keeping(game))
        enc = game.state.encounter
        assert result["ok"] and result["status"] == "started" and enc.mode == "fight" and enc.round == 0
        assert enc.get("warden").arrangement.actions[0].attacks[0].target == "zhouheng"
        assert not enc.receipts and game.state.investigator.hp == hp and not turn.checks
        assert [notice["type"] for notice in notices] == ["fight_start"]
        assert not fake.values and not fake.totals
    finally:
        await game.close()


@pytest.mark.parametrize("problem", ["npc-away", "npc-dead", "player-away", "player-down", "future", "opening", "unknown"])
async def test_impossible_encounter_requests_are_returned_to_keeper_without_mutating_rules(module, fake, problem):
    game, _, notices = fight_game(module, fake)
    raw = start()
    if problem == "npc-away":
        assert game.world.apply(O.Move(entity="warden", to="office", actor="engine")).ok
    elif problem == "npc-dead":
        module.npc("warden").status = "死亡"
    elif problem == "player-away":
        game.state.positions["zhouheng"] = "office"
    elif problem == "player-down":
        game.state.investigator.conditions.append("昏迷")
    elif problem == "future":
        with game.world.at(game.world.now + 10):
            assert game.world.apply(O.Occur(actor="engine", place="tower", participants=["warden"],
                                            text="更晚的已发生行动")).ok
    elif problem == "opening":
        raw["opening"] = attack("warden").model_dump(mode="json", by_alias=True)
    else:
        raw["hp"] = -100
    row = saved_call(game, raw)
    before = game.world.rec.seq
    try:
        result = await game._keeper_encounter(raw, Turn(), row["encounter_journal"], keeping(game))
        assert not result["ok"] and result["status"] == "rejected" and result["problems"]
        assert game.state.encounter is None and game.world.rec.seq == before and not notices
    finally:
        await game.close()


@pytest.mark.parametrize("phase", ["active", "ended"])
async def test_a_new_keeper_request_cannot_reset_an_unarchived_fight_from_this_turn(module, fake, phase):
    game, _, notices = fight_game(module, fake)
    await begin(game, ["warden"])
    game.state.encounter.round = 3
    if phase == "ended":
        game.state.encounter.phase = "ended"
    game.world.rec.entities["warden"].life.hp -= 2
    before = game.state.encounter.model_dump(), game.world.rec.seq, len(notices)
    row = saved_call(game, start())
    try:
        result = await game._keeper_encounter(row["encounter"], Turn(), row["encounter_journal"], keeping(game))
        assert not result["ok"]
        assert (game.state.encounter.model_dump(), game.world.rec.seq, len(notices)) == before
    finally:
        await game.close()


async def test_an_npc_can_initiate_a_turn_based_pursuit_on_the_modules_existing_route(module, fake):
    game, _, notices = fight_game(module, fake, location="office")
    assert game.world.apply(O.Move(entity="warden", to="office", actor="engine")).ok
    row = saved_call(game, start(mode="chase"))
    fake.queue(40, 40)
    try:
        result = await game._keeper_encounter(row["encounter"], Turn(), row["encounter_journal"], keeping(game))
        enc = game.state.encounter
        assert result["ok"] and enc.mode == "chase" and enc.round == 0
        assert enc.chase.site.source == "module" and enc.chase.quarry == ["zhouheng"]
        assert enc.chase.pursuers == ["warden"] and not enc.receipts
        assert {person.id: person.speed for person in enc.participants} == {"zhouheng": 8, "warden": 8}
        assert sum(notice["type"] == "chase_start" for notice in notices) == 1 and not fake.values
    finally:
        await game.close()


@pytest.mark.parametrize("interrupted", ["notice", "sanity", "speed", "chase-ended"])
async def test_loading_a_partly_opened_encounter_reuses_its_rules_and_first_dice(module, fake, interrupted):
    if interrupted == "sanity":
        module.creatures.append(Creature(id="ghoul", name="食尸鬼", description="犬脸人影", sanity="0/1D3",
                                         stats={"生命": 13, "敏捷": 40}, attacks=["爪击 40%，伤害 1D6"]))
    game, cast, notices = fight_game(module, fake, location="office" if interrupted in {"speed", "chase-ended"} else "tower")
    foe = "ghoul" if interrupted == "sanity" else "warden"
    if interrupted not in {"notice", "sanity"}:
        assert game.world.apply(O.Move(entity=foe, to=game.state.location, actor="engine")).ok
    row = saved_call(game, start(mode="chase" if interrupted in {"speed", "chase-ended"} else "fight", foe=foe))
    original_emit = game.emit
    failure_notice = {"notice": "fight_start", "sanity": "sanity", "chase-ended": "chase_end"}.get(interrupted)

    async def broken(notice):
        if notice["type"] == failure_notice:
            raise ConnectionError("opening interrupted")
        await original_emit(notice)

    game.emit = broken
    if interrupted == "sanity":
        fake.queue(99).queue_totals(3)
    elif interrupted == "speed":
        fake.queue(40, 40)
        game.rules.dice = Breaking(fake, 2)
    elif interrupted == "chase-ended":
        fake.queue(1, 99)
    turn = Turn()
    restored = None
    try:
        with pytest.raises(ConnectionError):
            await game._keeper_encounter(row["encounter"], turn, row["encounter_journal"], keeping(game))
        state = GameState.model_validate_json(game.state.model_dump_json())
        restored = Game(module.model_copy(deep=True), state, cast, Rules(dice=fake), emit=original_emit)
        saved = restored.state.pending_turn["keeper"]
        loaded = TURN_RECORD.validate_python(saved["voice_turn"])
        resumed = saved["calls"][0]
        result = await restored._keeper_encounter(resumed["encounter"], loaded, resumed["encounter_journal"], keeping(restored))
        assert result["ok"] and not fake.values and not fake.totals
        if interrupted == "sanity":
            assert restored.state.investigator.san == game.state.investigator.san
            assert len(loaded.sanity) == 1 and restored.state.sightings["zhouheng"] == ["ghoul"]
        if interrupted == "chase-ended":
            assert result["status"] == "ended" and restored.state.encounter is None
            assert len(restored.state.encounter_log) == 1
        else:
            assert restored.state.encounter.id == result["encounter"]
        before = restored.world.rec.seq, len(notices)
        assert await restored._keeper_encounter(resumed["encounter"], loaded, resumed["encounter_journal"], keeping(restored)) == result
        assert (restored.world.rec.seq, len(notices)) == before
        assert sum(notice["type"] == ("chase_start" if interrupted in {"speed", "chase-ended"} else "fight_start")
                   for notice in notices) == 1
    finally:
        await game.close()
        if restored is not None:
            await restored.close()


async def test_an_encounter_only_reaches_observers_in_its_own_scene(module, fake):
    game, _, notices = fight_game(module, fake)
    remote = game.state.investigator.model_copy(update={"id": "chen", "name": "陈明"}, deep=True)
    game.state.investigators.append(remote)
    game.state.positions[remote.id] = "square"
    row = saved_call(game, start())
    try:
        result = await game._keeper_encounter(row["encounter"], Turn(), row["encounter_journal"], keeping(game))
        assert result["ok"] and [person.id for person in game.state.encounter.participants] == ["zhouheng", "warden"]
        assert all(notice["audience"] == ["zhouheng"] for notice in notices)
        assert game.state.log[-1].audience == ["zhouheng"]
        assert "老守钟人" not in json.dumps(notices, ensure_ascii=False)
        assert notices[0]["fight"]["fighters"][1]["id"] == "f1"
    finally:
        await game.close()


async def test_a_saved_request_rechecks_presence_until_the_encounter_actually_begins(module, fake):
    game, _, notices = fight_game(module, fake)
    row = saved_call(game, start())

    def interrupted():
        if row["encounter_journal"].get("steps", {}).get("prepare", {}).get("done"):
            raise ConnectionError("accepted request, no occurrence yet")

    game.checkpoint = interrupted
    try:
        with pytest.raises(ConnectionError):
            await game._keeper_encounter(row["encounter"], Turn(), row["encounter_journal"], keeping(game))
        assert game.state.encounter is None
        game.checkpoint = None
        assert game.world.apply(O.Move(entity="warden", to="office", actor="engine")).ok
        result = await game._keeper_encounter(row["encounter"], Turn(), row["encounter_journal"], keeping(game))
        assert not result["ok"] and any("不在现场" in problem for problem in result["problems"])
        assert game.state.encounter is None and game.world.place("warden") == "office" and not notices
    finally:
        await game.close()


async def test_a_creature_form_keeps_the_npcs_identity_and_first_round_intention(module, fake):
    module.creatures.append(Creature(id="warden_form", npc="warden", name="钟楼怪影", description="扭曲的老人",
                                     sanity="0/0", stats={"生命": 13}, attacks=["爪击 40%，伤害 1D6"]))
    game, _, _ = fight_game(module, fake)
    row = saved_call(game, start(foe="warden_form"))
    fake.queue(1)
    try:
        result = await game._keeper_encounter(row["encounter"], Turn(), row["encounter_journal"], keeping(game))
        assert result["ok"]
        assert [person.id for person in game.state.encounter.participants] == ["zhouheng", "warden"]
        assert game.state.encounter.get("warden").arrangement.who == "warden"
        assert not game.state.encounter.receipts
    finally:
        await game.close()
