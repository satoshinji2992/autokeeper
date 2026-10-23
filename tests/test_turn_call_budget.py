"""Foreground RP and scene repairs have bounded logical model calls, without paid models."""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from autokeeper.agents.cast import Action, Plan, Ruling
from autokeeper.agents.director import Improv, NewLocation
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.engine.visuals import Visuals
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.rules.commands import Asked
from autokeeper.world import scheduler
from autokeeper.world.ops import AddFact

from .test_engine import NEWSSTAND, FakeCast, Improvising


class Counted(FakeCast):
    def __init__(self, action):
        super().__init__({"聊聊": (action, Ruling(needs_roll=False, minutes=0))})
        self.calls = Counter()

    async def plan(self, state, text, **kwargs):
        self.calls["plan"] += 1
        return Plan(action=self.script[text][0])

    async def arbiter(self, *args, **kwargs):
        self.calls["arbiter"] += 1
        return await super().arbiter(*args, **kwargs)

    async def npc(self, *args, **kwargs):
        self.calls["npc"] += 1
        return await super().npc(*args, **kwargs)

    async def keeper(self, *args, **kwargs):
        self.calls["keeper"] += 1
        return await super().keeper(*args, **kwargs)

    async def chronicle(self, *args, **kwargs):
        self.calls["chronicle"] += 1
        return await super().chronicle(*args, **kwargs)


def game_for(fake, action):
    module = Module.load(EXAMPLE)
    module.npc("warden").important = True
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    cast = Counted(action)
    return Game(module, state, cast, Rules(dice=fake)), cast


@pytest.mark.parametrize("kind", ["daily", "talk"])
async def test_pure_rp_uses_one_keeper_plan_and_one_npc_reply(kind, fake):
    action = Action(kind=kind, summary="聊聊守钟人如何度过雨天", npc="warden",
                    speech="下雨的时候，您一般怎么打发时间？", casual=True)
    game, cast = game_for(fake, action)
    game.world_later = True
    game.minds = scheduler.Budget(turn=2, skip=4)

    class QuietBrain:
        async def decide(self, ask):
            cast.calls["world"] += 1
            pytest.fail("ordinary zero-minute RP must not wake an independent NPC decision")

    game.brain = QuietBrain()
    try:
        parsed = await game.parse("聊聊")
        await game.prepare(parsed)
        turn = await game.resolve(parsed, await game.rule(parsed))
        await game.talk(turn)
        assert await game.narrate(turn) == ""
        await game.remember()
        await game._world_settled()
        assert cast.calls == {"plan": 1, "npc": 1}
        assert turn.npc_lines == [("warden", "warden 的回答")] and not turn.checks
        assert not game.state.clues  # skipping a rule call does not deliver gated evidence
    finally:
        await game.close()


@pytest.mark.parametrize("change", [
    {"casual": False}, {"approach": "说服"}, {"asked": Asked(skill="说服")},
    {"destination": "office"}, {"npc": "preacher"},
])
async def test_casual_rp_does_not_bypass_checks_requests_travel_or_absent_people(fake, change):
    action = Action(kind="talk", summary="和守钟人谈谈", npc="warden", speech="请听我说。", casual=True)
    action = action.model_copy(update=change)
    game, cast = game_for(fake, action)
    try:
        ruled = await game.rule(action)
        if "destination" in change or "npc" in change:
            assert not cast.calls  # an absent addressee is reported before any persuasion roll
            assert ruled.missing and not ruled.needs_roll
            assert not game.state.checks
        else:
            assert cast.calls == {"arbiter": 1}
    finally:
        await game.close()


def director_game(fake, plans):
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.pending_turn = {"input": "查看街角"}
    cast = Improvising({}, plans)
    return Game(module, state, cast, Rules(dice=fake)), cast


class Repairing(Improvising):
    def __init__(self, outputs):
        super().__init__({}, [])
        self.outputs = iter(outputs)

    async def director(self, state, kind, **kwargs):
        self.asked.append((kind, kwargs.get("at", ""), list(kwargs.get("problems") or [])))
        output = next(self.outputs)
        if isinstance(output, BaseException):
            raise output
        return output.model_copy(deep=True) if output else None


def duplicate_location():
    return Improv(kind="展开", reason="重复地点无法落实",
                  locations=[NewLocation(id="square", name="重复的广场", read_aloud="一片空地。", exits=["tower"])])


async def test_rejected_scene_drafts_stop_after_one_repair_instead_of_starting_over(fake):
    invalid = duplicate_location()
    game, cast = director_game(fake, [invalid] * 20)
    before = game.state.minutes
    try:
        assert await game._improvise("展开", required=True) is None
        assert len(cast.asked) == 2 and cast.asked[1][2]
        receipt = game.state.pending_turn["scene_package"]
        assert receipt["given_up"] and not receipt.get("applied") and not game.state.improv
        assert game.state.minutes == before and not game.state.checks
        assert await game._improvise("展开", required=True) is None
        assert len(cast.asked) == 2  # the same interrupted action does not reset the repair allowance
    finally:
        await game.close()


@pytest.mark.parametrize("valid_repair", [False, True])
async def test_a_service_retry_keeps_the_same_repair_and_original_problems(fake, monkeypatch, valid_repair):
    import autokeeper.engine.game as engine

    monkeypatch.setattr(engine, "PREPARE_PAUSE", 0.001)
    game, _ = director_game(fake, [])
    invalid = duplicate_location()
    game.cast = cast = Repairing([invalid, ConnectionError("temporary repair failure"),
                                  NEWSSTAND if valid_repair else invalid, NEWSSTAND])
    try:
        applied = await game._improvise("展开", required=True)
        assert bool(applied) == valid_repair
        assert len(cast.asked) == 3  # failed service send retries the repair, not a new first draft
        assert cast.asked[0][2] == []
        assert cast.asked[1][2] and cast.asked[2][2] == cast.asked[1][2]
        receipt = game.state.pending_turn["scene_package"]
        if valid_repair:
            assert receipt["applied"] and "draft" not in receipt and len(game.state.improv) == 1
        else:
            assert receipt["given_up"] and not receipt.get("applied") and not game.state.improv
    finally:
        await game.close()


async def test_a_cancelled_repair_resumes_its_saved_budget_and_problems_after_reload(fake, monkeypatch):
    import autokeeper.engine.game as engine

    monkeypatch.setattr(engine, "PREPARE_PAUSE", 0.001)
    game, _ = director_game(fake, [])
    game.cast = before = Repairing([duplicate_location(), ConnectionError("temporary repair failure"),
                                    asyncio.CancelledError()])
    checkpoints = []
    game.checkpoint = lambda: checkpoints.append(game.state.model_dump_json())
    resumed = None
    try:
        with pytest.raises(asyncio.CancelledError):
            await game._improvise("展开", required=True)
        assert len(before.asked) == 3 and checkpoints
        saved = GameState.model_validate_json(checkpoints[-1])
        draft = saved.pending_turn["scene_package"]["draft"]
        assert draft["failed"] == 1 and draft["problems"] == before.asked[1][2]
        cast = Repairing([duplicate_location(), NEWSSTAND])
        resumed = Game(game.m.model_copy(deep=True), saved, cast, Rules(dice=fake))
        assert await resumed._improvise("展开", required=True) is None
        assert len(cast.asked) == 1 and cast.asked[0][2] == draft["problems"]
        assert saved.pending_turn["scene_package"]["given_up"] and not saved.improv
    finally:
        if resumed is not None:
            await resumed.close()
        await game.close()


@pytest.mark.parametrize("cached", [False, True])
async def test_an_old_scene_receipt_without_draft_state_still_resumes(fake, cached):
    game, _ = director_game(fake, [])
    receipt = {"kind": "展开", "at": game.state.location}
    if cached:
        receipt["plan"] = NEWSSTAND.model_dump(mode="json")
    game.state.pending_turn["scene_package"] = receipt
    game.cast = cast = Repairing([] if cached else [duplicate_location(), NEWSSTAND])
    try:
        assert await game._improvise("展开", required=True) is not None
        assert len(cast.asked) == (0 if cached else 2) and receipt["applied"]
    finally:
        await game.close()


@pytest.mark.parametrize("old_scope", [{"kind": "开局改编"}, {"at": "tower"}])
async def test_a_different_scene_task_does_not_inherit_the_previous_draft_budget(fake, old_scope):
    game, _ = director_game(fake, [])
    game.state.pending_turn["scene_package"] = {
        "kind": "展开", "at": game.state.location, "draft": {"failed": 2, "problems": ["上个任务的问题"]}, **old_scope,
    }
    game.cast = cast = Repairing([NEWSSTAND])
    try:
        assert await game._improvise("展开", required=True) is not None
        assert len(cast.asked) == 1 and cast.asked[0][2] == []
    finally:
        await game.close()


async def test_a_world_change_rebases_the_package_with_a_new_budget_and_no_saved_stale_plan(fake, monkeypatch):
    game, _ = director_game(fake, [])
    game.cast = cast = Repairing([duplicate_location(), NEWSSTAND, NEWSSTAND])
    snapshots, drawings = [], []
    game.checkpoint = lambda: snapshots.append(game.state.model_dump_json())

    async def draw(visuals, keys):
        drawings.append(keys)
        if len(drawings) == 1:
            assert game.world.apply(AddFact(fact="new_testimony", text="有人刚刚提供了新证词")).ok
        return []

    monkeypatch.setattr(Visuals, "prepare", draw)
    try:
        assert await game._improvise("展开", required=True) is not None
        assert len(cast.asked) == 3 and cast.asked[1][2] and cast.asked[2][2] == []
        receipts = [GameState.model_validate_json(saved).pending_turn["scene_package"] for saved in snapshots]
        assert any(receipt.get("plan") and receipt.get("draft", {}).get("failed") == 1 for receipt in receipts)
        assert any(receipt.get("draft") == {} and "plan" not in receipt and "reads" not in receipt
                   for receipt in receipts)
        assert len(game.state.improv) == 1 and "new_testimony" in game.state.record.facts
    finally:
        await game.close()


async def test_one_repaired_draft_can_complete_and_its_saved_scene_is_reused(fake):
    invalid = NEWSSTAND.model_copy(update={"go": "absent_place"})
    game, cast = director_game(fake, [invalid, NEWSSTAND.model_copy(deep=True)])
    before = game.state.minutes
    try:
        first = await game._improvise("展开", required=True)
        assert first is not None and len(cast.asked) == 2
        assert game.state.pending_turn["scene_package"]["applied"]
        assert await game._improvise("展开", required=True) == first
        assert len(cast.asked) == 2 and len(game.state.improv) == 1 and game.state.minutes == before
    finally:
        await game.close()


async def test_a_temporary_service_failure_does_not_consume_the_draft_repair(fake, monkeypatch):
    import autokeeper.engine.game as engine

    monkeypatch.setattr(engine, "PREPARE_PAUSE", 0.001)
    game, old = director_game(fake, [])

    class Recovers(Improvising):
        async def director(self, state, kind, **kwargs):
            if not self.asked:
                self.asked.append((kind, kwargs.get("at", ""), kwargs.get("problems")))
                raise ConnectionError("temporary service failure")
            return await super().director(state, kind, **kwargs)

    game.cast = cast = Recovers({}, [NEWSSTAND.model_copy(deep=True)])
    try:
        assert await game._improvise("展开", required=True) is not None
        assert len(cast.asked) == 2 and not old.asked and len(game.state.improv) == 1
    finally:
        await game.close()


async def test_a_player_encountering_pending_preparation_waits_and_reuses_that_draft(fake):
    gate, begun = asyncio.Event(), asyncio.Event()
    game, old = director_game(fake, [])
    action = Action(kind="investigate", summary="查看街角")
    rule = Ruling(needs_roll=False)

    class Preparing(Improvising):
        async def director(self, state, kind, **kwargs):
            begun.set()
            await gate.wait()
            return await super().director(state, kind, **kwargs)

    game.cast = cast = Preparing({"查看街角": (action, rule)}, [NEWSSTAND.model_copy(deep=True)])
    pending = None
    before = game.state.minutes
    try:
        assert await game._improvise("展开", required=False, patience=0.0) is None
        await begun.wait()
        pending = asyncio.create_task(game.rule(action))
        await asyncio.sleep(0)
        assert not pending.done() and not game.state.improv and game.state.minutes == before
        gate.set()
        assert await pending is not None
        assert len(cast.asked) == 1 and not old.asked and not game.backstage
        assert len(game.state.improv) == 1 and game.m.npc("paperboy").name == "报童小六"
        assert game.state.minutes == before and not game.state.checks
    finally:
        gate.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await game.close()
