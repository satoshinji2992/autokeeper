"""State and image updates received over the wire must reach the currently visible scene."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from PIL import Image

from autokeeper.engine.game import Game
from autokeeper.engine.state import time_of_day
from autokeeper.module.schema import Location, Module
from autokeeper.paths import EXAMPLE
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.net import NetGameScreen
from autokeeper.tui.widgets.panels import SceneArt

from .test_multiplayer_transport import transport_hub as _transport_hub
from .test_net import FakeLink

transport_hub = _transport_hub


@pytest.mark.parametrize("change", ["location", "time", "sky", "people"])
async def test_personal_snapshot_updates_the_scene_as_well_as_the_character_sheet(change, tmp_path):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    state = Game.new(module, [lead], human=[lead.id])
    portrait = tmp_path / "warden.png"
    Image.new("RGB", (40, 50), "red").save(portrait)
    module.npc("warden").location = state.location
    module.pictures[module.npc("warden").portrait] = str(portrait)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = NetGameScreen(module, state, FakeLink(), lead.id, [])
        await app.push_screen(screen)
        await pilot.pause()
        updated = state.model_copy(deep=True)
        if change == "location":
            updated.location = "tower"
        elif change == "time":
            updated.minutes = 23 * 60
        elif change == "sky":
            updated.sky = "rain"
        else:
            updated.npc("warden").met = True
        await screen.on_msg({"t": "sync", "state": updated.model_dump(mode="json")})
        scene = screen.query_one(SceneArt)
        assert scene.place == module.location(updated.location).name
        assert scene.when == time_of_day(updated.minutes)
        assert scene.sky == screen._sky(updated.location)
        assert scene.people == screen._faces(updated.location)


async def test_module_updates_refresh_the_current_scene():
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    state = Game.new(module, [lead], human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = NetGameScreen(module, state, FakeLink(), lead.id, [])
        await app.push_screen(screen)
        await pilot.pause()
        updated = module.model_copy(deep=True)
        updated.location(state.location).name = "改建后的广场"
        await screen.on_msg({"t": "module", "module": updated.model_dump(mode="json")})
        assert screen.query_one(SceneArt).place == "改建后的广场"


@pytest.mark.parametrize("first", ["module", "state"])
async def test_new_location_metadata_and_state_can_arrive_in_either_order(first):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    state = Game.new(module, [lead], human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = NetGameScreen(module, state, FakeLink(), lead.id, [])
        await app.push_screen(screen)
        await pilot.pause()
        updated_module = module.model_copy(deep=True)
        updated_module.locations = [Location(id="shed", name="小屋", read_aloud="木板墙边放着一张旧桌。")]
        updated_state = state.model_copy(deep=True)
        updated_state.location, updated_state.visited = "shed", ["shed"]
        messages = {"module": {"t": "module", "module": updated_module.model_dump(mode="json")},
                    "state": {"t": "sync", "state": updated_state.model_dump(mode="json")}}
        await screen.on_msg(messages[first])
        screen._topbar()  # clock/UI refreshes must remain valid while the matching envelope is in flight
        await screen.on_msg(messages["state" if first == "module" else "module"])
        assert screen.state.location == "shed" and screen.query_one(SceneArt).place == "小屋"


@pytest.mark.parametrize("kind", ["sky", "people", "npc", "improv"])
async def test_events_that_change_scene_state_send_a_fresh_snapshot(transport_hub, monkeypatch, kind):
    hub = transport_hub
    sent = []

    async def broadcast(message):
        sent.append(message)

    async def share():
        pass

    monkeypatch.setattr(hub, "_broadcast", broadcast)
    monkeypatch.setattr(hub, "_share_book", share)
    await hub._event({"type": kind, "sky": "rain", "location": hub.state.location})
    assert any(message["t"] == "sync" for message in sent)


async def test_replacing_image_contents_resends_them_even_when_the_path_is_unchanged(transport_hub, tmp_path):
    hub = transport_hub
    sent = []

    async def send(text):
        sent.append(json.loads(text))

    hub.clients = {"a": SimpleNamespace(closed=False, send_str=send)}
    hub.sits["zhouheng"] = "a"
    key = "updated-scene"
    picture = tmp_path / "scene.png"
    hub.m.location(hub.state.location).art = key
    hub.m.art[key] = "."
    hub.m.pictures[key] = str(picture)
    Image.new("RGB", (40, 50), "red").save(picture)
    await hub._picture(key)
    Image.new("RGB", (40, 50), "blue").save(picture)
    await hub._picture(key)
    events = [message["ev"] for message in sent if message.get("ev", {}).get("type") == "art"]
    assert len(events) == 2
    assert events[0]["picture_data"] != events[1]["picture_data"]
    await hub._picture(key)
    assert len(sent) == 2  # unchanged image bytes are still suppressed
