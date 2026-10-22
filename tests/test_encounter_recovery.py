"""A fight cut short goes on from where it was: every die thrown once, nothing landed twice, a fight
that closed is not begun again (invariants I2, I3 and I7 of the encounter engine)."""

from __future__ import annotations

import pytest

from autokeeper.engine.encounter.plan import DeclaredAction, EncounterTurnPlan
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world.store import World

from .encounter_kit import arrange, attack, begin, fight_game

HIT = "攻击 楼梯口打盹的老人（徒手，1D3）"


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


def warden_attacks(state, text, brief):
    return EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng"))])


async def turn_of(game, text):
    """A turn begun with the player's words as the turn graph begins one (its journal, pending_turn,
    is what a retry goes on from), read and ruled."""
    game.state.pending_turn = {"input": text}
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


async def test_a_fight_its_end_sets_off_begins_cleanly_and_a_retry_goes_on_from_it(module, fake):
    """The review's blocker: the ended fight was still the encounter going on when its stake's flag
    began the next one; that Begin was refused and every retry failed the same way."""
    from autokeeper.module.schema import Creature, Event, Stake

    module.npc("warden").stats = {"生命": 2}
    module.flags["bell_stopped"] = "钟停了"
    module.creatures.append(Creature(id="hound", name="猎犬", description="影子", sanity="0/0",
                                     stats={"生命": 8, "敏捷": 40}, attacks=["咬 30%，伤害 1D4"]))
    module.events.append(Event(id="ev2", title="又来了", when="flag:bell_stopped", text="猎犬扑来", fight=["hound"]))
    game, _cast, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="守"))]))
    await begin(game, ["warden"], npcs=[arrange("warden", DeclaredAction(do="defend", summary="守"))],
                stakes=[Stake(when="ended:won", flags=["bell_stopped"])])
    original = game.emit
    broke = []

    async def broken(ev):
        if ev["type"] == "fight_start" and not broke:
            broke.append(ev)
            raise ConnectionError("the new fight's notice did not go out")
        await original(ev)

    game.emit = broken
    game.state.turn += 1
    action, ruling = await turn_of(game, HIT)
    fake.queue(1, 99, 10)  # a critical punch kills him (more than his HP), his answer fails; the hound's sight holds
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    first = game.state.encounter_log[-1]
    assert first.ended.how == "won" and game.state.encounter.id != first.id  # the old one in the log, the new one begun
    game.emit = original
    await game.resolve(action, ruling)
    enc = game.state.encounter
    assert [p.id for p in enc.participants] == ["zhouheng", "hound"] and enc.round == 0 and "bell_stopped" in game.state.flags
    kinds = [e["type"] for e in events]
    assert kinds.count("fight_end") == 1 and kinds.count("fight_start") == 2 and len(game.state.encounter_log) == 1  # the first one's and the hound's, once


async def test_a_stake_that_fired_mid_fight_sets_its_flags_on_the_retry(module, fake):
    """The pre-release review: a stake fired at a round's end landed, the notice of its flag broke, and
    the retry found nothing due any more: the flag's event never came. Its flags are read from the
    unit's journal row, as an end's are."""
    from autokeeper.module.schema import Event, Stake

    module.flags["pre"] = "前提"
    module.flags["post"] = "后果"
    module.events.append(Event(id="ev_post", title="钟声", when="flag:post", text="钟声响了"))
    keep = arrange("warden", DeclaredAction(do="defend", summary="守"))
    game, _cast, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[keep]))
    game.state.flags.append("pre")
    await begin(game, ["warden"], npcs=[keep], stakes=[Stake(when="flag:pre", flags=["post"])])
    original, broke = game.emit, []

    async def broken(ev):
        if ev["type"] == "flag" and not broke:
            broke.append(ev)
            raise ConnectionError("the flag's notice did not go out")
        await original(ev)

    game.emit = broken
    game.state.turn += 1
    action, ruling = await turn_of(game, HIT)
    fake.queue(90, 90)  # the punch misses, the dodge fails: nothing else happens this round
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    assert game.state.encounter.stakes[0].fired and "ev_post" not in game.state.fired
    game.emit = original
    await game.resolve(action, ruling)
    assert "post" in game.state.flags and game.state.fired.count("ev_post") == 1 and not fake.values
    assert [e["type"] for e in events].count("event") == 1


# -- the independent review's probes, turned into regression tests ---------------------------------------
async def test_a_failed_die_of_a_first_sights_loss_is_rolled_again_and_the_loss_kept(module, fake):
    from autokeeper.agents.cast import Action, Ruling
    from autokeeper.engine.encounter.plan import EncounterStart
    from autokeeper.module.schema import Creature

    module.creatures.append(Creature(id="ghoul", name="食尸鬼", description="犬脸", sanity="0/1D6",
                                     stats={"生命": 13, "敏捷": 40}, attacks=["爪击 40%，伤害 1D6"]))
    game, _, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("ghoul", DeclaredAction(do="defend", summary="守"))]))
    start = EncounterStart(mode="fight", foes=["ghoul"], pace=6, reason="x")
    action = Action(kind="other", summary="开打", declared={"start": start.model_dump(mode="json", by_alias=True)})
    ruling = Ruling(needs_roll=False, reason="x")
    san = game.state.investigator.san
    game.state.pending_turn = {"input": "开打"}
    fake.queue(99)  # the SAN roll fails; the 1D6 loss has no die yet: the dice break there
    with pytest.raises(IndexError):
        await game.resolve(action, ruling)
    assert game.state.sightings.get("zhouheng") in (None, [])  # neither the sighting nor the loss
    fake.queue_totals(4)
    await game.resolve(action, ruling)
    assert game.state.investigator.san == san - 4 and game.state.sightings == {"zhouheng": ["ghoul"]}


async def test_first_aid_stabilizes_the_dying_with_one_hit_point(module, fake):
    from autokeeper.engine.encounter.actions import Ctx
    from autokeeper.engine.encounter.model import ActionReceipt
    from autokeeper.engine.encounter.resolve import Work, _aid
    from autokeeper.world.ops import EncounterStep
    from autokeeper.world.record import ENGINE

    game, _, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan())
    await begin(game, ["warden"])
    inv = game.state.investigator
    inv.hp, inv.conditions = 0, ["重伤", "濒死", "昏迷"]
    ctx = Ctx.of(game)
    w = Work(game, ctx, "aid")
    r = ActionReceipt(id="x", round=1, actor="warden", source="keeper", summary="-",
                      declared=DeclaredAction(do="aid", target=inv.id, summary="-"))
    w.enc.receipts.append(r)
    fake.queue(5)
    _aid(w, r, "warden", inv.id)
    assert game.world.apply(EncounterStep(id="aid", encounter=ctx.enc.id, unit="aid", effects=w.unit.effects, actor=ENGINE)).ok
    assert inv.hp == 1 and "濒死" in inv.conditions and inv.injury.stabilized_at is not None  # p. 101: 1 temporary HP


async def test_a_replan_whose_call_failed_is_asked_again_on_the_retry(module, fake):
    calls = {"n": 0}

    def arrange_mode(state, text, brief):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ConnectionError("model timeout during replan")
        if calls["n"] in (1, 2):
            return EncounterTurnPlan()  # the input's and the round's arrangement: nothing for the warden
        return EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng"))])

    game, _cast, _events = fight_game(module, fake, arrange=arrange_mode)
    await begin(game, ["warden"])
    game.state.encounter.participants[0].asked = True
    game.state.turn += 1
    action, ruling = await turn_of(game, HIT)
    fake.queue(10, 90).queue_totals(2)
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    fake.queue(10, 50).queue_totals(1)
    await game.resolve(action, ruling)
    assert calls["n"] == 4  # asked again, once
    warden = [r for r in game.state.encounter.receipts if r.actor == "warden"]
    assert [r.phase for r in warden] == ["committed"]


async def test_an_opening_blow_that_killed_its_foe_still_ends_the_fight_on_a_retry(module, fake):
    from autokeeper.agents.cast import Action, Ruling
    from autokeeper.engine.encounter.plan import EncounterStart
    from autokeeper.module.schema import Stake

    module.npc("warden").stats = {"生命": 2}
    module.flags["warden_down"] = "守钟人倒下"
    game, _, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="守"))]))
    start = EncounterStart(mode="fight", foes=["warden"], pace=6, reason="x", opening=attack("warden", summary="周衡扑向守钟人"),
                           npcs=[arrange("warden", DeclaredAction(do="defend", summary="守"))],
                           stakes=[Stake(when="dead:warden", flags=["warden_down"])])
    action = Action(kind="attack", summary="开打", declared={"start": start.model_dump(mode="json", by_alias=True)})
    ruling = Ruling(needs_roll=False, reason="x")
    original = game.emit

    async def broken(ev):
        if ev["type"] == "fight_end":
            raise ConnectionError("x")
        await original(ev)

    game.emit = broken
    game.state.pending_turn = {"input": "开打"}
    fake.queue(1, 99)  # a critical punch kills him; his answer fails
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    game.emit = original
    await game.resolve(action, ruling)
    kinds = [e["type"] for e in events]
    assert kinds.count("fight_end") == 1 and "warden_down" in game.state.flags and game.state.encounter is None


async def test_with_no_human_at_the_table_the_ai_players_declarations_settle_the_rounds(module, fake):
    game, _cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="守"))]))
    game.state.human = []  # autoplay, watching: Game.new(..., human=[])
    await begin(game, ["warden"])
    assert [p.controller for p in game.state.encounter.participants] == ["ai", "keeper"]
    for n in (1, 2):
        game.state.turn += 1
        action, ruling = await turn_of(game, HIT)
        fake.queue(90, 90)
        await game.resolve(action, ruling)
        assert game.state.encounter.round == n and not fake.values


async def test_a_mishap_cards_hp_lands_through_effects_once_with_its_injury_id(module, fake, monkeypatch):
    from autokeeper import cards
    from autokeeper.engine.game import Turn

    card = cards.MishapCard(id="falling_beam", deck="mishap", name="落梁", text="房梁砸了下来。", hp="1D3")
    monkeypatch.setattr(cards, "find", lambda cid: card)
    game, _, events = fight_game(module, fake)
    inv = game.state.investigator
    fake.queue_totals(2)
    assert await game._mishap("falling_beam", Turn(), "rest-mishap") == "落梁"
    assert inv.hp == inv.hp_max - 2 and any(i.startswith("mishap:") for i in inv.injury.injuries)
    assert [e["type"] for e in events][:2] == ["card", "damage"]
    assert any(op.kind == "effects" for op in game.world.rec.journal)  # one effects op, refused twice by its injury id
