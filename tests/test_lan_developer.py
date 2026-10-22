"""开发者模式 at a LAN table: the host who opened the room in this app has it on the Hub's game, which
runs in the room's own thread; guests have none of it, and nothing of it reaches their screens."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import threading
from typing import Any

import aiohttp
import pytest
from rich.console import Console
from textual.widgets import Button, Input, OptionList

from autokeeper.net import hub as hub_module
from autokeeper.net import room as lan
from autokeeper.paths import EXAMPLE
from autokeeper.plugins.schema import Case, Command, Plugin
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp, Menu
from autokeeper.tui.modals.menu import GameMenu
from autokeeper.tui.modals.plugins import PluginsModal
from autokeeper.tui.modals.settings import SettingsModal
from autokeeper.tui.screens.net import NetGameScreen
from autokeeper.tui.widgets.devpanel import DevPanel

from .test_net import NoticeCast
from .test_tui import until

SECRET_MODEL = "test:claude-hostonly-7"  # a model call only the host's panel may show


@pytest.fixture
async def room(skills_dir, fake):
    """A room as the title screen opens one: the Hub in a thread with a loop of its own."""
    opened = lan.Room(EXAMPLE, seats=["玩家"], cast=NoticeCast(), rules=Rules(dice=fake), host="127.0.0.1", port=0)
    await asyncio.to_thread(opened.open)
    yield opened
    await asyncio.to_thread(opened.close)


async def on_hub(room: lan.Room, work) -> Any:
    """work(hub) on the Hub's own loop, as the host's screen does it."""
    async def there():
        return work(room.hub)

    return await asyncio.wrap_future(room.run(there()))


def spy(room: lan.Room) -> list[tuple[str, dict[str, Any]]]:
    """Every message the Hub sends, with whom it went to (cid)."""
    hub, sent = room.hub, []
    real = hub._send

    async def send(cid: str, msg: dict[str, Any]) -> None:
        sent.append((cid, json.loads(json.dumps(msg, ensure_ascii=False, default=str))))
        await real(cid, msg)

    hub._send = send
    return sent


async def seated(app, pilot) -> NetGameScreen:
    """Through the lobby to the table: the free seat, ready, the first round open."""
    await pilot.pause(0.5)
    await pilot.press("enter")
    await pilot.pause(0.1)
    await pilot.press("y")
    assert await until(pilot, lambda: isinstance(app.screen, NetGameScreen) and app.screen.my_turn, tries=100)
    return app.screen


def palette(screen) -> list[str]:
    return [name for name, _, _ in Menu(screen)._entries()]


def topbar(screen) -> str:
    console = Console(width=200, record=True, file=io.StringIO())
    console.print(screen.query_one("#topbar").content)
    return console.export_text()


def panel_text(screen) -> str:
    return str(screen.query_one(DevPanel).body.render())


TRAP = Plugin(id="trap", name="陷阱", description="踩到就扣血", code="def handle(event, ctx, api):\n    return {}\n",
              hooks=["command"], capabilities=["damage"], commands=[Command(name="踩", description="踩陷阱")],
              tests=[Case(name="踩", event={"hook": "command", "command": "踩", "text": "x"}, expected_state={})])


async def test_the_host_has_developer_mode_on_the_hub_s_game(room, monkeypatch):
    from autokeeper.engine import developer_web
    from autokeeper.plugins import author

    sent = spy(room)
    threads: dict[str, str] = {}
    real_dashboard = developer_web.open_dashboard

    async def dashboard(game, **kw):
        threads["web"] = threading.current_thread().name
        assert game is room.hub.game  # the page reads the Hub's game itself
        return await real_dashboard(game, **kw)

    drafted: list[tuple[str, str, bool]] = []

    async def house_rule(cast, m, state, text, on_step=None):
        drafted.append((text, threading.current_thread().name, state is room.hub.state))
        return True, "已添加：每夜进食"

    monkeypatch.setattr(developer_web, "open_dashboard", dashboard)
    monkeypatch.setattr(author, "house_rule", house_rule)

    def install(hub) -> None:
        hub.m.extensions[TRAP.id] = TRAP
        hub.m.plugins.append(TRAP.id)
        hub.cast.debug_calls = [{"turn": 0, "agent": "keeper", "model": SECRET_MODEL, "seconds": 1.5}]

    await on_hub(room, install)

    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{room.port}", "小明"))
    app.room = room  # opened from this app's title screen
    urls: list[str] = []
    monkeypatch.setattr(app, "open_url", urls.append)
    heard: list[dict[str, Any]] = []
    async with app.run_test(size=(140, 44)) as pilot, aiohttp.ClientSession() as http:
        screen = await seated(app, pilot)
        assert screen.link.host and screen.may_develop and not screen.developing
        guest = await http.ws_connect(f"ws://127.0.0.1:{room.port}/ws", max_msg_size=64 * 1024 * 1024)
        await guest.send_json({"t": "hello", "name": "小红"})  # watching: every broadcast reaches her

        async def listen() -> None:
            with contextlib.suppress(Exception):
                async for msg in guest:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        heard.append(json.loads(msg.data))

        listening = asyncio.create_task(listen())
        assert "开发者模式  F12" in palette(screen) and not any(n.startswith("规则插件") for n in palette(screen))

        # F12: on, the top bar says so, the web page opens on the Hub's game from the Hub's loop
        await pilot.press("f12")
        assert await until(pilot, lambda: screen.developing and bool(urls))
        server = room.hub.game.debug_server
        assert threads["web"] == "hub" and urls == [server.url] and screen.dev_url == server.url
        assert screen.has_class("-dev") and "开发者模式" in topbar(screen)
        # the panel: the Hub's calls, where everyone really is
        npc = room.hub.m.npcs[0]
        assert await until(pilot, lambda: "人物实际位置" in panel_text(screen) and f"{npc.name}@" in panel_text(screen))
        assert await until(pilot, lambda: "hostonly-7" in panel_text(screen) and "守秘人" in panel_text(screen))
        assert any(n.startswith("规则插件") for n in palette(screen))

        # 设置: off and on again, and 规则插件 behind it
        screen.action_settings()
        assert await until(pilot, lambda: isinstance(app.screen, SettingsModal))
        settings = app.screen
        await pilot.pause()
        shown = [b.id for b in settings.query(".settings-page").results(Button) if b.display]
        assert shown == ["settings-models", "settings-developer", "settings-plugins"]
        assert str(settings.query_one("#settings-developer", Button).label) == "关闭开发者模式"
        settings.query_one("#settings-developer", Button).press()
        assert await until(pilot, lambda: not screen.developing and not screen.has_class("-dev"))
        assert not settings.query_one("#settings-plugins").display
        settings.query_one("#settings-developer", Button).press()
        assert await until(pilot, lambda: screen.developing and settings.query_one("#settings-plugins").display)
        settings.query_one("#settings-plugins", Button).press()
        assert await until(pilot, lambda: isinstance(app.screen, PluginsModal))
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("escape")
        assert await until(pilot, lambda: app.screen is screen)

        # Ctrl+K: the Hub's rules; a pause and a house rule go to the Hub and happen on its loop
        await pilot.press("ctrl+k")
        assert await until(pilot, lambda: isinstance(app.screen, PluginsModal))
        plugins = app.screen
        assert "陷阱" in "".join(str(c.render()) for c in plugins.query(".plugin-card"))
        plugins.query_one("#plugin-0", Button).press()
        assert await until(pilot, lambda: "trap" in room.hub.state.plugin_errors)
        box = plugins.query_one("#house-rule", Input)
        box.value = "每过一夜要吃一顿饭"
        box.focus()
        await pilot.press("enter")
        assert await until(pilot, lambda: bool(drafted), tries=60)
        assert drafted == [("每过一夜要吃一顿饭", "hub", True)]
        assert await until(pilot, lambda: any(m.get("t") == "say" and "已添加：每夜进食" in m.get("text", "") for m in heard))
        await pilot.press("escape")
        await pilot.pause(0.3)
        await guest.close()
        await listening

    # Nothing of the mode reached the guest: not the page, not the panel's calls.
    hosts = room.hub.hosts
    theirs = [msg for cid, msg in sent if cid not in hosts]
    assert theirs and heard
    for msg in [*theirs, *heard]:
        text = json.dumps(msg, ensure_ascii=False)
        assert server.key not in text and "hostonly-7" not in text and "人物实际位置" not in text
        assert "debug" not in text and "plugin_errors" not in text
    assert {m["t"] for m in theirs} <= {"welcome", "seats", "sync", "pool", "event", "turn", "module", "say", "over"}


async def test_a_guest_has_no_developer_mode_anywhere(room, monkeypatch):
    """Even when the room runs in the same app, a screen the Hub did not take for the host's has
    no 开发者模式 and no 规则插件: Esc menu, 设置, F12, /dev, Ctrl+K, Ctrl+P."""
    monkeypatch.setattr(hub_module, "room_key", lambda url: "")  # joined without the host's key
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{room.port}", "小红"))
    app.room = room
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await seated(app, pilot)
        assert not screen.link.host and not screen.may_develop
        await pilot.press("escape")
        assert await until(pilot, lambda: isinstance(app.screen, GameMenu))
        ids = [o.id for o in app.screen.query_one(OptionList)._options]
        assert "settings" in ids and not {"developer", "plugins"} & set(ids)
        await pilot.press("escape")
        await pilot.pause()
        screen.action_settings()
        assert await until(pilot, lambda: isinstance(app.screen, SettingsModal))
        assert not app.screen.query(".settings-page") and not app.screen.query("#settings-plugins")
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("f12")
        await pilot.pause(0.2)
        assert not screen.developing and not screen.has_class("-dev")
        screen.query_one("#command", Input).value = "/dev"
        await pilot.press("enter")
        await pilot.pause(0.2)
        screen.action_developer()
        await pilot.pause(0.2)
        assert not screen.developing and not screen.dev_url
        await pilot.press("ctrl+k")
        await pilot.pause(0.2)
        assert app.screen is screen
        screen.action_plugins()
        await pilot.pause(0.3)
        assert app.screen is screen
        assert not any(n.startswith(("开发者模式", "规则插件")) for n in palette(screen))
        assert getattr(room.hub.game, "debug_server", None) is None
