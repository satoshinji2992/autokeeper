"""Prepared identities go straight to the keeper; custom connections are prepared once."""

from __future__ import annotations

import pytest

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
