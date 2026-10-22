"""The keeper/director owns reinterpretation; the world preserves what actually reached play."""

from __future__ import annotations

import pytest

from autokeeper import paths
from autokeeper.agents.director import Improv
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules
from autokeeper.world import ops as O
from autokeeper.world.store import World

from .test_engine import FakeCast

SECRET = "npc:warden:secrets:0"


class Director(FakeCast):
    def __init__(self, plan):
        super().__init__({})
        self.plan = plan
        self.calls = 0

    async def director(self, state, kind, **kwargs):
        self.calls += 1
        return self.plan

    async def consistent(self, *args):
        raise AssertionError("a separate rewrite approval must never be requested")


def game_with(fake, plan=None):
    module = Module.load(paths.EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    return Game(module, state, Director(plan), Rules(dice=fake))


async def test_scene_preparation_accepts_the_directors_reinterpretation_without_an_approval_call(fake):
    change = O.Rewrite(target=f"fact:{SECRET}", text="布道者藏了那件东西", reason="布道者把自己的行为栽给守钟人")
    package = Improv(kind="展开", reason="纠正证词背后的真相", backstage=[change])
    game = game_with(fake, package)
    game.state.add("npc", f"我见过，{game.world.rec.facts[SECRET].text}。", who="布道者")
    game.world.sync()

    accepted = await game._plan("展开", action=None, ruling=None, at=game.state.location)

    assert accepted is package and game.cast.calls == 1
    assert game.world.rec.facts[SECRET].text == "他在钟里藏了东西"  # preparation is not a commit
    await game.close()


async def test_direct_backstage_reinterpretation_preserves_the_old_account_and_survives_reload(fake):
    game = game_with(fake)
    old = game.world.rec.facts[SECRET].text
    entry = game.state.add("npc", f"布道者说：{old}。", who="布道者")
    game.world.sync()
    places = {entity.id: entity.at for entity in game.world.rec.entities.values()}
    owners = dict(game.state.item_owners)
    change = O.Rewrite(target=f"fact:{SECRET}", text="布道者藏了那件东西", reason="布道者栽赃守钟人")

    assert await game._backstage([change], "这次证词是栽赃") == []
    assert entry.text == f"布道者说：{old}。"
    told = game.world.rec.facts[f"told:{SECRET}:1"]
    assert told.text == old and told.revealed is not None
    assert game.world.rec.facts[SECRET].text == change.text
    assert game.world.rec.facts[SECRET].revealed is None
    assert {entity.id: entity.at for entity in game.world.rec.entities.values()} == places
    assert game.state.item_owners == owners
    revision = game.world.rec.revisions[-1]
    assert revision.old == old and revision.new == change.text and revision.reason == change.reason
    restored = World(game.m.model_copy(deep=True), game.state.model_copy(deep=True))
    assert restored.rec.facts[f"told:{SECRET}:1"].text == old
    assert restored.rec.revisions[-1].reason == change.reason
    await game.close()


@pytest.mark.parametrize("change", [
    O.Rewrite(target=f"fact:{SECRET}", text="他没藏过东西"),  # witnessed wording needs an explanation
    O.Rewrite(target="fact:clue:c_diary", text="一张空白纸", reason="假日记"),  # material keeps its words
])
async def test_removing_model_approval_does_not_remove_observation_or_material_guards(fake, change):
    game = game_with(fake)
    game.state.add("npc", game.world.rec.facts[SECRET].text, who="布道者")
    game.state.clues.append("c_diary")
    game.world.sync()
    before = game.state.model_dump_json()

    assert await game._backstage([change], "改写")
    assert game.state.model_dump_json() == before
    await game.close()


async def test_reinterpreting_a_public_event_cannot_erase_the_event_or_move_its_participants(fake):
    game = game_with(fake)
    result = game.world.apply(O.Occur(text="钟停了一下。", place="square", participants=[game.state.investigator.id]))
    event = game.world.rec.event(result.event)
    assert event is not None
    invalid = O.Rewrite(target=f"event:{event.id}", retract=True, reason="其实没人看见")
    assert await game._backstage([invalid], "取消目击")

    change = O.Rewrite(target=f"event:{event.id}", text="报童在广场外牵动了提前布好的细线。", reason="停钟是人为布置")
    assert await game._backstage([change], "另有真相") == []
    assert game.world.rec.event(event.id).text == "钟停了一下。"
    assert game.state.location == "square"
    assert game.world.rec.facts[f"behind:{event.id}:1"].text == change.text
    await game.close()
