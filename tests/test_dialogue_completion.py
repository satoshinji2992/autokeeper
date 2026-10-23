"""An NPC's answer finishes a conversation, not the player's other work in that turn."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from autokeeper.agents.cast import Action, Initiative, KeeperCall, Narration, Ruling
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules


class FakeCast:
    def __init__(self) -> None:
        self.told: dict[str, list[str]] = {}
        self.unsettled: dict[str, list[str]] = {}
        self.npc_calls: list[str] = []
        self.reports: list[str] = []
        self.voice_script: list[KeeperCall] = []

    async def npc(self, state: GameState, npc_id: str, message: str) -> str:
        self.npc_calls.append(npc_id)
        return "您先在这儿坐，茶水在桌上。"

    async def keeper(self, state: GameState, report: str, on_delta=None, lore: str = "", *,
                     check: Callable[[Any], bool] | None = None, fix: str = "", voices: list[str] | None = None) -> Narration:
        self.reports.append(report)
        if voices is not None and self.voice_script:
            call = self.voice_script.pop(0)
            assert call.npc in voices
            return Narration(text="", call=call)
        scene = {"things": [{"name": "整理好的采访笔记", "holder": state.investigator.id}],
                 "now": "采访笔记已整理妥当，调查员坐在窗旁。"}
        assert check is not None and check(scene)
        return Narration(text="你在窗旁坐定，把采访笔记整理妥当，折好收进衣袋。", scene=scene)


def made(fake) -> tuple[Game, FakeCast, list[dict]]:
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    state.turn = 1
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    cast = FakeCast()
    game = Game(module, state, cast, Rules(dice=fake), emit=emit)  # type: ignore[arg-type]
    return game, cast, events


@pytest.mark.parametrize("kind,casual", [("talk", False), ("daily", True)])
async def test_a_plain_conversation_finishes_with_the_npcs_own_answer(fake, kind, casual):
    game, cast, events = made(fake)
    action = Action(kind=kind, summary="周衡向守钟人道谢", npc="warden", speech="谢谢您。", casual=casual)
    game.state.add("player", "谢谢您。", who=game.state.investigator.name)
    turn = Turn(action=action, ruling=Ruling(needs_roll=False))
    await game.talk(turn)
    assert await game.narrate(turn) == ""
    assert cast.npc_calls == ["warden"] and not cast.reports
    assert [event["type"] for event in events].count("dialogue_end") == 1
    assert "整理好的采访笔记" not in game.state.investigator.inventory
    await game.close()


@pytest.mark.parametrize("kind,summary,speech,minutes", [
    ("daily", "周衡请守钟人借张椅子，随后在窗边等半小时并整理采访笔记", "能借张椅子吗？", 30),
    ("daily", "周衡付过茶钱，向守钟人道谢，随后坐到窗边整理采访笔记", "茶钱放这儿，谢谢。", 5),
    ("talk", "周衡告诉守钟人自己先休息片刻，随后在窗边整理采访笔记", "我先歇会儿。", 30),
])
async def test_other_work_or_rest_continues_after_the_npcs_answer(fake, kind, summary, speech, minutes):
    game, cast, events = made(fake)
    action = Action(kind=kind, summary=summary, npc="warden", speech=speech, casual=False)
    game.state.add("player", summary, who=game.state.investigator.name)
    game.state.minutes += minutes
    turn = Turn(action=action, ruling=Ruling(needs_roll=False, minutes=minutes, rest=True))
    await game.talk(turn)
    text = await game.narrate(turn)
    assert cast.npc_calls == ["warden"] and len(cast.reports) == 1
    assert "整理妥当" in text and "整理好的采访笔记" in game.state.investigator.inventory
    assert game.state.ongoing == "采访笔记已整理妥当，调查员坐在窗旁。"
    assert not any(event["type"] == "dialogue_end" for event in events)
    assert [entry.text for entry in game.state.log if entry.kind == "npc"] == ["您先在这儿坐，茶水在桌上。"]
    await game.close()


async def test_an_npcs_initiative_does_not_replace_the_players_action(fake):
    game, cast, events = made(fake)
    game.initiative = [Initiative(npc="warden", does="告诉周衡茶水放在桌上")]
    game.state.pending_turn = {"input": "我整理一下采访笔记。"}
    cast.voice_script.append(KeeperCall(npc="warden", instruction="对方正在整理笔记，告诉他茶水放在桌上。"))
    action = Action(kind="investigate", summary="周衡整理采访笔记", target="采访笔记")
    game.state.add("player", "我整理一下采访笔记。", who=game.state.investigator.name)
    turn = Turn(action=action, ruling=Ruling(needs_roll=False))
    await game.talk(turn)
    assert not turn.joined and not cast.npc_calls
    await game.narrate(turn)
    assert turn.joined == ["warden"] and cast.npc_calls == ["warden"] and len(cast.reports) == 2
    assert "整理好的采访笔记" in game.state.investigator.inventory
    assert not any(event["type"] == "dialogue_end" for event in events)
    await game.close()
