"""A deliberately false promise permits play without forging presence or revealing intent."""

import json

from autokeeper import config, paths
from autokeeper.agents.cast import Action, Cast, Ruling
from autokeeper.engine.game import TURN_RECORD, Game
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules

from .test_harness import SCRIPT, SCRIPTED
from .test_harness import harness_sandbox as harness_sandbox


async def test_prepare_accepts_a_false_public_agreement_without_forcing_follow_or_revealing_it(fake, harness_sandbox):
    module = Module.load(paths.EXAMPLE)
    module.showcases = []
    module.events = []
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    state.turn = 1
    cfg = config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}, visual_limit=0)
    cast = Cast(cfg, module)
    events = []

    async def emit(event):
        events.append(event)

    game = Game(module, state, cast, Rules(dice=fake), emit=emit)
    claim = "好，我陪你去广场。"
    intent = "暂时留在钟楼，不随调查员离开。"
    motive = "担心无人看守，先敷衍来访者以免争执。"
    SCRIPT.append({"text": claim, "accompany": "", "deceptions": [
        {"claim": claim, "intent": intent, "motive": motive, "accompany": "square"},
    ]})
    action = Action(kind="move", summary="和守钟人去广场", npc="warden", companions=["warden"], destination="square")
    before = state.minutes
    try:
        await game.prepare(action)
        preparation = state.pending_turn["preparation"]
        said = TURN_RECORD.validate_python(preparation["turn"]).npc_lines  # the preparation's own turn
        assert not preparation["blocked"] and said == [("warden", claim)]
        assert state.minutes == before and state.location == "tower"
        assert not game.world.entity("warden").following and game.world.place("warden") == "tower"
        assert cast.claimed_accompany(state, "warden", "square")
        assert not any(event.kind == "move" and event.actor == "warden" for event in state.record.events)
        # A resumed preparation keeps the first statement; it does not manufacture
        # consent through another model call or consume the deception a second time.
        lines = state.npc("warden").lines
        await game.prepare(action)
        assert state.npc("warden").lines == lines

        turn = await game.resolve(action, Ruling(needs_roll=False, minutes=0))
        assert state.location == "square" and game.world.place("warden") == "tower"
        assert not any("老守钟人与调查员一同抵达" in note for note in turn.notes)
        public = json.dumps(events, ensure_ascii=False) + game._report(turn)
        assert claim in public
        assert intent not in public and motive not in public and "deceptions" not in public
        assert "尚未同意" not in public and "故意骗" not in public
    finally:
        await game.close()
        await cast.close()


async def test_partial_travel_consent_keeps_its_destination_and_waits_for_the_player(fake, harness_sandbox):
    module = Module.load(paths.EXAMPLE)
    module.showcases = []
    module.events = []
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    state.turn = 1
    state.heard.update({"square": "广场", "office": "报社"})
    cfg = config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}, visual_limit=0)
    cast = Cast(cfg, module)
    events = []

    async def emit(event):
        events.append(event)

    game = Game(module, state, cast, Rules(dice=fake), emit=emit)
    claim = "我陪你走到广场，报社那一段我不去。"
    SCRIPT.append({"text": claim, "accompany": "square"})
    action = Action(kind="move", summary="请守钟人与我一起去报社", npc="warden",
                    companions=["warden"], destination="office")
    before = state.minutes
    try:
        await game.prepare(action)
        preparation = state.pending_turn["preparation"]
        prepared_turn = TURN_RECORD.validate_python(preparation["turn"])
        assert preparation["blocked"] and prepared_turn.npc_lines == [("warden", claim)]
        warden = game.world.entity("warden")
        assert (warden.following, warden.follow_to) == (state.investigator.id, "square")
        assert state.location == game.world.place("warden") == "tower" and state.minutes == before
        assert not any(event.kind == "move" for event in state.record.events)

        ruling = await game.rule(action)
        assert ruling is not None and not ruling.needs_roll
        turn = await game.resolve(action, ruling)
        report = game._report(turn)
        assert "只同意前往广场" in report and "还没同意继续到报社" in report
        assert "仅完成出发前交谈" in report and "行程与目的地的后续行动没有执行" in report
        assert "尚未同意同行" not in report
        assert state.location == game.world.place("warden") == "tower" and state.minutes == before
        assert (warden.following, warden.follow_to) == (state.investigator.id, "square")
        assert not turn.arrived and not any(event["type"] == "move" for event in events)
    finally:
        await game.close()
        await cast.close()
