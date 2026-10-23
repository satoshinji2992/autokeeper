"""Held multiplayer narration keeps its original scene and durable delivery scope."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Narration, Ruling
from autokeeper.engine.game import Turn
from autokeeper.engine.state import GameState
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_multiplayer_transport import transport_hub  # noqa: F401
from .test_net import TableCast


@pytest.fixture
def recorded(transport_hub, monkeypatch):  # noqa: F811
    hub = transport_hub
    reports = []

    async def keeper(state, report, on_delta=None, lore="", *, check=None, fix="", settled=False):
        reports.append((report, settled))
        text = "；".join(marker for marker in ("A_RESULT", "B_RESULT") if marker in report)
        scene = {"room": "square"} if settled else {}
        if check is not None:
            check(scene)
        return Narration(text=text or "现场结果。", scene=scene)

    monkeypatch.setattr(hub.cast, "keeper", keeper)
    hub.m.foreshadow.clear()
    return hub, reports, keeper


def acted(hub, marker):
    st = hub.state
    return Turn(action=Action(kind="use", summary=marker), interval={
        "actor": st.investigator.id, "start": st.minutes, "end": st.minutes,
        "participants": [st.investigator.id], "origin": st.location, "destination": st.location,
    })


async def test_split_scene_results_use_separate_keeper_inputs_and_readers(recorded):
    hub, reports, _ = recorded
    game, st = hub.game, hub.state
    st.location, st.apart, st.turn = "square", {"lin": "tower"}, 1
    game.holding = True
    await game.narrate(acted(hub, "A_RESULT"))
    st.active, st.location, st.apart, st.turn = 1, "tower", {"zhouheng": "square"}, 2
    game.holding = False
    await game.narrate(acted(hub, "B_RESULT"))
    assert len(reports) == 2
    assert all(not ("A_RESULT" in report and "B_RESULT" in report) for report, _ in reports)
    told = [entry for entry in st.log if entry.kind == "keeper"]
    assert [(entry.text, entry.audience) for entry in told] == [("A_RESULT", ["zhouheng"]), ("B_RESULT", ["lin"])]
    assert not st.held


async def test_same_scene_still_gets_one_combined_telling(recorded):
    hub, reports, _ = recorded
    game, st = hub.game, hub.state
    st.location, st.turn = "square", 1
    game.holding = True
    await game.narrate(acted(hub, "A_RESULT"))
    assert len(st.held) == 1 and not reports
    st.active, st.turn, game.holding = 1, 2, False
    await game.narrate(acted(hub, "B_RESULT"))
    assert len(reports) == 1 and all(marker in reports[0][0] for marker in ("A_RESULT", "B_RESULT"))
    assert [entry.audience for entry in st.log if entry.kind == "keeper"] == [None]
    assert not st.held


async def test_solo_move_does_not_pull_earlier_shared_report_into_private_destination(recorded):
    hub, reports, _ = recorded
    game, st = hub.game, hub.state
    st.location, st.turn = "square", 1
    game.holding = True
    await game.narrate(acted(hub, "A_RESULT"))
    st.active, st.turn = 1, 2
    moved = await game.resolve(Action(kind="move", summary="B_RESULT", destination="tower", alone=True),
                               Ruling(needs_roll=False))
    before = st.record.seq
    game.holding = False
    await game.narrate(moved)
    assert len(reports) == 2 and reports[0][1] and not reports[1][1]
    assert all(not ("A_RESULT" in report and "B_RESULT" in report) for report, _ in reports)
    told = [entry for entry in st.log if entry.kind == "keeper"]
    assert [(entry.text, entry.audience) for entry in told] == [("A_RESULT", None), ("B_RESULT", ["lin"])]
    assert game.world.place("zhouheng") == "square" and game.world.place("lin") == "tower"
    assert st.record.seq == before  # the old report's offered scene was not executed


async def test_held_delivery_restart_uses_saved_scope_without_reasking_or_replaying_world(recorded, fake):
    hub, reports, keeper = recorded
    game, st = hub.game, hub.state
    st.location, st.apart, st.turn = "square", {"lin": "tower"}, 2
    st.held = [
        {"turn": 1, "who": "周衡", "actor": "zhouheng", "place": "square", "audience": ["zhouheng"],
         "report": "A_RESULT", "rest": None},
        {"turn": 2, "who": "林秋", "actor": "lin", "place": "tower", "audience": ["lin"],
         "report": "B_RESULT", "rest": None},
    ]
    saved = []

    def stop_after_first_delivery():
        if st.held and st.held[0].get("telling", {}).get("body_ended"):
            saved.append(st.model_dump_json())
            raise asyncio.CancelledError

    game.checkpoint = stop_after_first_delivery
    with pytest.raises(asyncio.CancelledError):
        await game.tell_held()
    assert len(reports) == 1
    restored = Hub(EXAMPLE, state=GameState.model_validate_json(saved[-1]), cast=TableCast(), rules=Rules(dice=fake))
    restored.cast.keeper = keeper
    before = restored.state.record.seq
    await restored.game.tell_held()
    assert len(reports) == 2  # first group's persisted text is reused
    assert not restored.state.held and restored.state.record.seq == before
    told = [entry for entry in restored.state.log if entry.kind == "keeper"]
    assert [(entry.text, entry.audience) for entry in told] == [("A_RESULT", ["zhouheng"]), ("B_RESULT", ["lin"])]


@pytest.mark.parametrize("kind", ["showcase", "art", "plugin_effect", "plugin_panel"])
async def test_repacked_visual_and_plugin_events_keep_their_source_audience(recorded, monkeypatch, kind):
    from autokeeper.net import media

    hub, _, _ = recorded
    hub.clients = dict.fromkeys(("a", "b", "watcher"))
    hub.sits = {"zhouheng": "a", "lin": "b"}
    sent = []

    async def send(cid, message):
        sent.append((cid, message))

    monkeypatch.setattr(hub, "_send", send)
    monkeypatch.setattr(media, "pack", lambda *args, **kwargs: "fake-picture")
    key = "private-scene"
    monkeypatch.setattr(hub, "_art_keys", lambda book: {key})
    await hub._event({"type": kind, "key": key, "picture": "fake-file", "audience": ["lin"],
                      "plugin": "internal-plugin", "text": "private-result"})
    events = [(cid, message["ev"]) for cid, message in sent if message["t"] == "event"]
    assert len(events) == 1 and events[0][0] == "b" and events[0][1]["audience"] == ["lin"]


async def test_personal_map_and_every_picture_envelope_exclude_another_seats_private_visit(recorded, monkeypatch):
    from autokeeper.module.schema import Location
    from autokeeper.net import media
    from autokeeper.world.record import Entity

    hub, _, _ = recorded
    st, key = hub.state, "private-room-picture"
    hub.m.locations.append(Location(id="private-room", name="无人共享的密室", read_aloud="屋里只有一盏红灯。",
                                   keeper="", exits=[hub.m.start], art=key))
    hub.m.art[key], hub.m.art_colors[key], hub.m.pictures[key] = "PRIVATE ASCII", "PRIVATE MASK", "fake-private-photo"
    st.location, st.apart, st.visited = "private-room", {"zhouheng": hub.m.start}, [hub.m.start, "private-room"]
    st.heard["private-room"] = "无人共享的密室"
    st.record.entities["private-room"] = Entity(id="private-room", kind="location", name="无人共享的密室",
                                                observed_by=["lin"])
    st.add("keeper", "你走进无人共享的密室。", audience=["lin"])
    hub.clients, hub.sits = dict.fromkeys(("a", "b", "watcher")), {"zhouheng": "a", "lin": "b"}
    sent = []

    async def send(cid, message):
        sent.append((cid, message))

    monkeypatch.setattr(hub, "_send", send)
    monkeypatch.setattr(media, "pack", lambda path, **kwargs: "PRIVATE BITMAP" if "private" in path else "")
    before = st.model_dump(mode="json")
    for cid, seat in (("a", "zhouheng"), ("b", "lin"), ("watcher", None)):
        book, view = hub._book(seat), hub._view(seat)
        assert ("private-room" in {loc["id"] for loc in book["locations"]}) == (cid == "b")
        assert ("private-room" in view["visited"]) == (cid == "b")
        assert ("private-room" in view["heard"]) == (cid == "b")
        assert view["location"] == ("private-room" if cid == "b" else hub.m.start)
        GameState.model_validate(view)
    await hub._share_book()
    await hub._picture(key)
    await hub._event({"type": "art", "key": key, "art": "PRIVATE ASCII", "status": "ready"})
    for cid, message in sent:
        if cid != "b":
            assert key not in message.get("art", {}) and key not in message.get("colors", {})
            assert message.get("ev", {}).get("key") != key
    assert any(cid == "b" and message.get("ev", {}).get("key") == key for cid, message in sent)
    assert st.model_dump(mode="json") == before


async def test_restored_completed_batch_tells_held_results_before_collecting_new_actions(recorded, monkeypatch):
    hub, _, _ = recorded
    hub.started.set()
    hub.clients = {"a": None}
    hub.state.held = [{"turn": 1, "who": "周衡", "actor": "zhouheng", "place": "square", "audience": None,
                       "report": "A_RESULT", "rest": None}]
    order = []

    async def quiet(*args, **kwargs):
        return None

    async def tell():
        order.append("tell")
        hub.state.held.clear()

    async def collect():
        order.append("collect")
        raise asyncio.CancelledError

    monkeypatch.setattr(hub, "_broadcast", quiet)
    monkeypatch.setattr(hub, "_tell_held", tell)
    monkeypatch.setattr(hub, "_collect", collect)
    with pytest.raises(asyncio.CancelledError):
        await hub.play()
    assert order == ["tell", "collect"]
