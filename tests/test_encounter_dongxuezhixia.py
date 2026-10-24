"""The user's solo game of 《冬雪之下》 (2026-10-22) on the encounter engine, with scripted dice and a
scripted keeper plan: 托马斯·里德 against 埃里·赫琴斯, who fights as the entity `erie_entity`
(Creature.npc). What went wrong there, asserted the right way round: the names the players see, the
words the windows say, the Colt revolver he carries, and what comes after he is down."""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper import paths
from autokeeper.agents.cast import Action, Ruling
from autokeeper.agents.encounter import EncounterHint
from autokeeper.engine import debrief
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.encounter import actions, choices, plan_check, views
from autokeeper.engine.encounter.actions import Ctx
from autokeeper.engine.encounter.plan import AttackPick, DeclaredAction, EncounterStart, EncounterTurnPlan
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Module, Stake
from autokeeper.world.ops import Occur

from .encounter_kit import arrange, attack, begin, fight_game, play

ME = "thomas_reed"
COLT = "柯尔特左轮手枪"
SHOWN = "埃里·赫琴斯(?)"  # the man they met at the truck; not what he is
STAND = DeclaredAction(do="defend", summary="埃里站着不动")


@pytest.fixture
def module() -> Module:
    m = Module.load(paths.find_module("dongxuezhixia"))
    m.events, m.endings = [], []
    m.pregens = [next(p for p in m.pregens if p.id == ME)]  # the user's solo game: 托马斯 alone
    return m


def winter(module, fake, **modes):
    game, cast, events = fight_game(module, fake, location="main_street", **modes)
    game.state.npc("erie").met = True  # they met him at the truck
    result = game.world.apply(Occur(text="卡车旁的男人自称埃里·赫琴斯。", place="main_street",
                                    witnesses=[ME], introduced={"erie": ""}, actor="keeper"))
    assert result.ok, result.problems
    return game, cast, events


def hits_him(state, text, brief) -> EncounterTurnPlan:
    """埃里's keeper plan: he strikes at 托马斯 while he stands, and stops once he is down."""
    down = not state.find(ME).can_act
    return EncounterTurnPlan(npcs=[arrange("erie", STAND if down else attack(ME, summary="埃里扑向托马斯"))])


def leaked(text: str, *, ids: bool = True) -> list[str]:
    """What of the keeper's own the text gives away: the entity's name and note, its description, and
    with `ids` any id (a LAN table's view keys its seats by the investigators' ids: those may be there)."""
    keeper = ("异界", "“埃里”", "卡洛琳召来", "erie", "colt_revolver", "natural:")
    return [x for x in (*keeper, *(("thomas_reed",) if ids else ())) if x in text]


# -- 1. names ---------------------------------------------------------------------------------------
async def test_the_entity_is_fought_as_the_man_they_met_with_a_question_mark(module, fake):
    game, cast, events = winter(module, fake, arrange=hits_him)
    fake.queue(10)  # the first sight of it (1/1D4+1): the sanity roll holds, 1
    await begin(game, ["erie_entity"], npcs=[arrange("erie", attack(ME))])
    fighters = views.fight_view(game)["fighters"]
    assert [f["name"] for f in fighters] == ["托马斯·里德", SHOWN]
    words = [o.label for o in choices.options(game, ME)]
    assert f"射击 {SHOWN}（{COLT}，1D10）" in words and f"攻击 {SHOWN}（徒手，1D3+1D4）" in words
    table = json.dumps(views.table(game), ensure_ascii=False)  # what a LAN table is sent
    assert SHOWN in table and not leaked(table, ids=False)
    reveal = next(e for e in events if e["type"] == "reveal")
    assert reveal["name"] == SHOWN
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(40, 90).queue_totals(3)  # his claw lands, the dodge fails: 3; then 托马斯's punch
    fake.queue(90, 90)  # 托马斯 misses, 埃里 fights back and misses too
    out, asked = await play(app, f"攻击 {SHOWN}（徒手，1D3+1D4）", answers=["dodge"])
    assert asked[0]["prompt"] == f"{SHOWN}向你扑来，你怎么应对？"
    shown = [e for e in out if e["type"] in ("fight_act", "fight_state", "fight_round")]
    assert shown and not any(leaked(json.dumps(e, ensure_ascii=False), ids=False) for e in shown)
    assert all("_entity_line" not in e and "_entity_lines" not in e for e in out)
    assert {e["attacker_name"] for e in shown if e["type"] == "fight_act"} == {SHOWN, "托马斯·里德"}
    report = cast.reports[-1]
    assert f"【称呼】“埃里”（玩家所见：{SHOWN}）" in report  # the omniscient keeper may receive private backstage notes
    assert "异界存在" not in "\n".join(event.get("text", "") for event in out)
    assert not any(leaked(e.text) for e in game.state.log if e.kind in ("event", "dice"))


async def test_never_met_he_is_what_they_see_and_once_known_his_name_without_the_note(module, fake):
    game, _cast, _ = winter(module, fake, arrange=hits_him)
    game.state.npc("erie").met = False
    game.state.record.sightings[ME]["erie"].name = ""
    fake.queue(10)
    await begin(game, ["erie_entity"])
    assert views.fight_view(game)["fighters"][1]["name"] == "正在崩解的人形"  # the form's `called`
    game.state.identified.append("erie_entity")  # they know what it is now
    assert views.fight_view(game)["fighters"][1]["name"] == "“埃里”"


def test_a_name_with_a_keepers_note_is_flagged_by_the_check(module):
    from autokeeper.module import graph

    found = [str(i) for i in graph.audit(module)]
    assert any("怪物 erie_entity 的名字「“埃里”（异界存在）」带括号注释" in x for x in found)


# -- 2. the words a window says --------------------------------------------------------------------
async def test_a_shot_gone_stale_is_asked_again_in_the_players_words(module, fake):
    """The window said 「原来的行动做不成了：thomas_reed 没有拿着 colt_revolver（能用的：natural:徒手、
    柯尔特左轮手枪）。」: the plan's problem, for the model, shown to the player as it was."""
    game, _cast, _ = winter(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("erie", STAND)]))
    fake.queue(10)
    await begin(game, ["erie_entity"], npcs=[arrange("erie", STAND)])
    shot = DeclaredAction(do="shoot", attacks=[AttackPick(target="erie", with_="colt_revolver")], summary="托马斯拔枪射击")
    await game._declare(Turn(), ME, shot, "words", None)
    app = turn_graph.build(game, InMemorySaver())
    _out, asked = await play(app, "/encounter")
    assert asked[0]["kind"] == "redeclare"
    assert asked[0]["prompt"] == f"原来的行动做不成了：托马斯·里德手里没有这件武器（能用的：徒手、{COLT}）。这一下改做什么？"
    assert {o["label"] for o in asked[0]["options"]} >= {f"射击 {SHOWN}（{COLT}，1D10）", "防守"}
    # not asked (nobody at the table to answer): it lapses, and the account says so in the same words
    game.state.encounter.pending = None
    lapse = actions.preconditions(Ctx.of(game), ME, shot)[0]
    assert "colt_revolver" in lapse and actions.said(lapse) == f"{{{ME}}}手里没有这件武器（能用的：徒手、{COLT}）"


# -- 3. the Colt he carries ------------------------------------------------------------------------
async def test_the_colt_revolver_is_a_revolver_under_its_own_name_and_a_plan_names_it_by_name(module, fake):
    game, _cast, _ = winter(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("erie", STAND)]))
    fake.queue(10)
    await begin(game, ["erie_entity"])
    v = Ctx.of(game).view(ME)
    gun = next(a for a in v.attacks() if a.firearm)
    assert (gun.id, gun.name, gun.weapon, gun.damage_text, gun.capacity) == (COLT, COLT, ".38 左轮手枪", "1D10", 6)
    for words in (COLT, ".38 左轮手枪", "左轮手枪"):  # by id, by the table's name, by what it is
        assert actions.option(v, words, "shoot") == gun, words
    assert actions.option(v, "colt_revolver", "shoot") is None  # an id nobody has
    bad = EncounterStart(mode="fight", foes=["erie_entity"], pace=6, reason="开枪",
                         opening=DeclaredAction(do="shoot", attacks=[AttackPick(target="erie_entity", with_="colt_revolver")], summary="开枪"))
    assert plan_check.opening(game, bad) == [f"opening：{ME} 没有拿着 colt_revolver（能用的：natural:徒手、{COLT}）"]
    good = bad.model_copy(update={"opening": bad.opening.model_copy(update={"attacks": [AttackPick(target="erie_entity", with_=COLT)]})})
    assert plan_check.opening(game, good) == []


async def test_an_opening_with_a_weapon_nobody_holds_does_not_land_and_the_first_round_is_the_players(module, fake):
    """The plan's start wrote the first shot with `colt_revolver`, an id it made up from the inventory: it
    is sent back with the ids he can use; still wrong, it does not land."""
    shoot = DeclaredAction(do="shoot", attacks=[AttackPick(target="erie_entity", with_="colt_revolver")], summary="托马斯开枪")
    start = EncounterStart(mode="fight", foes=["erie_entity"], pace=6, reason="托马斯开枪", opening=shoot,
                           npcs=[arrange("erie", STAND)])
    hint = EncounterHint(mode="fight", **{"with": ["erie"]})
    game, cast, _ = winter(module, fake, starts=[start], hints={"我拔枪打他": hint})
    cast.script["我拔枪打他"] = (Action(kind="attack", summary="托马斯拔枪射击", npc="erie"), Ruling(needs_roll=False))
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10)  # the first sight
    out, asked = await play(app, "我拔枪打他")
    assert not asked and not any(e["type"] == "fight_act" for e in out)  # nothing fired with a gun he does not have
    enc = game.state.encounter
    assert enc is not None and enc.round == 0 and not enc.receipts  # the first round waits for his choice
    report = cast.reports[-1]  # the keeper hears what did not land, with the ids he can use
    assert f"【未落实】遭遇开始时的安排有没落实的：opening：{ME} 没有拿着 colt_revolver（能用的：natural:徒手、{COLT}）" in report
    assert any(o.label == f"射击 {SHOWN}（{COLT}，1D10）" for o in choices.options(game, ME))


@pytest.mark.parametrize(("words", "table", "action"), [
    ("柯尔特左轮手枪", ".38 左轮手枪", "revolver"),  # the maker's name never overrules what it is
    (".45 自动手枪", ".45 自动手枪", "auto"),
    ("勃朗宁", ".32 自动手枪", "auto"),  # a maker alone: its best-known one
    ("温彻斯特", "杠杆步枪", "lever"),
    ("温彻斯特霰弹枪", "霰弹枪", "break"),
    ("猎枪", "猎枪", "bolt"),
    ("匕首", "小刀", ""),
])
def test_real_weapon_names_find_their_entry_in_the_weapon_table(words, table, action):
    from autokeeper.rules.weapons import weapon_of

    found = weapon_of(words)
    assert found is not None and (found.name, found.mechanism) == (table, action)


def test_a_box_of_cartridges_is_no_gun(module, fake):
    from autokeeper.rules.weapons import weapon_of

    assert weapon_of("一盒 .38 子弹") is None and weapon_of("左轮手枪的枪套") is None
    game, _cast, _ = winter(module, fake)
    game.state.investigator.inventory[:0] = ["一盒 .38 子弹"]
    names = [a.name for a in Ctx.of(game, _fight(game)).view(ME).attacks()]
    assert names == ["徒手", COLT]


def test_two_guns_of_the_same_kind_require_the_specific_weapon(module, fake):
    game, _cast, _events = winter(module, fake)
    game.state.investigator.inventory.append("备用左轮手枪")
    view = Ctx.of(game, _fight(game)).view(ME)
    guns = [a for a in view.attacks() if a.firearm]
    assert len(guns) == 2
    assert actions.option(view, ".38 左轮手枪", "shoot") is None
    assert actions.option(view, "左轮手枪", "shoot") is None
    assert actions.option(view, COLT, "shoot").id == COLT
    assert actions.option(view, "备用左轮手枪", "shoot").id == "备用左轮手枪"


def _fight(game):
    from autokeeper.engine.encounter.model import EncounterState, Participant

    return EncounterState(id="x", mode="fight", place="main_street", started_turn=0,
                          participants=[Participant(id=ME, kind="investigator", side="party", controller="human")])


# -- 4. alone and down -----------------------------------------------------------------------------
async def test_down_and_dying_alone_the_rounds_go_on_and_death_ends_the_game(module, fake):
    """After 埃里's blow the game sat at an empty input box: no round went on, no dying roll, no end."""
    game, cast, _events = winter(module, fake, arrange=hits_him)
    fake.queue(10)
    await begin(game, ["erie_entity"], npcs=[arrange("erie", attack(ME))])
    game.state.investigator.hp = 8  # 埃里's claw for 8 is a major wound, and his last 8 points
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(40, 90).queue_totals(8).queue(90)  # the claw lands, the dodge fails: 8; CON for the major wound fails
    fake.queue(20)  # the end of round 2: dying, CON 55 holds
    fake.queue(80)  # the end of round 3: it fails
    out, asked = await play(app, f"攻击 {SHOWN}（徒手，1D3+1D4）", answers=["dodge"])
    st = game.state
    assert [a["kind"] for a in asked] == ["reaction"]  # asked once; then nobody waits for him
    assert [e["round"] for e in out if e["type"] == "fight_round"] == [1, 2, 3]  # each round on the screen as it settles
    assert "死亡" in st.investigator.conditions and not fake.values
    assert st.encounter is None and st.encounter_log[-1].ended.how == "lost"
    assert st.ending == "party_dead" and any(e["type"] == "ending" and e["title"] == "全员殒命" for e in out)
    report = cast.reports[-1]
    assert "还吊着一口气" in report and "没能撑过去" in report and "【结局：全员殒命】" in report
    assert debrief.build(module, st).ending == "全员殒命"  # the debrief opens on it as on any ending


async def test_a_foe_that_means_to_finish_him_does(module, fake):
    """The keeper plan has 埃里 strike at him lying there: an attempt to kill someone out cold succeeds
    (Keeper Rulebook p. 100)."""
    game, cast, _ = winter(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("erie", attack(ME))]))
    fake.queue(10)
    await begin(game, ["erie_entity"], npcs=[arrange("erie", attack(ME))])
    game.state.investigator.hp = 8
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(40, 90).queue_totals(8).queue(90)  # down and dying in round 1
    fake.queue(70).queue_totals(2)  # round 2: the claw at him lying there lands (not a fumble): finished
    await play(app, f"攻击 {SHOWN}（徒手，1D3+1D4）", answers=["dodge"])
    st = game.state
    assert "死亡" in st.investigator.conditions and not fake.values and st.ending == "party_dead"
    finished = next(x for x in cast.reports[-1].split("\n") if "（HP 0 → 0）" in x)
    assert "70 失败" in finished and "当场死亡" in finished  # a failed roll, unanswered: it still lands


async def test_out_cold_alone_it_ends_by_its_stakes_and_he_wakes_a_day_later(module, fake):
    """Unconscious, not dying: 埃里 lets him be, the fight ends as the stakes say, time passes per the
    rules (at 0 HP with only minor wounds: a day, the first point of natural healing), he plays on and
    the keeper tells what he wakes to."""
    module.flags["beaten"] = "托马斯被埃里打倒"
    game, cast, _events = winter(module, fake, arrange=hits_him)
    fake.queue(10)
    await begin(game, ["erie_entity"], npcs=[arrange("erie", attack(ME))], stakes=[Stake(when="ended:lost", flags=["beaten"])])
    inv = game.state.investigator
    inv.hp = 2
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(40, 90).queue_totals(3)  # a minor wound to 0: out cold
    out, _ = await play(app, f"攻击 {SHOWN}（徒手，1D3+1D4）", answers=["dodge"])
    st = game.state
    assert st.encounter is None and st.encounter_log[-1].ended.how == "lost" and "beaten" in st.flags
    assert inv.can_act and inv.hp == 1 and "昏迷" not in inv.conditions and not fake.values
    fell = st.encounter_log[-1]
    assert st.minutes >= 1440 and st.clock.startswith("D2")  # a day later
    assert any(e["type"] == "damage" and e["text"].startswith("醒了过来") for e in out)
    report = cast.reports[-1]
    assert "【昏迷】托马斯·里德失去了知觉" in report and "醒了过来（昏迷了 1 天）" in report
    assert fell.rounds == 2  # the round he fell in, and one in which 埃里 did nothing more to him


@pytest.mark.parametrize("conditions,stabilized", [
    (["永久疯狂"], False),
    (["重伤", "濒死", "昏迷"], True),
])
async def test_unable_to_resume_is_an_interrupted_investigation_not_a_death(module, fake, conditions, stabilized):
    game, _cast, events = winter(module, fake)
    inv = game.state.investigator
    inv.conditions = conditions
    if stabilized:
        inv.hp = 0
        inv.injury.stabilized_at = game.state.minutes
    before = inv.hp
    turn = Turn()
    await game._party_down(turn)
    assert game.state.ending == turn.ending == "party_unfit"
    assert "死亡" not in inv.conditions and inv.hp == before
    assert any(e["type"] == "ending" and e["title"] == "调查中断" for e in events)
    assert debrief.build(module, game.state).ending == "调查中断"
    assert not fake.values and not fake.totals
    await game.close()


async def test_waking_does_not_restore_a_permanently_insane_investigator(module, fake):
    game, _cast, _events = winter(module, fake)
    inv = game.state.investigator
    inv.conditions = ["昏迷", "永久疯狂"]
    inv.injury.unconscious_at = game.state.minutes
    await game._party_down(Turn())
    assert "昏迷" not in inv.conditions and "永久疯狂" in inv.conditions
    assert not inv.can_act and game.state.ending == "party_unfit"
    await game.close()


async def test_automatic_rounds_resume_without_repeating_the_dying_roll(module, fake):
    game, _cast, events = winter(module, fake, arrange=hits_him)
    fake.queue(10)
    await begin(game, ["erie_entity"], npcs=[arrange("erie", STAND)])
    inv = game.state.investigator
    inv.hp = 0
    inv.conditions = ["重伤", "昏迷", "濒死"]
    inv.injury.dying_since = 0
    game.state.pending_turn = {"input": "/encounter"}
    action = await game.encounter_input("/encounter")
    ruling = await game.rule(action)
    emit = game.emit

    async def interrupted(event):
        if event["type"] == "fight_round" and event["round"] == 2:
            raise ConnectionError("回合通知中断")
        await emit(event)

    game.emit = interrupted
    fake.queue(20, 80)
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    assert list(fake.values) == [80] and "死亡" not in inv.conditions
    game.emit = emit
    await game.resolve(action, ruling)
    assert not fake.values and game.state.ending == "party_dead"
    assert [e["round"] for e in events if e["type"] == "fight_round"] == [1, 2]
    assert sum(e["type"] == "ending" for e in events) == 1
    await game.close()


