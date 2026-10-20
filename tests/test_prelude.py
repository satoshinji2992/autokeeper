"""开局演出 (module/prelude.py): written at compile time from what the players are told, played
before the game screen while the director adapts the module (tui/screens/prelude.py)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from autokeeper import config, paths
from autokeeper.config import Role
from autokeeper.engine.game import Game
from autokeeper.harness import models as hm
from autokeeper.module import adapt, prelude
from autokeeper.module.schema import Module, PreludeFrame
from autokeeper.paths import EXAMPLE
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens import prelude as screen
from autokeeper.tui.screens.game import GameScreen
from autokeeper.tui.screens.prelude import PreludeScreen

from .test_tui import Canned


def test_a_module_without_one_gets_a_plain_opening_from_what_the_players_are_told():
    m = Module.load(paths.MODULES / "zhuishuren")
    own = [PreludeFrame(text="一九二二年，密歇根。"), PreludeFrame(text="一封信找上了你。")]
    m.prelude = own
    assert prelude.frames(m) == own  # the module's own, written at compile time
    m.prelude = []
    shots = prelude.frames(m)
    assert 2 <= len(shots) <= prelude.MAX_FRAMES and shots[0].text.startswith("《追书人》")
    assert all(not s.art or s.art in prelude.public_art(m) for s in shots)
    assert not prelude.problems(m, shots)
    start = m.location(m.start).art
    assert start in prelude.public_art(m)  # the place they arrive at may be shown


def test_a_shot_that_shows_or_says_too_much_is_kept_out():
    m = Module.load(paths.MODULES / "zhuishuren")
    hidden = next(loc.art for loc in m.locations if loc.art and loc.art not in prelude.public_art(m))
    secret = m.truth.strip().splitlines()[0][:40]
    assert prelude.shot(m, PreludeFrame(art=hidden, text="夜色里的地方"))  # not public yet
    assert prelude.shot(m, PreludeFrame(text=secret))  # the truth in a caption
    assert prelude.shot(m, PreludeFrame(text="字" * (prelude.TEXT_LIMIT + 1)))
    out = SimpleNamespace(prelude=[PreludeFrame(art=hidden, text="一九二二年，禁酒令下的密歇根。"),
                                   PreludeFrame(text=secret), PreludeFrame(text="一封信找上了你。")])
    [(kind, group, items)] = adapt.edits(m, "prelude", out)
    assert (kind, group) == ("append", "prelude") and len(items) == 2
    assert all(item.get("art", "") != hidden for item in items)  # the picture is dropped, the words kept


async def test_a_new_game_plays_its_prelude_while_the_opening_is_prepared(monkeypatch, tmp_path, skills_dir):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    monkeypatch.setattr(screen, "HOLD", 0.05)
    monkeypatch.setattr(screen, "TYPE", 0.0)
    order: list[str] = []
    prepare, start = Game.prepare_opening, Game.start

    async def preparing(self):
        order.append("prepare")
        await asyncio.sleep(0.3)
        out = await prepare(self)
        order.append("prepared")
        return out

    async def starting(self, on_delta=None):
        order.append("start")
        return await start(self, on_delta)

    monkeypatch.setattr(Game, "prepare_opening", preparing)
    monkeypatch.setattr(Game, "start", starting)
    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="prelude")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(GameScreen(m, state, resume=False))
        await pilot.pause(0.1)
        assert isinstance(app.screen, PreludeScreen) and order == ["prepare"]
        for _ in range(60):
            await pilot.pause(0.1)
            if not isinstance(app.screen, PreludeScreen):
                break
        assert isinstance(app.screen, GameScreen)
        await app.workers.wait_for_complete()
        assert order[:3] == ["prepare", "prepared", "start"]  # the keeper starts once the opening is ready
        assert app.screen.game.prelude_played  # so no big opening picture covers the keeper's first words


async def test_waiting_for_the_opening_after_the_shots_shows_it_is_still_going():
    """The user, 2026-10-21: a still 「开场准备中…」 looked like a hang. The dots move."""
    ready = asyncio.get_running_loop().create_future()
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(PreludeScreen(Module.load(EXAMPLE), ready, shots=[]))
        seen = set()
        for _ in range(8):
            await pilot.pause(0.2)
            seen.add(str(app.screen.query_one("#prelude-status").render()))
        assert len({s.strip() for s in seen if s.startswith("开场准备中")}) >= 2
        ready.set_result(None)
        for _ in range(10):
            await pilot.pause(0.2)
            if not isinstance(app.screen, PreludeScreen):
                break
        assert not isinstance(app.screen, PreludeScreen)
