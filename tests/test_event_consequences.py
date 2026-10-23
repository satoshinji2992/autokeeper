"""Prepared consequences and completed-round triggers, with no model or combat dice."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.encounter.model import (
    ActionReceipt,
    Edge,
    Engagement,
    Hold,
    PendingChoice,
)
from autokeeper.engine.encounter.plan import DeclaredAction, EncounterTurnPlan
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState, format_time
from autokeeper.module.schema import Creature, Event, Module, Stake
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.rules.consequences import Die

from .encounter_kit import Keeper, arrange, fight_game, play


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    m.flags["stopped"] = "袭击已经结束"
    return m


def reinforcement(*, when: str = "", rounds: int | None = None, target: str = "warden") -> Event:
    return Event(id="reinforcement", title="援兵赶到", when=when, where="tower", text="原文明确无需掷骰，袭击者当场死亡。",
                 read_aloud="援兵赶到，袭击者倒地身亡。", effects=[Die(target=target, why="原文明确必死，无需检定")],
                 after_rounds={"event": "ambush", "completed": rounds} if rounds else None, sets=["stopped"])


async def ambush(game) -> None:
    event = Event(id="ambush", title="袭击", when="visit:tower", text="守钟人动手。", where="tower", fight=["warden"],
                  stakes=[Stake(when="dead:warden", flags=["stopped"])])
    await game._fire(event, Turn())


def held_scene(game) -> None:
    enc = game.state.encounter
    enc.scene.holds = [Hold(held="zhouheng", by="warden", since=1)]
    enc.scene.engaged = [Engagement(a="zhouheng", b="warden", since=1)]
    enc.get("zhouheng").edges = [Edge(dice=-1, why="受制", source="warden", until="freed"),
                                 Edge(dice=1, why="协助", source="warden", until="next_roll")]
    enc.receipts = [ActionReceipt(id="interrupted-attack", round=1, actor="warden", source="keeper",
                                 declared=DeclaredAction(do="defend", summary="未完成的行动"), summary="未完成的行动",
                                 phase="awaiting_reaction")]
    enc.pending = PendingChoice(id="answer", kind="reaction", who="zhouheng", receipt="interrupted-attack",
                                prompt="应对", options=[], default="none")


async def test_round_trigger_counts_committed_ends_and_closes_the_original_fight(module, fake):
    game, _, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="防守"))]))
    try:
        await ambush(game)
        game.m.events = [reinforcement(rounds=3)]
        enc = game.state.encounter
        eid, started = enc.id, game.state.minutes
        assert enc.source_event == "ambush" and enc.completed_rounds == 0
        app = turn_graph.build(game, InMemorySaver())
        for n in (1, 2):
            await play(app, "防守")
            assert game.state.encounter.completed_rounds == n
            assert "reinforcement" not in game.state.fired
            assert game.state.minutes == started  # rounds are seconds, not invented minutes
        final, _ = await play(app, "防守")
        assert game.state.encounter is None
        summary = game.state.encounter_log[-1]
        assert summary.id == eid and summary.source_event == "ambush" and summary.completed_rounds == 3
        assert summary.ended.how == "won" and "stopped" in game.state.flags
        assert game.world.rec.entities["warden"].status == "死亡"
        assert [e["type"] for e in [*events, *final]].count("fight_end") == 1
        assert not fake.values and not fake.totals
    finally:
        await game.close()


async def test_death_releases_holds_assistance_and_pending_even_with_other_foes(module, fake):
    game, _, _ = fight_game(module, fake)
    from .encounter_kit import begin

    try:
        await begin(game, ["warden", "preacher"])
        held_scene(game)
        await game._fire(reinforcement(), Turn())
        enc = game.state.encounter
        assert enc is not None and enc.get("preacher") is not None
        assert not enc.scene.holds and not enc.scene.engaged and enc.pending is None
        assert not enc.get("zhouheng").edges
        assert enc.receipt("interrupted-attack").phase == "lapsed"
        assert game.world.rec.entities["warden"].life.conditions == ["死亡"]
    finally:
        await game.close()


def remote_game(module, fake):
    first = module.pregens[0].model_copy(deep=True)
    second = first.model_copy(update={"id": "remote", "name": "异地调查员"}, deep=True)
    state = Game.new(module, [first, second], human=[first.id, second.id])
    state.location, state.apart = "tower", {"remote": "square"}
    notices = []

    async def emit(event):
        notices.append(event)

    game = Game(module, state, Keeper(), Rules(dice=fake), emit=emit)
    return game, notices


async def test_an_event_elsewhere_changes_the_body_and_notifies_only_its_witnesses(module, fake):
    game, notices = remote_game(module, fake)
    try:
        await ambush(game)
        held_scene(game)
        game.m.events = [reinforcement(when=format_time(game.state.minutes))]
        game.state.active = 1
        action = Action(kind="use", summary="异地调查员整理笔记")
        result = await game.resolve(action, Ruling(needs_roll=False, minutes=1))
        assert game.state.encounter is None and game.world.rec.entities["warden"].status == "死亡"
        assert not result.fight and not result.events
        assert "encounter_snapshot" not in result.narrating
        relevant = [n for n in notices if n["type"] in ("fight_end", "flag", "event") and n.get("id") != "ambush"]
        assert {n["type"] for n in relevant} >= {"fight_end", "flag", "event"}
        assert all(n.get("audience") == ["zhouheng"] for n in relevant)
        assert game.world.place("remote") == "square" and game.world.place("zhouheng") == "tower"
    finally:
        await game.close()


async def test_death_and_finished_encounter_resume_after_reload_without_reapplying(module, fake):
    game, notices = remote_game(module, fake)
    games = [game]
    try:
        await ambush(game)
        held_scene(game)
        game.m.events = [reinforcement(when=format_time(game.state.minutes))]
        game.state.active = 1
        game.state.pending_turn = {"input": "整理笔记"}
        action, ruling = Action(kind="use", summary="整理笔记"), Ruling(needs_roll=False, minutes=1)
        emit = game.emit

        async def disconnect(event):
            if event["type"] == "fight_end":
                raise ConnectionError("ending delivery interrupted")
            await emit(event)

        game.emit = disconnect
        with pytest.raises(ConnectionError, match="ending delivery"):
            await game.resolve(action, ruling)
        assert game.state.encounter.phase == "ended" and game.world.rec.entities["warden"].status == "死亡"
        saved = GameState.model_validate_json(game.state.model_dump_json())
        resumed = Game(module, saved, Keeper(), Rules(dice=fake), emit=emit)
        games.append(resumed)
        result = await resumed.resolve(action, ruling)
        assert saved.encounter is None and saved.encounter_log[-1].ended.how == "won"
        assert saved.fired.count("reinforcement") == 1 and saved.flags.count("stopped") == 1
        assert not result.fight and not fake.values and not fake.totals
        assert len([e for e in resumed.world.rec.events if e.kind == "condition" and "死了" in e.text]) == 1
        assert len([n for n in notices if n["type"] == "fight_end"]) == 1
        assert next(n for n in notices if n["type"] == "fight_end")["audience"] == ["zhouheng"]
    finally:
        for old in games:
            await old.close()


async def test_narration_and_a_plot_flag_do_not_infer_a_death(module, fake):
    game, _, _ = fight_game(module, fake)
    try:
        await ambush(game)
        event = reinforcement().model_copy(update={"effects": []})
        await game._fire(event, Turn())
        assert game.world.rec.entities["warden"].status != "死亡" and game.state.encounter is not None
        assert "stopped" in game.state.flags  # a plot marker is not a substitute for a bodily effect
    finally:
        await game.close()


async def test_unwitnessed_consequence_commits_but_never_invents_a_player_witness(module, fake):
    game, _, events = fight_game(module, fake, location="square")
    try:
        await game._fire(reinforcement(), Turn())
        assert game.world.rec.entities["warden"].status == "死亡"
        assert "reinforcement" in game.state.fired and "stopped" in game.state.flags
        assert not [n for n in events if n["type"] in ("event", "fight_end", "flag")]
        assert not [entry for entry in game.state.log if entry.kind == "event"]
    finally:
        await game.close()


async def test_round_gate_uses_finished_summary_and_keeps_other_conditions(module, fake):
    game, _, _ = fight_game(module, fake)
    try:
        await ambush(game)
        game.state.encounter.completed_rounds = 3
        await game._encounter_end("parley", Turn())
        event = reinforcement(rounds=3, when="flag:permission")
        game.m.events = [event]
        await game._events(Turn())
        assert "reinforcement" not in game.state.fired
        game.state.flags.append("permission")
        await game._events(Turn())
        assert "reinforcement" in game.state.fired and game.world.rec.entities["warden"].status == "死亡"
    finally:
        await game.close()


async def test_invalid_prepared_target_cannot_commit_event_or_its_flags(module, fake):
    game, _, _ = fight_game(module, fake)
    try:
        with pytest.raises(RuntimeError, match="不是调查员"):
            await game._fire(reinforcement(target="missing-person"), Turn())
        assert "reinforcement" not in game.state.fired and "stopped" not in game.state.flags
    finally:
        await game.close()


async def test_an_npcs_creature_form_resolves_to_the_same_body_and_hold(module, fake):
    module.creatures.append(Creature(id="masked-form", name="变形的袭击者", npc="warden", description="一道人影", sanity="",
                                     stats={"生命": 20, "敏捷": 30}, attacks=["徒手 30%，伤害 1D3"]))
    game, _, _ = fight_game(module, fake)
    try:
        await ambush(game)
        held_scene(game)
        await game._fire(reinforcement(target="masked-form"), Turn())
        assert game.state.encounter is None
        assert game.world.rec.entities["warden"].status == "死亡"
        assert "masked-form" not in game.world.rec.entities
        assert game.state.encounter_log[-1].ended.how == "won"
    finally:
        await game.close()


@pytest.mark.parametrize("source,completed", [("another-encounter", 5), ("ambush", 2)])
async def test_round_gate_does_not_invent_rounds_for_other_or_early_finished_fights(module, fake, source, completed):
    game, _, _ = fight_game(module, fake)
    try:
        await ambush(game)
        game.state.encounter.source_event = source
        game.state.encounter.completed_rounds = completed
        await game._encounter_end("parley", Turn())
        game.m.events = [reinforcement(rounds=3)]
        await game._events(Turn())
        assert "reinforcement" not in game.state.fired and "stopped" not in game.state.flags
        assert game.world.rec.entities["warden"].status != "死亡"
    finally:
        await game.close()


async def test_a_live_report_after_the_round_crosses_a_minute_keeps_its_actual_hold(module, fake):
    game, cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="保持压制"))]))
    try:
        await ambush(game)
        game.state.encounter.scene.holds = [Hold(held="zhouheng", by="warden", since=0)]
        game.state.encounter.clock.pace = 60
        started = game.state.minutes
        await play(turn_graph.build(game, InMemorySaver()), "防守")
        assert game.state.minutes == started + 1 and game.state.encounter.scene.holds
        report = cast.reports[-1]
        assert f"时刻：{started + 1}" in report and "仍制住周衡" in report and "仍被制住" in report, report
        assert "不能冒充本段快照" not in report
    finally:
        await game.close()


async def test_reload_after_round_end_does_not_count_the_same_round_twice(module, fake):
    game, _, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="防守"))]))
    games, saved = [game], []
    try:
        await ambush(game)
        game.m.events = [reinforcement(rounds=1)]
        game.state.pending_turn = {"input": "防守"}
        action = await game.encounter_input("防守")
        ruling = await game.rule(action)

        def disconnect() -> None:
            steps = game.state.pending_turn.get("resolution", {}).get("steps", {})
            if not saved and any(key.endswith("/r1/end") and row.get("done") for key, row in steps.items()):
                saved.append(game.state.model_dump_json())
                raise ConnectionError("round receipt saved")

        game.checkpoint = disconnect
        with pytest.raises(ConnectionError, match="round receipt"):
            await game.resolve(action, ruling)
        state = GameState.model_validate_json(saved[0])
        assert state.encounter.completed_rounds == 1 and "reinforcement" not in state.fired
        resumed = Game(module, state, Keeper(), Rules(dice=fake), emit=game.emit)
        games.append(resumed)
        await resumed.resolve(action, ruling)
        assert state.encounter is None and state.encounter_log[-1].completed_rounds == 1
        assert state.fired.count("reinforcement") == 1 and state.flags.count("stopped") == 1
        assert len([e for e in events if e["type"] == "fight_end"]) == 1
    finally:
        for old in games:
            await old.close()
