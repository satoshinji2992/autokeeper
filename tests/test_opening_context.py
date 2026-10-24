"""The keeper receives an arriving person's identity and actual links, not only a quest."""

from __future__ import annotations

from autokeeper.agents import director
from autokeeper.agents.director import Improv, NewEvent, NewNPC
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import parse_time
from autokeeper.module.schema import NPC, Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context, ops
from autokeeper.world.record import PLAYERS

from .test_engine import improvised, make


async def test_an_unintroduced_visitor_has_a_reason_to_be_here_without_publicly_disclosing_it(fake):
    module = Module.load(EXAMPLE)
    module.npcs.append(NPC(
        id="worker", name="马泰奥·贝里", role="砖厂工人，也是周衡任职报纸的读者", location="tower",
        goal="请周衡将弟弟生病的事登报", knows=["房东在店里听他说要找周衡，便告诉他记者暂住钟楼。"],
    ))
    game, _, _ = make(module, fake, {}, location="tower")
    try:
        assert game.world.apply(ops.Relate(a="worker", b="warden", rel="熟识", note="守钟人也在砖厂做过工。")).ok
        brief = context.keeper_brief(game.world)
        assert "身份：砖厂工人，也是周衡任职报纸的读者" in brief
        assert "请周衡将弟弟生病的事登报" in brief
        assert "房东在店里听他说要找周衡，便告诉他记者暂住钟楼。" in brief
        assert "马泰奥·贝里对老守钟人：熟识（守钟人也在砖厂做过工。）" in brief
        assert not game.state.npc("worker").met
        assert all(PLAYERS not in f.known_by and f.revealed is None for f in game.world.rec.facts.values()
                   if f.id.startswith("npc:worker:"))
        public = context.keeper_view(game.world)
        assert "记者暂住钟楼" not in public and "砖厂做过工" not in public
    finally:
        await game.close()


async def test_current_knowledge_and_relationships_replace_the_original_arrival_story(fake):
    module = Module.load(EXAMPLE)
    module.npc("warden").knows = ["从未见过调查员，不知道他从哪里来。"]
    game, _, _ = make(module, fake, {}, location="tower")
    try:
        w = game.world
        assert w.apply(ops.Rewrite(
            target="fact:npc:warden:knows:0", text="周衡是旧同事介绍来的，已寄信告知今天抵达。",
            reason="将开局联系接到调查员已写的职业背景。",
        )).ok
        assert w.apply(ops.Relate(a="warden", b=game.state.investigator.id, rel="信任", note="旧同事的介绍信。")).ok
        assert w.apply(ops.Relate(a="warden", b=game.state.investigator.id, rel="信任", note="发现介绍信署名不对，仍在核实。")).ok
        # Even an out-of-date cached module list cannot replace the record's current facts.
        module.npc("warden").knows = ["已失效的人物旧稿。"]
        brief = context.keeper_brief(w)
        assert "本人所知（不等于已经告诉调查员）" in brief
        assert "周衡是旧同事介绍来的，已寄信告知今天抵达。" in brief
        assert "已失效的人物旧稿" not in brief
        assert "信任（发现介绍信署名不对，仍在核实。）" in brief
        assert "信任（旧同事的介绍信。）" not in brief
    finally:
        await game.close()


async def test_a_local_persons_newly_learned_knowledge_keeps_its_source_and_stays_local(fake):
    module = Module.load(EXAMPLE)
    module.npc("preacher").location = "office"
    game, _, _ = make(module, fake, {}, location="tower")
    try:
        w = game.world
        assert w.apply(ops.AddFact(
            fact="room_notice", text="周衡改住报社楼上的客房。", fact_kind="knows", known_by=["preacher"],
            actor="preacher", visibility="private",
        )).ok
        assert w.apply(ops.Learn(
            fact="room_notice", who=["warden"], actor="preacher", how="午前在广场当面告知。",
        )).ok
        assert w.apply(ops.AddFact(
            fact="remote_errand", text="布道者今晚替印刷厂收账。", fact_kind="knows", known_by=["preacher"],
            actor="preacher", visibility="private",
        )).ok
        brief = context.keeper_brief(w)
        assert "周衡改住报社楼上的客房。（听说，来源：街头布道者）" in brief
        assert "布道者今晚替印刷厂收账" not in brief
        assert "周衡改住报社楼上的客房" not in context.keeper_view(w)
    finally:
        await game.close()


def test_opening_director_receives_authored_arrival_and_conditional_events():
    module = Module.load(EXAMPLE)
    module.opening = "旧同事在广场等候，陪调查员走到钟楼门口。"
    module.location(module.start).keeper = "先由守钟人认出介绍信，不代替调查员上楼。"
    module.events = [
        Event(id="welcome", title="接待", when=f"visit:{module.start}", where=module.start,
              text="守钟人开门接待。", read_aloud="老人将门拉开。"),
        Event(id="later", title="晚间来访", when="D1 21:00", where=module.start,
              text="印刷工晚上来送更正稿。"),
        Event(id="done", title="旧事", when=f"visit:{module.start}", where=module.start,
              text="已经讲过的旧事。"),
        Event(id="elsewhere", title="远处", when="D1 21:00", where="office", text="远处无关的场面。"),
    ]
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.fired = ["done"]
    opening = director.context(module, state, "开局改编")
    assert module.opening in opening and module.location(module.start).keeper in opening
    assert "守钟人开门接待" in opening and "老人将门拉开" in opening
    assert "条件 D1 21:00：印刷工晚上来送更正稿" in opening
    assert "已经讲过的旧事" not in opening and "远处无关的场面" not in opening
    regular = director.context(module, state, "展开")
    assert module.opening not in regular and "印刷工晚上来送更正稿" not in regular


async def test_opening_delivers_an_improvised_visitors_identity_without_firing_later_connections(fake):
    module = Module.load(EXAMPLE)
    module.events = []
    module.endings = []
    arriving = Improv(
        kind="开局改编", reason="将工人的求助接到调查员的编辑职业", npcs=[
            NewNPC(id="worker", name="马泰奥·贝里", role="砖厂工人，也是调查员报纸的读者", location=module.start,
                   goal="想请编辑登出弟弟生病的事", knows=["房东告诉他编辑今天会到广场，他便带着稿纸来等。"]),
            NewNPC(id="printer", name="约翰·韦伯", role="报社的印刷工", location="office", goal="下班后替主编送信"),
        ], events=[
            NewEvent(id="worker_arrival", title="来客", when="now", where=module.start, present=["worker"],
                     read_aloud="一个带着稿纸的男人在广场等候，自称马泰奥。"),
            NewEvent(id="later_letter", title="晚信", when="D1 21:00", where=module.start, present=["printer"],
                     read_aloud="晚间另有来访，送来一封信。"),
        ],
    )
    game, cast, _ = improvised(module, fake, {}, [arriving])
    game.state.investigator.occupation = "编辑"
    try:
        await game.start()
        assert len(cast.asked) == 1 and cast.asked[0][0] == "开局改编"
        assert "身份：砖厂工人，也是调查员报纸的读者" in cast.lores[0]
        assert "想请编辑登出弟弟生病的事" in cast.lores[0]
        assert "房东告诉他编辑今天会到广场，他便带着稿纸来等。" in cast.lores[0]
        assert "worker_arrival" in game.state.fired and "later_letter" not in game.state.fired
        assert "晚间另有来访" not in cast.reports[0]
        assert len([npc for npc in module.npcs if npc.id == "worker"]) == 1
        assert game.world.place("worker") == module.start
        assert game.world.place("printer") == "office" and "printer" not in game.world.present(module.start)
        game.state.minutes = parse_time("D1 21:00")
        await game._time(Turn())
        assert "later_letter" in game.state.fired and game.world.place("printer") == module.start
    finally:
        await game.close()
