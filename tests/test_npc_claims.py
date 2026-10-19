"""NPCs can relay claims without learning the GM's truth labels or rewriting objective facts."""

from autokeeper.engine.state import GameState
from autokeeper.module.schema import NPC
from autokeeper.world import context, knowledge, scheduler
from autokeeper.world import ops as O
from autokeeper.world.agents import Canned, Decision, Scene
from autokeeper.world.record import Step
from autokeeper.world.store import World

from .test_world import fresh


def a_reported_claim():
    w = fresh("office")
    w.m.npcs.append(NPC(id="editor", name="报社编辑", role="本镇报社编辑", location="square"))
    w.sync()
    assert w.apply(O.Move(entity="warden", to="square")).ok
    claim = w.rec.facts["npc:warden:lies:0"]
    assert w.apply(O.Learn(fact=claim.id, who=["preacher"], actor="warden", visibility="private")).ok
    return w, claim


def test_the_speaker_knows_their_own_lie_but_listeners_get_only_the_report_and_opaque_reference():
    w, claim = a_reported_claim()
    native = claim.id
    alias = knowledge.public_ref(claim, "preacher")
    view = context.npc_view(w, "preacher")
    own = context.npc_view(w, "warden").text
    assert alias.startswith("claim:") and alias in view.text and native not in view.text
    assert "lies:" not in view.text and "这是你编的" not in view.text
    assert "钟只是老了（听说，来源：老守钟人）" in view.text
    assert "钟只是老了（这是你编的）" in own
    # Internal dependency IDs remain canonical. Saving does not change refs or truth.
    assert native in view.facts and f"fact:{native}" in view.reads
    copy = World(w.m.model_copy(deep=True), GameState.model_validate_json(w.st.model_dump_json()))
    assert knowledge.public_ref(copy.rec.facts[native], "preacher") == alias
    assert copy.rec.facts[native].true is False
    assert knowledge.public_ref(claim, "director") == native


def test_truthful_reports_and_untruthful_reports_use_the_same_reference_and_source_conventions():
    w, claim = a_reported_claim()
    truth = w.rec.facts["npc:warden:knows:0"]
    assert w.apply(O.Learn(fact=truth.id, who=["preacher"], actor="warden", visibility="private")).ok
    view = context.npc_view(w, "preacher").text
    for fact in (claim, truth):
        assert knowledge.public_ref(fact, "preacher").startswith("claim:")
        assert f"{fact.text}（听说，来源：老守钟人）" in view
        assert fact.id not in view
    assert "不能由此得知真伪" not in view  # no special label that identifies a false statement


def test_guessing_or_reusing_a_different_npcs_reference_never_grants_fact_access():
    w, claim = a_reported_claim()
    alias = knowledge.public_ref(claim, "preacher")
    assert knowledge.resolve_ref(w, "preacher", alias) == claim.id
    assert knowledge.resolve_ref(w, "preacher", claim.id) == claim.id  # old saved plans remain usable
    assert knowledge.resolve_ref(w, "editor", alias) is None
    assert knowledge.resolve_ref(w, "editor", knowledge.public_ref(claim, "editor")) is None
    assert knowledge.resolve_ref(w, "editor", claim.id) is None
    assert knowledge.resolve_ref(w, "preacher", "truth") is None


def test_saved_plan_and_memory_references_do_not_expose_truth_classification():
    w, claim = a_reported_claim()
    assert w.apply(O.Enlist(npc="preacher")).ok
    assert w.apply(O.SetPlan(npc="preacher", steps=[Step(
        id="retell", text="转告听来的说法", when=f"knows:preacher:{claim.id}",
    )])).ok
    assert w.apply(O.Remember(npc="preacher", text=f"稍后转述记录 {claim.id}。", fact=claim.id)).ok
    alias = knowledge.public_ref(claim, "preacher")
    for view in (context.npc_view(w, "preacher").text, context.npc_brief(w, "preacher")):
        assert claim.id not in view and alias in view
    assert w.agent("preacher").plan[0].when.endswith(claim.id)
    assert any(memory.fact == claim.id for memory in w.agent("preacher").memory)


async def test_normal_world_decisions_can_relay_an_opaque_claim_without_converting_it_to_truth():
    w, claim = a_reported_claim()
    alias = knowledge.public_ref(claim, "preacher")
    assert w.apply(O.Enlist(npc="preacher")).ok
    w.agent("preacher").wake = w.now

    def decide(ask):
        assert alias in ask.views["preacher"] and claim.id not in ask.views["preacher"]
        return Scene(decisions=[Decision(npc="preacher", ops=[
            O.Learn(fact=alias, who=["editor"], visibility="private"),
            O.Remember(npc="preacher", text="已经把守钟人的说法告诉编辑。", fact=alias),
            O.SetPlan(npc="preacher", steps=[Step(id="later", text="等编辑再来询问", when=f"knows:editor:{alias}")]),
        ])])

    report = await scheduler.advance(w, Canned(decide), w.now, w.now + 1)
    assert not report.refused and len(report.landed) == 3
    assert "editor" in claim.known_by and claim.true is False and claim.kind == "lie"
    assert any(memory.fact == claim.id for memory in w.agent("preacher").memory)
    assert w.agent("preacher").plan[0].when == f"knows:editor:{claim.id}"
    heard = context.npc_view(w, "editor").text
    assert "钟只是老了（听说，来源：街头布道者）" in heard
    assert "这是你编的" not in heard and "lies:" not in heard
    assert not any(f.true and f.text == claim.text for f in w.rec.facts.values())
