"""The keeper plan inside a fight or a chase (Cast.plan with an encounter brief, plan_encounter,
encounter_choice), the brief it reads (agents/encounter.encounter_brief), and the arbiter's
encounter section. Scripted models, no model calls; the engine's checks are stand-ins."""

from __future__ import annotations

import pytest

from autokeeper import config
from autokeeper.agents.cast import Action, Cast, Ruling
from autokeeper.agents.encounter import (
    ACTION_NEEDS,
    ARBITER_ENCOUNTER,
    ENCOUNTER_PLAN_SYSTEM,
    ArrangePlan,
    Brief,
    BriefAction,
    BriefAttack,
    BriefPerson,
    BriefStake,
    EncounterHint,
    PursuitAnswer,
    ReactionAnswer,
    default_actions,
    encounter_brief,
)
from autokeeper.engine.encounter.plan import EncounterStart, EncounterTurnPlan, SiteEdge, SiteNode
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401


class Journal:
    def __init__(self):
        self.rows: list[tuple[str, dict]] = []

    def append(self, kind, data, turn=None):
        self.rows.append((kind, data))

    def observe(self, *_a, **_k):
        pass


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


@pytest.fixture
def cast(module):
    c = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    c.debug_journal = Journal()
    return c


@pytest.fixture
def state(module):
    st = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    st.location = "tower"
    return st


def ghoul_fight() -> Brief:
    inv = BriefPerson(id="zhouheng", name="周衡", kind="investigator", side="party", controller="human", hp=11, hp_max=11,
                      dex=60, skills={"闪避": 30, "格斗(斗殴)": 25},
                      attacks=[BriefAttack(id="revolver", name=".38 左轮", skill="射击(手枪)", value=40, damage="1D10",
                                           firearm=True, loaded=0, capacity=6, spare=0),
                               BriefAttack(id="cane", name="手杖", skill="格斗(斗殴)", value=25, damage="1D6+DB", held=False)])
    ghoul = BriefPerson(id="douglas", name="道格拉斯·金博尔", public="犬脸的佝偻身影", kind="creature", side="foe", hp=13,
                        hp_max=13, dex=65, build=1, db="+1D4", goal="护书撤退", skills={"闪避": 32},
                        attacks=[BriefAttack(id="claw", name="爪击", skill="格斗", value=40, damage="1D6+DB", natural=True,
                                             per_round=3),
                                 BriefAttack(id="rifle", name="猎枪", skill="射击(步枪)", value=30, damage="2D6",
                                             firearm=True, loaded=1, capacity=2, spare=None, jammed=True)],
                        traits=["枪伤减半"], missing=["备弹"])
    warden = BriefPerson(id="warden", name="老守钟人", side="neutral", hp=None, hp_max=None, attacks=[], conditions=[],
                         arranged="躲在钟架后（不掺和）", react="dodge")
    return Brief(id="enc1", mode="fight", round=2, place="钟楼", reason="道格拉斯被撞见", pace=6,
                 people=[inv, ghoul, warden], engaged=[("zhouheng", "douglas")], fled=["zhouheng"],
                 stakes=[BriefStake(when="escaped:douglas", flags=["douglas_escaped"], note="他带着日记跑了")],
                 lines=["{douglas} 爪击 {zhouheng}：命中，4 点伤害"])


# -- the brief --------------------------------------------------------------------------------------
def test_the_brief_shows_everything_a_valid_plan_needs():
    text = encounter_brief(ghoul_fight())
    assert "【这场遭遇里的 id】zhouheng（周衡）、douglas（犬脸的佝偻身影）、warden（老守钟人）" in text
    assert "道格拉斯·金博尔（玩家所见：犬脸的佝偻身影）" in text  # true and public names
    assert "生命 13/13" in text and "生命 11/11" in text and "生命 ?/?" in text  # exact HP, unknown said so
    assert "claw＝爪击（格斗 40，1D6+DB，每轮 3 次，天生的）" in text
    assert "rifle＝猎枪" in text and "弹 1/2" in text and "备弹 未知" in text and "卡壳" in text
    assert "cane＝手杖" in text and "收着，要先 ready" in text
    assert "现在做不到：shoot（rifle卡壳了）" in text  # what each action needs, in the brief itself
    assert "unjam（rifle）" in text and "【需要安排】" in text and "目标：护书撤退" in text
    assert "缺少的数值（在 fills 里定下）：备弹" in text and "特性：枪伤减半" in text
    assert "近身缠斗：zhouheng 与 douglas" in text
    assert "【有人逃跑】zhouheng：douglas 每人都要在 pursue 或 let_go 里写明追不追" in text
    assert "escaped:douglas → douglas_escaped（他带着日记跑了）" in text
    assert "{douglas} 爪击 {zhouheng}：命中，4 点伤害" in text and "一轮 6 秒" in text
    # A human's actions are theirs to say: no 现在能做 line for the investigator.
    inv_block = text.split("`zhouheng`")[1].split("`douglas`")[0]
    assert "现在能做" not in inv_block


def test_default_actions_follow_ammo_jams_and_what_is_held():
    b = ghoul_fight()
    me = b.people[0].model_copy(update={"controller": "ai"})
    acts = {a.do: a for a in default_actions(me, b)}
    assert not acts["shoot"].ok and "没子弹" in acts["shoot"].why
    assert not acts["reload"].ok and acts["reload"].why == "没有备弹"
    assert acts["ready"].why == "cane" and acts["attack"].ok and "徒手" in acts["attack"].why
    assert "pursue" in acts  # someone fled
    ghoul = {a.do: a for a in default_actions(b.people[1], b)}
    assert ghoul["attack"].why == "用：claw" and "speak" in ghoul  # a creature has no fists, only its claws
    held = b.model_copy(update={"holds": ["douglas 制住了 zhouheng"]})
    assert not {a.do: a for a in default_actions(me, held)}["flee"].ok
    # The engine's own preconditions, when it passes them, are what the brief shows.
    mine = b.people[1].model_copy(update={"actions": [BriefAction(do="attack", ok=False, why="不在近身处")]})
    text = encounter_brief(b.model_copy(update={"people": [mine]}))
    assert "现在做不到：attack（不在近身处）" in text and "unjam" not in text


def test_a_chase_brief_draws_its_site_or_asks_for_one():
    b = Brief(mode="chase", round=1, people=[BriefPerson(id="douglas", name="道格拉斯", node="gate")],
              nodes=[SiteNode(id="gate", name="铁门"), SiteNode(id="lane", name="窄道", exit=True, blocks_sight=True)],
              edges=[SiteEdge(a="gate", b="lane")], quarry=["douglas"], pursuers=["zhouheng"])
    text = encounter_brief(b)
    assert "gate＝铁门" in text and "lane＝窄道（出口，到这就脱身）（挡视线）" in text and "gate—lane" in text
    assert "逃的：douglas；追的：zhouheng" in text and "pace 必填" in text
    assert "move" in text and "hide" in text
    assert "画一条" in encounter_brief(b.model_copy(update={"site_missing": True}))


def test_the_system_prompt_states_the_books_defaults_and_every_action():
    assert "默认反击" in ENCOUNTER_PLAN_SYSTEM and "打算撤离时才闪避" in ENCOUNTER_PLAN_SYSTEM  # p. 87, in the code's comment
    assert all(f"- {do}：" in ENCOUNTER_PLAN_SYSTEM for do in ACTION_NEEDS)


# -- the plan inside an encounter ----------------------------------------------------------------
ACT = {"action": {"do": "attack", "attacks": [{"target": "douglas"}], "summary": "周衡挥拳打向犬脸的身影"},
       "npcs": [{"who": "douglas", "goal": "护书撤退", "actions": [{"do": "flee", "to": "away", "summary": "道格拉斯夺路而逃"}],
                 "react": "dodge", "let_go": ["zhouheng"]}]}


async def test_an_act_plan_reads_the_brief_and_comes_back_checked(cast, state):
    checked: list[EncounterTurnPlan] = []

    def check(plan):
        checked.append(plan)
        return []

    SCRIPT.append(ACT)
    out = await cast.plan(state, "我一拳打过去", encounter=encounter_brief(ghoul_fight()), check=check)
    assert out.ok and isinstance(out.value, EncounterTurnPlan) and out.value.action.attacks[0].target == "douglas"
    assert checked == [out.value] and len(SEEN) == 1
    sent = SEEN[-1]
    assert sent["system"].startswith(ENCOUNTER_PLAN_SYSTEM) and "【守秘人的底牌（只给你看）】" in sent["system"]
    prompt = sent["messages"][-1]["content"]
    assert "【遭遇 enc1】" in prompt and "【玩家】周衡：我一拳打过去" in prompt and "填 action" in prompt
    assert set(sent["schema"]["properties"]) >= {"action", "npcs", "join", "fills", "stakes", "end", "pace"}
    assert state.usage["plan"]["calls"] == 1 and not state.transcripts.get("plan:encounter")


async def test_problems_go_back_once_with_the_answer_and_a_fixed_plan_lands(cast, state):
    answers = iter([["douglas 没有 rifle 以外的枪：bite 不在他的攻击里"], []])
    SCRIPT.extend([ACT, ACT])
    out = await cast.plan(state, "我一拳打过去", encounter="【遭遇 enc1】", check=lambda _p: next(answers))
    assert out.ok and len(SEEN) == 2
    again = SEEN[-1]["messages"][-1]["content"]
    assert "【上一份回答的问题】\n- douglas 没有 rifle 以外的枪：bite 不在他的攻击里" in again
    assert "【上一份回答】" in again and "周衡挥拳打向犬脸的身影" in again
    assert cast.debug_journal.rows == []


async def test_still_invalid_after_the_resend_comes_back_with_its_problems_and_goes_to_the_journal(cast, state):
    SCRIPT.extend([ACT, ACT])
    out = await cast.plan(state, "我一拳打过去", encounter="【遭遇 enc1】", check=lambda _p: ["缺少这些人的安排：warden"])
    assert not out.ok and out.value is not None and out.problems == ["缺少这些人的安排：warden"]
    assert out.raw == ACT and len(SEEN) == 2  # one resend, no third call, nothing put in its place
    kind, row = cast.debug_journal.rows[-1]
    assert kind == "plan" and row["phase"] == "invalid" and row["answer"] == ACT and row["problems"] == out.problems


async def test_an_answer_not_in_the_schemas_shape_is_told_what_is_wrong(cast, state):
    bad = {"action": {"do": "attack"}}  # no summary: the harness repairs once, then the plan resends once
    SCRIPT.extend([bad, bad, ACT])
    out = await cast.plan(state, "我一拳打过去", encounter="【遭遇 enc1】")
    assert out.ok and "格式不对：action.summary" in SEEN[-1]["messages"][-1]["content"]


async def test_arrange_react_and_pursuit_have_their_own_small_schemas(cast, state):
    SCRIPT.append({"npcs": ACT["npcs"], "pace": 6})
    out = await cast.plan(state, "", encounter="【遭遇 enc1】", mode="arrange")
    assert isinstance(out.value, ArrangePlan) and out.value.action is None
    assert "action" not in SEEN[-1]["schema"]["properties"] and "【玩家】" not in SEEN[-1]["messages"][-1]["content"]
    SCRIPT.append({"reaction": "dodge", "summary": "周衡侧身闪避"})
    out = await cast.plan(state, "我往旁边一闪", encounter="【遭遇 enc1】", mode="react", ask="【可选的应对】dodge、fight_back")
    assert isinstance(out.value, ReactionAnswer) and out.value.reaction == "dodge"
    assert "【可选的应对】dodge、fight_back" in SEEN[-1]["messages"][-1]["content"]
    SCRIPT.append({"decisions": [{"who": "douglas", "let_go": ["zhouheng"], "why": "只想带着书走"}]})
    out = await cast.plan(state, "", encounter="【遭遇 enc1】", mode="pursuit", ask="要定的：douglas")
    assert isinstance(out.value, PursuitAnswer) and out.value.decisions[0].let_go == ["zhouheng"]


async def test_an_encounter_plan_may_look_something_up_first(cast, state):
    SCRIPT.extend([{**ACT, "lookup": "道格拉斯怕不怕火"}, ACT])
    out = await cast.plan(state, "我一拳打过去", encounter="【遭遇 enc1】")
    assert out.ok and out.value.lookup == "" and len(SEEN) == 2
    assert "【查询结果】（道格拉斯怕不怕火）" in SEEN[-1]["messages"][-1]["content"]
    assert ("lookup", {"query": "道格拉斯怕不怕火", "found": "没有查到相关的记录，按手头的资料安排。"}) in cast.debug_journal.rows


# -- the start of an encounter --------------------------------------------------------------------
async def test_plan_encounter_starts_one_from_the_hint_with_who_may_be_in_it(cast, state):
    start = {"mode": "fight", "foes": ["warden"], "npcs": [{"who": "warden", "goal": "守住钟楼",
             "actions": [{"do": "attack", "attacks": [{"target": "zhouheng"}], "summary": "守钟人抡起扳手"}],
             "react": "fight_back"}], "pace": 5, "reason": "周衡要砸齿轮"}
    SCRIPT.append(start)
    hint = EncounterHint.model_validate({"mode": "fight", "with": ["warden"], "trigger": "on_fail"})
    out = await cast.plan_encounter(state, "我砸了齿轮", hint, check=lambda s: [] if s.pace else ["pace 必填"])
    assert out.ok and isinstance(out.value, EncounterStart) and out.value.foes == ["warden"]
    prompt = SEEN[-1]["messages"][-1]["content"]
    assert "【要开始的遭遇】打起来，和 warden，检定失败后开始" in prompt and "`warden` " in prompt
    assert "`zhouheng` 周衡｜调查员" in prompt and "【玩家】我砸了齿轮" in prompt
    assert "默认反击" in SEEN[-1]["system"]
    assert set(SEEN[-1]["schema"]["properties"]) >= {"mode", "foes", "npcs", "stakes", "site", "pace", "reason"}
    SCRIPT.append(start)
    await cast.plan_encounter(state, "我砸了齿轮", hint, brief="（引擎给的名单）")
    assert "【可能卷入的人】\n（引擎给的名单）" in SEEN[-1]["messages"][-1]["content"]


# -- AI investigators -----------------------------------------------------------------------------
async def test_an_ai_investigator_picks_one_offered_option_by_id(cast, state):
    options = [("hit", "打向犬脸的身影"), ("run", "往楼梯跑")]
    SCRIPT.append({"choice": "run"})
    out = await cast.encounter_choice(state, "zhouheng", "【眼前】犬脸的身影扑了过来", options)
    assert out.ok and out.value.choice == "run" and out.value.react == ""
    sent = SEEN[-1]
    assert sent["schema"]["properties"]["choice"]["enum"] == ["hit", "run"]
    assert "react" not in sent["schema"]["properties"]  # a chase: no answer to blows to set
    assert "【可选】\n- hit：打向犬脸的身影\n- run：往楼梯跑" in sent["messages"][-1]["content"]
    assert "周衡" in sent["system"] and "守秘人的底牌" not in sent["system"]  # the public view only
    assert state.usage["choice"]["calls"] == 1


async def test_in_a_fight_an_ai_investigator_sets_its_answer_to_blows_in_the_same_answer(cast, state):
    """The review: with the answer to blows out of the option words, AI companions only ever dodged. In
    a fight the same answer may set it, one of those it can set; one it cannot goes back once."""
    options = [("hit", "攻击 犬脸的身影（徒手，1D3）"), ("guard", "防守")]
    SCRIPT.append({"choice": "hit", "react": "cover"})
    SCRIPT.append({"choice": "hit", "react": "fight_back"})
    out = await cast.encounter_choice(state, "zhouheng", "第 1 轮战斗。", options, ["dodge", "fight_back"])
    assert out.ok and (out.value.choice, out.value.react) == ("hit", "fight_back")
    first, second = SEEN[-2], SEEN[-1]
    assert first["schema"]["properties"]["react"]["enum"] == ["", "dodge", "fight_back"]
    assert "【被攻击时的应对（react）】dodge（闪避）、fight_back（反击）" in first["messages"][-1]["content"]
    assert "react" in second["messages"][-1]["content"] and "cover" in second["messages"][-1]["content"]  # sent back once


# -- the arbiter in an encounter ------------------------------------------------------------------
async def test_the_arbiter_rules_an_other_action_with_effects_by_the_books_table(cast, state):
    SCRIPT.append({"needs_roll": True, "skill": "投掷", "effects": [
        {"kind": "damage", "target": "douglas", "dice": "1d6", "what": "油灯砸中，火苗窜起"},
        {"kind": "condition", "target": "douglas", "what": "着火"}]})
    action = Action(kind="other", summary="周衡把油灯砸向犬脸的身影")
    ruling = await cast.arbiter(state, action, encounter="【遭遇 enc1】", problems=["target 不在这场遭遇里：ghoul"])
    assert ruling.effects[0].dice == "1D6" and ruling.effects[1].what == "着火"
    sent = SEEN[-1]
    assert "血肉横飞 8D10" in sent["system"] and "窒息和溺水" in sent["system"]  # Table III, every ruling
    prompt = sent["messages"][-1]["content"]
    assert ARBITER_ENCOUNTER.splitlines()[0] in prompt and "【遭遇 enc1】" in prompt
    assert "【上一次裁定的问题，请改正】\n- target 不在这场遭遇里：ghoul" in prompt
    assert "lookup" not in sent["schema"]["properties"] and "【可能动手的】" not in prompt
    assert not {"combat", "surprise", "chase", "chase_party", "harm", "harm_to"} & set(sent["schema"]["properties"])


def test_a_ruling_with_bad_dice_fails_before_the_engine_sees_it():
    with pytest.raises(ValueError):
        Ruling.model_validate({"needs_roll": False, "effects": [{"kind": "damage", "target": "x", "dice": "1D6点伤害"}]})
    pattern = Ruling.model_json_schema()["$defs"]["RuledEffect"]["properties"]["dice"]
    assert "pattern" in pattern or "pattern" in str(pattern)
