"""Settled discoveries have recipients before the keeper writes; dice and scenes do not share them."""

from __future__ import annotations

import copy
import json

import pytest
from langgraph.types import Command

from autokeeper import config, paths
from autokeeper.agents.cast import Cast
from autokeeper.engine import audience, fog, presentation
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Clue, Event, Handout, Module
from autokeeper.net.hub import Hub
from autokeeper.rules.coc import Rules
from autokeeper.world import knowledge

from .test_keeper_tools_execution import final, role_calls, voice
from .test_scene import SCRIPT, SEEN, WRITER, sandbox  # noqa: F401

SECRET = "微弱的曲调里反复出现三个音，节奏与蓝色光斑同步。"
COMMON = "窗框被风吹得轻轻一响，桌上的灯仍亮着。"
SPOKEN = "风大了，你们留意窗户。"


def table(fake, monkeypatch, *, state=None, module=None, public=False):
    module = module or Module.load(paths.EXAMPLE)
    if state is None:
        lead = module.pregens[0].model_copy(deep=True)
        party = [lead, lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True),
                 lead.model_copy(update={"id": "chen", "name": "陈明"}, deep=True)]
        state = Game.new(module, party, human=[p.id for p in party])
        state.location, state.minutes = "tower", 20 * 60 + 20
        module.events, module.endings, module.foreshadow, module.agendas, module.showcases = [], [], [], [], []
        module.handouts.append(Handout(id="h_resonance", title="频率记录", text=SECRET, images=["resonance"]))
        module.art["resonance"] = "  ~~~ /  *  / ~~~  "
        module.clues.append(Clue(id="c_resonance", title="异响的节律", text=SECRET,
                                 where="tower", handout="h_resonance"))
    cast = Cast(config.Config(roles={r: WRITER for r in config.DEFAULT_ROLES}, minds=0, visual_limit=0,
                              imagegen=False, public_rolls=public), module)
    # The real Hub and Graph share exactly this module and persistent world.
    with monkeypatch.context() as patch:
        patch.setattr(Module, "load", lambda _: module)
        hub = Hub(paths.EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    hub.game.write_progress = hub.game.world_later = False
    hub.game.world.reviewing = False
    hub.clients = dict.fromkeys(("a", "b", "c", "watch"))
    hub.names = {cid: cid for cid in hub.clients}
    hub.sits = {state.investigators[0].id: "a", "lin": "b", "chen": "c"}
    hub.started.set()
    messages = []

    async def send(cid, message):
        messages.append((cid, copy.deepcopy(message)))

    monkeypatch.setattr(hub, "_send", send)
    monkeypatch.setattr(hub, "_save", lambda: None)
    hub.game.checkpoint = hub._save
    return hub, messages


async def run(hub, value):
    offers = []
    async for mode, chunk in turn_graph.stream(hub.graph, hub.game, value, hub.thread,
                                              stream_mode=["custom", "updates"]):
        if mode == "custom":
            await hub._event(chunk)
        elif "__interrupt__" in chunk:
            offers.extend(item.value for item in chunk["__interrupt__"])
    return offers


def wire(messages, client):
    return json.dumps([msg for cid, msg in messages if cid == client], ensure_ascii=False)


async def close(hub):
    await hub.game.close()
    await hub.cast.close()


@pytest.mark.parametrize("skill", ["聆听", "图书馆使用", "机械维修"])
@pytest.mark.parametrize("public", [False, True])
async def test_failed_then_critical_personal_check_does_not_share_results_but_keeps_scene_and_speech(
        fake, monkeypatch, skill, public):
    hub, messages = table(fake, monkeypatch, public=public)
    hub.m.events.append(Event(id="wind", title="起风", text="风吹动窗框。", when="D1 20:22",
                              location="tower", read_aloud=COMMON))
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡分辨窗边的节律", "target": "窗边的节律"}},
        {"needs_roll": True, "skill": skill, "minutes": 1},  # omitted scope is conservative, not broadcast
        final("你没能分辨出规律。"),
        {"action": {"kind": "investigate", "summary": "林秋分辨窗边的节律", "target": "窗边的节律"}},
        {"needs_roll": True, "skill": skill, "minutes": 1, "clues_on_success": ["c_resonance"]},
        voice("warden", "提醒在场者窗边起风了，不知道任何人的个人发现。"),
        {"text": SPOKEN},
        final(SECRET),
    ])
    SCRIPT[-1]["telling"].update(asides=[{"to": ["zhouheng", "lin", "chen"], "text": "灯罩晃了一下。"}],
                                 media=[{"ref": "image:resonance"}])
    fake.queue(88).queue(1)
    try:
        assert (await run(hub, {"input": "我分辨窗边的节律。"}))[0]["kind"] == "push"
        assert not await run(hub, Command(resume="drop"))
        hub.state.active = 1
        messages.clear()
        assert not await run(hub, {"input": "我也分辨窗边的节律。"}) and not SCRIPT and not fake.values
        assert SECRET in wire(messages, "b")
        assert SECRET not in wire(messages, "a") + wire(messages, "c") + wire(messages, "watch")
        for cid in ("a", "b", "c"):
            assert COMMON in wire(messages, cid) and SPOKEN in wire(messages, cid)
            assert "灯罩晃了一下" in wire(messages, cid)
        body = next(e for e in hub.state.log if e.kind == "keeper" and e.text == SECRET)
        assert body.audience == ["lin"] and hub.state.checks[-1].audience == ["lin"]
        assert knowledge.clue_known(hub.state, "c_resonance", "lin")
        assert not knowledge.clue_known(hub.state, "c_resonance", "zhouheng")
        assert presentation.current(hub.state, "lin") and not presentation.current(hub.state, "zhouheng")
        assert "c_resonance" in fog.personal(hub.m, hub.state, "lin").clues
        assert "c_resonance" not in fog.personal(hub.m, hub.state, "zhouheng").clues
        for cid in ("a", "c", "watch"):
            assert SECRET not in json.dumps(hub._welcome(cid), ensure_ascii=False)
        assert SECRET in json.dumps(hub._welcome("b"), ensure_ascii=False)
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 2
        assert len(role_calls("Dialogue")) == 1 and len(role_calls("HostedReply")) == 3
        prompt = str(role_calls("HostedReply")[-1]["messages"])
        assert "【正文接收者】林秋" in prompt and "【共同现场名单】" in prompt
        # Folding common history cannot turn the personal result into shared recollection.
        for n in range(50):
            hub.state.add("keeper", f"共同经过{n}。")
        shared = []

        async def chronicle(state, entries):
            shared.append(entries)
            return "众人留在钟楼。"

        monkeypatch.setattr(hub.cast, "chronicle", chronicle)
        hub.game.emit = hub._event
        await hub.game.remember()
        assert len(shared) == 1 and SECRET not in shared[0] and SECRET not in hub.state.memory
        restored = GameState.model_validate_json(hub.state.model_dump_json())
        assert SECRET in str([e.text for e in audience.log(restored, "lin")])
        assert SECRET not in str([e.text for e in audience.log(restored, "zhouheng")])
    finally:
        await close(hub)


async def test_a_common_visible_action_result_is_delivered_to_all_present_people(fake, monkeypatch):
    hub, messages = table(fake, monkeypatch)
    body = "你推开沉重的门，屋里的冷风涌向众人。"
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡推开沉重的门", "target": "沉重的门"}},
        {"needs_roll": True, "skill": "力量", "result_scope": "scene"},
        final(body),
    ])
    fake.queue(1)
    try:
        assert not await run(hub, {"input": "我推开门。"}) and not SCRIPT
        assert all(body in wire(messages, cid) for cid in ("a", "b", "c", "watch"))
        assert next(e for e in hub.state.log if e.kind == "keeper").audience is None
    finally:
        await close(hub)


async def test_a_second_person_can_discover_the_same_clue_then_public_testimony_can_share_it(fake, monkeypatch):
    hub, messages = table(fake, monkeypatch)
    for who in ("周衡", "林秋"):
        SCRIPT.extend([
            {"action": {"kind": "investigate", "summary": f"{who}读窗边的记录", "target": "窗边的记录"}},
            {"needs_roll": False, "auto_clues": ["c_resonance"]},
            final(SECRET),
        ])
    try:
        assert not await run(hub, {"input": "我读窗边的记录。"})
        assert not knowledge.clue_known(hub.state, "c_resonance", "lin")
        hub.state.active = 1
        messages.clear()
        assert not await run(hub, {"input": "我也读记录。"})
        assert SECRET in wire(messages, "b") and SECRET not in wire(messages, "c")
        assert hub.state.clues.count("c_resonance") == 1
        assert [knowledge.clue_known(hub.state, "c_resonance", who) for who in ("zhouheng", "lin", "chen")] == [True, True, False]
        assert "c_resonance" in str(role_calls("Ruling")[1]["messages"][-1])
        hub.m.npc("warden").clues.append("c_resonance")
        knowledge.seed(hub.m, hub.state, hub.game.world.rec)
        hub.game.world.rec.facts["clue:c_resonance"].known_by.append("warden")
        hub.state.active = 2
        SCRIPT.extend([
            {"action": {"kind": "talk", "summary": "陈明请老人说明记录", "npc": "warden", "speech": "这份记录是什么意思？"}},
            {"needs_roll": False},
            voice("warden", "向在场的人讲述你知道的窗边记录。"),
            {"text": SECRET, "told": ["c_resonance"]},
        ])
        messages.clear()
        assert not await run(hub, {"input": "老人家，这记录是什么意思？"}) and not SCRIPT
        assert all(SECRET in wire(messages, cid) for cid in ("a", "b", "c"))
        assert all(knowledge.clue_known(hub.state, "c_resonance", who) for who in ("zhouheng", "lin", "chen"))
        assert hub.state.clues.count("c_resonance") == 1
    finally:
        await close(hub)


async def test_a_personal_check_does_not_revoke_a_previously_shared_discovery(fake, monkeypatch):
    hub, messages = table(fake, monkeypatch)
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡当众展开记录", "target": "记录"}},
        {"needs_roll": False, "auto_clues": ["c_resonance"], "result_scope": "scene"},
        final(SECRET),
        {"action": {"kind": "investigate", "summary": "林秋独自辨认笔迹", "target": "笔迹"}},
        {"needs_roll": True, "skill": "侦查"},
        final("你认出某个字是后来补写的。"),
    ])
    fake.queue(1)
    try:
        assert not await run(hub, {"input": "我当众展开记录。"})
        hub.state.active = 1
        assert not await run(hub, {"input": "我辨认笔迹。"}) and not SCRIPT
        assert all(knowledge.clue_known(hub.state, "c_resonance", who) for who in ("zhouheng", "lin", "chen"))
        assert all("c_resonance" in fog.personal(hub.m, hub.state, who).clues for who in ("zhouheng", "lin", "chen"))
        assert SECRET in json.dumps(hub._welcome("c"), ensure_ascii=False)
        assert "某个字是后来补写" not in wire(messages, "a") + wire(messages, "c")
    finally:
        await close(hub)


@pytest.mark.parametrize("stage", ["before-delivery", "after-body"])
async def test_saved_personal_result_keeps_readers_and_media_when_reconnected(fake, monkeypatch, stage):
    hub, _messages = table(fake, monkeypatch)
    hubs = [hub]
    hub.state.active = 1
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "林秋独自辨认节律", "target": "窗边的记录"}},
        {"needs_roll": True, "skill": "聆听", "clues_on_success": ["c_resonance"]},
        final(SECRET),
    ])
    SCRIPT[-1]["telling"]["media"] = [{"ref": "image:resonance"}]
    fake.queue(1)
    saved = []

    def cut():
        receipt = hub.state.pending_turn.get("keeper", {})
        at = "text" in receipt and not receipt.get("body_started") if stage == "before-delivery" else receipt.get("body_ended")
        if at and not saved:
            saved.append(hub.state.model_dump_json())
            raise ConnectionError("个人结果交接断开")

    hub.game.checkpoint = cut
    try:
        with pytest.raises(ConnectionError, match="个人结果交接断开"):
            await run(hub, {"input": "我独自辨认节律。"})
        original = hub
        restored = GameState.model_validate_json(saved[0])
        hub, _messages = table(fake, monkeypatch, state=restored, module=original.m.model_copy(deep=True))
        hubs.append(hub)
        assert not await run(hub, {"input": "/retry"}) and not SCRIPT and not fake.values
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("HostedReply")) == 1
        assert len([e for e in hub.state.log if e.kind == "keeper" and e.text == SECRET]) == 1
        assert all(SECRET not in json.dumps(hub._welcome(cid), ensure_ascii=False) for cid in ("a", "c", "watch"))
        assert SECRET in json.dumps(hub._welcome("b"), ensure_ascii=False)
        assert presentation.current(hub.state, "lin") and not presentation.current(hub.state, "zhouheng")
        assert all(p["audience"] == ["lin"] for p in hub.state.presentations)
    finally:
        for current in hubs:
            await close(current)
