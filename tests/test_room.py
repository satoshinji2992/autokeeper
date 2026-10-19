"""A LAN room opened on this machine (net/room.py), from the title screen's 开房间 and from
`autokeeper host`; the lobby (准备房间) and the page browsers open. Stand-in casts, no model
calls; the Hub runs in its own thread as it does for players."""

from __future__ import annotations

import asyncio
import io
import socket
from typing import Any

import aiohttp
import pytest
from rich.console import Console
from textual.widgets import Checkbox, Input, OptionList, Static

import autokeeper
from autokeeper import paths
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net import room as lan
from autokeeper.net.hub import Hub, room_key_file
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast, closed, opened, until


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def shown(screen, *selectors: str) -> str:
    """What the screen's statics and lists say, as plain text."""
    console = Console(width=200, record=True, file=io.StringIO())
    for sel in selectors:
        for w in screen.query(sel):
            if isinstance(w, OptionList):
                for i in range(w.option_count):
                    console.print(w.get_option_at_index(i).prompt)
            elif isinstance(w, Static):
                console.print(w.content)
    return console.export_text()


@pytest.fixture
def stand_ins(monkeypatch, tmp_path, skills_dir, fake):
    """Every Hub this test opens plays with the stand-in cast and scripted dice; saves go to a temp folder."""
    import autokeeper.agents.cast as cast_module
    import autokeeper.rules.coc as coc

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setattr(cast_module, "Cast", lambda cfg, m: TableCast())
    real = coc.Rules
    monkeypatch.setattr(coc, "Rules", lambda: real(dice=fake))
    return fake


def test_each_address_says_which_network_it_is_on():
    assert lan.network("eth0 eth0", "192.168.1.5") == "局域网"
    assert lan.network("Radmin VPN Radmin VPN", "26.1.2.3") == "Radmin VPN"
    assert lan.network("eth0 eth0", "26.27.115.128") == "Radmin VPN"  # WSL's mirror of it: told by its range
    assert lan.network("{8056} ZeroTier One [8056c2e21c000001]", "10.147.17.5") == "ZeroTier"
    assert lan.network("ztabcdef12 ztabcdef12", "172.22.1.2") == "ZeroTier"
    assert lan.network("tailscale0 tailscale0", "100.101.102.103") == "Tailscale"
    assert lan.network("ham0 ham0", "25.4.5.6") == "Hamachi"
    for adapter, ip in (("docker0 docker0", "172.17.0.1"), ("br-1d0f6d0befde br-1d0f6d0befde", "172.25.112.1"),
                        ("{x} vEthernet (WSL)", "172.20.0.1"), ("lo lo", "10.255.255.254"), ("eth0 eth0", "127.0.0.1"),
                        ("eth0 eth0", "169.254.3.4"), ("{y} Clash", "198.18.0.1"), ("{z} VMware Network Adapter VMnet8", "192.168.80.1")):
        assert lan.network(adapter, ip) is None, adapter  # nobody else can reach these


def test_the_default_route_s_address_comes_first(monkeypatch):
    monkeypatch.setattr(lan, "_adapters", lambda: [("eth0 eth0", "26.27.115.128"), ("docker0 docker0", "172.20.0.1"),
                                                   ("{a} vEthernet (External)", "192.168.1.20"), ("eth4 eth4", "10.21.231.25")])
    monkeypatch.setattr(lan, "_outgoing", lambda: "192.168.1.20")  # a Hyper-V external switch carries the LAN
    assert lan.addresses() == [("192.168.1.20", "局域网"), ("10.21.231.25", "局域网"), ("26.27.115.128", "Radmin VPN")]
    monkeypatch.setattr(lan, "_adapters", lambda: [])
    monkeypatch.setattr(lan, "_outgoing", lambda: "")
    assert lan.addresses() == [("127.0.0.1", "本机")]
    assert lan.join_target("10.0.0.2", 8765) == "10.0.0.2" and lan.join_target("10.0.0.2", 9000) == "10.0.0.2:9000"
    assert lan.page_url("10.0.0.2", 8000) == "http://10.0.0.2:8000" and lan.page_url("10.0.0.2", 80) == "http://10.0.0.2"


async def test_a_room_runs_its_hub_in_a_thread_and_closes_it(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    room = lan.Room(EXAMPLE, seats=["玩家"], port=0, cast=TableCast(), rules=Rules(dice=fake))
    hub = await asyncio.to_thread(room.open)
    assert room.is_open and hub.port == room.port and room_key_file(room.port).read_text(encoding="utf-8") == hub.key
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(f"ws://127.0.0.1:{room.port}/ws")
        await ws.send_json({"t": "hello", "name": "小明", "key": hub.key})
        welcome = await until(ws, lambda x: x["t"] == "welcome")
        assert welcome["host"] and welcome["room"]["port"] == room.port and welcome["room"]["web"] == 0
        assert welcome["room"]["addresses"] and all(len(row) == 2 for row in welcome["room"]["addresses"])
        await asyncio.to_thread(room.close)
        while (msg := await asyncio.wait_for(ws.receive(), 5)).type == aiohttp.WSMsgType.TEXT:
            pass  # what was said before it closed
        assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
    assert not room.is_open and not room_key_file(room.port).exists()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", room.port), 1).close()
    room.close()  # twice is fine


async def test_a_room_that_cannot_open_says_why(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    with pytest.raises(lan.RoomError, match="座位只能填"):
        await asyncio.to_thread(lan.Room(EXAMPLE, seats=["谁都行"], port=0, cast=TableCast()).open)
    with socket.socket() as taken:
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        port = taken.getsockname()[1]
        with pytest.raises(lan.RoomError, match="可能已经开着一个") as failed:
            await asyncio.to_thread(lan.Room(EXAMPLE, port=port, cast=TableCast(), rules=Rules(dice=fake)).open)
        assert str(port) not in str(failed.value) and str(port) in failed.value.detail  # the port is for the 遇到问题 page
        room = lan.Room(EXAMPLE, port=0, web_port=port, cast=TableCast(), rules=Rules(dice=fake))
        await asyncio.to_thread(room.open)
        with pytest.raises(lan.RoomError, match="网页版没开起来") as failed:
            room.start_web()
        assert str(port) in failed.value.detail
        room.close()
        # opened from the title screen, a taken port is swapped for a free one
        room = lan.Room(EXAMPLE, port=port, web_port=port, any_port=True, cast=TableCast(), rules=Rules(dice=fake))
        await asyncio.to_thread(room.open)
        try:
            with open(tmp_path / "web.log", "a", encoding="utf-8") as log:
                await asyncio.to_thread(room.start_web, log)
            assert room.is_open and port not in (room.port, room.web_port) and room.shared["port"] == room.port
        finally:
            await asyncio.to_thread(room.close)


async def test_the_page_runs_on_its_own_and_goes_with_the_room(monkeypatch, tmp_path, skills_dir, fake):
    """The browsers' page: our template (amber, a CJK font, the F5 note), served by a process of
    its own that the room stops."""
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    room = lan.Room(EXAMPLE, port=0, web_port=free_port(), cast=TableCast(), rules=Rules(dice=fake))
    await asyncio.to_thread(room.open)
    try:
        with open(tmp_path / "web.log", "a", encoding="utf-8") as log:
            url = await asyncio.to_thread(room.start_web, log)
        assert url.endswith(f":{room.web_port}") and room.shared["web"] == room.web_port
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{room.web_port}/") as r:
                page = await r.text()
            async with http.get(f"http://127.0.0.1:{room.web_port}/static/js/textual.js") as r:
                script = await r.text()
        assert "<title>AutoKeeper 联机</title>" in page and '"Sarasa Mono SC", "Noto Sans Mono CJK SC", "Microsoft YaHei Mono", monospace' in page
        assert "F5 会刷新网页" in page and "/suggest" in page and "fonts.googleapis.com" not in page  # nothing to wait for behind a firewall
        assert "fontFamily:\"'Roboto Mono'" in script  # what the page swaps for its own fonts
        page_process = room.page
    finally:
        await asyncio.to_thread(room.close)
    assert page_process is not None and page_process.poll() is not None  # its stdin closed: it went


async def test_the_lobby_shows_the_table_and_never_the_book(monkeypatch, tmp_path, skills_dir, fake):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.lobby import LobbyScreen
    from autokeeper.tui.sheet import SheetModal
    from autokeeper.tui.trouble import TroubleModal

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setenv("AUTOKEEPER_GUEST", "1")  # a friend's seat, though on this machine
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋", "occupation": "医生", "age": 41, "backstory": {"说明": "AutoKeeper 补充", "个人描述": "AutoKeeper 补充：总揣着一只听诊器"}}, deep=True)
    state = Game.new(m, [lead, friend], slot="lan-lobby", human=[lead.id, "lin"])
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    hub.room.update(addresses=[["192.168.1.5", "局域网"], ["26.1.2.3", "Radmin VPN"]], port=8765, web=8000)
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小红"))
    async with app.run_test(size=(140, 44)) as pilot:
        for _ in range(40):
            await pilot.pause(0.1)
            if isinstance(app.screen, LobbyScreen) and app.screen.welcome:
                break
        lobby = app.screen
        assert isinstance(lobby, LobbyScreen) and not lobby.link.host
        await pilot.pause(0.3)
        text = shown(lobby, "#lobby-head", "#seats", "#lobby-room", "#lobby-rule", "#lobby-module", "#lobby-sheet")
        assert f"《{m.title}》" in text and m.hook.strip()[:12] in text
        assert "周衡" in text and "林秋" in text and "医生 · 41 岁" in text and "总揣着一只听诊器" in text
        assert "AutoKeeper 补充" not in shown(lobby, "#seats")  # a note for the keeper's records, not who she is
        assert "空座" in text and "发给朋友：http://192.168.1.5:8000" in text and "你是房主" not in text
        assert "26.1.2.3" not in text and "版本" not in text and "8765" not in text  # the rest is on the 遇到问题 page
        await pilot.press("h")
        await pilot.pause(0.2)
        assert isinstance(app.screen, TroubleModal)
        help_text = app.screen.page
        assert "26.1.2.3" in help_text and "Radmin VPN" in help_text and "8765" in help_text and autokeeper.__version__ in help_text
        await pilot.press("escape")
        await pilot.pause(0.1)
        for secret in [m.truth, m.keeper_notes, *(s for n in m.npcs for s in n.secrets)]:
            if secret.strip():
                assert secret.strip()[:12] not in text  # the keeper's book stays on the host
        await pilot.press("down")  # 林秋's seat: her sheet beside the list
        await pilot.pause(0.2)
        assert "人物卡 · 林秋" in str(lobby.query_one("#lobby-card").border_title)
        await pilot.press("c")
        await pilot.pause(0.2)
        assert isinstance(app.screen, SheetModal) and app.screen.party[app.screen.at].id == "lin"
        await pilot.press("escape")
        await pilot.press("enter")  # sits in 林秋's seat
        seats = await asyncio.wait_for(_seated(lobby, "lin"), 5)
        assert seats["player"] == "小红" and seats["online"]
        assert "● 你" in shown(lobby, "#seats")
        await pilot.press("escape")  # 离开: a browser's seat has no title screen to go back to
        await pilot.pause(0.3)
        assert app.return_code == 0
    await closed(task)


async def _seated(lobby, seat: str) -> dict[str, Any]:
    while True:
        found = next((s for s in lobby.seats if s["investigator"] == seat), None)
        if found and found["online"]:
            return found
        await asyncio.sleep(0.05)


async def test_the_host_opens_a_room_from_the_title_and_plays_in_it(monkeypatch, stand_ins):
    from autokeeper.tui import host as host_screen
    from autokeeper.tui.app import AutoKeeperApp, TitleScreen
    from autokeeper.tui.host import HostScreen
    from autokeeper.tui.lobby import CloseRoomModal, LobbyScreen
    from autokeeper.tui.net import NetGameScreen
    from autokeeper.tui.trouble import TroubleModal

    monkeypatch.setattr(host_screen, "modules", lambda: [EXAMPLE])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause(0.3)
        menu = app.screen.query_one("#menu", OptionList)
        menu.highlighted = menu.get_option_index("host")
        await pilot.press("enter")
        await pilot.pause(0.2)
        screen = app.screen
        assert isinstance(screen, HostScreen)
        await pilot.press("enter")  # the only module
        await pilot.pause(0.2)
        assert screen.step == "room" and screen.plan == {"zhouheng": "玩家"}
        await pilot.press("enter", "enter")  # 玩家 → AI → 不上场
        assert screen.plan == {"zhouheng": "不上场"}
        await pilot.press("enter")  # → 玩家
        assert "玩家" in shown(screen, "#host-seats") and "1 位玩家" in shown(screen, "#host-note")
        seats = screen.query_one("#host-seats", OptionList)
        seats.highlighted = seats.get_option_index("add-blank")
        await pilot.press("enter")  # an empty seat, for a friend who brings their own
        assert screen.blanks == 1 and "2 位玩家" in shown(screen, "#host-note") and "空座位" in shown(screen, "#host-seats")
        seats.highlighted = seats.get_option_index("blank-0")
        await pilot.press("enter")  # and gone again
        assert screen.blanks == 0 and "1 位玩家" in shown(screen, "#host-note")
        seats.highlighted = 0
        assert not screen.query("#host-port")  # no ports on the form: a taken one is swapped by itself
        await pilot.press("f1")  # 遇到问题
        await pilot.pause(0.2)
        assert isinstance(app.screen, TroubleModal) and "朋友进不来" in app.screen.page
        await pilot.press("escape")
        await pilot.pause(0.1)
        screen.query_one("#host-name", Input).value = "小明"
        screen.query_one("#host-web", Checkbox).value = False
        screen.query_one("#host-open").press()
        for _ in range(60):
            await pilot.pause(0.1)
            if isinstance(app.screen, LobbyScreen) and app.screen.welcome:
                break
        lobby = app.screen
        assert isinstance(lobby, LobbyScreen) and lobby.link.host and lobby.room is app.room
        port = app.room.port
        assert lan.recall()["name"] == "小明" and lan.recall()["web"] is False
        await pilot.pause(0.2)
        text = shown(lobby, "#lobby-head", "#lobby-room")
        assert "你是房主" in text and "朋友在「加入房间」里填" in text
        assert str(lobby.query_one("#help").label) == "朋友进不来？"
        assert lobby.query_one("#close_room").display
        await pilot.press("enter")  # the only seat
        await pilot.pause(0.1)
        await pilot.press("y")  # ready: every seat taken and ready, the game begins
        for _ in range(60):
            await pilot.pause(0.1)
            if isinstance(app.screen, NetGameScreen) and app.screen.my_turn:
                break
        game = app.screen
        assert isinstance(game, NetGameScreen) and game.seat == "zhouheng" and game.link.host
        hub = app.room.hub
        assert hub is not None and hub.names[next(iter(hub.hosts))] == "小明·房主"
        game.action_close_room()  # Ctrl+P → 关闭房间, for the host
        await pilot.pause(0.2)
        assert isinstance(app.screen, CloseRoomModal)
        await pilot.click("#yes")
        for _ in range(60):
            await pilot.pause(0.1)
            if isinstance(app.screen, TitleScreen):
                break
        assert isinstance(app.screen, TitleScreen) and app.room is None
        assert not room_key_file(port).exists()
        assert str(app.screen.query_one("#menu", OptionList).get_option("host").prompt) == "开房间"


async def test_the_title_goes_back_to_an_open_room_and_quitting_closes_it(monkeypatch, stand_ins):
    from autokeeper.tui import host as host_screen
    from autokeeper.tui.app import AutoKeeperApp, TitleScreen
    from autokeeper.tui.host import HostScreen
    from autokeeper.tui.lobby import LobbyScreen

    m = Module.load(EXAMPLE)
    saved = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="lan-example-0101-000000", human=[])
    from autokeeper.tui.app import write_save

    write_save(saved, m)
    write_save(Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="example-0101-000000", human=["zhouheng"]), m)
    monkeypatch.setattr(host_screen, "modules", lambda: [EXAMPLE])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        app.push_screen(HostScreen())
        await pilot.pause(0.2)
        screen = app.screen
        await pilot.press("enter")
        await pilot.pause(0.2)
        saves = screen.query_one("#host-saves", OptionList)
        assert saves.display and saves.option_count == 2  # the LAN save, not the single-player one
        saves.focus()
        await pilot.press("down", "enter")
        assert screen.slot == "lan-example-0101-000000" and screen.plan == {"zhouheng": "AI"}  # as it was saved
        screen.query_one("#host-seats", OptionList).focus()
        await pilot.press("enter", "enter")  # a saved party: 玩家 / AI only
        assert screen.plan == {"zhouheng": "AI"}
        await pilot.press("enter")
        screen.query_one("#host-web", Checkbox).value = False
        screen.query_one("#host-open").press()
        for _ in range(60):
            await pilot.pause(0.1)
            if isinstance(app.screen, LobbyScreen) and app.screen.welcome:
                break
        room = app.room
        assert room is not None and room.hub.state.slot == "lan-example-0101-000000" and room.hub.state.human == ["zhouheng"]
        await pilot.press("escape")  # the host leaves the lobby: the room stays open
        await pilot.pause(0.3)
        assert isinstance(app.screen, TitleScreen) and room.is_open
        menu = app.screen.query_one("#menu", OptionList)
        assert str(menu.get_option("host").prompt) == "回到房间"
        menu.highlighted = menu.get_option_index("host")
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.1)
            if isinstance(app.screen, LobbyScreen) and app.screen.welcome:
                break
        assert isinstance(app.screen, LobbyScreen) and app.screen.link.host and app.screen.room is room
    assert not room.is_open  # quitting the app closed it


def test_autokeeper_host_opens_the_same_room(monkeypatch, stand_ins):
    """The command line still opens a room: the shared one, its Hub in a thread, closed on the way out."""
    from typer.testing import CliRunner

    from autokeeper.cli import app as cli

    seen: dict[str, Any] = {}

    def wait(room: lan.Room) -> None:  # instead of waiting for Ctrl+C: one player looks in
        async def look() -> None:
            async with aiohttp.ClientSession() as http:
                ws = await http.ws_connect(f"ws://127.0.0.1:{room.port}/ws")
                await ws.send_json({"t": "hello", "name": "小明"})
                seen["welcome"] = await until(ws, lambda x: x["t"] == "welcome")
                await ws.close()

        asyncio.run(look())
        seen["room"] = room

    monkeypatch.setattr(lan.Room, "wait", wait)
    port = free_port()
    result = CliRunner().invoke(cli, ["host", str(EXAMPLE), "--no-web", "--hub-port", str(port), "--seats", "玩家"])
    assert result.exit_code == 0, result.output
    said = result.output.replace("\n", "")  # the console wraps at its width
    assert "房间开好了" in said and f"autokeeper join 127.0.0.1:{port}" in said and "--load lan-example" in said
    welcome = seen["welcome"]
    assert welcome["room"]["port"] == port and [s["investigator"] for s in welcome["seats"]] == ["zhouheng"]
    assert not seen["room"].is_open  # closed when the command ends
    bad = CliRunner().invoke(cli, ["host", str(EXAMPLE), "--no-web", "--hub-port", str(port), "--seats", "随便"])
    assert bad.exit_code == 1 and "座位只能填" in bad.output
