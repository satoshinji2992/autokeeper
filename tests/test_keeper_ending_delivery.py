"""A hosted ending delivers addressed observations without committing another scene."""

from __future__ import annotations

import copy
import json

import pytest

from autokeeper.engine import graph as turn_graph
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Ending

from .test_keeper_tools_execution import final, role_calls, shut, table, voice
from .test_net_private import private_hub as _private_hub
from .test_request_execution import run
from .test_scene import SCRIPT, sandbox  # noqa: F401

private_hub = _private_hub


@pytest.mark.parametrize("disconnect_at_media", [False, True])
async def test_hosted_ending_keeps_its_npc_and_private_deliveries_without_landing_another_scene(
        fake, private_hub, monkeypatch, disconnect_at_media):
    hub, messages = private_hub
    old_hub_game = hub.game
    game, graph, cast = table(fake, party=True)
    games, casts, emitted, saved = [old_hub_game, game], [cast], [], []
    st = game.state
    actor, start = st.investigator.id, st.minutes
    aside = "只有你注意到老人把一片纸放回衣袋。"
    public = "你和老人道别，楼内重新安静下来。"
    response = "好了，钟停了。这块擦手布给你。"
    gift, fake_gift = "擦手布", "凭空出现的结局赠礼"
    game.m.flags["unearned"] = "未曾发生的额外剧情事实"
    game.m.endings = [Ending(id="stopped", title="钟停了", condition="flag:bell_stopped", text="平静下来。")]
    ended = final(public, scene={
        "room": "office", "here": [], "flags": ["unearned"],
        "things": [{"name": fake_gift, "holder": actor}], "now": "未经执行的新场面",
    })
    ended["telling"].update(asides=[{"to": [actor], "text": aside}],
                            media=[{"ref": "handout:h_diary", "to": [actor]}])
    SCRIPT.extend([
        {"action": {"kind": "talk", "summary": "周衡请老人关停钟表", "npc": "warden", "speech": "请关停钟表。"}},
        {"needs_roll": True, "skill": "说服", "minutes": 1, "set_flags": ["bell_stopped"]},
        voice("warden", "关停请求已经成功，向来访者确认，并把擦手布递给他。"),
        {"text": response, "items": [{"name": gift, "to": actor}]},
        ended,
    ])
    fake.queue(1)
    record = turn_graph.record_event

    def observe(current, event):
        emitted.append(copy.deepcopy(event))
        record(current, event)

    monkeypatch.setattr(turn_graph, "record_event", observe)

    def disconnect():
        if game.state.presentations and not saved:
            saved.append(game.state.model_dump_json())
            raise ConnectionError("结局材料已登记，尚未投递")

    if disconnect_at_media:
        game.checkpoint = disconnect
    try:
        if disconnect_at_media:
            with pytest.raises(ConnectionError, match="结局材料已登记"):
                await run(graph, game, {"input": "请老人关停钟表。"})
            assert len(saved) == 1 and not SCRIPT
            state = GameState.model_validate_json(saved[0])
            assert state.ending == "stopped" and len(state.presentations) == 1
            await game.close()
            game, graph, cast = table(fake, module=game.m.model_copy(deep=True), state=state)
            games.append(game)
            casts.append(cast)
            _, offers = await run(graph, game, {"input": "/retry"})
        else:
            _, offers = await run(graph, game, {"input": "请老人关停钟表。"})
        st = game.state
        assert not offers and not SCRIPT and not st.pending_turn
        assert st.ending == "stopped" and st.turn == 1 and st.minutes == start + 1
        assert len(st.checks) == 1 and st.checks[0].passed and not fake.values
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("Dialogue")) == 1
        assert len(role_calls("HostedReply")) == 2
        assert [e.text for e in st.log if e.kind == "npc"] == [response]
        assert st.investigator.inventory.count(gift) == 1
        assert len([entity for entity in game.world.rec.entities.values() if entity.name == gift]) == 1
        assert st.location == "tower" and game.world.place("warden") == "tower"
        assert "unearned" not in st.flags and not st.ongoing
        assert fake_gift not in st.investigator.inventory
        assert not any(entity.name == fake_gift for entity in game.world.rec.entities.values())
        assert len([e for e in st.log if e.kind == "keeper" and e.text == public]) == 1
        notes = [e for e in st.log if e.kind == "keeper" and e.text == aside]
        assert len(notes) == 1 and notes[0].audience == [actor]
        paper = game.m.handout("h_diary")
        assert len(st.presentations) == 1 and st.presentations[0]["audience"] == [actor]
        assert sum(e.kind == "system" and e.text == f"收到材料：{paper.title}" for e in st.log) == 1
        assert sum(d.kind == "handout" and d.text == paper.title for d in st.deeds) == 1
        assert st.clues == [] and st.handouts == []  # addressed presentation does not grant a shared discovery

        hub.game, hub.cast, hub.m, hub.state = game, cast, game.m, st
        for event in emitted:
            if event["type"] in ("aside", "presentation"):
                assert event["audience"] == [actor]
                await hub._event(event)
        delivered = [(cid, msg) for cid, msg in messages
                     if msg.get("t") == "event" and msg["ev"]["type"] in ("aside", "presentation")]
        assert {cid for cid, _ in delivered} == {"a"}
        assert {msg["ev"]["type"] for _, msg in delivered} == {"aside", "presentation"}
        assert aside in json.dumps(hub._welcome("a")["log"], ensure_ascii=False)
        assert hub._welcome("a")["media"]["papers"]
        for cid in ("b", "c", "watch"):
            welcome = hub._welcome(cid)
            assert aside not in json.dumps(welcome, ensure_ascii=False)
            assert not welcome["media"]["papers"]  # the module's post-game debrief is a separate public resource
    finally:
        await shut(games, casts)
