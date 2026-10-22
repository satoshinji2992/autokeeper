"""Personal views still pass through the final network disclosure and seating contract."""

from __future__ import annotations

import copy
import json

import pytest
from PIL import Image

from autokeeper.engine.encounter import choices, views
from autokeeper.engine.encounter.model import ChaseSite, ChaseState, EncounterState, Participant
from autokeeper.engine.encounter.plan import SiteEdge, SiteNode
from autokeeper.module.schema import Creature
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.tui.screens.lobby import LobbyScreen

from .encounter_kit import begin
from .test_personal_perception import disguise
from .test_personal_perception import table as _table

table = _table


@pytest.fixture
def wire(table):
    game = table
    hub = Hub(EXAMPLE, state=game.state, cast=game.cast, rules=game.rules)
    hub.m, hub.game = game.m, game
    received = {key: [] for key in ("a", "b", "watch")}

    class Socket:
        closed = False

        def __init__(self, key):
            self.key = key

        async def send_str(self, text):
            received[self.key].append(json.loads(text))

    hub.clients = {key: Socket(key) for key in received}
    hub.sits = {"zhouheng": "a", "lin": "b"}
    hub.names = {"a": "甲", "b": "乙", "watch": "旁观者"}
    return hub, received


async def test_personal_live_fight_keeps_private_stats_and_portrait_ids_off_the_wire(wire, tmp_path):
    hub, received = wire
    game = hub.game
    game.m.creatures.append(Creature(id="secret-ghoul", name="食尸鬼真身", called="佝偻黑影", description="幕后资料",
                                     sanity="0/0", stats={"生命": 20, "敏捷": 73}))
    image = tmp_path / "face.png"
    Image.new("RGB", (8, 8), "red").save(image)
    game.m.pictures["creature-secret-ghoul"] = str(image)
    game.world.sync()
    game.rules.dice.queue(10, 10)
    await begin(game, ["secret-ghoul"])
    disguise(game)
    try:
        await hub._event({"type": "fight_state", "fight": views.fight_view(game), "lines": []})
        for cid in received:
            event = next(msg["ev"] for msg in received[cid] if msg.get("ev", {}).get("type") == "fight_state")
            foe = next(row for row in event["fight"]["fighters"] if row["name"] == "佝偻黑影")
            art = next(msg["ev"] for msg in received[cid] if msg.get("ev", {}).get("type") == "art")
            assert "dex" not in foe and "look" not in foe
            assert foe["portrait"] == art["key"] and foe["portrait"].startswith("face-")
            assert "secret-ghoul" not in json.dumps(received[cid], ensure_ascii=False)
        mine = next(msg["ev"]["fight"] for msg in received["a"] if msg.get("ev", {}).get("type") == "fight_state")
        imagined = next(row for row in mine["fighters"] if row["name"] == "渗血的怪物")
        assert imagined["id"].startswith("seen-") and imagined["side"] == "foe"
        assert imagined["portrait"] == "monster_face" and imagined["hp"] is None
        await hub._send("a", {"t": "sync", "state": hub._view("zhouheng")})
        battle = received["a"][-1]["state"]["encounter_public"]
        option = next(row for row in battle["choices"]["zhouheng"] if row[0].startswith("攻击 渗血的怪物"))
        assert choices.match(game, "zhouheng", option[2]).attacks[0].target == "lin"
    finally:
        await game.close()


async def test_personal_live_chase_does_not_restore_unseen_route_or_internal_nodes(wire):
    hub, received = wire
    game = hub.game
    nodes = [SiteNode(id=f"node{i}", name=f"路线{i}", look=f"远处的场景{i}", exit=i == 5) for i in range(6)]
    site = ChaseSite(nodes=nodes, edges=[SiteEdge(a=f"node{i}", b=f"node{i + 1}") for i in range(5)],
                     origin="node0", source="keeper")
    game.state.encounter = EncounterState(
        id="chase", mode="chase", place="tower", started_turn=0,
        participants=[Participant(id="zhouheng", kind="investigator", side="party", controller="human", node="node0"),
                      Participant(id="lin", kind="investigator", side="party", controller="human", node="node0"),
                      Participant(id="warden", kind="npc", side="foe", controller="keeper", node="node0")],
        chase=ChaseState(site=site, quarry=["zhouheng", "lin"], pursuers=["warden"]))
    disguise(game)
    try:
        await hub._event({"type": "chase_state", "chase": views.chase_view(game), "lines": []})
        for cid in received:
            event = next(msg["ev"] for msg in received[cid] if msg.get("ev", {}).get("type") == "chase_state")
            chase = event["chase"]
            assert chase["legs"][-1] == {"name": "", "look": "", "obstacle": None, "exit": False}
            assert all("id" not in leg for leg in chase["legs"])
            assert all("node" not in runner for runner in chase["runners"])
        mine = next(msg["ev"]["chase"] for msg in received["a"] if msg.get("ev", {}).get("type") == "chase_state")
        assert any(row["name"] == "渗血的怪物" and row["side"] == "foe" for row in mine["runners"])
        assert "渗血的怪物" not in json.dumps(received["b"], ensure_ascii=False)
    finally:
        await game.close()


async def test_a_rejoining_seat_receives_its_personal_snapshot_and_window_before_entering(wire, monkeypatch):
    hub, received = wire
    game = hub.game
    await begin(game, ["warden"])
    disguise(game)
    hub.sits["zhouheng"] = None
    hub.started.set()
    hub.open = True
    hub.pool = [("lin", "守住门口")]
    hub.early = [("lin", "另一人的下一轮私密声明")]
    public = copy.deepcopy(hub._welcome("a"))
    assert any(row["name"] == "林秋" for row in public["state"]["investigators"])
    assert public["state"]["encounter_public"]["choices"] == {}
    lobby = LobbyScreen("localhost", "甲")
    entered = []
    sent = []

    async def send(msg):
        sent.append(msg)

    monkeypatch.setattr(lobby.link, "send", send)
    monkeypatch.setattr(lobby, "_show_all", lambda: None)
    monkeypatch.setattr(lobby, "_buttons", lambda: None)
    monkeypatch.setattr(lobby, "_highlighted", lambda: {"investigator": "zhouheng", "human": True, "online": False})
    monkeypatch.setattr(lobby, "_enter", lambda first: entered.append(first))
    try:
        await lobby.on_msg(public)
        await lobby.action_sit()
        assert sent == [{"t": "sit", "investigator": "zhouheng"}]
        assert not entered and lobby.chosen is None
        await lobby.on_msg({"t": "event", "ev": {"type": "status", "text": ""}})
        assert not entered  # ordinary traffic cannot finish the seating handshake
        await hub._sit("a", "zhouheng")
        seated = next(msg for msg in received["a"] if msg["t"] == "seated")
        await lobby.on_msg(seated)
        assert lobby.chosen == "zhouheng" and lobby.pending_seat is None and len(entered) == 1
        assert [inv.id for inv in lobby.state.investigators] == ["zhouheng"]
        assert not any(inv.id == "lin" for inv in lobby.m.pregens)
        assert lobby.state.encounter_public["choices"]["zhouheng"]
        assert entered[0] == [seated["pool"]] and "zhouheng" in seated["pool"]["open_to"]
        assert seated["pool"]["mine"] == {"now": [], "next": []}
        assert "另一人的下一轮私密声明" not in json.dumps(seated, ensure_ascii=False)
    finally:
        await game.close()


async def test_a_pending_seat_change_cannot_enter_on_an_unrelated_turn(wire, monkeypatch):
    hub, _ = wire
    game = hub.game
    lobby = LobbyScreen("localhost", "甲")
    entered = []
    monkeypatch.setattr(lobby, "_show_all", lambda: None)
    monkeypatch.setattr(lobby, "_enter", lambda first: entered.append(first))
    try:
        await lobby.on_msg(hub._welcome("a"))
        lobby.chosen, lobby.pending_seat = "lin", "zhouheng"
        await lobby.on_msg({"t": "turn", "state": hub._view("zhouheng")})
        await lobby.on_msg({"t": "event", "ev": {"type": "status", "text": ""}})
        assert not entered and lobby.pending_seat == "zhouheng"
    finally:
        await game.close()


async def test_a_new_game_waits_in_the_lobby_after_its_seat_confirmation(wire, monkeypatch):
    hub, received = wire
    game = hub.game
    hub.fresh = True
    hub.sits = {"zhouheng": None, "lin": None}
    lobby = LobbyScreen("localhost", "甲")
    entered = []
    refreshed = []
    monkeypatch.setattr(lobby, "_show_all", lambda: refreshed.append(True))
    monkeypatch.setattr(lobby, "_enter", lambda first: entered.append(first))
    try:
        await hub._sit("a", "zhouheng")
        seated = next(msg for msg in received["a"] if msg["t"] == "seated")
        await lobby.on_msg(seated)
        assert lobby.chosen == "zhouheng" and lobby.bring and refreshed
        assert not lobby.started and not entered
        await lobby.on_msg({"t": "begin"})
        assert entered == [[{"t": "begin"}]]
    finally:
        await game.close()
