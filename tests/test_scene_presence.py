"""The rooms of one building: someone in the study is near the party in the hall, a public square
is no building (world/context.rooms, nearby), and the parser can send the party to that room."""

from __future__ import annotations

import pytest

from autokeeper.module.schema import Location, Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import context
from autokeeper.world import ops as O

from .test_engine import make


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.locations += [Location(id="house", name="守钟人家", read_aloud="一栋小木屋。", exits=["square"]),
                    Location(id="study", name="书房", read_aloud="一间小书房。", exits=["house"], within="house")]  # fmt: skip
    m.location("square").exits.append("house")
    return m


def test_the_rooms_of_a_house_are_near_a_public_square_is_no_building(module, fake):
    game, _, _ = make(module, fake, {}, location="house")
    w = game.world
    assert context.rooms(w.m, "house") == {"study"} and context.rooms(w.m, "study") == {"house"}
    module.location("square").public = True
    assert context.rooms(w.m, "tower") == set()  # the tower stands in the square, which anyone walks across
    w.apply(O.Move(entity="warden", to="study", actor="director"))
    assert context.nearby(w, "house") == {"warden": "study"}
    w.apply(O.Condition(entity="warden", status="失踪", actor="director"))
    assert context.nearby(w, "house") == {}


async def test_the_parser_can_send_the_party_to_the_room_where_someone_is(module, fake):
    from autokeeper import config
    from autokeeper.agents.cast import Cast

    game, _, _ = make(module, fake, {}, location="house")
    game.world.apply(O.Move(entity="warden", to="study", actor="director"))
    cast = Cast(config.load(), game.m)
    cast.world = game.world
    assert "老守钟人(warden) 在 书房(study)" not in cast.bearings(game.state)  # not met yet: no name to give
    assert game.world.apply(O.Occur(actor="keeper", text="调查员已通过介绍认识守钟人。", witnesses=[game.state.investigator.id],
                                    introduced={"warden": "老守钟人"}, visibility="private")).ok
    assert "同一处的别的房间里" not in cast.bearings(game.state)  # a room they have not heard of stays unnamed
    game.state.visited.append("study")
    assert "同一处的别的房间里：老守钟人(warden) 在 书房(study)" in cast.bearings(game.state)

