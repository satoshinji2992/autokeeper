"""Visible card controls, pinned save actions, release updates and ready-seat locking."""

from __future__ import annotations

import sys

import aiohttp
import httpx
import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, OptionList

from autokeeper import imagery, paths, roster, updates
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.cardedit import CardEditModal
from autokeeper.tui.modals.update import UpdateModal
from autokeeper.tui.screens.cards import CardsScreen
from autokeeper.tui.screens.lobby import LobbyScreen

from .test_lancards import card, hello
from .test_net import TableCast, closed, opened, until


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_cards_have_clickable_actions_and_save_stays_at_the_right_while_fields_scroll(monkeypatch, size):
    from autokeeper.module.schema import Module

    monkeypatch.setattr(imagery, "available", lambda: False)
    monkeypatch.setattr(paths, "module_folders", lambda: [EXAMPLE])
    mine = Module.load(EXAMPLE).pregens[0].model_copy(update={"id": "visible-card", "name": "原姓名"})
    roster.keep(mine, {})
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=size) as pilot:
        app.push_screen(CardsScreen())
        await pilot.pause()
        cards = app.screen
        assert not cards.query_one("#card-action-edit", Button).disabled
        assert not cards.query_one("#card-action-delete", Button).disabled
        await pilot.click("#card-action-edit")
        await pilot.pause()
        form = app.screen
        assert isinstance(form, CardEditModal)
        save = form.query_one("#edit-save", Button)
        fields = form.query_one("#card-edit-fields", VerticalScroll)
        start = save.region
        assert start.x >= fields.region.right and start.bottom <= size[1]
        fields.scroll_end(animate=False)
        await pilot.pause()
        assert fields.scroll_y > 0 and save.region == start
        form.query_one("#edit-name", Input).value = "已改名"
        await pilot.click("#edit-save")
        await pilot.pause()
        assert app.screen is cards and roster.saved()[0].name == "已改名"
        await pilot.click("#card-action-delete")
        await pilot.pause()
        await pilot.click("#answer-yes")
        await pilot.pause()
        assert not roster.saved()
        assert cards.query_one("#card-action-edit", Button).disabled


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_lobby_prominent_start_and_ready_blocks_mouse_and_shortcut_seat_changes(monkeypatch, fake, size):
    monkeypatch.setattr(imagery, "available", lambda: False)
    monkeypatch.setattr(LobbyScreen, "_connect", lambda self: None)
    hub = Hub(EXAMPLE, seats=["玩家", "玩家"], cast=TableCast(), rules=Rules(dice=fake))
    hub.names["me"] = "本人"
    hub.sits["zhouheng"] = "me"
    hub.ready.add("zhouheng")
    sent = []

    async def send(message):
        sent.append(message)
        return True

    app = AutoKeeperApp(theme="amber")
    try:
        async with app.run_test(size=size) as pilot:
            lobby = LobbyScreen("127.0.0.1:8765", "本人")
            monkeypatch.setattr(lobby.link, "send", send)
            app.push_screen(lobby)
            await pilot.pause()
            await lobby.on_msg({**hub._welcome("me"), "t": "seated", "investigator": "zhouheng"})
            await pilot.pause()
            start = lobby.query_one("#start", Button)
            assert start.region.width >= 20 and start.region.height == 3
            assert 0 <= start.region.x < start.region.right <= size[0] and start.region.bottom <= size[1]
            assert lobby.query_one("#pick", Button).disabled and lobby.query_one("#watch", Button).disabled
            seats = lobby.query_one("#seats", OptionList)
            seats.highlighted = seats.get_option_index("seat-1")
            seats.focus()
            await pilot.press("enter", "r", "w")
            await pilot.pause()
            assert not sent and lobby.chosen == "zhouheng"
            await pilot.click("#ready")
            assert sent == [{"t": "ready", "on": False}]
            hub.ready.clear()
            await lobby.on_msg({"t": "seats", "seats": hub._seats(), "bring": True})
            await pilot.pause()
            assert not lobby.query_one("#pick", Button).disabled
            await lobby.action_sit()
            assert sent[-1] == {"t": "sit", "investigator": "seat-1"}
    finally:
        await hub.game.close()


async def test_ready_seat_is_locked_by_host_until_readiness_is_cancelled(fake):
    hub = Hub(EXAMPLE, seats=["玩家", "玩家"], cast=TableCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            player, _ = await hello(http, hub, "本人")
            await player.send_json({"t": "sit", "investigator": "zhouheng"})
            await until(player, lambda message: message["t"] == "seated")
            await player.send_json({"t": "ready", "on": True})
            await until(player, lambda message: message["t"] == "seats" and message["seats"][0]["ready"])
            for request in ({"t": "sit", "investigator": "seat-1"}, {"t": "watch"},
                            {"t": "bring", "investigator": card()}, {"t": "card", "op": "begin", "n": 1}):
                await player.send_json(request)
                refusal = await until(player, lambda message: message["t"] in {"error", "card"})
                assert "取消准备" in (refusal.get("text") or refusal.get("error", ""))
                assert "zhouheng" in hub.ready and hub.sits["zhouheng"] is not None and not hub.sits["seat-1"]
            await player.send_json({"t": "ready", "on": False})
            await until(player, lambda message: message["t"] == "seats" and not message["seats"][0]["ready"])
            await player.send_json({"t": "sit", "investigator": "seat-1"})
            assert (await until(player, lambda message: message["t"] == "seated"))["investigator"] == "seat-1"
            await player.close()
    finally:
        await closed(task)


async def test_title_opens_release_check_without_installing(monkeypatch):
    monkeypatch.setattr(imagery, "available", lambda: False)

    async def latest():
        return updates.Release("99.0.0")

    monkeypatch.setattr(updates, "latest", latest)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:
        menu = app.screen.query_one("#menu", OptionList)
        menu.highlighted = menu.get_option_index("update")
        menu.action_select()
        await pilot.pause()
        assert isinstance(app.screen, UpdateModal)
        assert app.screen.query_one("#update-install", Button).disabled
        assert "99.0.0" in str(app.screen.query_one("#update-status").render())
        await pilot.click("#update-close")
        assert not isinstance(app.screen, UpdateModal)


async def test_release_check_accepts_only_the_repositories_versioned_wheel(monkeypatch):
    real = httpx.AsyncClient
    digest = "a" * 64

    def reply(request):
        assert str(request.url) == updates.API
        return httpx.Response(200, json={"tag_name": "v0.0.6", "assets": [
            {"name": "autokeeper-0.0.6-py3-none-any.whl", "browser_download_url": "https://elsewhere/wrong.whl"},
            {"name": "autokeeper-0.0.6-py3-none-any.whl", "browser_download_url":
             updates.REPOSITORY + "/releases/download/v0.0.6/autokeeper-0.0.6-py3-none-any.whl", "digest": "sha256:" + digest},
        ]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(reply), **kwargs))
    release = await updates.latest()
    assert release.version == "0.0.6" and release.newer
    assert release.wheel.endswith(".whl#sha256=" + digest)
    assert updates.version("0.0.6.0") == updates.version("v0.0.6")
    with pytest.raises(ValueError):
        updates.version("0.0.6; echo arbitrary")


def test_uv_update_retains_extras_and_cannot_run_from_guest_or_source(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "INSTALLED", True)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(updates.shutil, "which", lambda name: "/tools/uv")
    monkeypatch.delenv("AUTOKEEPER_GUEST", raising=False)
    (tmp_path / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "autokeeper", extras = ["claude", "graphrag"] }]\n', encoding="utf-8")
    release = updates.Release("99.0.0", updates.REPOSITORY + "/releases/download/v99.0.0/autokeeper-99.0.0-py3-none-any.whl")
    command = updates.command(release)
    assert command is not None and command[-1].startswith("autokeeper[claude,graphrag] @ https://")
    assert "tool" in command and "install" in command and "--python" in command
    monkeypatch.setenv("AUTOKEEPER_GUEST", "1")
    assert updates.command(release) is None
    monkeypatch.delenv("AUTOKEEPER_GUEST")
    monkeypatch.setattr(paths, "INSTALLED", False)
    assert updates.command(release) is None


@pytest.mark.parametrize("code", [0, 1])
async def test_update_process_reports_completion_or_failure_without_a_shell(monkeypatch, code):
    argv = ["uv", "tool", "install", "autokeeper @ https://example.invalid/package.whl"]
    monkeypatch.setattr(updates, "command", lambda release: argv)

    class Process:
        returncode = code

        async def communicate(self):
            return b"installation details\nnetwork error", None

    async def start(*actual, **kwargs):
        assert list(actual) == argv and "shell" not in kwargs
        assert kwargs["stdin"] == updates.asyncio.subprocess.DEVNULL
        return Process()

    monkeypatch.setattr(updates.asyncio, "create_subprocess_exec", start)
    result = await updates.install(updates.Release("99.0.0"))
    assert (not result) if code == 0 else "network error" in result
