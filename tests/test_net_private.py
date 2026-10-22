"""A private line is delivered and replayed by seat, never hidden only by the UI."""

from __future__ import annotations

import copy
import json

import pytest

from autokeeper.engine.game import Game
from autokeeper.module.schema import Location, Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_net import TableCast


@pytest.fixture
def private_hub(fake, monkeypatch):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    party = [lead, lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True),
             lead.model_copy(update={"id": "chen", "name": "陈明"}, deep=True)]
    state = Game.new(m, party, slot="private-table", human=[i.id for i in party])
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    hub.clients = dict.fromkeys(("a", "b", "c", "watch"))
    hub.names = {"a": "小明", "b": "小红", "c": "小陈", "watch": "路人"}
    hub.sits = {lead.id: "a", "lin": "b", "chen": "c"}
    hub.started.set()
    messages: list[tuple[str, dict]] = []

    async def send(cid, msg):
        messages.append((cid, copy.deepcopy(msg)))

    monkeypatch.setattr(hub, "_send", send)
    monkeypatch.setattr(hub, "_save", lambda: None)
    return hub, messages


async def test_private_speech_is_routed_by_real_sender_seat(private_hub):
    hub, messages = private_hub
    await hub._on("a", {"t": "speak", "text": "钥匙藏在内袋。", "to": ["lin"], "from": "chen"})
    assert [cid for cid, _ in messages] == ["a", "b"]
    ev = messages[0][1]["ev"]
    assert ev["who"] == "周衡" and ev["audience"] == ["zhouheng", "lin"]
    assert ev["to"] == "林秋" and ev["channel"] == "whisper"
    assert hub.pool == [] and hub.early == []  # no action slot, rule or model call
    speech = next(e for e in hub.state.log if e.kind == "speech")
    assert speech.audience == ["zhouheng", "lin"] and "钥匙藏在内袋" in speech.text
    assert "钥匙藏在内袋" not in json.dumps(hub._view(), ensure_ascii=False)


async def test_paper_keeps_its_delivery_method_in_the_private_record(private_hub):
    hub, messages = private_hub
    await hub._on("a", {"t": "speak", "text": "午夜来找我。", "to": ["lin"], "channel": "note"})
    assert messages[0][1]["ev"]["channel"] == "note"
    entry = hub.state.log[-1]
    assert "递交纸条" in entry.text and entry.audience == ["zhouheng", "lin"]


@pytest.mark.parametrize("to", [None, "lin", [], ["warden"], ["missing"], [123], ["zhouheng"]])
async def test_private_receivers_must_be_other_investigators(private_hub, to):
    hub, messages = private_hub
    await hub._on("a", {"t": "speak", "text": "秘密。", "to": to})
    assert len(messages) == 1 and messages[0][0] == "a" and messages[0][1]["t"] == "error"
    assert not hub.state.log


async def test_a_spectator_cannot_invent_a_private_sender(private_hub):
    hub, messages = private_hub
    await hub._on("watch", {"t": "speak", "text": "冒名。", "to": ["lin"], "from": "zhouheng"})
    assert len(messages) == 1 and messages[0][0] == "watch" and "观战" in messages[0][1]["text"]
    assert not hub.state.log


async def test_whispers_cannot_reach_a_distant_investigator(private_hub):
    hub, messages = private_hub
    hub.state.apart["lin"] = "tower"
    hub.state.record.entities["lin"].at = "tower"
    await hub._on("a", {"t": "speak", "text": "隔空私语。", "to": ["lin"]})
    assert len(messages) == 1 and messages[0][1]["t"] == "error"
    assert not hub.state.log


async def test_ordinary_speech_reaches_only_present_investigators_when_apart(private_hub):
    hub, messages = private_hub
    hub.state.apart["chen"] = "tower"
    hub.state.record.entities["chen"].at = "tower"
    await hub._on("a", {"t": "speak", "text": "“先检查门锁。”"})
    assert [cid for cid, _ in messages] == ["a", "b"]
    assert set(hub.state.log[-1].audience) == {"zhouheng", "lin"}


async def test_addressing_someone_normally_is_not_a_whisper(private_hub):
    hub, messages = private_hub
    await hub._on("a", {"t": "speak", "text": "对林秋说：先检查门锁。"})
    assert [cid for cid, _ in messages] == ["a", "b", "c", "watch"]
    assert "channel" not in messages[0][1]["ev"]


async def test_table_chat_remains_out_of_character_and_unrecorded(private_hub):
    hub, messages = private_hub
    await hub._on("watch", {"t": "say", "text": "稍等一分钟。"})
    assert len(messages) == 4 and all(msg["t"] == "say" for _, msg in messages)
    assert not hub.state.log


async def test_lobby_speech_cannot_create_a_log_and_skip_the_opening(private_hub):
    hub, messages = private_hub
    hub.started.clear()
    await hub._on("a", {"t": "speak", "text": "“开门。”"})
    assert messages == [("a", {"t": "error", "text": "游戏尚未开始。"})]
    assert not hub.state.log


async def test_private_history_is_absent_until_the_character_sits_down(private_hub):
    hub, messages = private_hub
    hub.state.add("keeper", "广场上起风了。", who="守秘人")
    await hub._on("a", {"t": "speak", "text": "柜子里有枪。", "to": ["lin"]})
    assert "柜子里有枪" not in json.dumps(hub._welcome("watch"), ensure_ascii=False)
    assert "柜子里有枪" not in json.dumps(hub._welcome("c"), ensure_ascii=False)
    assert "柜子里有枪" in json.dumps(hub._welcome("b")["log"], ensure_ascii=False)
    messages.clear()
    hub.sits["lin"] = None
    await hub._sit("watch", "lin")
    replay = [msg for cid, msg in messages if cid == "watch" and msg["t"] == "history"]
    assert len(replay) == 1 and len(replay[0]["log"]) == 1
    assert "柜子里有枪" in replay[0]["log"][0]["text"]
    assert "广场上起风" not in json.dumps(replay, ensure_ascii=False)


async def test_sitting_in_a_character_with_no_private_history_sends_an_empty_replacement(private_hub):
    hub, messages = private_hub
    hub.state.add("keeper", "林秋的秘密。", who="守秘人", audience=["lin"])
    hub.sits["lin"] = None
    await hub._sit("watch", "lin")
    messages.clear()
    hub.sits["chen"] = None
    await hub._sit("watch", "chen")
    replay = [msg for cid, msg in messages if cid == "watch" and msg["t"] == "history"]
    assert replay == [{"t": "history", "seat": "chen", "log": []}]


async def test_keeper_aside_uses_the_same_private_network_route(private_hub):
    hub, messages = private_hub
    await hub._event({"type": "aside", "who": "守秘人", "text": "只有你听到了敲门声。", "audience": ["lin"]})
    assert [cid for cid, _ in messages] == ["b"]
    await hub._event({"type": "aside", "text": "后台安排。", "audience": []})
    assert len(messages) == 1


async def test_private_deeds_are_filtered_before_state_is_sent(private_hub):
    hub, messages = private_hub
    hub.state.add("handout", "只给林秋的密码。", who="守秘人", audience=["lin"])
    await hub._broadcast({"t": "sync", "state": hub._view()})
    by_client = {cid: json.dumps(msg, ensure_ascii=False) for cid, msg in messages}
    assert "只给林秋的密码" in by_client["b"]
    assert all("只给林秋的密码" not in by_client[cid] for cid in ("a", "c", "watch"))


def test_folded_private_history_never_appears_in_shared_state(private_hub):
    hub, _ = private_hub
    entry = hub.state.add("keeper", "你认出了那枚戒指。", who="守秘人", audience=["lin"])
    hub.state.private_history.append(entry)
    hub.state.log.clear()
    assert "你认出了那枚戒指" in json.dumps(hub._welcome("b")["log"], ensure_ascii=False)
    assert "你认出了那枚戒指" not in json.dumps(hub._welcome("c"), ensure_ascii=False)
    assert "private_history" not in hub._view("lin")


def test_a_private_aside_does_not_unlock_its_place_or_art_for_everyone(private_hub):
    hub, _ = private_hub
    hub.m.locations.append(Location(id="vault", name="黑池密室", read_aloud="石壁渗着水。", art="vault"))
    hub.m.art["vault"] = "PRIVATE VAULT ART"
    hub.state.add("keeper", "你梦见了黑池密室。", who="守秘人", audience=["lin"])
    welcome = hub._welcome("c")
    assert "vault" not in {loc["id"] for loc in welcome["module"]["locations"]}
    assert "vault" not in welcome["art"] and "vault" not in welcome["picture_data"]


async def test_speech_is_already_recorded_when_it_is_delivered(private_hub, monkeypatch):
    hub, _ = private_hub

    async def send(cid, msg):
        assert len(hub.state.log) == 1
        assert hub.state.log[-1].text.endswith("先把灯点上。")

    monkeypatch.setattr(hub, "_send", send)
    await hub._on("a", {"t": "speak", "text": "先把灯点上。", "to": ["lin"]})


async def test_later_movement_or_unconsciousness_does_not_drop_delivered_speech(private_hub):
    hub, _ = private_hub
    await hub._on("a", {"t": "speak", "text": "等我回来。", "to": ["lin"]})
    hub.state.apart["lin"] = "tower"
    hub.state.find("lin").conditions.append("昏迷")
    hub.early = [("zhouheng", "看门"), ("chen", "看窗")]
    hub.window = 0.0
    await hub._collect()  # the next boundary never tries to revalidate or record that line again
    speech = [e for e in hub.state.log if e.kind == "speech"]
    assert len(speech) == 1 and "在广场" in speech[0].text and "等我回来" in speech[0].text
