"""The world record in play (engine/game.py): a game keeps one, its changes settle there, and
while time passes the important NPCs away from the party go on with their lives."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Turn
from autokeeper.engine.state import WorldChange
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops as O
from autokeeper.world import scheduler
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step

from .test_engine import make


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


TALK = Action(kind="talk", summary="周衡问守钟人钟的事", npc="warden", speech="这钟怎么总多敲一下？")
WAIT = Action(kind="wait", summary="周衡在广场上等着")


def ruled(minutes: int = 0, changes: list[WorldChange] | None = None) -> Ruling:
    return Ruling(needs_roll=False, minutes=minutes, reason="", changes=changes or [], rest=minutes > 60)


async def test_a_game_keeps_a_world_record_and_enlists_the_npcs_its_module_marks(module, fake):
    module.npc("warden").important = True
    game, cast, _ = make(module, fake, {})
    rec = game.world.rec
    assert "warden" in rec.agents and "preacher" not in rec.agents
    assert rec.simulated == game.state.minutes and cast.world is game.world
    assert {"square", "tower", "warden", "zhouheng"} <= set(rec.entities)


async def test_a_conversation_and_what_the_ruling_settled_go_into_the_record(module, fake):
    module.npc("warden").important = True
    game, _, _ = make(module, fake, {}, location="tower")
    tea = WorldChange(kind="promise", key="tea", text="周衡答应晚上给守钟人带一壶热茶", npc="warden")
    turn = await game.resolve(TALK, ruled(changes=[tea]))
    await game.talk(turn)
    rec = game.world.rec
    assert rec.promises["tea"].status == "active" and game.state.world["tea"].kind == "promise"  # the old ledger too
    talk = next(e for e in rec.events if e.op.startswith("talk:"))
    assert talk.visibility == "public" and "这钟怎么总多敲一下" in talk.text
    assert any("这钟怎么总多敲一下" in m.text for m in game.world.agent("warden").memory)
    # A retried turn settles nothing twice.
    seq = rec.seq
    game._exchanged("warden", TALK, "warden 的回答")
    assert rec.seq == seq


async def test_npcs_away_from_the_party_go_on_while_time_passes_and_the_keeper_tells_what_it_sees(module, fake):
    module.npc("warden").important = True
    game, cast, _ = make(module, fake, {}, location="square")
    w = game.world
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="down", text="下楼去广场看看", due=w.now + 30,
                                                ops=[{"kind": "move", "entity": "warden", "to": "square"}])]))  # fmt: skip
    turn = await game.resolve(WAIT, ruled(minutes=90))
    assert w.entity("warden").at == "square" and {n.id for n in module.npcs_at("square")} == {"warden", "preacher"}
    assert turn.world == ["老守钟人来到广场"]
    await game.narrate(turn)
    assert "眼前发生：老守钟人来到广场" in cast.reports[-1]
    assert w.rec.simulated == game.state.minutes
    again = await game.resolve(WAIT, ruled(minutes=0))  # no time passed: nothing simulated again
    assert again.world == []


async def test_an_npc_acts_on_a_conversation_once_the_party_has_moved_on(module, fake):
    module.npc("warden").important = True
    game, _, _ = make(module, fake, {}, location="tower")
    w = game.world
    w.apply(O.Enlist(npc="warden", places=["office"]))  # it knows the way to the newspaper office

    def think(ask):
        assert "这钟怎么总多敲一下" in ask.views["warden"]  # its own memory of the talk
        assert "这钟怎么总多敲一下" in ask.reasons["warden"]
        return Scene(decisions=[Decision(npc="warden", notice="记者在打听钟", judge="得把东西挪走",
                                         ops=[O.Move(entity="warden", to="office", trace="齿轮间的柜门开着，里面空了")])])  # fmt: skip

    game.brain, game.minds = Canned(think), scheduler.Budget(turn=2, skip=4)  # type: ignore[assignment]
    turn = await game.resolve(TALK, ruled())
    await game.talk(turn)
    assert game.brain.asks == [] and w.entity("warden").at == "tower"  # its voice answered; nothing backstage yet
    leave = Action(kind="move", summary="周衡下楼回广场", destination="square")
    went = await game.resolve(leave, Ruling(needs_roll=False, minutes=0, reason="移动"))
    assert len(game.brain.asks) == 1 and w.entity("warden").at == "office"  # once they had gone
    assert not any("老守钟人" in t for t in went.world)  # up in the tower, away from them
    later = await game.resolve(WAIT, ruled(minutes=60))
    assert len(game.brain.asks) == 1 and w.entity("warden").at == "office" and later.world == []
    back = Action(kind="move", summary="周衡又上了钟楼", destination="tower")
    returned = await game.resolve(back, Ruling(needs_roll=False, minutes=0, reason="移动"))
    report = game._report(returned)
    assert "此刻在这里的人（以这一条为准）：没有别人" in report


async def test_a_failed_decision_is_asked_again_in_the_next_turn(module, fake):
    module.npc("warden").important = True
    game, _, _ = make(module, fake, {}, location="square")
    w = game.world
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="think", text="想想要不要下楼", due=w.now + 10)]))
    calls = []

    def down(ask):
        calls.append(ask)
        return None if len(calls) == 1 else Scene(decisions=[Decision(npc="warden", wait=600)])

    game.brain, game.minds = Canned(down), scheduler.Budget(turn=1, skip=2)  # type: ignore[assignment]
    await game.resolve(WAIT, ruled(minutes=30))
    game.state.turn += 1
    await game.resolve(WAIT, ruled(minutes=30))
    assert len(calls) == 2 and w.agent("warden").wake is not None


async def test_an_onscreen_npc_reacts_to_a_due_plan_and_the_player_sees_its_action(module, fake):
    module.npc("warden").important = True
    game, cast, _ = make(module, fake, {}, location="tower")
    w = game.world
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="lock", text="检查门锁", due=w.now + 1)]))
    game.brain = Canned(lambda ask: Scene(decisions=[Decision(npc="warden", ops=[
        O.Occur(text="守钟人主动放下工具，开始检查门锁", place="tower"),
        O.AdvancePlan(npc="warden", step="lock", status="done"),
    ])]))
    game.minds = scheduler.Budget()
    turn = await game.resolve(WAIT, ruled(minutes=1))
    assert len(game.brain.asks) == 1 and "该做了" in game.brain.asks[0].reasons["warden"]
    assert w.agent("warden").plan[0].status == "done"
    assert turn.world == ["守钟人主动放下工具，开始检查门锁"]
    await game.narrate(turn)
    assert "开始检查门锁" in cast.reports[-1]


async def test_an_npc_with_nothing_left_to_do_plans_again_when_time_jumps_not_every_turn(module, fake):
    module.npc("warden").important = True
    game, _, _ = make(module, fake, {}, location="tower")
    game.brain = Canned(lambda ask: Scene(decisions=[Decision(npc="warden", wait=600)]))
    game.minds = scheduler.Budget()
    await game.resolve(WAIT, ruled(minutes=10))
    assert game.brain.asks == []  # ten minutes in the same room: no reason to think anew
    await game.resolve(WAIT, ruled(minutes=180))  # a long wait: time has jumped
    assert len(game.brain.asks) == 1 and "还没有接下来的安排" in game.brain.asks[0].reasons["warden"]
    await game.resolve(WAIT, ruled(minutes=1))
    assert len(game.brain.asks) == 1  # no new reason: respect its chosen wait


async def test_autonomous_onscreen_actions_cannot_bypass_clue_permissions(module, fake):
    module.npc("warden").important = True
    game, _, _ = make(module, fake, {}, location="tower")
    secret = module.npc("warden").secrets[0]
    game.brain = Canned(lambda ask: Scene(decisions=[Decision(npc="warden", ops=[
        O.Occur(text=secret, place="tower"),
        O.Learn(fact="clue:c_diary", who=["players"]),
    ])]))
    game.minds = scheduler.Budget()
    turn = await game.resolve(WAIT, ruled(minutes=10))
    assert turn.world == []
    assert game.world.rec.facts["clue:c_diary"].revealed is None


async def test_an_onscreen_npc_can_share_a_clue_the_player_has_unlocked(module, fake):
    module.npc("warden").important = True
    game, _, _ = make(module, fake, {}, location="tower")
    game.state.npc("warden").unlocked.append("c_diary")
    game.world.agent("warden").wake = game.state.minutes  # it means to think now
    game.brain = Canned(lambda ask: Scene(decisions=[Decision(npc="warden", ops=[
        O.Learn(fact="clue:c_diary", who=["players"]),
    ])]))
    game.minds = scheduler.Budget()
    turn = await game.resolve(WAIT, ruled(minutes=10))
    assert any(module.clue("c_diary").text.strip() in text for text in turn.world)
    assert game.world.rec.facts["clue:c_diary"].revealed is not None


async def test_an_offscreen_public_conversation_never_reaches_the_keepers_report(module, fake):
    module.npc("preacher").important = True
    game, _, _ = make(module, fake, {}, location="tower")
    game.world.agent("preacher").wake = game.state.minutes  # it means to think now
    game.brain = Canned(lambda ask: Scene(decisions=[Decision(npc="preacher", ops=[
        O.Occur(text="布道者向站务员打听短笺", place="square", visibility="public"),
    ])]))
    game.minds = scheduler.Budget()
    turn = await game.resolve(WAIT, ruled(minutes=10))
    assert turn.world == [] and "向站务员打听" not in game._report(turn)
    assert any("向站务员打听" in e.text for e in game.world.rec.events)


async def test_a_directors_plan_lands_through_the_world_once(module, fake):
    from autokeeper.agents.director import Improv, NewNPC

    game, _, _ = make(module, fake, {})
    plan = Improv(kind="展开", reason="广场上多一个卖报的孩子", npcs=[NewNPC(id="newsboy", name="报童", role="卖报的孩子", location="square")])
    applied = await game._land("展开", plan)
    assert applied is not None and module.npc("newsboy").location == "square"
    rec = game.world.rec
    assert "newsboy" in rec.entities and len(game.state.improv) == 1
    assert any(t.kind == "question" for t in rec.threads.values())
    assert await game._land("展开", plan) is None and len(game.state.improv) == 1  # the same plan twice: the id is taken


async def test_a_name_said_at_the_table_does_not_give_away_the_secrets_about_its_bearer(module, fake):
    module.npc("warden").secrets = ["老守钟人在钟里藏了亡子的怀表"]
    game, _, _ = make(module, fake, {})
    game.state.add("keeper", "你在广场上远远看见了老守钟人，他正低头扫着台阶。")
    game.world.sync()
    assert game.world.rec.facts["npc:warden:secrets:0"].revealed is None
    game.state.add("keeper", "他终于开口：钟里藏了亡子的怀表。")
    # Ellipsis is resolved by the role's explicit disclosure, not fuzzy text overlap.
    assert game.world.apply(O.Learn(fact="npc:warden:secrets:0", who=["players"], actor="engine",
                                    visibility="public")).ok
    game.world.sync()
    assert game.world.rec.facts["npc:warden:secrets:0"].revealed is not None


async def test_an_npc_session_opens_again_when_the_record_changed_what_it_knows(module):
    from autokeeper import config
    from autokeeper.agents.cast import Cast

    cfg = config.Config()
    cast = Cast(cfg, module)
    first = await cast._fresh_npc("warden")
    assert await cast._fresh_npc("warden") is first
    module.npc("warden").knows.append("报社的人昨晚来过钟楼")
    second = await cast._fresh_npc("warden")
    assert second is not first and "报社的人昨晚来过钟楼" in second.system
    await cast.close()


async def test_the_keeper_hears_an_npcs_status_on_arriving(module, fake):
    game, _, _ = make(module, fake, {}, location="square")
    game.world.apply(O.Condition(entity="warden", status="受伤", id="hurt"))
    turn = Turn()
    await game._arrive("tower", turn)
    assert "老守钟人（受伤）" in game._report(turn)


# -- the director backstage ----------------------------------------------------------------------
class Directing:
    """A director that answers from a list of plans, and a consistency check that objects on cue."""

    improvising = True

    def __init__(self, plans, objections=()):
        from .test_engine import FakeCast

        self.base = FakeCast({})
        self.plans, self.objections, self.asked, self.checked = list(plans), list(objections), [], []

    def __getattr__(self, name):
        return getattr(self.base, name)

    async def director(self, state, kind, *, action="", reason="", at="", problems=None):
        self.asked.append(list(problems or []))
        return self.plans.pop(0) if self.plans else None

    async def consistent(self, state, material):
        self.checked.append(material)
        return self.objections.pop(0) if self.objections else []


def directed(module, fake, cast):
    from autokeeper.engine.game import Game
    from autokeeper.rules.coc import Rules

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    return Game(module, state, cast, Rules(dice=fake))  # type: ignore[arg-type]


def plan_with(*backstage):
    from autokeeper.agents.director import Improv, NewNPC

    return Improv(kind="展开", reason="广场上多一个卖报的孩子", backstage=list(backstage),
                  npcs=[NewNPC(id="newsboy", name="报童", role="卖报的孩子", location="square")])  # fmt: skip


async def test_the_director_rewrites_a_secret_and_gives_an_npc_a_plan_backstage(module, fake):
    secret = "他在钟里藏了亡子的怀表"
    cast = Directing([plan_with(
        O.Rewrite(target="fact:npc:warden:secrets:0", text=secret, goals={}),
        O.Enlist(npc="warden", goals=["让钟一直走下去"]),
        O.SetPlan(npc="warden", steps=[Step(id="dusk", text="天黑后去报社打听那个记者", when="after:1200")]),
        O.Enlist(npc="newsboy", goals=["把晚报卖完"]),  # one the plan itself brings in
    )])
    game = directed(module, fake, cast)
    applied = await game._improvise("展开", at="square")
    assert applied is not None and cast.asked == [[]]
    w = game.world
    assert w.rec.facts["npc:warden:secrets:0"].text == secret and module.npc("warden").secrets == [secret]
    assert w.agent("warden").plan[-1].id == "dusk" and "newsboy" in w.rec.agents
    assert [r.status for r in w.rec.requests.values()] == ["landed"]
    assert all(x.actor == "director" for x in w.rec.journal if x.kind in ("rewrite", "plan"))


async def test_a_backstage_op_the_world_refuses_goes_back_to_the_director_to_fix(module, fake):
    good = O.Enlist(npc="warden", goals=["守住钟楼"])
    bad = O.SetPlan(npc="preacher", steps=[Step(id="x", text="去钟楼")])  # not an important NPC
    cast = Directing([plan_with(good, bad), plan_with(good)])
    game = directed(module, fake, cast)
    assert await game._improvise("展开", at="square") is not None
    assert len(cast.asked) == 2 and any("preacher 不是重要 NPC" in p for p in cast.asked[1])
    assert "warden" in game.world.rec.agents and "preacher" not in game.world.rec.agents


async def test_a_plan_whose_backstage_still_fails_cannot_land_partially(module, fake):
    bad = O.Rewrite(target="fact:nothing_here", text="不存在的事")
    cast = Directing([plan_with(bad), plan_with(bad)])
    game = directed(module, fake, cast)
    assert await game._improvise("展开", at="square", required=True) is None
    assert len(cast.asked) == 3  # written again after two refusals; this director had no more
    assert not any(n.id == "newsboy" for n in module.npcs)
    assert not game.state.improv and not game.world.rec.requests and not game.world.rec.revisions


class Unchecked(Directing):
    """A director whose consistency check never gets an answer."""

    async def consistent(self, state, material):
        self.checked.append(material)
        return None


async def test_what_the_players_met_is_not_rewritten_when_the_check_fails(module, fake):
    told = O.Rewrite(target="fact:npc:warden:knows:1", text="守钟人从来不碰那口钟", reason="他一直在骗人")
    hidden = O.Rewrite(target="fact:npc:warden:secrets:0", text="他在钟里藏了亡子的怀表", reason="更有人情味")
    module.npc("warden").knows.append("守钟人每天黄昏给钟上一次弦")
    cast = Unchecked([plan_with(told), plan_with(hidden)])
    game = directed(module, fake, cast)
    game.state.add("npc", "我每天黄昏都去，守钟人每天黄昏给钟上一次弦。", who="布道者")
    game.world.sync()
    assert await game._improvise("展开", at="square") is not None
    assert any("没能核对" in p for p in cast.asked[1])  # the first plan went back
    assert game.world.rec.facts["npc:warden:knows:1"].text == "守钟人每天黄昏给钟上一次弦"
    assert game.world.rec.facts["npc:warden:secrets:0"].text == "他在钟里藏了亡子的怀表"  # nothing they met: no check needed


async def test_a_rewrite_that_does_not_hold_together_is_sent_back(module, fake):
    module.npc("warden").knows.append("守钟人每天黄昏给钟上一次弦")
    rewrite = O.Rewrite(target="fact:npc:warden:secrets:0", text="他早就不管这口钟了，钟是布道者在上弦")
    cast = Directing([plan_with(rewrite), plan_with()], objections=[["玩家知道守钟人每天给钟上弦"]])
    game = directed(module, fake, cast)
    game.state.add("keeper", "老守钟人说，他每天黄昏都要给钟上一次弦。")
    assert game.world.apply(O.Learn(fact="npc:warden:knows:1", who=["players"], actor="engine",
                                    visibility="public")).ok
    game.world.sync()
    assert await game._improvise("展开", at="square") is not None
    assert "每天黄昏给钟上一次弦" in cast.checked[0] and "钟是布道者在上弦" in cast.checked[0]
    assert any("说不通" in p for p in cast.asked[1])
    assert game.world.rec.facts["npc:warden:secrets:0"].text == "他在钟里藏了东西"


async def test_backstage_ops_made_on_a_world_that_has_moved_since_do_not_land(module, fake):
    module.npc("warden").important = True
    plan = plan_with(O.SetPlan(npc="warden", steps=[Step(id="a", text="去报社")]))
    cast = Directing([plan])
    game = directed(module, fake, cast)
    landed = await game._plan("展开", action=None, ruling=None, at="square")
    assert landed is not None and "agent:warden" in landed.reads
    game.world.apply(O.Enlist(npc="warden", goals=["换了个念头"]))  # the warden changed while the plan waited backstage
    assert await game._land("展开", landed, "square") is None
    assert not game.state.improv and not game.world.rec.requests and game.world.agent("warden").plan == []
    assert not any(n.id == "newsboy" for n in module.npcs)


# -- the module's situation (局面) ------------------------------------------------------------------
async def test_the_modules_situation_is_set_going_when_the_game_starts(module, fake):
    from autokeeper.agents.director import context as director_context
    from autokeeper.engine.state import parse_time
    from autokeeper.module.graph import audit
    from autokeeper.module.schema import Agenda, Bond, Foreshadow, Move, Tie

    module.relations = [Bond(a="preacher", b="warden", kind="敌对", value=-40, why="守钟人不让他上钟楼布道")]
    module.agendas = [Agenda(npc="preacher", goals=["上钟楼布道"], steps=[Move(id="climb", text="半夜爬上钟楼", when="D1 23:30"),
                                                                       Move(id="tell", text="把看见的讲给镇上的人", when="flag:bell_stopped")])]  # fmt: skip
    module.foreshadow = [Foreshadow(id="f_tick", hint="钟楼里的咔哒声比钟摆慢半拍", where="tower", pays_off="k_motive")]
    module.ties = [Tie(id="t_old", hook="守钟人是他父亲的老战友", npc="warden", fits=["重要之人"])]
    assert not [i for i in audit(module) if i.level == "error"]
    game, _, _ = make(module, fake, {}, location="square")
    rec = game.world.rec
    assert rec.relations["preacher|warden|敌对"].value == -40 and "warden" in rec.relations["preacher|warden|敌对"].known_by
    plan = game.world.agent("preacher").plan
    assert [(s.id, s.due, s.when) for s in plan] == [("climb", parse_time("D1 23:30"), ""), ("tell", None, "flag:bell_stopped")]
    assert rec.threads["foreshadow:f_tick"].kind == "foreshadow"
    turn = Turn()
    await game._arrive("tower", turn)
    game._foreshadow(turn)
    assert any("比钟摆慢半拍" in n for n in turn.notes)
    again = Turn()
    await game._arrive("square", Turn())
    await game._arrive("tower", again)
    game._foreshadow(again)
    assert not any("比钟摆慢半拍" in n for n in again.notes)  # given once
    assert "守钟人是他父亲的老战友" in director_context(module, game.state, "开局改编")


# -- the world lives after a turn is told (Game.world_later) -------------------------------------------
async def test_on_a_screen_npcs_decide_after_the_turn_is_told_not_while_the_player_waits(module, fake):
    module.npc("warden").important = True
    game, cast, _ = make(module, fake, {}, location="tower")
    game.world_later = True
    w = game.world
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="lock", text="检查门锁", due=w.now + 1)]))
    asked: list = []

    def act(ask):
        asked.append(ask)
        return Scene(decisions=[Decision(npc="warden", ops=[O.Occur(text="守钟人放下工具，开始检查门锁", place="tower"),
                                                            O.AdvancePlan(npc="warden", step="lock", status="done")])])  # fmt: skip

    game.brain, game.minds = Canned(act), scheduler.Budget()  # type: ignore[assignment]
    turn = await game.resolve(WAIT, ruled(minutes=10))
    assert asked == [] and turn.world == []  # the turn is told without waiting for him
    await game.narrate(turn)
    await game.remember()
    assert game._world_task is not None
    await asyncio.gather(game._world_task)
    assert len(asked) == 1 and w.agent("warden").plan[0].status == "done"
    assert game.ready()  # what he did in front of the party comes as an interlude
    told = await game.interlude()
    assert told is not None and "开始检查门锁" in cast.reports[-1]


async def test_the_next_action_waits_for_the_worlds_turn_to_finish(module, fake):
    module.npc("warden").important = True
    # Away from the party, his prepared step lands offscreen and he thinks on after it.
    game, _, _ = make(module, fake, {"我看看齿轮": (Action(kind="investigate", summary="周衡看齿轮", target="齿轮间"),
                                                   Ruling(needs_roll=False, minutes=1, reason=""))}, location="office")  # fmt: skip
    game.world_later = True
    w = game.world
    w.apply(O.SetPlan(npc="warden", steps=[Step(id="go", text="下楼", due=w.now + 1,
                                                ops=[{"kind": "move", "entity": "warden", "to": "square"}])]))  # fmt: skip
    w.agent("warden").wake = w.now + 5  # and he means to think again a little later
    release = asyncio.Event()

    async def slow(ask):
        await release.wait()
        return Scene(decisions=[Decision(npc="warden", wait=600)])

    game.brain, game.minds = Canned(slow), scheduler.Budget()  # type: ignore[assignment]
    turn = await game.resolve(WAIT, ruled(minutes=10))
    await game.narrate(turn)
    await game.remember()
    parsing = asyncio.create_task(game.parse("我看看齿轮"))
    await asyncio.sleep(0)
    assert not parsing.done()  # the world's minutes come first
    release.set()
    await parsing
    assert w.entity("warden").at == "square"  # his prepared step landed before the new action


async def test_an_agenda_step_with_an_outcome_goes_ahead_with_no_model_and_its_flag_ends_the_game(module, fake):
    from autokeeper.module.schema import Agenda, Move

    module.agendas = [Agenda(npc="warden", goals=["守住秘密"], steps=[
        Move(id="office", text="去报社打听那个记者", when="D1 14:30", does="at:warden:office"),
        Move(id="stop", text="把钟停下", when="D1 15:00", does="flag:bell_stopped"),
    ])]
    game, _, _ = make(module, fake, {}, location="square")
    asked: list = []
    game.brain = Canned(lambda ask: asked.append(ask) or Scene(decisions=[]))  # type: ignore[assignment]
    game.minds = scheduler.Budget(turn=2, skip=4)
    plan = game.world.agent("warden").plan
    assert [(s.ops, s.achieved) for s in plan] == [([{"kind": "move", "entity": "warden", "to": "office"}], "at:warden:office"),
                                                   ([{"kind": "flag", "flag": "bell_stopped"}], "flag:bell_stopped")]
    fake.queue_totals(3)  # the ending's sanity reward
    turn = await game.resolve(WAIT, ruled(minutes=70))
    assert game.world.place("warden") == "office" and "bell_stopped" in game.state.flags
    assert [s.status for s in game.world.agent("warden").plan] == ["done", "done"]
    assert not any("该做了" in a.reasons["warden"] for a in asked)  # no model was asked to carry the steps out
    assert game.state.ending == "stopped" and turn.ending == "stopped"  # the clock ran out with no one stepping in


async def test_the_modules_past_is_remembered_by_those_who_know_it(module, fake):
    from autokeeper.module.schema import Past
    from autokeeper.world import context

    module.npc("warden").important = True
    module.history = [Past(when="二十年前", text="守钟人的儿子从钟楼上摔下来死了", who=["warden"], where="tower", known_by=["preacher"])]
    game, _, _ = make(module, fake, {}, location="square")
    w = game.world
    past = next(e for e in w.rec.events if e.text.startswith("（二十年前）"))
    assert past.visibility == "private" and set(past.witnesses) == {"warden", "preacher"} and not past.reactive
    assert "儿子从钟楼上摔下来" in context.npc_view(w, "warden").text
    assert "儿子从钟楼上摔下来" not in context.keeper_view(w)


async def test_a_failed_search_keeps_the_places_foreshadowing_for_a_later_visit(module, fake):
    from autokeeper.module.schema import Foreshadow

    module.foreshadow = [Foreshadow(id="f_tick", hint="钟楼里的咔哒声比钟摆慢半拍", where="tower", pays_off="k_motive")]
    game, _, _ = make(module, fake, {}, location="square")
    fake.queue(99)
    missed = Turn()
    await game._arrive("tower", missed)
    missed.checks.append(game.rules.check(game.state.investigator, "侦查"))
    game._foreshadow(missed)
    assert not missed.checks[0].passed
    assert not any("比钟摆慢半拍" in n for n in missed.notes) and "f_tick" not in game.state.hinted
    later = Turn()
    await game._arrive("square", Turn())
    await game._arrive("tower", later)
    game._foreshadow(later)
    assert any("比钟摆慢半拍" in n for n in later.notes) and game.state.hinted == ["f_tick"]
