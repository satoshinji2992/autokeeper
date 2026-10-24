"""A received document cannot authorize later mutations through its original art keys."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from autokeeper.engine import presentation
from autokeeper.engine.state import GameState
from autokeeper.net import media
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_media_observation_versions import Receiver, made, picture
from .test_net import TableCast


def paper_for(hub: Hub, viewer: str) -> dict:
    return next(handout for handout in hub._book(viewer)["handouts"] if handout["id"] == hub.m.handouts[0].id)


def give(hub: Hub, ident: str, to: list[str]) -> dict:
    paper = hub.m.handouts[0]
    paper.title, paper.text = "一张旧照片", "照片背面写着：钟楼的守钟人。"
    paper.images = ["warden"]
    delivery = presentation.prepare(hub.m, hub.state, {"ref": f"handout:{paper.id}", "to": to}, ident)
    hub.state.presentations.append(delivery)
    return delivery


async def test_received_handout_keeps_text_and_image_on_share_sync_and_reconnect(fake, tmp_path):
    hub = made(fake, tmp_path)
    delivery = give(hub, "original-photo", ["zhouheng"])
    before = copy.deepcopy(delivery)
    frozen = delivery["payload"]["pictures"]["warden"]
    try:
        old = paper_for(hub, "zhouheng")
        assert old["images"] == [delivery["payload"]["versions"]["warden"]]
        assert old["images"] != ["warden"] and not old["originals"]
        frozen_key = old["images"][0]
        hub.m.art["warden"] = "SECRET NEW PORTRAIT"
        picture(Path(hub.m.pictures["warden"]), "red")
        paper = hub.m.handouts[0]
        paper.title, paper.text = "未公开的新标题", "未公开的新正文"
        saved_before_book = hub.state.model_dump_json()
        assert paper_for(hub, "zhouheng") == old
        assert hub.state.model_dump_json() == saved_before_book
        assert "warden" not in hub._visible_art_keys("zhouheng")
        assert frozen_key in hub._visible_art_keys("zhouheng")
        await hub._share_book()
        await hub._event({"type": "art", "key": "warden", "art": "SECRET NEW PORTRAIT", "status": "ready"})
        welcome = hub._welcome("a")
        sent = json.dumps([*hub.clients["a"].messages, welcome], ensure_ascii=False)
        assert "SECRET NEW PORTRAIT" not in sent and "未公开的新正文" not in sent and "未公开的新标题" not in sent
        assert welcome["picture_data"][frozen_key] == media.pack(frozen, max_size=1600)
        assert delivery == before
        assert not hub._book("lin")["handouts"]

        restored = Hub(EXAMPLE, state=GameState.model_validate_json(hub.state.model_dump_json()),
                       cast=TableCast(), rules=Rules(dice=fake))
        try:
            restored.clients, restored.sits = {"a": Receiver(), "b": Receiver()}, dict(hub.sits)
            assert paper_for(restored, "zhouheng") == old
            assert restored._welcome("a")["picture_data"][frozen_key] == welcome["picture_data"][frozen_key]
            assert restored._media_view("zhouheng")["papers"][0]["payload"]["handout"]["text"] == before["payload"]["handout"]["text"]
        finally:
            await restored.game.close()
    finally:
        await hub.game.close()


async def test_new_delivery_updates_only_its_recipient_and_preserves_the_old_history(fake, tmp_path):
    hub = made(fake, tmp_path)
    first = give(hub, "first-photo", ["zhouheng", "lin"])
    history = copy.deepcopy(first)
    old = paper_for(hub, "lin")
    try:
        paper = hub.m.handouts[0]
        paper.title, paper.text = "照片的新附注", "这份补充只交给周衡。"
        hub.m.art["warden"] = "NEW AUTHORIZED PHOTO"
        picture(Path(hub.m.pictures["warden"]), "red")
        second = presentation.prepare(hub.m, hub.state, {"ref": f"handout:{paper.id}", "to": ["zhouheng"]}, "second-photo")
        hub.state.presentations.append(second)
        newer = paper_for(hub, "zhouheng")
        assert newer["title"] == paper.title and newer["text"] == paper.text
        assert newer["images"] != old["images"]
        assert hub.m.art[newer["images"][0]] == "NEW AUTHORIZED PHOTO"
        assert paper_for(hub, "lin") == old
        assert presentation.history(hub.state, "zhouheng")[0] == history
        assert hub._media_view("lin")["papers"][0]["payload"]["handout"]["text"] == history["payload"]["handout"]["text"]
        await hub._share_book()
        assert "NEW AUTHORIZED PHOTO" not in json.dumps(hub.clients["b"].messages)
        assert "NEW AUTHORIZED PHOTO" in json.dumps(hub.clients["a"].messages)
    finally:
        await hub.game.close()


async def test_a_received_paper_without_version_metadata_never_reauthorizes_mutable_images(fake, tmp_path):
    hub = made(fake, tmp_path)
    delivery = give(hub, "recorded-photo", ["zhouheng"])
    delivery["payload"].pop("versions", None)
    try:
        shown = paper_for(hub, "zhouheng")
        assert shown["title"] == delivery["payload"]["handout"]["title"]
        assert not shown["images"] and not shown["originals"]
        assert "warden" not in hub._visible_art_keys("zhouheng")
        assert hub._media_view("zhouheng")["papers"][0]["payload"]["art"]
    finally:
        await hub.game.close()


@pytest.mark.parametrize("has_delivery", [False, True])
async def test_source_original_images_do_not_reenter_a_received_documents_resource_permissions(fake, tmp_path, has_delivery):
    hub = made(fake, tmp_path)
    paper = hub.m.handouts[0]
    try:
        if has_delivery:
            give(hub, "given-paper", ["zhouheng"])
        else:
            hub.state.handouts.append(paper.id)
            hub.state.add("handout", paper.title, audience=["zhouheng"])
        paper.originals = ["source/page-secret.png"]
        key = paper.original_keys()[0]
        hub.m.art[key] = "MUTABLE ORIGINAL PAGE"
        shown = paper_for(hub, "zhouheng")
        if has_delivery:
            assert shown["originals"] == [] and key not in hub._visible_art_keys("zhouheng")
        else:
            assert shown["originals"] == paper.originals and key in hub._visible_art_keys("zhouheng")
    finally:
        await hub.game.close()
