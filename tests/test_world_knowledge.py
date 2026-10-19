"""Public knowledge needs its own evidence, never shared words or another reveal marker."""

from __future__ import annotations

from autokeeper import paths
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState, WorldChange
from autokeeper.module.schema import Module
from autokeeper.world import adapters, context, knowledge
from autokeeper.world import ops as O
from autokeeper.world.record import PLAYERS, Fact
from autokeeper.world.store import World

from .test_world import fresh


def test_public_address_does_not_reveal_the_bookhunters_truth_or_cascade():
    m = Module.load(paths.MODULES / "zhuishuren")
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    w = World(m, st)
    for _ in range(8):
        w.sync()
    assert w.rec.facts["truth"].revealed is None
    assert not knowledge.sees(w.rec.facts["truth"], PLAYERS)
    assert all(f.revealed is None for f in w.rec.facts.values() if f.kind in {"secret", "conclusion", "clue"})


def test_full_statement_has_durable_source_and_does_not_certify_longer_secret():
    w = fresh("tower")
    secret = w.rec.facts["npc:warden:secrets:0"]
    hidden = w.rec.facts["f_longer"] = Fact(id="f_longer", text=secret.text + "，那是一只困在铜盒里的怪物", kind="secret")
    w.st.add("npc", f"我得告诉你：{secret.text}。", who="老守钟人")
    assert secret.id in w.sync()
    assert secret.reveal_source.startswith("log:") and secret.text in secret.reveal_excerpt
    assert hidden.revealed is None
    w.st.log.clear()
    w.st.memory = ""
    for _ in range(3):
        w.sync()
    assert knowledge.sees(secret, PLAYERS) and hidden.revealed is None
    assert secret.text not in knowledge.shown(w.m, w.st, w.rec)
    loaded = GameState.model_validate_json(w.st.model_dump_json())
    assert loaded.record.facts[secret.id].reveal_source == secret.reveal_source


def test_fragments_and_full_player_guesses_cannot_prove_a_secret():
    w = fresh("tower")
    f = w.rec.facts["f_secret"] = Fact(id="f_secret", text="守钟人把怀表藏在钟里，表里有另一个人的灵魂", kind="secret")
    w.st.add("keeper", "守钟人把怀表藏在钟里。")
    w.st.add("npc", "表里有另一个人的灵魂。", who="街头布道者")
    w.st.add("player", f.text, who=w.st.investigator.name)
    assert not knowledge.exposed(f.text, knowledge.table(w.m, w.st, w.rec))
    w.sync()
    assert f.revealed is None


def test_paraphrase_needs_an_explicit_disclosure_instead_of_guessing_semantics():
    w = fresh("tower")
    f = w.rec.facts["f_secret"] = Fact(id="f_secret", text="老守钟人在钟里藏了亡子的怀表", kind="secret")
    w.st.add("keeper", "他终于开口：钟里藏了亡子的怀表。")
    w.sync()
    assert f.revealed is None
    assert w.apply(O.Learn(fact=f.id, who=[PLAYERS], actor="engine", visibility="public")).ok
    assert knowledge.sees(f, PLAYERS) and f.reveal_source.startswith("event:")


def test_found_clues_and_explicit_local_events_keep_evidence_after_logs_are_folded():
    w = fresh("tower")
    w.st.clues.append("c_gears")
    w.sync()
    clue = w.rec.facts["clue:c_gears"]
    assert clue.reveal_source == "clue:c_gears" and clue.reveal_excerpt == clue.text
    assert w.apply(O.AddFact(fact="f_seen", text="左手缺了两根手指", about=["warden"], public=True)).ok
    f = w.rec.facts["f_seen"]
    assert f.reveal_source.startswith("event:") and "两根手指" in f.reveal_excerpt
    w.rec.events.clear()
    w.st.log.clear()
    w.sync()
    assert knowledge.sees(f, PLAYERS)


def test_legacy_markers_are_reverified_or_quarantined_without_claiming_unseen():
    w = fresh("tower")
    f = w.rec.facts["truth"]
    f.revealed, f.known_by = 0, [PLAYERS]
    public = w.rec.facts["npc:warden:knows:0"]
    public.revealed, public.known_by = 2, ["warden", PLAYERS]
    w.st.add("npc", public.text, who="老守钟人")
    w.sync()
    assert public.revealed == 2 and public.reveal_source.startswith("log:")
    assert f.revealed is None and f.legacy_revealed == 0 and not knowledge.sees(f, PLAYERS)
    assert "无法核验" in f.reveal_note and "truth" in context.director_view(w).text
    assert "历史公开待核验" in context.director_view(w).text
    result = w.apply(O.Rewrite(target="fact:truth", text="其他真相", actor="director"))
    assert not result.ok and "历史公开标记尚未核验" in result.problems[0]
    w.st.add("keeper", f.text)
    w.sync()
    assert knowledge.sees(f, PLAYERS) and f.legacy_revealed is None and f.reveal_excerpt == f.text
    assert "已由可追溯" in f.reveal_note


def test_verified_fact_does_not_make_another_legacy_mark_verifiable():
    w = fresh("tower")
    f = w.rec.facts["truth"]
    f.revealed, f.reveal_source, f.reveal_excerpt = 0, "old-archive:1", f.text
    duplicate = w.rec.facts["f_duplicate"] = Fact(id="f_duplicate", text=f.text, revealed=0, known_by=[PLAYERS])
    w.sync()
    assert f.revealed == 0
    assert duplicate.revealed is None and duplicate.legacy_revealed == 0


def test_private_conversation_is_known_to_participants_without_informing_bystanders():
    w = fresh("tower")
    assert w.apply(O.Move(entity="preacher", to="tower")).ok
    change = WorldChange(kind="fact", key="private_letter", text="塔菲只给守钟人看了信封上的字", npc="warden")
    results = adapters.settle_changes(w, [change], source="warden", spoken=change.text, private=True)
    assert results and all(r.ok for r in results)
    fact = w.rec.facts["wc:private_letter"]
    assert knowledge.sees(fact, PLAYERS) and knowledge.sees(fact, "warden")
    assert not knowledge.sees(fact, "preacher")
    e = w.rec.event(results[0].event)
    assert e.visibility == "private" and "preacher" not in e.witnesses
    assert fact.reveal_source == f"private-event:{e.id}"


def test_private_learning_does_not_inform_an_unrelated_npc_in_the_room():
    w = fresh("tower")
    assert w.apply(O.Move(entity="preacher", to="tower")).ok
    fact = w.rec.facts["npc:warden:knows:0"]
    assert w.apply(O.Learn(fact=fact.id, who=[PLAYERS], actor="warden", visibility="private")).ok
    assert knowledge.sees(fact, PLAYERS) and not knowledge.sees(fact, "preacher")
    assert w.rec.events[-1].visibility == "private"


def test_rumours_and_received_lies_do_not_give_the_listener_the_system_truth():
    w = fresh("tower")
    assert w.apply(O.Learn(fact="npc:warden:lies:0", who=["preacher"], actor="engine")).ok
    assert w.apply(O.AddFact(fact="f_rumour", text="钟楼下埋着一口井", fact_kind="rumor", true=False, known_by=["preacher"])).ok
    teller = context.npc_view(w, "warden").text
    listener = context.npc_view(w, "preacher").text
    assert "钟只是老了（这是你编的）" in teller
    assert "钟只是老了（来自亲历或当面获知的记录）" in listener
    assert "钟楼下埋着一口井（传闻，尚未证实）" in listener
    assert "这是你编的" not in listener


def test_director_reads_track_only_the_records_actually_displayed():
    from autokeeper.world.scheduler import fresh as moved

    w = fresh("tower")
    assert w.apply(O.Enlist(npc="warden")).ok
    assert w.apply(O.MakePromise(promise="tea", by="warden", to=w.st.investigator.id, text="请记者喝茶")).ok
    assert w.apply(O.Relate(a="warden", b=w.st.investigator.id, rel="信任", delta=10)).ok
    assert w.apply(O.OpenThread(thread="clock", title="钟里有什么")).ok
    assert w.apply(O.Schedule(event="dusk", at=w.now + 60, text="黄昏给钟上弦", actor="warden")).ok
    # Only the last thirty hidden facts are actually presented to the director.
    for i in range(35):
        w.rec.facts[f"f_{i}"] = Fact(id=f"f_{i}", text=f"后台备忘 {i}")
    view = context.director_view(w)
    event = w.rec.events[-1]
    expected = {"entity:warden", "entity:tower", "agent:warden", "promise:tea",
                f"relation:warden|{w.st.investigator.id}|信任", "thread:clock",
                f"event:{event.id}", "scheduled:dusk", "fact:f_34"}
    assert expected <= set(view.reads) and "fact:f_0" not in view.reads
    w.rec.facts["f_0"].version += 1
    assert moved(w, view.reads) == []
    assert w.apply(O.Move(entity="warden", to="square")).ok
    assert "entity:warden" in moved(w, view.reads)
    w.rec.scheduled["dusk"].status = "done"
    assert "scheduled:dusk" in moved(w, view.reads)
