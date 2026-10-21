"""Fights on the encounter engine (engine/encounter), through the turn graph, with scripted dice and a
scripted keeper plan: what the reviews of 2026-10-21 found wrong, asserted the right way round."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.agents.cast import Action, RuledEffect, Ruling
from autokeeper.agents.encounter import EncounterHint, Pursuit, PursuitAnswer
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.encounter import choices, views
from autokeeper.engine.encounter.plan import (
    AttackPick,
    DeclaredAction,
    EncounterStart,
    EncounterTurnPlan,
    Thing,
)
from autokeeper.module.schema import Creature, Event, Module, Stake
from autokeeper.paths import EXAMPLE

from .encounter_kit import THREAD, arrange, attack, begin, fight_game, play

HIT = "攻击 楼梯口打盹的老人（徒手）｜被攻击时闪避"


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


def warden_attacks(state, text, brief):
    return EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng", summary="守钟人挥拳"))])


async def test_a_round_asks_the_player_before_any_die_of_the_blow_aimed_at_them(module, fake):
    game, cast, _ = fight_game(module, fake, arrange=warden_attacks)
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10, 60, 20, 50).queue_totals(2, 3)
    _out, asked = await play(app, HIT)
    st = game.state
    assert [a["kind"] for a in asked] == ["reaction"] and asked[0]["who"] == "zhouheng"
    assert list(fake.values) == [20, 50] and list(fake.totals) == [3]  # not a die of the warden's blow yet
    assert "楼梯口打盹的老人" in asked[0]["prompt"] and "老守钟人" not in asked[0]["prompt"]  # the name the players know
    assert {o["id"] for o in asked[0]["options"]} >= {"dodge", "fight_back"}
    from langgraph.types import Command

    more = []
    async for mode, chunk in app.astream(Command(resume="dodge"), THREAD, stream_mode=["custom", "updates"]):
        if mode == "custom":
            more.append(chunk)
    acts = [e for e in more if e["type"] == "fight_act"]
    assert len(acts) == 1 and acts[0]["attacker"] == "warden" and acts[0]["reaction"] == "dodge"
    assert st.investigator.hp == 8 and game.world.rec.entities["warden"].life.hp == 9
    assert not fake.values and not fake.totals and not st.pending_turn
    report = cast.reports[-1]
    assert "【战斗 · 第 1 轮】先后：周衡 → 老守钟人" in report and "【称呼】老守钟人（玩家所见：楼梯口打盹的老人）" in report


async def test_words_are_the_keeper_plans_never_parsed_by_their_first_word(module, fake):
    """The review: 「打开门逃跑」 began with 打 and became an attack on whoever was nearest."""
    door = DeclaredAction(do="other", summary="周衡想拉开门逃出去")
    game, cast, _ = fight_game(module, fake, act=EncounterTurnPlan(action=door, npcs=[arrange("warden", attack("zhouheng"))]))
    cast.ruling_for["周衡想拉开门逃出去"] = Ruling(needs_roll=True, skill="力量", reason="门很重")
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(30, 20, 90)  # STR to pull the door; the warden's blow, the dodge (asked: dodge)
    fake.queue_totals(1)
    out, _asked = await play(app, "打开门逃跑", answers=["dodge"])
    assert [c[0] for c in cast.calls][:1] == ["act"] and cast.calls[0][1] == "打开门逃跑"  # the plan read the words
    assert [a.summary for a in cast.ruled] == ["周衡想拉开门逃出去"]  # the arbiter ruled it, from the neutral summary
    acts = [e for e in out if e["type"] == "fight_act"]
    assert [a["attacker"] for a in acts] == ["warden"]  # nobody attacked the warden
    report = cast.reports[-1]
    assert "打开门逃跑" not in report and "周衡想拉开门逃出去" in report  # never the player's raw words


async def test_a_bracketed_name_is_a_choice_like_any_other(module, fake):
    module.creatures.append(Creature(id="eli", name="“埃里”（异界存在）", description="门框里的影子", sanity="0/1",
                                     stats={"生命": 8, "敏捷": 40}, attacks=["触手 30%，伤害 1D4"]))
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("eli", attack("zhouheng"))]))
    fake.queue(99)  # the first sight's sanity roll fails: 1
    await begin(game, ["eli"])
    words = [o.label for o in choices.options(game, "zhouheng")]
    label = next(w for w in words if w.startswith("攻击 “埃里”（异界存在）（徒手）"))
    found = choices.match(game, "zhouheng", label)
    assert found is not None and found.do == "attack" and found.attacks[0].target == "eli"
    assert choices.match(game, "zhouheng", "攻击 “埃里”") is None  # only the whole words


async def test_npcs_fight_back_and_a_creature_with_three_attacks_makes_them_all(module, fake):
    module.creatures.append(Creature(id="ghoul", name="食尸鬼", description="犬脸的东西", sanity="0/1",
                                     stats={"生命": 13, "敏捷": 70}, attacks=["爪击 40%，伤害 1D6", "每轮 3 次"]))
    game, _cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("ghoul", attack("zhouheng", n=1, summary="食尸鬼扑咬"))]))
    fake.queue(99)
    await begin(game, ["ghoul"])
    game.state.encounter.participants[0].asked = True  # asked before: its preset stands for an ordinary blow
    game.state.encounter.participants[0].react = "dodge"
    game.state.investigator.hp = game.state.investigator.hp_max = 30  # no blow can put him down: nothing to ask
    app = turn_graph.build(game, InMemorySaver())
    # the ghoul (DEX 70) first: three claws, each answered by a dodge; then 周衡's punch, which it fights back
    fake.queue(90, 90, 90, 90, 90, 90, 90, 90)
    out, asked = await play(app, "攻击 食尸鬼（徒手）｜被攻击时闪避")
    acts = [e for e in out if e["type"] == "fight_act"]
    assert not asked and [a["attacker"] for a in acts] == ["ghoul", "ghoul", "ghoul", "zhouheng"]
    assert acts[-1]["reaction"] == "fight"  # the keeper's people fight back unless arranged otherwise (p. 87)
    assert not fake.values


async def test_fighting_back_never_does_extreme_damage(module, fake):
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))]))
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(20, 5).queue_totals(2)  # 周衡 regular; the warden fights back with an extreme success: normal damage, 1D3
    out, _ = await play(app, HIT)
    act = next(e for e in out if e["type"] == "fight_act")
    assert act["winner"] == "defender" and act["hurt"] == "zhouheng" and not act["extreme"] and act["damage"] == 2


async def test_an_opening_blow_keeps_the_weapon_it_named(module, fake):
    """The review: the opening blow ignored the named weapon (fists became a revolver and back)."""
    module.npc("warden").stats = {"生命": 30}
    hit = Action(kind="attack", summary="周衡掏枪打向守钟人", npc="warden")
    shot = DeclaredAction(do="shoot", attacks=[AttackPick(target="warden", with_=".38 左轮手枪")], summary="周衡朝守钟人开枪")
    start = EncounterStart(mode="fight", foes=["warden"], pace=6, reason="开枪", opening=shot,
                           npcs=[arrange("warden", DeclaredAction(do="defend", summary="躲"), react="cover")])
    game, cast, _ = fight_game(module, fake, starts=[start], hints={"shoot": EncounterHint(mode="fight", **{"with": ["warden"]})})
    cast.script["shoot"] = (hit, Ruling(needs_roll=False))
    game.state.investigator.inventory.append(".38 左轮手枪")
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90, 10).queue_totals(5)  # he dives for cover and fails; the shot a hard success: 1D10 = 5
    out, _ = await play(app, "shoot")
    act = next(e for e in out if e["type"] == "fight_act")
    assert act["kind"] == "shot" and act["attack"] == ".38 左轮手枪" and act["roll"]["skill"] == "射击(手枪)"
    assert next(e for e in out if e["type"] == "fight_round")["order"][0] == "zhouheng"  # +50: a readied gun fired
    gun = game.world.rec.entities[".38 左轮手枪"]
    assert gun.weapon.loaded == 5  # the round fired is the gun's, in the world
    assert game.world.rec.entities["warden"].life.hp == 25 and act["reaction"] == "cover"


async def test_disarming_drops_the_real_weapon_and_claws_cannot_be_taken(module, fake):
    module.npc("warden").look += "，手里攥着一把扳手"
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))]))
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(5, 90)  # 周衡's disarm: extreme against a failed answer
    _out, _ = await play(app, "缴械 楼梯口打盹的老人｜被攻击时闪避")
    wrench = game.world.rec.entities["warden:棍棒"]
    assert wrench.at == "tower"  # on the floor where they fight, no longer his
    assert all(not a.item for a in views.Ctx.of(game).view("warden").attacks() if a.name == "棍棒") or \
        not any(a.name == "棍棒" for a in views.Ctx.of(game).view("warden").attacks())


async def test_a_dying_investigator_rolls_con_from_the_end_of_the_next_round_and_the_fight_goes_on(module, fake):
    """p. 101; the review: downed investigators were frozen."""
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))]))
    await begin(game, ["warden"])
    inv = game.state.investigator
    inv.hp, inv.conditions = 0, ["重伤", "濒死", "昏迷"]
    inv.injury.dying_since = None
    game.state.encounter.participants[0].react = "dodge"
    from autokeeper.engine import effects

    # dying since round 1 (the first round): no roll at its end, one at the end of round 2
    inv.injury.dying_since = 1
    app = turn_graph.build(game, InMemorySaver())
    out, _ = await play(app, "/encounter")
    assert game.state.encounter.round == 1 and "死亡" not in inv.conditions and not fake.values
    fake.queue(80)  # round 2's end: CON 55 fails
    out, _ = await play(app, "/encounter", thread={**THREAD, "configurable": {"thread_id": "enc2"}})
    assert "死亡" in inv.conditions
    assert any(e["type"] == "damage" and e["text"] == "伤重不治" for e in out)
    del effects


# -- running away, and what the outcome brings about --------------------------------------------------
FLEE = "逃跑｜被攻击时闪避"


async def test_an_explicit_let_go_is_obeyed_and_the_stakes_wait_for_it(module, fake):
    """The review: an explicit 「不追」 was overridden by the default pursuers; and the outcome was ruled
    before anyone decided whether to give chase."""
    module.flags["got_away"] = "调查员从守钟人那里脱身"
    game, cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", attack("zhouheng"), let_go=["zhouheng"])]))
    await begin(game, ["warden"], stakes=[Stake(when="escaped:players", flags=["got_away"])])
    app = turn_graph.build(game, InMemorySaver())
    _out, asked = await play(app, FLEE)
    st = game.state
    assert not asked and st.chase is None and "got_away" in st.flags
    assert st.encounter is None and st.encounter_log[-1].ended.how == "fled"
    report = cast.reports[-1]
    assert report.index("【周衡 脱身逃跑】") < report.index("周衡脱身了，没有人追上去") < report.index("【战斗结束】")
    assert [c[0] for c in cast.calls if c[0] == "pursuit"] == []  # decided in the arrangement: nobody asked again


async def test_an_undecided_pursuer_is_asked_before_the_outcome_and_a_chase_follows_a_yes(module, fake):
    module.flags["got_away"] = "调查员从守钟人那里脱身"
    game, cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", attack("zhouheng"))]),
        pursuit=PursuitAnswer(decisions=[Pursuit(who="warden", pursue=["zhouheng"], why="护着钟楼")]))
    await begin(game, ["warden"], stakes=[Stake(when="escaped:players", flags=["got_away"])])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(40, 40)  # the chase's speed rolls (CON): 8 and 8
    out, _asked = await play(app, FLEE)
    st = game.state
    assert "pursuit" in [c[0] for c in cast.calls] and "zhouheng" in cast.calls[-1][2]  # asked, and told who ran
    assert st.chase is not None and st.encounter.mode == "chase" and "got_away" not in st.flags  # not judged yet
    assert any(e["type"] == "chase_start" for e in out) and "【战斗转为追逐】" in cast.reports[-1]
    assert "【战斗结束】" not in cast.reports[-1]


async def test_a_pursuit_nobody_decides_is_no_pursuit_and_says_so(module, fake):
    game, _cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", attack("zhouheng"))]), pursuit=None)
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    await play(app, FLEE)
    assert game.state.chase is None and game.state.encounter_log[-1].ended.how == "fled"


# -- a horror first seen ------------------------------------------------------------------------------
GHOUL = Creature(id="ghoul", name="食尸鬼", called="犬脸的佝偻身影", description="犬脸的东西", sanity="0/1D6",
                 stats={"生命": 13, "敏捷": 40}, attacks=["爪击 40%，伤害 1D6"], notes="护甲 3；枪伤减半")


async def test_first_sight_costs_each_investigator_once_and_never_what_an_event_paid(module, fake):
    module.creatures.append(GHOUL.model_copy())
    lead = module.pregens[0]
    from .test_engine import make

    game, _, events = make(module, fake, {}, location="tower")
    game.state.investigators.append(lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True))
    fake.queue(30, 99).queue_totals(4)  # 周衡's roll holds, 林秋's fails: 1D6 = 4
    await begin(game, ["ghoul"])
    sanity = [e for e in events if e["type"] == "sanity"]
    assert [e["who"] for e in sanity] == ["周衡", "林秋"] and sum(e["type"] == "reveal" for e in events) == 1
    assert next(e for e in events if e["type"] == "reveal")["name"] == "犬脸的佝偻身影"  # never the bestiary's name
    assert game.state.sightings == {"zhouheng": ["ghoul"], "lin": ["ghoul"]}
    # an event that pays for the sight itself (its own sanity roll, Event.sights): nobody pays again
    other = Module.load(EXAMPLE)
    other.events, other.endings = [], []
    other.creatures.append(GHOUL.model_copy())
    horde = Event(id="e_horde", title="它们出来了", when="flag:x", text="", sanity="0/1D3", fight=["ghoul"], sights=["ghoul"])
    game2, _, events2 = make(other, fake, {}, location="tower")
    fake.queue(30)  # the event's own roll, the only one
    from autokeeper.engine.game import Turn

    await game2._fire(horde, Turn())
    assert sum(e["type"] == "sanity" for e in events2) == 1 and game2.state.encounter is not None


async def test_danger_the_players_can_judge_reaches_the_keeper_in_their_words(module, fake):
    module.creatures.append(GHOUL.model_copy(update={"attacks": ["爪击 40%，伤害 1D6", "每轮 3 次"]}))
    game, _cast, _events = fight_game(module, fake)
    fake.queue(30)
    turn = await begin(game, ["ghoul"])
    cues = [line for line in turn.fight if line.startswith("【可观察】")]
    assert any("爪子" in c for c in cues) and any("硬壳" in c for c in cues) and not any(ch.isdigit() for c in cues for ch in c)


# -- what the keeper knows and what its people perceive ----------------------------------------------------
async def test_the_brief_says_what_each_npc_perceives_and_the_check_refuses_who_it_cannot_see(module, fake):
    from autokeeper.engine.encounter import plan_check
    from autokeeper.engine.encounter.model import Cover

    game, _cast, _ = fight_game(module, fake)
    await begin(game, ["warden"])
    game.state.encounter.scene.cover.append(Cover(who="zhouheng", what="大钟背后", partial=False, since=0))
    text = views.keeper_text(game)
    assert "他察觉得到：谁也看不见" in text and "zhouheng（藏在掩体后面，看不见）" in text
    landed = plan_check.land(game, EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng"))]), act=False)
    assert not landed.npcs and any("warden 看不见 zhouheng" in p for p in landed.unsettled)


async def test_an_answer_still_wrong_after_its_resend_lands_nothing_in_its_place(module, fake):
    """The user's call: nothing is substituted; the NPC does nothing that round, its defence from its
    sheet; the whole answer goes to the developer's journal and the keeper hears 【未落实】."""
    class Journal:
        rows: list = []

        def append(self, kind, data, turn=None):
            self.rows.append((kind, data))

        def sync(self, *_a, **_k):
            pass

        def observe(self, *_a, **_k):
            pass

    bad = EncounterTurnPlan(npcs=[arrange("warden", attack("nobody"))])
    game, cast, _events = fight_game(module, fake, arrange=[bad], act=None)
    game.debug_journal = Journal()
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(30, 20).queue_totals(1)  # 周衡's punch (regular), the warden fights back by its sheet (hard): 1 to 周衡
    out, _asked = await play(app, HIT)
    acts = [e for e in out if e["type"] == "fight_act"]
    assert [a["attacker"] for a in acts] == ["zhouheng"] and acts[0]["reaction"] == "fight"  # no blow of its own
    report = cast.reports[-1]
    assert "【未落实】" in report and "nobody 不在这场遭遇里" in report
    assert any(kind == "encounter-plan" and "nobody" in str(data) for kind, data in Journal.rows)


async def test_an_important_npc_spends_its_luck_pool_on_its_own_roll(module, fake):
    module.npc("warden").important = True
    game, cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", attack("zhouheng"), luck=10)]))
    await begin(game, ["warden"])
    game.state.encounter.participants[0].asked, game.state.encounter.participants[0].react = True, "dodge"
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90, 50, 90, 50).queue_totals(1)  # 周衡 misses (the warden fights back: 50 against 45, 5 short); its blow, the dodge
    _out, _asked = await play(app, HIT)
    ent = game.world.rec.entities["warden"]
    assert ent.luck == 50 - 5  # its POW, less what it spent; it does not come back
    assert any("花了 5 点幸运" in line for line in cast.reports[-1].splitlines())


# -- something else, as the arbiter rules it ------------------------------------------------------------
async def test_something_else_is_ruled_and_the_whole_ruling_lands(module, fake):
    module.flags["lamp_broken"] = "油灯被砸碎了"
    lamp = DeclaredAction(do="other", summary="周衡把油灯砸向守钟人")
    game, cast, _ = fight_game(module, fake, act=EncounterTurnPlan(action=lamp, npcs=[arrange("warden", DeclaredAction(do="defend", summary="守"))]))
    cast.ruling_for["周衡把油灯砸向守钟人"] = Ruling(needs_roll=True, skill="投掷", set_flags=["lamp_broken"],
                                                 effects=[RuledEffect(kind="damage", target="warden", dice="1D6", what="油灯砸碎")])
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10).queue_totals(4)
    await play(app, "我把油灯砸过去！")
    assert "lamp_broken" in game.state.flags and game.world.rec.entities["warden"].life.hp == 7
    assert "我把油灯砸过去" not in cast.reports[-1]


async def test_a_weapon_picked_up_is_registered_before_it_is_used(module, fake):
    poker = DeclaredAction(do="attack", attacks=[AttackPick(target="warden", with_="拨火棍")], takes=[Thing(name="拨火棍")],
                           summary="周衡抄起拨火棍打守钟人")
    game, _cast, _events = fight_game(module, fake, act=EncounterTurnPlan(action=poker, npcs=[arrange("warden", DeclaredAction(do="defend", summary="守"))]))
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10, 90, 30).queue_totals(6)  # 1D8 = 6, a major wound: the warden's CON holds
    out, _ = await play(app, "我抄起壁炉边的拨火棍砸他")
    act = next(e for e in out if e["type"] == "fight_act")
    assert act["attack"] == "棍棒" and "拨火棍" in game.state.investigator.inventory
    from autokeeper.engine.encounter import plan_check

    unheld = DeclaredAction(do="attack", attacks=[AttackPick(target="warden", with_="斧头")], summary="周衡挥斧")
    assert any("没有拿着 斧头" in p for p in plan_check.turn(game, "zhouheng")(EncounterTurnPlan(action=unheld)))


async def test_shooting_into_a_melee_takes_a_penalty_die(module, fake):
    from autokeeper.engine.encounter.model import Engagement

    module.npc("warden").stats = {"生命": 30}
    start = EncounterStart(mode="fight", foes=["warden"], allies=["preacher"], pace=6, reason="混战")
    game, _cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[
        arrange("warden", DeclaredAction(do="defend", summary="守")), arrange("preacher", DeclaredAction(do="defend", summary="守"))]))
    game.state.investigator.inventory.append(".38 左轮手枪")
    from autokeeper.engine.game import Turn

    assert await game._encounter_open(start, Turn())
    game.state.encounter.scene.engaged.append(Engagement(a="preacher", b="warden", since=0))
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90, 10).queue_totals(3)
    out, _ = await play(app, "射击 楼梯口打盹的老人（.38 左轮手枪）｜被攻击时闪避")
    act = next(e for e in out if e["type"] == "fight_act")
    assert "朝扭打中的人开枪" in act["line"] and act["roll"]["bonus"] == -1


async def test_a_creature_that_heals_itself_gets_up_again(module, fake):
    module.creatures.append(Creature(id="husk", name="空壳", description="缝起来的尸体", sanity="0/1", stats={"生命": 2, "敏捷": 30},
                                     attacks=["抓 20%，伤害 1D3"], notes="每轮恢复 2 点"))
    game, cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("husk", DeclaredAction(do="defend", summary="守"))]))
    fake.queue(30)
    await begin(game, ["husk"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10, 90).queue_totals(3)  # a hard punch: 3, it is down, and comes back at the round's end
    _out, _ = await play(app, "攻击 空壳（徒手）｜被攻击时闪避")
    life = game.world.rec.entities["husk"].life
    assert life.hp == 2 and not life.regenerating and game.state.encounter.phase == "open"
    assert "又撑着站了起来" in cast.reports[-1]


async def test_an_old_save_in_the_middle_of_a_fight_comes_back_as_an_encounter(module, fake):
    from autokeeper.engine import combat as battle
    from autokeeper.engine.encounter import migrate
    from autokeeper.engine.game import Game
    from autokeeper.rules.coc import Rules

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    me, warden = battle.from_investigator(state.investigators[0]), battle.from_npc(module.npc("warden"))
    warden.hp = 4
    state.fight = battle.Fight(fighters=[me, warden], place="tower", round=2, started=1)
    state.pending_turn = {"input": "攻击", "resolution": {"steps": {"battle-mode": {"done": True}, "fight-round": {"done": True}}}}
    from .test_engine import FakeCast

    game = Game(module, state, FakeCast({}), Rules(dice=fake))  # type: ignore[arg-type]
    enc = game.state.encounter
    assert enc is not None and game.state.fight is None and enc.round == 2 and [p.id for p in enc.participants] == ["zhouheng", "warden"]
    assert game.world.rec.entities["warden"].life.hp == 4  # adopted as it stood
    assert not game.state.pending_turn and any(e.text == migrate.DROPPED for e in game.state.log)
    await game.close()


# -- care, numbers, companions, the chase's end -----------------------------------------------------------
async def test_first_aid_wakes_the_unconscious_and_ticks_the_skill(module, fake):
    lead = module.pregens[0]
    game, _cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="守"))]))
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    friend.hp, friend.conditions = 0, ["昏迷"]
    friend.injury.injuries = ["old:w1"]
    game.state.investigators.append(friend)
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10)  # 急救 (base 30): a hard success
    await play(app, "急救 林秋｜被攻击时闪避")
    lin = game.state.find("lin")
    assert lin.hp == 1 and "昏迷" not in lin.conditions and "old:w1" in lin.injury.aided  # once per injury
    assert "急救" in game.state.investigator.marks


async def test_outnumbered_a_defender_who_has_answered_gives_the_next_attacker_a_bonus_die(module, fake):
    lead = module.pregens[0]
    game, cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="守"))]))
    game.state.investigators.append(lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True))
    game.state.human = ["zhouheng"]
    cast.choose = lambda options: next(o.id for o in options if o.value.get("do") == "attack")  # the AI companion attacks too
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90, 90, 90, 90)  # both miss; the warden answers both
    out, _ = await play(app, HIT)
    acts = [e for e in out if e["type"] == "fight_act"]
    assert [a["attacker"] for a in acts] == ["zhouheng", "lin"]  # the AI companion picked from the same choices
    assert acts[0]["roll"]["bonus"] == 0 and acts[1]["roll"]["bonus"] == 1 and "以多打少" in acts[1]["line"]


async def test_caught_at_the_end_of_a_chase_it_is_a_fight_again_in_the_same_encounter(module, fake):
    from autokeeper.engine.game import Turn

    hounds = Event(id="e_run", title="守钟人追了上来", when="flag:x", text="", chase=["warden"])
    game, _cast, events = fight_game(module, fake)
    fake.queue(40, 40)
    await game._fire(hounds, Turn())
    eid = game.state.encounter.id
    await game._chase_end(Turn(), "caught", "守钟人追上了周衡", fight_ids=["warden", "zhouheng"])
    enc = game.state.encounter
    assert game.state.chase is None and enc.id == eid and enc.mode == "fight" and enc.phase == "open"
    assert enc.switches == ["r0:fight"] and [(e.a, e.b) for e in enc.scene.engaged] == [("warden", "zhouheng")]
    assert [e["type"] for e in events][-2:] == ["chase_end", "fight_start"]


async def test_the_push_offer_passes_its_answer_on_as_it_came(module, fake, monkeypatch):
    """§9: the push offer's resume value is the player's word or {"choice": "push", "how": ...}; the
    graph hands it to Game.push unchanged."""
    from autokeeper.engine.game import Turn

    from .test_engine import make

    look = Action(kind="investigate", summary="周衡查看齿轮", target="齿轮")
    ruling = Ruling(needs_roll=True, skill="侦查", clues_on_success=["c_diary"])
    game, _, _ = make(module, fake, {"look": (look, ruling)}, location="tower")
    module.clue("c_diary").where = "tower"
    got = []

    async def push(value):
        got.append(value)
        return Turn()

    monkeypatch.setattr(game, "push", push)
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90)
    answer = {"choice": "push", "how": "把齿轮一个个拆下来看"}
    await play(app, "look", answers=[answer])
    assert got == [answer]
