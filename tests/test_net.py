"""LAN play over real websockets, with a stand-in cast (no model calls)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest

import autokeeper
from autokeeper import paths
from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.net.link import address
from autokeeper.paths import EXAMPLE
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
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
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
        assert "已被占用" in (await until(b, lambda x: x["t"] == "error"))["text"]
        await b.send_json({"t": "sit", "investigator": "lin"})  # every seat taken: the game begins

        opened_round = await until(a, pool_open)  # a round: both may act, in any order
        assert opened_round["capacity"] == 3  # two players: three actions, nobody more than two
        await b.send_json({"t": "act", "text": "我抢着先说"})
        await b.send_json({"t": "act", "text": "我再四下看看"})
        pooled = await until(a, lambda x: x["t"] == "pool" and x["count"] == 2)
        assert pooled["open"] and pooled["by"] == {"zhouheng": 0, "lin": 2} and pooled["waiting"] == ["周衡"]
        assert pooled["mine"] == {"now": [], "next": []}  # A sees how many she put in, not what
        await b.send_json({"t": "act", "text": "我还要第三件事"})  # no room for it this round: it waits for the next
        held = await until(b, lambda x: x["t"] == "pool" and bool(x["mine"].get("next")))
        assert held["count"] == 2 and held["mine"] == {"now": ["我抢着先说", "我再四下看看"], "next": ["我还要第三件事"]}
        await b.send_json({"t": "undo"})  # takes back her latest: the one for the next round
        assert (await until(b, lambda x: x["t"] == "pool" and not x["mine"]["next"]))["count"] == 2
        await b.send_json({"t": "undo"})  # then her last one in this round
        await until(a, lambda x: x["t"] == "pool" and x["count"] == 1)
        await b.send_json({"t": "act", "text": "我改主意了，翻翻报纸"})
        await until(a, lambda x: x["t"] == "pool" and x["count"] == 2)  # hers are in first
        await a.send_json({"t": "act", "text": "我看看告示栏"})  # three in: the keeper settles it
        await until(b, lambda x: x["t"] == "pool" and not x["open"])
        said =[(m["ev"]["who"], m["ev"]["text"]) for m in [await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "said") for _ in range(3)]]
        assert said == [("林秋", "我抢着先说"), ("林秋", "我改主意了，翻翻报纸"), ("周衡", "我看看告示栏")]  # as they came in
        await until(a, pool_open)  # told: the next round

        await a.send_json({"t": "say", "text": "（场外）我去倒杯水"})
        assert (await until(b, lambda x: x["t"] == "say"))["from"] == "小明"  # table talk at any time
        await b.close()  # 小红 drops out: the AI plays her seat
        seats = await until(a, lambda x: x["t"] == "seats" and not x["seats"][1]["online"])
        assert [s["online"] for s in seats["seats"]] == [True, False]
        await a.send_json({"t": "act", "text": "我四下看看"})
        assert (await until(a, lambda x: x["t"] == "pool" and x["count"] == 1))["capacity"] == 2  # alone: two
        await a.send_json({"t": "done"})  # one is enough this time
        ai = await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "said" and bool(x["ev"].get("ai")))
        assert ai["ev"]["who"] == "林秋" and cast.ai_turns == ["lin"]
        await until(a, pool_open)
        await a.close()
    await closed(task)
    assert paths.save("lan-test").exists()  # the host saves after every turn


async def test_only_the_host_adds_a_house_rule(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        guest = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await guest.send_json({"t": "hello", "name": "小红"})
        await until(guest, lambda x: x["t"] == "welcome")
        await guest.send_json({"t": "sit", "investigator": "zhouheng"})
        await guest.send_json({"t": "house_rule", "text": "每过一夜要吃一顿饭"})  # its code would run on the host's machine
        assert "房主" in (await until(guest, lambda x: x["t"] == "error"))["text"] and hub.rules.empty()
        host = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await host.send_json({"t": "hello", "name": "小明", "key": hub.key})
        await until(host, lambda x: x["t"] == "welcome")
        await host.send_json({"t": "house_rule", "text": "每过一夜要吃一顿饭"})
        await until(host, lambda x: x["t"] == "error" and "叙述结束后处理" in x["text"])
        await guest.close()
        await host.close()
    await closed(task)


async def test_everyone_has_a_slot_and_one_player_may_take_the_extra_one(skills_dir, fake):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    party = [lead, *(lead.model_copy(update={"id": i, "name": n}, deep=True) for i, n in (("lin", "林秋"), ("su", "苏青")))]
    hub = Hub(EXAMPLE, state=Game.new(m, party, slot="lan-three", human=[p.id for p in party]), cast=TableCast(), rules=Rules(dice=fake))
    hub.sits = {p.id: f"c{i}" for i, p in enumerate(party)}
    hub.open = True
    assert hub.capacity() == 4
    hub.pool = [("zhouheng", "一"), ("zhouheng", "二")]  # he took the extra slot
    assert not hub._may_add("zhouheng") and hub._may_add("lin") and hub._may_add("su")
    hub.pool.append(("lin", "三"))
    assert not hub._may_add("lin") and hub._waiting_for() == ["su"]  # her second would take 苏青's place
    hub.pool.append(("su", "四"))
    assert hub._waiting_for() == []  # full: everyone in, one of them twice


class GatedCast(TableCast):
    """The keeper tells only when the test lets them, so a player can speak meanwhile."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.gate.set()

    async def keeper(self, state, report, on_delta=None, lore=""):
        await self.gate.wait()
        return await super().keeper(state, report, on_delta, lore)


async def test_what_is_said_while_the_keeper_tells_goes_in_first_next_round(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    cast = GatedCast()
    hub = Hub(EXAMPLE, state=Game.new(m, [lead, friend], slot="lan-early", human=[lead.id, "lin"]), cast=cast, rules=Rules(dice=fake))
    task = await opened(hub)
    url = f"ws://127.0.0.1:{hub.port}/ws"
    async with aiohttp.ClientSession() as http:
        a, b = await http.ws_connect(url), await http.ws_connect(url)
        for ws, name, seat in ((a, "小明", "zhouheng"), (b, "小红", "lin")):
            await ws.send_json({"t": "hello", "name": name})
            await until(ws, lambda x: x["t"] == "welcome")
            await ws.send_json({"t": "sit", "investigator": seat})
        await until(b, pool_open)
        cast.gate.clear()  # the keeper takes their time over this round
        await a.send_json({"t": "act", "text": "我看看告示栏"})
        await a.send_json({"t": "done"})
        await b.send_json({"t": "done"})  # nothing from her this round
        await until(b, lambda x: x["t"] == "event" and x["ev"]["type"] == "said")  # the keeper is telling
        await b.send_json({"t": "act", "text": "等等，我拉住他"})
        await b.send_json({"t": "act", "text": "我喊他名字"})
        held = await until(b, lambda x: x["t"] == "pool" and len(x["mine"]["next"]) == 2)
        assert not held["open"] and held["mine"]["next"] == ["等等，我拉住他", "我喊他名字"]
        await b.send_json({"t": "act", "text": "还有一句"})
        assert "2 条" in (await until(b, lambda x: x["t"] == "error"))["text"]
        await b.send_json({"t": "undo"})  # her latest
        await until(b, lambda x: x["t"] == "pool" and x["mine"]["next"] == ["等等，我拉住他"])
        cast.gate.set()
        second = await until(a, lambda x: pool_open(x) and x["by"].get("lin") == 1)
        assert second["count"] == 1 and second["by"]["zhouheng"] == 0  # hers went in first
        assert (await until(b, lambda x: pool_open(x) and bool(x["mine"]["now"])))["mine"] == {"now": ["等等，我拉住他"], "next": []}
        await a.close()
        await b.close()
    await closed(task)


async def test_a_player_joins_from_the_game_screen(monkeypatch, tmp_path, skills_dir, fake):
    from textual.widgets import Input

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.dialogs import RollModal
    from autokeeper.tui.screens.net import NetGameScreen
    from autokeeper.tui.widgets.panels import StoryLog

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=NoticeCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小明"))
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("enter")  # the free seat is highlighted
        await pilot.pause(0.1)
        await pilot.press("y")  # ready: the game begins
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
        await pilot.press("enter")  # an empty line: that's all from me this round
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
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
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
        await a.send_json({"t": "done"})
        await b.send_json({"t": "done"})  # both done before the pool is full: settled now
        heard = []
        while "pool-closed" not in heard or heard[-1] != "pool-open":  # until the next round opens
            msg = await asyncio.wait_for(a.receive_json(), 5.0)
            heard.append(msg["ev"]["type"] if msg["t"] == "event" else ("pool-open" if msg["open"] else "pool-closed") if msg["t"] == "pool" else msg["t"])
        heard = heard[heard.index("pool-closed"):]
        assert "error" not in heard and heard.index("said") < heard.index("keeper_end")  # ruled and told
        fake.queue_totals(3, 2)  # the ending's 理智 +1D6, for each of them
        await a.send_json({"t": "act", "text": "我拉下制动杆"})
        await b.send_json({"t": "act", "text": "我跟上"})
        await a.send_json({"t": "done"})
        await b.send_json({"t": "done"})
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
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=NoticeCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await ws.send_json({"t": "hello", "name": "小明"})
        await until(ws, lambda x: x["t"] == "welcome")
        await ws.send_json({"t": "sit", "investigator": "zhouheng"})
        await ws.send_json({"t": "ready"})
        await until(ws, pool_open)
        fake.queue(30)
        await ws.send_json({"t": "act", "text": "我看看告示栏"})
        await ws.send_json({"t": "done"})
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
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")

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
        await ws.send_json({"t": "done"})
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
    from autokeeper.tui.screens.net import NetGameScreen
    from autokeeper.tui.widgets.speaker import Speaker

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=PreacherCast(), rules=Rules(dice=fake))
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
        screen = app.screen
        assert isinstance(screen, NetGameScreen) and screen.my_turn
        panel = screen.query_one(Speaker)
        screen.query_one(Input).value = "我问布道者在等什么"
        await pilot.press("enter")
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
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.1)
            if not panel.display:
                break
        assert not panel.display
    await closed(task)


async def test_an_ai_seat_that_fails_neither_closes_the_room_nor_blocks_the_next_round(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [lead, friend], slot="lan-fail", human=[lead.id, "lin"])

    class Failing(TableCast):
        fail_keeper = 0
        fail_ai = 0

        async def keeper(self, state, report, on_delta=None, lore=""):
            if state.investigator.id == "lin" and self.fail_keeper:
                self.fail_keeper -= 1
                raise TimeoutError("模型超时")
            return await super().keeper(state, report, on_delta, lore)

        async def investigator(self, state, inv_id, situation):
            if self.fail_ai:
                self.fail_ai -= 1
                raise ConnectionError("断网")
            return await super().investigator(state, inv_id, situation)

    cast = Failing()
    cast.fail_keeper = 3  # the turn and both of the table's tries to finish it
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        a = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await a.send_json({"t": "hello", "name": "小明"})
        await until(a, lambda x: x["t"] == "welcome")
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        await a.send_json({"t": "start"})
        await until(a, pool_open)
        await a.send_json({"t": "act", "text": "我四下看看"})
        await a.send_json({"t": "done"})
        note = await until(a, lambda x: x["t"] == "say" and "林秋" in x["text"])
        assert "没能讲完" in note["text"] and not hub.state.pending_turn  # let go, what settled stays
        await until(a, pool_open)  # the room goes on
        cast.fail_ai = 1  # the AI cannot even think of a move this time
        await a.send_json({"t": "act", "text": "我看看告示栏"})  # the next action is not refused
        await a.send_json({"t": "done"})
        assert (await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "said"))["ev"]["text"] == "我看看告示栏"
        assert "没有行动" in (await until(a, lambda x: x["t"] == "say" and "林秋" in x["text"]))["text"]
        await until(a, pool_open)
        assert "c_notice" in hub.state.clues
        await a.close()
    await closed(task)


async def test_a_rounds_actions_happen_at_once_and_are_told_together(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [lead, friend], slot="lan-round", human=[lead.id, "lin"])

    class Timed(TableCast):
        def __init__(self) -> None:
            super().__init__()
            self.reports: list[str] = []

        async def arbiter(self, state, action, at=None):
            ruling = await super().arbiter(state, action, at)
            return ruling.model_copy(update={"minutes": 30 if "翻" in action.summary else 10})

        async def keeper(self, state, report, on_delta=None, lore=""):
            self.reports.append(report)
            return await super().keeper(state, report, on_delta, lore)

    cast = Timed()
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        a = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        b = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await a.send_json({"t": "hello", "name": "小明"})
        await b.send_json({"t": "hello", "name": "小红"})
        await until(a, lambda x: x["t"] == "welcome")
        await until(b, lambda x: x["t"] == "welcome")
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        await b.send_json({"t": "sit", "investigator": "lin"})
        await until(a, pool_open)
        told_before, start = len(cast.reports), hub.state.minutes
        await a.send_json({"t": "act", "text": "我翻翻旧报纸"})  # 30 minutes
        await b.send_json({"t": "act", "text": "我和站长聊几句"})  # 10, at the same time
        await a.send_json({"t": "done"})
        await b.send_json({"t": "done"})
        held = await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "held")  # the first waits for the second
        assert held
        end = await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "keeper_end")
        assert end and len(cast.reports) == told_before + 1  # one telling for the round
        report = cast.reports[-1]
        assert "同时行动" in report and "〔周衡〕" in report and "〔林秋〕" in report
        await until(a, pool_open)
        assert hub.state.minutes - start == 30  # the longest of them, not their sum
        assert hub.state.round_spent is None and not hub.state.held
        await a.close()
        await b.close()
    await closed(task)


async def test_a_round_is_a_moment_in_real_time_and_nobody_waits_on_who_does_not_act(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [lead, friend], slot="lan-window", human=[lead.id, "lin"])
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    hub.window = 0.6
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        a = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        b = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await a.send_json({"t": "hello", "name": "小明"})
        await b.send_json({"t": "hello", "name": "小红"})
        await until(a, lambda x: x["t"] == "welcome")
        await until(b, lambda x: x["t"] == "welcome")
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        await b.send_json({"t": "sit", "investigator": "lin"})
        assert (await until(a, pool_open))["closes_in"] is None  # nobody has acted: no clock yet
        await a.send_json({"t": "act", "text": "我看看告示栏"})
        counting = await until(a, lambda x: x["t"] == "pool" and x["count"] == 1)
        assert 0 < counting["closes_in"] <= 0.6  # from the first action, the others have the window
        closed_round = await until(a, lambda x: x["t"] == "pool" and not x["open"], limit=3)
        assert closed_round["count"] == 1  # 小红 did not act in time: settled without her, nobody waited
        said = await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "said")
        assert said["ev"]["who"] == "周衡"
        await b.send_json({"t": "act", "text": "我也去看看"})  # too late for that moment: the next round's
        nxt = await until(a, pool_open)
        assert nxt["count"] == 1 and nxt["closes_in"] is not None  # it opens the next moment at once
        await a.close()
        await b.close()
    await closed(task)


async def test_a_fights_round_at_the_table_is_declared_by_everyone_and_settled_once(monkeypatch, tmp_path, skills_dir, fake):

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [lead, friend], slot="lan-fight", human=[lead.id, "lin"])
    state.location = "tower"
    from .test_fight import begun

    begun(m, state, ["warden"])

    class Telling(TableCast):
        def __init__(self) -> None:
            super().__init__()
            self.reports: list[str] = []

        async def keeper(self, state, report, on_delta=None, lore=""):
            self.reports.append(report)
            return await super().keeper(state, report, on_delta, lore)

        async def plan(self, state, text, *, encounter=None, mode="act", check=None, ask=""):  # the keeper arranges the warden
            from autokeeper.agents.encounter import Checked
            from autokeeper.engine.encounter.plan import EncounterTurnPlan

            from .encounter_kit import arrange, attack

            return Checked(value=EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng"))]))

    cast = Telling()
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    fake.queue(*[90] * 6)  # every blow misses and every answer fails: nobody is hurt
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        a = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        b = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await a.send_json({"t": "hello", "name": "小明"})
        await b.send_json({"t": "hello", "name": "小红"})
        await until(a, lambda x: x["t"] == "welcome")
        await until(b, lambda x: x["t"] == "welcome")
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        await b.send_json({"t": "sit", "investigator": "lin"})
        await until(a, pool_open)
        told = len(cast.reports)
        await a.send_json({"t": "act", "text": "攻击 楼梯口打盹的老人（徒手）｜被攻击时闪避"})
        await b.send_json({"t": "act", "text": "攻击 楼梯口打盹的老人（徒手）｜被攻击时反击"})
        await a.send_json({"t": "done"})
        await b.send_json({"t": "done"})
        seen: list[dict] = []
        while True:  # each throws their own dice when asked; the round goes on until it is told
            msg = await asyncio.wait_for(a.receive_json(), 5)
            if msg["t"] == "offer":
                await a.send_json({"t": "choice", "mode": "roll"})
            if msg["t"] == "event":
                seen.append(msg["ev"])
                if msg["ev"]["type"] == "keeper_end":
                    break
            while True:
                try:
                    other = await asyncio.wait_for(b.receive_json(), 0.01)
                except TimeoutError:
                    break
                if other["t"] == "offer":
                    await b.send_json({"t": "choice", "mode": "roll"})
        kinds = [e["type"] for e in seen]
        assert kinds.count("fight_round") == 1 and kinds.count("fight_act") == 3  # two investigators and the warden
        assert len(cast.reports) == told + 1 and "【战斗 · 第 1 轮】" in cast.reports[-1]
        assert hub.state.encounter is not None and hub.state.encounter.round == 1
        await a.close()
        await b.close()
    await closed(task)


async def test_a_fight_reaches_the_table_as_the_table_sees_it_and_with_its_foes_faces(fake, tmp_path):
    from PIL import Image

    from autokeeper.engine import combat as battle
    from autokeeper.engine.encounter import views
    from autokeeper.engine.encounter.plan import EncounterStart
    from autokeeper.engine.game import Turn
    from autokeeper.engine.state import GameState
    from autokeeper.module.schema import Creature

    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    sent: list[dict] = []

    async def collect(msg):
        sent.append(msg)

    hub._broadcast = collect
    for key in ("creature-ghoul", "creature-shade"):  # the shade is the module's too, but not in this fight
        Image.new("RGB", (40, 60), (80, 30, 30)).save(tmp_path / f"{key}.png")
        hub.m.pictures[key] = str(tmp_path / f"{key}.png")
    hub.m.creatures.append(Creature(id="ghoul", name="食尸鬼", called="腐臭的影子", description="腐烂的脸上长着獠牙",
                                    sanity="0/1D6", notes="怕火；护甲 1；每轮恢复 1 点", stats={"生命": 12}, attacks=["爪击 40%，伤害 1D6"]))
    st = hub.state
    fake.queue(30)  # the first sight holds
    assert await hub.game._encounter_open(EncounterStart(mode="fight", foes=["ghoul"], pace=6, reason="它闻到了活人的气味"), Turn())
    hub.game.world.rec.entities["ghoul"].life.hp = 5
    await hub._event({"type": "fight_start", "fight": views.fight_view(hub.game), "line": "【战斗开始】"})
    await hub._event({"type": "reveal", "id": "ghoul", "name": "腐臭的影子", "portrait": "creature-ghoul", "look": "腐烂的脸上长着獠牙"})
    events = [m["ev"] for m in sent if m["t"] == "event"]
    kinds = [e["type"] for e in events]
    art = [e for e in events if e["type"] == "art"]
    assert [a["key"] for a in art] == ["creature-ghoul"] and art[0]["picture_data"]  # its face once, never the bestiary
    assert kinds.index("art") < kinds.index("fight_start")  # the face before the fight that shows it
    start, reveal = events[len(kinds) - 1 - kinds[::-1].index("fight_start")], events[kinds.index("reveal")]
    foe = next(f for f in start["fight"]["fighters"] if f["id"] == "f1")  # its seat, never its own id
    assert "look" not in foe and "dex" not in foe and foe["wound"] == "受伤" and foe["hp"] is None and foe["name"] == "腐臭的影子"
    assert "look" not in reveal and reveal["portrait"] == "creature-ghoul"
    view = hub._view()
    assert "encounter" not in view and "sightings" not in view  # the keeper's: every HP, the arrangements, true names
    now = view["encounter_public"]
    shown = next(f for f in now["fight"]["fighters"] if f["id"] == "f1")
    assert "ghoul" not in [f["id"] for f in [*now["fight"]["fighters"], *start["fight"]["fighters"]]]
    assert shown["hp"] is None and shown["wound"] == "受伤" and not {"armor", "regen", "look", "notes", "sanity", "skills", "dex"} & set(shown)
    assert "食尸鬼" not in str(now)  # the name the players know
    me = st.investigators[0]
    assert next(f for f in now["fight"]["fighters"] if f["id"] == me.id)["hp"] == me.hp  # an investigator whole
    client = GameState.model_validate(view)  # the screens read its choices from the table's view
    assert client.encounter is None and client.encounter_public["choices"][me.id] == views.table(hub.game)["choices"][me.id]
    runner = battle.from_investigator(me)
    ghoul = hub.game._fighter("ghoul")
    runner.pos, ghoul.pos = 2, 0
    st.chase = battle.Chase(runners=[runner, ghoul], track=battle.default_track("street"), quarry="party", reason="它追来了")
    view = hub._view()
    legs = [leg["name"] for leg in view["chase"]["track"]["legs"]]
    assert all(legs[:5]) and not any(legs[5:]) and view["chase"]["reason"] == ""  # the way only as far as it is seen
    legs = [leg["name"] for leg in view["encounter_public"]["chase"]["legs"]]
    assert all(legs[:5]) and not any(legs[5:])
    await hub.game.close()


class FakeLink:
    host = False

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)

    async def close(self) -> None:
        pass


async def test_a_seated_players_screen_gets_each_rounds_choices_and_the_heartbeat_in_a_fight(fake):
    from autokeeper.engine.encounter import views
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.net import NetGameScreen

    from .encounter_kit import begin
    from .test_engine import FakeCast

    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="lan", human=[m.pregens[0].id])
    st.location, st.prologue, st.turn = "tower", "（开场已讲过）", 3
    seat = st.investigators[0].id
    link = FakeLink()

    def pool(open_: bool, mine: list[str] | None = None) -> dict:
        return {"t": "pool", "open": open_, "capacity": 2, "count": len(mine or []), "by": {}, "done": [], "waiting": [],
                "open_to": [seat] if open_ and not mine else [], "extra": "", "closes_in": None,
                "mine": {"now": mine or [], "next": []}}

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen = NetGameScreen(m, st.model_copy(deep=True), link, seat, [])
        app.push_screen(screen)
        await pilot.pause(0.3)
        picker = screen.query_one("#picker")
        host = Game(m, st, FakeCast({}), Rules(dice=fake))  # type: ignore[arg-type]
        await begin(host, ["warden"])
        view = {**st.model_dump(mode="json", exclude={"encounter"}), "encounter_public": views.table(host)}
        await screen.on_msg({"t": "event", "ev": {"type": "fight_start", "fight": views.fight_view(host), "line": "【战斗开始】"}})
        await screen.on_msg({"t": "sync", "state": view})  # the state with the fight, after the turn
        await pilot.pause(0.3)
        assert screen._beating and not picker.display  # the round is not open to this seat yet
        await screen.on_msg(pool(True))
        await pilot.pause(0.2)
        words = [picker.get_option_at_index(i).id for i in range(picker.option_count)] if picker.display else []
        assert any(w.startswith("攻击 楼梯口打盹的老人（徒手）") for w in words) and screen._clock is None  # the table's own window
        picker.highlighted = 0
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert {"t": "act", "text": words[0]} in link.sent and not picker.display
        await screen.on_msg(pool(True, mine=[words[0]]))  # theirs is in: no list again this round
        await pilot.pause(0.1)
        assert not picker.display
        await screen.on_msg(pool(True))
        await pilot.pause(0.1)
        assert picker.display  # the next round's
        await screen.on_msg(pool(False))
        await pilot.pause(0.1)
        assert not picker.display  # the round closed: settled by the host


async def test_an_ai_player_in_a_fight_sees_it_and_the_rounds_choices(fake):
    from autokeeper.engine.autoplay import situation
    from autokeeper.engine.encounter import choices

    from .encounter_kit import begin
    from .test_engine import FakeCast

    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    st = Game.new(m, [lead, friend], slot="ai-fight")
    st.location = "tower"
    game = Game(m, st, FakeCast({}), Rules(dice=fake))  # type: ignore[arg-type]
    await begin(game, ["warden"])
    game.world.rec.entities["warden"].life.hp = 3
    text = situation(m, st, "lin", game)
    assert "正在战斗（第 1 轮）" in text and "楼梯口打盹的老人（重伤" in text and "HP 3" not in text  # its wounds in words
    assert "林秋这一轮可以做的" in text and "周衡这一轮可以做的" not in text
    order = next(line.strip().split(" —— ")[0] for line in text.splitlines() if line.strip().startswith("攻击 楼梯口打盹的老人"))
    found = choices.match(game, "lin", order)
    assert found is not None and found.do == "attack" and found.attacks[0].target == "warden"  # the very words the engine matches
    game.state.encounter = None
    assert "正在战斗" not in situation(m, st, game=game) and situation(m, st, game=game).endswith("你下一步做什么？")
    await game.close()
