"""Improvised illustrations are independent, non-blocking, persistent and disposable."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image

from autokeeper import artkit, config, imagery
from autokeeper.agents import artist
from autokeeper.agents.cast import Action, Ruling
from autokeeper.agents.director import Improv, NewItem, NewLocation, NewNPC
from autokeeper.config import EXAMPLE
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.engine.visuals import Visuals
from autokeeper.harness import Reply
from autokeeper.module import improv
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp, GameScreen
from autokeeper.tui.widgets import ItemCard

from .test_engine import FakeCast
from .test_tui import canned, until  # noqa: F401


def scene():
    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="../../same-save")
    plan = Improv(
        kind="展开", reason="在广场边歇脚",
        locations=[NewLocation(id="cafe", name="街角咖啡馆", read_aloud="临街的小店，窗上蒙着热气。", exits=["square"])],
        npcs=[NewNPC(id="owner", name="店主", role="有秘密的店主", look="围裙上沾着咖啡渍", location="cafe")],
        items=[NewItem(id="cup", name="蓝边瓷杯", description="杯沿一圈靛蓝色的细线。", where="cafe")],
    )
    assert improv.check(m, plan, at="square") == []
    improv.apply(m, plan)
    st.improv.append({"turn": 1, "kind": "展开", "plan": plan.model_dump()})
    return m, st


async def silent(_):
    pass


async def test_images_arrive_in_background_and_survive_loading(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVES", tmp_path)
    gate = asyncio.Event()
    subjects, events = [], []

    async def draw(cfg, subject, *, portrait=False, on_reply=None):
        subjects.append(subject)
        await gate.wait()
        on_reply(Reply(usage={"input_tokens": 10, "output_tokens": 5}))
        text = " /--\\\n |__|"
        return text, artkit.auto_mask(text)

    async def photos(cfg, folder, targets, **kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        for key, _, _ in targets:
            Image.new("RGB", (16, 16), (30, 40, 50)).save(folder / f"{key}.jpg")

    async def emit(event):
        events.append(event)

    monkeypatch.setattr(artist, "draw", draw)
    monkeypatch.setattr(imagery, "fetch_folder", photos)
    m, st = scene()
    visuals = Visuals(m, st, config.Config(), emit)
    visuals.schedule()
    assert len(visuals.tasks) == 3 and len(st.visuals) == 3
    assert m.location("cafe").art != m.location("square").art
    assert all(m.art[key].strip() for key in st.visuals)
    pending_save = st.model_dump_json()
    await asyncio.sleep(0)
    assert not any(t.done() for t in visuals.tasks.values())
    gate.set()
    await asyncio.gather(*visuals.tasks.values())
    assert len(events) == 6 and len(m.pictures) == 3
    assert "有秘密" not in "\n".join(subjects)
    assert st.usage["visual:artist"]["calls"] == 3
    assert visuals.folder.is_relative_to(tmp_path / "art")
    saved = GameState.model_validate_json(pending_save)  # the last complete turn, before the images finished
    loaded = Module.load(EXAMPLE)
    improv.restore(loaded, saved.improv)
    again = Visuals(loaded, saved, config.Config(), silent)
    again.schedule()
    assert not again.tasks and loaded.pictures == m.pictures
    assert all(loaded.art[key] == m.art[key] for key in saved.visuals)
    await visuals.close()
    await again.close()


async def test_illustration_failure_and_cancellation_do_not_break_play(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVES", tmp_path)

    async def broken(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr(artist, "draw", broken)
    m, st = scene()
    visuals = Visuals(m, st, config.Config(search="none"), silent)
    visuals.schedule()
    await asyncio.gather(*visuals.tasks.values())
    assert all(record["status"] == "failed" and record["art"] for record in st.visuals.values())
    assert all(record["error"] == "ConnectionError" for record in st.visuals.values())
    await visuals.close()

    async def slow(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(artist, "draw", slow)
    m, st = scene()
    st.slot = "cancel-test"
    visuals = Visuals(m, st, config.Config(search="none"), silent)
    visuals.schedule()
    await asyncio.sleep(0)
    await visuals.close()
    assert all(task.done() for task in visuals.tasks.values())
    assert all(record["status"] == "pending" for record in st.visuals.values())


async def test_inspecting_an_improvised_prop_shows_its_picture_without_giving_a_clue(fake):
    m, st = scene()
    st.location = "cafe"
    events = []

    async def emit(event):
        events.append(event)

    game = Game(m, st, FakeCast({}), Rules(dice=fake), emit)
    turn = await game.resolve(Action(kind="daily", summary="看看瓷杯", target="蓝边瓷杯"), Ruling(needs_roll=False))
    shown = [event for event in events if event["type"] == "item"]
    assert len(shown) == 1 and shown[0]["name"] == "蓝边瓷杯"
    assert m.art[shown[0]["art"]] and not turn.clues and not turn.checks
    assert "杯沿一圈靛蓝色的细线" in game._report(turn)
    await game.close()


def test_items_are_validated_as_world_entities():
    m = Module.load(EXAMPLE)
    bad = Improv(kind="展开", reason="无效物件", items=[NewItem(id="square", name="杯子", description="白瓷", where="nowhere")])
    problems = improv.check(m, bad, at="square")
    assert any("已经存在" in p for p in problems) and any("不存在" in p for p in problems)


async def test_switching_models_also_restarts_pending_art_on_the_new_model(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(config, "SAVES", tmp_path)
    gate = asyncio.Event()
    configs = []

    async def draw(cfg, *args, **kwargs):
        configs.append(cfg)
        await gate.wait()
        return "[]", "yy"

    monkeypatch.setattr(artist, "draw", draw)
    m, st = scene()
    original, replacement = config.Config(search="none"), config.Config(search="none")
    game = Game(m, st, SimpleNamespace(cfg=original), Rules(dice=fake))
    await asyncio.sleep(0)
    old_tasks = list(game.visuals.tasks.values())
    await game.replace_cast(SimpleNamespace(cfg=replacement))
    assert all(t.cancelled() for t in old_tasks)
    gate.set()
    await asyncio.gather(*game.visuals.tasks.values())
    assert all(cfg is replacement for cfg in configs[-3:]) and game.visuals.cfg is replacement
    await game.close()


async def test_art_waits_for_the_player_turn_and_obeys_a_separate_job_allowance(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVES", tmp_path)
    calls = []

    async def draw(*args, **kwargs):
        calls.append(args)
        return "[]", "yy"

    monkeypatch.setattr(artist, "draw", draw)
    m, st = scene()
    visuals = Visuals(m, st, config.Config(search="none", visual_limit=1), silent)
    visuals.pause()
    visuals.schedule()
    await asyncio.sleep(0)
    assert not calls
    visuals.resume()
    await asyncio.gather(*visuals.tasks.values())
    assert len(calls) == 1
    assert sum(v["status"] == "ready" for v in st.visuals.values()) == 1
    assert all(v["art"] for v in st.visuals.values())
    await visuals.close()


async def test_a_failed_picture_retries_but_does_not_loop_forever(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVES", tmp_path)
    calls = 0

    async def broken(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise ConnectionError("offline")

    monkeypatch.setattr(artist, "draw", broken)
    m, st = scene()
    visuals = Visuals(m, st, config.Config(search="none"), silent)
    for _ in range(4):
        visuals.schedule()
        await asyncio.gather(*visuals.tasks.values())
    assert calls == 6 and all(v["attempts"] == "2" for v in st.visuals.values())
    assert all(v["status"] == "failed" for v in st.visuals.values())
    await visuals.close()


async def test_cancelling_photo_search_keeps_the_drawing_and_resumes_only_the_photo(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SAVES", tmp_path)
    drawing, started = [], asyncio.Event()

    async def draw(*args, **kwargs):
        drawing.append(1)
        return "[]", "yy"

    async def slow(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(artist, "draw", draw)
    monkeypatch.setattr(imagery, "fetch_folder", slow)
    m, st = scene()
    cfg = config.Config(visual_limit=1)
    visuals = Visuals(m, st, cfg, silent)
    visuals.schedule()
    await asyncio.wait_for(started.wait(), 1)
    await visuals.close()
    assert len(drawing) == 1
    count = []

    async def found(*args, **kwargs):
        count.append(1)

    monkeypatch.setattr(imagery, "fetch_folder", found)
    again = Visuals(m, GameState.model_validate_json(st.model_dump_json()), cfg, silent)
    again.schedule()
    await asyncio.gather(*again.tasks.values())
    assert len(drawing) == len(count) == 1
    await again.close()


@pytest.mark.usefixtures("canned")
async def test_a_prop_card_updates_in_place_when_its_illustration_arrives(monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(artist, "draw", slow)
    m, st = scene()
    st.location, st.slot = "cafe", "prop-card"
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, st, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        item = m.items[0]
        await screen.handle({"type": "item", "id": item.id, "name": item.name, "text": item.description, "art": item.art})
        await pilot.pause()
        card = screen.query_one(ItemCard)
        art = " __\n|__|"
        await screen.handle({"type": "art", "key": item.art, "art": art, "mask": artkit.auto_mask(art)})
        await pilot.pause()
        assert len(screen.query(ItemCard)) == 1 and card.module.art[item.art] == art
        assert list(card.content.renderables)[1].plain == art
