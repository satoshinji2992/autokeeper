"""Prepared identities go straight to the keeper; custom connections are prepared once."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper import config
from autokeeper.agents.director import Improv, NewItem
from autokeeper.engine.visuals import Visuals
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_engine import NEWSSTAND, improvised


@pytest.mark.parametrize("change", ["none", "stats", "equipment"])
async def test_authored_identity_needs_no_director_before_the_opening(fake, change):
    module = Module.load(EXAMPLE)
    game, cast, _ = improvised(module, fake, {}, [NEWSSTAND])
    inv = game.state.investigator
    if change == "stats":
        inv.luck -= 1
    elif change == "equipment":
        inv.inventory.append("备用手电")
    try:
        assert await game.prepare_opening() is None
        await game.start()
        assert cast.asked == [] and not game.state.improv
        assert len(cast.reports) == 1 and module.opening.strip() in cast.reports[0]
        assert inv.name in cast.reports[0]
    finally:
        await game.close()


@pytest.mark.parametrize("field", ["id", "name", "occupation", "backstory", "known_locations"])
async def test_custom_identity_still_prepares_connections_before_keeper(fake, field):
    module = Module.load(EXAMPLE)
    game, cast, _ = improvised(module, fake, {}, [NEWSSTAND])
    inv = game.state.investigator
    if field == "backstory":
        inv.backstory = {"重要之人": "街角的报童"}
    elif field == "known_locations":
        inv.known_locations = ["tower"] if inv.known_locations != ["tower"] else ["square"]
    else:
        setattr(inv, field, getattr(inv, field) + "（自定）")
    try:
        await game.prepare_opening()
        await game.start()
        assert len(cast.asked) == 1 and cast.asked[0][0] == "开局改编"
        assert game.state.improv and "报童" in cast.reports[-1]
        assert getattr(inv, field) != getattr(module.pregens[0], field)
    finally:
        await game.close()


async def test_opening_connections_and_story_do_not_wait_for_an_unfinished_prop_picture(fake, monkeypatch):
    module = Module.load(EXAMPLE)
    letter = Improv(kind="开局改编", reason="熟人留下了介绍信", items=[
        NewItem(id="welcome_note", name="介绍信", description="折过两次的信纸", where=module.start, hidden=False),
    ])
    game, cast, events = improvised(module, fake, {}, [letter])
    cfg = config.Config(search="none")
    cast.cfg = game.visuals.cfg = cfg
    game.visuals.fill_gaps = False
    game.state.investigator.backstory["重要之人"] = "镇上的旧同学"
    drawing, release = asyncio.Event(), asyncio.Event()

    async def picture(visuals, key, subject, kind):
        drawing.set()
        await release.wait()
        record = visuals.state.visuals[key]
        record.update(art="NOTE", mask="....", status="ready", photo_status="done")
        await visuals._publish(key)

    monkeypatch.setattr(Visuals, "_make_inner", picture)
    preparing = asyncio.create_task(game.prepare_opening())
    try:
        done, _ = await asyncio.wait({preparing}, timeout=0.5)
        assert preparing in done  # the story package is ready while its illustration is not
        assert await preparing is not None
        await asyncio.wait_for(drawing.wait(), 0.5)
        assert game.state.improv and next(item for item in module.items if item.id == "welcome_note").name == "介绍信"
        await asyncio.wait_for(game.start(), 1)
        assert game.state.prologue and not release.is_set()
        release.set()
        await asyncio.gather(*game.visuals.tasks.values())
        assert any(e["type"] == "art" and e["status"] == "ready" for e in events)
        assert len(cast.asked) == 1
    finally:
        release.set()
        preparing.cancel()
        await asyncio.gather(preparing, return_exceptions=True)
        await game.close()
