"""Illustrations choose a photo or its fallback; actual handouts keep image and text."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image
from textual.app import App
from textual.widgets import Button, Static

from autokeeper import imagery
from autokeeper.tui.modals.dialogs import HandoutModal
from autokeeper.tui.screens import game as game_screen

FALLBACK = "  ___\n (o o)\n  /_\\"
PHOTO = "\x1b[38;2;236;176;72mPHOTO PREVIEW\x1b[0m"


class MediaApp(App):
    CSS = """
    Screen { align: center middle; background: #110e08; color: #ffb000; }
    #handout-modal { width: 70; height: auto; max-height: 94%; border: solid #ffb000; padding: 1 2; }
    .modal-body { height: auto; max-height: 36; }
    Static { height: auto; }
    Button { margin: 1 0; }
    """
    art_style = "photo"

    def __init__(self, modal: HandoutModal):
        super().__init__()
        self.modal = modal

    def on_mount(self) -> None:
        self.push_screen(self.modal)


@pytest.fixture
def original(tmp_path):
    path = tmp_path / "portrait.png"
    Image.new("RGB", (32, 40), "#4e4e4e").save(path)
    return str(path)


def rendered(modal: HandoutModal) -> str:
    return "\n".join(str(widget.render()) for widget in modal.query(Static))


def reveal_timers(modal, monkeypatch):
    started = []
    original = modal.set_interval

    def track(interval, callback, *args, **kwargs):
        if callback == modal._develop:
            started.append(interval)
        return original(interval, callback, *args, **kwargs)

    monkeypatch.setattr(modal, "set_interval", track)
    return started


async def test_a_photo_illustration_has_one_image_and_no_fallback_paper_or_reveal_animation(monkeypatch, original):
    monkeypatch.setattr(imagery, "available", lambda: True)
    monkeypatch.setattr(imagery, "render", lambda *args: PHOTO)
    modal = HandoutModal("开门的妇人", "这不是一张纸面材料", [original], kind="插图", fallback_art=FALLBACK)
    timers = reveal_timers(modal, monkeypatch)
    app = MediaApp(modal)
    async with app.run_test(size=(90, 32)) as pilot:
        await pilot.pause()
        assert modal.sheet is None and not modal.query("#paper") and not timers
        assert "开门的妇人" in str(modal.query_one("#illustration-title", Static).render())
        assert "PHOTO PREVIEW" in rendered(modal) and FALLBACK not in rendered(modal)
        assert "这不是一张纸面材料" not in rendered(modal)
        assert len(modal.query("#handout-image-0")) == 1 and not modal.query("#illustration-fallback")
        assert modal.query_one("#original-0", Button)
        modal._develop()
        assert modal.revealed == 0


@pytest.mark.parametrize("failure", ["unavailable", "raises", "empty"])
async def test_an_unrenderable_photo_uses_the_ascii_fallback_without_paper(monkeypatch, original, failure):
    monkeypatch.setattr(imagery, "available", lambda: failure != "unavailable")

    def render(*args):
        if failure == "raises":
            raise OSError("unreadable image")
        return ""

    monkeypatch.setattr(imagery, "render", render)
    modal = HandoutModal("人物特写", "", [original], kind="插图", fallback_art=FALLBACK)
    async with MediaApp(modal).run_test(size=(90, 32)) as pilot:
        await pilot.pause()
        assert FALLBACK in rendered(modal) and not modal.query("#paper")
        assert not modal.query("#illustration-fallback") and "PHOTO PREVIEW" not in rendered(modal)
        assert modal.query_one("#original-0", Button)


async def test_an_illustration_without_a_picture_shows_only_its_ascii_fallback(monkeypatch):
    def render(*args):
        pytest.fail("没有原图不应调用图像渲染器")

    monkeypatch.setattr(imagery, "render", render)
    modal = HandoutModal("钟楼剪影", "", kind="插图", fallback_art=FALLBACK)
    timers = reveal_timers(modal, monkeypatch)
    async with MediaApp(modal).run_test(size=(90, 32)) as pilot:
        await pilot.pause()
        assert FALLBACK in str(modal.query_one("#illustration-fallback", Static).render())
        assert not modal.query("#paper") and not modal.query(Button) and not timers


async def test_a_real_handout_retains_its_scanned_image_and_complete_text(monkeypatch, original):
    monkeypatch.setattr(imagery, "available", lambda: True)
    monkeypatch.setattr(imagery, "render", lambda *args: PHOTO)
    modal = HandoutModal("县卫生部门来信", "第一段说明病况。\n最后一段保留统计数字。", [original], kind="信件")
    timers = reveal_timers(modal, monkeypatch)
    async with MediaApp(modal).run_test(size=(90, 32)) as pilot:
        await pilot.pause()
        assert modal.sheet is not None and timers
        modal.revealed = modal.sheet.size - 1
        modal._develop()
        assert "PHOTO PREVIEW" in rendered(modal)
        assert "第一段说明病况" in rendered(modal) and "最后一段保留统计数字" in rendered(modal)
        assert not modal.query("#illustration-title") and not modal.query("#illustration-fallback")


def test_game_screen_marks_image_payload_as_fallback_without_altering_delivery(monkeypatch, original):
    called, opened = [], []
    real = game_screen.HandoutModal

    def modal(*args, **kwargs):
        called.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(game_screen, "HandoutModal", modal)
    app = SimpleNamespace(screen=None, push_screen=opened.append)
    screen = SimpleNamespace(_media_slots={}, _media_papers={}, _media_seen=set(), _media_overlay=None,
                             app=app, _flash_off=lambda: None)
    app.screen = screen
    event = {"id": "private-portrait", "audience": ["lin"], "slot": "overlay", "action": "show", "title": "妇人",
             "payload": {"kind": "image", "picture": original, "art": FALLBACK, "colors": ""}}
    before = deepcopy(event)
    game_screen.GameScreen._present(screen, event)
    assert called == [(("妇人", "", [original]), {"kind": "插图", "fallback_art": FALLBACK})]
    assert event == before and screen._media_slots["overlay"] is event
    assert len(opened) == 1 and opened[0].sheet is None and not screen._media_papers
