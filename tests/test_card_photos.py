"""Character image lookup: failures remain distinguishable from empty search results."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import httpx
import pytest

from autokeeper.agents import card


async def test_image_results_without_size_metadata_are_kept(monkeypatch):
    class Search:
        def images(self, query, **kwargs):
            assert query == "永雏塔菲 虚拟主播"
            return [
                {"image": "https://example.org/portrait.png", "title": "人物立绘"},
                {"image": "https://example.org/tiny.png", "width": 32, "height": 32},
                {"title": "缺少图片地址"},
            ]

    monkeypatch.setattr(card.importlib.util, "find_spec", lambda _: object())
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=Search))
    fig = card.Figure("永雏塔菲")
    await card._image_search(fig, "永雏塔菲 虚拟主播")
    assert [p.url for p in fig.photos] == ["https://example.org/portrait.png"]


async def test_failed_search_reports_the_error_and_still_tries_commons(monkeypatch):
    async def images(fig, query):
        raise httpx.ConnectTimeout("test timeout")

    async def commons(client, query, **kwargs):
        assert query == "永雏塔菲"
        raise httpx.HTTPStatusError("test", request=httpx.Request("GET", "https://example.org"),
                                    response=httpx.Response(403))

    monkeypatch.setattr(card, "_image_search", images)
    monkeypatch.setattr(card.imagery, "search", commons)
    fig = card.Figure("永雏塔菲")  # A missing Wikipedia page must not disable the other image source.
    await card.find_photos(fig)
    assert not fig.photos
    assert "图片搜索请求超时" in fig.photo_notice
    assert "HTTP 403" in fig.photo_notice


async def test_empty_successful_search_is_not_reported_as_a_network_failure(monkeypatch):
    async def images(fig, query):
        return

    async def commons(*args, **kwargs):
        return []

    monkeypatch.setattr(card, "_image_search", images)
    monkeypatch.setattr(card.imagery, "search", commons)
    fig = card.Figure("人物", photo_errors=["上次请求超时"])
    await card.find_photos(fig)
    assert fig.photo_errors == []
    assert "未检索到人物图片" in fig.photo_notice
    assert "超时" not in fig.photo_notice


@pytest.mark.parametrize("errors", [[], ["图片搜索请求超时"]])
async def test_photo_picker_explains_why_it_has_no_candidates(monkeypatch, errors):
    from autokeeper.module.schema import Module
    from autokeeper.paths import EXAMPLE
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.card import CardScreen

    app = AutoKeeperApp(theme="amber")
    notices = []
    monkeypatch.setattr(app, "notify", lambda message, **kwargs: notices.append(message))
    async with app.run_test(size=(120, 40)) as pilot:
        screen = CardScreen(Module.load(EXAMPLE))
        app.push_screen(screen)
        await pilot.pause()
        screen.builder.figure = card.Figure("永雏塔菲", photo_errors=errors)
        screen._photos()
        assert screen.step == "photo"
        assert notices[-1] == screen.builder.figure.photo_notice

