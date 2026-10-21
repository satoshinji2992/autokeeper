"""The module's own missing pictures: a photo found for each, else one Codex draws, kept for all its
games; no picture at all when neither can be had (pictures.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from autokeeper import config, imagegen, imagery, paths, pictures
from autokeeper.engine.visuals import Visuals
from autokeeper.imagery import Candidate
from autokeeper.module.schema import NPC, Creature, Item, Module
from autokeeper.paths import EXAMPLE
from autokeeper.pictures import fill as real_fill

REAL_GAPS = Visuals.gaps  # tests/conftest.py turns them off for every other test


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.npcs.append(NPC(id="paperboy", name="卖报的孩子", role="街角卖报的孩子"))  # a passer-by
    m.items.append(Item(id="ledger", name="钟楼账本", description="皮面的旧账本", where="office"))
    m.items.append(Item(id="cup", name="茶杯", description="缺了口的茶杯", where="office", important=False))
    m.creatures.append(Creature(id="hound", name="猎犬", description="从角落里渗出来的瘦长之物"))
    return m


def test_what_has_no_picture_of_its_own_and_passers_by_get_none(module):
    keys = {g.key: g.kind for g in pictures.gaps(module)}
    assert keys["scene-tower"] == "scene" and keys["warden"] == "portrait" and keys["item-ledger"] == "item"
    assert keys["creature-hound"] == "creature"
    assert "face-paperboy" not in keys and "item-cup" not in keys  # a passer-by, a thing not worth registering
    assert not module.npc("paperboy").portrait  # and passers-by get none


async def test_a_photo_first_then_a_drawing_kept_for_every_game(module, monkeypatch):
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    searched, drawn = [], []

    async def found(cfg, client, subject, kind, *, on_reply=None, web=False):
        searched.append((kind, web))
        if kind == "scene":
            return Image.new("RGB", (640, 400), (90, 80, 60)), Candidate("Old tower", "p", "t", "CC0", "A", 640, 400)
        return None

    async def make(text, target, *, kind, module, **kwargs):
        drawn.append((kind, text))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (300, 400)).save(target)
        return Path(target)

    monkeypatch.setattr(imagery, "find_picture", found)
    monkeypatch.setattr(imagegen, "make", make)
    cfg = config.Config()
    lines: list[str] = []
    done = await real_fill(cfg, module, log=lines.append)
    assert set(done) == {"square", "scene-tower", "scene-office", "warden", "preacher", "zhouheng", "item-ledger", "creature-hound"}
    assert all(web for _, web in searched) and ("creature", True) not in searched  # nobody has photographed a creature
    assert {kind for kind, _ in drawn} == {"portrait", "item", "creature"}  # no photo fitted: drawn
    tower = paths.module_art(module.id) / "places" / "scene-tower.jpg"
    assert tower.is_file() and module.pictures["scene-tower"] == str(tower) and module.location("tower").art == "scene-tower"
    assert next(i for i in module.items if i.id == "ledger").art == "item-ledger"
    assert not pictures.gaps(module)
    again = Module.load(EXAMPLE)  # another game of it: what was found and drawn before
    assert set(pictures.adopt(again)) >= {"square", "scene-tower", "warden"}
    assert again.location("tower").art == "scene-tower" and not pictures.gaps(again)


async def test_nothing_to_be_had_leaves_it_without_a_picture(module, monkeypatch):
    async def none(*args, **kwargs):
        return None

    monkeypatch.setattr(imagery, "find_picture", none)
    lines: list[str] = []
    assert await real_fill(config.Config(), module, log=lines.append) == []  # Codex is off in tests
    assert any("暂时不配图" in line for line in lines)
    assert await real_fill(config.Config(search="none"), module, log=lines.append) == []  # nothing to look with


async def test_a_game_fills_them_in_the_background_and_the_screen_hears(module, monkeypatch):
    from autokeeper.engine.game import Game

    events = []

    async def emit(event):
        events.append(event)

    async def found(cfg, client, subject, kind, *, on_reply=None, web=False):
        return Image.new("RGB", (640, 400), (90, 80, 60)), Candidate("Photo", "p", "t", "CC0", "A", 640, 400)

    monkeypatch.setattr(pictures, "fill", real_fill)
    monkeypatch.setattr(Visuals, "gaps", REAL_GAPS)
    monkeypatch.setattr(imagery, "find_picture", found)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)], slot="gaps")
    visuals = Visuals(module, state, config.Config(), emit)
    visuals.schedule()
    await visuals.tasks["gaps"]
    arrived = {e["key"] for e in events if e["type"] == "art"}
    assert {"scene-tower", "warden", "item-ledger"} <= arrived and all(Path(e["picture"]).is_file() for e in events)
    quiet = Visuals(module, state, config.Config(), emit)
    quiet.fill_gaps = False  # where nobody sees pictures
    quiet.schedule()
    assert "gaps" not in quiet.tasks


async def test_what_came_to_nothing_is_not_looked_for_again_for_a_while(module, monkeypatch):
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    looked, drawn = [], []

    async def none(cfg, client, subject, kind, *, on_reply=None, web=False):
        looked.append(subject)

    async def broken(text, target, *, kind, module, **kwargs):
        drawn.append(kind)
        raise imagegen.Failed("没画成")

    monkeypatch.setattr(imagery, "find_picture", none)
    monkeypatch.setattr(imagegen, "make", broken)
    lines: list[str] = []
    assert await real_fill(config.Config(), module, log=lines.append) == []
    first, attempts = len(looked), len(drawn)
    assert first and attempts
    await real_fill(config.Config(), module, log=lines.append)
    assert len(looked) == first and len(drawn) == 2 * attempts  # searched once; a drawing gets a second try
    await real_fill(config.Config(), module, log=lines.append)
    assert len(looked) == first and len(drawn) == 2 * attempts  # then rests for pictures.RETRY days
    await real_fill(config.Config(), module, log=lines.append, force=True)
    assert len(looked) == 2 * first  # unless asked to (art prepare --force)


async def test_a_search_that_breaks_is_not_held_against_it(module, monkeypatch):
    calls = []

    async def offline(cfg, client, subject, kind, *, on_reply=None, web=False):
        calls.append(subject)
        raise OSError("no network")

    monkeypatch.setattr(imagery, "find_picture", offline)
    await real_fill(config.Config(), module, log=lambda _: None)
    await real_fill(config.Config(), module, log=lambda _: None)
    assert len(calls) == 2 * len([g for g in pictures.gaps(module) if g.kind != "creature"])


def test_pictures_are_looked_for_by_what_can_be_seen_never_the_keepers_notes(module):
    module.npcs.append(NPC(id="killer", name="哈罗德·格雷", role="真凶，杀了三个人", look="灰色大衣的中年人", goal="灭口"))
    subjects = {g.key: g.subject for g in pictures.gaps(module)}
    assert subjects["face-killer"] == "灰色大衣的中年人"
    assert not any("真凶" in s or "哈罗德" in s for s in subjects.values())


async def test_the_wait_runs_from_the_last_real_try(module, monkeypatch):
    import datetime as dt
    import json

    async def none(*args, **kwargs):
        return None

    monkeypatch.setattr(imagery, "find_picture", none)
    target = paths.module_art(module.id) / "places" / "scene-tower.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    then = (dt.datetime.now() - dt.timedelta(days=6)).isoformat(timespec="seconds")
    note = target.with_suffix(".tried.json")
    note.write_text(json.dumps({"at": then, "searched": True, "failed": 2}), encoding="utf-8")
    await real_fill(config.Config(), module, log=lambda _: None)
    assert json.loads(note.read_text(encoding="utf-8"))["at"] == then  # nothing tried for it this time
