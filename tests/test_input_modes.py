"""Input channels, bare browser Alt and queued KP questions, without model calls."""

from __future__ import annotations

from autokeeper import paths
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast


async def test_kp_questions_wait_separately_from_the_network_action_pool(fake, monkeypatch):
    m = Module.load(paths.EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    cast = FakeCast({})
    hub = Hub(paths.EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    monkeypatch.setattr(hub, "_seat_of", lambda cid: state.investigator.id)
    hub.open = True
    before = state.turn, state.minutes
    await hub._on("local-player", {"t": "act", "text": "/kp 我刚才说的是书房。"})
    assert not hub.pool and not hub.early and hub.instant.qsize() == 1
    seat, text = hub.instant.get_nowait()
    await hub._afterwards(seat, text)
    assert (state.turn, state.minutes) == before and not cast.heard and not cast.ruled
    assert "我刚才说的是书房" in cast.reports[-1]
    await hub.game.close()
