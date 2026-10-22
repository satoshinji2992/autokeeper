"""Ordinary checks keep their original scene through engine receipts and LAN projection."""

from __future__ import annotations

import copy

import pytest
from textual.widgets import Button, Switch

from autokeeper import config
from autokeeper.agents.cast import Action, Failure, PushRuling, Ruling
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.rules.commands import Asked
from autokeeper.tui.app import AutoKeeperApp

from .test_net import TableCast

ACTION = Action(kind="investigate", summary="独自在广场查看蓝色暗扣", target="蓝色暗扣")
RULING = Ruling(needs_roll=True, skill="侦查", minutes=1,
                on_push_fail=Failure(risk="暗扣会折断，夹伤手指"))


@pytest.fixture
def check_hub(fake, monkeypatch):
    def make(*, saved: GameState | None = None, public: bool = False):
        if saved is None:
            module = Module.load(EXAMPLE)
            lead = module.pregens[0].model_copy(deep=True)
            friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
            saved = Game.new(module, [lead, friend], slot="check-scope", human=[lead.id, friend.id])
            saved.apart[friend.id] = "tower"
            saved.turn = 1
        cast = TableCast()
        cast.cfg = config.Config(public_rolls=public)
        hub = Hub(EXAMPLE, state=saved, cast=cast, rules=Rules(dice=fake))
        hub.m.events, hub.m.endings, hub.m.foreshadow = [], [], []
        hub.clients = dict.fromkeys(("a", "b", "watch"))
        hub.names = {"a": "甲", "b": "乙", "watch": "旁观"}
        hub.sits = {saved.investigators[0].id: "a", "lin": "b"}
        messages = []

        async def send(cid, msg):
            messages.append((cid, copy.deepcopy(msg)))

        monkeypatch.setattr(hub, "_send", send)
        monkeypatch.setattr(hub, "_save", lambda: None)
        return hub, messages

    return make


def events(messages, cid, *kinds):
    return [msg["ev"] for client, msg in messages
            if client == cid and msg.get("t") == "event" and msg["ev"]["type"] in kinds]


@pytest.mark.parametrize("public", [False, True])
async def test_failed_check_projects_offer_and_full_record_only_to_original_scene(check_hub, fake, public):
    hub, messages = check_hub(public=public)
    first, second = hub.state.investigators
    fake.queue(88)
    try:
        turn = await hub.game.resolve(ACTION, RULING)
        assert turn.push and not turn.checks[-1].passed
        assert hub.game.world.place(first.id) != hub.game.world.place(second.id)
        assert hub.state.pending_push["audience"] == [first.id]
        assert hub.state.pending_push["roll_audience"] == (None if public else [first.id])
        assert hub._view(first.id)["pending_push"]["action"] == ACTION.summary
        await hub._broadcast({"t": "sync", "state": hub._view()})
        b_sync = [msg for cid, msg in messages if cid == "b" and msg["t"] == "sync"][-1]["state"]
        for view in (hub._view(second.id), hub._welcome("b")["state"], b_sync, hub._welcome("watch")["state"]):
            assert view["pending_push"] is None and not view["checks"]
        assert hub._view(first.id)["checks"][-1]["action"] == ACTION.summary
        assert bool(events(messages, "b", "dice")) is public
        assert not events(messages, "b", "outcome")
        for cid in ("b", "watch"):
            welcome = hub._welcome(cid)
            assert bool([e for e in welcome["log"] if e["kind"] == "dice"]) is public
            assert bool([d for d in welcome["state"]["deeds"] if d["kind"] == "check"]) is public
        assert not fake.values
    finally:
        await hub.game.close()


async def test_scene_observers_receive_the_check_but_an_unseated_connection_does_not(check_hub, fake):
    hub, messages = check_hub()
    hub.state.apart.clear()
    fake.queue(88)
    try:
        await hub.game.resolve(ACTION, RULING)
        assert set(hub.state.pending_push["audience"]) == {i.id for i in hub.state.investigators}
        assert events(messages, "b", "dice") and not events(messages, "watch", "dice")
        assert hub._view("lin")["pending_push"]["risk"] == RULING.on_push_fail.risk
        assert hub._view("lin")["checks"] and hub._view()["pending_push"] is None
    finally:
        await hub.game.close()


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("choice", ["luck", "push", "drop", "refused"])
async def test_saved_offer_keeps_scope_after_reunion_and_setting_change(check_hub, fake, public, choice, monkeypatch):
    hub, _ = check_hub(public=public)
    fake.queue(88)
    await hub.game.resolve(ACTION, RULING)
    saved = GameState.model_validate_json(hub.state.model_dump_json())
    await hub.game.close()
    # The second investigator arrives after the first roll. Reloading with another
    # preference must not expand its stored delivery scope, even for the new push.
    saved.apart.clear()
    loaded, messages = check_hub(saved=saved, public=not public)
    if choice == "refused":
        async def refuse(*args):
            return PushRuling(achievable=False, minutes=0, why="这样摸不到暗扣")

        monkeypatch.setattr(loaded.cast, "repush", refuse)
    if choice == "push":
        fake.queue(12)
    try:
        assert loaded.game.world.place(saved.investigators[0].id) == loaded.game.world.place("lin")
        assert loaded._view("lin")["pending_push"] is None
        resumed = await loaded.game.push(
            {"choice": "push", "how": "垫住柜门再伸手摸暗扣"} if choice in ("push", "refused") else choice)
        assert saved.pending_push is None
        assert saved.checks[-1].passed is (choice in ("luck", "push"))
        assert all(e.audience == (None if public else [saved.investigators[0].id])
                   for e in saved.log if e.kind == "dice")
        assert bool(events(messages, "b", "dice", "luck")) is (public and choice in ("luck", "push"))
        assert not events(messages, "b", "outcome")
        welcome = loaded._welcome("b")
        assert not welcome["state"]["checks"]
        assert bool([e for e in welcome["log"] if e["kind"] == "dice"]) is public
        assert not any("垫住柜门" in e["text"] for e in welcome["log"])
        assert not resumed.push and not fake.values
    finally:
        await loaded.game.close()


async def test_delivery_retry_uses_saved_observers_and_does_not_roll_again(check_hub, fake):
    hub, _ = check_hub()
    hub.state.pending_turn = {"input": "检查暗扣", "active": 0}
    fake.queue(88)

    async def fail_on_dice(event):
        if event["type"] == "dice":
            raise ConnectionError("offline delivery interrupted")

    hub.game.emit = fail_on_dice
    with pytest.raises(ConnectionError, match="delivery interrupted"):
        await hub.game.resolve(ACTION, RULING)
    saved = GameState.model_validate_json(hub.state.model_dump_json())
    await hub.game.close()
    saved.apart.clear()
    loaded, messages = check_hub(saved=saved, public=True)
    try:
        await loaded.game.resolve(ACTION, RULING)
        assert not fake.values
        assert len([e for e in saved.log if e.kind == "dice"]) == 1
        assert saved.pending_push["audience"] == [saved.investigators[0].id]
        assert not events(messages, "b", "dice", "outcome")
        assert not loaded._welcome("b")["log"] and not loaded._view("lin")["checks"]
    finally:
        await loaded.game.close()


@pytest.mark.parametrize("public", [False, True])
async def test_repeat_keeps_original_dice_scope_after_reunion(check_hub, fake, public):
    hub, messages = check_hub(public=public)
    fake.queue(88)
    try:
        await hub.game.resolve(ACTION, RULING)
        await hub.game.push("drop")
        hub.state.apart.clear()
        hub.state.turn += 1
        hub.game.public_rolls = not public
        messages.clear()
        await hub.game.resolve(ACTION, Ruling(needs_roll=False, repeat=True, skill="侦查"))
        assert bool(events(messages, "b", "repeat")) is public
        assert not hub._view("lin")["checks"] and not fake.values
    finally:
        await hub.game.close()


@pytest.mark.parametrize("public", [False, True])
async def test_waived_requested_check_obeys_selected_dice_scope(check_hub, public):
    hub, messages = check_hub(public=public)
    try:
        await hub.game.resolve(ACTION.model_copy(update={"asked": Asked(skill="侦查")}), Ruling(needs_roll=False))
        assert len(events(messages, "a", "waived")) == 1
        assert bool(events(messages, "b", "waived")) is public
    finally:
        await hub.game.close()


async def test_instant_rolls_and_growth_stay_public_while_apart(check_hub, fake):
    hub, messages = check_hub()
    try:
        fake.queue_totals(4)
        assert (await hub.game.command(".r 1d6")).kind == "roll"
        hub.state.ending = "stopped"
        hub.state.investigator.marks = ["侦查"]
        fake.queue(80).queue_totals(3)
        assert (await hub.game.command(".en")).kind == "develop"
        assert len(events(messages, "b", "command")) == 2
        assert len([e for e in hub._welcome("b")["log"] if e["kind"] == "dice"]) == 2
        assert all(e.audience is None for e in hub.state.log if e.kind == "dice")
    finally:
        await hub.game.close()


async def test_table_public_dice_preference_survives_settings_save_and_reload():
    assert not config.load().public_rolls
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press("f9")
        await pilot.pause()
        app.screen.query_one("#settings-public_rolls", Switch).value = True
        app.screen.query_one("#settings-save", Button).press()
        await pilot.pause()
    assert config.load().public_rolls
    config.save_ui_settings({"public_rolls": False})
    assert not config.load().public_rolls
