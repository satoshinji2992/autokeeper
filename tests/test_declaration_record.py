"""A queued action and its sealed batch share one accepted declaration record."""

from autokeeper.engine.scenes import SceneRuntime
from autokeeper.engine.state import GameState
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_party_arrangement import PartyCast, make_hub


async def test_replacing_even_the_same_text_creates_one_new_version(fake):
    hub = make_hub(fake)
    try:
        hub._accept_declaration("zhouheng", "检查门锁")
        first = hub.state.declarations[0].model_copy(deep=True)
        hub.state.minutes += 7
        hub._accept_declaration("zhouheng", "检查门锁")
        assert len(hub.state.declarations) == 1
        second = hub.state.declarations[0]
        assert second.id != first.id and second.at == first.at + 7
        assert hub.queued_declarations() == [("zhouheng", "检查门锁")]
        assert "declarations" not in hub._view("lin")
    finally:
        await hub.game.close()


async def test_seal_restore_and_encounter_deferral_retain_the_same_id_and_time(fake):
    hub = make_hub(fake)
    restored = None
    try:
        hub._accept_declaration("zhouheng", "看看旧报纸")
        accepted = hub.state.declarations[0].model_copy(deep=True)
        await hub.game.arrange_party([("zhouheng", accepted.text)])
        hub._freeze_declarations()
        step = hub.state.round_plan["steps"][0]
        assert step["declaration"] == accepted.id and "received" not in step
        assert not hub.queued_declarations()
        saved = GameState.model_validate_json(hub.state.model_dump_json())
        restored = Hub(EXAMPLE, state=saved, cast=PartyCast(), rules=Rules(dice=fake))
        assert not restored.early
        assert restored.state.declarations[0].id == accepted.id
        assert restored.game.pending_party() == [(accepted.seat, accepted.text)]
        restored._defer_declarations([(accepted.seat, accepted.text)])
        queued = restored.state.declarations
        assert len(queued) == 1 and queued[0] == accepted
        assert restored.early == [(accepted.seat, accepted.text)]
        assert restored.game.pending_party() is None
    finally:
        await hub.game.close()
        if restored is not None:
            await restored.game.close()


async def test_freezing_twice_never_creates_a_second_declaration(fake):
    hub = make_hub(fake)
    try:
        hub._accept_declaration("zhouheng", "看看旧报纸")
        await hub.game.arrange_party([("zhouheng", "看看旧报纸")])
        hub._freeze_declarations()
        first = hub.state.declarations[0].model_dump()
        hub.state.minutes += 5
        hub._freeze_declarations()
        assert [row.model_dump() for row in hub.state.declarations] == [first]
    finally:
        await hub.game.close()


async def test_seal_checkpoint_already_has_the_declaration_reference(fake):
    hub = make_hub(fake)
    snapshots = []
    try:
        hub._accept_declaration("zhouheng", "看看旧报纸")
        accepted = hub.state.declarations[0].model_copy(deep=True)
        hub.game.checkpoint = lambda: snapshots.append(GameState.model_validate_json(hub.state.model_dump_json()))
        await hub.game.arrange_party([(accepted.seat, accepted.text)])
        assert snapshots
        saved = snapshots[0]
        row = saved.declarations[0]
        assert row.id == accepted.id and row.at == accepted.at
        assert row.batch == saved.round_plan["id"]
        assert saved.round_plan["steps"][0]["declaration"] == row.id
    finally:
        await hub.game.close()


async def test_completed_batch_removes_its_declarations_but_preserves_unstarted_queue(fake):
    hub = make_hub(fake)
    try:
        hub.sits = {"zhouheng": "a", "lin": "b"}
        hub._accept_declaration("zhouheng", "看看旧报纸")
        hub._accept_declaration("lin", "下一轮看窗户")
        queued = hub.state.declarations[-1].model_copy(deep=True)
        hub.pool = [("zhouheng", "看看旧报纸")]
        hub.early = [("lin", queued.text)]
        await hub._settle()
        assert hub.state.declarations == [queued]
        assert hub.queued_declarations() == hub.early
        assert not hub.state.round_plan
    finally:
        await hub.game.close()


async def test_initial_scene_split_transfers_each_original_once_to_its_owner(fake):
    hub = make_hub(fake)
    try:
        hub._accept_declaration("zhouheng", "看看旧报纸")
        hub._accept_declaration("lin", "查看广场")
        original = {row.id: row.model_copy(deep=True) for row in hub.state.declarations}
        hub.state.positions = {"zhouheng": "office", "lin": "square"}

        async def emit(ident, event):
            pass

        runtime = SceneRuntime(hub.state, hub.m, lambda ident: PartyCast(), lambda ident: Rules(dice=fake), emit)
        rows = [row for scene in runtime.scenes.values() for row in scene.declarations]
        assert not hub.state.declarations
        assert {row.id: row for row in rows} == original and len(rows) == 2
        for scene in runtime.scenes.values():
            assert all(row.seat in scene.members for row in scene.declarations)
    finally:
        await hub.game.close()
