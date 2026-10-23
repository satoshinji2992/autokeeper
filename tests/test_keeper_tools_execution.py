"""Scripted keeper choices pass through real Cast, world operations and turn recovery."""

from __future__ import annotations

import copy
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper import config, paths
from autokeeper.agents.cast import Cast
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Event, Module
from autokeeper.rules.coc import Rules
from autokeeper.world import ops as O

from .test_net_private import private_hub as _private_hub
from .test_request_execution import run
from .test_scene import SCRIPT, SEEN, WRITER, sandbox  # noqa: F401

private_hub = _private_hub


def table(fake, *, module=None, state=None, party=False):
    if module is None:
        module = Module.load(paths.EXAMPLE)
        module.events, module.endings, module.foreshadow, module.agendas, module.showcases = [], [], [], [], []
    if state is None:
        first = module.pregens[0].model_copy(deep=True)
        investigators = [first]
        if party:
            investigators += [first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True),
                              first.model_copy(update={"id": "chen", "name": "陈明"}, deep=True)]
        state = Game.new(module, investigators, human=[i.id for i in investigators])
        state.location, state.minutes = "tower", 20 * 60 + 20
    cast = Cast(config.Config(roles={role: WRITER for role in config.DEFAULT_ROLES},
                              minds=0, visual_limit=0, imagegen=False), module)
    game = Game(module, state, cast, Rules(dice=fake))
    return game, turn_graph.build(game, InMemorySaver()), cast


def final(text="本段实际经过结束。", *, scene=None, continuation=None):
    telling = {"scene": {} if scene is None else scene, "text": text}
    if continuation is not None:
        telling["continuation"] = continuation
    return {"kind": "tell", "telling": telling}


def voice(npc, instruction, *, private=False):
    return {"kind": "npc", "npc": npc, "instruction": instruction, "private": private}


def role_calls(title):
    return [row for row in SEEN if row["schema"] and row["schema"].get("title") == title]


async def shut(games, casts):
    for game in games:
        await game.close()
    for cast in casts:
        await cast.close()


@pytest.mark.parametrize("speaker", [None, "warden", "preacher"])
async def test_postroll_keeper_can_discard_or_replace_old_detection_without_telling_an_npc_the_private_goal(fake, speaker):
    game, graph, cast = table(fake)
    if speaker == "preacher":
        assert game.world.apply(O.Move(entity="preacher", to="tower", actor="engine")).ok
    original = "我停在墙边装作整理衣服，悄悄查看门缝，不让别人察觉。"
    private_reason = "仅供主持：守钟人正专注齿轮背面的暗格，不知道来访者的隐蔽目的。"
    old_cue = "发现调查员暗中查看门缝，走过去质问"
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡不被察觉地查看门缝", "target": "门缝",
                    "visible": "站在墙边整理衣服"}, "initiative": [{"npc": "warden", "does": old_cue}]},
        {"needs_roll": True, "skill": "潜行", "minutes": 1, "why": "不被察觉地完成门边观察", "reason": private_reason},
    ])
    if speaker:
        SCRIPT.extend([voice(speaker, "你仍忙着手边的事，递出一块擦手布，问来访者是否需要擦手。"),
                       {"text": "手上沾油了吗？这块布给你。", "items": [{"name": "擦手布", "to": game.state.investigator.id}]}])
    SCRIPT.append(final("旁人继续忙自己的事，门边没有人追问。"))
    fake.queue(1)
    start = game.state.minutes
    try:
        events, offers = await run(graph, game, {"input": original})
        assert not offers and not SCRIPT and len(game.state.checks) == 1 and game.state.checks[0].passed
        assert game.state.minutes == start + 1
        keeper_calls = role_calls("HostedReply")
        assert len(keeper_calls) == (2 if speaker else 1)
        assert old_cue in str(keeper_calls[0]["messages"]) and private_reason in str(keeper_calls[0]["messages"])
        npc_calls = role_calls("Dialogue")
        assert len(npc_calls) == bool(speaker)
        for call in npc_calls:
            message = str(call["messages"])
            assert old_cue not in message and private_reason not in message and "周衡不被察觉地查看门缝" not in message
            assert "递出一块擦手布" in message
        assert len([e for e in events if e["type"] == "npc"]) == bool(speaker)
        assert game.state.investigator.inventory.count("擦手布") == bool(speaker)
        assert private_reason not in str([e for e in events if e["type"] in ("npc", "keeper_delta", "keeper_end")])
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("honest", [False, True])
async def test_actual_handover_and_real_or_false_accompaniment_reach_the_same_keeper_before_continuing(fake, honest):
    game, graph, cast = table(fake)
    who = game.state.investigator.id
    original = "请借我擦手布，也请陪我去广场。若愿意同行，我先等两分钟；否则等我决定。"
    claim = "布给你。好，我陪你去广场。"
    intent = "先敷衍来访者，自己留在钟楼。"
    answer = {"text": claim, "items": [{"name": "擦手布", "to": who}], "accompany": "square" if honest else ""}
    if not honest:
        answer["deceptions"] = [{"claim": "好，我陪你去广场。", "intent": intent,
                                 "motive": "钟楼必须有人看守。", "accompany": "square"}]
    control = ({"next": {"action": {"kind": "wait", "summary": "周衡在原处等两分钟"}}} if honest else
               {"pending": "擦手布已借到，同行仍按人物实际回应处理，等玩家决定下一步。"})
    SCRIPT.extend([
        {"action": {"kind": "talk", "summary": "周衡借擦手布并邀请守钟人同行", "npc": "warden",
                    "speech": "请借我擦手布，也请陪我去广场。", "approach": "说服"},
         "remaining": "若愿意同行先等两分钟，否则等玩家决定。"},
        {"needs_roll": True, "skill": "说服", "minutes": 1, "why": "借用擦手布", "reason": "成功只确定借用，同行尚未决定。"},
        voice("warden", "借用擦手布已经谈妥，请当场递给他；同行请求按你自己眼下真实打算回应。"),
        answer,
        final("擦手布已在周衡手里，老人仍在原处。", continuation=control),
    ])
    if honest:
        SCRIPT.extend([{"needs_roll": False, "minutes": 2}, final("两分钟过去，老人还在身旁。")])
    fake.queue(1)
    start = game.state.minutes
    try:
        _, offers = await run(graph, game, {"input": original})
        assert not offers and not SCRIPT and game.state.investigator.inventory.count("擦手布") == 1
        assert len(game.state.checks) == 1 and game.state.location == "tower"
        warden = game.world.entity("warden")
        assert (warden.following, warden.follow_to) == ((who, "square") if honest else ("", ""))
        calls = role_calls("ContinuedHostedReply")
        assert len(calls) == 2 and claim in str(calls[1]["messages"])
        assert "【配音实际执行回执】" in str(calls[1]["messages"]) and "擦手布" in str(calls[1]["messages"])
        assert len(role_calls("Plan")) == 1 and len(role_calls("Dialogue")) == 1
        assert game.state.turn == (2 if honest else 1) and game.state.minutes == start + (3 if honest else 1)
        assert (who in game.state.unfinished) is not honest
        if not honest:
            assert any(intent in f.text and f.kind == "secret" for f in game.world.rec.facts.values())
            assert intent not in str([e.text for e in game.state.log if e.kind in ("npc", "keeper")])
    finally:
        await shut([game], [cast])


@pytest.mark.parametrize("stage", ["before-call", "call-saved", "reply-saved", "shown-saved", "call-done", "final-saved"])
async def test_saved_voice_stages_do_not_repeat_models_transfer_check_time_or_an_acknowledged_notification(fake, monkeypatch, stage):
    game, graph, cast = table(fake)
    games, casts, saved, emitted = [game], [cast], [], []
    original = "我检查一下随身器材。"
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡检查随身器材", "target": "随身器材"}},
        {"needs_roll": True, "skill": "侦查", "minutes": 3},
        voice("warden", "看见他整理工具，拿一块擦手布给他。"),
        {"text": "拿着，擦擦手。", "items": [{"name": "擦手布", "to": game.state.investigator.id}]},
        final("器材整理好了。"),
    ])
    fake.queue(1)
    original_record = turn_graph.record_event

    def observe(g, event):
        emitted.append(copy.deepcopy(event))
        original_record(g, event)

    monkeypatch.setattr(turn_graph, "record_event", observe)
    start = game.state.minutes

    def disconnect():
        journal = game.state.pending_turn
        receipt = journal.get("keeper", {})
        calls = receipt.get("calls", [])
        replies = receipt.get("voice_turn", {}).get("replies", {})
        said = replies.get("keeper:0:warden", {})
        ready = {
            "before-call": journal.get("talk_done") and not calls,
            "call-saved": bool(calls) and not replies,
            "reply-saved": bool(said) and not said.get("shown"),
            "shown-saved": said.get("shown") and not calls[0].get("done"),
            "call-done": bool(calls) and calls[0].get("done") and "text" not in receipt,
            "final-saved": "text" in receipt and not receipt.get("landed"),
        }[stage]
        if ready and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError(f"断线：{stage}")

    game.checkpoint = disconnect
    try:
        with pytest.raises(RuntimeError, match=f"断线：{stage}"):
            await run(graph, game, {"input": original})
        assert len(saved) == 1
        restored = GameState.model_validate_json(saved[0])
        game, graph, cast = table(fake, module=game.m.model_copy(deep=True), state=restored)
        games.append(game)
        casts.append(cast)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert game.state.minutes == start + 3 and game.state.turn == 1 and len(game.state.checks) == 1
        assert not fake.values and game.state.investigator.inventory.count("擦手布") == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("Dialogue")) == 1
        assert len(role_calls("HostedReply")) == 2
        assert len([e for e in game.state.log if e.kind == "npc"]) == 1
        assert len([e for e in game.state.log if e.kind == "keeper" and e.text == "器材整理好了。"]) == 1
        assert len([e for e in emitted if e["type"] == "npc"]) == 1
    finally:
        await shut(games, casts)


async def test_private_tool_reply_and_document_reach_only_the_actor_in_real_events_and_hub_projection(fake, private_hub):
    hub, messages = private_hub
    old_game = hub.game
    game, graph, cast = table(fake, party=True)
    hub.game, hub.cast, hub.m, hub.state = game, cast, game.m, game.state
    actor = game.state.investigator.id
    secret = "只给你看，读完别声张。"
    game.state.npc("warden").unlocked.append("c_diary")
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡整理衣领"}},
        {"needs_roll": False, "minutes": 0},
        voice("warden", "私下请眼前来访者看你持有的日记。", private=True),
        {"text": secret, "media": [{"ref": "handout:h_diary"}]},
        final("老人收好手边的东西。"),
    ])
    try:
        events, offers = await run(graph, game, {"input": "我整理一下衣领。"})
        assert not offers and not SCRIPT
        npc = next(e for e in events if e["type"] == "npc")
        media = next(e for e in events if e["type"] == "presentation")
        assert npc["audience"] == media["audience"] == [actor]
        assert next(e for e in game.state.log if e.kind == "npc").audience == [actor]
        recorded = [e for e in game.world.rec.events if secret in e.text]
        assert recorded and all(e.visibility == "private" for e in recorded)
        assert game.state.clues == [] and game.state.handouts == []
        await hub._event(npc)
        await hub._event(media)
        private_frames = [(cid, msg) for cid, msg in messages
                          if msg.get("t") == "event" and msg["ev"]["type"] in ("npc", "presentation")]
        assert private_frames and {cid for cid, _ in private_frames} == {"a"}
        assert secret in json.dumps(hub._welcome("a"), ensure_ascii=False)
        paper = game.m.handouts[0].text.strip()
        for cid in ("b", "c", "watch"):
            assert secret not in json.dumps(hub._welcome(cid), ensure_ascii=False)
            assert paper not in json.dumps(hub._welcome(cid), ensure_ascii=False)
        assert secret not in json.dumps(hub._view(), ensure_ascii=False)
    finally:
        await shut([old_game, game], [cast])


async def test_a_legal_unrolled_daily_conversation_keeps_the_existing_reply_shortcut(fake):
    game, graph, cast = table(fake)
    SCRIPT.extend([
        {"action": {"kind": "daily", "casual": True, "summary": "周衡向守钟人问好", "npc": "warden", "speech": "晚上好。"}},
        {"text": "晚上好，外头冷吧。"},
    ])
    try:
        events, offers = await run(graph, game, {"input": "我向老人说晚上好。"})
        assert not offers and not SCRIPT and not game.state.checks
        assert len(role_calls("Dialogue")) == 1 and "keeper" not in game.state.usage and not role_calls("Ruling")
        assert any(e["type"] == "dialogue_end" for e in events) and not any(e["type"] == "keeper_delta" for e in events)
    finally:
        await shut([game], [cast])


async def test_a_flag_following_stage_resumes_its_voice_and_uses_its_actual_pending_instead_of_the_old_next(fake):
    game, graph, cast = table(fake)
    games, casts, saved = [game], [cast], []
    game.m.flags["tools_ready"] = "器材已经实际整理完毕"
    game.m.events = [Event(id="knocking", title="门口来人", when="flag:tools_ready",
                          text="楼梯口有人敲门。", read_aloud="楼梯口传来敲门声。")]
    original = "先检查器材，然后去广场。"
    SCRIPT.extend([
        {"action": {"kind": "investigate", "summary": "周衡检查器材", "target": "随身器材"}, "remaining": "然后去广场。"},
        {"needs_roll": False, "minutes": 1},
        final("你把器材整理好。", scene={"flags": ["tools_ready"]}, continuation={
            "next": {"action": {"kind": "move", "summary": "周衡前往广场", "destination": "square"}}}),
        voice("warden", "听见楼梯口的敲门声，提醒来访者门口有人。"),
        {"text": "门口有人，你要现在出去看看吗？"},
        final("老人停在门边，等着你的决定。", continuation={"pending": "先决定如何回应门口来人，再去广场。"}),
    ])

    def disconnect():
        following = game.state.pending_turn.get("keeper", {}).get("following_keeper", {})
        calls = following.get("calls", [])
        if calls and calls[0].get("done") and not saved:
            saved.append(game.state.model_dump_json())
            raise RuntimeError("事件配音已结算后断线")

    game.checkpoint = disconnect
    start = game.state.minutes
    try:
        with pytest.raises(RuntimeError, match="事件配音已结算后断线"):
            await run(graph, game, {"input": original})
        assert len(saved) == 1
        module = game.m.model_copy(deep=True)
        state = GameState.model_validate_json(saved[0])
        game, graph, cast = table(fake, module=module, state=state)
        games.append(game)
        casts.append(cast)
        _, offers = await run(graph, game, {"input": "/retry"})
        assert not offers and not SCRIPT and not game.state.pending_turn
        assert game.state.location == "tower" and game.state.minutes == start + 1 and game.state.turn == 1
        assert game.state.fired.count("knocking") == 1
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("Dialogue")) == 1
        assert len([e for e in game.state.log if e.kind == "npc"]) == 1
        assert "决定如何回应" in game.state.unfinished[game.state.investigator.id]["remaining"]
    finally:
        await shut(games, casts)


@pytest.mark.parametrize("with_voice", [False, True])
@pytest.mark.parametrize("body", ["老人把工具摆回原处。", ""])
async def test_graph_and_real_story_blocks_place_tool_speech_before_one_final_keeper_group(fake, with_voice, body):
    from textual.app import App, ComposeResult

    from autokeeper.tui.widgets.panels import StoryLog

    class StoryApp(App):
        def compose(self) -> ComposeResult:
            yield StoryLog()

    game, graph, cast = table(fake)
    line = "这几件工具平日就放在这里。"
    instruction = "低头看手边工具，随口提醒来访者它们平日的位置。"
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡整理工具"}},
        {"needs_roll": False, "minutes": 0},
    ])
    if with_voice:
        SCRIPT.extend([voice("warden", instruction), {"text": line}])
    SCRIPT.append(final(body))
    try:
        events, offers = await run(graph, game, {"input": "我整理一下工具。"})
        assert not offers and not SCRIPT
        shown = [e for e in events if e["type"] in ("npc", "keeper_start", "keeper_delta", "keeper_end")]
        expected = (["npc"] if with_voice else []) + ["keeper_start", "keeper_delta", "keeper_end"]
        assert [e["type"] for e in shown] == expected
        assert [e["text"] for e in shown if e["type"] == "keeper_delta"] == [body]
        assert shown[-1]["text"] == body and instruction not in json.dumps(shown, ensure_ascii=False)
        assert len(role_calls("HostedReply")) == (2 if with_voice else 1)

        app = StoryApp()
        async with app.run_test() as pilot:
            story = app.query_one(StoryLog)
            for event in shown:
                if event["type"] == "npc":
                    story.add("npc", event["text"], who=event["name"])
                elif event["type"] == "keeper_start":
                    story.begin_keeper()
                elif event["type"] == "keeper_delta":
                    story.feed(event["text"])
                else:
                    story.end_keeper(event["text"])
            await pilot.pause()
            blocks = list(story.query(".entry"))
            assert ["npc" if block.has_class("npc") else "keeper" for block in blocks] == (
                (["npc"] if with_voice else []) + ["keeper"])
            assert blocks[-1].render().plain == body
            if with_voice:
                assert line in blocks[0].render().plain
                if body:
                    assert body not in blocks[0].render().plain
            assert instruction not in "".join(block.render().plain for block in blocks)
    finally:
        await shut([game], [cast])
