"""The story beside the story (no model calls): each investigator's own record on the sheet
(本局经历), the 故事至今 written every few turns from what the table saw and nothing else, the
背景 page, the right column's pages, and all of it at a shared table through the sync."""

from __future__ import annotations

import asyncio
import io

import aiohttp
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from rich.console import Console
from rich.text import Text
from textual.widgets import Static, TabbedContent

from autokeeper import config, paths
from autokeeper.agents.cast import Cast
from autokeeper.config import Role
from autokeeper.engine import graph as turn_graph
from autokeeper.engine import progress
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState, Progress, WorldFact
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.net.hub import PRIVATE, Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.sheet import SheetModal, sheet_view
from autokeeper.tui.modals.usage import usage_view
from autokeeper.tui.screens.game import COMMANDS, GameScreen
from autokeeper.tui.widgets.pages import Page, PagesModal
from autokeeper.tui.widgets.panels import CluePanel, HistoryPanel

from .test_engine import FakeCast
from .test_net import TableCast, closed, opened, pool_open, until

ACCOUNT = "周衡来到小镇广场，读了告示栏上的字。"


class Writer(hm.Model):
    """The cheap model: the same account every time; it keeps what it was given for one (the
    chronicler's folds, also canned, are not counted). `gate` holds the reply back, `fail`
    makes the call fail."""

    asked: list[str] = []
    gate: asyncio.Event | None = None
    fail = False

    async def _call(self, messages, *, schema, images, on_delta):
        if "「故事至今」" in messages[-1]["content"]:
            Writer.asked.append(messages[-1]["content"])
        if Writer.gate is not None:
            await Writer.gate.wait()
        if Writer.fail:
            raise RuntimeError("model down")
        return Reply(text=ACCOUNT, data={} if schema else None, usage={"input_tokens": 300, "output_tokens": 40})


@pytest.fixture
def writer(monkeypatch, tmp_path, skills_dir):
    """Every role on the canned writer; saves, traces and the spend ledger in a temp folder."""
    for var in ("AUTOKEEPER_PRESET", "AUTOKEEPER_MODEL", "AUTOKEEPER_FALLBACK", "AUTOKEEPER_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "module_folders", lambda: [EXAMPLE])
    monkeypatch.setattr(Writer, "asked", [])
    monkeypatch.setattr(Writer, "gate", None)
    monkeypatch.setattr(Writer, "fail", False)
    monkeypatch.setitem(hm.MODELS, "canned", Writer)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    return cfg


def plain(renderable, width: int = 200) -> str:
    console = Console(width=width, record=True, file=io.StringIO())
    console.print(renderable)
    return console.export_text()


def table_game(cfg, module: Module) -> tuple[Game, list[dict]]:
    lead = module.pregens[0].model_copy(deep=True)
    st = Game.new(module, [lead], slot="sofar", human=[lead.id])
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    game = Game(module, st, Cast(cfg, module), Rules(), emit=emit)
    game.write_progress = True
    return game, events


def play(st: GameState, turn: int, text: str) -> None:
    """One turn as the log has it: the action and the keeper's telling."""
    st.turn = turn
    st.add("player", text, who=st.investigator.name)
    st.add("keeper", f"（第{turn}轮的叙述）", who="守秘人")


def find(st: GameState, m: Module, clue: str) -> None:
    """A clue turns up, as the engine records it."""
    st.clues.append(clue)
    st.add("clue", f"〔{m.clue(clue).title}〕{m.clue(clue).text.strip()}")


# -- when it is written ------------------------------------------------------------------------
def policy_state(turns: int, *, asked: int = 0) -> tuple[Module, GameState]:
    """A game `turns` turns in, its last account asked for and written at turn `asked`."""
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    st = Game.new(m, [lead], slot="policy", human=[lead.id])
    for turn in range(1, asked + 1):
        play(st, turn, "我四下看看")
    st.progress = Progress(text=ACCOUNT, turn=asked, asked=asked, seen=st.logged, marks=progress.marks(st))
    for turn in range(asked + 1, turns + 1):
        play(st, turn, "我和守钟人聊了聊天气")  # talk and looking around: no milestone
    return m, st


def test_no_new_account_within_six_turns_even_with_a_clue():
    m, st = policy_state(5)
    find(st, m, "c_notice")
    assert progress.moved(st) == ["线索"] and not progress.due(st)
    m, st = policy_state(10, asked=5)
    find(st, m, "c_notice")
    assert not progress.due(st)  # five turns since the last


def test_an_account_six_turns_on_after_a_clue():
    m, st = policy_state(6)
    find(st, m, "c_notice")
    assert progress.due(st)
    m, st = policy_state(11, asked=5)
    find(st, m, "c_notice")
    assert progress.due(st)


def test_no_account_after_twenty_turns_with_nothing_new():
    _, st = policy_state(20, asked=20)
    st.turn = 40  # the count goes on, nothing was said or done
    assert not progress.due(st) and progress.refused(st)


def test_a_catch_up_account_after_fifteen_turns_of_talk():
    _, st = policy_state(14)
    assert not progress.moved(st) and not progress.due(st)  # talk alone, for fourteen turns
    play(st, 15, "我又四下看看")
    assert progress.due(st)
    _, st = policy_state(29, asked=15)
    assert not progress.due(st)
    play(st, 30, "继续聊")
    assert progress.due(st)


def test_the_ending_always_gets_one():
    _, st = policy_state(7, asked=6)
    st.ending = "stopped"
    st.add("ending", "钟停了：钟停在十一点五十九分。")
    assert progress.due(st)  # one turn after the last, at once
    st.progress.asked = st.turn
    assert not progress.due(st)  # and only the one


@pytest.mark.parametrize("what", ["place", "person", "beat", "sanity", "wound", "night", "hours"])
def test_each_milestone_brings_one_six_turns_on(what):
    _, st = policy_state(12, asked=6)
    inv = st.investigators[0]
    if what == "place":
        st.visited.append("tower")
    elif what == "person":
        st.npc("warden").met = True
    elif what == "beat":
        st.flags.append("bell_stopped")
    elif what == "sanity":
        inv.san -= progress.SAN_LOSS
    elif what == "wound":
        inv.hp -= progress.HP_LOSS
    elif what == "night":
        st.minutes += 1440
    else:
        st.minutes += progress.TIME_SKIP
    assert progress.due(st), what


def test_a_little_sanity_or_a_short_while_is_no_milestone():
    _, st = policy_state(12, asked=6)
    st.investigators[0].san -= progress.SAN_LOSS - 1
    st.minutes += progress.TIME_SKIP - 1
    assert not progress.moved(st) and not progress.due(st)


# -- what it is given, and that nobody waits ---------------------------------------------------
async def test_the_story_so_far_comes_after_a_milestone_from_what_the_table_saw(writer):
    m = Module.load(EXAMPLE)
    game, events = table_game(writer, m)
    st = game.state
    # What the table never saw: the director's plan, the keeper's own memory.
    st.improv.append({"turn": 1, "kind": "展开", "plan": {"note": "导演的安排：午夜钟里的东西会出来"}})
    st.transcripts["keeper"] = [{"role": "user", "content": "守秘人自己记的：钟里藏着东西"}]
    st.add("keeper", "午后的广场上没什么人，钟楼的影子斜斜地压在石板路上。", who="守秘人")
    st.progress.marks = progress.marks(st)  # as Game.start leaves it after the opening
    for turn in range(1, 6):
        play(st, turn, f"我做第{turn}件事")
        if turn == 2:
            find(st, m, "c_notice")
            st.add("npc", "外乡人？钟楼这几天不让进。", who="老守钟人")
            dusk = next(e for e in m.events if e.id == "e_dusk")
            st.add("event", dusk.read_aloud)  # what the table was told; its keeper's note is not
        await game.remember()
    assert not Writer.asked and st.progress.asked == -1  # a clue, but not six turns in yet
    play(st, 6, "我做第6件事")
    await game.remember()
    await game._recounting
    asked = Writer.asked[0]
    assert m.hook.strip()[:10] in asked and "钟楼的影子" in asked and "我做第1件事" in asked and "我做第6件事" in asked
    assert m.clue("c_notice").text.strip()[:10] in asked and "老守钟人（NPC）：外乡人？" in asked and dusk.read_aloud in asked
    secrets = [m.truth, m.keeper_notes, *[s for n in m.npcs for s in n.secrets], *[loc.keeper for loc in m.locations if loc.keeper],
               *[e.text for e in m.events], *[c.text.strip() for c in m.clues if c.id != "c_notice"],
               "导演的安排", "守秘人自己记的"]
    assert all(s.strip()[:12] not in asked for s in secrets if s.strip())
    assert st.progress == Progress(text=ACCOUNT, turn=6, asked=6, seen=st.logged, marks=progress.marks(st))
    assert events[-1] == {"type": "progress", "text": ACCOUNT, "turn": 6}  # silent: the screens mark the tab
    assert st.usage["progress"]["calls"] == 1 and "进展" in plain(usage_view(st))  # Ctrl+B counts it
    assert "progress" not in st.transcripts  # stateless: the last account comes with the material

    for turn in range(7, 13):
        play(st, turn, f"我做第{turn}件事")
        await game.remember()
    assert len(Writer.asked) == 1  # six more turns, but nothing new turned up
    find(st, m, "c_archive")
    play(st, 13, "我做第13件事")
    await game.remember()
    await game._recounting
    again = Writer.asked[1]
    assert ACCOUNT in again and "第 6 轮" in again  # the last account, and what came after it
    assert "我做第6件事" not in again and "我做第7件事" in again and "我做第13件事" in again
    assert st.progress.turn == 13


async def test_the_story_so_far_never_holds_up_a_turn_and_a_failed_one_keeps_the_last(writer):
    m = Module.load(EXAMPLE)
    game, events = table_game(writer, m)
    st = game.state
    Writer.gate = asyncio.Event()
    find(st, m, "c_notice")
    for turn in range(1, 7):
        play(st, turn, f"我做第{turn}件事")
        await game.remember()  # returns with the model still writing
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(Writer.asked) == 1 and not game._recounting.done()
    assert st.progress.text == "" and st.progress.asked == 6  # no second call while it writes
    Writer.gate.set()
    await game._recounting
    assert st.progress.text == ACCOUNT

    Writer.fail = True
    find(st, m, "c_archive")
    for turn in range(7, 13):
        play(st, turn, f"我做第{turn}件事")
        await game.remember()
    await game._recounting  # the failure stays in the task
    assert st.progress.text == ACCOUNT and st.progress.turn == 6 and st.progress.asked == 12
    assert [e["type"] for e in events].count("progress") == 1
    Writer.fail = False
    for turn in range(13, 18):
        play(st, turn, f"我做第{turn}件事")
        await game.remember()
    assert len(Writer.asked) == 2  # not again within six turns of the failed call
    play(st, 18, "我做第18件事")
    await game.remember()
    await game._recounting
    assert len(Writer.asked) == 3 and st.progress.turn == 18  # the clue it missed still counts
    assert "我做第7件事" in Writer.asked[2]  # from the last account that was written

    game.write_progress = False  # autoplay and replays write none
    find(st, m, "c_gears")
    for turn in range(19, 40):
        play(st, turn, "继续")
        await game.remember()
    assert len(Writer.asked) == 3


async def test_a_player_may_ask_for_it_once_a_turn(writer):
    m = Module.load(EXAMPLE)
    game, _ = table_game(writer, m)
    st = game.state
    play(st, 1, "我四下看看")
    assert game.recount() == ""  # no milestone needed when asked
    assert game.recount() == "正在整理进展。"
    await game._recounting
    assert st.progress.text == ACCOUNT and "本回合已整理进展" in game.recount()  # once a turn
    st.turn = 2
    assert "暂无新事件" in game.recount()  # nothing since
    play(st, 3, "我再看看")
    assert game.recount() == ""
    await game._recounting
    assert len(Writer.asked) == 2


async def test_the_ending_gets_its_account_at_once_and_the_opening_is_kept(fake):
    """Through the turn graph: the opening is kept for the 背景 page; looking around brings no
    account; the ending brings one at once, four turns in."""

    class Telling(FakeCast):
        def __init__(self, script):
            super().__init__(script)
            self.material: list[str] = []

        async def progress(self, state, material):
            self.material.append(material)
            return f"到第 {state.turn} 轮为止的经过"

    from autokeeper.agents.cast import Action, Ruling

    module = Module.load(EXAMPLE)
    look = Action(kind="investigate", summary="周衡看看广场", target="广场")
    stop = Action(kind="use", summary="周衡拉下制动杆", target="制动杆")
    script = {"看": (look, Ruling(needs_roll=False, minutes=5)), "停": (stop, Ruling(needs_roll=False, set_flags=["bell_stopped"]))}
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    cast = Telling(script)
    game = Game(module, state, cast, Rules(dice=fake))  # type: ignore[arg-type]
    game.write_progress = True
    await game.start()
    assert state.prologue == "叙事" and state.progress.marks == progress.marks(state)
    app = turn_graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": "sofar"}}
    for _ in range(3):
        await app.ainvoke({"input": "看"}, thread)
    assert not cast.material
    fake.queue_totals(3)  # the ending's 理智 +1D6
    await app.ainvoke({"input": "停"}, thread)
    await game._recounting
    assert state.ending and state.progress.turn == 4 and len(cast.material) == 1


def test_an_account_is_shown_as_plain_lines_near_its_length():
    assert progress.trim("**故事至今**\n- 周衡到了广场。\n\n他读了告示。") == "故事至今\n周衡到了广场。\n他读了告示。"
    long = "周衡在广场上转了一圈。" * 40
    cut = progress.trim(long)
    assert cut.endswith("。") and progress.LIMIT <= len(cut) <= progress.LIMIT * 3 // 2


# -- the record ------------------------------------------------------------------------------
def deeds_game() -> tuple[Module, GameState]:
    m = Module.load(paths.MODULES / "zhuishuren")
    party = [p.model_copy(deep=True) for p in m.pregens]
    me, her = party
    st = Game.new(m, party, slot="record", human=[me.id])
    st.turn = 1
    st.add("player", "我去书房看看窗框", who=me.name)
    st.add("dice", "侦查 60 → 23 困难成功 ✓", who=me.name)
    st.add("clue", "〔被撬的窗框〕窗框上有新鲜的撬痕")
    st.turn, st.active = 2, 1
    st.add("player", "我翻翻书桌上的记账簿", who=her.name)
    st.add("clue", "〔记账簿〕最后几页写着一个陌生的名字")
    st.add("sanity", "理智检定 55 → 77 失败，损失 1D3 = 2（55 → 53）", who=me.name)  # an event: everyone checks
    st.active = 0
    st.add("damage", "受到 2 点伤害（HP 12 → 10）", who=me.name)
    st.world["room"] = WorldFact(kind="promise", text="托马斯答应让他们住进空房间", who=me.id, turn=1, source="arbiter")
    st.world["paper"] = WorldFact(kind="relationship", text="伊迪丝和报社主编闹翻了", who=her.id, turn=2, source="arbiter")
    return m, st


def test_the_sheet_tells_the_whole_story_and_what_they_went_through():
    m, st = deeds_game()
    me, her = st.investigators
    sheet = plain(sheet_view(me, None, 100, state=st))
    for k, v in me.backstory.items():
        assert v in sheet, k  # every field, the note on where the card came from too
    assert "为人" in sheet and "牵挂" in sheet and me.occupation in sheet and f"住在{me.residence}" in sheet
    record = sheet.split("本局经历", 1)[1]
    assert "我去书房看看窗框" in record and "侦查 60 → 23 困难成功" in record and "〔被撬的窗框〕" in record
    assert "我翻翻书桌上的记账簿" not in record and "〔记账簿〕" not in record  # hers, on her card
    assert "理智检定 55 → 77" in record and "受伤 1 次，共 2 点" in record and "理智共失去 2 点" in record
    assert "托马斯答应让他们住进空房间（未了）" in record and "闹翻了" not in record
    theirs = plain(sheet_view(her, state=st)).split("本局经历", 1)[1]
    assert "我翻翻书桌上的记账簿" in theirs and "〔记账簿〕" in theirs and "伊迪丝和报社主编闹翻了" in theirs
    assert "本局经历" not in plain(sheet_view(me, width=60))  # outside a game (the lobby, the cards): the card alone
    bare = m.pregens[0].model_copy(update={"backstory": {}, "inventory": []})
    assert "为人" not in plain(sheet_view(bare))


def test_an_old_save_finds_its_record_in_the_log_it_has():
    _, st = deeds_game()
    data = st.model_dump(mode="json")
    for key in ("deeds", "logged", "progress", "prologue"):
        data.pop(key)
    old = GameState.model_validate(data)
    assert old.logged == len(st.log)
    assert [(d.who, d.kind, d.text) for d in old.deeds] == [(d.who, d.kind, d.text) for d in st.deeds]  # clues to whose turn it was


async def test_a_parse_that_breaks_off_leaves_no_line_in_the_record(fake):
    class Down(FakeCast):
        async def intent(self, state, text):
            raise RuntimeError("model down")

    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    game = Game(module, state, Down({}), Rules(dice=fake))  # type: ignore[arg-type]
    app = turn_graph.build(game, InMemorySaver())
    with pytest.raises(RuntimeError):
        await app.ainvoke({"input": "我看看告示栏"}, {"configurable": {"thread_id": "down"}})
    assert state.deeds == [] and state.logged == 0 and state.log == []


# -- a shared table ----------------------------------------------------------------------------
async def keep(ws: aiohttp.ClientWebSocketResponse, heard: list[dict], test) -> dict:
    """The next message that passes `test`; every one before it is kept in `heard`."""
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), 5.0)
        heard.append(msg)
        if test(msg):
            return msg


class StoryCast(TableCast):
    def __init__(self) -> None:
        super().__init__()
        self.material: list[str] = []

    async def progress(self, state, material):
        self.material.append(material)
        return ACCOUNT


async def test_a_shared_table_gets_the_story_so_far_and_the_records_through_the_sync(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    cast = StoryCast()
    hub = Hub(EXAMPLE, seats=["玩家"], cast=cast, rules=Rules(dice=fake))
    assert not {"progress", "deeds", "prologue", "logged"} & PRIVATE
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await ws.send_json({"t": "hello", "name": "小明"})
        welcome = await until(ws, lambda x: x["t"] == "welcome")
        assert "log" not in welcome["state"] and welcome["state"]["progress"]["text"] == ""
        await ws.send_json({"t": "sit", "investigator": "zhouheng"})
        await ws.send_json({"t": "ready"})
        heard: list[dict] = []
        await keep(ws, heard, pool_open)
        acts = ["我看看告示栏", *(f"我四下看看{n}" for n in range(1, 6))]  # a clue first, then six turns in
        for text in acts:
            await ws.send_json({"t": "act", "text": text})
            await ws.send_json({"t": "done"})
            await keep(ws, heard, lambda x: x["t"] == "pool" and not x["open"])  # the keeper settles it
            await keep(ws, heard, pool_open)  # the next round
        while not any(x["t"] == "sync" and x["state"]["progress"]["text"] for x in heard):
            heard.append(await asyncio.wait_for(ws.receive_json(), 5.0))
        told = next(x for x in heard if x["t"] == "event" and x["ev"]["type"] == "progress")
        assert told["ev"] == {"type": "progress", "text": ACCOUNT, "turn": 6}
        view = GameState.model_validate(next(x for x in heard if x["t"] == "sync" and x["state"]["progress"]["text"])["state"])
        assert view.progress.text == ACCOUNT and (view.progress.turn, view.progress.asked) == (6, 6)
        assert [d.text for d in view.deeds if d.kind == "act"] == acts and "c_notice" in view.clues
        assert view.prologue == "叙事" and not view.log  # the opening as told; the log stays on the host
        assert len(cast.material) == 1 and hub.m.truth not in cast.material[0]
        await ws.send_json({"t": "recount"})  # asked for again in the same round
        assert "本回合已整理进展" in (await keep(ws, heard, lambda x: x["t"] == "error"))["text"]
        await ws.send_json({"t": "act", "text": "我再看看"})  # one more turn: saved after it, the account in the save
        await ws.send_json({"t": "done"})
        await keep(ws, heard, lambda x: x["t"] == "pool" and not x["open"])
        await keep(ws, heard, pool_open)
        await ws.send_json({"t": "recount"})  # a new round: asked for, it is written at once
        await keep(ws, heard, lambda x: x["t"] == "event" and x["ev"]["type"] == "progress")
        assert len(cast.material) == 2
        await ws.close()
    await closed(task)
    saved = GameState.model_validate_json(paths.save(hub.state.slot).read_text(encoding="utf-8"))
    assert saved.progress.text == ACCOUNT  # the host keeps it in the save


async def test_a_client_screen_shows_what_the_sync_brings(writer):
    from autokeeper.tui.screens.net import NetGameScreen

    class Link:
        host = False

        async def close(self) -> None:
            return None

    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    st = Game.new(m, [lead], slot="client", human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = NetGameScreen(m, st.model_copy(deep=True), Link(), lead.id, [])  # type: ignore[arg-type]
        app.push_screen(screen)
        await pilot.pause(0.3)
        st.prologue = "午后的广场上没什么人。"
        play(st, 3, "我看看告示栏")
        st.progress = Progress(text=ACCOUNT, turn=3, next=6, seen=st.logged)
        screen._sync(st.model_dump(mode="json", exclude=PRIVATE))
        await pilot.pause()
        assert ACCOUNT in shown(screen, "#progress")
        background = shown(screen, "#background")
        assert "午后的广场上没什么人" in background and m.hook.strip()[:8] in background and m.truth not in background
        await pilot.press("f4")
        await pilot.pause()
        assert isinstance(app.screen, SheetModal) and "我看看告示栏" in plain(app.screen.query_one("#sheet-body", Static).content)


# -- the pages -------------------------------------------------------------------------------
def shown(screen, page: str) -> str:
    return screen.query_one(page, Page).body.content.plain


async def until_idle(pilot, screen) -> None:
    for _ in range(50):
        if screen.game is not None and not screen.busy:
            return
        await pilot.pause(0.1)


async def test_the_right_column_turns_its_pages(writer):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    st = Game.new(m, [lead], slot="pages", human=[lead.id])
    st.prologue = "午后的广场上没什么人，钟楼的影子斜斜地压在石板路上。"
    st.progress = Progress(text=ACCOUNT, turn=3, asked=3)
    st.world["noon"] = WorldFact(kind="promise", text="报社答应等周衡到明天中午", who=lead.id, turn=2, source="arbiter")
    app = AutoKeeperApp(theme="amber")
    said: list[str] = []
    real = app.notify
    app.notify = lambda message, **kw: (said.append(str(message)), real(message, **kw))[-1]  # type: ignore[method-assign]
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, st, resume=True)
        app.push_screen(screen)
        await until_idle(pilot, screen)
        assert screen.game.write_progress
        tabs = screen.query_one("#pages", TabbedContent)
        assert tabs.active == "history-page"
        await pilot.press("ctrl+g")
        assert tabs.active == "progress-page"
        page = shown(screen, "#progress")
        assert ACCOUNT in page and "更新于第 3 轮" in page and "Ctrl+Y" in page and "报社答应等周衡到明天中午" in page
        await pilot.press("ctrl+g")
        assert tabs.active == "background-page"
        background = shown(screen, "#background")
        assert m.title in background and "钟楼的影子" in background and m.hook.strip()[:8] in background and lead.name in background
        assert m.truth not in background and m.keeper_notes not in background
        await pilot.press("ctrl+g")
        assert tabs.active == "history-page"
        await pilot.click(next(t for t in screen.query("#pages Tab") if t.id and t.id.endswith("progress-page")))  # a tab clicked
        assert tabs.active == "progress-page"
        assert {"经过", "进展", "背景", "整理进展"} <= {title for title, *_ in COMMANDS}  # in the Ctrl+P menu
        assert not [c for c in COMMANDS if "每 3 轮" in c[2]]
        await screen.run_action("page('background')")
        assert tabs.active == "background-page"

        notices = len(said)
        st.progress = Progress(text="第二段故事至今。", turn=6, asked=6)  # written in the background meanwhile
        await screen.handle({"type": "progress", "text": st.progress.text, "turn": 6})
        assert "第二段故事至今" in shown(screen, "#progress") and "更新于第 6 轮" in shown(screen, "#progress")
        assert tabs.get_tab("progress-page").label.plain == "进展·新" and len(said) == notices  # silent
        assert paths.save("pages").exists()  # idle: kept at once
        await pilot.press("ctrl+g")  # 背景 → 经过: still waiting
        assert tabs.get_tab("progress-page").label.plain == "进展·新"
        await pilot.press("ctrl+g")  # opened: read
        await pilot.pause()
        assert tabs.active == "progress-page" and tabs.get_tab("progress-page").label.plain == "进展"

        st.turn = 7
        st.add("player", "我看看告示栏", who=lead.name)
        await pilot.press("ctrl+y")  # asked for now: written while play goes on
        await screen.game._recounting
        await pilot.pause()
        assert shown(screen, "#progress").count(ACCOUNT) == 1 and "更新于第 7 轮" in shown(screen, "#progress")
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert "本回合已整理进展，可在下一回合再次整理。" in said  # once a turn

        rounds = len(screen.query(".history-round"))
        st.turn = 4
        screen._history("player", Text("▶ 周衡：我看告示栏"), None)  # shown before its turn is counted
        st.turn = 5
        screen._history("player", Text("▶ 周衡：我去钟楼"), None)
        screen._history("dice", Text("◈ 周衡：.r 1d6 → 4"), None)  # a roll after it, the same round
        await pilot.pause()
        heads = [w.content.plain for w in screen.query(".history-round")][rounds:]
        assert heads == ["— 第 5 轮 —", "— 第 6 轮 —"]
        await pilot.press("ctrl+g", "ctrl+g")  # 经过
        for n in range(40):
            screen._history("player", Text(f"▶ 周衡：第{n}件事"), None)
        await pilot.pause()
        screen.query_one(CluePanel).show([f"线索{n}" for n in range(10)])  # the list below grows, the page shrinks
        await pilot.pause()
        panel = screen.query_one(HistoryPanel)
        assert panel.max_scroll_y > 0 and panel.scroll_y == panel.max_scroll_y  # the newest line still in sight

        await pilot.press("f4")
        await pilot.pause()
        assert isinstance(app.screen, SheetModal) and app.screen.state is st


def test_the_opening_is_on_the_background_page_only_once_told():
    from autokeeper.tui.widgets.pages import background_view

    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    st = Game.new(m, [lead], slot="told", human=[lead.id])
    assert m.opening.strip() not in plain(background_view(m, st, lead))  # the keeper has not told it yet
    st.add("keeper", "（开场的叙述）", who="守秘人")
    assert "（开场的叙述）" in plain(background_view(m, st, lead))  # a save from before the prologue was kept
    st.prologue = "开场原话"
    shown = plain(background_view(m, st, lead))
    assert "开场原话" in shown and m.hook.strip() in shown and lead.name in shown and "未填写人物背景" in shown


def test_a_saved_line_reads_as_the_live_one_did():
    from autokeeper.engine.state import LogEntry
    from autokeeper.tui.widgets.panels import log_line

    hurt = LogEntry(turn=2, time="D1 14:20", kind="damage", who="周衡", text="受到 2 点伤害（HP 12 → 10）")
    lost = LogEntry(turn=2, time="D1 14:20", kind="sanity", who="周衡", text="理智检定 50 → 77 失败，损失 1D3 = 2（50 → 48）")
    assert log_line(hurt).plain == "  ✚ 周衡 受到 2 点伤害（HP 12 → 10）"
    assert log_line(lost).plain.startswith("  ※ 周衡 理智检定 50 → 77")


async def test_without_room_for_the_column_the_pages_open_over_the_story(writer):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    st = Game.new(m, [lead], slot="narrow", human=[lead.id])
    st.progress = Progress(text=ACCOUNT, turn=3, next=6)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:
        screen = GameScreen(m, st, resume=True)
        app.push_screen(screen)
        await until_idle(pilot, screen)
        assert not screen.query_one("#right").display
        await pilot.press("ctrl+g")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, PagesModal) and modal.query_one("#pages", TabbedContent).active == "progress-page"
        assert ACCOUNT in shown(modal, "#progress")
        await pilot.press("tab")
        assert modal.query_one("#pages", TabbedContent).active == "background-page"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen


@pytest.mark.parametrize("height", [24, 30, 34, 40, 44])
async def test_dice_and_clues_leave_the_sidebar_pages_readable(writer, height):
    from autokeeper.tui.widgets.panels import DiceTray, SheetPanel

    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="sidebar-fit")
    st.progress = Progress(text="\n".join(f"第{i}条调查进展" for i in range(30)), turn=3)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, height)) as pilot:
        screen = GameScreen(m, st, resume=True)
        app.push_screen(screen)
        await until_idle(pilot, screen)
        screen.query_one(DiceTray).display = True
        screen.query_one(CluePanel).show([f"很长的线索标题第{i}条" for i in range(20)])
        await pilot.pause()
        tabs = screen.query_one("#pages", TabbedContent)
        assert tabs.outer_size.height >= 8
        assert tabs.region.bottom <= screen.query_one("#right").region.bottom
        await pilot.pause(0.3)  # the clue list's growth laid out (slower runners: Windows' CI) before the tab is clicked
        await pilot.click(tabs.get_tab("progress-page"))
        page = screen.query_one("#progress", Page)
        for _ in range(30):  # switched, then laid out (Windows' CI read a size of 0 in between)
            if tabs.active == "progress-page" and page.size.height >= 5:
                break
            await pilot.pause(0.1)
        assert tabs.active == "progress-page" and page.size.height >= 5
        assert page.max_scroll_y > 0
        page.scroll_end(animate=False)
        for _ in range(30):  # scrolled once laid out (Windows' CI read 0 just after)
            if page.scroll_y == page.max_scroll_y:
                break
            await pilot.pause(0.1)
        assert page.scroll_y == page.max_scroll_y
        assert "第29条调查进展" in shown(screen, "#progress")
        assert screen.query_one(SheetPanel).region.bottom <= tabs.region.y


async def test_a_saved_investigator_s_card_on_the_title_screen_has_their_record(writer, monkeypatch):
    from textual.widgets import OptionList

    from autokeeper import imagery
    from autokeeper.tui.library import write_save
    from autokeeper.tui.screens.cards import CardsScreen

    monkeypatch.setattr(imagery, "available", lambda: False)
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    st = Game.new(m, [lead], slot="example-0101-000000", human=[lead.id])
    play(st, 1, "我看看告示栏")
    write_save(st, m)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(CardsScreen())
        await pilot.pause(0.5)
        screen = app.screen
        assert screen.query_one("#choices", OptionList).highlighted_option.id == f"s{st.slot}:{lead.id}"  # the first card
        preview = plain(screen.query_one("#preview-body", Static).content)
        assert "本局经历" in preview and "我看看告示栏" in preview
        pregen = next(oid for oid in screen.cards if oid.startswith("m"))
        assert screen._game(pregen) is None  # a pregen is in no game
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SheetModal) and app.screen.state is not None and app.screen.state.deeds
