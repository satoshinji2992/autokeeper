"""A changed combat intention is the player's action, never a silent default pass."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.agents.cast import Action, Ruling
from autokeeper.agents.encounter import Checked, ReactionAnswer
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.encounter.model import ChoiceOption, Participant
from autokeeper.engine.encounter.plan import AttackPick, DeclaredAction, EncounterTurnPlan
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .encounter_kit import arrange, begin, fight_game, play


@pytest.fixture
async def stale_game(fake):
    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    game, cast, events = fight_game(module, fake, arrange=EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="不出手"))]))
    await begin(game, ["warden"])
    old = DeclaredAction(do="shoot", attacks=[AttackPick(target="warden", with_="missing_weapon")],
                         summary="原先准备开枪")
    await game._declare(Turn(), game.state.investigator.id, old, "words", None)
    yield game, cast, events
    await game.close()


async def pending_redeclaration(game):
    deliver = game.emit
    app = turn_graph.build(game, InMemorySaver())
    _, offers = await play(app, "/encounter")
    game.emit = deliver
    assert [offer["kind"] for offer in offers] == ["redeclare"]
    return game.state.encounter.pending


async def test_free_text_redeclaration_executes_the_revised_action_in_the_same_round(stale_game, fake):
    game, cast, _ = stale_game
    pending = await pending_redeclaration(game)
    cast.modes["act"] = EncounterTurnPlan(action=DeclaredAction(do="speak", speech="请先停手。", summary="请求停手"))
    await game.respond(pending.id, {"text": "我收起坏枪，改为开口请求先停手。"}, who=pending.who)
    enc = game.state.encounter
    mine = [row for row in enc.receipts if row.actor == pending.who]
    assert [row.declared.do for row in mine] == ["shoot", "speak"]
    assert [row.phase for row in mine] == ["cancelled", "committed"]
    assert mine[-1].source == "words" and mine[-1].round == 1
    assert enc.completed_rounds == 1 and not enc.pending and not fake.values
    assert [mode for mode, _, _ in cast.calls].count("act") == 1


async def test_unreadable_revision_keeps_the_original_choice_without_spending_the_round(stale_game):
    game, cast, _ = stale_game
    pending = await pending_redeclaration(game)
    cast.modes["act"] = None
    before = game.state.model_dump(mode="json")
    with pytest.raises(RuntimeError, match="没能读懂"):
        await game.respond(pending.id, {"text": "我改用别的方法"}, who=pending.who)
    enc = game.state.encounter
    assert enc.pending.id == pending.id and enc.completed_rounds == 0
    assert len(enc.receipts) == len(before["encounter"]["receipts"])
    assert enc.receipts[0].phase == "awaiting_reaction"
    assert game.state.investigator.luck == before["investigators"][0]["luck"]


async def test_a_preset_redeclaration_uses_its_option_without_a_model_call(stale_game):
    game, cast, _ = stale_game
    pending = await pending_redeclaration(game)
    before = len(cast.calls)
    await game.respond(pending.id, pending.default, who=pending.who)
    assert len(cast.calls) == before
    mine = [row for row in game.state.encounter.receipts if row.actor == pending.who]
    assert mine[-1].declared.do == "defend" and mine[-1].source == "button"


@pytest.mark.parametrize("elsewhere", [False, True])
async def test_revision_and_its_rule_read_the_choice_owners_sheet_without_changing_focus(stale_game, elsewhere):
    game, cast, _ = stale_game
    pending = await pending_redeclaration(game)
    lead = game.state.investigator
    other = lead.model_copy(update={"id": "other", "name": "另一位调查员"}, deep=True)
    game.state.investigators.append(other)
    game.state.active = 1
    game.world.rec.entities["other"] = game.world.rec.entities[lead.id].model_copy(update={"id": "other", "name": other.name})
    if elsewhere:
        game.world.rec.entities["other"].at = "office"
        game.state.location, game.state.apart = "office", {lead.id: "tower"}
    else:
        game.state.encounter.participants.append(Participant(id="other", kind="investigator", side="party", controller="human"))
    seen = []

    async def plan(state, text, **kwargs):
        seen.append(("plan", state.investigator.id))
        assert game.state.active == 1
        return Checked(value=EncounterTurnPlan(action=DeclaredAction(do="other", summary="扶住倒下的架子")))

    async def arbiter(state, action, **kwargs):
        seen.append(("arbiter", state.investigator.id))
        assert game.state.active == 1
        return Ruling(needs_roll=False, reason="可以扶稳")

    cast.plan, cast.arbiter = plan, arbiter
    await game.respond(pending.id, {"text": "我改为扶住倒下的架子"}, who=pending.who)
    mine = [row for row in game.state.encounter.receipts if row.actor == lead.id]
    assert seen == [("plan", lead.id), ("arbiter", lead.id)]
    assert mine[-1].declared.do == "other" and mine[-1].ruling["needs_roll"] is False
    assert game.state.active == 1 and game.state.investigators[1].id == "other"
    assert mine[-1].phase == "committed" and game.state.encounter.completed_rounds == 1
    assert game.world.place(lead.id) == "tower" and game.world.place("other") == ("office" if elsewhere else "tower")


async def test_a_saved_revision_reuses_the_interpretation_and_applies_it_only_once(stale_game, fake, monkeypatch):
    game, cast, _ = stale_game
    pending = await pending_redeclaration(game)
    cast.modes["act"] = EncounterTurnPlan(action=DeclaredAction(do="speak", speech="请停手", summary="请停手"))

    async def disconnect(*args, **kwargs):
        raise ConnectionError("解释完成、提交前断开")

    monkeypatch.setattr(game, "_plan_unit", disconnect)
    with pytest.raises(ConnectionError):
        await game.respond(pending.id, {"text": "放下枪，请对方停手"}, who=pending.who)
    state = GameState.model_validate_json(game.state.model_dump_json())
    restored = Game(game.m.model_copy(deep=True), state, cast, Rules(dice=fake))
    cast.world = restored.world
    try:
        await restored.respond(pending.id, {"text": "放下枪，请对方停手"}, who=pending.who)
        await restored.respond(pending.id, {"text": "放下枪，请对方停手"}, who=pending.who)
        mine = [row for row in state.encounter.receipts if row.actor == pending.who]
        assert len(mine) == 2 and mine[-1].declared.do == "speak" and mine[-1].phase == "committed"
        assert state.encounter.completed_rounds == 1
        assert [mode for mode, _, _ in cast.calls].count("act") == 1
    finally:
        await restored.close()


async def test_a_free_text_reaction_is_interpreted_for_its_owner_not_the_attacker(stale_game):
    game, cast, _ = stale_game
    pending = await pending_redeclaration(game)
    lead = game.state.investigator
    other = lead.model_copy(update={"id": "other", "name": "当前攻击者"}, deep=True)
    game.state.investigators.append(other)
    game.world.rec.entities["other"] = game.world.rec.entities[lead.id].model_copy(update={"id": "other"})
    game.state.active = 1
    seen = []

    async def plan(state, text, **kwargs):
        seen.append((state.investigator.id, kwargs["mode"]))
        assert game.state.active == 1
        return Checked(value=ReactionAnswer(reaction="dodge", summary="俯身闪开"))

    cast.plan = plan
    reaction = pending.model_copy(update={"kind": "reaction", "options": [ChoiceOption(id="dodge", label="闪避")],
                                          "default": "none"})
    answer = await game._read_answer(reaction, {"text": "我赶紧俯身避开"})
    assert answer["choice"] == "dodge" and seen == [(lead.id, "react")]
    assert game.state.active == 1


async def test_unreadable_explicit_reaction_is_not_converted_to_the_default(stale_game):
    game, cast, _ = stale_game
    pending = await pending_redeclaration(game)
    cast.modes["react"] = None
    reaction = pending.model_copy(update={"kind": "reaction", "options": [ChoiceOption(id="dodge", label="闪避")],
                                          "default": "dodge"})
    before = game.state.model_dump_json()
    with pytest.raises(RuntimeError, match="原选择已保留"):
        await game._read_answer(reaction, {"text": "我用其他方法应对"})
    assert game.state.model_dump_json() == before


async def test_stale_human_intent_in_a_confirmed_lan_round_is_not_silently_lapsed(stale_game):
    game, _, _ = stale_game
    enc = game.state.encounter
    game.table_turn_based = True
    game.begin_round()
    game.state.round_plan["encounter"] = [enc.id, enc.mode, 1]
    try:
        await game.resolve(Action(kind="other", summary="结算本轮", encounter=enc.id, declared={"beat": True}),
                           Ruling(needs_roll=False))
        current = game.state.encounter
        assert current.pending is not None and current.pending.kind == "redeclare"
        assert current.completed_rounds == 0 and current.receipts[0].phase == "awaiting_reaction"
    finally:
        game.end_round(interrupted=True)
