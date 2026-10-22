"""Choosing who you play at a LAN table: your own card, a pregen, or one made on the host's
models; checked by the Hub, shown to everyone (no model calls)."""

from __future__ import annotations

import asyncio
import io
from typing import Any

import aiohttp
import pytest
from PIL import Image

from autokeeper import paths, roster
from autokeeper.agents import card as cardmod
from autokeeper.agents.card import Builder
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net import cards as netcards
from autokeeper.net import media
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from tests.test_card import CardModel, cards  # noqa: F401  (the canned card models, as a fixture)
from tests.test_net import TableCast, closed, opened, until

STATS = {"力量": 60, "体质": 70, "体型": 50, "敏捷": 55, "外貌": 45, "智力": 80, "意志": 65, "教育": 75}


def card(**changes: Any) -> dict[str, Any]:
    base = {"id": "card-mine", "name": "沈芸", "occupation": "记者", "age": 28, "characteristics": dict(STATS), "luck": 60,
            "skills": {"侦查": 70, "图书馆使用": 60}, "backstory": {"个人描述": "短发，说话快"}, "inventory": ["笔记本"]}
    return base | changes


def jpeg(colour: str = "red") -> str:
    out = io.BytesIO()
    Image.new("RGB", (64, 80), colour).save(out, "JPEG")
    path = paths.DATA / "face.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out.getvalue())
    return media.pack(str(path))


async def hello(http: aiohttp.ClientSession, hub: Hub, name: str, **extra: Any) -> tuple[aiohttp.ClientWebSocketResponse, dict]:
    ws = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
    await ws.send_json({"t": "hello", "name": name, **extra})
    return ws, await until(ws, lambda x: x["t"] == "welcome")


def test_a_card_is_checked_and_its_derived_values_worked_out_again():
    inv = netcards.vet(card(hp=99, hp_max=40, mp=50, mp_max=50, san=30, name="沈" * 80, backstory={"个人描述": "长" * 2000}), "x")
    assert inv.hp_max == (70 + 50) // 10 and inv.hp == inv.hp_max  # never the card's own numbers
    assert inv.mp_max == 65 // 5 and inv.mp == inv.mp_max and inv.san == 30 and inv.san_start_of_day == 30
    assert inv.move == 9 and inv.damage_bonus == "0" and len(inv.name) == 24 and len(inv.backstory["个人描述"]) == 400
    for bad, why in ((card(characteristics=STATS | {"力量": 120}), "属性"), (card(name=""), "姓名"), (card(luck=0), "幸运"),
                     (card(skills={"侦查": 150}), "技能"), (card(characteristics={"力量": 50}), "八项"), ("不是卡", "无法读取"),
                     (card(conditions=["死亡"]), "无法参加"), (card(age=7), "年龄")):  # fmt: skip
        with pytest.raises(netcards.Refused, match=why):
            netcards.vet(bad, "x")


def test_a_modern_card_in_the_twenties_gets_a_word_for_the_host():
    m = Module.load(EXAMPLE)
    assert netcards.era_note(netcards.vet(card(skills={"计算机使用": 60}), "x"), m)
    assert not netcards.era_note(netcards.vet(card(), "x"), m)


async def test_a_brought_card_takes_the_seat_and_everyone_sees_it(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家", "玩家"], cast=TableCast(), rules=Rules(dice=fake))  # the pregen's seat and an empty one
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        a, welcome = await hello(http, hub, "小明")
        assert welcome["bring"] and [s.get("blank", False) for s in welcome["seats"]] == [False, True]
        host, _ = await hello(http, hub, "房主", key=hub.key)
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        confirmed = await until(a, lambda x: x["t"] == "seated")
        assert confirmed["investigator"] == "zhouheng" and "state" in confirmed and "pool" in confirmed
        await a.send_json({"t": "bring", "investigator": card(skills={"计算机使用": 60}, hp_max=99), "portrait": jpeg()})
        seated = await until(a, lambda x: x["t"] == "seated")
        mine = seated["investigator"]
        face = await until(host, lambda x: x["t"] == "face")  # the portrait reaches every screen
        assert media.picture(face["picture_data"]) and hub.m.pictures[face["key"]]
        synced = await until(host, lambda x: x["t"] == "sync" and any(i["name"] == "沈芸" for i in x["state"]["investigators"]))
        inv = next(i for i in synced["state"]["investigators"] if i["id"] == mine)
        assert inv["hp_max"] == 12 and inv["portrait"] == face["key"]
        seats = (await until(host, lambda x: x["t"] == "seats" and x["seats"][0]["name"] == "沈芸"))["seats"]
        assert seats[0]["origin"] == "own" and seats[0]["player"] == "小明" and "现代" in seats[0]["note"]  # for the host
        others = (await until(a, lambda x: x["t"] == "seats" and x["seats"][0]["name"] == "沈芸"))["seats"]
        assert "note" not in others[0]
        assert hub.state.human[0] == mine and hub.sits[mine] and "zhouheng" not in hub.sits

        await a.send_json({"t": "bring", "investigator": card(characteristics=STATS | {"意志": 0})})
        assert "1 到 99" in (await until(a, lambda x: x["t"] == "error"))["text"]
        await a.send_json({"t": "bring", "pregen": "zhouheng"})  # back to the pregen
        assert (await until(a, lambda x: x["t"] == "seated"))["investigator"] == "zhouheng"
        b, _ = await hello(http, hub, "小红")
        await b.send_json({"t": "sit", "investigator": "seat-1"})
        await b.send_json({"t": "bring", "pregen": "zhouheng"})
        assert "其他座位" in (await until(b, lambda x: x["t"] == "error"))["text"]
        await b.send_json({"t": "ready"})
        assert "先选" in (await until(b, lambda x: x["t"] == "error"))["text"]
        await b.send_json({"t": "bring", "investigator": card(name="林秋")})
        theirs = (await until(b, lambda x: x["t"] == "seated"))["investigator"]
        await a.send_json({"t": "ready"})
        await until(b, lambda x: x["t"] == "seats" and x["seats"][0]["ready"])
        assert not hub.started.is_set()  # 林秋 is not ready yet
        await b.send_json({"t": "ready"})
        await until(a, lambda x: x["t"] == "begin")
        assert [i.name for i in hub.state.investigators] == ["周衡", "林秋"] and hub.state.human == ["zhouheng", theirs]
        await b.send_json({"t": "bring", "pregen": "zhouheng"})
        assert "不可更换角色" in (await until(b, lambda x: x["t"] == "error"))["text"]
        for ws in (a, b, host):
            await ws.close()
    await closed(task)


async def test_starting_now_fills_or_drops_the_empty_seats(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    with pytest.raises(ValueError, match="额外座位"):
        Hub(EXAMPLE, seats=["玩家", "AI"], cast=TableCast(), rules=Rules(dice=fake))
    hub = Hub(EXAMPLE, seats=["不上场", "玩家", "玩家"], cast=TableCast(), rules=Rules(dice=fake))  # only empty seats
    assert hub.state.investigators == [] and list(hub.sits) == ["seat-1", "seat-2"]
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        a, _ = await hello(http, hub, "小明")
        await a.send_json({"t": "start"})
        assert "尚无已选角色" in (await until(a, lambda x: x["t"] == "error"))["text"] and not hub.started.is_set()
        await a.send_json({"t": "sit", "investigator": "seat-2"})
        confirmed = await until(a, lambda x: x["t"] == "seated")
        assert confirmed["investigator"] == "seat-2" and "state" in confirmed
        await a.send_json({"t": "start"})  # the seat with a player gets the free pregen; the other goes
        assert (await until(a, lambda x: x["t"] == "seated"))["investigator"] == "zhouheng"
        await until(a, lambda x: x["t"] == "begin")
        assert [i.id for i in hub.state.investigators] == ["zhouheng"] and list(hub.sits) == ["zhouheng"] and not hub.blank
        await a.close()
    await closed(task)


async def test_a_saved_game_keeps_its_party(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="lan-old", human=["zhouheng"])
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        a, welcome = await hello(http, hub, "小明")
        assert not welcome["bring"]
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        await a.send_json({"t": "bring", "investigator": card()})
        assert "存档" in (await until(a, lambda x: x["t"] == "error"))["text"]
        await a.send_json({"t": "card", "op": "begin"})
        await a.send_json({"t": "card", "op": "propose", "n": 1})
        assert "开局前" in (await until(a, lambda x: x["t"] == "card"))["error"]
        assert [i.name for i in hub.state.investigators] == ["周衡"] and not hub.builders
        await a.close()
    await closed(task)


async def test_the_host_s_own_cards_never_reach_the_table(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    secret = netcards.vet(card(name="房主的秘密角色"), "card-secret")
    roster.keep(secret, {})
    hub = Hub(EXAMPLE, seats=["玩家", "玩家"], cast=TableCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    heard: list[str] = []
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await ws.send_json({"t": "hello", "name": "小红"})
        await ws.send_json({"t": "sit", "investigator": "seat-1"})
        await ws.send_json({"t": "bring", "investigator": card()})
        while True:
            msg = await asyncio.wait_for(ws.receive(), 5.0)
            heard.append(msg.data)
            if '"seated"' in msg.data:
                break
        await ws.close()
    await closed(task)
    assert not any("房主的秘密角色" in text or "card-secret" in text for text in heard)


class SlowBuilder(Builder):
    """The canned card models, with the questions held until the test lets them go."""

    gate: asyncio.Event

    async def propose(self):
        await self.gate.wait()
        return await super().propose()


async def test_a_card_is_made_on_the_host_one_step_at_a_time(cards, monkeypatch, tmp_path, skills_dir, fake):  # noqa: F811
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    SlowBuilder.gate = asyncio.Event()
    hub.card_builder = lambda: SlowBuilder(cards, hub.m)
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        a, _ = await hello(http, hub, "小明")
        watcher, _ = await hello(http, hub, "小红")
        await watcher.send_json({"t": "card", "op": "begin"})
        await watcher.send_json({"t": "card", "op": "look_up", "n": 1, "query": "一个女记者"})
        assert "入座玩家" in (await until(watcher, lambda x: x["t"] == "card"))["error"]  # only seated players
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        await a.send_json({"t": "card", "op": "begin"})
        await a.send_json({"t": "card", "op": "begin"})  # again: still one card for this player
        await a.send_json({"t": "card", "op": "look_up", "n": 1, "query": "在码头长大、胆子很大的女记者"})
        found = await until(a, lambda x: x["t"] == "card" and x["n"] == 1)
        assert found["figure"]["source"] == "描述" and len(hub.builders) == 1
        await a.send_json({"t": "card", "op": "propose", "n": 2})
        await a.send_json({"t": "card", "op": "propose", "n": 3})  # while the first still runs
        assert "上一步尚未完成" in (await until(a, lambda x: x["t"] == "card" and x["n"] == 3))["error"]
        SlowBuilder.gate.set()
        asked = await until(a, lambda x: x["t"] == "card" and x["n"] == 2)
        questions = asked["proposal"]["questions"]
        answers = [[q["ask"], q["options"][0]] for q in questions]
        await a.send_json({"t": "card", "op": "build", "n": 4, "answers": answers})
        built = (await until(a, lambda x: x["t"] == "card" and x["n"] == 4))["card"]
        assert built["name"] == "周樟寿" and hub.builders[next(iter(hub.builders))].answers[0] == tuple(answers[0])
        opened_here: list[Any] = []
        monkeypatch.setattr(cardmod, "read_file", lambda path: opened_here.append(path))
        (tmp_path / "secret.json").write_text("{}", encoding="utf-8")
        await a.send_json({"t": "card", "op": "read", "n": 5, "text": str(tmp_path / "secret.json")})
        await until(a, lambda x: x["t"] == "card" and x["n"] == 5)
        assert not opened_here  # a path typed by a player is never opened on the host
        await a.send_json({"t": "card", "op": "end"})
        await a.send_json({"t": "bring", "investigator": built, "made": True})
        await until(a, lambda x: x["t"] == "seated")
        assert hub.state.investigators[0].name == "周樟寿" and hub.origin[hub.state.investigators[0].id] == "new" and not hub.builders
        for ws in (a, watcher):
            await ws.close()
    await closed(task)
    assert CardModel.calls[:2] == ["Proposal", "Draft"]


async def _until(pilot, test, rounds: int = 100) -> None:
    for _ in range(rounds):
        if test():
            return
        await pilot.pause(0.05)
    raise AssertionError("the screen never got there")


async def test_a_player_picks_and_makes_their_card_in_the_lobby(cards, monkeypatch, tmp_path, skills_dir, fake):  # noqa: F811
    from textual.widgets import Input, OptionList, Static

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.menu import GameMenu
    from autokeeper.tui.screens.card import CardScreen
    from autokeeper.tui.screens.lobby import LobbyScreen
    from autokeeper.tui.screens.seatcard import SeatCardScreen

    roster.keep(netcards.vet(card(name="我的老卡"), "card-old"), {})
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    hub.card_builder = lambda: Builder(cards, hub.m)
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小明"))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.5)
        lobby = app.screen
        assert isinstance(lobby, LobbyScreen)
        await pilot.press("enter")  # the free seat
        await pilot.pause(0.2)
        await pilot.press("r")  # 选角色
        await _until(pilot, lambda: isinstance(app.screen, SeatCardScreen))
        picker = app.screen
        ol = picker.query_one(OptionList)
        assert "r:card-old" in picker.cards and "m:zhouheng" in picker.cards
        ol.highlighted = ol.get_option_index("r:card-old")
        await pilot.press("enter")  # one of my own
        await _until(pilot, lambda: hub.state.investigators[0].name == "我的老卡" and lobby.chosen == hub.state.investigators[0].id)
        await pilot.press("r")
        await _until(pilot, lambda: isinstance(app.screen, SeatCardScreen))
        await pilot.press("a")  # 新建角色
        await _until(pilot, lambda: isinstance(app.screen, GameMenu))
        await pilot.press("enter")  # AI 协助车卡
        await _until(pilot, lambda: isinstance(app.screen, CardScreen))
        screen = app.screen
        screen.query_one(Input).value = "在码头长大、胆子很大的女记者"
        await pilot.press("enter")
        await _until(pilot, lambda: screen.step == "photo")  # a described person: a photo from this machine, or none
        await pilot.press("down", "enter")  # 不使用照片
        await _until(pilot, lambda: screen.step == "ask")
        for _ in range(3):
            await pilot.press("space")
            await pilot.pause()
        await _until(pilot, lambda: screen.step == "extra")
        await pilot.press("enter")
        await _until(pilot, lambda: screen.step == "card")
        await pilot.press("enter")  # 就用这张卡
        await _until(pilot, lambda: app.screen is lobby and hub.state.investigators[0].name == "周樟寿")
        await _until(pilot, lambda: "周樟寿" in str(lobby.query_one("#lobby-investigator", Static).render()))
        assert {c.name for c in roster.saved()} == {"我的老卡", "周樟寿"}  # kept on this machine too
        await pilot.press("y")  # ready: the game begins
        await _until(pilot, lambda: hub.started.is_set())
    await closed(task)


async def test_a_browser_seat_never_lists_the_host_s_cards(monkeypatch, tmp_path, skills_dir, fake):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.seatcard import SeatCardScreen

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    roster.keep(netcards.vet(card(name="房主的卡"), "card-host"), {})
    monkeypatch.setenv("AUTOKEEPER_GUEST", "1")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=TableCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小红"))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("enter")
        await pilot.pause(0.2)
        await pilot.press("r")
        await _until(pilot, lambda: isinstance(app.screen, SeatCardScreen))
        assert app.screen.mine is None and not any(k.startswith("r:") for k in app.screen.cards)
    await closed(task)
