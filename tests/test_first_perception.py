"""A character who steps in sees public conduct, not the player's concealed plan."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Initiative

from .test_harness import SCRIPT, SEEN, harness_sandbox  # noqa: F401
from .test_npc_acts import setup


@pytest.mark.parametrize("speech", ["", "您这口钟平常怎么修？"])
async def test_a_first_reply_never_receives_private_summary_as_missing_visible_conduct(fake, speech):
    game, _ = setup(fake)
    hidden = "偷偷翻查钟楼里的日记，若被发觉就谎称问路，绝不能透露要找失踪者的计划"
    action = Action(kind="investigate", summary=hidden, target="抽屉", speech=speech)
    game.state.pending_turn = {"input": hidden, "initiative": [
        Initiative(npc="warden", does="按自己守住钟楼的打算先询问来客", first=True).model_dump()]}
    SCRIPT.append({"text": "我这儿正忙，有事就在门口说。"})
    await game.prepare(action)
    assert len(SEEN) == 1
    sent = SEEN[0]["messages"][-1]["content"]
    assert hidden not in sent and "偷偷翻查" not in sent and "谎称问路" not in sent and "找失踪者" not in sent
    if speech:
        assert f"说：「{speech}」" in sent
    assert game.state.pending_turn["preparation"]["turn"]["npc_lines"] == [
        ["warden", "我这儿正忙，有事就在门口说。"]]
    await game.close()


async def test_a_visible_first_interruption_still_changes_what_the_arbiter_reads(fake):
    game, _ = setup(fake)
    hidden = "暗中找机会拿走日记，表面只碰一下钟旁的杆子"
    visible = "伸手靠近钟旁的长杆"
    action = Action(kind="investigate", summary=hidden, visible=visible, target="长杆")
    game.state.pending_turn = {"input": hidden, "initiative": [
        Initiative(npc="warden", does="看到有人伸手靠近长杆，先开口提醒别碰", first=True).model_dump()]}
    SCRIPT.append({"text": "别碰那根杆子！钟会停的。"})
    await game.prepare(action)
    assert len(SEEN) == 1
    sent = SEEN[0]["messages"][-1]["content"]
    assert f"正要：{visible}（还没做成）" in sent and hidden not in sent and "拿走日记" not in sent
    SCRIPT.append({"needs_roll": False})
    await game.rule(action)
    assert len(SEEN) == 2 and "needs_roll" in SEEN[-1]["schema"]["properties"]
    assert "别碰那根杆子！钟会停的。" in SEEN[-1]["messages"][-1]["content"]
    assert not fake.values
    await game.close()
