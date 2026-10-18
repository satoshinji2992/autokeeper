"""LAN play over real websockets, with a stand-in cast (no model calls)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest

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

    async def keeper(self, state, report, on_delta=None):
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
        assert any(n.secrets for n in m.npcs) and not any(n["secrets"] for n in book["npcs"])
        assert not any(c["text"] for c in book["clues"]) and len(book["clues"]) == len(m.clues)
        await b.send_json({"t": "hello", "name": "小红"})
        await until(b, lambda x: x["t"] == "welcome")
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        await until(b, lambda x: x["t"] == "seats" and x["seats"][0]["player"] == "小明")  # A is seated first
        await b.send_json({"t": "sit", "investigator": "zhouheng"})
        assert "已经有人" in (await until(b, lambda x: x["t"] == "error"))["text"]
        await b.send_json({"t": "sit", "investigator": "lin"})  # every seat taken: the game begins

        turn = await until(a, lambda x: x["t"] == "turn")
        assert turn["investigator"] == "zhouheng" and turn["player"] == "小明"
        await b.send_json({"t": "act", "text": "我抢着先说"})
        assert "还没轮到你" in (await until(b, lambda x: x["t"] == "error"))["text"]
        await a.send_json({"t": "act", "text": "我看看告示栏"})
        said = await until(b, lambda x: x["t"] == "event" and x["ev"]["type"] == "said")
        assert said["ev"]["who"] == "周衡" and said["ev"]["player"] == "小明"  # the other player sees it
        await until(b, lambda x: x["t"] == "event" and x["ev"]["type"] == "keeper_end")

        await a.send_json({"t": "say", "text": "（场外）我去倒杯水"})
        heard = await all_of(b, say=lambda x: x["t"] == "say", turn=lambda x: x["t"] == "turn")
        assert heard["say"]["from"] == "小明" and heard["turn"]["investigator"] == "lin"
        await b.close()  # 小红 drops out on her own turn: the AI plays it
        seen = await all_of(
            a,
            seats=lambda x: x["t"] == "seats" and not x["seats"][1]["online"],
            said=lambda x: x["t"] == "event" and x["ev"]["type"] == "said" and bool(x["ev"].get("ai")),
        )
        assert [s["online"] for s in seen["seats"]["seats"]] == [True, False]
        assert seen["said"]["ev"]["who"] == "林秋" and seen["said"]["ev"].get("ai") and cast.ai_turns == ["lin"]
        assert (await until(a, lambda x: x["t"] == "turn"))["investigator"] == "zhouheng"
        await a.close()
    await closed(task)
    assert (tmp_path / "saves" / "lan-test.json").exists()  # the host saves after every turn


async def test_a_player_joins_from_the_game_screen(monkeypatch, tmp_path, skills_dir, fake):
    from textual.widgets import Input

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.net import NetGameScreen
    from autokeeper.tui.widgets import StoryLog

    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
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
        assert not screen.m.truth and not screen.m.clue("c_notice").text  # not found yet
        screen.query_one(Input).value = "我看看告示栏"
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.1)
            if "周衡（小明）：我看看告示栏" in "".join(str(w.render()) for w in screen.query_one(StoryLog).children):
                break
        lines = "".join(str(w.render()) for w in screen.query_one(StoryLog).children)
        assert "周衡（小明）：我看看告示栏" in lines and "叙事" in lines
        notice = hub.m.clue("c_notice")
        assert "c_notice" in screen.state.clues and screen.m.clue("c_notice").title == notice.title  # revealed by the event
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
