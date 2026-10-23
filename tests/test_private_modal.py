"""/sc has a separate composer; its body never goes through ordinary public action input."""

from __future__ import annotations

import pytest
from textual.widgets import Input, Select

from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.private import PrivateMessageModal
from autokeeper.tui.screens.net import NetGameScreen

from .test_net import FakeLink


@pytest.mark.parametrize("target", ["kp", "seat:lin", "cancel"])
async def test_sc_opens_a_private_window_and_routes_only_to_its_selected_recipient(target):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    peer = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [lead, peer], human=[lead.id, peer.id])
    link = FakeLink()
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = NetGameScreen(module, state, link, lead.id, [])
        await app.push_screen(screen)
        screen.query_one("#command", Input).focus()
        await pilot.pause()
        screen.busy = True  # private correspondence must also work while the KP is narrating
        screen.query_one("#command", Input).value = "/sc"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, PrivateMessageModal)
        assert not link.sent
        if target == "cancel":
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is screen and not link.sent
        else:
            app.screen.query_one(Select).value = target
            app.screen.query_one(Input).value = "请不要公开这条消息。"
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is screen
            assert len(link.sent) == 1 and link.sent[0]["text"] == "请不要公开这条消息。"
            if target == "kp":
                assert link.sent[0]["t"] == "kp_private" and link.sent[0]["request"]
            else:
                assert link.sent[0] == {"t": "speak", "text": "请不要公开这条消息。", "to": ["lin"], "channel": "whisper"}


async def test_sc_body_is_private_while_normal_input_still_submits_a_public_action():
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    state = Game.new(module, [lead], human=[lead.id])
    link = FakeLink()
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = NetGameScreen(module, state, link, lead.id, [])
        await app.push_screen(screen)
        screen.query_one("#command", Input).focus()
        await pilot.pause()
        screen.query_one("#command", Input).value = "/sc 我想确认一下刚才的异常。"
        await pilot.press("enter")
        await pilot.pause()
        assert link.sent[-1]["t"] == "kp_private"
        assert link.sent[-1]["text"] == "我想确认一下刚才的异常。"
        screen.query_one("#command", Input).value = "我去查看书房。"
        await pilot.press("enter")
        await pilot.pause()
        assert link.sent[-1] == {"t": "act", "text": "我去查看书房。"}
