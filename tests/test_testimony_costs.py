"""Each actual reader settles authored information costs, independently of delivery and knowledge."""

from __future__ import annotations

import pytest

from autokeeper.engine import audience, presentation
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import knowledge

from .conftest import FakeDice
from .test_npc_replies import ASK, Teller


def table(fake, *, state=None, module=None):
    m = module or Module.load(EXAMPLE)
    m.events, m.endings, m.foreshadow, m.agendas, m.showcases = [], [], [], [], []
    clue = m.clue("c_diary")
    clue.sanity, clue.mythos = "1/1", 2
    if state is None:
        lead = m.pregens[0].model_copy(deep=True)
        party = [lead, lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True),
                 lead.model_copy(update={"id": "chen", "name": "陈明"}, deep=True)]
        state = Game.new(m, party)
        state.location, state.turn = "tower", 1
        state.npc("warden").unlocked = [clue.id]
    cast, events = Teller({"warden": [clue.id]}), []

    async def emit(event):
        events.append(event)

    return Game(m, state, cast, Rules(dice=fake), emit=emit), events


async def reply(game, turn, *, readers=None, key="testimony", keep=lambda _turn: None):
    return await game._reply(turn, key, "warden", lambda: game.cast.npc(game.state, "warden", ASK.speech),
                             keep, audience=readers)


@pytest.mark.parametrize("readers", [["zhouheng"], ["zhouheng", "lin"], None])
async def test_private_and_public_testimony_settles_each_actual_listener_only_once_without_the_document(readers):
    selected = readers or ["zhouheng", "lin", "chen"]
    fake = FakeDice().queue(*([20] * len(selected)))
    game, events = table(fake)
    before = {i.id: (i.san, i.value("克苏鲁神话")) for i in game.state.investigators}
    turn = Turn()
    try:
        said = await reply(game, turn, readers=readers)
        assert said.heard and said.told == ["c_diary"]
        for inv in game.state.investigators:
            touched = inv.id in selected
            assert (inv.san, inv.value("克苏鲁神话")) == (
                before[inv.id][0] - int(touched), before[inv.id][1] + 2 * int(touched))
            assert knowledge.clue_known(game.state, "c_diary", inv.id) is touched
            personal = [e for e in audience.log(game.state, inv.id) if e.kind == "sanity"]
            assert len(personal) == int(touched)
            assert all(e.who == inv.name for e in personal)
            assert not presentation.papers(game.state, inv.id)
        assert game.state.clue_settled == {"c_diary": selected}
        assert game.state.clues == ["c_diary"] and not game.state.handouts
        assert not game.state.presentations and not fake.values
        notices = [e for e in events if e["type"] == "sanity"]
        assert [e["audience"] for e in notices] == [[reader] for reader in selected]
        assert len(turn.sanity) == len(selected)
        await reply(game, turn, readers=readers)
        game.state.turn += 1
        await reply(game, Turn(), readers=readers, key="retold")
        assert len([e for e in events if e["type"] == "sanity"]) == len(selected)
        assert all(i.san == before[i.id][0] - int(i.id in selected) for i in game.state.investigators)
        assert len(game.cast.npc_calls) == 2  # same receipt did not ask; a new conversation can still occur
    finally:
        await game.close()


async def test_one_private_recipient_then_public_testimony_does_not_recharge_the_first_person():
    fake = FakeDice().queue(20, 20, 20)
    game, events = table(fake)
    before = {i.id: i.san for i in game.state.investigators}
    try:
        await reply(game, Turn(), readers=["zhouheng"], key="private")
        assert game.state.clue_settled["c_diary"] == ["zhouheng"]
        game.state.active, game.state.turn = 1, 2
        await reply(game, Turn(), key="public")
        assert game.state.clue_settled["c_diary"] == ["zhouheng", "lin", "chen"]
        assert all(i.san == before[i.id] - 1 and i.value("克苏鲁神话") == 2 for i in game.state.investigators)
        assert len([e for e in events if e["type"] == "sanity"]) == 3 and not fake.values
        assert not game.state.handouts and not game.state.presentations
        for n in range(50):
            game.state.add("keeper", f"其后经过{n}。")
        await game.remember()
        saved = GameState.model_validate_json(game.state.model_dump_json())
        again, _ = table(FakeDice(), state=saved, module=game.m.model_copy(deep=True))
        try:
            await again._gain(["c_diary"], Turn(), None)
            assert all(i.san == before[i.id] - 1 and i.value("克苏鲁神话") == 2 for i in again.state.investigators)
            assert not again.state.handouts
        finally:
            await again.close()
    finally:
        await game.close()


async def test_a_second_listeners_failed_dice_bridge_preserves_the_first_listeners_completed_cost():
    fake = FakeDice().queue(20)  # first settles; the second person's dice transport is interrupted
    game, _ = table(fake)
    before = {i.id: i.san for i in game.state.investigators}
    saved = {}
    turn = Turn()

    def keep(current):
        saved.update(state=game.state.model_dump_json(), turn=TURN_RECORD.dump_json(current))

    try:
        with pytest.raises(IndexError):
            await reply(game, turn, readers=["zhouheng", "lin"], keep=keep)
        assert game.state.clue_settled == {"c_diary": ["zhouheng"]}
        assert game.state.find("zhouheng").san == before["zhouheng"] - 1
        assert game.state.find("lin").san == before["lin"]
        restored = GameState.model_validate_json(saved["state"])
        resumed_fake = FakeDice().queue(20)
        resumed, shown = table(resumed_fake, state=restored, module=game.m.model_copy(deep=True))
        kept = TURN_RECORD.validate_json(saved["turn"])
        try:
            await reply(resumed, kept, readers=["zhouheng", "lin"])
            assert not resumed.cast.npc_calls and not resumed_fake.values
            assert resumed.state.clue_settled == {"c_diary": ["zhouheng", "lin"]}
            assert all(resumed.state.find(who).san == before[who] - 1 for who in ("zhouheng", "lin"))
            assert [e["audience"] for e in shown if e["type"] == "sanity"] == [["zhouheng"], ["lin"]]
        finally:
            await resumed.close()
    finally:
        await game.close()


async def test_reconnect_after_one_sanity_notice_replays_only_the_other_without_rolling_again():
    game, events = table(FakeDice().queue(20, 20))
    turn, saved = Turn(), {}
    send = game.emit

    def keep(current):
        saved.update(state=game.state.model_dump_json(), turn=TURN_RECORD.dump_json(current))

    async def cut(event):
        if event["type"] == "sanity" and event["audience"] == ["lin"]:
            raise ConnectionError("第二人的理智通知尚未送达")
        await send(event)

    game.emit = cut
    try:
        with pytest.raises(ConnectionError):
            await reply(game, turn, readers=["zhouheng", "lin"], keep=keep)
        assert [e["audience"] for e in events if e["type"] == "sanity"] == [["zhouheng"]]
        restored = GameState.model_validate_json(saved["state"])
        resumed, shown = table(FakeDice(), state=restored, module=game.m.model_copy(deep=True))
        try:
            await reply(resumed, TURN_RECORD.validate_json(saved["turn"]), readers=["zhouheng", "lin"])
            assert [e["audience"] for e in shown if e["type"] == "sanity"] == [["lin"]]
            assert not resumed.cast.npc_calls and resumed.state.clue_settled["c_diary"] == ["zhouheng", "lin"]
        finally:
            await resumed.close()
    finally:
        await game.close()
