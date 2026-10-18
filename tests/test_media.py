from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from autokeeper import config
from autokeeper.config import EXAMPLE
from autokeeper.net import media
from autokeeper.net.hub import Hub
from autokeeper.tui.net import table_module

from .test_net import TableCast


def test_a_remote_player_can_open_a_picture_without_the_hosts_filesystem(monkeypatch, tmp_path):
    host = tmp_path / "host" / "scene.jpg"
    host.parent.mkdir()
    Image.new("RGB", (1920, 1080), (30, 40, 50)).save(host)
    wire = media.pack(str(host))
    host.unlink()
    monkeypatch.setattr(config, "SAVES", tmp_path / "player")
    cached = Path(media.unpack(wire))
    assert cached.is_file() and cached.is_relative_to(config.SAVES)
    with Image.open(cached) as image:
        assert max(image.size) <= 512
    assert not media.unpack("not a photo")
    assert not media.unpack("a" * (media.LIMIT * 2 + 1))
    oversized = io.BytesIO()
    Image.new("RGB", (600, 1)).save(oversized, "JPEG")
    assert not media.unpack(base64.b64encode(oversized.getvalue()).decode())


def test_unlocked_handout_images_keep_more_detail_than_scene_thumbnails(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVES", tmp_path / "player")
    original = tmp_path / "map.png"
    Image.new("RGB", (1500, 2000), (30, 40, 50)).save(original)
    encoded = media.pack(str(original), max_size=1600)
    assert not media.unpack(encoded)
    with Image.open(media.unpack(encoded, max_size=1600)) as picture:
        assert picture.size == (1200, 1600)


async def test_original_handouts_only_travel_to_players_after_discovery(monkeypatch, tmp_path, fake):
    from autokeeper.rules.coc import Rules

    monkeypatch.setattr(config, "SAVES", tmp_path / "cache")
    image = tmp_path / "private-map.png"
    Image.new("RGB", (1000, 1400), (30, 40, 50)).save(image)
    hub = Hub(EXAMPLE, cast=TableCast(), rules=Rules(dice=fake))
    handout = hub.m.handouts[0]
    handout.images = ["private_map"]
    hub.m.pictures["private_map"] = str(image)
    before = hub._welcome("player")
    assert "private_map" not in before["picture_data"]
    assert not table_module(before).pictures
    sent = []

    async def record(message):
        sent.append(message)

    monkeypatch.setattr(hub, "_broadcast", record)
    hub.state.handouts.append(handout.id)
    await hub._event({"type": "handout", "id": handout.id, "title": handout.title, "text": handout.text})
    event = next(message["ev"] for message in sent if message["t"] == "event")
    assert "private_map" in event["picture_data"]
    welcome = hub._welcome("player")
    cached = table_module(welcome)
    assert cached.handouts[0].text == handout.text and Path(cached.pictures["private_map"]).is_file()
    assert str(image) not in str(welcome)
