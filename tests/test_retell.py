"""Telling the latest narration again, the debrief and the epilogue (no model calls)."""

from __future__ import annotations

import asyncio
import dataclasses
import json

import aiohttp
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper import config, paths
from autokeeper.agents.cast import Action, Cast, Ruling
from autokeeper.config import Role
from autokeeper.engine import debrief
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import PLAY_WAIT, Game, Turn
from autokeeper.harness import Reply, spend
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import NEWSSTAND, FakeCast, SlowDirector, make, run_turn
from .test_net import LeverCast, closed, opened, until

RETOLD = "风把告示吹得哗哗响，纸角卷起又落下。"
AFTER = "老守钟人：钟停下以后，他把钥匙交给了镇公所，再没上过楼。\n周衡：那篇稿子他压在抽屉里，一个字也没登。"


def teller(cast):
    """A fake cast whose keeper can tell its last narration again and write an epilogue."""
    cast.retold, cast.epilogues = [], []

    async def retell(state, report, told, *, extra="", on_delta=None):
        cast.retold.append((report, told, extra))
        text = RETOLD if len(cast.retold) == 1 else f"{RETOLD}（第 {len(cast.retold)} 版）"
        if on_delta:
            await on_delta(text)
        return text

    async def epilogue(state, prompt, on_delta=None):
        cast.epilogues.append(prompt)
        if on_delta:
            await on_delta(AFTER)
        return AFTER

    cast.retell, cast.epilogue = retell, epilogue
    return cast


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


PLEAD = Action(kind="talk", summary="周衡劝守钟人说出钟里的事", npc="warden", speech="钟里到底有什么？", approach="说服")
HARD = Ruling(needs_roll=True, skill="说服", difficulty="困难", clues_on_success=["c_diary"], minutes=15)
LOOK = Action(kind="investigate", summary="周衡翻看告示栏", target="告示栏")
BOARD = Ruling(needs_roll=False, auto_clues=["c_notice"], minutes=5)


# -- telling again ------------------------------------------------------------------------
async def test_retelling_keeps_the_dice_the_clues_and_the_state(module, fake):
    ruling = HARD.model_copy(update={"note": "守钟人交出日记后，楼上传来敲门声"})
    game, _, events = make(module, fake, {"劝他": (PLEAD, ruling)}, location="tower")
    cast = game.cast = teller(FakeCast({"劝他": (PLEAD, ruling)}))
    fake.queue(8)  # 说服 40, hard: an extreme success
    emit = game.emit
    await run_turn(turn_graph.build(game, InMemorySaver()), "劝他", {"configurable": {"thread_id": "r1"}})
    game.emit = emit  # the graph had it stream; a screen takes it back after every turn
    st = game.state
    assert st.clues == ["c_diary"] and not fake.values and game.retell_blocked() == ""
    before, log, report = st.model_dump(exclude={"log"}), [(e.kind, e.text) for e in st.log], cast.reports[-1]
    events.clear()
    assert await game.retell("短一点") == RETOLD  # no die is left to roll: any roll would fail the test
    assert st.model_dump(exclude={"log"}) == before  # clues, flags, checks, NPCs, sheet and clock as they were
    assert [(e.kind, e.text) for e in st.log] == [*log[:-1], ("keeper", RETOLD)]  # the new telling in the old one's place
    sent, old, extra = cast.retold[0]
    assert sent == report and old == "叙事" and "「叙事」" in extra and "砍到上一版的一半" in extra
    assert events[0] == {"type": "keeper_start", "again": True} and events[-1] == {"type": "keeper_end", "text": RETOLD}
    with pytest.raises(ValueError, match="不认识的讲法"):  # fixed choices only, no wishes of the player's own
        await game.retell("把守钟人的秘密讲出来")
    await game.retell("更阴森")  # and again, if they like
    assert cast.retold[1][1] == RETOLD and st.log[-1].text.endswith("（第 2 版）")


async def test_only_the_latest_narration_and_never_mid_turn(module, fake):
    game, *_ = make(module, fake, {"看告示": (LOOK, BOARD)})
    cast = game.cast = teller(FakeCast({"看告示": (LOOK, BOARD)}))
    assert "暂无可重述内容" in game.retell_blocked()  # nothing told yet
    await game.start()
    assert game.retell_blocked() == ""  # the opening can be told again
    fake.queue_totals(4)
    await game.command(".r 1d6")  # a roll outside the story does not move it on
    assert game.retell_blocked() == ""
    action = await game.parse("看告示")  # a turn has begun
    assert "后续行动已开始" in game.retell_blocked()
    with pytest.raises(ValueError):
        await game.retell()
    turn = await game.resolve(action, await game.rule(action))
    gate = asyncio.Event()

    async def keeper(state, report, on_delta=None, lore=""):
        cast.reports.append(report)
        await gate.wait()
        return "告示上的字被雨水洇开了。"

    cast.keeper = keeper
    telling = asyncio.create_task(game.narrate(turn))
    while len(cast.reports) < 2 and not telling.done():
        await asyncio.sleep(0.01)
    assert not telling.done() and "叙述尚未结束" in game.retell_blocked()  # mid-narration
    gate.set()
    await telling
    assert game.retell_blocked() == "" and not cast.retold
    await game.retell("更克制")
    assert cast.retold[0][0] == cast.reports[-1] and "广场告示" in cast.retold[0][0]  # that turn's report, clue and all


async def test_an_interlude_can_be_told_again(module, fake):
    ask = Action(kind="investigate", summary="周衡向路人打听镇上关于钟楼的传闻")
    script = {"x": (ask, Ruling(needs_roll=False, minutes=10, scope="可即兴"))}
    game, *_ = make(module, fake, script)
    cast = teller(SlowDirector(script, [NEWSSTAND]))
    game.cast, game.director_wait = cast, PLAY_WAIT
    await game._improvise("展开", at=game.state.location, patience=0.0)
    await asyncio.sleep(0.3)
    assert (await game.interlude()).interlude
    await game.retell()
    report, old, _ = cast.retold[0]
    assert "插曲" in report and report == cast.reports[-1] and old == "叙事"  # the interlude, not the turn before it
    assert game.state.log[-1].text == RETOLD and "c_rumor" in game.state.clues


class Scripted(hm.Model):
    """A model that answers from a list and keeps what each call was sent."""

    replies: list = []
    seen: list[list[dict]] = []

    async def _call(self, messages, *, schema, images, on_delta):
        Scripted.seen.append([dict(m) for m in messages])
        out = Scripted.replies.pop(0)
        if isinstance(out, Exception):
            raise out
        if on_delta:
            await on_delta(out)
        return Reply(text=out, data=hm.parse_json(out) if schema else None, usage={"input_tokens": 100, "output_tokens": 10})


def telling(text: str) -> str:
    """A keeper's telling as it is written: the scene first (here, nothing changed), then the words."""
    return json.dumps({"scene": {}, "text": text}, ensure_ascii=False)


@pytest.fixture
def scripted(monkeypatch, tmp_path):
    monkeypatch.setitem(hm.MODELS, "scripted", Scripted)
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setattr(paths, "USER_DIR", tmp_path / "user")
    monkeypatch.setenv("AUTOKEEPER_TRACE", "off")
    spend.budget.cache_clear()
    Scripted.replies, Scripted.seen = [], []
    yield config.Config(roles={r: Role("scripted", "scripted", None) for r in config.DEFAULT_ROLES})
    spend.budget.cache_clear()


async def test_the_keeper_remembers_only_the_new_telling(module, fake, scripted, skills_dir):
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    game = Game(module, state, Cast(scripted, module), Rules(dice=fake), emit=emit)
    Scripted.replies += [telling("午后的广场空荡荡的。"), telling("你凑近告示栏，纸角在风里翻动。"), "告示栏上只剩半张纸。"]
    await game.narrate(Turn(action=Action(kind="wait", summary="周衡在广场上站了一会儿")))
    await game.narrate(Turn(action=Action(kind="move", summary="周衡走到告示栏前")))
    keeper = state.transcripts["keeper"]
    first, second = keeper[0]["content"], keeper[2]["content"]
    await game.retell("更克制")
    assert [m["content"] for m in keeper] == [first, "午后的广场空荡荡的。", second, "告示栏上只剩半张纸。"]
    sent = Scripted.seen[-1]  # the window: the earlier turn whole, the retold one without its old answer
    assert [m["content"] for m in sent if m["role"] == "assistant"] == ["午后的广场空荡荡的。"]
    assert sent[-1]["content"].startswith(second) and "「你凑近告示栏，纸角在风里翻动。」" in sent[-1]["content"] and "更克制" in sent[-1]["content"]
    assert state.log[-1].text == "告示栏上只剩半张纸。" and [e.kind for e in state.log] == ["keeper", "keeper"]

    Scripted.replies.append(RuntimeError("断线了"))  # a failed retelling leaves the old one standing
    with pytest.raises(RuntimeError):
        await game.retell()
    assert [m["content"] for m in keeper][-1] == "告示栏上只剩半张纸。" and len(keeper) == 4
    assert state.log[-1].text == "告示栏上只剩半张纸。" and events[-1] == {"type": "keeper_end", "text": "告示栏上只剩半张纸。"}
    assert game.retell_blocked() == ""

    state.ending = "stopped"  # the epilogue is asked of the keeper and kept out of its memory
    Scripted.replies.append(AFTER)
    assert await debrief.epilogue(game) == AFTER and state.epilogue == AFTER
    assert len(keeper) == 4 and "【后日谈｜钟停了】" in Scripted.seen[-1][-1]["content"]
    assert game.retell_blocked() == ""  # the last narration can still be told again


# -- after the end ----------------------------------------------------------------------------
def test_the_debrief_opens_the_book_only_after_the_ending(module, fake):
    game, *_ = make(module, fake, {})
    st = game.state
    with pytest.raises(ValueError, match="结束"):
        debrief.build(module, st)
    st.clues, st.visited, st.flags, st.ending = ["c_notice", "c_diary"], ["square", "tower"], ["bell_stopped"], "stopped"
    st.npc("warden").met = True
    d = debrief.build(module, st)
    assert (d.ending, d.truth) == ("钟停了", module.truth.strip())
    assert [(c.title, c.where, c.how) for c in d.found] == [("广场告示", "广场 · 告示栏", "不用检定"), ("守钟人的日记", "钟楼 · 老守钟人", "说服（困难）")]
    assert [(c.title, c.where, c.how, c.core) for c in d.missed] == [
        ("齿轮间", "钟楼 · 齿轮间", "侦查（困难）", True),
        ("旧报", "报社 · 旧报合订本", "图书馆使用（常规）", True),
    ]
    assert [r.title for r in d.roads] == ["多出的那一下"]
    assert d.roads[0].how == ["条件：以上都没有发生", "〔午夜〕（到了 D1 22:00）时还没有别的结局，就是这一个"]
    warden, preacher = d.people
    assert warden.met and warden.secrets == ["他在钟里藏了东西"] and warden.lies == ["钟只是老了"] and not preacher.met
    assert d.deductions == [("钟里藏着会动的东西，多出的钟声是它在敲", 1, 3), ("守钟人知道钟里有什么，并在替它遮掩", 2, 2)]
    assert d.turns == ["钟被停下了"] and d.unvisited == ["报社"]
    st.ending = "toll"
    assert debrief.build(module, st).roads[0].how == ["需要：钟被停下了"]


async def test_the_epilogue_is_told_once_after_the_ending(module, fake):
    game, _, events = make(module, fake, {})
    cast = teller(game.cast)
    with pytest.raises(ValueError):
        await debrief.epilogue(game)
    st = game.state
    st.ending, st.clues = "stopped", ["c_diary"]
    st.npc("warden").met = True
    assert await debrief.epilogue(game) == AFTER and st.epilogue == AFTER
    prompt = cast.epilogues[0]
    assert "老守钟人（钟楼的守钟人）" in prompt and "他在钟里藏了东西" in prompt and "周衡（记者" in prompt
    assert "街头布道者" not in prompt and "守钟人的日记" in prompt  # only the people they met
    assert await debrief.epilogue(game) == AFTER and len(cast.epilogues) == 1  # kept, not told twice
    assert [e["type"] for e in events] == ["epilogue_start", "epilogue_delta", "epilogue", "epilogue"]


# -- at the screen ----------------------------------------------------------------------------
async def test_f9_tells_it_again_and_f10_opens_the_book(canned, fake):
    from textual.widgets import Static

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.debrief import DebriefModal
    from autokeeper.tui.modals.retell import RetellModal
    from autokeeper.tui.screens.game import GameScreen

    from .test_tui import story_text
    from .test_tui import until as waits

    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="retell", human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await waits(pilot, lambda: screen.game is not None and "钟楼底下" in story_text(screen) and not screen.busy)
        await pilot.press("f6")  # the recap is not a narration: nothing to tell again yet
        assert app.screen is screen
        cast = screen.game.cast = teller(FakeCast({"看告示": (LOOK, BOARD)}))
        screen.game.rules = Rules(dice=fake)
        screen.send_line("看告示")
        assert await waits(pilot, lambda: "〔广场告示〕" in story_text(screen) and not screen.busy)
        blocks = list(screen.story.query(".keeper"))
        assert screen.told is blocks[-1] and str(blocks[-1].render()) == "叙事"
        screen.busy = True  # mid-turn: refused
        await pilot.press("f6")
        assert app.screen is screen
        screen.busy = False
        await pilot.press("f6")
        assert await waits(pilot, lambda: isinstance(app.screen, RetellModal))
        await pilot.press("down", "enter")  # 短一点
        assert await waits(pilot, lambda: RETOLD in story_text(screen) and not screen.busy)
        assert list(screen.story.query(".keeper")) == blocks and str(blocks[-1].render()) == RETOLD  # rewritten in place
        assert "砍到上一版的一半" in cast.retold[0][2]
        assert RETOLD in paths.save("retell").read_text(encoding="utf-8")  # and saved
        screen.send_line("/retell 更阴森")  # straight from the box
        assert await waits(pilot, lambda: "（第 2 版）" in story_text(screen) and not screen.busy)
        await pilot.press("f6")
        assert await waits(pilot, lambda: isinstance(app.screen, RetellModal))
        screen.game.told = dataclasses.replace(screen.game.told)  # meanwhile the keeper went on (an interlude)
        await pilot.press("enter")
        assert await waits(pilot, lambda: app.screen is screen)
        await pilot.pause(0.2)
        assert len(cast.retold) == 2  # not the narration they asked about: nothing told again

        await pilot.press("f10")  # not over yet
        assert app.screen is screen
        state.ending, state.flags = "stopped", ["bell_stopped"]
        await pilot.press("f10")
        assert await waits(pilot, lambda: isinstance(app.screen, DebriefModal))
        def shown() -> str:
            return "".join(str(w.render()) for w in app.screen.query(Static))

        assert "其他结局" in shown() and "多出的那一下" in shown() and "✗ 〔齿轮间〕钟楼 · 齿轮间 · 侦查（困难） · 关键" in shown()
        assert "按 E" in shown()
        await pilot.press("e")
        assert await waits(pilot, lambda: "再没上过楼" in shown())
        assert state.epilogue == AFTER and len(cast.epilogues) == 1
        await pilot.press("escape")
        assert await waits(pilot, lambda: app.screen is screen and not screen.busy)


@pytest.fixture
def canned(monkeypatch, tmp_path, skills_dir):
    from .test_tui import Canned

    for var in ("AUTOKEEPER_PRESET", "AUTOKEEPER_MODEL", "AUTOKEEPER_FALLBACK", "AUTOKEEPER_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    return cfg


# -- at a shared table --------------------------------------------------------------------------
async def test_a_shared_table_hears_it_again_and_gets_the_book_at_the_end(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    cast = teller(LeverCast())
    hub = Hub(EXAMPLE, seats=["玩家"], cast=cast, rules=Rules(dice=fake))
    task = await opened(hub)
    url = f"ws://127.0.0.1:{hub.port}/ws"
    async with aiohttp.ClientSession() as http:
        a, b = await http.ws_connect(url), await http.ws_connect(url)
        await a.send_json({"t": "hello", "name": "小明"})
        assert not (await until(a, lambda x: x["t"] == "welcome"))["module"]["truth"]
        await b.send_json({"t": "hello", "name": "小红"})  # watching
        await until(b, lambda x: x["t"] == "welcome")
        await a.send_json({"t": "sit", "investigator": "zhouheng"})  # the only seat: the game begins
        await a.send_json({"t": "ready"})
        await until(a, lambda x: x["t"] == "pool" and x["open"])
        await b.send_json({"t": "retell", "nudge": "更阴森"})
        assert "观战" in (await until(b, lambda x: x["t"] == "error"))["text"]
        await a.send_json({"t": "retell", "nudge": "更阴森"})  # the opening, told again for everyone
        heard = await until(b, lambda x: x["t"] == "event" and x["ev"]["type"] == "retold")
        assert heard["ev"] == {"type": "retold", "who": "小明", "nudge": "更阴森"}
        assert (await until(b, lambda x: x["t"] == "event" and x["ev"]["type"] == "keeper_start"))["ev"]["again"]
        assert (await until(b, lambda x: x["t"] == "event" and x["ev"]["type"] == "keeper_end"))["ev"]["text"] == RETOLD
        assert hub.state.log[-1].text == RETOLD and "开场" in cast.retold[0][0]

        fake.queue_totals(3)  # the ending's 理智 +1D6
        await a.send_json({"t": "act", "text": "我拉下制动杆"})
        await a.send_json({"t": "done"})
        book = await until(b, lambda x: x["t"] == "module" and bool(x["module"]["truth"]))  # the whole book, before "over"
        assert book["module"]["npcs"][0]["secrets"] == ["他在钟里藏了东西"]
        await until(b, lambda x: x["t"] == "over")
        await a.send_json({"t": "retell"})  # after the end, the ending itself
        await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "keeper_end" and x["ev"]["text"].endswith("（第 2 版）"))
        assert "【结局：钟停了】" in cast.retold[1][0]
        await b.send_json({"t": "epilogue"})  # told on the host, heard by everyone
        assert (await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "epilogue"))["ev"]["text"] == AFTER
        assert (await until(b, lambda x: x["t"] == "sync" and bool(x["state"]["epilogue"])))["state"]["epilogue"] == AFTER
        late = await http.ws_connect(url)  # someone who joins afterwards gets the book too
        await late.send_json({"t": "hello", "name": "小刚"})
        assert (await until(late, lambda x: x["t"] == "welcome"))["module"]["truth"] == hub.m.truth
        for ws in (a, b, late):
            await ws.close()
    await closed(task)
    assert len(cast.epilogues) == 1


async def test_a_player_at_a_shared_table_asks_from_the_screen(monkeypatch, tmp_path, skills_dir, fake):
    from textual.widgets import Input, Static

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.debrief import DebriefModal
    from autokeeper.tui.modals.retell import RetellModal
    from autokeeper.tui.screens.net import NetGameScreen

    from .test_tui import story_text
    from .test_tui import until as waits

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    cast = teller(LeverCast())
    hub = Hub(EXAMPLE, seats=["玩家"], cast=cast, rules=Rules(dice=fake))
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小明"))
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("enter")  # the free seat
        await pilot.pause(0.1)
        await pilot.press("y")  # ready: the game begins
        assert await waits(pilot, lambda: isinstance(app.screen, NetGameScreen) and app.screen.my_turn)
        screen = app.screen
        blocks = list(screen.story.query(".keeper"))
        await pilot.press("f6")
        assert await waits(pilot, lambda: isinstance(app.screen, RetellModal))
        await pilot.press("enter")  # 换个讲法: the host tells the opening again
        assert await waits(pilot, lambda: RETOLD in story_text(screen))
        assert list(screen.story.query(".keeper")) == blocks and str(blocks[-1].render()) == RETOLD
        fake.queue_totals(3)  # the ending's 理智 +1D6
        screen.query_one(Input).value = "我拉下制动杆"
        await pilot.press("enter")
        await pilot.press("enter")  # that's all from me this round
        assert await waits(pilot, lambda: "F10 查看复盘" in story_text(screen))
        assert screen.state.ending == "stopped" and screen.m.truth == hub.m.truth  # the book came with the ending
        await pilot.press("f10")
        assert await waits(pilot, lambda: isinstance(app.screen, DebriefModal))

        def shown() -> str:
            return "".join(str(w.render()) for w in app.screen.query(Static))

        assert "他在钟里藏了东西" in shown() and "✗ 〔齿轮间〕" in shown()
        await pilot.press("e")  # told on the host, for the whole table
        assert await waits(pilot, lambda: "再没上过楼" in shown())
    await closed(task)
    assert len(cast.retold) == 1 and len(cast.epilogues) == 1
