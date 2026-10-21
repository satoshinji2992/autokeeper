"""A fight cut short goes on from where it was: every die thrown once, nothing landed twice, a fight
that closed is not begun again (invariants I2, I3 and I7 of the encounter engine)."""

from __future__ import annotations

import pytest

from autokeeper.engine.encounter.plan import DeclaredAction, EncounterTurnPlan
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world.store import World

from .encounter_kit import arrange, attack, begin, fight_game

HIT = "攻击 楼梯口打盹的老人（徒手）｜被攻击时闪避"


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


def warden_attacks(state, text, brief):
    return EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng"))])


async def turn_of(game, text):
    action = await game.encounter_input(text)
    return action, await game.rule(action)


async def test_a_turn_cut_short_after_its_fight_closed_only_sends_what_it_owed(module, fake):
    """The user's review, 2026-10-21: the ending notice failed, the retry opened the fight again and
    the warden, knocked out, struck once more."""
    module.npc("warden").stats = {"生命": 2}  # one point is a major wound: knocked out, not dying
    game, _cast, events = fight_game(module, fake, arrange=warden_attacks)
    await begin(game, ["warden"])
    game.state.encounter.participants[0].asked = True
    original = game.emit

    async def broken(ev):
        if ev["type"] == "fight_end":
            raise ConnectionError("the ending notice did not go out")
        await original(ev)

    game.emit = broken
    action, ruling = await turn_of(game, HIT)
    fake.queue(10, 90, 80).queue_totals(1)  # a hard punch, the answer fails: 1 point, a major wound, CON fails
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    enc = game.state.encounter
    assert enc.phase == "ended" and enc.ended.how == "won"
    game.emit = original
    hp = game.state.investigator.hp
    resumed = await game.resolve(action, ruling)  # nothing queued: a die thrown again would fail here
    assert game.state.investigator.hp == hp and not resumed.checks
    kinds = [e["type"] for e in events]
    assert kinds.count("fight_end") == 1 and kinds.count("fight_act") == 1 and "fight_start" in kinds
    assert game.world.rec.entities["warden"].life.hp == 1 and "昏迷" in game.world.rec.entities["warden"].life.conditions
    await game.archive_encounter()
    assert game.state.encounter is None and game.state.encounter_log[-1].ended.how == "won"


class Breaking:
    """Dice that break once, at their `at`-th percentile roll (a crash between two dice)."""

    def __init__(self, dice, at: int):
        self.dice, self.at, self.n = dice, at, 0

    def check(self, target, bonus=0):
        self.n += 1
        if self.n == self.at:
            raise ConnectionError("the process died between two dice")
        return self.dice.check(target, bonus)

    def __getattr__(self, name):
        return getattr(self.dice, name)


async def test_a_crash_between_the_attack_die_and_the_defence_die_throws_each_once(module, fake):
    game, _cast, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))]))
    await begin(game, ["warden"])
    action, ruling = await turn_of(game, HIT)
    fake.queue(10, 90).queue_totals(2)
    game.rules.dice = Breaking(fake, 2)  # the warden's answer: never thrown before the crash
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    assert list(fake.values) == [90]  # the attack die is thrown and kept; nothing landed
    assert game.world.rec.entities["warden"].life.hp == 11
    game.rules.dice = fake
    await game.resolve(action, ruling)
    assert not fake.values and not fake.totals  # the attack die replayed, the answer thrown once
    act = next(e for e in events if e["type"] == "fight_act")
    assert act["roll"]["value"] == 10 and act["answer"]["value"] == 90
    assert game.world.rec.entities["warden"].life.hp == 9


async def test_an_exception_inside_a_unit_applies_nothing_and_its_retry_applies_once(module, fake, monkeypatch):
    game, _cast, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))]))
    await begin(game, ["warden"])
    action, ruling = await turn_of(game, HIT)
    fake.queue(10, 90).queue_totals(2)
    real = World.apply
    broke = []

    def apply(self, op):
        if getattr(op, "id", "").endswith("/a0/roll") and not broke:
            broke.append(op.id)
            raise RuntimeError("the op could not be written")
        return real(self, op)

    monkeypatch.setattr(World, "apply", apply)
    with pytest.raises(RuntimeError):
        await game.resolve(action, ruling)
    assert game.world.rec.entities["warden"].life.hp == 11 and not any(e["type"] == "fight_act" for e in events)
    await game.resolve(action, ruling)
    assert game.world.rec.entities["warden"].life.hp == 9  # once
    assert sum(e["type"] == "fight_act" for e in events) == 1 and not fake.values
    receipts = [r for r in game.state.encounter.receipts if r.actor == "zhouheng"]
    assert [r.phase for r in receipts] == ["committed"] and len(receipts[0].rolls) == 2
