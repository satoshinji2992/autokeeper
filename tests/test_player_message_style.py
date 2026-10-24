"""Public input stays readable; a different group's input remains public but subdued."""

from __future__ import annotations

import pytest

from autokeeper.engine import audience
from autokeeper.engine.state import GameState, LogEntry
from autokeeper.module.schema import Location
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.net import NetGameScreen
from autokeeper.tui.widgets.panels import HistoryLine

from .test_net import FakeLink, TableCast
from .test_net_private import private_hub as _private_hub

private_hub = _private_hub


@pytest.mark.parametrize("kind,text", [("act", "我去看看门锁。"), ("speak", "先等我回来。")])
async def test_input_keeps_its_original_group_through_movement_and_reload(private_hub, fake, kind, text):
    hub, messages = private_hub
    hub.state.apart["chen"] = "office"
    hub.state.record.entities["chen"].at = "office"
    hub.open = True
    await hub._on("a", {"t": kind, "text": text})
    echoes = [(cid, msg["ev"]) for cid, msg in messages if msg.get("ev", {}).get("type") in ("said", "speech")]
    assert [cid for cid, _ in echoes] == ["a", "b", "c", "watch"]
    for _, event in echoes:
        assert event["table_present"] == ["zhouheng", "lin"] and "audience" not in event
        assert not audience.offscene(event, "lin") and audience.offscene(event, "chen")
        assert not audience.offscene(event, None)  # spectators have no personal scene
    # Nobody gains character knowledge just from the public table transcript.
    assert not audience.log(hub.state, "chen")
    hub.state.apart.clear()
    hub.state.record.entities["chen"].at = "square"
    restored = GameState.model_validate_json(hub.state.model_dump_json())
    rebuilt = Hub(EXAMPLE, state=restored, cast=TableCast(), rules=Rules(dice=fake))
    rebuilt.clients, rebuilt.sits = hub.clients, hub.sits
    line = next(entry for entry in rebuilt._welcome("c")["log"] if entry["text"] == hub.state.table_history[-1].text)
    assert audience.offscene(line, "chen") and not audience.offscene(line, "lin")


async def test_rooms_within_the_same_scene_are_not_presented_as_a_separate_group(private_hub):
    hub, messages = private_hub
    hub.m.locations.append(Location(id="alcove", name="壁龛", within="square", read_aloud="门边的壁龛。"))
    hub.state.apart["lin"] = "alcove"
    await hub._table_input("zhouheng", "我在门边查看。")
    assert all(not audience.offscene(msg["ev"], "lin") for _, msg in messages)


async def test_live_and_reconnected_player_lines_keep_their_style_without_duplicate_history(private_hub):
    hub, _ = private_hub
    lines = [
        LogEntry(turn=1, time="12:00", kind="player", who="周衡", text="我先检查门锁。",
                 delivery="table:own", table_present=["zhouheng", "lin"]),
        LogEntry(turn=2, time="12:00", kind="speech", who="林秋", text="我来照亮门边。",
                 delivery="table:local", table_present=["zhouheng", "lin"]),
        LogEntry(turn=3, time="12:15", kind="player", who="陈明", text="我在钟楼等你们。",
                 delivery="table:away", table_present=["chen"]),
    ]
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = NetGameScreen(hub.m, hub.state, FakeLink(), "zhouheng", lines)
        await app.push_screen(screen)
        await pilot.pause()
        own, local, away = list(screen.story.query(".player"))
        assert not own.has_class("offscene") and not local.has_class("offscene") and away.has_class("offscene")
        assert own.styles.color == local.styles.color != away.styles.color
        assert not own.styles.text_style.italic
        assert own.content.spans and local.content.spans and not away.content.spans
        assert len(screen.query(HistoryLine)) == 3  # backlog isn't logged again as fresh input
        await screen.handle({"type": "said", "who": "陈明", "text": lines[-1].text,
                             "delivery": "table:away", "table_present": ["chen"]})
        await pilot.pause()
        assert len(screen.story.query(".player")) == 3  # a late live echo doesn't duplicate restored history
        await screen.handle({"type": "speech", "who": "林秋", "text": "这里有划痕。",
                             "delivery": "table:new", "table_present": ["zhouheng", "lin"]})
        await screen.handle({"type": "speech", "who": "陈明", "text": "钟楼很安静。",
                             "delivery": "table:far", "table_present": ["chen"]})
        await pilot.pause()
        new, far = list(screen.story.query(".player"))[-2:]
        assert new.styles.color == own.styles.color and far.styles.color == away.styles.color
        assert not new.has_class("offscene") and far.has_class("offscene")
        assert len(screen.query(HistoryLine)) == 5
        live = LogEntry(turn=4, time="12:20", kind="speech", who="陈明", text="钟楼很安静。",
                        delivery="table:far", table_present=["chen"])
        await screen.on_msg({"t": "history", "seat": "zhouheng", "log": [live.model_dump(mode="json")]})
        await pilot.pause()
        assert len(screen.story.query(".player")) == len(screen.query(HistoryLine)) == 5
