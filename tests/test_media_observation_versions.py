"""Personal artwork follows an observed version, never a mutable world resource."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image

from autokeeper import media_snapshot
from autokeeper.engine import perception, presentation
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.engine.visuals import Visuals
from autokeeper.module.schema import Look, Module, View
from autokeeper.net import media
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import Condition, Move

from .test_net import TableCast


def picture(path: Path, color: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 30), color).save(path)
    return path


class Receiver:
    closed = False

    def __init__(self):
        self.messages = []

    async def send_str(self, text):
        self.messages.append(json.loads(text))


def made(fake, tmp_path):
    module = Module.load(EXAMPLE)
    first = module.pregens[0].model_copy(deep=True)
    second = first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [first, second], slot="observed-pictures", human=[first.id, second.id])
    state.location, state.apart = "tower", {"lin": "office"}
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    hub.m.art["warden"], hub.m.art_colors["warden"] = "old face", ""
    hub.m.pictures["warden"] = str(picture(tmp_path / "warden.png", "blue"))
    hub.state.npc("warden").met = True
    hub.clients = {"a": Receiver(), "b": Receiver()}
    hub.sits = {first.id: "a", second.id: "b"}
    hub.game.world.observe("tower", [first.id], "first-sighting")
    return hub


async def test_a_secret_redraw_cannot_replace_old_ascii_or_photos_on_sync_or_reconnect(fake, tmp_path):
    hub = made(fake, tmp_path)
    seen = hub.state.record.sightings["zhouheng"]["warden"].portrait
    frozen = hub.m.pictures[seen]
    before = Path(frozen).read_bytes()
    try:
        assert hub.game.world.apply(Move(entity="warden", to="office", visibility="private")).ok
        hub.m.art["warden"] = "unseen corpse"
        picture(Path(hub.m.pictures["warden"]), "red")  # even replacing the same file cannot rewrite a sighting
        await hub._event({"type": "art", "key": "warden", "art": "unseen corpse", "mask": "", "status": "ready"})
        await hub._share_book()
        welcome = hub._welcome("a")
        assert welcome["art"][seen] == "old face"
        assert welcome["picture_data"][seen] == media.pack(frozen, max_size=512)
        assert "unseen corpse" not in json.dumps([*hub.clients["a"].messages, welcome])
        assert "warden" not in welcome["art"] and "warden" not in welcome["picture_data"]
        assert Path(frozen).read_bytes() == before

        hub.game.world.observe("office", ["lin"], "later-sighting")
        newer = hub.state.record.sightings["lin"]["warden"].portrait
        assert newer != seen and hub.m.art[newer] == "unseen corpse"
        saved = GameState.model_validate_json(hub.state.model_dump_json())
        rebuilt = Hub(EXAMPLE, state=saved, cast=TableCast(), rules=Rules(dice=fake))
        try:
            rebuilt.clients, rebuilt.sits = {"a": Receiver(), "b": Receiver()}, dict(hub.sits)
            assert rebuilt._welcome("a")["art"][seen] == "old face"
            assert rebuilt._welcome("b")["art"][newer] == "unseen corpse"
            assert rebuilt._welcome("a")["picture_data"][seen] == welcome["picture_data"][seen]
        finally:
            await rebuilt.game.close()
    finally:
        await hub.game.close()


async def test_a_late_redraw_updates_only_the_observers_of_that_revision(fake, tmp_path, monkeypatch):
    hub = made(fake, tmp_path)
    old = hub.state.record.sightings["zhouheng"]["warden"].portrait
    try:
        assert hub.game.world.apply(Move(entity="warden", to="office", visibility="private")).ok
        visuals = hub.game.visuals
        visuals.look("warden", "手臂受伤")
        hub.game.world.observe("office", ["lin"], "witnessed-injury")
        seen = hub.state.record.sightings["lin"]["warden"].portrait
        assert seen != old and hub.state.visuals[seen]["pending"]

        async def redraw(record, shown, target):
            hub.m.art["warden"] = "observed bandage"
            return picture(target, "red")

        monkeypatch.setattr(visuals, "_may_make", lambda: True)
        monkeypatch.setattr(visuals, "_redraw", redraw)
        await visuals._vary_inner("warden")
        assert hub.m.art[old] == "old face" and hub.m.art[seen] == "observed bandage"
        assert not hub.state.visuals[seen]["pending"]
        assert "observed bandage" not in json.dumps(hub.clients["a"].messages)
        assert "observed bandage" in json.dumps(hub.clients["b"].messages)
        assert hub.state.record.sightings["zhouheng"]["warden"].portrait == old
    finally:
        await hub.game.close()


async def test_public_status_observed_before_redraw_is_attached_to_its_own_new_version(fake, tmp_path, monkeypatch):
    hub = made(fake, tmp_path)
    old = hub.state.record.sightings["zhouheng"]["warden"].portrait
    try:
        assert hub.game.world.apply(Move(entity="warden", to="office", visibility="private")).ok
        hub.game.world.observe("office", ["lin"], "seen-unhurt")
        assert hub.state.record.sightings["lin"]["warden"].portrait == old
        assert hub.game.world.apply(Condition(entity="warden", status="受伤", visibility="public")).ok
        assert hub.state.record.sightings["lin"]["warden"].status == "受伤"
        assert hub.state.record.sightings["lin"]["warden"].portrait == old
        await hub.game._people()  # The actual order is condition -> observe -> schedule the new appearance.
        seen = hub.state.record.sightings["lin"]["warden"].portrait
        assert seen != old and hub.state.visuals[seen]["pending"]
        assert hub.state.record.sightings["zhouheng"]["warden"].portrait == old

        async def redraw(record, shown, target):
            return picture(target, "red")

        monkeypatch.setattr(hub.game.visuals, "_may_make", lambda: True)
        monkeypatch.setattr(hub.game.visuals, "_redraw", redraw)
        await hub.game.visuals._vary_inner("warden")
        assert hub.m.pictures[seen] != hub.m.pictures[old]
        assert hub._welcome("a")["picture_data"][old] != hub._welcome("b")["picture_data"][seen]
    finally:
        await hub.game.close()


async def test_an_interrupted_late_picture_is_recovered_only_for_its_subscribed_version(fake, tmp_path):
    hub = made(fake, tmp_path)
    old = hub.state.record.sightings["zhouheng"]["warden"].portrait
    try:
        assert hub.game.world.apply(Move(entity="warden", to="office", visibility="private")).ok
        visuals = hub.game.visuals
        visuals.look("warden", "手臂受伤")
        hub.game.world.observe("office", ["lin"], "witnessed-injury")
        seen = hub.state.record.sightings["lin"]["warden"].portrait
        record = hub.state.looks["warden"]
        record.update(status="ready", picture=str(picture(tmp_path / "late.png", "red")),
                      shown_revision=record["revision"])
        saved = GameState.model_validate_json(hub.state.model_dump_json())
        module = Module.load(EXAMPLE)
        module.pictures["warden"] = hub.m.pictures["warden"]

        async def quiet(event):
            pass

        restored = Visuals(module, saved, None, quiet)
        try:
            assert not saved.visuals[seen]["pending"]
            assert module.pictures[seen] != module.pictures[old]
            assert saved.record.sightings["zhouheng"]["warden"].portrait == old
        finally:
            await restored.close()
    finally:
        await hub.game.close()


async def test_a_failed_redraw_does_not_mix_a_new_unseen_change_into_an_old_subscription(fake, tmp_path, monkeypatch):
    hub = made(fake, tmp_path)
    try:
        visuals = hub.game.visuals
        visuals.look("warden", "袖口划破")
        hub.game.world.observe("tower", ["zhouheng"], "seen-torn-sleeve")
        seen = hub.state.record.sightings["zhouheng"]["warden"].portrait
        before = hub.m.pictures[seen]
        record = hub.state.looks["warden"]
        record.update(later="秘密变化", later_revision="unseen-next-version")

        async def failed(record, shown, target):
            raise OSError("offline fixture")

        monkeypatch.setattr(visuals, "_may_make", lambda: True)
        monkeypatch.setattr(visuals, "_redraw", failed)
        await visuals._vary_inner("warden")
        assert record["revision"] == "unseen-next-version" and "later_revision" not in record

        async def redrawn(record, shown, target):
            hub.m.art["warden"] = "unseen change"
            return picture(target, "red")

        monkeypatch.setattr(visuals, "_redraw", redrawn)
        await visuals._vary_inner("warden")
        assert hub.m.pictures[seen] == before and hub.m.art[seen] == "old face"
        assert "unseen change" not in json.dumps(hub.clients["a"].messages)
    finally:
        await hub.game.close()


async def test_repeated_observation_reuses_the_snapshot_without_rereading_the_picture(fake, tmp_path, monkeypatch):
    hub = made(fake, tmp_path)
    try:
        old = hub.state.record.sightings["zhouheng"]["warden"].portrait
        source = Path(hub.m.pictures["warden"])
        read = Path.read_bytes
        reads = []

        def track(path):
            if path == source:
                reads.append(path)
            return read(path)

        monkeypatch.setattr(Path, "read_bytes", track)
        hub.game.world.observe("tower", ["zhouheng"], "second-sighting")
        assert hub.state.record.sightings["zhouheng"]["warden"].portrait == old and not reads
        picture(source, "red")
        hub.game.world.observe("tower", ["zhouheng"], "third-sighting")
        assert hub.state.record.sightings["zhouheng"]["warden"].portrait != old and len(reads) == 1
    finally:
        await hub.game.close()


async def test_place_views_and_state_resolve_to_the_same_personal_asset(fake, tmp_path):
    hub = made(fake, tmp_path)
    try:
        place = hub.m.location("tower")
        place.art = "tower"
        hub.m.art["tower"] = "the clock tower"
        place.views = [View(key="tower_corner", label="窗下")]
        hub.m.art["tower_corner"] = "a narrow window"
        hub.m.pictures["tower_corner"] = str(picture(tmp_path / "window.png", "blue"))
        hub.game.world.observe_place("tower", ["zhouheng"], "window-observed")
        seen = hub.state.record.place_sightings["zhouheng"]["tower"]
        await hub._event({"type": "view", "location": "tower", "key": "tower_corner", "audience": ["zhouheng"]})
        events = [msg["ev"] for msg in hub.clients["a"].messages if msg.get("t") == "event"]
        assert events[0]["type"] == "art" and events[0]["key"] == seen.views["tower_corner"]
        assert events[1]["type"] == "view" and events[1]["key"] == seen.views["tower_corner"]
        assert not hub.clients["b"].messages
        assert hub._view("zhouheng")["view"] == seen.art
        assert hub._welcome("a")["state"]["view"] in hub._welcome("a")["art"]
    finally:
        await hub.game.close()


async def test_a_remote_place_redraw_preserves_old_visited_art_until_the_next_visit(fake, tmp_path, monkeypatch):
    hub = made(fake, tmp_path)
    try:
        hub.m.location("tower").art = "tower"
        hub.m.art["tower"] = "the clock tower"
        hub.m.pictures["tower"] = str(picture(tmp_path / "tower.png", "blue"))
        hub.game.world.observe_place("tower", ["zhouheng"], "seen-tower")
        old = hub.state.record.place_sightings["zhouheng"]["tower"].art
        before = media.pack(hub.m.pictures[old], max_size=512)
        hub.state.positions["zhouheng"] = "office"
        visuals = hub.game.visuals
        visuals.look("tower", "钟楼失火")

        async def redraw(record, shown, target):
            hub.m.art["tower"] = "unseen fire"
            return picture(target, "red")

        monkeypatch.setattr(visuals, "_may_make", lambda: True)
        monkeypatch.setattr(visuals, "_redraw", redraw)
        await visuals._vary_inner("tower")
        assert hub.state.record.place_sightings["zhouheng"]["tower"].art == old
        assert hub._welcome("a")["picture_data"][old] == before
        assert "unseen fire" not in json.dumps(hub._welcome("a"))
        hub.state.positions["zhouheng"] = "tower"
        hub.game.world.observe_place("tower", ["zhouheng"], "returned-to-tower")
        newer = hub.state.record.place_sightings["zhouheng"]["tower"].art
        assert newer != old and hub._view("zhouheng")["view"] == newer
        assert hub._welcome("a")["picture_data"][newer] != before
    finally:
        await hub.game.close()


async def test_an_observed_avatar_version_cannot_bypass_a_personal_appearance_override(fake, tmp_path):
    hub = made(fake, tmp_path)
    try:
        seen = hub.state.record.sightings["zhouheng"]["warden"].portrait
        perception.apply(hub.game, [{"to": ["zhouheng"], "target": "warden", "visible": False,
                                     "reason": "角色此时看不见守钟人"}])
        await hub._share_book()
        await hub._event({"type": "art", "key": seen, "art": "old face", "status": "ready"})
        assert not perception.picture_visible(hub.game, "zhouheng", seen)
        sent = json.dumps(hub.clients["a"].messages)
        assert "old face" not in sent and seen not in sent
    finally:
        await hub.game.close()


async def test_a_predrawn_place_change_is_delivered_at_activation_only_to_current_observers(fake, tmp_path):
    hub = made(fake, tmp_path)
    try:
        place = hub.m.location("tower")
        place.art = "tower"
        place.looks = [Look(when="flag:burnt", change="塔楼烧毁", art="tower_burnt")]
        hub.m.art["tower"] = "clock tower"
        hub.m.pictures["tower"] = str(picture(tmp_path / "tower.png", "blue"))
        hub.m.pictures["tower_burnt"] = str(picture(tmp_path / "tower_burnt.png", "red"))
        hub.state.positions["lin"] = "tower"
        hub.game.world.observe("tower", ["zhouheng", "lin"], "shared-visit")
        hub.state.positions["lin"] = "office"
        old = hub.state.record.place_sightings["lin"]["tower"].art
        hub.state.flags.append("burnt")
        hub.game.visuals.schedule()
        await asyncio.gather(*hub.game.visuals.tasks.values())
        newer = hub.state.record.place_sightings["zhouheng"]["tower"].art
        assert newer != old and hub.state.record.place_sightings["lin"]["tower"].art == old
        assert hub._view("zhouheng")["view"] == newer
        assert hub._welcome("a")["picture_data"][newer] != hub._welcome("b")["picture_data"][old]
        assert any(msg.get("ev", {}).get("key") == newer for msg in hub.clients["a"].messages)
        assert not any(msg.get("ev", {}).get("key") == newer for msg in hub.clients["b"].messages)
    finally:
        await hub.game.close()


def test_explicit_media_delivery_keeps_the_authorized_image_after_source_replacement(tmp_path):
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    source = picture(tmp_path / "original.png", "blue")
    module.pictures["warden"] = str(source)
    delivery = presentation.prepare(module, state, {"ref": "portrait:warden", "to": ["zhouheng"]}, "private-photo")
    state.presentations.append(delivery)
    frozen = delivery["payload"]["picture"]
    before = Path(frozen).read_bytes()
    picture(source, "red")
    restored = GameState.model_validate_json(state.model_dump_json())
    assert Path(presentation.current(restored, "zhouheng")["overlay"]["payload"]["picture"]).read_bytes() == before
    assert not presentation.current(restored, None)
    assert frozen != str(source) and media_snapshot.remember(module, state, "warden")
