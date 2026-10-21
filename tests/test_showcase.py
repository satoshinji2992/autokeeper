"""大场景: big scenes load (or are absent), each trigger fires once, pictures come in the
promised order and through the ledger, the full-screen view shows and goes, and nothing about
a big scene reaches a LAN player before its moment. Codex is a canned stand-in throughout."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image

from autokeeper import config, credits, imagegen, imagery, paths, scenery
from autokeeper.agents.cast import Action, Ruling
from autokeeper.agents.director import Improv, NewShowcase
from autokeeper.engine import showcase
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module import graph
from autokeeper.module.schema import Creature, Module, Showcase
from autokeeper.net import media
from autokeeper.net.hub import Hub, table
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, Improvising
from .test_imagegen import RED, FakeCodex, colour_of, near, picture
from .test_net import TableCast
from .test_tui import canned  # noqa: F401


def moments(tmp_path: Path) -> Module:
    """The example with a big scene for every kind of trigger, each with a picture of its own."""
    m = Module.load(EXAMPLE)
    m.creatures.append(Creature(id="coil", name="齿轮背后的藤", description="缠在齿轮背后、还在动的东西"))
    wanted = [("opening", "开场"), ("visit:tower", "重要场景"), ("event:e_dusk", "突发"), ("creature:coil", "怪物"),
              ("flag:bell_stopped", "危机"), ("ending:stopped", "结局")]
    for i, (when, kind) in enumerate(wanted):
        key = f"sc{i}"
        m.pictures[key] = str(picture(tmp_path / "art" / f"{key}.jpg"))
        m.showcases.append(Showcase(id=key, kind=kind, when=when, title=f"标题{i}", prompt=f"画面{i}", art=key))
    return m


def collector():
    events = []

    async def emit(ev):
        events.append(ev)

    return events, emit


def shown(events) -> list[str]:
    return [e["id"] for e in events if e["type"] == "showcase"]


# -- the format -----------------------------------------------------------------------------
def test_modules_and_saves_load_with_and_without_big_scenes():
    old = Module.load(EXAMPLE)
    assert old.showcases == [] and not [i for i in graph.audit(old) if i.level == "error"]
    for mid in ("zhuishuren", "dongxuezhixia", "fallout3-youxierenyouyanbujin"):  # written by hand, 5–15 each
        m = Module.load(paths.MODULES / mid)
        assert 5 <= len(m.showcases) <= 15 and not [i for i in graph.audit(m) if i.level == "error"]
        assert any(sc.when == "opening" for sc in m.showcases)
        assert {f"ending:{e.id}" for e in m.endings} <= {sc.when for sc in m.showcases}  # every ending has one
    saved = Game.new(old, [old.pregens[0].model_copy(deep=True)]).model_dump(mode="json")
    del saved["shown"]
    assert GameState.model_validate(saved).shown == []  # a save from before big scenes
    m = Module.load(EXAMPLE)
    m.showcases = [Showcase(id="a", kind="怪物", when="creature:nobody", title="t", prompt="p"),
                   Showcase(id="a", kind="开场", when="opening", title="t", prompt="p")]
    errors = [i.text for i in graph.audit(m) if i.level == "error"]
    assert any("creature:nobody" in e for e in errors) and any("重复" in e for e in errors)


# -- the moments ------------------------------------------------------------------------------
async def test_each_kind_of_moment_shows_its_big_scene_once(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    m = moments(tmp_path)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="moments")
    events, emit = collector()
    cast = FakeCast({})
    game = Game(m, state, cast, Rules(dice=fake), emit)
    game.showcases.cfg = config.Config()
    await game.start()
    assert shown(events) == ["sc0"]  # the opening
    ev = next(e for e in events if e["type"] == "showcase")
    assert ev == {"type": "showcase", "id": "sc0", "kind": "开场", "title": "标题0", "picture": m.pictures["sc0"]}
    walk = Action(kind="move", summary="去钟楼", destination="tower")
    await game.resolve(walk, Ruling(needs_roll=False, minutes=0))
    assert shown(events)[-1] == "sc1"  # first visit
    await game.resolve(Action(kind="move", summary="回广场", destination="square"), Ruling(needs_roll=False, minutes=0))
    await game.resolve(walk, Ruling(needs_roll=False, minutes=0))
    assert shown(events).count("sc1") == 1  # not the second time
    state.minutes += 4 * 60
    await game._time()
    assert shown(events)[-1] == "sc2"  # e_dusk fired
    cast.keeper = lambda *a, **k: asyncio.sleep(0, "齿轮背后的藤动了一下。")  # the narration names the creature
    await game._narrate("报告")
    await game._narrate("报告")
    assert shown(events)[-1] == "sc3" and shown(events).count("sc3") == 1
    turn = Turn()
    fake.queue_totals(3)  # the ending's 理智 +1D6
    await game._flags(["bell_stopped"], turn)
    await game._check_ending(turn)
    assert shown(events)[-2:] == ["sc4", "sc5"]  # the flag, and the ending it brings
    assert state.shown == ["sc0", "sc1", "sc2", "sc3", "sc4", "sc5"]
    for when in ("opening", "visit:tower", "event:e_dusk", "flag:bell_stopped", "ending:stopped"):
        await game.showcases.fire(when)
    assert len(shown(events)) == 6
    await game.close()


async def test_a_big_scene_without_its_picture_is_skipped_never_waited_for(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    m.showcases = [Showcase(id="tower", kind="重要场景", when="visit:tower", title="钟楼", prompt="窄楼梯")]
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="skip")
    events, emit = collector()
    game = Game(m, state, FakeCast({}), Rules(dice=fake), emit)
    game.showcases.cfg = config.Config(search="none")  # no Codex (the conftest), no search: nothing to show
    await game.resolve(Action(kind="move", summary="去钟楼", destination="tower"), Ruling(needs_roll=False, minutes=0))
    assert not shown(events) and state.shown == ["tower"]  # its moment has passed
    game.showcases.cfg = config.Config(showcase=False)
    m.showcases.append(Showcase(id="late", kind="突发", when="event:e_dusk", title="黄昏", prompt="影子", art="square"))
    m.pictures["square"] = str(picture(tmp_path / "square.jpg"))
    state.minutes += 4 * 60
    await game._time()
    assert not shown(events)  # switched off in the settings
    await game.close()


async def test_the_opening_waits_for_its_picture_until_someone_acts(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    gate = asyncio.Event()

    async def slow(cfg, m, sc, target, **kwargs):
        await gate.wait()
        picture(target)
        return "AI 照描述画"

    monkeypatch.setattr(showcase, "prepare", slow)
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    m = Module.load(EXAMPLE)
    m.showcases = [Showcase(id="open", kind="开场", when="opening", title="钟楼的影子", prompt="午后的广场")]
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="opening-wait")
    events, emit = collector()
    game = Game(m, state, FakeCast({}), Rules(dice=fake), emit)
    game.showcases.cfg = config.Config()
    game.showcases.prepare()
    await game.start()
    assert not shown(events) and state.shown == []  # not ready: the narration did not wait for it
    gate.set()
    await asyncio.wait_for(game.showcases.task, 2)
    assert shown(events) == ["open"] and state.shown == ["open"]  # ready before anyone acted: shown then
    await game.close()

    gate.clear()
    m.showcases[0] = Showcase(id="open2", kind="开场", when="opening", title="又一次", prompt="雾")
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="opening-late")
    events, emit = collector()
    game = Game(m, state, FakeCast({"看看": (Action(kind="investigate", summary="看看"), Ruling(needs_roll=False))}), Rules(dice=fake), emit)
    game.showcases.cfg = config.Config()
    game.showcases.prepare()
    await game.start()
    await game.parse("看看")  # someone has acted: the opening's moment is over
    gate.set()
    await asyncio.wait_for(game.showcases.task, 2)
    assert not shown(events) and state.shown == ["open2"]
    await game.close()


# -- where the pictures come from ---------------------------------------------------------------
async def test_pictures_come_from_the_module_then_codex_from_a_reference_then_codex_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    calls = []

    async def make(text, target, *, kind, module, original=None, title="", where="game", timeout=0, on_reply=None):
        calls.append((Path(original) if original else None, text))
        picture(Path(target), RED)
        return Path(target)

    async def found(cfg, client, subject, kind, **kwargs):
        return Image.new("RGB", (640, 360), (20, 120, 40)), imagery.Candidate("Old lane", "p", "t", "CC0", "A", 640, 360)

    monkeypatch.setattr(imagegen, "make", make)
    monkeypatch.setattr(imagery, "find_picture", found)
    m = Module.load(EXAMPLE)
    m.pictures["square"] = str(picture(tmp_path / "art" / "square.jpg"))
    m.pictures["bell"] = str(picture(tmp_path / "art" / "bell.jpg"))
    own = Showcase(id="own", kind="怪物", when="flag:bell_stopped", title="钟", prompt="大钟", art="bell")
    placed = Showcase(id="placed", kind="开场", when="opening", title="广场", prompt="午后的广场")  # its place: square
    searched = Showcase(id="searched", kind="危机", when="visit:tower", title="楼梯", prompt="窄楼梯")  # the tower has no picture
    alone = Showcase(id="alone", kind="结局", when="ending:toll", title="钟声", prompt="午夜的钟")
    cfg = config.Config()
    target = lambda sc: showcase.cache(m.id) / showcase.name(sc)  # noqa: E731
    assert await showcase.prepare(cfg, m, own, target(own)) == "模组原图" and not calls
    assert await showcase.prepare(cfg, m, placed, target(placed)) == "AI 照原图画"
    assert calls[-1][0] == Path(m.pictures["square"]) and "made from the attached picture" in calls[-1][1]
    assert await showcase.prepare(cfg, m, searched, target(searched)) == "AI 照原图画"
    assert calls[-1][0] == showcase.photo(target(searched))  # found through imagery's search, then redrawn
    credit = credits.get(showcase.photo(target(searched)))
    assert credit["license"] == "CC0"
    cfg_offline = config.Config(search="none")
    assert await showcase.prepare(cfg_offline, m, alone, target(alone)) == "AI 照描述画" and calls[-1][0] is None
    assert len(calls) == 3 and all(showcase.ready(cfg, m, sc, target(sc)) for sc in (own, placed, searched, alone))
    assert target(placed).is_relative_to(paths.cache() / "showcases")  # outside the module, for every game of it


async def test_without_codex_nothing_is_generated_found_or_own_pictures_are_shown_as_they_are(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")

    async def make(*args, **kwargs):
        raise AssertionError("no generation without Codex")

    async def found(cfg, client, subject, kind, **kwargs):
        return Image.new("RGB", (640, 360), (20, 120, 40)), imagery.Candidate("Old lane", "p", "t", "CC0", "A", 640, 360)

    monkeypatch.setattr(imagegen, "make", make)
    monkeypatch.setattr(imagery, "find_picture", found)
    m = Module.load(EXAMPLE)
    m.pictures["square"] = str(picture(tmp_path / "art" / "square.jpg"))
    placed = Showcase(id="placed", kind="开场", when="opening", title="广场", prompt="午后的广场")
    searched = Showcase(id="searched", kind="危机", when="visit:tower", title="楼梯", prompt="窄楼梯")
    target = lambda sc: showcase.cache(m.id) / showcase.name(sc)  # noqa: E731
    cfg = config.Config()
    assert showcase.ready(cfg, m, placed, target(placed)) == m.pictures["square"]  # the module's picture of the place
    assert await showcase.prepare(cfg, m, searched, target(searched)) == "找到的照片"
    assert showcase.ready(cfg, m, searched, target(searched)) == str(showcase.photo(target(searched)))
    offline = config.Config(search="none")
    lone = Showcase(id="lone", kind="结局", when="ending:toll", title="钟声", prompt="午夜的钟")
    assert await showcase.prepare(offline, m, lone, target(lone)) == "" and not showcase.ready(offline, m, lone, target(lone))


async def test_big_scenes_are_counted_in_the_module_s_hundred_and_stop_there(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    fake = FakeCodex(tmp_path / "codex-home", asyncio.create_subprocess_exec)
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    m = Module.load(EXAMPLE)
    m.showcases = [Showcase(id=f"s{i}", kind="突发", when="event:e_dusk", title="t", prompt=f"画面{i}") for i in range(3)]
    m.showcases.insert(1, Showcase(id="open", kind="开场", when="opening", title="开场", prompt="广场"))
    ledger = imagegen.ledger(m.id)
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"module": m.id, "used": imagegen.LIMIT - 2, "pictures": []}), encoding="utf-8")
    log = []
    done = await showcase.prepare_module(config.Config(search="none"), m, log=log.append)
    assert done == ["open", "s0"]  # the opening first, one at a time, until the module's pictures are used up
    assert imagegen.used(m.id) == imagegen.LIMIT and len(fake.prompts) == 2
    assert any("到时跳过" in line for line in log)


# -- the director --------------------------------------------------------------------------------
async def test_the_director_adds_a_few_big_scenes_at_most_kept_with_the_save(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    made = []

    async def make(text, target, *, kind, module, original=None, title="", where="game", timeout=0, on_reply=None):
        made.append(Path(target))
        picture(Path(target), RED)
        return Path(target)

    monkeypatch.setattr(imagegen, "make", make)
    m = Module.load(EXAMPLE)
    plans = [Improv(kind="推进", reason=f"r{i}", showcases=[NewShowcase(id=f"d{i}", kind="危机", when="event:e_finale",
                                                                          title="午夜", prompt="钟楼在月下")]) for i in range(4)]
    asks = {f"x{i}": (Action(kind="other", summary=f"行动{i}"), Ruling(needs_roll=False, scope="可即兴")) for i in range(4)}
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="director-scenes")
    game = Game(m, state, Improvising(asks, plans), Rules(dice=fake))
    game.showcases.cfg = config.Config(search="none")
    for i in range(4):
        state.turn = 3 * i  # rounds apart: one place opens a new line once in three (Game._may_expand)
        await game.rule(Action(kind="other", summary=f"行动{i}"))
    assert [sc.id for sc in m.showcases] == ["d0", "d1", "d2"] and all(sc.improvised for sc in m.showcases)
    await asyncio.wait_for(game.showcases.task, 2)
    assert len(made) == 3 and all(p.is_relative_to(game.visuals.folder) for p in made)  # per save, not per module
    assert table(m, state)["showcases"] == []
    await game.close()


# -- on screen -------------------------------------------------------------------------------------
async def test_the_big_scene_fills_the_screen_and_goes_on_a_key_a_click_or_by_itself(canned, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(scenery, "fill", lambda m: [])  # these are about the hand-drawn scenes and faces
    monkeypatch.setattr(scenery, "fill_people", lambda m: [])
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals import showcase as screen_module
    from autokeeper.tui.modals.dialogs import RollModal
    from autokeeper.tui.screens.game import GameScreen

    from .test_tui import until

    drawn = []

    def render(path, width, height, style="photo", when="", fill=False, graded="", face=False, people=()):
        drawn.append((path, width, height, style, fill))
        return "大图\n" * 3

    monkeypatch.setattr(imagery, "available", lambda: True)
    monkeypatch.setattr(imagery, "render", render)
    monkeypatch.setattr(screen_module, "SHOWN", 30.0)  # long enough for a slow runner; shortened below
    m = Module.load(EXAMPLE)
    big = str(picture(tmp_path / "big.jpg"))
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="fullscreen")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        for _ in range(40):
            await pilot.pause(0.1)
            if screen.game is not None and not screen.busy:
                break
        ev = {"type": "showcase", "id": "a", "kind": "怪物", "title": "细碎的脚步声", "picture": big}
        await screen.handle(ev)
        assert await until(pilot, lambda: isinstance(app.screen, screen_module.ShowcaseScreen) and bool(drawn))  # up, and drawn
        assert drawn[-1] == (big, 120, 39, app.art_style, True)  # the whole screen but the title strip, like the scene
        assert "细碎的脚步声" in str(app.screen.query_one("#showcase-title").render())
        await pilot.press("x")
        assert await until(pilot, lambda: app.screen is screen)  # any key
        await screen.handle(ev)
        assert await until(pilot, lambda: isinstance(app.screen, screen_module.ShowcaseScreen) and bool(app.screen.query("#showcase-picture")))
        await pilot.click("#showcase-picture")
        assert await until(pilot, lambda: app.screen is screen)  # a click
        await screen.handle(ev)
        assert await until(pilot, lambda: isinstance(app.screen, screen_module.ShowcaseScreen) and bool(app.screen.query("#showcase-picture")))
        shown = app.screen
        shown.leave()
        shown.leave()  # a key just as the timer runs: gone once, the game screen stays
        assert await until(pilot, lambda: app.screen is screen)
        await pilot.pause(0.2)
        assert app.screen is screen
        monkeypatch.setattr(screen_module, "SHOWN", 0.4)
        await screen.handle(ev)
        assert await until(pilot, lambda: isinstance(app.screen, screen_module.ShowcaseScreen) and bool(app.screen.query("#showcase-picture")))
        assert await until(pilot, lambda: app.screen is screen)  # by itself
        await screen.handle(ev)
        await pilot.pause(0.1)
        await screen.make_way()  # a roll prompt comes up: the big scene gives way
        await pilot.pause(0.1)
        assert app.screen is screen
        app.push_screen(RollModal({"who": "周衡", "skill": "侦查", "difficulty": "常规", "target": 50, "hard": 25,
                                   "extreme": 10, "need": 50, "bonus": 0, "action": "看看", "why": ""}))
        await pilot.pause(0.1)
        await screen.handle(ev)
        await pilot.pause(0.1)
        assert isinstance(app.screen, RollModal)  # not over a roll prompt
        app.screen.dismiss("roll")
        await pilot.pause(0.1)
        canned.showcase = False  # what the settings saved
        await screen.handle(ev)
        await pilot.pause(0.1)
        assert app.screen is screen  # switched off in the settings


# -- LAN -------------------------------------------------------------------------------------------
async def test_lan_players_get_a_big_scene_only_at_its_moment(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    hub.game.showcases.cfg = config.Config()
    hub.m.pictures["boss"] = str(picture(tmp_path / "art" / "boss.jpg", RED))
    hub.m.showcases.append(Showcase(id="boss", kind="最终boss", when="visit:tower", title="塔顶的东西",
                                    prompt="齿轮背后缠着会动的藤，月光照着它", art="boss"))
    sent = []

    async def collect(msg):
        sent.append(msg)

    hub._broadcast = collect
    before = json.dumps(hub._welcome("early"), ensure_ascii=False)
    assert "塔顶的东西" not in before and "会动的藤" not in before and "boss" not in before
    assert table(hub.m, hub.state)["showcases"] == [] and "shown" not in hub._view()
    await hub.game.showcases.fire("visit:tower")
    msg = next(x for x in sent if x["t"] == "event" and x["ev"]["type"] == "showcase")
    assert msg["ev"]["title"] == "塔顶的东西" and "prompt" not in msg["ev"] and "picture" not in msg["ev"]
    assert near(colour_of(media.unpack(msg["ev"]["picture_data"])), RED)
    assert "会动的藤" not in json.dumps(sent, ensure_ascii=False)  # the words for the picture stay home
    hub.state.ending = "stopped"  # the whole book opens at the end, but for big scenes
    assert hub._book()["showcases"] == []
    await hub.game.close()


async def test_a_player_at_a_shared_table_sees_the_big_scene(skills_dir, fake, tmp_path, monkeypatch):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.showcase import ShowcaseScreen
    from autokeeper.tui.screens.net import NetGameScreen

    from .test_net import closed, opened

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setattr(imagery, "available", lambda: True)
    monkeypatch.setattr(imagery, "render", lambda *a, **k: "大图")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    hub.game.showcases.cfg = config.Config()
    hub.m.pictures["boss"] = str(picture(tmp_path / "art" / "boss.jpg", RED))
    hub.m.showcases.append(Showcase(id="boss", kind="最终boss", when="visit:tower", title="塔顶的东西", prompt="藤", art="boss"))
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小明"))
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("enter")  # the free seat
        await pilot.pause(0.1)
        await pilot.press("y")  # ready: the game begins
        for _ in range(40):
            await pilot.pause(0.1)
            if isinstance(app.screen, NetGameScreen) and app.screen.my_turn:
                break
        assert isinstance(app.screen, NetGameScreen)
        await hub.game.showcases.fire("visit:tower")
        for _ in range(40):
            await pilot.pause(0.1)
            if isinstance(app.screen, ShowcaseScreen):
                break
        assert isinstance(app.screen, ShowcaseScreen) and near(colour_of(app.screen.picture), RED)
        assert Path(app.screen.picture).is_relative_to(paths.table_art())  # the player's own copy
    await closed(task)
    await hub.game.close()

