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

HIT = "攻击 楼梯口打盹的老人（徒手，1D3）"


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
    assert len(acts) == 1 and acts[0]["attacker"] == "f1" and acts[0]["reaction"] == "dodge"
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
    assert [a["attacker"] for a in acts] == ["f1"]  # nobody attacked the warden
    report = cast.reports[-1]
    assert "打开门逃跑" not in report and "周衡想拉开门逃出去" in report  # never the player's raw words


async def test_a_keepers_note_in_a_name_never_reaches_the_players(module, fake):
    """The user's game of 冬雪之下 (2026-10-22): the fight showed 「“埃里”（异界存在）」, a creature's name
    with the keeper's note in it. They see what they know (profiles.shown_name), never the note."""
    module.creatures.append(Creature(id="eli", name="“埃里”（异界存在）", description="门框里的影子", sanity="0/1",
                                     stats={"生命": 8, "敏捷": 40}, attacks=["触手 30%，伤害 1D4"]))
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("eli", attack("zhouheng"))]))
    fake.queue(99)  # the first sight's sanity roll fails: 1
    await begin(game, ["eli"])
    words = [o.label for o in choices.options(game, "zhouheng")]
    assert words[0] == "攻击 不明生物（徒手，1D3）"  # no `called`, not identified: nothing of its name
    assert not any("埃里" in w or "异界" in w for w in words)
    game.state.identified.append("eli")  # once they know what it is: its name, never the note
    label = next(o.label for o in choices.options(game, "zhouheng") if o.label.startswith("攻击 "))
    assert label == "攻击 “埃里”（徒手，1D3）" and views.fight_view(game)["fighters"][1]["name"] == "“埃里”"
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
    game.state.identified.append("ghoul")  # they know what it is: 食尸鬼 on their screens
    game.state.encounter.participants[0].asked = True  # asked before: its preset stands for an ordinary blow
    game.state.encounter.participants[0].react = "dodge"
    game.state.investigator.hp = game.state.investigator.hp_max = 30  # no blow can put him down: nothing to ask
    app = turn_graph.build(game, InMemorySaver())
    # the ghoul (DEX 70) first: three claws, each answered by a dodge; then 周衡's punch, which it fights back
    fake.queue(90, 90, 90, 90, 90, 90, 90, 90)
    out, asked = await play(app, "攻击 食尸鬼（徒手，1D3）")
    acts = [e for e in out if e["type"] == "fight_act"]
    assert not asked and [a["attacker"] for a in acts] == ["f1", "f1", "f1", "zhouheng"]
    assert acts[-1]["reaction"] == "fight_back"  # the keeper's people fight back unless arranged otherwise (p. 87)
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
    _out, _ = await play(app, "缴械 楼梯口打盹的老人")
    wrench = game.world.rec.entities["warden:棍棒"]
    assert wrench.at == "tower"  # on the floor where they fight, no longer his
    assert all(not a.item for a in views.Ctx.of(game).view("warden").attacks() if a.name == "棍棒") or \
        not any(a.name == "棍棒" for a in views.Ctx.of(game).view("warden").attacks())


async def test_a_dying_investigator_rolls_con_from_the_end_of_the_next_round_and_the_fight_goes_on(module, fake):
    """p. 101; the review: downed investigators were frozen. With nobody else at the table able to act,
    the rounds go on by themselves: no roll at the end of the round he began dying in, one at every
    round's end after it; a failure, and with every investigator dead the game reaches its end."""
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))]))
    await begin(game, ["warden"])
    inv = game.state.investigator
    inv.hp, inv.conditions = 0, ["重伤", "濒死", "昏迷"]
    inv.injury.dying_since = 1  # dying since round 1 (the first round): no roll at its end, one at the end of round 2
    game.state.encounter.participants[0].react = "dodge"
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(80)  # round 2's end: CON 55 fails
    out, _ = await play(app, "/encounter")
    st = game.state
    assert "死亡" in inv.conditions and not fake.values and st.encounter_log[-1].rounds == 2
    assert [e["round"] for e in out if e["type"] == "fight_round"] == [1, 2]
    assert any(e["type"] == "damage" and e["text"] == "伤重不治" for e in out)
    assert st.encounter_log[-1].ended.how == "lost" and st.ending == "party_dead"  # no ending of the module's: the engine's


# -- running away, and what the outcome brings about --------------------------------------------------
FLEE = "逃跑"


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
    assert not asked and "got_away" in st.flags
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
    assert st.encounter.chase is not None and st.encounter.mode == "chase" and "got_away" not in st.flags  # not judged yet
    assert any(e["type"] == "chase_start" for e in out) and "【战斗转为追逐】" in cast.reports[-1]
    assert "【战斗结束】" not in cast.reports[-1]


async def test_a_pursuit_nobody_decides_is_no_pursuit_and_says_so(module, fake):
    game, _cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", attack("zhouheng"))]), pursuit=None)
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    await play(app, FLEE)
    assert game.state.encounter is None and game.state.encounter_log[-1].ended.how == "fled"


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
    assert [a["attacker"] for a in acts] == ["zhouheng"] and acts[0]["reaction"] == "fight_back"  # no blow of its own
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
    out, _asked = await play(app, HIT)
    ent = game.world.rec.entities["warden"]
    assert ent.luck == 50 - 5  # its POW, less what it spent; it does not come back
    assert any("花了 5 点幸运" in line for line in cast.reports[-1].splitlines())  # the keeper knows
    shown = [line for e in out if e["type"] == "fight_state" for line in e.get("lines", [])]
    shown += [e.get("line", "") for e in out if e["type"] == "fight_act"]
    assert not any("幸运" in line for line in shown) and not any("幸运" in e.text for e in game.state.log)  # the players do not


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
    assert act["attack"] == "拨火棍" and "拨火棍" in game.state.investigator.inventory  # its own name; a club by the table
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
    out, _ = await play(app, "射击 楼梯口打盹的老人（.38 左轮手枪，1D10）")
    act = next(e for e in out if e["type"] == "fight_act")
    assert "朝扭打中的人开枪" in act["line"] and act["roll"]["bonus"] == -1


async def test_a_creature_that_heals_itself_gets_up_again(module, fake):
    module.creatures.append(Creature(id="husk", name="空壳", description="缝起来的尸体", sanity="0/1", stats={"生命": 2, "敏捷": 30},
                                     attacks=["抓 20%，伤害 1D3"], notes="每轮恢复 2 点"))
    game, cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("husk", DeclaredAction(do="defend", summary="守"))]))
    fake.queue(30)
    await begin(game, ["husk"])
    game.state.identified.append("husk")
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10, 90).queue_totals(3)  # a hard punch: 3, it is down, and comes back at the round's end
    _out, _ = await play(app, "攻击 空壳（徒手，1D3）")
    life = game.world.rec.entities["husk"].life
    assert life.hp == 2 and not life.regenerating and game.state.encounter.phase == "open"
    assert "又撑着站了起来" in cast.reports[-1]


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
    await play(app, "急救 林秋")
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


async def test_a_fight_the_plan_said_a_failure_sets_off_begins_after_a_failed_push(module, fake):
    """The review: with a pushable failure the fight of an on_fail hint never began."""
    talk = Action(kind="talk", summary="周衡想骗守钟人交出日记", npc="warden", speech="把日记给我")
    ruling = Ruling(needs_roll=True, skill="话术", clues_on_success=["c_diary"])
    start = EncounterStart(mode="fight", foes=["warden"], pace=6, reason="守钟人恼了", npcs=[arrange("warden", attack("zhouheng"))])
    game, cast, _events = fight_game(module, fake, starts=[start],
                                    hints={"lie": EncounterHint(mode="fight", trigger="on_fail", **{"with": ["warden"]})})
    cast.script["lie"] = (talk, ruling)
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90, 90)  # the lie fails; pushed, it fails again
    out, asked = await play(app, "lie", answers=[{"choice": "push", "how": "换个说法再试一次"}])
    assert [a["kind"] for a in asked] == ["push"]
    assert game.state.encounter is not None and any(e["type"] == "fight_start" for e in out)
    assert not any(e["type"] == "npc" for e in out)  # the fight it set off: no ordinary reply on top of it


# -- WP-D: delaying one's action, the aim, the NPC's own voice, an event's move held back ---------------
async def test_a_delayed_action_waits_for_the_one_named_and_then_goes(module, fake):
    """pp. 86, 113: a character may delay their action until another has acted."""
    wait = DeclaredAction(do="attack", attacks=[AttackPick(target="warden", with_="natural:徒手")], after="warden",
                          summary="周衡等守钟人先动")
    game, cast, _ = fight_game(module, fake, act=EncounterTurnPlan(action=wait), arrange=warden_attacks)
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90, 90, 90, 90)  # the warden misses, 周衡's dodge fails too; then 周衡 misses, the warden's answer fails
    out, _asked = await play(app, "等他先动手，我再打", answers=["dodge"])
    acts = [e["attacker"] for e in out if e["type"] == "fight_act"]
    assert acts == ["f1", "zhouheng"]  # DEX 65 would have gone first
    assert "周衡等着老守钟人先动" in cast.reports[-1] and not fake.values


async def test_an_aim_is_for_the_next_shot_only(module, fake):
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))]))
    game.state.investigator.inventory.append(".38 左轮手枪")
    game.state.investigator.skills["射击(手枪)"] = 50
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    labels = [o.label for o in choices.options(game, "zhouheng")]
    aim = next(x for x in labels if x.startswith("瞄准"))
    shot = next(x for x in labels if x.startswith("射击"))
    await play(app, aim)
    fake.queue(95, 95)
    out, _ = await play(app, shot)
    out2, _ = await play(app, shot)
    first, second = (next(e for e in o if e["type"] == "fight_act") for o in (out, out2))
    assert first["roll"]["bonus"] == 1 and "瞄准" in first["line"]
    assert second["roll"]["bonus"] == 0  # the aim was spent on the shot after it


async def test_a_punch_after_aiming_does_not_spend_the_aim_but_the_round_after_it_is_gone(module, fake):
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))]))
    game.state.investigator.inventory.append(".38 左轮手枪")
    game.state.investigator.skills["射击(手枪)"] = 50
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    labels = [o.label for o in choices.options(game, "zhouheng")]
    await play(app, next(x for x in labels if x.startswith("瞄准")))
    fake.queue(95, 95)  # the punch misses, the warden's answer fails
    out, _ = await play(app, HIT)
    assert next(e for e in out if e["type"] == "fight_act")["roll"]["bonus"] == 0
    assert not any(e.source.startswith("aim") for e in game.state.encounter.get("zhouheng").edges)  # not kept past its round


async def test_an_npc_the_plan_has_speak_says_it_through_his_own_reply(module, fake):
    talk = arrange("warden", DeclaredAction(do="defend", summary="守着"), speak=True, goal="劝他们离开钟楼")
    game, cast, _events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[talk]))
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(95, 95)
    out, _ = await play(app, HIT)
    said = [m for who, m in cast.npc_calls if who == "warden"]
    assert len(said) == 1 and "正在打斗" in said[0] and "劝他们离开钟楼" in said[0]
    assert any(e["type"] == "npc" and e["npc"] == "warden" for e in out)


async def test_an_events_move_waits_while_a_fight_goes_on(module, fake):
    from autokeeper.engine.game import Turn

    game, _cast, _ = fight_game(module, fake)
    await begin(game, ["warden"])
    turn = Turn()
    await game._fire(Event(id="e_away", title="钟声把人震出了钟楼", when="flag:x", text="", move_to="square"), turn)
    assert game.state.location == "tower" and any("遭遇还在进行" in n for n in turn.notes)


async def test_asked_every_time_when_the_player_says_so(module, fake):
    game, _cast, _ = fight_game(module, fake, arrange=warden_attacks)
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(95, 95, 95, 95)  # both rounds: the punch and its answer miss; the warden's blow and the dodge miss
    _out, asked = await play(app, HIT, answers=[{"choice": "dodge", "ask": "always"}])
    assert [a["kind"] for a in asked] == ["reaction"] and game.state.encounter.get("zhouheng").ask == "always"
    fake.queue(95, 95, 95, 95)
    _out, asked = await play(app, HIT, answers=["dodge"], thread={**THREAD, "configurable": {"thread_id": "again"}})
    assert [a["kind"] for a in asked] == ["reaction"] and asked[0]["ask"] == "always"  # a blow that could not put him down


# -- when the game stops to ask how a blow is answered (review 2026-10-22) -----------------------------
CLAW = Creature(id="ghoul", name="食尸鬼", description="犬脸的东西", sanity="0/1", stats={"生命": 13, "敏捷": 10},
                attacks=["爪击 40%，伤害 1D6"])


async def asking(module, fake, hp: int = 11):
    from autokeeper.engine.encounter.actions import Ctx

    module.creatures.append(CLAW.model_copy(deep=True))
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("ghoul", attack("zhouheng"))]))
    fake.queue(99)
    await begin(game, ["ghoul"])
    inv = game.state.investigator
    inv.hp = inv.hp_max = hp
    claw = next(a for a in Ctx.of(game).view("ghoul").attacks() if a.name == "爪击")

    def ask() -> bool:
        return choices.should_ask(Ctx.of(game), "zhouheng", "ghoul", claw, "melee", ["dodge", "fight_back"])

    return game, ask


async def test_after_the_first_ask_the_answer_set_stands_for_blows_no_more_dangerous(module, fake):
    """11 HP against a 1D6 claw: asked the first time, then the answer stands, claw after claw."""
    game, ask = await asking(module, fake)
    me = game.state.encounter.get("zhouheng")
    assert ask()  # the first blow at them
    me.asked, me.react, me.risk = True, "dodge", 1  # asked about a blow that could wound them badly (6 of 11)
    assert [ask() for _ in range(3)] == [False, False, False]


async def test_asked_again_once_each_time_the_danger_rises_and_when_the_answer_does_not_fit(module, fake):
    game, ask = await asking(module, fake)
    me = game.state.encounter.get("zhouheng")
    me.asked, me.react, me.risk = True, "dodge", 1
    game.state.investigator.hp = 5  # a claw could now put him down
    assert ask()
    me.risk = 2  # asked about it: not again for the same danger
    assert not ask() and not ask()
    me.react = "cover"  # an answer that does not fit a claw
    assert ask()
    me.react, me.ask = "dodge", "always"  # 每次被攻击都询问
    assert ask()


async def test_the_answer_given_in_the_window_is_the_one_kept_for_the_next_blows(module, fake):
    game, _cast, _ = fight_game(module, fake, arrange=warden_attacks)
    await begin(game, ["warden"])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10, 60, 20, 50).queue_totals(2, 3)  # 周衡 lands one; the warden swings; 周衡 fights back and fails
    _out, asked = await play(app, HIT, answers=["fight_back"])
    me = game.state.encounter.get("zhouheng")
    assert [a["kind"] for a in asked] == ["reaction"] and me.react == "fight_back" and me.asked
    assert views.table(game)["presets"]["zhouheng"] == "fight_back"


# -- the answer to blows: set once beside the list, never in an option's words ------------------------
async def test_options_name_the_action_and_the_answer_to_blows_is_the_preset_set_beside_them(module, fake):
    game, cast, _ = fight_game(module, fake, arrange=warden_attacks)
    game.state.investigator.inventory.append(".38 左轮手枪")
    await begin(game, ["warden"])
    labels = [o.label for o in choices.options(game, "zhouheng")]
    assert "攻击 楼梯口打盹的老人（徒手，1D3）" in labels and "射击 楼梯口打盹的老人（.38 左轮手枪，1D10）" in labels
    assert not any(w in label for label in labels for w in ("被攻击时", "｜", "闪避", "反击"))
    now = views.table(game)
    assert now["presets"]["zhouheng"] == "dodge" and now["answers"]["zhouheng"] == ["dodge", "fight_back"]
    journal, rnd, calls = len(game.world.rec.journal), game.state.encounter.round, len(cast.calls)
    assert game.set_react("zhouheng", "fight_back") == ""
    enc = game.state.encounter
    assert enc.get("zhouheng").react == "fight_back" and enc.round == rnd and not enc.receipts and len(cast.calls) == calls
    assert len(game.world.rec.journal) == journal + 1  # its own op: the encounter changes only through the world
    assert views.table(game)["presets"]["zhouheng"] == "fight_back"
    assert [o.label for o in choices.options(game, "zhouheng")] == labels  # the words stay; only the preset moved
    assert game.set_react("zhouheng", "cover") == "这场战斗中不能这样应对"  # nobody has a gun
    assert game.set_react("zhouheng", "dodge") == "" and game.set_react("zhouheng", "fight_back") == ""  # back and forth
    found = choices.match(game, "zhouheng", "攻击 楼梯口打盹的老人（徒手，1D3）")
    assert found is not None and found.do == "attack" and found.react == "fight_back"
    assert choices.match(game, "zhouheng", "攻击 楼梯口打盹的老人（徒手，1D3）｜被攻击时闪避") is None
    game.state.pending_turn = {"input": "x"}
    assert game.set_react("zhouheng", "dodge") == "本轮正在结算"


async def test_an_ai_companion_sets_its_answer_to_blows_with_its_pick(module, fake):
    """The review: since the answer to blows left the option words, AI teammates only ever had the
    stored one (a dodge). Its pick sets it, held to what a player's Tab may set: fighting back yes,
    diving for cover (no foe with a gun) no."""
    li = module.pregens[0].model_copy(update={"id": "li", "name": "李四"}, deep=True)
    picks = [("防守", "fight_back"), ("防守", "cover")]

    def choose(options):
        label, react = picks.pop(0)
        return next(o.id for o in options if o.label == label), react

    keep = arrange("warden", DeclaredAction(do="defend", summary="守着"))
    game, cast, _ = fight_game(module, fake, choose=choose, arrange=lambda *a: EncounterTurnPlan(npcs=[keep]))
    game.state.investigators.append(li)
    await begin(game, ["warden"], npcs=[keep])
    app = turn_graph.build(game, InMemorySaver())
    await play(app, "防守")
    assert cast.stances == ["dodge", "fight_back"] and game.state.encounter.get("li").react == "fight_back"
    await play(app, "防守")
    assert game.state.encounter.get("li").react == "fight_back"  # cover is no answer it can set here
