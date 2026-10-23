"""Private mental consequences never leave their seat as character-card values."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net import cards
from autokeeper.net.hub import public_card
from autokeeper.rules.coc import Investigator, Rules
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.sheet import sheet_view, skill_rows
from autokeeper.tui.screens.net import NetGameScreen, table_module
from autokeeper.tui.widgets.panels import SheetPanel, party_rows

from .test_net_private import private_hub as _private_hub
from .test_private_ui import Link

private_hub = _private_hub


def plain(renderable) -> str:
    console = Console(width=140, record=True, file=io.StringIO())
    console.print(renderable)
    return console.export_text()


def troubled(hub):
    inv = hub.state.investigators[0]
    inv.san, inv.san_start_of_day, inv.san_lost_today = 47, 61, 14
    inv.skills["克苏鲁神话"] = 13
    inv.conditions = ["重伤", "临时疯狂"]
    inv.madness.temporary_since, inv.madness.temporary_until = 300, 600
    return inv


def hidden(card):
    assert card["san"] is None and card["san_start_of_day"] is None and card["san_lost_today"] is None
    assert "克苏鲁神话" not in card["skills"] and not card.get("madness")
    assert not any("疯狂" in c for c in card["conditions"])


@pytest.mark.parametrize(("client", "viewer"), [("a", "zhouheng"), ("b", "lin"), ("watch", None)])
def test_welcome_sync_and_module_project_mental_state_before_sending(private_hub, client, viewer):
    hub, _ = private_hub
    lead = troubled(hub)
    own = lead.model_dump(mode="json")
    assert public_card(own, lead.id, hub.m, hub.state) == own
    welcome = hub._welcome(client)
    assert welcome["state"] == hub._view(viewer)
    state = GameState.model_validate(welcome["state"])
    module = Module.model_validate(welcome["module"])
    for inv in state.investigators:
        if inv.id == viewer:
            assert inv.san == hub.state.find(viewer).san
        else:
            hidden(inv.model_dump())
            assert inv.hp == hub.state.find(inv.id).hp
    if viewer == lead.id:
        assert state.find(lead.id).madness == lead.madness
        assert state.find(lead.id).value("克苏鲁神话") == 13
    else:
        assert "重伤" in state.find(lead.id).conditions  # Ordinary visible wounds remain available.
        hidden(next(inv for inv in welcome["module"]["pregens"] if inv["id"] == lead.id))
        assert next(inv for inv in module.pregens if inv.id == lead.id).san is None
    assert lead.model_dump(mode="json") == own  # Projection never mutates the rule-owned original.


async def test_private_sanity_changes_do_not_disclose_values_through_sync_or_reconnect(private_hub, fake):
    hub, messages = private_hub
    lead = troubled(hub)
    before = public_card(lead.model_dump(), "lin", hub.m, hub.state)
    fake.queue(20)
    await hub.game.emit(hub.game._lose_sanity(lead, "1/1", Turn()))
    assert lead.san == 46
    assert public_card(lead.model_dump(), "lin", hub.m, hub.state) == before
    private_results = [(cid, msg) for cid, msg in messages if msg.get("ev", {}).get("type") == "sanity"]
    assert len(private_results) == 1 and private_results[0][0] == "a"
    for client in ("b", "c", "watch"):
        welcome = hub._welcome(client)
        hidden(next(inv for inv in welcome["state"]["investigators"] if inv["id"] == lead.id))
        assert not any(e["kind"] == "sanity" and e["who"] == lead.name for e in welcome["log"])
    assert hub._welcome("a")["state"]["investigators"][0]["san"] == 46


def test_undisclosed_values_render_as_unknown_and_cannot_become_rule_defaults(private_hub, fake):
    hub, _ = private_hub
    troubled(hub)
    view = hub._view("lin")
    st = GameState.model_validate(view)
    inv = st.find("zhouheng")
    assert inv.san is None  # Explicit null did not become POW or zero during parsing.
    rows = {name: value for name, value, _, _ in skill_rows(inv)}
    assert rows["克苏鲁神话"] == "未公开"
    sheet = plain(sheet_view(inv, width=120, state=st))
    assert "SAN 未公开" in sheet and "克苏鲁神话" in sheet and "None" not in sheet
    assert "SAN未公开" in party_rows(st.investigators, "lin", 48).plain
    with pytest.raises(ValueError, match="未公开"):
        inv.value("理智")
    with pytest.raises(ValueError, match="未公开"):
        inv.value("克苏鲁神话")
    with pytest.raises(ValueError, match="未公开"):
        Rules(dice=fake).sanity(inv, "0/1", "1D6")
    assert not fake.values
    with pytest.raises(cards.Refused, match="未公开理智"):
        cards.vet(inv.model_dump(), inv.id)
    with pytest.raises(ValueError, match="未公开"):
        Game(hub.m, st, hub.cast, Rules(dice=fake))
    zero = Investigator.model_validate(hub.state.find("zhouheng").model_dump() | {"san": 0})
    assert zero.require_sanity() == 0 and zero.san == 0
    assert Investigator.model_json_schema()["properties"]["san"]["type"] == "integer"


@pytest.mark.parametrize("client", ["b", "watch"])
async def test_network_screen_renders_projected_cards_and_sync_for_player_and_spectator(private_hub, client):
    hub, _ = private_hub
    troubled(hub)
    welcome = hub._welcome(client)
    seat = hub._seat_of(client)
    module, state = table_module(welcome), GameState.model_validate(welcome["state"])
    app, link = AutoKeeperApp(theme="amber"), Link()
    async with app.run_test(size=(130, 40)) as pilot:
        screen = NetGameScreen(module, state, link, seat, [])
        app.push_screen(screen)
        for _ in range(30):
            await pilot.pause(0.05)
            if screen.ready:
                break
        assert screen.ready
        panel = screen.query_one(SheetPanel)
        assert panel.shown[0].id == (seat or "zhouheng")
        if seat is None:
            assert panel.shown[0].san is None
        else:
            assert panel.shown[0].san is not None
        screen._sync(hub._view(seat))
        await pilot.pause()
        assert "未公开" in plain(party_rows(screen.state.investigators, screen._viewer().id, 48))
        if seat is None:
            assert "未公开" in plain(sheet_view(screen._viewer(), width=120))
