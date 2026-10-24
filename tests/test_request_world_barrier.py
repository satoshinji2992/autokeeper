"""Prepared arrivals settle before the KP chooses whether a compound request continues.

There is no post-narration NPC worker or second world-barrier narration. The first
actual scene report already contains what happened during the player's action.
"""

from __future__ import annotations

import pytest

from autokeeper.engine.state import GameState
from autokeeper.world import ops as O
from autokeeper.world.record import Step

from .test_keeper_backstage_runtime import assert_no_independent_world_model, table
from .test_keeper_memory_runtime import words
from .test_keeper_tools_execution import final, role_calls, shut, voice
from .test_request_execution import run
from .test_scene import SCRIPT, sandbox  # noqa: F401

HELP = "布道者当面喊：救救我，有人在追我！先听我说！"
PRIVATE = "布道者在报社核对私人账目。"
PENDING = "先听布道者解释求救，再决定是否继续等到20:35。"
ORIGINAL = "先向守钟人确认时间，没有新情况就等到20:35；有人求救先停下来听。"
FIRST_BODY = "老人告诉了你时间，布道者赶到门前呼救。"


def setup(fake, *, private=False, state=None, module=None):
    game, graph, cast = table(fake, state=state, module=module)
    if state is not None:
        return game, graph, cast
    w = game.world
    w.rec.simulated = game.state.minutes
    target = "office" if private else "tower"
    assert w.apply(O.Enlist(npc="preacher", goals=["先把眼前的事处理好"], actor="engine")).ok
    assert w.apply(O.SetPlan(npc="preacher", steps=[Step(
        id="arrive", text="去报社处理私事" if private else "跑上钟楼求救", due=w.now + 1,
        achieved=f"at:preacher:{target}", meet=False,
        ops=[{"kind": "move", "entity": "preacher", "to": target, "visibility": "private" if private else "public"},
             {"kind": "occur", "place": target, "text": PRIVATE if private else HELP,
              "visibility": "private" if private else "public"}],
    )], actor="engine")).ok
    return game, graph, cast


def script(*, private=False, minutes=5):
    SCRIPT.extend([
        {"action": {"kind": "talk", "summary": "周衡向守钟人确认时间", "npc": "warden", "speech": "能确认一下时间吗？"},
         "remaining": "无新情况就等到20:35；有人求救先停下来听。"},
        {"needs_roll": True, "skill": "说服", "minutes": minutes},
        voice("warden", "请按刚才的实际检定回答调查员询问的时间。"),
        {"text": "现在是二十点二十五分。"},
        final("老人告诉了你时间。" if private else FIRST_BODY,
              continuation={"next": {"action": {"kind": "wait", "summary": "周衡原地等到20:35"}}} if private
              else {"pending": PENDING}),
    ])
    if private:
        SCRIPT.extend([{"needs_roll": False, "minutes": 15 - minutes, "rest": True}, final("钟面走到了二十点三十五分。")])


def assert_not_repeated(game):
    st = game.state
    assert len(st.checks) == 1 and st.checks[0].passed and st.checks[0].turn == 1
    assert [entry.text for entry in st.log if entry.kind == "player"] == [ORIGINAL]
    assert len([entry for entry in st.log if entry.kind == "dice"]) == 1
    assert len(role_calls("Plan")) == len(role_calls("Dialogue")) == 1
    assert len([entry for entry in st.log if entry.kind == "npc"]) == 1
    assert_no_independent_world_model(game)


async def test_visible_prepared_arrival_is_in_the_first_report_and_stops_the_old_wait(fake):
    game, graph, cast = setup(fake)
    start = game.state.minutes
    script()
    fake.queue(1)
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        st = game.state
        assert not offers and not SCRIPT and not st.pending_turn and not fake.values
        assert st.turn == 1 and st.minutes == start + 5 and game.world.place("preacher") == "tower"
        assert st.unfinished[st.investigator.id]["remaining"] == PENDING
        calls = role_calls("ContinuedHostedReply")
        assert len(calls) == 2 and HELP in words(calls[0]["messages"])
        assert len(role_calls("Ruling")) == 1
        assert len([event for event in game.world.rec.events if event.text == HELP]) == 1
        assert_not_repeated(game)
    finally:
        await shut([game], [cast])


async def test_private_prepared_work_does_not_add_a_second_telling_or_interrupt_the_old_wait(fake):
    game, graph, cast = setup(fake, private=True)
    start = game.state.minutes
    script(private=True)
    fake.queue(1)
    try:
        _, offers = await run(graph, game, {"input": ORIGINAL})
        st = game.state
        assert not offers and not SCRIPT and not st.pending_turn and not st.unfinished and not fake.values
        assert st.turn == 2 and st.minutes == start + 15 and game.world.place("preacher") == "office"
        assert len(role_calls("ContinuedHostedReply")) == 2 and len(role_calls("HostedReply")) == 1
        assert len(role_calls("Ruling")) == 2
        assert len([event for event in game.world.rec.events if event.text == PRIVATE]) == 1
        assert PRIVATE not in "\n".join(entry.text for entry in st.log if entry.kind in {"keeper", "npc"})
        assert_not_repeated(game)
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("cut", ["prepared-work", "before-keeper", "final-saved", "final-landed"])
async def test_inline_world_and_keeper_receipts_resume_without_repeating_arrival_or_original_request(fake, cut):
    game, graph, cast = setup(fake)
    games, casts, saved = [game], [cast], []
    start = game.state.minutes
    script()
    fake.queue(1)

    def disconnect():
        journal = game.state.pending_turn
        receipt = journal.get("keeper", {})
        happened = any(event.text == HELP for event in game.world.rec.events)
        ready = {
            "prepared-work": happened and journal.get("stage") == "resolve",
            "before-keeper": journal.get("stage") == "narrate" and not receipt,
            "final-saved": "text" in receipt and not receipt.get("landed"),
            "final-landed": receipt.get("landed"),
        }[cut]
        if ready and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError(f"scripted inline world interrupted: {cut}")

    game.checkpoint = disconnect
    try:
        with pytest.raises(RuntimeError, match="scripted inline world interrupted"):
            await run(graph, game, {"input": ORIGINAL})
        assert len(saved) == 1 and not fake.values
        state = GameState.model_validate_json(saved[0])
        module = game.m.model_copy(deep=True)
        await shut([game], [cast])
        game, graph, cast = setup(fake, module=module, state=state)
        games.append(game)
        casts.append(cast)
        _, offers = await run(graph, game, {"input": "/retry"})
        st = game.state
        assert not offers and not SCRIPT and not st.pending_turn and not fake.values
        assert st.turn == 1 and st.minutes == start + 5 and game.world.place("preacher") == "tower"
        assert st.unfinished[st.investigator.id]["remaining"] == PENDING
        assert len(role_calls("Ruling")) == 1 and len(role_calls("ContinuedHostedReply")) == 2
        assert len([event for event in game.world.rec.events if event.text == HELP]) == 1
        assert len([entry for entry in st.log if entry.kind == "keeper" and entry.text == FIRST_BODY]) == 1
        assert_not_repeated(game)
    finally:
        await shut(games, casts)
