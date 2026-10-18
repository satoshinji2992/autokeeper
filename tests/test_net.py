"""LAN play over real websockets, with a stand-in cast (no model calls)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest

import autokeeper
from autokeeper import config
from autokeeper.agents.cast import Action, Ruling
from autokeeper.config import EXAMPLE
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.net.link import address
from autokeeper.rules.coc import Rules


class TableCast:
    """Agents that answer at once: every action is ruled trivially (reading the notice board
    finds its clue), AI players look around."""

    improvising = False

    def __init__(self) -> None:
        self.ai_turns: list[str] = []

    async def intent(self, state, text):
        return Action(kind="investigate", summary=f"{state.investigator.name}：{text}")

    async def arbiter(self, state, action, at=None):
        board = "告示栏" in action.summary and "c_notice" not in state.clues
        return Ruling(needs_roll=False, minutes=5, auto_clues=["c_notice"] if board else [])

    async def keeper(self, state, report, on_delta=None, lore=""):
        if on_delta:
            await on_delta("叙事")
        return "叙事"

    async def npc(self, state, npc_id, message):
        return "……"

    async def investigator(self, state, inv_id, situation):
        self.ai_turns.append(inv_id)
        return "我四下看看"

    async def chronicle(self, state, entries):
        return "摘要"

    async def lore(self, state, query, keywords=None):
        return ""

    async def close(self) -> None:
        return None


async def until(ws: aiohttp.ClientWebSocketResponse, test, limit: float = 5.0) -> dict[str, Any]:
    """The next message that passes `test` (others are skipped)."""
    while True:
        msg = await asyncio.wait_for(ws.receive_json(), limit)
        if test(msg):
            return msg


def pool_open(x: dict) -> bool:
    return x["t"] == "pool" and x["open"]


async def opened(hub: Hub) -> asyncio.Task:
    """The Hub serving on a free port."""
    task = asyncio.create_task(hub.serve("127.0.0.1", 0, linger=True))
    for _ in range(100):
        if hub.port:
            break
        await asyncio.sleep(0.02)
    return task


async def closed(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def all_of(ws: aiohttp.ClientWebSocketResponse, **tests) -> dict[str, dict[str, Any]]:
    """The first message passing each named test, in whatever order they arrive."""
    found: dict[str, dict[str, Any]] = {}
    while len(found) < len(tests):
        msg = await asyncio.wait_for(ws.receive_json(), 5.0)
        for name, test in tests.items():
            if name not in found and test(msg):
                found[name] = msg
    return found


def test_addresses_accept_what_people_type():
    assert address("192.168.1.5") == "ws://192.168.1.5:8765/ws"
    assert address("192.168.1.5:9000") == "ws://192.168.1.5:9000/ws"
    assert address("ws://example.lan:8765") == "ws://example.lan:8765/ws"


async def test_two_players_share_a_table_and_the_ai_covers_for_a_dropout(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [lead, friend], slot="lan-test", human=[lead.id, "lin"])
    cast = TableCast()
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    task = await opened(hub)
    url = f"ws://127.0.0.1:{hub.port}/ws"
    async with aiohttp.ClientSession() as http:
        a = await http.ws_connect(url)
        b = await http.ws_connect(url)
        await a.send_json({"t": "hello", "name": "小明"})
        welcome = await until(a, lambda x: x["t"] == "welcome")
        assert [s["investigator"] for s in welcome["seats"]] == ["zhouheng", "lin"] and not welcome["started"]
        book = welcome["module"]  # the keeper's book stays on the host
        assert m.truth and not book["truth"] and "transcripts" not in welcome["state"] and "improv" not in welcome["state"]
        assert welcome["version"] == autokeeper.__version__  # a client on another version is warned
        assert any(n.secrets for n in m.npcs) and not any(n["secrets"] for n in book["npcs"])
        assert book["clues"] == []
        await b.send_json({"t": "hello", "name": "小红"})
        await until(b, lambda x: x["t"] == "welcome")
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        await until(b, lambda x: x["t"] == "seats" and x["seats"][0]["player"] == "小明")  # A is seated first
        await b.send_json({"t": "sit", "investigator": "zhouheng"})
        assert "已经有人" in (await until(b, lambda x: x["t"] == "error"))["text"]
        await b.send_json({"t": "sit", "investigator": "lin"})  # every seat taken: the game begins

        await until(a, pool_open)  # a round: both may act, in any order
        await b.send_json({"t": "act", "text": "我抢着先说"})
        pooled = await until(a, lambda x: x["t"] == "pool" and x["done"] == ["lin"])
        assert pooled["open"] and pooled["waiting"] == ["周衡"]  # nothing is settled until everyone is in
        await b.send_json({"t": "act", "text": "我改主意了，四下看看"})  # sent again: it replaces hers
        await a.send_json({"t": "act", "text": "我看看告示栏"})  # the pool is full: the keeper settles it
        await until(b, lambda x: x["t"] == "pool" and not x["open"])
        await b.send_json({"t": "act", "text": "我插一句"})
        assert "结算" in (await until(b, lambda x: x["t"] == "error"))["text"]  # no actions while the keeper tells
        said = [(m["ev"]["who"], m["ev"]["text"]) for m in [await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "said") for _ in range(2)]]
        assert said == [("周衡", "我看看告示栏"), ("林秋", "我改主意了，四下看看")]  # in seat order
        await until(a, pool_open)  # told: the next round

        await a.send_json({"t": "say", "text": "（场外）我去倒杯水"})
        assert (await until(b, lambda x: x["t"] == "say"))["from"] == "小明"  # table talk at any time
        await b.close()  # 小红 drops out: the AI plays her seat
        seats = await until(a, lambda x: x["t"] == "seats" and not x["seats"][1]["online"])
        assert [s["online"] for s in seats["seats"]] == [True, False]
        await a.send_json({"t": "act", "text": "我四下看看"})  # the only seated player: the pool is full
        ai = await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "said" and bool(x["ev"].get("ai")))
        assert ai["ev"]["who"] == "林秋" and cast.ai_turns == ["lin"]
        await until(a, pool_open)
        await a.close()
    await closed(task)
    assert (tmp_path / "saves" / "lan-test.json").exists()  # the host saves after every turn


async def test_a_player_joins_from_the_game_screen(monkeypatch, tmp_path, skills_dir, fake):
    from textual.widgets import Input

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.net import NetGameScreen
    from autokeeper.tui.screens import RollModal
    from autokeeper.tui.widgets import StoryLog

    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=NoticeCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小明"))
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("enter")  # the free seat is highlighted
        for _ in range(40):
            await pilot.pause(0.1)
            if isinstance(app.screen, NetGameScreen) and app.screen.my_turn:
                break
        screen = app.screen
        assert isinstance(screen, NetGameScreen) and screen.my_turn and screen.seat == "zhouheng"
        assert not screen.m.truth and not screen.m.clues  # not found yet
        fake.queue_totals(5)
        screen.query_one(Input).value = ".r 1d6"
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.1)
            if "1d6 → 5" in "".join(str(w.render()) for w in screen.query_one(StoryLog).children):
                break
        assert screen.my_turn  # a plain roll leaves the turn with you
        fake.queue(30)  # reading the board takes a 侦查 roll, thrown on this screen
        screen.query_one(Input).value = "我看看告示栏"
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.1)
            if isinstance(app.screen, RollModal):
                break
        assert isinstance(app.screen, RollModal) and fake.values
        await pilot.pause(0.5)
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.1)
            if "↳ 获得线索〔广场告示〕" in "".join(str(w.render()) for w in screen.query_one(StoryLog).children):
                break  # the dice have settled, and under them what they led to
        lines = "".join(str(w.render()) for w in screen.query_one(StoryLog).children)
        assert "周衡（小明·房主）：我看看告示栏" in lines and "↳ 获得线索〔广场告示〕" in lines and "叙事" in lines
        notice = hub.m.clue("c_notice")
        assert "c_notice" in screen.state.clues and screen.m.clue("c_notice").title == notice.title  # revealed by the event
    await closed(task)


class LeverCast(TableCast):
    """Pulling the lever stops the bell: the sample module's happy ending."""

    async def arbiter(self, state, action, at=None):
        if "制动杆" in action.summary:
            return Ruling(needs_roll=False, minutes=5, set_flags=["bell_stopped"])
        return await super().arbiter(state, action, at)


async def test_a_check_is_the_turn_s_action_and_growth_waits_for_the_end(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(update={"marks": ["侦查"]}, deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋", "marks": []}, deep=True)
    state = Game.new(m, [lead, friend], slot="lan-dice", human=[lead.id, "lin"])
    hub = Hub(EXAMPLE, state=state, cast=LeverCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    url = f"ws://127.0.0.1:{hub.port}/ws"
    async with aiohttp.ClientSession() as http:
        a, b = await http.ws_connect(url), await http.ws_connect(url)
        for ws, name, seat in ((a, "小明", "zhouheng"), (b, "小红", "lin")):
            await ws.send_json({"t": "hello", "name": name})
            await until(ws, lambda x: x["t"] == "welcome")
            await ws.send_json({"t": "sit", "investigator": seat})
        await until(a, pool_open)
        fake.queue_totals(4)
        await a.send_json({"t": "act", "text": ".r 1d6"})  # at once, and not the round's action
        assert (await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "command"))["ev"]["text"] == "1d6 → 4"
        await a.send_json({"t": "act", "text": ".ra 侦查"})  # a check is the action
        await b.send_json({"t": "act", "text": ".en"})
        early = await until(b, lambda x: x["t"] == "event" and x["ev"]["type"] == "command" and x["ev"]["who"] == "林秋")
        assert early["ev"]["kind"] == "refused" and "结局" in early["ev"]["text"]
        await b.send_json({"t": "act", "text": "我四下看看"})
        heard = []
        while "pool-closed" not in heard or heard[-1] != "pool-open":  # until the next round opens
            msg = await asyncio.wait_for(a.receive_json(), 5.0)
            heard.append(msg["ev"]["type"] if msg["t"] == "event" else ("pool-open" if msg["open"] else "pool-closed") if msg["t"] == "pool" else msg["t"])
        heard = heard[heard.index("pool-closed"):]
        assert "error" not in heard and heard.index("said") < heard.index("keeper_end")  # ruled and told
        fake.queue_totals(3, 2)  # the ending's 理智 +1D6, for each of them
        await a.send_json({"t": "act", "text": "我拉下制动杆"})
        await b.send_json({"t": "act", "text": "我跟上"})
        await until(a, lambda x: x["t"] == "over")
        fake.queue_totals(80, 6)  # 80 > 55: 侦查 grows by 6
        await a.send_json({"t": "act", "text": ".en"})
        grown = await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "command")
        assert grown["ev"]["who"] == "周衡" and "侦查 成长检定 80 → +6" in grown["ev"]["text"]
        await a.send_json({"t": "act", "text": ".en"})
        assert "已经做过" in (await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "command"))["ev"]["text"]
        await a.close()
        await b.close()
    await closed(task)
    assert hub.state.investigators[0].skills["侦查"] == 61


class NoticeCast(TableCast):
    """Reading the notice board takes a 侦查 roll."""

    async def arbiter(self, state, action, at=None):
        if "告示栏" in action.summary and "c_notice" not in state.clues:
            return Ruling(needs_roll=True, skill="侦查", minutes=5, clues_on_success=["c_notice"], reason="告示贴得很高，字又小")
        return await super().arbiter(state, action, at)


async def test_a_seated_player_throws_their_own_dice(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=NoticeCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await ws.send_json({"t": "hello", "name": "小明"})
        await until(ws, lambda x: x["t"] == "welcome")
        await ws.send_json({"t": "sit", "investigator": "zhouheng"})
        await until(ws, pool_open)
        fake.queue(30)
        await ws.send_json({"t": "act", "text": "我看看告示栏"})
        offer = (await until(ws, lambda x: x["t"] == "offer"))["offer"]
        assert offer["kind"] == "roll" and (offer["skill"], offer["target"], offer["why"]) == ("侦查", 55, "告示贴得很高，字又小")
        assert fake.values  # the dice wait for the player
        await ws.send_json({"t": "choice", "mode": "roll"})
        led = await until(ws, lambda x: x["t"] == "event" and x["ev"]["type"] == "outcome")
        assert led["ev"]["lines"] == ["获得线索〔广场告示〕"] and not fake.values
        await ws.close()
    await closed(task)


async def test_the_director_s_reasoning_stays_on_the_host(skills_dir, fake):
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await ws.send_json({"t": "hello", "name": "小明"})
        await until(ws, lambda x: x["t"] == "welcome")
        await hub._event({"type": "improv", "kind": "后果", "summary": "", "reason": "让守钟人起了疑心"})
        first = await until(ws, lambda x: x["t"] in ("module", "event"))
        assert first["t"] == "module" and not first["module"]["truth"]  # new places and people before the event
        ev = await until(ws, lambda x: x["t"] == "event")
        assert ev["ev"]["type"] == "improv" and "reason" not in ev["ev"]
        await ws.close()
    await closed(task)


async def test_private_rulings_flags_and_pending_clues_never_reach_a_client(fake):
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    sent = []

    async def collect(msg):
        sent.append(msg)

    hub._broadcast = collect
    hub.state.pending_push = {"skill": "侦查", "clues": ["hidden_clue"], "flags": ["hidden_flag"], "can_push": True}
    hub.state.flags.append("hidden_flag")
    await hub._event({"type": "ruling", "reason": "幕后真相", "clues_on_success": ["hidden_clue"]})
    assert not sent
    await hub._event({"type": "flag", "flag": "hidden_flag", "meaning": "幕后真相"})
    assert len(sent) == 1 and sent[0]["t"] == "sync"
    view = sent[0]["state"]
    assert not view["flags"] and not view["fired"]
    assert "clues" not in view["pending_push"] and "flags" not in view["pending_push"]
    await hub.game.close()


async def test_a_seated_player_can_resume_a_failed_narration(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")

    class InterruptedTable(TableCast):
        calls = 0

        async def keeper(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("offline")
            return await super().keeper(*args, **kwargs)

    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    hub = Hub(EXAMPLE, state=st, cast=InterruptedTable(), rules=Rules(dice=fake))
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await ws.send_json({"t": "hello", "name": "小明"})
        await until(ws, lambda x: x["t"] == "welcome")
        await ws.send_json({"t": "sit", "investigator": st.investigator.id})
        await until(ws, pool_open)
        await ws.send_json({"t": "act", "text": "我看看告示栏"})
        error = await until(ws, lambda x: x["t"] == "error")
        assert error["retry"] and st.pending_turn and st.turn == 1
        minute = st.minutes
        await ws.send_json({"t": "act", "text": "/retry"})
        await until(ws, lambda x: x["t"] == "event" and x["ev"]["type"] == "keeper_end")
        await until(ws, pool_open)
        assert not st.pending_turn and st.turn == 1 and st.minutes == minute
        assert st.clues.count("c_notice") == 1
        await ws.close()
    await closed(task)


class PreacherCast(TableCast):
    """Speaking to the preacher in the square gets a wary answer; anything else is looking around."""

    async def intent(self, state, text):
        if "布道者" in text:
            return Action(kind="talk", summary=f"{state.investigator.name}问布道者", npc="preacher")
        return await super().intent(state, text)

    async def npc(self, state, npc_id, message):
        return "（压低声音，四下张望）午夜之前，离开这里。"


async def test_the_speaker_shows_at_a_shared_table(monkeypatch, tmp_path, skills_dir, fake):
    from textual.widgets import Input

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.net import NetGameScreen
    from autokeeper.tui.speaker import Speaker

    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=PreacherCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小明"))
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("enter")  # the free seat
        for _ in range(40):
            await pilot.pause(0.1)
            if isinstance(app.screen, NetGameScreen) and app.screen.my_turn:
                break
        screen = app.screen
        assert isinstance(screen, NetGameScreen) and screen.my_turn
        panel = screen.query_one(Speaker)
        screen.query_one(Input).value = "我问布道者在等什么"
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.1)
            if panel.display and panel.feeling == "警惕":
                break
        assert panel.display and panel.who == "街头布道者" and panel.feeling == "警惕"  # his face, from the table's module
        assert panel.portrait is not None and panel.portrait.art == hub.m.art["preacher"]
        for _ in range(40):  # the next round is told without him: he steps back
            await pilot.pause(0.1)
            if screen.my_turn and not screen.pooled:
                break
        screen.query_one(Input).value = "我四下看看"
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.1)
            if not panel.display:
                break
        assert not panel.display
    await closed(task)
