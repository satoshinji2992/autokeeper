"""Actor-bound world tools are scoped, dry-runnable, atomic and replay-safe without models."""

from __future__ import annotations

import json
from collections import deque

import pytest

from autokeeper import config
from autokeeper.harness import Agent, Reply
from autokeeper.harness import models as hm
from autokeeper.harness.loop import step_schema
from autokeeper.world import knowledge
from autokeeper.world.ops import AddFact, Learn, Move
from autokeeper.world.store import World
from autokeeper.world.tools import Gateway, harness_tools

from .test_world import staged


@pytest.fixture
def world():
    return staged("tower")


def test_npc_query_cannot_read_remote_secrets_events_or_other_plans(world):
    world.apply(AddFact(fact="private_preacher", text="赤月密令哨兵词", known_by=["preacher"]))
    world.emit("occur", "远方密室哨兵词", actor="preacher", place="office", visibility="private", witnesses=[])
    before = world.st.model_dump_json()
    npc = Gateway(world, "warden").query()
    gm = Gateway(world, "director").query(limit=100)
    assert "赤月密令哨兵词" not in json.dumps(npc, ensure_ascii=False)
    assert "远方密室哨兵词" not in json.dumps(npc, ensure_ascii=False)
    assert set(npc["plans"]) == {"warden"}
    assert "赤月密令哨兵词" in json.dumps(gm, ensure_ascii=False)
    assert world.st.model_dump_json() == before


def test_recipient_cannot_classify_a_lie_from_fact_metadata_id_or_search(world):
    fact = world.rec.facts["npc:warden:lies:0"]
    assert world.apply(Learn(fact=fact.id, who=["preacher"], actor="warden", visibility="private")).ok
    receiver = Gateway(world, "preacher")
    rows = receiver.query("钟只是老了")["facts"]
    heard = next(row for row in rows if row["text"] == fact.text)
    assert heard["id"].startswith("claim:")
    assert {"kind", "true", "known_by", "revealed", "reveal_source"}.isdisjoint(heard)
    assert "lies" not in json.dumps(receiver.query(), ensure_ascii=False)
    assert "这是你编的" not in heard["source"] and "不能由此得知真伪" not in heard["source"]
    assert not receiver.query("lies")["facts"]
    assert not receiver.query("false")["facts"]
    own = next(row for row in Gateway(world, "warden").query("钟只是老了")["facts"] if row["text"] == fact.text)
    assert "这是你编的" in own["source"]
    assert fact.text == "钟只是老了" and not fact.true


def test_preview_is_pure_and_returns_original_read_versions(world):
    gateway = Gateway(world, "warden")
    before = world.st.model_dump_json(), world.m.model_dump_json()
    result = gateway.preview([{"kind": "move", "entity": "warden", "to": "square"}])
    assert result["ok"] and result["status"] == "preview"
    assert result["positions"]["warden"] == "square" and world.place("warden") == "tower"
    assert result["reads"]["entity:warden"] == world.version("entity:warden")
    assert result["post_reads"]["entity:warden"] > result["reads"]["entity:warden"]
    assert all(world.version(ref) == value for ref, value in gateway.native_reads(result["reads"]).items())
    assert (world.st.model_dump_json(), world.m.model_dump_json()) == before


def test_commit_binds_actor_and_publishes_actual_results(world):
    gateway = Gateway(world, "warden")
    ops = [{"kind": "move", "entity": "warden", "to": "square", "actor": "director"}]
    reads = gateway.query()["reads"]
    result = gateway.commit(ops, reads=reads, request_id="depart-1")
    assert result["ok"] and result["status"] == "committed"
    assert result["ops"][0]["actor"] == "warden"
    assert result["ops"][0]["operation"]["actor"] == "warden"
    assert world.place("warden") == result["positions"]["warden"] == "square"
    assert result["events"][0]["actor"] == "warden"
    assert world.m.npc("warden").location == "square"


def test_one_invalid_op_rejects_the_entire_batch(world):
    gateway = Gateway(world, "director")
    before = world.st.model_dump_json(), world.m.model_dump_json()
    result = gateway.commit([{"kind": "move", "entity": "warden", "to": "square"},
                             {"kind": "transfer", "item": "no-such-item", "to": "preacher"}],
                            reads=gateway.query()["reads"], request_id="bad-batch")
    assert not result["ok"] and result["index"] == 1
    assert (world.st.model_dump_json(), world.m.model_dump_json()) == before


def test_old_read_versions_reject_without_mutation(world):
    gateway = Gateway(world, "warden")
    reads = gateway.query()["reads"]
    assert world.apply(Move(entity="warden", to="square")).ok
    before = world.st.model_dump_json()
    result = gateway.commit([{"kind": "move", "entity": "warden", "to": "tower"}], reads=reads, request_id="old")
    assert not result["ok"] and result["status"] == "stale" and "entity:warden" in result["stale"]
    assert world.st.model_dump_json() == before


def test_same_request_replays_after_state_changed_and_conflicts_on_new_payload(world):
    gateway = Gateway(world, "warden")
    reads = gateway.query()["reads"]
    ops = [{"kind": "move", "entity": "warden", "to": "square"}]
    result = gateway.commit(ops, reads=reads, request_id="stable")
    seq = world.rec.seq
    assert result["ok"]
    replay = gateway.commit(ops, reads=reads, request_id="stable")
    assert replay["ok"] and replay["repeat"] and world.rec.seq == seq
    again = Gateway(World(world.m.model_copy(deep=True), world.st.model_copy(deep=True)), "warden")
    assert again.commit(ops, reads=reads, request_id="stable")["repeat"]
    changed = gateway.commit([{"kind": "move", "entity": "warden", "to": "tower"}], reads=reads, request_id="stable")
    assert not changed["ok"] and changed["status"] == "conflict"


def test_npc_cannot_move_someone_else_or_schedule_privileged_operations(world):
    gateway = Gateway(world, "warden")
    for ops in ([{"kind": "move", "entity": "preacher", "to": "tower", "actor": "director"}],
                [{"kind": "schedule", "event": "hidden", "at": world.now + 60, "text": "改写事实", "ops":
                  [{"kind": "rewrite", "target": "fact:truth", "text": "假真相", "actor": "director"}]}],
                [{"kind": "schedule", "event": "force", "at": world.now + 60, "text": "叫别人过来", "ops":
                  [{"kind": "move", "entity": "preacher", "to": "tower", "actor": "director"}]}]):
        result = gateway.preview(ops)
        assert not result["ok"]
    assert world.place("preacher") == "square" and not world.rec.scheduled


def test_opaque_fact_handles_decode_before_vetting_and_do_not_grant_new_knowledge(world):
    gateway = Gateway(world, "warden")
    ref = knowledge.public_ref(world.rec.facts["f_key"], "warden")
    decoded = gateway.normalize_ops([{"kind": "remember", "npc": "warden", "text": "记住钥匙", "fact": ref}])
    assert decoded[0]["fact"] == "f_key"
    result = gateway.preview(decoded)
    assert result["ok"] and result["ops"][0]["operation"]["fact"] == ref
    hidden = knowledge.public_ref(world.rec.facts["truth"], "warden")
    rejected = gateway.preview([{"kind": "learn", "fact": hidden, "who": ["warden"]}])
    assert not rejected["ok"]


def test_gm_rewrite_cannot_bypass_known_or_unverified_fact_guards(world):
    gateway = Gateway(world, "director")
    for fid, mark in (("truth", "revealed"), ("npc:warden:secrets:0", "legacy_revealed")):
        fact = world.rec.facts[fid]
        setattr(fact, mark, 3)
        result = gateway.preview([{"kind": "rewrite", "target": f"fact:{fid}", "text": "改写后的内容"}])
        assert not result["ok"] and fact.text != "改写后的内容"


@pytest.mark.parametrize("op", [{"kind": "reroll"}, {"kind": "clock", "minutes": 1000},
                               {"kind": "move", "entity": "warden", "to": "square", "minutes": 5},
                               {"kind": "improvise", "plan": {}}])
def test_world_tools_cannot_reroll_change_clock_or_skip_scene_preparation(world, op):
    clock = world.st.minutes
    assert not Gateway(world, "director").preview([op])["ok"]
    assert world.st.minutes == clock


async def test_harness_typed_tool_arguments_execute_a_real_world_preview(world, monkeypatch):
    script = deque([{"thought": "预演移动", "tool": "world_preview", "ops": [{"kind": "move", "entity": "warden", "to": "square"}]},
                    {"thought": "核对完成", "tool": "finish", "answer": "尚未执行行动"}])
    observations = []

    class Scripted(hm.Model):
        async def _call(self, messages, **kwargs):
            value = script.popleft()
            return Reply(text=json.dumps(value), data=value)

    monkeypatch.setitem(hm.MODELS, "gateway-test", Scripted)
    role = config.Role("gateway-test", "scripted", None)
    cfg = config.Config(roles={r: role for r in config.DEFAULT_ROLES})
    tools = harness_tools(Gateway(world, "warden"))
    schema = step_schema(tools)
    assert schema["properties"]["ops"]["type"] == "array"
    assert schema["properties"]["reads"]["type"] == "object"
    assert {t.name for t in tools} == {"world_query", "world_preview"}
    assert all("actor" not in t.args for t in tools)
    agent = Agent("world", "只读取和预演", tools, cfg=cfg, on_event=lambda kind, data: observations.append((kind, data)))
    try:
        result = await agent.run("预演去广场")
        assert result.finished and result.answer == "尚未执行行动" and world.place("warden") == "tower"
        acted = next(data for kind, data in observations if kind == "act")
        assert json.loads(acted["observation"])["positions"]["warden"] == "square"
        assert not script
    finally:
        await agent.close()


def test_npc_inventory_is_private_and_only_important_known_items_are_returned(world):
    from autokeeper.world.record import Entity

    world.rec.entities.update({
        "hidden_floor": Entity(id="hidden_floor", kind="item", name="暗格里的密码本", at="tower", hidden=True),
        "visible_floor": Entity(id="visible_floor", kind="item", name="桌上的重要笔记", at="tower"),
        "ordinary_cup": Entity(id="ordinary_cup", kind="item", name="普通杯子", at="tower", important=False),
        "preacher_watch": Entity(id="preacher_watch", kind="item", name="布道者的怀表", at="preacher", equipment="held"),
        "self_hidden": Entity(id="self_hidden", kind="item", name="自己的密信", at="warden", equipment="concealed", hidden=True,
                              quantity=2),
        "shown_item": Entity(id="shown_item", kind="item", name="刚出示的徽章", at="preacher", equipment="concealed",
                             hidden=True, quantity=7, observed_by=["warden"]),
    })
    gateway = Gateway(world, "warden")
    result = gateway.query(limit=100)
    entities = {entity["id"]: entity for entity in result["entities"]}
    assert {"visible_floor", "self_hidden", "shown_item"} <= entities.keys()
    assert {"hidden_floor", "ordinary_cup", "preacher_watch"}.isdisjoint(entities)
    assert entities["self_hidden"]["hidden"] and entities["self_hidden"]["quantity"] == 2
    assert {"hidden", "equipment", "quantity", "observed_by"}.isdisjoint(entities["shown_item"])
    assert "暗格里的密码本" not in json.dumps(result, ensure_ascii=False)


def test_equipment_preview_and_commit_bind_the_holder_and_keep_clock(world):
    gateway = Gateway(world, "warden")
    before = world.st.minutes
    ops = [{"kind": "equip", "item": "coat", "equipment": "worn", "actor": "director"}]
    preview = gateway.preview(ops)
    assert preview["ok"] and world.rec.entities["coat"].equipment == "carried"
    committed = gateway.commit(ops, reads=preview["reads"], request_id="put-on-coat")
    assert committed["ok"] and world.rec.entities["coat"].equipment == "worn"
    assert committed["ops"][0]["actor"] == "warden" and world.st.minutes == before
    assert not Gateway(world, "preacher").preview([{"kind": "equip", "item": "coat", "quantity": 0}])["ok"]


def test_scheduled_time_slice_does_not_expose_the_final_skip_clock(world):
    from autokeeper.engine.state import format_time

    world.st.minutes += 500
    world.clock = world.st.minutes - 300
    result = Gateway(world, "warden").query()
    assert result["clock"] == format_time(world.now) and result["clock"] != world.st.clock
