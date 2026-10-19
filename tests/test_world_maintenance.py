"""The maintainer reports inconsistencies without changing state or enforcing honesty."""

from __future__ import annotations

from autokeeper.world import desk, maintenance
from autokeeper.world.ops import AddFact, Move
from autokeeper.world.record import Fact, Step

from .test_world import staged


def test_inspection_is_read_only_and_reports_exact_cross_layer_ids():
    world = staged("tower")
    world.m.npc("warden").location = "office"
    world.st.item_owners["coat"] = world.st.investigator.id
    before = world.st.model_dump_json(), world.m.model_dump_json()
    result = maintenance.inspect(world)
    issues = {issue["code"]: issue for issue in result["issues"]}
    assert result["read_only"] and issues["npc_projection"]["ref"] == "entity:warden"
    assert issues["item_owner"]["ref"] == "entity:coat"
    assert "世界位置=tower" in issues["npc_projection"]["evidence"]
    assert (world.st.model_dump_json(), world.m.model_dump_json()) == before


def test_intentional_lies_rumors_and_conflicting_testimony_are_not_reported_as_bugs():
    world = staged()
    world.rec.facts["comforting_claim"] = Fact(id="comforting_claim", text="钟楼绝对安全", kind="lie", true=False,
                                              about=["preacher"], known_by=["preacher", "warden"])
    world.apply(AddFact(fact="town_rumor", text="镇里有人说钟已经停了", fact_kind="rumor", true=False,
                        known_by=["warden"]))
    before = world.st.model_dump_json()
    result = maintenance.inspect(world)
    assert not any(issue["ref"] in {"fact:comforting_claim", "fact:town_rumor"} for issue in result["issues"])
    assert "撒谎" in result["scope"] and world.st.model_dump_json() == before
    assert world.rec.facts["comforting_claim"].text == "钟楼绝对安全" and not world.rec.facts["comforting_claim"].true


def test_invalid_conditions_blocked_steps_and_stale_requests_keep_their_evidence():
    world = staged()
    world.rec.agents["warden"].plan = [Step(id="bad", text="等某人", when="at:nobody:wrong-place"),
                                           Step(id="blocked", text="等门打开", status="blocked", note="门锁着")]
    reads = {"entity:warden": world.version("entity:warden")}
    request = desk.submit(world, "测试待提交", [{"kind": "move", "entity": "warden", "to": "square"}], reads=reads)
    assert world.apply(Move(entity="warden", to="square")).ok
    result = maintenance.inspect(world)
    issues = {issue["code"]: issue for issue in result["issues"]}
    assert issues["plan_condition"]["ref"] == "agent:warden/step:bad"
    assert issues["plan_blocked"]["severity"] == "info"
    assert issues["request_stale"]["ref"] == f"request:{request.id}"
    assert request.status == "pending" and world.rec.agents["warden"].plan[0].status == "pending"


def test_unproven_disclosure_is_reported_without_revealing_or_rewriting_it():
    world = staged()
    fact = world.rec.facts["truth"]
    fact.legacy_revealed = 3
    result = maintenance.inspect(world)
    issue = next(issue for issue in result["issues"] if issue["code"] == "legacy_reveal_unverified")
    assert issue["ref"] == "fact:truth" and fact.revealed is None and fact.legacy_revealed == 3
