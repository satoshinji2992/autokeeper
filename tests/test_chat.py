"""Table talk (engine/chat.py): out-of-character chat, words to the party, and actions, at a
single-player table with AI companions and at a LAN table (net/hub.Hub._speak)."""

from __future__ import annotations

import asyncio
from types import MethodType, SimpleNamespace

import pytest

from autokeeper.engine import chat
from autokeeper.engine import world as legacy_world
from autokeeper.engine.autoplay import situation
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import WorldChange
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast
from .test_tui import canned, story_text  # noqa: F401  (canned: a fixture, used by name)

TEAM = ["林秋", "约翰·史密斯"]


@pytest.mark.parametrize("text, aside", [("//我去倒杯水", "我去倒杯水"), ("((我去倒杯水))", "我去倒杯水"),
                                         ("（（我去倒杯水））", "我去倒杯水"), ("// ", "")])
def test_out_of_character_needs_an_explicit_marker(text, aside):
    assert chat.ooc(text) == aside and chat.said(text, TEAM) is None


@pytest.mark.parametrize("text", ["（压低声音）别进那扇门。", "(我去倒杯水)", "约翰，你怎么看？", "对管家说：你好", "对林秋说"])
def test_a_single_bracket_or_plain_words_stay_in_character_actions(text):
    assert chat.ooc(text) is None and chat.said(text, TEAM) is None  # the keeper reads it, as before


@pytest.mark.parametrize("text, line, to", [
    ("“把枪给我”", "“把枪给我”", ""),
    ("「走吧」", "「走吧」", ""),
    ('"走吧"', '"走吧"', ""),
    ("『走吧』", "『走吧』", ""),
    ("（压低声音）“别进那扇门。”", "（压低声音）“别进那扇门。”", ""),
    ("对林秋说：快跑", "快跑", "林秋"),
    ("对约翰说，等等我", "等等我", "约翰·史密斯"),
    ("对大家说 分头找", "分头找", "大家"),
])  # fmt: skip
def test_words_to_the_party_are_marked_by_quotes_or_whom_they_are_for(text, line, to):
    assert chat.said(text, TEAM) == chat.Said(line, to)


def test_alone_there_is_nobody_to_say_it_to():
    assert chat.said("“有人吗？”", []) is None  # the keeper hears it, as an action


def table(fake, location: str = "tower"):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋", "inventory": ["左轮手枪"]}, deep=True)
    st = Game.new(m, [lead, friend], human=[lead.id])
    st.location = location
    cast = FakeCast({})
    game = Game(m, st, cast, Rules(dice=fake))  # type: ignore[arg-type]
    return m, st, game, cast


async def test_words_to_the_party_are_recorded_without_the_keeper_or_a_turn(fake):
    m, st, game, cast = table(fake)
    turn, minutes = st.turn, st.minutes
    entry = game.speak("zhouheng", "（压低声音）“把枪给我。”", "林秋")
    assert entry.kind == "speech" and entry.who == "周衡" and entry.text == "在钟楼对林秋说：（压低声音）“把枪给我。”"
    assert (st.turn, st.minutes) == (turn, minutes) and not cast.heard and not cast.ruled and not cast.reports
    assert st.find("lin").inventory == ["左轮手枪"] and "左轮手枪" not in st.find("zhouheng").inventory  # a request moves nothing
    event = game.world.rec.events[-1]  # said in front of the warden: he heard it
    assert event.place == "tower" and "warden" in event.witnesses and event.participants == ["zhouheng", "lin"]
    assert "林秋" in situation(m, st, "lin", game) and "把枪给我" in situation(m, st, "lin", game)  # her AI reads it next
    assert not st.deeds  # words, not a deed on the sheet
    await game.close()


async def test_the_keeper_hears_the_party_talk_once_in_its_next_report(fake):
    _, st, game, _ = table(fake)
    game.speak("zhouheng", "“先别碰那口钟。”")
    report = game._report(Turn())
    assert "【调查员之间的交谈】" in report and "周衡在钟楼对同伴说：“先别碰那口钟。”" in report
    assert "不替被问到的调查员回答、交出东西或行动" in report
    st.add("keeper", "钟声停了。", who="守秘人")
    assert "【调查员之间的交谈】" not in game._report(Turn())  # told: not again
    await game.close()


def test_a_ruling_cannot_move_a_teammates_things(fake):
    m, st, _, _ = table(fake)
    give = WorldChange(kind="transfer", key="gun", text="林秋把枪交给了周衡", item="左轮手枪", owner="zhouheng")
    assert not legacy_world.admissible(m, st, give, source="arbiter")  # only her own action hands it over
    legacy_world.apply(m, st, [give], source="arbiter")
    assert st.find("lin").inventory == ["左轮手枪"]


def lan_screen(seat: str = "zhouheng"):
    from autokeeper.tui.screens.net import NetGameScreen

    sent: list[dict] = []
    notes: list[str] = []
    st = SimpleNamespace(investigators=[SimpleNamespace(id="zhouheng", name="周衡"), SimpleNamespace(id="lin", name="林秋")],
                         ending=None)
    screen = SimpleNamespace(seat=seat, state=st, link=SimpleNamespace(send=sent.append), run_worker=lambda work: work(),
                             app=SimpleNamespace(notify=lambda text, **_: notes.append(text)), pool_open=True,
                             my_turn=True, held=[], pooled=[], pool={"round": "test-round"})
    screen._round_packet = MethodType(NetGameScreen._round_packet, screen)
    return screen, sent, notes


@pytest.mark.parametrize("text, msg", [
    ("（压低声音）别进那扇门。", {"t": "act", "text": "（压低声音）别进那扇门。"}),
    ("约翰，你怎么看？", {"t": "act", "text": "约翰，你怎么看？"}),
    ("//我去倒杯水", {"t": "say", "text": "我去倒杯水"}),
    ("（（我去倒杯水））", {"t": "say", "text": "我去倒杯水"}),
    ("((brb))", {"t": "say", "text": "brb"}),
    ("“把枪给我”", {"t": "speak", "text": "“把枪给我”"}),
    ("对林秋说：快走", {"t": "speak", "text": "对林秋说：快走"}),
])  # fmt: skip
def test_the_lan_box_sends_actions_speech_and_chat_apart(text, msg):
    from autokeeper.tui.screens.net import NetGameScreen

    screen, sent, _ = lan_screen()
    NetGameScreen.send_line(screen, text)
    expected = msg | ({"round": "test-round"} if msg["t"] == "act" else {})
    assert sent == [expected]


def test_a_spectator_may_chat_out_of_character_but_not_speak():
    from autokeeper.tui.screens.net import NetGameScreen

    screen, sent, notes = lan_screen(seat="")
    NetGameScreen.send_line(screen, "//加油")
    NetGameScreen.send_line(screen, "“我也来”")
    assert sent == [{"t": "say", "text": "加油"}] and notes == ["观战模式下可用 // 开头发送场外消息。"]


async def test_at_a_lan_table_words_to_the_party_take_no_slot_and_no_keeper(monkeypatch, tmp_path, fake):
    import aiohttp

    from autokeeper import paths
    from autokeeper.net.hub import Hub

    from .test_net import TableCast, closed, opened, pool_open, until

    class Counted(TableCast):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def intent(self, state, text):
            self.calls += 1
            return await super().intent(state, text)

        async def keeper(self, state, report, on_delta=None, lore=""):
            self.calls += 1
            self.reports = [*getattr(self, "reports", []), report]
            return await super().keeper(state, report, on_delta, lore)

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(m, [lead, friend], slot="lan-chat", human=[lead.id, "lin"])
    state.add("keeper", "开场。", who="守秘人")  # not a fresh game: no opening told
    cast = Counted()
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    task = await opened(hub)
    url = f"ws://127.0.0.1:{hub.port}/ws"
    async with aiohttp.ClientSession() as http:
        a, b, c = [await http.ws_connect(url) for _ in range(3)]
        for ws, name in ((a, "小明"), (b, "小红"), (c, "路人")):
            await ws.send_json({"t": "hello", "name": name})
            await until(ws, lambda x: x["t"] == "welcome")
        await a.send_json({"t": "sit", "investigator": "zhouheng"})
        await b.send_json({"t": "sit", "investigator": "lin"})
        await until(a, pool_open)
        calls = cast.calls

        await a.send_json({"t": "speak", "text": "（压低声音）“把枪给我。”"})
        heard = await until(b, lambda x: x["t"] == "event" and x["ev"]["type"] == "speech")
        speech = dict(heard["ev"])
        assert speech.pop("delivery").startswith("table:")
        assert speech.pop("table_present") == ["zhouheng", "lin"]
        assert speech == {"type": "speech", "who": "周衡", "player": "小明", "to": "", "text": "（压低声音）“把枪给我。”"}
        for _ in range(50):
            if any(e.kind == "speech" for e in hub.state.log):
                break
            await asyncio.sleep(0.02)
        said = next(e for e in hub.state.log if e.kind == "speech")
        assert said.who == "周衡" and said.text == f"在{m.location(state.location).name}对同伴说：（压低声音）“把枪给我。”"
        assert hub.pool == [] and hub.early == [] and cast.calls == calls  # no slot, no keeper
        await a.send_json({"t": "speak", "text": "对林秋说：你先走"})
        assert (await until(b, lambda x: x["t"] == "event" and x["ev"]["type"] == "speech"))["ev"]["to"] == "林秋"

        await c.send_json({"t": "say", "text": "加油"})  # a spectator chats out of character: shown, never recorded
        assert (await until(a, lambda x: x["t"] == "say"))["text"] == "加油"
        await c.send_json({"t": "speak", "text": "“我也来”"})
        assert "观战" in (await until(c, lambda x: x["t"] == "error"))["text"]
        assert not any("加油" in e.text or "我也来" in e.text for e in hub.state.log)

        await a.send_json({"t": "act", "text": "我看看告示栏"})  # plain words: an action, as before
        await until(b, lambda x: x["t"] == "pool" and x["count"] == 1)
        await a.send_json({"t": "done"})
        await b.send_json({"t": "done"})
        await until(a, lambda x: x["t"] == "event" and x["ev"]["type"] == "said" and x["ev"]["text"] == "我看看告示栏")
        await until(a, lambda x: x["t"] == "pool" and not x["open"])
        await until(a, pool_open)
        assert any("周衡在" in r and "把枪给我" in r for r in cast.reports)  # the keeper heard it with the round
        for ws in (a, b, c):
            await ws.close()
    await closed(task)


async def test_alone_with_ai_companions_the_same_marks_mean_the_same(canned, fake):  # noqa: F811
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.game import GameScreen

    from .test_tui import until as settled

    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋", "inventory": ["左轮手枪"]}, deep=True)
    state = Game.new(m, [lead, friend], slot="chat", human=[lead.id])
    state.location = "tower"
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await settled(pilot, lambda: screen.game is not None and not screen.busy)
        played: list[str] = []
        screen.play = played.append  # what would go to the keeper
        turn = state.turn

        screen.send_line("“把枪给我”")
        await pilot.pause()
        assert not played and state.turn == turn and state.find("lin").inventory == ["左轮手枪"]
        assert state.log[-1].kind == "speech" and state.log[-1].text == "在钟楼对同伴说：“把枪给我”"
        assert "▷ 周衡：“把枪给我”" in story_text(screen)
        assert "把枪给我" in situation(m, state, "lin", screen.game)  # the companion reads it on its turn

        screen.send_line("//我去倒杯水")  # nobody to chat with: shown, never recorded
        assert "【场外】我去倒杯水" in story_text(screen) and "倒杯水" not in "".join(e.text for e in state.log)

        screen.busy = True  # the keeper is telling: shown now, recorded when it is done
        screen.send_line("对林秋说：跟紧我")
        assert "▷ 周衡 → 林秋：跟紧我" in story_text(screen) and state.log[-1].text.endswith("“把枪给我”")
        screen._busy(False)
        assert state.log[-1].text == "在钟楼对林秋说：跟紧我"

        screen.send_line("（压低声音）别进那扇门。")
        screen.send_line("约翰，你怎么看？")
        assert played == ["（压低声音）别进那扇门。", "约翰，你怎么看？"]  # in character, for the keeper
