"""Photo previews never replace a file a Windows reader holds, or reuse an old source version."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from rich.console import Console
from rich.text import Text
from textual.widgets import Static

from autokeeper import imagery
from autokeeper.agents.card import Figure, Photo
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net import media
from autokeeper.paths import EXAMPLE
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.card import CardScreen
from autokeeper.tui.screens.lobby import LobbyScreen


def test_changed_source_gets_a_new_cache_while_the_old_picture_is_open(tmp_path, monkeypatch):
    monkeypatch.setattr(imagery, "preprocessing_available", lambda: False)
    source = tmp_path / "头像.png"
    Image.new("RGB", (30, 80), "red").save(source)
    old = imagery.preprocess(source, "portrait")
    with old.open("rb") as reader:
        before = reader.read()
        Image.new("RGB", (30, 80), "blue").save(source)
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        new = imagery.preprocess(source, "portrait")
        assert new != old and old.read_bytes() == before
        with Image.open(new) as picture:
            assert picture.getpixel((0, 0))[2] > picture.getpixel((0, 0))[0]
        assert imagery.preprocess(source, "portrait") == new  # Future timestamps must not force another write.


def test_concurrent_previews_and_selection_publish_one_complete_file(tmp_path, monkeypatch):
    monkeypatch.setattr(imagery, "preprocessing_available", lambda: False)
    source = tmp_path / "portrait.png"
    Image.new("RGB", (300, 800), "red").save(source)
    barrier = threading.Barrier(8)
    published = []
    original = imagery._publish_prepared

    def publish(out, data):
        published.append(out)
        original(out, data)

    monkeypatch.setattr(imagery, "_publish_prepared", publish)

    def process(_):
        barrier.wait(timeout=10)
        out = imagery.preprocess(source, "portrait")
        with Image.open(out) as picture:
            picture.load()
        return out

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(process, range(8)))
    assert len(set(results)) == 1 and published == results[:1]


@pytest.mark.parametrize("face", [False, True])
def test_same_path_repaint_invalidates_every_layer_of_the_renderer_cache(tmp_path, monkeypatch, face):
    monkeypatch.setattr(imagery, "preprocessing_available", lambda: False)
    source = tmp_path / "portrait.png"
    monkeypatch.setattr(imagery, "_chafa", lambda im, *a, **kw: str(im.getpixel((im.width // 2, im.height // 2))))
    Image.new("RGB", (30, 80), "red").save(source)
    # Force the framed / portrait layer, not just the final ANSI text layer.
    first = imagery.render(str(source), 30, 14, when="上午", face=face)
    Image.new("RGB", (30, 80), "blue").save(source)
    second = imagery.render(str(source), 30, 14, when="上午", face=face)
    assert first != second


async def test_photo_processing_and_preview_errors_do_not_close_character_creation(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("[WinError 5] cache busy")

    monkeypatch.setattr(imagery, "preprocess", denied)
    monkeypatch.setattr(imagery, "render", denied)
    monkeypatch.setattr(imagery, "available", lambda: True)
    source = tmp_path / "portrait.png"
    Image.new("RGB", (30, 80), "red").save(source)
    app = AutoKeeperApp(theme="amber")
    notices = []
    monkeypatch.setattr(app, "notify", lambda text, **kw: notices.append(text))
    async with app.run_test(size=(120, 40)) as pilot:
        screen = CardScreen(Module.load(EXAMPLE))
        await app.push_screen(screen)
        screen.builder.figure = Figure("调查员", photos=[Photo(str(source))])
        screen.shots[0] = source
        screen.from_card = True
        screen.builder.card = screen.m.pregens[0].model_copy(deep=True)
        worker = screen.pick_photo(0)
        await worker.wait()
        await pilot.pause()
        assert app.screen is screen and screen.step == "card" and screen.chosen == source
        assert any("使用原图" in text for text in notices)


@pytest.mark.parametrize("change", ["card", "new_card", "photo", "same_file"])
async def test_lobby_refreshes_changed_cards_and_portraits_without_rejoining(tmp_path, monkeypatch, change):
    monkeypatch.setattr(LobbyScreen, "_connect", lambda self: None)
    monkeypatch.setattr(imagery, "available", lambda: True)
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    lead.portrait = "my-face"
    state = Game.new(module, [lead], human=[lead.id])
    source = tmp_path / "portrait.png"
    Image.new("RGB", (30, 80), "red").save(source)

    def face(m, key, style):
        with Image.open(m.pictures[key]) as picture:
            red, _, blue = picture.getpixel((0, 0))
            return Text("红色头像" if red > blue else "蓝色头像")

    monkeypatch.setattr("autokeeper.tui.screens.lobby.face_text", face)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        lobby = LobbyScreen("127.0.0.1:8765", "玩家")
        await app.push_screen(lobby)
        await lobby.on_msg({"t": "welcome", "module": module.model_dump(mode="json"),
                            "state": state.model_dump(mode="json"), "pictures": {lead.portrait: str(source)},
                            "seats": [{"investigator": lead.id, "human": True, "online": False}], "bring": True})
        await pilot.pause(0.2)

        def body():
            console = Console(width=100, no_color=True)
            with console.capture() as capture:
                console.print(lobby.query_one("#lobby-sheet", Static).content)
            return capture.get()

        assert "红色头像" in body()
        if change in ("card", "new_card"):
            state.investigators[0].name = "新角色"
            if change == "new_card":
                state.investigators[0].id = "replacement-card"
            await lobby.on_msg({"t": "sync", "state": state.model_dump(mode="json")})
            if change == "new_card":
                assert "等待角色资料" in body() and lead.name not in body()
                await lobby.on_msg({"t": "seats", "seats": [{"investigator": "replacement-card",
                                                            "human": True, "online": False}]})
            assert "新角色" in body() and "新角色" in str(lobby.query_one("#lobby-investigator", Static).content)
        else:
            Image.new("RGB", (30, 80), "blue").save(source)
            if change == "photo":
                await lobby.on_msg({"t": "face", "key": lead.portrait, "picture_data": media.pack(str(source))})
            else:
                await lobby.on_msg({"t": "sync", "state": state.model_dump(mode="json")})
            await pilot.pause(0.2)
            assert "蓝色头像" in body() and "红色头像" not in body()
