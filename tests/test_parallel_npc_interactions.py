"""An NPC's already-committed time is coordinated before approving another social check."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.agents.cast import Action, KeeperCall, Narration, Ruling
from autokeeper.engine import graph
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import Move, Occur

from .test_engine import FakeCast
from .test_party_places import made

THREAD = {"configurable": {"thread_id": "parallel-npc"}}


class Conversations(FakeCast):
    def __init__(self):
        first = Action(kind="talk", summary="周衡先核对记录", npc="warden", speech="请核对记录。")
        second = Action(kind="talk", summary="林秋请对方读出日期", npc="warden", speech="请读出那一行。", approach="说服")
        super().__init__({"先问": (first, Ruling(needs_roll=False, minutes=10)),
                          "再问": (second, Ruling(needs_roll=True, skill="说服", difficulty="困难", minutes=2,
                                               why="让对方读出记录的日期"))})
        self.cursors = []

    async def arbiter(self, state, action, at=None, **kwargs):
        self.cursors.append(self.world.st.round_elapsed.get(state.investigator.id, 0))
        return await super().arbiter(state, action, at, **kwargs)

    async def npc(self, state, npc_id, message):
        self.npc_calls.append((npc_id, message))
        return "我现在翻到这一行：三月二日，名字后面是一个叉。"

    async def keeper(self, state, report, on_delta=None, lore="", *, voices=None, check=None, fix=""):
        self.reports.append(report)
        if voices is not None and self.voice_script:
            call = self.voice_script.pop(0)
            if call.npc in voices:
                return Narration(text="", call=call)
        here = self.world.present(state.location)
        scene = {"here": here, "observations": [{"witnesses": here, "text": "来客请对方核对记录，对方当面读出了日期。"}]}
        assert check is not None and check(scene)
        return Narration(text="对方当面读出了日期和记号。", scene=scene)


def setup(fake):
    game, _old, events = made(fake)
    cast = Conversations()
    game.cast, cast.world = cast, game.world
    game.table_turn_based = True
    assert game.world.apply(Move(entity="warden", to="square", actor="keeper")).ok
    game.begin_round()
    return game, cast, events


async def ask(game, cast, text, *, by_hand=False):
    if text == "再问":
        cast.voice_script.append(KeeperCall(npc="warden", instruction="兑现已成功的请求，读出日期。"))
    events = []
    async for mode, chunk in graph.build(game, InMemorySaver()).astream(
            {"input": text, "by_hand": by_hand}, THREAD, stream_mode=["custom", "updates"]):
        if mode == "custom":
            events.append(chunk)
        else:
            assert "__interrupt__" not in chunk
    return events


async def test_two_players_can_each_receive_the_same_npcs_actual_post_roll_reply(fake):
    game, cast, events = setup(fake)
    st, base = game.state, game.state.minutes
    try:
        await game.arrange_party([("zhouheng", "先问"), ("lin", "再问")])
        events = await ask(game, cast, "先问")
        st.active = 1
        fake.queue(12)
        events += await ask(game, cast, "再问")
        assert cast.cursors == [0, 10]  # the second arbiter sees the actual available conversation slot
        assert st.round_elapsed == {"zhouheng": 10, "lin": 12} and st.minutes == base + 12
        assert len(cast.npc_calls) == 2
        assert sum(e["type"] == "npc" and "三月二日" in e["text"] for e in events) == 2
        assert st.checks[-1].passed and not fake.values
        assert any("等候了" in report for report in cast.reports)
        assert any("对方当面读出了日期" in e.text and e.t == base + 12 for e in game.world.rec.events)
    finally:
        await game.close()


async def test_a_later_actual_scene_witness_is_aligned_before_the_check_too(fake):
    game, cast, _events = setup(fake)
    base = game.state.minutes
    try:
        with game.world.at(base + 10):
            assert game.world.apply(Occur(text="布道者完成刚才的交谈。", place="square", participants=["preacher"])).ok
        game.state.minutes = base + 10
        fake.queue(12)
        await ask(game, cast, "再问")
        assert cast.cursors == [10] and len(cast.npc_calls) == 1
        assert game.state.round_elapsed == {"zhouheng": 12}
        assert game.world.available_at("preacher") == base + 10  # the later passive observation is not work
    finally:
        await game.close()


async def test_a_targets_committed_later_arrival_is_reached_before_the_social_check(fake):
    game, cast, events = setup(fake)
    base = game.state.minutes
    try:
        assert game.world.apply(Move(entity="warden", to="office", actor="keeper")).ok
        with game.world.at(base + 10, start=base):
            assert game.world.apply(Move(id="later-actual-arrival", entity="warden", to="square", actor="keeper")).ok
        game.state.minutes = base + 10
        assert game.world.place_at("warden", base + 5) == ""
        fake.queue(12)
        events = await ask(game, cast, "再问")
        assert cast.cursors == [10] and len(cast.npc_calls) == 1
        assert game.state.round_elapsed == {"zhouheng": 12}
        assert any(e["type"] == "npc" and "三月二日" in e["text"] for e in events)
    finally:
        await game.close()


async def test_a_departed_target_is_missing_before_any_social_die_is_offered(fake):
    game, cast, events = setup(fake)
    try:
        assert game.world.apply(Move(entity="warden", to="office", actor="keeper")).ok
        fake.queue(12)
        events = await ask(game, cast, "再问", by_hand=True)
        assert not cast.ruled and not cast.npc_calls and not game.state.checks
        assert list(fake.values) == [12]
        assert not any(e["type"] == "dice" for e in events)
    finally:
        await game.close()


async def test_waiting_receipt_survives_reload_without_repeating_the_wait_or_the_social_roll(fake):
    game, cast, _events = setup(fake)
    games, saved = [game], []
    base = game.state.minutes
    try:
        await ask(game, cast, "先问")
        game.state.active = 1

        def disconnect():
            receipt = game.state.pending_turn.get("interaction", {})
            if receipt.get("wait"):
                saved.append(game.state.model_dump_json())
                raise ConnectionError("交谈时段已保存，尚未裁定")

        game.checkpoint = disconnect
        with pytest.raises(ConnectionError):
            await ask(game, cast, "再问")
        state = GameState.model_validate_json(saved[-1])
        assert state.pending_turn["interaction"]["wait"] == 10 and state.round_elapsed["lin"] == 10
        resumed = Game(game.m.model_copy(deep=True), state, cast, Rules(dice=fake))
        resumed.table_turn_based = True
        games.append(resumed)
        fake.queue(12)
        await graph.build(resumed, InMemorySaver()).ainvoke({"input": "/retry"}, THREAD)
        assert state.round_elapsed == {"zhouheng": 10, "lin": 12} and state.minutes == base + 12
        assert len(cast.npc_calls) == 2 and len(state.checks) == 1 and not fake.values
    finally:
        for instance in games:
            await instance.close()
