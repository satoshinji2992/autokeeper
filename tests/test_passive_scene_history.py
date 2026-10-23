"""Passive knowledge is not an exclusive NPC action, including out-of-order parallel scenes."""

from __future__ import annotations

import pytest

from autokeeper.engine.state import GameState
from autokeeper.world import context as world_context
from autokeeper.world import scene
from autokeeper.world.ops import AddFact, Move, Occur

from .test_scene import made, sandbox  # noqa: F401


def context(start: int, end: int, **kw) -> scene.Context:
    return scene.Context(travel=lambda _a, _b: 5, elapsed=end - start, interval=(start, end), **kw)


async def test_passive_observation_does_not_reserve_a_later_npc_time_slot(fake):
    game, _ = made("square", fake=fake)
    w, start = game.world, game.state.minutes
    try:
        planned = scene.plan(w, {"here": ["preacher"], "observations": [
            {"witnesses": ["preacher"], "text": "来客说自己的手表停了。"},
        ]}, context(start, start + 20, ids="late-hearing"))
        assert not planned.problems and scene.land(w, planned.held)
        assert w.available_at("preacher") is None
        earlier = scene.plan(w, {"here": ["preacher"], "observations": [
            {"witnesses": ["preacher"], "text": "另一位来客说自己是来查报纸的。"},
        ]}, context(start, start + 5, ids="early-hearing"))
        assert not earlier.problems and scene.land(w, earlier.held)
        assert all("preacher" in e.witnesses and "preacher" not in e.participants for e in w.rec.events)
    finally:
        await game.close()


async def test_earlier_hearing_can_land_after_later_active_work_without_rewriting_it(fake):
    game, _ = made("square", fake=fake)
    w, start = game.world, game.state.minutes
    try:
        with w.at(start + 20):
            assert w.apply(Occur(id="actual-work", text="布道者完成了检查。", participants=["preacher"],
                                 place="square")).ok
        planned = scene.plan(w, {"here": ["preacher"], "observations": [
            {"witnesses": ["preacher"], "text": "来客在门口说要找一份旧报纸。"},
        ]}, context(start, start + 5))
        assert not planned.problems and scene.land(w, planned.held)
        assert w.available_at("preacher") == start + 20
        assert any(e.id == "ev:actual-work" and e.t == start + 20 for e in w.rec.events)
        assert w.place("preacher") == "square"
    finally:
        await game.close()


@pytest.mark.parametrize("during_move", [False, True])
async def test_later_arrival_or_transit_cannot_be_backfilled_as_a_witness(fake, during_move):
    game, _ = made("square", fake=fake)
    w, start = game.world, game.state.minutes
    try:
        assert w.apply(Move(entity="warden", to="office", actor="keeper")).ok
        with w.at(start + 20, start=start + 10 if during_move else start + 20):
            assert w.apply(Move(entity="warden", to="square", actor="keeper")).ok
        planned = scene.plan(w, {"here": ["preacher", "warden"], "observations": [
            {"witnesses": ["warden"], "text": "来客说了一句只有广场上的人能听到的话。"},
        ]}, context(start + 5 if not during_move else start + 11, start + 15))
        assert planned.problems and not planned.held
        assert not any("只有广场" in e.text for e in w.rec.events)
    finally:
        await game.close()


async def test_witness_who_arrived_at_the_end_only_can_hear_at_the_end(fake):
    game, _ = made("square", fake=fake)
    w, start = game.world, game.state.minutes
    try:
        assert w.apply(Move(entity="warden", to="office", actor="keeper")).ok
        with w.at(start + 5, start=start):
            assert w.apply(Move(entity="warden", to="square", actor="keeper")).ok
        planned = scene.plan(w, {"here": ["preacher", "warden"], "observations": [
            {"witnesses": ["warden"], "text": "守钟人抵达时听见来客自报姓名。"},
        ]}, context(start, start + 5))
        assert not planned.problems and scene.land(w, planned.held)
        assert w.available_at("warden") == start + 5
    finally:
        await game.close()


async def test_a_later_departed_witness_can_remember_what_was_heard_before_leaving(fake):
    game, _ = made("square", fake=fake)
    w, start = game.world, game.state.minutes
    try:
        with w.at(start + 20, start=start + 10):
            assert w.apply(Move(entity="preacher", to="office", actor="keeper")).ok
        planned = scene.plan(w, {"observations": [
            {"witnesses": ["preacher"], "text": "离开前，来客告诉布道者自己在等朋友。"},
        ]}, context(start, start + 5))
        assert not planned.problems and scene.land(w, planned.held)
        assert w.place("preacher") == "office" and w.available_at("preacher") == start + 20
        restored = GameState.model_validate_json(w.st.model_dump_json())
        heard = next(e for e in restored.record.events if "在等朋友" in e.text)
        assert heard.t == start + 5 and "preacher" in heard.witnesses
    finally:
        await game.close()


async def test_historical_npc_views_do_not_import_future_memories_events_or_new_facts(fake):
    game, _ = made("square", fake=fake)
    w, start = game.world, game.state.minutes
    try:
        with w.at(start + 20):
            assert w.apply(Occur(id="later-name", actor="keeper", witnesses=["preacher"],
                                 text="来客透露自己的化名是银鸟。", place="square", visibility="private")).ok
            assert w.apply(AddFact(fact="new-name", text="失踪者的新住址是松树街。", known_by=["preacher"],
                                  visibility="private")).ok
        w.st.minutes = start + 20
        assert "银鸟" in world_context.npc_brief(w, "preacher")
        assert "松树街" in world_context.npc_view(w, "preacher").text
        with w.at(start + 5):
            for text in (world_context.npc_brief(w, "preacher"), world_context.npc_view(w, "preacher").text):
                assert "银鸟" not in text and "松树街" not in text
    finally:
        await game.close()


async def test_keeper_retains_sourced_encounter_outcomes_after_many_later_memories(fake):
    from autokeeper.engine.encounter.model import EncounterSummary, EndRecord

    game, _ = made("square", fake=fake)
    w = game.world
    try:
        w.st.encounter_log.append(EncounterSummary(
            id="cabin-fight", mode="fight", place="tower", started_turn=1, rounds=3,
            participants=[w.st.investigator.id, "warden"],
            ended=EndRecord(round=3, how="won", text="调查员获胜，守钟人死亡。"),
            history=["第3轮：调查员开枪命中守钟人，造成致命伤。"],
        ))
        for n in range(20):
            w.apply(Occur(id=f"later:{n}", text="后来在广场闲谈。", place="square"))
        text = world_context.keeper_brief(w)
        assert "encounter:cabin-fight" in text and "地点：钟楼" in text
        assert "守钟人死亡" in text and "开枪命中" in text
        assert "不是NPC自动知情" in text and "不替他补写" in text
        assert "开枪命中" not in world_context.npc_brief(w, "preacher")
    finally:
        await game.close()
