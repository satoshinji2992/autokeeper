"""The keeper arranges each turn (Cast.plan): the player's action, who of those here joins in, and
what it looks up first. Scripted models and FakeCast, no model calls."""

from __future__ import annotations

import json

import pytest

from autokeeper import config, paths
from autokeeper.agents.cast import PLAN_SYSTEM, Action, Cast, Initiative, Plan, Ruling
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import ops as O
from autokeeper.world import scheduler
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step

from .test_engine import FakeCast
from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401

LOOK = Action(kind="investigate", summary="周衡查看齿轮", target="齿轮间")


class PlanCast(FakeCast):
    """A FakeCast whose keeper hands back a plan (or fails to)."""

    def __init__(self, script, plans):
        super().__init__(script)
        self.plans = plans  # text -> Plan, or an exception to raise
        self.planned: list[str] = []

    async def plan(self, state, text):
        self.planned.append(text)
        out = self.plans[text]
        if isinstance(out, Exception):
            raise out
        return out


def make(module, fake, cast, location="tower"):
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = location
    return Game(module, state, cast, Rules(dice=fake))  # type: ignore[arg-type]


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


async def test_the_keepers_plan_is_the_turns_action_and_keeps_only_those_here_who_may_join_in(module, fake):
    plan = Plan(action=LOOK, initiative=[Initiative(npc="preacher", does="在广场上大声布道"),  # not here
                                         Initiative(npc="warden", does="伸手拦住他，不让他碰齿轮"),
                                         Initiative(npc="warden", does="又说一遍"),  # twice
                                         Initiative(npc="nobody", does="路过")])  # not the module's
    cast = PlanCast({"我看看齿轮": (Action(kind="other", summary="不该用到"), Ruling(needs_roll=False))},
                    {"我看看齿轮": plan})
    game = make(module, fake, cast)
    action = await game.parse("我看看齿轮")
    assert action.summary == "周衡查看齿轮" and cast.planned == ["我看看齿轮"] and cast.heard == []
    assert [(x.npc, x.does) for x in game.initiative] == [("warden", "伸手拦住他，不让他碰齿轮")]
    await game.close()


async def test_a_failed_plan_or_a_cast_without_one_falls_back_to_the_parser(module, fake):
    script = {"我看看齿轮": (LOOK, Ruling(needs_roll=False))}
    cast = PlanCast(script, {"我看看齿轮": RuntimeError("model down")})
    game = make(module, fake, cast)
    assert (await game.parse("我看看齿轮")).summary == "周衡查看齿轮"
    assert cast.planned == ["我看看齿轮"] and cast.heard == ["我看看齿轮"] and game.initiative == []
    await game.close()

    def broken(*_):  # a reply that is no Plan: Plan.model_validate raises
        return Plan.model_validate({"initiative": []})

    cast = PlanCast(script, {})
    cast.plan = broken  # type: ignore[method-assign]
    game = make(module, fake, cast)
    assert (await game.parse("我看看齿轮")).summary == "周衡查看齿轮" and cast.heard == ["我看看齿轮"]
    await game.close()
    plain = FakeCast(script)  # no plan at all
    game = make(module, fake, plain)
    assert (await game.parse("我看看齿轮")).summary == "周衡查看齿轮" and plain.heard == ["我看看齿轮"]
    await game.close()


async def test_the_plan_runs_on_the_keepers_model_in_its_own_session_with_the_book_and_the_backstage(module, fake):
    cfg = config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES})
    cast = Cast(cfg, module)
    game = make(module, fake, cast)
    game.state.add("npc", "钟有钟的规矩。", who="老守钟人")
    SCRIPT.append({"action": {"kind": "investigate", "summary": "周衡查看齿轮", "target": "齿轮间"},
                   "initiative": [{"npc": "warden", "does": "咳嗽一声，提醒他别乱碰"}]})
    action = await game.parse("我看看齿轮")
    sent = SEEN[-1]
    prompt = sent["messages"][-1]["content"]
    assert sent["system"].startswith(PLAN_SYSTEM) and "【守秘人的底牌（只给你看）】" in sent["system"]
    assert "summary 只用玩家说过的叫法和【场景】里给出的名字" in sent["system"]
    assert "【幕后（只给你看）】" in prompt and "【在场、可以主动的人】" in prompt and "(warden)" in prompt
    assert "【玩家】我看看齿轮" in prompt and "钟有钟的规矩" in prompt
    assert set(sent["schema"]["properties"]) >= {"action", "initiative"}
    assert action.summary == "周衡查看齿轮" and [x.npc for x in game.initiative] == ["warden"]
    assert "plan" in cast.sessions and "keeper" not in cast.sessions and "intent" not in cast.sessions
    assert game.state.usage["plan"]["calls"] == 1 and "intent" not in game.state.usage
    assert not game.state.transcripts.get("plan")  # stateless: the narration's memory is not touched either
    await game.close()
    await cast.close()


async def test_a_blind_keeper_plans_without_its_book(module, fake):
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    cast.backstage = False
    game = make(module, fake, cast)
    SCRIPT.append({"action": {"kind": "investigate", "summary": "周衡查看齿轮"}})
    await game.parse("我看看齿轮")
    assert "守秘人的底牌" not in SEEN[-1]["system"] and "【幕后（只给你看）】" not in SEEN[-1]["messages"][-1]["content"]
    await game.close()
    await cast.close()


# -- who joins in of their own accord (Game._join_in) -------------------------------------------------
def joining(module, fake, initiative, action=LOOK, ruling=None):
    ruling = ruling or Ruling(needs_roll=False, minutes=1, reason="")
    cast = PlanCast({"我看看齿轮": (action, ruling)}, {"我看看齿轮": Plan(action=action, initiative=initiative)})
    return make(module, fake, cast), cast


async def test_one_the_keeper_has_join_in_speaks_in_its_own_voice_and_the_turn_is_still_told(module, fake):
    game, cast = joining(module, fake, [Initiative(npc="warden", does="咳嗽一声，提醒他别乱碰齿轮")])
    events = []

    async def emit(ev):
        events.append(ev)

    game.emit = emit
    action = await game.parse("我看看齿轮")
    turn = await game.resolve(action, await game.rule(action))
    await game.talk(turn)
    [(npc, message)] = cast.npc_calls
    assert npc == "warden" and "这一轮你主动：咳嗽一声，提醒他别乱碰齿轮" in message and "不是你知道的事" in message
    assert turn.npc_lines == [("warden", "warden 的回答")] and turn.joined == ["warden"]
    assert any(e["type"] == "npc" and e["npc"] == "warden" for e in events)
    assert game.state.log[-1].kind == "npc" and game.state.npc("warden").met
    talk = next((e for e in game.world.rec.events if e.op == "talk:1:warden:initiative"), None)
    assert talk is not None and talk.visibility == "public" and "warden 的回答" in talk.text
    told = await game.narrate(turn)  # what the player did is still told, the line is not repeated
    assert told and "其中老守钟人是这一轮自己主动开口的" in cast.reports[-1] and "【对话已展示】" in cast.reports[-1]
    await game.close()


async def test_the_one_addressed_is_not_asked_twice_and_one_gone_is_not_asked(module, fake):
    talk = Action(kind="talk", summary="周衡问守钟人钟的事", npc="warden", speech="这钟怎么总多敲一下？")
    game, cast = joining(module, fake, [Initiative(npc="warden", does="主动说起钟声")], action=talk)
    action = await game.parse("我看看齿轮")
    turn = await game.resolve(action, await game.rule(action))
    await game.talk(turn)
    assert [n for n, _ in cast.npc_calls] == ["warden"] and turn.joined == []  # answered once, as the one addressed
    await game.close()
    game, cast = joining(module, fake, [Initiative(npc="warden", does="拦住他")])
    action = await game.parse("我看看齿轮")
    game.world.apply(O.Move(id="away", actor="director", entity="warden", to="square"))  # gone before the talk
    turn = await game.resolve(action, await game.rule(action))
    await game.talk(turn)
    assert cast.npc_calls == [] and turn.joined == []
    await game.close()


async def test_a_resumed_turn_takes_who_joins_in_from_its_journal(module, fake):
    game, cast = joining(module, fake, [])
    game.state.pending_turn = {"input": "我看看齿轮"}
    await game.parse("我看看齿轮")
    assert game.state.pending_turn["initiative"] == []
    game.state.pending_turn["initiative"] = [{"npc": "warden", "does": "拦住他"}]  # as the journal kept it
    game.initiative = []  # a new process
    turn = await game.resolve(LOOK, Ruling(needs_roll=False, minutes=1, reason=""))
    await game.talk(turn)
    assert [n for n, _ in cast.npc_calls] == ["warden"]
    await game.close()


async def test_one_who_joined_in_is_not_woken_backstage_in_the_same_turn(module, fake):
    module.npc("warden").important = True
    game, cast = joining(module, fake, [Initiative(npc="warden", does="拦住他")])
    decided: list[list[str]] = []

    async def decide(ask):
        decided.append(list(ask.npcs))
        return Scene(decisions=[Decision(npc=n, wait=600) for n in ask.npcs])

    game.brain, game.minds = Canned(decide), scheduler.Budget()  # type: ignore[assignment]
    w = game.world
    w.agent("warden").wake = w.now  # he means to think now
    action = await game.parse("我看看齿轮")
    turn = await game.resolve(action, await game.rule(action))
    await game.talk(turn)  # the world lives after the exchange (world_later off)
    assert turn.joined == ["warden"] and decided == [] and game._directed == {"warden"}
    cast.plans["我看看齿轮"] = Plan(action=LOOK)
    await game.parse("我看看齿轮")  # next turn, nobody joins in: he is the world's again
    assert game._directed == set()
    turn = await game.resolve(action, await game.rule(action))
    await game.talk(turn)
    assert decided == [["warden"]]
    await game.close()


async def test_the_scheduler_holds_those_named_wakes_and_steps_alike(module, fake):
    module.npc("warden").important = True
    game = make(module, fake, PlanCast({}, {}), location="square")  # away from the party, his step lands offscreen
    w = game.world
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="go", text="下楼", due=w.now,
                                                ops=[{"kind": "move", "entity": "warden", "to": "office"}])]))
    w.agent("warden").wake = w.now
    decided = []

    async def decide(ask):
        decided.append(list(ask.npcs))
        return Scene(decisions=[Decision(npc=n, wait=600) for n in ask.npcs])

    start = w.now
    report = await scheduler.advance(w, Canned(decide), start, start + 5, tick="held", held={"warden"})
    # The review of 2026-10-20: held means no model asked for him, not his appointment missed.
    assert decided == [] and not report.woke and w.entity("warden").at == "office"
    assert w.agent("warden").plan[0].status == "done"
    report = await scheduler.advance(w, Canned(decide), start + 5, start + 10, tick="free")
    assert decided == [["warden"]]  # the next turn he is the world's again: it asks him what next
    await game.close()


async def test_one_who_steps_in_is_played_before_the_ruling(module, fake):
    """The review of 2026-10-20: the keeper had the warden stop the investigator before he touched
    the gears, and the touch was settled first. Stepping in (Initiative.first) is played before the
    arbiter rules, so the ruling and the roll go on it; he is not asked again after."""
    game, cast = joining(module, fake, [Initiative(npc="warden", does="喝住他：别碰齿轮", first=True)])
    order: list[str] = []
    npc, arbiter = cast.npc, cast.arbiter

    async def speaking(state, npc_id, message, **kw):
        order.append(f"npc:{npc_id}")
        return await npc(state, npc_id, message)

    async def ruling(state, action, at=None):
        order.append("arbiter")
        assert any(e.kind == "npc" for e in state.log)  # what he said is in what the arbiter reads
        return await arbiter(state, action, at)

    cast.npc, cast.arbiter = speaking, ruling  # type: ignore[method-assign]
    game.state.pending_turn = {"input": "我看看齿轮"}
    action = await game.parse("我看看齿轮")
    await game.prepare(action)
    turn = await game.resolve(action, await game.rule(action))
    await game.talk(turn)
    assert order == ["npc:warden", "arbiter"]
    assert "正要：" in cast.npc_calls[0][1] and "抢在前面主动：喝住他：别碰齿轮" in cast.npc_calls[0][1]
    assert turn.npc_lines == [("warden", "warden 的回答")] and turn.joined == ["warden"]
    await game.close()


# -- what the keeper looks up first (Plan.lookup) -----------------------------------------------------
def scripted(module, fake):
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    return make(module, fake, cast), cast


async def test_a_lookup_is_answered_from_the_record_and_the_keeper_is_asked_again(module, fake):
    game, cast = scripted(module, fake)
    events = []

    async def emit(ev):
        events.append(ev)

    game.emit = emit
    draft = {"kind": "investigate", "summary": "周衡查看齿轮"}
    SCRIPT.append({"lookup": "老守钟人知道齿轮间的什么事", "action": draft})
    SCRIPT.append({"lookup": "", "action": {**draft, "target": "齿轮间"},
                   "initiative": [{"npc": "warden", "does": "提醒他齿轮会咬手"}]})
    action = await game.parse("我看看齿轮")
    asked = [c for c in SEEN if c["system"].startswith(PLAN_SYSTEM)]
    assert len(asked) == 2 and not SCRIPT
    again = asked[1]["messages"][-1]["content"]
    assert "【查询结果】（老守钟人知道齿轮间的什么事）" in again and "现在给出安排，lookup 留空" in again
    assert "【老守钟人】此刻在钟楼" in again and "老守钟人自己的资料" in again
    assert asked[0]["messages"][-1]["content"] in again  # the same turn, asked again with the answer
    assert action.target == "齿轮间" and [x.npc for x in game.initiative] == ["warden"]
    assert game.state.usage["plan"]["calls"] == 2
    shown = "".join(e.text for e in game.state.log) + json.dumps(events, ensure_ascii=False)
    assert "查询结果" not in shown and "老守钟人自己的资料" not in shown  # the players never see it
    await game.close()
    await cast.close()


async def test_two_lookups_at_most_and_the_last_ones_is_not_answered(module, fake):
    game, cast = scripted(module, fake)
    for i in range(3):
        SCRIPT.append({"lookup": f"第{i + 1}次想查的钟楼旧事", "action": {"kind": "investigate", "summary": f"周衡查看齿轮{i}"}})
    action = await game.parse("我看看齿轮")
    asked = [c for c in SEEN if c["system"].startswith(PLAN_SYSTEM)]
    assert len(asked) == 3 and not SCRIPT and action.summary == "周衡查看齿轮2"
    last = asked[2]["messages"][-1]["content"]
    assert "【查询结果】（第1次想查的钟楼旧事）" in last and "【查询结果】（第2次想查的钟楼旧事）" in last
    assert "第3次" not in last
    await game.close()
    await cast.close()


async def test_a_lookup_finds_the_records_matching_lines_with_rewrites_in(module, fake):
    game, cast = scripted(module, fake)
    w = game.world
    w.apply(O.AddFact(id="ledger", actor="director", fact="ledger", text="钟楼的账本记着每一次多敲的钟声", fact_kind="setting"))
    found = await cast.look_up(game.state, "钟楼账本记着什么")
    assert "【记录里相关的】" in found and "钟楼的账本记着每一次多敲的钟声" in found
    assert (await cast.look_up(game.state, "zzz")) == "没有查到相关的记录，按手头的资料安排。"
    await game.close()
    await cast.close()


async def test_who_joins_in_belongs_to_the_turn_that_planned_it(module, fake):
    game, cast = joining(module, fake, [Initiative(npc="warden", does="拦住他")])
    action = await game.parse("我看看齿轮")
    await game.parse("/kp 规则上能不能这样？")  # a question to the keeper: nobody arranged for it
    assert game.initiative == [] and game._directed == set()
    turn = await game.resolve(action, await game.rule(action))
    await game.talk(turn)
    assert cast.npc_calls == []
    await game.close()
    game, cast = joining(module, fake, [Initiative(npc="warden", does="拦住他")])
    action = await game.parse("我看看齿轮")
    for _ in range(2):  # played once; a later turn with no plan of its own does not play it again
        turn = await game.resolve(action, Ruling(needs_roll=False, minutes=1, reason=""))
        await game.talk(turn)
    assert [n for n, _ in cast.npc_calls] == ["warden"]
    await game.close()


async def test_the_rhetoric_eval_goes_through_the_keepers_plan(tmp_path, monkeypatch):
    """The review of 2026-10-20: eval rhetoric still measured the old parser. It rules on what the
    keeper's plan makes of the words, as play does."""
    from autokeeper.eval import rhetoric

    planned: list[str] = []

    async def plan(self, state, text):
        planned.append(text)
        return Plan(action=Action(kind="investigate", summary="周衡翻看旧报", target="旧报"))

    async def arbiter(self, state, action, at=None):
        return Ruling(needs_roll=True, skill="图书馆使用", reason="")

    monkeypatch.setattr(rhetoric.Cast, "plan", plan)
    monkeypatch.setattr(rhetoric.Cast, "arbiter", arbiter)
    summary = await rhetoric.run(paths.MODULES / "zhuishuren", tmp_path / "out.json", limit=1, adversarial=False,
                                 referees=("claude-haiku-5-5",), systems=("split",))
    assert len(planned) == 2 and summary["cases"] == 1  # the plain words and one rhetorical, both planned


# -- an encounter announced by the everyday plan (Plan.encounter) -----------------------------------
async def test_the_plan_may_announce_an_encounter_and_the_arbiter_still_starts_the_old_one(module, fake):
    """Plan.encounter is one small object on the everyday schema, for the encounter engine (WP-C);
    until then the arbiter's Ruling.combat/chase start the old engine's fights, as before."""
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    game = make(module, fake, cast)
    SCRIPT.append({"action": {"kind": "attack", "summary": "周衡扑向守钟人", "npc": "warden"},
                   "encounter": {"mode": "fight", "with": ["warden"], "trigger": "now"}})
    action = await game.parse("我扑向守钟人")
    sent = SEEN[-1]
    hint = sent["schema"]["$defs"]["EncounterHint"]
    assert set(hint["properties"]) == {"mode", "with", "trigger"} and "encounter" in sent["schema"]["properties"]
    assert "【可能动手的】" in sent["messages"][-1]["content"] and "encounter：这一轮真的打起来" in sent["system"]
    SCRIPT.append({"needs_roll": True, "skill": "格斗(斗殴)", "combat": ["warden"], "surprise": "foe"})
    ruling = await cast.arbiter(game.state, action)
    assert ruling.combat == ["warden"] and ruling.surprise == "foe"
    sent = SEEN[-1]
    assert {"combat", "surprise", "chase", "chase_party", "harm", "harm_to"} <= set(sent["schema"]["properties"])
    assert "【可能动手的】" in sent["messages"][-1]["content"] and "chase_party 写调查员是逃还是追" in sent["system"]
    await game.close()
    await cast.close()


def test_an_old_configuration_naming_the_tactics_role_loads_and_ignores_it(tmp_path):
    (paths.USER_DIR).mkdir(parents=True, exist_ok=True)
    (paths.USER_DIR / "config.toml").write_text('[roles.tactics]\nmodel = "deepseek:deepseek-flash"\n', encoding="utf-8")
    cfg = config.load()
    assert "tactics" not in cfg.roles and "tactics" not in cfg.role_names and "tactics" not in config.ROLES_SHOWN
    assert cfg.role("plan").model == cfg.role("keeper").model
