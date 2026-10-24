"""A keeper's scene image belongs to a location, independently of its private audience."""

from __future__ import annotations

import json
from types import MethodType, SimpleNamespace

import pytest

from autokeeper.engine import presentation
from autokeeper.engine.scenes import SceneView
from autokeeper.engine.state import GameState, SceneState
from autokeeper.tui.screens.game import GameScreen
from autokeeper.tui.screens.net import NetGameScreen

from .test_net_private import private_hub as _private_hub

private_hub = _private_hub


async def test_shared_opening_scene_does_not_cover_a_split_players_new_location_or_reconnect(private_hub):
    hub, _ = private_hub
    hub.m.location("square").art = "opening"
    hub.m.art["opening"] = "[ OPENING SQUARE ]"
    await hub.game.present([{"ref": "scene:square", "slot": "scene"}], key="opening")
    # The current real-save shape already identifies its place through the scene reference.
    hub.state.presentations[0].pop("location")
    hub.state.positions = {"zhouheng": "square", "lin": "tower", "chen": "square"}
    hub.state.apart["lin"] = "tower"
    before = hub.state.model_dump_json()
    assert presentation.current(hub.state, "zhouheng")["scene"]["ref"] == "scene:square"
    assert not presentation.current(hub.state, "lin")
    assert hub._media_view("zhouheng")["slots"]["scene"]["location"] == "square"
    assert not hub._media_view("lin")["slots"]
    assert not hub._welcome("b")["media"]["slots"]
    assert hub.state.model_dump_json() == before  # projection does not rewrite the save or other players' media


@pytest.mark.parametrize("ref", ["scene:square", "image:opening"])
async def test_new_scene_media_keeps_the_host_location_without_exposing_resource_ids(private_hub, ref):
    hub, messages = private_hub
    if ref.startswith("scene:"):
        hub.m.location("square").art = "opening"
    hub.m.art["opening"] = "[ PRIVATE VISION ]"
    await hub.game.present([{"ref": ref, "slot": "scene", "to": ["lin"]}], key="vision")
    assert [cid for cid, _ in messages] == ["b"]
    delivery = hub.state.presentations[0]
    assert delivery["location"] == "square"
    wire = messages[0][1]["ev"]
    assert wire["location"] == "square" and "ref" not in wire
    assert NetGameScreen._media_unpacked(wire)["location"] == "square"
    assert not hub._welcome("a")["media"]["slots"]
    assert not hub._welcome("watch")["media"]["slots"]
    if ref.startswith("image:"):
        assert "PRIVATE VISION" not in json.dumps(hub._welcome("a"))
        assert "PRIVATE VISION" not in json.dumps(hub._welcome("watch"))
    hub.state.apart["lin"] = "tower"
    restored = GameState.model_validate_json(hub.state.model_dump_json())
    assert not presentation.current(restored, "lin")


async def test_scene_view_and_different_scene_clock_use_the_players_physical_location(private_hub):
    hub, _ = private_hub
    hub.m.art["tower-view"] = "[ INSIDE THE TOWER ]"
    hub.state.positions = {"zhouheng": "square", "lin": "tower", "chen": "square"}
    local = SceneView(hub.state, SceneState(id="tower-scene", members=["lin"], place="tower", at=900))
    delivery = presentation.prepare(hub.m, local, {"ref": "image:tower-view", "slot": "scene", "to": ["lin"]}, "tower")
    hub.state.presentations.append(delivery)
    assert hub.state.location == "square" and delivery["location"] == "tower"
    assert presentation.current(local, "lin")["scene"] is delivery
    assert presentation.current(hub.state, "lin")["scene"] is delivery
    assert not presentation.current(hub.state, "zhouheng")
    local.minutes = 950
    assert presentation.current(local, "lin")["scene"] is delivery


def screen_stub(module, state, shown):
    rendered = []
    canvas = SimpleNamespace(show=lambda *args, **kwargs: rendered.append((args, kwargs)))
    screen = SimpleNamespace(m=module, state=state, view=(state.location, state.view), app=SimpleNamespace(art_style="ascii"),
                             _media_slots={"scene": shown}, query=lambda cls: SimpleNamespace(first=lambda: canvas),
                             _sky=lambda loc: None, _topbar=lambda: None, _faces=lambda loc: (),
                             _pending_module=None, _pending_state=None, _panels=lambda: None, _battle_synced=lambda: None)
    screen._scene = MethodType(GameScreen._scene, screen)
    return screen, rendered


async def test_live_client_move_and_state_sync_draw_the_new_places_art_instead_of_the_opening(private_hub):
    hub, _ = private_hub
    hub.m.location("square").art, hub.m.art["opening"] = "opening", "[ OLD SQUARE ]"
    hub.m.location("tower").art, hub.m.art["tower-view"] = "tower-view", "[ NEW TOWER ]"
    saved = presentation.prepare(hub.m, hub.state, {"ref": "scene:square", "slot": "scene"}, "opening")
    shown = NetGameScreen._media_unpacked(hub._media_wire(saved))
    screen, rendered = screen_stub(hub.m, hub.state, shown)
    screen._scene("square")
    assert rendered[-1][0][1] == "[ OLD SQUARE ]"
    # A move notice can precede its authoritative sync. Both paths must choose the new art.
    screen.view = ("tower", "tower-view")
    screen._scene("tower")
    assert rendered[-1][0][1] == "[ NEW TOWER ]"
    newer = hub.state.model_copy(deep=True, update={"location": "tower", "view": "tower-view"})
    NetGameScreen._sync(screen, newer.model_dump(mode="json"))
    assert rendered[-1][0][1] == "[ NEW TOWER ]" and screen.state.location == "tower"
    assert screen._media_slots["scene"] is shown  # no all-table clear or private-media mutation


async def test_single_player_restore_filters_an_old_scene_but_keeps_received_material(private_hub):
    hub, _ = private_hub
    hub.state.investigators = [hub.state.investigators[0]]
    hub.m.art["opening"] = "[ OPENING ]"
    hub.m.location("square").art = "opening"
    await hub.game.present([{"ref": "scene:square", "slot": "scene"}], key="opening")
    await hub.game.present([{"ref": f"handout:{hub.m.handouts[0].id}"}], key="letter")
    hub.state.location = "tower"
    current = presentation.current(hub.state, hub.state.investigator.id)
    assert "scene" not in current and current["overlay"]["payload"]["kind"] == "handout"
    assert presentation.papers(hub.state, hub.state.investigator.id)
