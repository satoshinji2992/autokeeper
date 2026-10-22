"""A model that turns a request down, and a switch of models mid-game (2026-10-22: switching from
Codex to Claude while the director's call kept failing left the turn at 「KP 正在构思…」).

Codex's strict mode refuses oneOf (the director's backstage ops are a tagged union): the request
came back 400 and the scene preparation asked again for five minutes. The switch ran in the turn's
own worker group, exclusively, so it cancelled the turn it was waiting for."""

from __future__ import annotations

import json

from autokeeper.agents.director import Improv
from autokeeper.engine.game import Game
from autokeeper.harness import models as hm
from autokeeper.harness.models import CodexError, refused
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.game import GameScreen

from .test_engine import Action, Ruling, make
from .test_engine import FailingDirector as _Failing
from .test_tui import canned  # noqa: F401  (a fixture)

REFUSED = ('{"type":"error","error":{"message":"Invalid schema for response_format \'codex_output_schema\': '
           '\'schema.properties.backstage.items.oneOf\' is not permitted when strict is true.",'
           '"type":"invalid_request_error"},"status":400}')


def test_the_directors_schema_goes_to_codex_without_oneof():
    strict = json.dumps(hm.strict_schema(Improv.model_json_schema()))
    assert '"oneOf"' not in strict and '"discriminator"' not in strict and '"anyOf"' in strict


def test_a_request_the_model_turned_down_is_refused_and_a_rate_limit_is_not():
    assert refused(CodexError(REFUSED))
    assert not refused(CodexError('{"status":429}')) and not refused(ConnectionError("down")) and not refused(None)


class RefusingDirector(_Failing):
    async def director(self, state, kind, **kw):
        self.asked.append((kind, kw.get("at", ""), kw.get("problems")))
        raise CodexError(REFUSED)


async def test_a_director_the_model_refuses_is_asked_once_and_the_action_settles(fake, monkeypatch):
    import autokeeper.engine.game as engine

    module = Module.load(EXAMPLE)
    monkeypatch.setattr(engine, "PREPARE_LIMIT", 5.0)
    monkeypatch.setattr(engine, "PREPARE_PAUSE", 1.0)
    ask = Action(kind="investigate", summary="周衡向路人打听传闻")
    script = {"x": (ask, Ruling(needs_roll=False, minutes=10, scope="可即兴"))}
    game, _, _ = make(module, fake, script)
    game.cast = cast = RefusingDirector(script, [])
    game.state.pending_turn = {"input": "x"}
    assert await game.rule(ask) is not None and len(cast.asked) == 1  # no five minutes of asking again


async def test_a_switch_of_models_asked_for_mid_turn_waits_for_the_turn(canned, monkeypatch):  # noqa: F811
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="switch", human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(GameScreen(m, state, resume=True))
        await pilot.pause(0.5)
        screen = app.screen
        applied: list[dict] = []
        monkeypatch.setattr(screen, "_apply_engine", lambda choice: applied.append(choice))
        screen._busy(True)  # the keeper is still telling
        choice = {"preset": "claude", "mode": "标准", "model": "", "fallback": ""}
        screen._switch_engine(choice)
        assert applied == [] and screen._engine_after == choice  # the turn is not cut short
        screen._busy(False)
        assert applied == [choice] and screen._engine_after is None  # made once the turn is told
