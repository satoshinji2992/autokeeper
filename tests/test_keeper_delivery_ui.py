"""Cached final tellings join the real network backlog and live story without duplicate blocks."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from autokeeper.engine import graph as turn_graph
from autokeeper.engine.state import GameState, LogEntry
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.net import NetGameScreen, table_module
from autokeeper.tui.widgets.panels import StoryLog

from .test_keeper_tools_execution import final, role_calls, shut, table
from .test_net_private import private_hub as _private_hub
from .test_scene import SCRIPT, sandbox  # noqa: F401

private_hub = _private_hub
BODY = "器材已经整理妥当，擦手布折好放在工具旁。"
CONFIG = {"configurable": {"thread_id": "keeper-delivery-ui"}}


class Link:
    host = False

    async def close(self) -> None:
        return None


async def play(graph, game, hub, value: dict) -> None:
    async for mode, event in turn_graph.stream(graph, game, value, CONFIG, stream_mode=["custom", "updates"]):
        if mode == "custom":
            await hub._event(event)


def story_blocks(screen) -> list[str]:
    return [block.render().plain for block in screen.story.query(".entry.keeper")]


def prepare(game) -> None:
    game.m.flags["tools_ready"] = "随身器材已整理完毕"
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡整理随身器材"}},
        {"needs_roll": False, "minutes": 2},
        final(BODY, scene={"flags": ["tools_ready"]}),
    ])


def attach(hub, game, cast) -> None:
    hub.game, hub.cast, hub.m, hub.state = game, cast, game.m, game.state


@pytest.mark.parametrize("cut", ["landed", "before-end"])
async def test_reconnected_backlog_and_cached_delivery_form_one_final_story_block(fake, private_hub, monkeypatch, cut):
    hub, _messages = private_hub
    old_hub_game = hub.game
    game, graph, cast = table(fake, party=True)
    games, casts, saved = [old_hub_game, game], [cast], []
    prepare(game)
    attach(hub, game, cast)
    record = turn_graph.record_event

    def save_at_land():
        receipt = game.state.pending_turn.get("keeper", {})
        if receipt.get("landed") and not saved:
            saved.append(game.state.model_dump_json())
            raise ConnectionError("场面已保存，正文尚未投递")

    def save_before_end(current, event):
        if event.get("type") == "keeper_end" and not saved:
            saved.append(current.state.model_dump_json())
            raise ConnectionError("正文已投递，结束事件尚未投递")
        record(current, event)

    if cut == "landed":
        game.checkpoint = save_at_land
    else:
        monkeypatch.setattr(turn_graph, "record_event", save_before_end)
    try:
        with pytest.raises(ConnectionError):
            await play(graph, game, hub, {"input": "我整理随身器材。"})
        assert len(saved) == 1 and not SCRIPT
        assert len(role_calls("HostedReply")) == 1
        state = GameState.model_validate_json(saved[0])
        assert "tools_ready" in state.flags
        assert sum(entry.kind == "keeper" and entry.text == BODY for entry in state.log) == (0 if cut == "landed" else 1)
        game, graph, cast = table(fake, module=game.m.model_copy(deep=True), state=state)
        games.append(game)
        casts.append(cast)
        attach(hub, game, cast)
        welcome = hub._welcome("a")
        backlog = [LogEntry.model_validate(entry) for entry in welcome["log"]]
        app = AutoKeeperApp(theme="amber")
        async with app.run_test(size=(140, 44)) as pilot:
            screen = NetGameScreen(table_module(welcome), GameState.model_validate(welcome["state"]),
                                   Link(), game.state.investigator.id, backlog)  # type: ignore[arg-type]
            app.push_screen(screen)
            await pilot.pause(0.2)
            sent = hub._send

            async def deliver(cid, message):
                await sent(cid, message)
                if cid == "a":
                    await screen.on_msg(message)

            monkeypatch.setattr(hub, "_send", deliver)
            await play(graph, game, hub, {"input": "/retry"})
            await pilot.pause()
            blocks = story_blocks(screen)
            assert sum(BODY in text for text in blocks) == 1
            assert len(blocks) == 1
            assert not game.state.pending_turn and not SCRIPT
            assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("HostedReply")) == 1
            assert sum(entry.kind == "keeper" and entry.text == BODY for entry in game.state.log) == 1
    finally:
        await shut(games, casts)


async def test_live_screen_can_finish_a_cached_body_after_the_standard_retry_error(fake, private_hub, monkeypatch):
    hub, _messages = private_hub
    old_hub_game = hub.game
    game, graph, cast = table(fake, party=True)
    prepare(game)
    attach(hub, game, cast)
    record, saved = turn_graph.record_event, []

    def disconnect(current, event):
        if event.get("type") == "keeper_end" and not saved:
            saved.append(current.state.model_dump_json())
            raise ConnectionError("结束事件投递前中断")
        record(current, event)

    monkeypatch.setattr(turn_graph, "record_event", disconnect)
    app = AutoKeeperApp(theme="amber")
    try:
        async with app.run_test(size=(140, 44)) as pilot:
            welcome = hub._welcome("a")
            screen = NetGameScreen(table_module(welcome), GameState.model_validate(welcome["state"]),
                                   Link(), game.state.investigator.id, [])  # type: ignore[arg-type]
            app.push_screen(screen)
            await pilot.pause(0.2)
            sent = hub._send

            async def deliver(cid, message):
                await sent(cid, message)
                if cid == "a":
                    await screen.on_msg(message)

            monkeypatch.setattr(hub, "_send", deliver)
            with pytest.raises(ConnectionError, match="结束事件投递前中断"):
                await play(graph, game, hub, {"input": "我整理随身器材。"})
            await screen.on_msg({"t": "error", "text": "叙述中断，可继续。", "retry": True})
            await play(graph, game, hub, {"input": "/retry"})
            await pilot.pause()
            blocks = story_blocks(screen)
            assert blocks == [BODY]
            assert not game.state.pending_turn and not SCRIPT
            assert len(role_calls("HostedReply")) == 1
            assert sum(entry.kind == "keeper" and entry.text == BODY for entry in game.state.log) == 1
    finally:
        await shut([old_hub_game, game], [cast])


async def test_delivery_identity_reuses_rendered_groups_and_folded_records_without_deduplicating_equal_words(monkeypatch):
    class StoryApp(App):
        def compose(self) -> ComposeResult:
            yield StoryLog()

    app = StoryApp()
    async with app.run_test() as pilot:
        story = app.query_one(StoryLog)
        text = "车停在门外。\n\n【车夫】我再等一会儿。"
        story.begin_keeper(delivery="accepted:first")
        story.feed(text, delivery="accepted:first")
        story.end_keeper(text, delivery="accepted:first")
        story.end_keeper(text, delivery="accepted:first")
        await pilot.pause()
        assert len(story.query(".entry.keeper")) == len(story.query(".entry.npc")) == 1
        story.begin_keeper(delivery="accepted:second")
        story.feed(text, delivery="accepted:second")
        story.end_keeper(text, delivery="accepted:second")
        await pilot.pause()
        assert len(story.query(".entry.keeper")) == len(story.query(".entry.npc")) == 2

        monkeypatch.setattr(story, "KEEP", 2)
        monkeypatch.setattr(story, "FOLD", 1)
        story.add("keeper", BODY, delivery="accepted:folded")
        for index in range(4):
            story.add("system", f"随后实际发生的第{index + 1}件事。")
        await pilot.pause()
        record = story._deliveries["accepted:folded"]
        assert record[2] is None and record in story._folded
        story.end_keeper(BODY, delivery="accepted:folded")
        await pilot.pause()
        assert record[2] is not None and record not in story._folded
        assert sum(block.render().plain == BODY for block in story.query(".entry.keeper")) == 1
