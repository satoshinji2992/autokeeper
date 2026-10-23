"""Offline transport regressions: real sockets, saved declarations and seat-bound cards."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import aiohttp
import pytest
from aiohttp import web

from autokeeper import paths
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState, WorldFact, read_save
from autokeeper.module.schema import Item, Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast, until


@pytest.fixture
def transport_hub(fake):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [lead, friend], slot="transport-table", human=[lead.id, friend.id])
    return Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))


@asynccontextmanager
async def sockets(hub):
    """Run only the transport; no background campaign or model is started."""
    app = web.Application()
    app.router.add_get("/ws", hub.ws)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as http:
            yield http, f"ws://127.0.0.1:{port}/ws"
    finally:
        await runner.cleanup()


async def sit(http, url, seat):
    ws = await http.ws_connect(url)
    await ws.send_json({"t": "hello", "name": seat})
    await until(ws, lambda msg: msg["t"] == "welcome")
    await ws.send_json({"t": "sit", "investigator": seat})
    await until(ws, lambda msg: msg["t"] == "seated")
    return ws


async def test_disconnect_retains_queued_declarations_and_reconnect_shows_them(transport_hub):
    hub = transport_hub
    async with sockets(hub) as (http, url):
        a = await sit(http, url, "zhouheng")
        b = await sit(http, url, "lin")
        for ws, text in ((a, "检查门锁"), (b, "留意楼梯")):
            await ws.send_json({"t": "act", "text": text})
            await until(ws, lambda msg, text=text: msg["t"] == "pool" and msg["mine"]["next"] == [text])
        await b.close()
        await until(a, lambda msg: msg["t"] == "seats" and not msg["seats"][1]["online"])
        assert hub.early == [("zhouheng", "检查门锁"), ("lin", "留意楼梯")]
        b = await sit(http, url, "lin")
        await b.send_json({"t": "hello", "name": "重连"})
        await until(b, lambda msg: msg["t"] == "welcome")
        await b.send_json({"t": "sit", "investigator": "lin"})
        seated = await until(b, lambda msg: msg["t"] == "seated")
        assert seated["pool"]["mine"]["next"] == ["留意楼梯"]
        await b.send_json({"t": "undo"})
        await until(b, lambda msg: msg["t"] == "pool" and not msg["mine"]["next"])
        assert hub.early == [("zhouheng", "检查门锁")]
        await a.close()
        await b.close()


async def test_disconnected_queued_action_is_collected_without_ai_replacement(transport_hub, monkeypatch):
    hub = transport_hub
    hub.early = [("lin", "留意楼梯")]
    hub.sits = {"zhouheng": "online", "lin": None}
    monkeypatch.setattr(hub, "_waiting_for", lambda: [])
    await hub._collect()
    assert hub.pool == [("lin", "留意楼梯")]
    calls = []

    async def settle(order):
        calls.extend(order)

    monkeypatch.setattr(hub, "_settle_round", settle)
    await hub._settle()
    assert calls == [("lin", "留意楼梯")]


async def test_pending_originals_survive_save_reload_and_explicit_undo(transport_hub, fake):
    hub = transport_hub
    hub.sits["zhouheng"] = "a"
    await hub._on("a", {"t": "act", "text": "检查门锁"})
    saved = read_save(paths.save(hub.state.slot).read_text(encoding="utf-8"))
    restored = Hub(EXAMPLE, state=saved, cast=TableCast(), rules=Rules(dice=fake))
    assert restored.early == [("zhouheng", "检查门锁")]
    restored.sits["zhouheng"] = "new"
    await restored._on("new", {"t": "undo"})
    saved = read_save(paths.save(hub.state.slot).read_text(encoding="utf-8"))
    assert not saved.queued_actions


async def test_open_collection_survives_host_restart(transport_hub, fake):
    hub = transport_hub
    hub.sits["zhouheng"] = "a"
    hub.open = True
    await hub._on("a", {"t": "act", "text": "检查门锁"})
    saved = read_save(paths.save(hub.state.slot).read_text(encoding="utf-8"))
    restored = Hub(EXAMPLE, state=saved, cast=TableCast(), rules=Rules(dice=fake))
    assert restored.early == [("zhouheng", "检查门锁")]
    assert not restored.state.round_plan


async def test_batch_restart_skips_finished_actions_and_keeps_duplicate_originals(transport_hub, monkeypatch, fake):
    hub = transport_hub
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.pool = [("zhouheng", "检查门锁"), ("lin", "留意楼梯"), ("lin", "留意楼梯")]
    run = hub._run
    calls = 0

    async def stop_before_second(text, seat, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            await hub._on("a", {"t": "act", "text": "记下窗户"})
            raise asyncio.CancelledError
        await run(text, seat, **kwargs)

    monkeypatch.setattr(hub, "_run", stop_before_second)
    with pytest.raises(asyncio.CancelledError):
        await hub._settle()
    saved = read_save(paths.save(hub.state.slot).read_text(encoding="utf-8"))
    assert saved.round_plan["done"] == ["0"]
    assert saved.queued_actions == [("zhouheng", "记下窗户")]
    restored = Hub(EXAMPLE, state=saved, cast=TableCast(), rules=Rules(dice=fake))
    await restored._settle()
    actions = [line.text for line in restored.state.log if line.kind == "player"]
    assert sum("检查门锁" in line for line in actions) == 1
    assert sum("留意楼梯" in line for line in actions) == 2
    assert restored.cast.ai_turns == []
    assert restored.early == [("zhouheng", "记下窗户")]
    assert not restored.state.round_plan and not restored.state.pending_turn


async def test_refusal_before_journal_does_not_enter_retry_loop(transport_hub, monkeypatch):
    hub = transport_hub
    hub.sits = {"zhouheng": "a", "lin": "b"}
    calls, messages = [], []

    async def run(text, seat, **kwargs):
        calls.append((seat, text))
        if seat == "lin":
            raise ValueError("原现场的遭遇尚未结束")

    async def send(cid, msg):
        messages.append((cid, msg))

    monkeypatch.setattr(hub, "_run", run)
    monkeypatch.setattr(hub, "_send", send)
    await asyncio.wait_for(hub._settle_round([("lin", "留意楼梯"), ("zhouheng", "检查门锁")]), 0.5)
    assert calls == [("lin", "留意楼梯"), ("zhouheng", "检查门锁")]
    assert hub.state.unfinished["lin"]["remaining"] == "留意楼梯"
    assert hub.retrying is None and not hub.state.pending_turn
    assert not any(msg.get("retry") for _, msg in messages)
    assert any(cid == "b" and "尚未执行" in msg.get("text", "") for cid, msg in messages)


async def test_socket_cards_are_projected_for_each_seat_in_all_state_envelopes(transport_hub):
    hub = transport_hub
    for i, inv in enumerate(hub.state.investigators):
        inv.backstory = {"秘密": f"私人背景{i}", "恐惧症和躁狂症": f"疯狂详情{i}"}
        inv.inventory = [f"未公开物品{i}"]
        inv.madness.temporary_until = 777 + i
    hub.m.pregens = [inv.model_copy(deep=True) for inv in hub.state.investigators]
    hub.state.world["host-secret"] = WorldFact(kind="fact", text="主持人内部事实", who="warden", turn=1, source="director")
    hub.state.event_attempts["event-secret"] = {"day": 1, "roll": 77}
    hub.state.pending_story_flags["flag-secret"] = ["clue-secret"]
    hub.m.items.append(Item(id="secret_item", name="未公开物品1", description="私人物品", where=hub.state.location,
                           owner="lin", important=True, hidden=True))
    async with sockets(hub) as (http, url):
        a = await sit(http, url, "zhouheng")
        b = await sit(http, url, "lin")
        watcher = await http.ws_connect(url)
        await watcher.send_json({"t": "hello", "name": "旁观"})
        public = await until(watcher, lambda msg: msg["t"] == "welcome")
        assert all(not inv["backstory"] and not inv["inventory"] for inv in public["module"]["pregens"])
        for kind in ("sync", "turn", "over"):
            await hub._broadcast({"t": kind, "state": hub._view()})
            for ws, mine in ((a, "zhouheng"), (b, "lin"), (watcher, None)):
                message = await until(ws, lambda msg, kind=kind: msg["t"] == kind)
                view = GameState.model_validate(message["state"])
                assert not view.world and not view.event_attempts and not view.pending_story_flags
                for inv in view.investigators:
                    source = hub.state.find(inv.id)
                    assert inv.characteristics == source.characteristics and inv.skills == source.skills
                    if inv.id == mine:
                        assert inv.backstory == source.backstory and inv.inventory == source.inventory
                        assert inv.madness == source.madness
                    else:
                        assert not inv.backstory and not inv.inventory and inv.madness.temporary_until is None
        await hub._share_book()
        own = await until(b, lambda msg: msg["t"] == "module")
        other = await until(a, lambda msg: msg["t"] == "module")
        assert own["module"]["pregens"][1]["backstory"] == hub.state.find("lin").backstory
        assert not other["module"]["pregens"][1]["backstory"]
        assert "secret_item" not in {item["id"] for item in other["module"]["items"]}
        Module.model_validate(own["module"])
        await a.close()
        await b.close()
        await watcher.close()


def test_module_projection_excludes_new_backstage_collections_and_keeps_selectable_cards(transport_hub):
    from autokeeper.module.schema import Agenda, Bond, Foreshadow, Past, Stage, Tie

    hub = transport_hub
    hub.m.relations = [Bond(a="warden", b="zhouheng", kind="trust", why="幕后关系")]
    hub.m.agendas = [Agenda(npc="warden", goals=["幕后计划"])]
    hub.m.history = [Past(when="昨夜", text="幕后前史")]
    hub.m.ties = [Tie(id="private-tie", hook="私人牵连")]
    hub.m.foreshadow = [Foreshadow(id="private-sign", hint="未演出伏笔", where=hub.state.location, pays_off="秘密结局",
                                 stages=[Stage(hint="秘密阶段")])]
    book = hub._book()
    parsed = Module.model_validate(book)
    assert not parsed.relations and not parsed.agendas and not parsed.history and not parsed.ties and not parsed.foreshadow
    assert parsed.pregens[0].id == "zhouheng"
    assert parsed.pregens[0].characteristics == hub.m.pregens[0].characteristics


def test_item_projection_requires_ownership_or_recorded_observation(transport_hub):
    from autokeeper.world.record import PLAYERS, Entity

    hub = transport_hub
    item = Item(id="private-key", name="私人钥匙", description="钥匙", where=hub.state.location,
                owner="lin", important=True, hidden=True)
    hub.m.items.append(item)
    hub.state.find("lin").inventory = [item.name, "不记名私物"]
    hub.state.record.entities[item.id] = Entity(id=item.id, kind="item", name=item.name, at="lin", important=True,
                                               hidden=True)
    assert item.id not in {row["id"] for row in hub._book("zhouheng")["items"]}
    assert item.id in {row["id"] for row in hub._book("lin")["items"]}
    hub.state.record.entities[item.id].observed_by = ["zhouheng"]
    assert hub._view("zhouheng")["investigators"][1]["inventory"] == [item.name]
    assert not hub._view()["investigators"][1]["inventory"]
    hub.state.record.entities[item.id].observed_by.append(PLAYERS)
    assert hub._view()["investigators"][1]["inventory"] == [item.name]
    hub.state.record.entities["ordinary"] = Entity(id="ordinary", kind="item", name="不记名私物", at="lin",
                                                  observed_by=["zhouheng"])
    assert hub._view("zhouheng")["investigators"][1]["inventory"] == [item.name, "不记名私物"]
    assert hub._view()["investigators"][1]["inventory"] == [item.name]


async def test_graph_focus_refusal_keeps_original_and_allows_other_seat(transport_hub, monkeypatch):
    hub = transport_hub
    hub.sits = {"zhouheng": "a", "lin": "b"}
    monkeypatch.setattr(hub.game, "_focus_blocked", lambda: "原现场尚未结束" if hub.state.investigator.id == "lin" else "")
    await asyncio.wait_for(hub._settle_round([("lin", "留意楼梯"), ("zhouheng", "检查门锁")]), 2.0)
    assert hub.state.turn == 1 and not hub.state.pending_turn
    assert hub.state.unfinished["lin"]["original"] == "留意楼梯"


async def test_two_refused_declarations_from_one_seat_are_both_retained(transport_hub, monkeypatch):
    hub = transport_hub
    monkeypatch.setattr(hub.game, "_focus_blocked", lambda: "原现场尚未结束")
    await hub._settle_round([("lin", "留意楼梯"), ("lin", "检查门锁")])
    assert hub.state.unfinished["lin"]["remaining"] == "留意楼梯\n检查门锁"
    assert not hub.state.pending_turn


async def test_abandoned_partial_turn_is_closed_in_same_save_as_its_journal(transport_hub, monkeypatch):
    hub = transport_hub
    await hub.game.arrange_party([("lin", "检查门锁")])
    hub.state.pending_turn = {"input": "检查门锁", "party_token": "0", "active": 1}

    async def failed(*args, **kwargs):
        raise RuntimeError("离线技术中断")

    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(hub, "_run", failed)
    monkeypatch.setattr(hub, "_broadcast", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await hub._unattended(hub.state.find("lin"), RuntimeError("离线技术中断"))
    saved = read_save(paths.save(hub.state.slot).read_text(encoding="utf-8"))
    assert not saved.pending_turn
    assert saved.round_plan["done"] == ["0"]


@pytest.mark.parametrize("route", ["round", "command", "ai"])
async def test_ordinary_declaration_echo_is_public_on_every_submission_route(transport_hub, monkeypatch, route):
    from autokeeper.agents.cast import Proposal

    hub = transport_hub
    secret = "我检查内袋里的私人信件，暂不告诉同伴"

    async def noop(*args, **kwargs):
        return None

    async def propose(*args, **kwargs):
        return Proposal(text=secret)

    monkeypatch.setattr(hub, "_run", noop)
    monkeypatch.setattr(hub, "_interlude", noop)
    monkeypatch.setattr(hub.cast, "investigator", propose)
    async with sockets(hub) as (http, url):
        a = await sit(http, url, "zhouheng")
        b = await sit(http, url, "lin")
        watcher = await http.ws_connect(url)
        await watcher.send_json({"t": "hello", "name": "旁观"})
        await until(watcher, lambda msg: msg["t"] == "welcome")
        if route == "round":
            await hub._on(hub.sits["zhouheng"], {"t": "act", "text": secret})
            await hub._settle_round([("zhouheng", secret)])
        elif route == "command":
            await hub._afterwards("zhouheng", secret)
        else:
            await hub._ai_turn(hub.state.find("zhouheng"))
        await hub._broadcast({"t": "notice", "text": "boundary"})
        for ws in (a, b, watcher):
            echoes = []
            while True:
                message = await asyncio.wait_for(ws.receive_json(), 2.0)
                if message.get("text") == "boundary":
                    break
                if message["t"] == "event" and message["ev"]["type"] == "said":
                    echoes.append(message["ev"])
            assert len(echoes) == 1
            assert echoes[0]["text"] == secret and "audience" not in echoes[0]
        await a.close()
        await b.close()
        await watcher.close()


async def test_only_a_reader_of_latest_telling_can_queue_retell(transport_hub, monkeypatch):
    from autokeeper.engine.game import Told

    hub = transport_hub
    entry = hub.state.add("keeper", "林秋独处时看见的场面", audience=["lin"])
    hub.game.told = Told(report="private", lore="", entry=entry, sky=("", 0))
    hub.open = True
    calls = []

    async def retell(nudge):
        calls.append(nudge)

    monkeypatch.setattr(hub.game, "retell", retell)
    async with sockets(hub) as (http, url):
        a = await sit(http, url, "zhouheng")
        b = await sit(http, url, "lin")
        await a.send_json({"t": "retell", "nudge": "短一点"})
        error = await until(a, lambda msg: msg["t"] == "error")
        assert "已见叙述" in error["text"] and hub.asks.empty()
        await b.send_json({"t": "retell", "nudge": "短一点"})
        await b.send_json({"t": "say", "text": "boundary"})
        await until(b, lambda msg: msg["t"] == "say" and msg["text"] == "boundary")
        assert hub.asks.qsize() == 1
        await hub._asks()
        assert calls == ["短一点"]
        await a.close()
        await b.close()


async def test_queued_retell_rechecks_readership_after_changing_seats(transport_hub, monkeypatch):
    from autokeeper.engine.game import Told

    hub = transport_hub
    entry = hub.state.add("keeper", "林秋独处时看见的场面", audience=["lin"])
    hub.game.told = Told(report="private", lore="", entry=entry, sky=("", 0))
    hub.open = True
    hub.sits = {"zhouheng": None, "lin": "b"}
    calls, messages = [], []

    async def retell(nudge):
        calls.append(nudge)

    async def send(cid, msg):
        messages.append((cid, msg))

    monkeypatch.setattr(hub.game, "retell", retell)
    monkeypatch.setattr(hub, "_send", send)
    await hub._ask_retell("b", "短一点")
    assert hub.asks.qsize() == 1
    hub.sits = {"zhouheng": "b", "lin": None}
    await hub._asks()
    assert not calls
    assert any(msg["t"] == "error" and "已见叙述" in msg["text"] for _, msg in messages)
