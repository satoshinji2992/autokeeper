"""A split table carries its experience, not another location's current stage."""

from __future__ import annotations

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Turn

from .test_request_execution import module
from .test_scene_runtime import made


async def test_split_retains_the_current_stage_only_at_its_actual_location(fake):
    runtime, _, _ = made(fake)
    try:
        runtime.state.positions["lin"] = "tower"
        original, = runtime.reconcile()
        source = runtime.scenes[original]
        view = runtime.state_view(original)
        shared = view.add("keeper", "两人一起在钟楼听见钟声。", who="守秘人")
        source.transcripts["keeper"] = [{"role": "assistant", "content": "旧主持会话"}]
        source.memory = "原先两人在钟楼。"

        # One person's last resolved action changed the source's focus before regrouping.
        runtime.state.positions["zhouheng"] = "square"
        source.place = "square"
        source.ongoing = "广场的摊主正等周衡答话。"
        source.journey = "office"
        runtime.reconcile()
        moved = runtime.state_view(runtime.for_member("zhouheng"))
        stayed = runtime.state_view(runtime.for_member("lin"))

        assert moved.ongoing == "广场的摊主正等周衡答话。" and moved.journey == "office"
        assert stayed.ongoing == "" and stayed.journey == ""
        assert shared in moved.log and shared in stayed.log
        assert not moved.transcripts and not stayed.transcripts
        assert not moved.memory and not stayed.memory
        assert not moved.held and not stayed.held
    finally:
        await runtime.close()


async def test_rejoining_does_not_restage_the_place_someone_just_left(fake):
    runtime, _, _ = made(fake)
    try:
        departing = runtime.scenes[runtime.for_member("zhouheng")]
        destination = runtime.scenes[runtime.for_member("lin")]
        departing.ongoing = "钟楼的守钟人正在检查齿轮。"
        departing.journey = "office"
        destination.ongoing = "广场的摊主正给林秋找零钱。"
        source_ids = {departing.id, destination.id}

        runtime.state.positions["zhouheng"] = "square"
        merged, = runtime.reconcile()
        view = runtime.state_view(merged)
        assert view.ongoing == "广场的摊主正给林秋找零钱。"
        assert view.journey == ""
        assert source_ids.isdisjoint({merged})
        assert all(runtime.scenes[ident].closed for ident in source_ids)
    finally:
        await runtime.close()


async def test_room_changes_keep_the_same_buildings_current_stage(fake):
    m = module()
    m.location("tower").within = None
    m.location("office").within = "tower"
    runtime, _, _ = made(fake, m=m)
    try:
        upstairs = runtime.scenes[runtime.for_member("zhouheng")]
        upstairs.place = "office"
        upstairs.ongoing = "楼上有人扶着档案室的门，等周衡进来。"
        upstairs.journey = "office"
        runtime.state.positions.update(zhouheng="office", lin="office")

        joined, = runtime.reconcile()
        assert runtime.scenes[joined].region == "tower"
        assert runtime.state_view(joined).ongoing == upstairs.ongoing
        assert runtime.state_view(joined).journey == "office"
    finally:
        await runtime.close()


async def test_keeper_receives_original_words_separately_from_the_generated_action(fake):
    runtime, _, _ = made(fake)
    ident = runtime.for_member("zhouheng")
    game = runtime.game(ident)
    try:
        game.state.pending_turn = {"input": "检查尸体"}
        action = Action(kind="move", summary="周衡前往报社检查尸体", destination="office")
        await game.narrate(Turn(action=action, ruling=Ruling(needs_roll=False, reason="移动")))
        report = game.cast.reports[-1]
        assert "【玩家本次原话，供核对行动范围】检查尸体" in report
        assert f"行动意图（是否达成以下列结算为准）：{action.summary}" in report
        assert "【本段行动者】周衡（zhouheng" in report
        # The data contract exposes the discrepancy; this is not a model-compliance test.
        assert runtime.state.positions["zhouheng"] == "tower"
    finally:
        await runtime.close()
