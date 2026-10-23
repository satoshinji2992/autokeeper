"""One skill name and sheet value from a ruling through saved offers and the actual throw."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from autokeeper.agents.cast import Action, PushRuling, Ruling
from autokeeper.engine.game import Game, PreparationError
from autokeeper.engine.graph import build
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.catalog import UnknownSkill, base_value, canonical
from autokeeper.rules.coc import Rules
from autokeeper.rules.creation import accept
from autokeeper.tui.modals.sheet import skill_rows

from .test_engine import FakeCast, make


@pytest.mark.parametrize("raw,expected", [
    (" 巧手 ", "妙手"), ("艺术和手艺（发艺）", "艺术与手艺(发艺)"),
    ("格斗（拳击）", "格斗(斗殴)"), ("dex", "敏捷"), ("SAN", "理智"), ("LUCK", "幸运"),
])
def test_rulings_and_pushes_use_the_same_names_as_sheet_lookups(raw, expected):
    assert canonical(raw) == expected
    assert Ruling(needs_roll=True, skill=raw).skill == expected
    assert PushRuling(achievable=True, skill=raw, minutes=1).skill == expected


def test_aliases_on_loaded_cards_are_the_values_that_rules_roll(inv, rules, fake):
    data = inv.model_dump()
    data["skills"] = {"巧手": 40, "艺术和手艺（发艺）": 50, "格斗（拳击）": 45}
    data["characteristics"] = {canonical(key): value for key, value in inv.characteristics.items()}
    data["characteristics"]["dex"] = data["characteristics"].pop("敏捷")
    card = type(inv).model_validate_json(type(inv).model_validate(data).model_dump_json())
    fake.queue(35, 40, 40, 60)
    assert [(rules.check(card, key).name, card.value(key)) for key in
            ("妙手", "艺术与手艺(发艺)", "斗殴", "DEX")] == [
        ("妙手", 40), ("艺术与手艺(发艺)", 50), ("格斗(斗殴)", 45), ("敏捷", 65),
    ]
    assert not fake.values


def test_unknown_names_do_not_consume_dice_but_real_bases_zero_and_custom_targets_still_work(inv, rules, fake):
    fake.queue(50)
    with pytest.raises(UnknownSkill, match="不能按 1% 代替"):
        rules.check(inv, "手很巧")
    with pytest.raises(UnknownSkill):
        base_value("手很巧", inv.characteristics)
    assert list(fake.values) == [50]
    assert inv.value("人类学") == 1 and inv.value("克苏鲁神话") == 0
    assert inv.value("艺术和手艺(木工)") == 5
    inv.skills["自定义灵媒术"] = 0
    assert rules.check(inv, "自定义灵媒术").target == 0
    fake.queue(25)
    assert rules.check(inv, "临时规则专用检定", target=35).target == 35
    fake.queue(50, 50)
    assert rules.check(inv, "人类学").target == 1
    assert rules.check(inv, "克苏鲁神话").target == 0
    assert not fake.values


def test_an_imported_card_keeps_explicit_zero_skills_in_checks_and_the_sheet(inv):
    card, fixes = accept("zero", name="原卡", characteristics=inv.characteristics,
                         skills={"巧手": 0, "自定义灵媒术": 0}, luck=50)
    assert not fixes
    assert card.value("妙手") == card.value("自定义灵媒术") == 0
    shown = {name: value for name, value, _raised, _marked in skill_rows(card)}
    assert shown["妙手"] == shown["自定义灵媒术"] == 0


@pytest.fixture
def module():
    module = Module.load(EXAMPLE)
    module.events, module.endings, module.agendas, module.foreshadow = [], [], [], []
    module.pregens[0].skills["妙手"] = 40
    return module


ACTION = Action(kind="other", target="前门与椅子", summary="用椅背卡住门把手")
THREAD = {"configurable": {"thread_id": "skill-offer"}}


async def until_offer(app, value):
    events, offers = [], []
    async for mode, chunk in app.astream(value, THREAD, stream_mode=["custom", "updates"]):
        if mode == "custom":
            events.append(chunk)
        elif "__interrupt__" in chunk:
            offers.append(chunk["__interrupt__"][0].value)
    return events, offers


async def test_saved_unrolled_alias_offer_rebuilds_at_forty_and_saved_settlement_never_rolls_twice(module, fake):
    game, _cast, _events = make(module, fake, {})
    original_luck = game.state.investigator.luck
    # Match the real saved shape: only the declaration and ruling, no stored target or rolled result.
    game.state.pending_turn = {"input": ACTION.summary, "by_hand": True, "action": ACTION.model_dump(mode="json"),
                               "ruling": {"needs_roll": True, "skill": "巧手", "difficulty": "常规", "bonus": 0}}
    saved = GameState.model_validate_json(game.state.model_dump_json())
    await game.close()
    for _ in range(2):
        cast = FakeCast({})
        game = Game(module, saved, cast, Rules(dice=fake))
        app = build(game, MemorySaver())
        _events, offers = await until_offer(app, {"input": "/retry", "by_hand": True})
        assert len(offers) == 1
        assert (offers[0]["skill"], offers[0]["target"], offers[0]["hard"], offers[0]["extreme"]) == ("妙手", 40, 20, 8)
        assert not cast.ruled and not game.state.checks and game.state.investigator.luck == original_luck
        saved = GameState.model_validate_json(game.state.model_dump_json())
        if _ == 0:
            await game.close()
    snapshots = []

    def disconnected_after_settlement():
        steps = game.state.pending_turn.get("resolution", {}).get("steps", {})
        if steps.get("check", {}).get("done") and not steps.get("check-notice", {}).get("done"):
            snapshots.append(game.state.model_dump_json())
            raise ConnectionError("check committed before its notice was delivered")

    game.checkpoint = disconnected_after_settlement
    fake.queue(35)
    with pytest.raises(ConnectionError):
        await until_offer(app, Command(resume="roll"))
    assert snapshots and not fake.values
    restored = GameState.model_validate_json(snapshots[-1])
    await game.close()
    game = Game(module, restored, FakeCast({}), Rules(dice=fake))
    try:
        events, offers = await until_offer(build(game, MemorySaver()), {"input": "/retry", "by_hand": True})
        assert not offers and not fake.values
        assert [(check.skill, check.passed) for check in game.state.checks] == [("妙手", True)]
        assert game.state.investigator.luck == original_luck
        assert any(e["type"] == "dice" and e["target"] == 40 and e["value"] == 35 for e in events)
    finally:
        await game.close()


@pytest.mark.parametrize("corrected", ["妙手", "仍未识别的技能"])
async def test_saved_unknown_ruling_uses_the_original_declaration_for_one_durable_correction(module, fake, corrected):
    class Correcting(FakeCast):
        def __init__(self):
            super().__init__({})
            self.corrections = []

        async def arbiter(self, state, action, at=None, *, problems=None):
            self.corrections.append((action.model_dump(mode="json"), problems))
            return Ruling(needs_roll=True, skill=corrected)

    game, _cast, _events = make(module, fake, {})
    cast = Correcting()
    game.cast = cast
    game.state.pending_turn = {"input": ACTION.summary, "by_hand": True, "action": ACTION.model_dump(mode="json"),
                               "ruling": {"needs_roll": True, "skill": "手很巧"}}
    try:
        if corrected == "妙手":
            _events, offers = await until_offer(build(game), {"input": "/retry", "by_hand": True})
            assert offers[0]["target"] == 40
        else:
            for _ in range(2):
                with pytest.raises(PreparationError, match="原行动已保留，尚未掷骰"):
                    await until_offer(build(game), {"input": "/retry", "by_hand": True})
        assert len(cast.corrections) == 1
        assert cast.corrections[0][0] == ACTION.model_dump(mode="json")
        assert "未识别" in cast.corrections[0][1][0]
        assert game.state.pending_turn["input"] == ACTION.summary
        assert not fake.values and not game.state.checks
    finally:
        await game.close()
