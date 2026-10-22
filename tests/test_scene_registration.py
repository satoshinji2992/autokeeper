"""A keeper can register an established presence without inventing a journey or a plot thread."""

from __future__ import annotations

import json

import pytest

from autokeeper.agents.cast import Action, Plan
from autokeeper.engine.state import GameState
from autokeeper.harness import models as hm
from autokeeper.module.schema import Item, Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context, scene
from autokeeper.world.ops import AddFact

from .test_scene import SCRIPT, SEEN, Writer, ctx, made


@pytest.fixture(autouse=True)
def local_writer(monkeypatch):
    monkeypatch.setitem(hm.MODELS, "writer", Writer)
    SCRIPT.clear()
    SEEN.clear()


def test_unrecorded_origin_can_be_registered_here_without_travel(fake):
    game, _ = made("office")
    w = game.world
    w.rec.entities["preacher"].at = ""
    before = w.st.model_dump_json()
    prepared = scene.plan(w, scene.Scene(here=["preacher"]), ctx(game, 0, walk=False))
    assert not prepared.problems and w.st.model_dump_json() == before
    assert [u.said for u in prepared.held] == [["补记街头布道者在报社"]]
    minutes = w.st.minutes
    assert scene.land(w, prepared.held)
    assert w.present("office") == ["preacher"] and w.m.npc("preacher").location == "office"
    assert w.st.minutes == minutes
    assert w.rec.journal[-1].actor == "keeper" and "补记已在眼前" in prepared.held[0].steps[0]["reason"]


@pytest.mark.parametrize("status", ["失踪", "死亡"])
def test_missing_origin_does_not_override_an_established_absence(fake, status):
    game, _ = made("office")
    e = game.world.rec.entities["preacher"]
    e.at, e.status = "", status
    prepared = scene.plan(game.world, {"here": ["preacher"]}, ctx(game, 0))
    assert prepared.problems and not prepared.held
    assert e.at == "" and e.status == status


def test_registered_remote_position_still_requires_actual_travel(fake):
    game, _ = made("office")
    prepared = scene.plan(game.world, {"here": ["preacher"]}, ctx(game, 0, walk=False))
    assert any("要 10 分钟" in p and "只过了 0 分钟" in p for p in prepared.problems)
    assert not prepared.held and game.world.rec.entities["preacher"].at == "square"


@pytest.mark.parametrize("fields", [
    {"companions": ["misspelled_actor"]},
    {"npc": "misspelled_actor"},
    {"before": [{"npc": "misspelled_actor", "speech": "一起走吧"}]},
    {"destination": "misspelled_place"},
])
async def test_structured_action_references_are_checked_before_consent_or_travel(fields):
    game, _ = made()
    prepared = game._planned_scene(Plan(action=Action(kind="move", summary="尝试与同伴出发", **fields)))
    assert prepared.problems and any("未登记" in problem for problem in prepared.problems)
    await game.close()


async def test_the_existing_plan_repair_corrects_a_party_reference_without_creating_an_npc():
    game, _ = made()
    teammate = game.state.investigator.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    game.state.investigators.append(teammate)
    game.state.human.append(teammate.id)
    game.world.sync()
    SCRIPT.extend([
        {"action": {"kind": "move", "summary": "周衡与林秋去钟楼", "destination": "tower", "companions": ["lynn"]}},
        {"action": {"kind": "move", "summary": "周衡与林秋去钟楼", "destination": "tower", "companions": ["lin"]}},
    ])
    plan = await game.cast.plan(game.state, "和林秋一起去钟楼", check=lambda p: game._planned_scene(p).problems)
    assert len(SEEN) == 2 and "lynn 未登记" in SEEN[-1]["messages"][-1]["content"]
    assert plan.action.companions == ["lin"] and not game._planned_scene(plan).problems
    await game.prepare(plan.action)
    assert not game.state.pending_turn["preparation"]["blocked"] and len(SEEN) == 2
    assert not any(n.name == "林秋" for n in game.m.npcs)
    await game.close()


def test_new_person_is_prepared_without_live_changes_then_visible_to_runtime_and_reload(fake):
    game, _ = made("office")
    w = game.world
    person = scene.Person(id="driver", name="亨利", role="车夫", look="沾着机油的外套", personality="爽快",
                          goal="把客人送到医院", knows=["刚才有一辆黑车开向桥边"])
    before = w.st.model_dump_json(), w.m.model_dump_json()
    minutes = w.st.minutes
    prepared = scene.plan(w, scene.Scene(people=[person], here=["driver"]), ctx(game, 0, walk=False))
    assert not prepared.problems
    assert (w.st.model_dump_json(), w.m.model_dump_json()) == before
    assert [u.key for u in prepared.held] == ["introduce:driver"]
    assert scene.land(w, prepared.held)
    assert w.present("office") == ["driver"] and w.rec.entities["driver"].origin == "improv"
    assert w.m.npc("driver").personality == "爽快" and game.cast.module.npc("driver").goal == "把客人送到医院"
    assert "刚才有一辆黑车开向桥边" in context.npc_brief(w, "driver")
    assert w.m.npc("driver").portrait and not w.rec.threads
    assert w.rec.journal[-1].actor == "keeper" and w.rec.journal[-1].kind == "improvise"
    assert w.st.minutes == minutes

    saved = GameState.model_validate_json(w.st.model_dump_json())
    loaded, _ = made(module=Module.load(EXAMPLE), state=saved)
    assert loaded.world.present("office") == ["driver"]
    assert loaded.cast.module.npc("driver").look == "沾着机油的外套"
    assert "刚才有一辆黑车开向桥边" in context.npc_brief(loaded.world, "driver")
    assert loaded.world.rec.journal[-1].id == w.rec.journal[-1].id
    assert not loaded.world.rec.threads


def test_person_registration_precedes_company_and_objects_without_making_bad_handover_true(fake):
    m = Module.load(EXAMPLE)
    m.items.append(Item(id="lamp", name="铜提灯", description="旧铜提灯", where="tower", owner="warden"))
    game, _ = made("office", module=m)
    lead = game.state.investigator.id
    prepared = scene.plan(game.world, {
        "people": [{"id": "driver", "name": "车夫"}], "here": ["driver"], "follow": ["driver"],
        "holds": [{"item": "lamp", "holder": "driver"}],
        "things": [{"name": "一杯热茶", "holder": "driver"}],
    }, ctx(game, 0))
    assert any("铜提灯递不过去" in p for p in prepared.problems)
    assert scene.land(game.world, prepared.held)
    assert game.world.rec.entities["driver"].following == lead
    assert game.world.rec.entities["lamp"].at == "warden"
    assert any(e.name == "一杯热茶" and e.at == "driver" for e in game.world.rec.entities.values())
    assert not game.world.rec.threads


def test_existing_person_cannot_be_recreated_to_replace_identity_or_knowledge(fake):
    game, _ = made("office")
    before = game.world.st.model_dump_json(), game.m.model_dump_json()
    prepared = scene.plan(game.world, {"people": [{"id": "preacher", "name": "假名", "knows": ["守钟人的秘密"]}]}, ctx(game))
    assert any("已经存在" in p for p in prepared.problems) and not prepared.held
    assert (game.world.st.model_dump_json(), game.m.model_dump_json()) == before


def test_relanding_same_registration_is_idempotent(fake):
    game, _ = made("office")
    prepared = scene.plan(game.world, {"people": [{"id": "driver", "name": "车夫"}]}, ctx(game))
    assert not prepared.problems and scene.land(game.world, prepared.held)
    journal = len(game.world.rec.journal)
    assert scene.land(game.world, prepared.held)
    assert len(game.world.rec.journal) == journal
    assert len(game.state.improv) == 1 and sum(n.id == "driver" for n in game.m.npcs) == 1


async def test_registered_person_can_speak_with_their_own_knowledge_not_others_secrets(fake):
    game, _ = made("office")
    w = game.world
    prepared = scene.plan(w, {"people": [{"id": "driver", "name": "亨利", "role": "车夫",
                                          "goal": "把客人送到医院", "knows": ["黑车开向桥边"]}]}, ctx(game))
    assert scene.land(w, prepared.held)
    assert w.apply(AddFact(fact="only_warden", text="隐秘钟室哨兵词", known_by=["warden"])).ok
    SCRIPT.append({"text": "那辆黑车开向桥边去了。"})
    try:
        text = await game.cast.npc(w.st, "driver", "刚才有什么车经过？")
        assert text == "那辆黑车开向桥边去了。" and w.st.npc("driver").met
        supplied = json.dumps(SEEN[-1]["messages"], ensure_ascii=False)
        assert "黑车开向桥边" in supplied and "把客人送到医院" in supplied
        assert "隐秘钟室哨兵词" not in supplied
        assert not SCRIPT
    finally:
        await game.cast.close()
