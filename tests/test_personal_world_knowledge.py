"""Personal evidence stays personal through synchronization, folding and backstage rewrites."""

from __future__ import annotations

from autokeeper.engine.state import GameState
from autokeeper.module.schema import Clue
from autokeeper.world import knowledge
from autokeeper.world import ops as O
from autokeeper.world.record import DIRECTOR, PLAYERS, Fact
from autokeeper.world.store import World

from .test_world import fresh


def split_world():
    w = fresh("tower")
    other = w.st.investigator.model_copy(update={"id": "remote", "name": "异地调查员"}, deep=True)
    w.st.investigators.append(other)
    w.st.apart[other.id] = "square"
    knowledge.seed(w.m, w.st, w.rec)
    return w


def test_global_discovery_indices_do_not_certify_shared_knowledge():
    w = split_world()
    own = w.st.investigator.id
    w.st.visited.append("tower")
    w.entity("tower").observed_by = [own]
    w.m.location("tower").read_aloud = "QA_PRIVATE_SCENE_84219"
    w.rec.facts["qa_room"] = Fact(id="qa_room", text="QA_PRIVATE_SCENE_84219")
    w.m.clues.append(Clue(id="qa_local", title="QA线索", text="QA_PRIVATE_CLUE_84219", where="tower"))
    w.st.clues.append("qa_local")
    w.st.npc("warden").met = True
    w.m.npc("warden").look = "QA_PRIVATE_LOOK_84219"
    w.rec.facts["qa_look"] = Fact(id="qa_look", text="QA_PRIVATE_LOOK_84219")
    knowledge.seed(w.m, w.st, w.rec)
    w.rec.facts["clue:qa_local"].known_by = [own]
    w.sync()
    assert all(not knowledge.sees(w.rec.facts[fid], PLAYERS) for fid in ("qa_room", "clue:qa_local", "qa_look"))
    assert knowledge.sees(w.rec.facts["clue:qa_local"], own)
    assert all(knowledge.sees(w.rec.facts[fid], DIRECTOR) for fid in ("qa_room", "clue:qa_local", "qa_look"))


def test_scoped_statement_is_learned_only_by_its_readers_and_survives_log_folding():
    w = split_world()
    own = w.st.investigator.id
    fact = w.rec.facts["qa_private"] = Fact(id="qa_private", text="QA_PRIVATE_FULL_STATEMENT_84219")
    w.st.add("npc", fact.text, who="发言者", audience=[own])
    w.sync()
    assert knowledge.sees(fact, own) and not knowledge.sees(fact, "remote") and not knowledge.sees(fact, PLAYERS)
    w.st.private_history.extend(w.st.log)
    w.st.log.clear()
    saved = GameState.model_validate_json(w.st.model_dump_json())
    restored = World(w.m, saved)
    restored.sync()
    fact = restored.rec.facts[fact.id]
    assert knowledge.sees(fact, own) and not knowledge.sees(fact, "remote") and not knowledge.sees(fact, PLAYERS)
    assert not restored.apply(O.Rewrite(target=f"fact:{fact.id}", text="QA_NEW_TRUTH_84219")).ok


def test_personally_known_fact_needs_a_reason_and_keeps_its_original_readers_when_rewritten():
    w = split_world()
    own = w.st.investigator.id
    fact = w.rec.facts["qa_private"] = Fact(id="qa_private", text="QA_OLD_ACCOUNT_84219", known_by=[own])
    assert not w.apply(O.Rewrite(target=f"fact:{fact.id}", text="QA_NEW_TRUTH_84219")).ok
    result = w.apply(O.Rewrite(target=f"fact:{fact.id}", text="QA_NEW_TRUTH_84219", reason="原先收到的是伪造材料"))
    assert result.ok, result.problems
    told = next(f for f in w.rec.facts.values() if f.id.startswith("told:qa_private:"))
    assert told.text == "QA_OLD_ACCOUNT_84219" and knowledge.sees(told, own)
    assert not knowledge.sees(told, "remote") and not knowledge.sees(told, PLAYERS)
    assert not knowledge.sees(fact, own) and not knowledge.sees(fact, "remote")


def test_personal_projection_of_a_shared_log_does_not_certify_the_objective_words_for_everyone():
    w = split_world()
    own = w.st.investigator.id
    fact = w.rec.facts["qa_projection"] = Fact(id="qa_projection", text="QA_PERSONAL_VIEW_84219")
    w.st.add("keeper", fact.text, presented={"remote": {"text": "QA_OTHER_VIEW_84219"}})
    w.sync()
    assert knowledge.sees(fact, own) and not knowledge.sees(fact, "remote") and not knowledge.sees(fact, PLAYERS)


def test_equipment_disclosed_in_one_scene_does_not_get_the_shared_players_marker():
    w = split_world()
    own = w.st.investigator.id
    result = w.apply(O.Equip(item="coat", reveal=True, evidence="QA_LOCAL_ITEM_84219"))
    assert result.ok, result.problems
    assert own in w.entity("coat").observed_by
    assert "remote" not in w.entity("coat").observed_by and PLAYERS not in w.entity("coat").observed_by


def test_private_witnessed_event_keeps_old_words_after_a_fact_rewrite():
    w = split_world()
    own = w.st.investigator.id
    learned = w.apply(O.AddFact(fact="qa_received", text="QA_OLD_RECEIVED_84219", known_by=[own],
                                actor="warden", visibility="private"))
    assert learned.ok, learned.problems
    event = w.rec.event(learned.event)
    old = event.text
    changed = w.apply(O.Rewrite(target="fact:qa_received", text="QA_NEW_HIDDEN_84219", reason="原材料是伪造的"))
    assert changed.ok, changed.problems
    assert event.text == old
    w.sync()
    assert not knowledge.sees(w.rec.facts["qa_received"], own)


def test_rewrite_before_sync_preserves_every_personally_addressed_original_reader():
    w = split_world()
    own = w.st.investigator.id
    fact = w.rec.facts["qa_private"] = Fact(id="qa_private", text="QA_SEPARATELY_RECEIVED_84219")
    for reader in (own, "remote"):
        w.st.add("npc", fact.text, who="发言者", audience=[reader])
    changed = w.apply(O.Rewrite(target="fact:qa_private", text="QA_NEW_HIDDEN_84219", reason="他们分别收到的都是同一伪造版本"))
    assert changed.ok, changed.problems
    told = next(f for f in w.rec.facts.values() if f.id.startswith("told:qa_private:"))
    assert {own, "remote"} <= set(told.known_by) and not knowledge.sees(told, PLAYERS)
