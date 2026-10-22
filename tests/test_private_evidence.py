"""Personal observations cannot become public map, fact, Idea or recap evidence."""

from __future__ import annotations

import pytest

from autokeeper import paths
from autokeeper.engine import fog, idea, progress
from autokeeper.engine.game import Game
from autokeeper.module.schema import Location, Module
from autokeeper.world import knowledge
from autokeeper.world.store import World


def table():
    module = Module.load(paths.EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    return module, state


@pytest.mark.parametrize("kind", ["keeper", "npc", "event", "handout", "opening"])
@pytest.mark.parametrize("recipients", [[], ["zhouheng"]])
def test_personal_place_names_never_unlock_the_public_map(kind, recipients):
    module, state = table()
    module.locations.append(Location(id="vault", name="黑池密室", read_aloud="石壁渗着水。", public=False))
    private = state.add(kind, "你独自梦见了黑池密室。", audience=recipients)

    assert "vault" not in fog.heard(module, state)
    assert "vault" not in fog.known(module, state)
    assert "黑池密室" not in "\n".join(fog.shown(module, state))
    assert private.text == "你独自梦见了黑池密室。" and private.audience == recipients

    state.add("keeper", "你们共同读到一张写着黑池密室的纸条。")
    assert fog.heard(module, state)["vault"] == "黑池密室"


def test_a_secret_in_a_personal_observation_is_not_verified_as_public_knowledge():
    module, state = table()
    world = World(module, state)
    secret = world.rec.facts["npc:warden:secrets:0"]
    state.add("keeper", f"你梦中听见：{secret.text}。", audience=[state.investigator.id])
    world.sync()

    assert not knowledge.exposed(secret.text, knowledge.table(module, state, world.rec))
    assert world.rec.facts[secret.id].revealed is None
    assert not knowledge.sees(world.rec.facts[secret.id], "players")
    assert all(secret.text not in entry.text for entry in knowledge.evidence(module, state, world.rec))

    state.add("npc", secret.text, who=module.npc("warden").name)
    world.sync()
    assert knowledge.sees(world.rec.facts[secret.id], "players")
    assert world.rec.facts[secret.id].reveal_source.startswith("log:")


@pytest.mark.parametrize("kind", ["keeper", "npc", "event", "player", "speech"])
def test_private_clue_discussion_does_not_change_the_public_idea_difficulty(kind):
    module, state = table()
    clue = module.clue("c_gears")
    for _ in range(idea.STRESSED):
        state.add(kind, clue.text, audience=[state.investigator.id])
    idea.note(module, state)

    assert idea.difficulty(state, clue.id) == "常规"
    assert clue.id not in state.mentions
    assert state.mentions_at == state.logged

    state.add("keeper", clue.text)
    idea.note(module, state)
    idea.note(module, state)
    assert idea.difficulty(state, clue.id) == "困难"
    assert state.mentions[clue.id].told == 1


@pytest.mark.parametrize("kind", progress.SHOWN)
def test_shared_progress_material_never_contains_private_lines(kind):
    module, state = table()
    state.add("keeper", "大家在广场约好下午见面。")
    private = state.add(kind, "只有你认出了黑曜戒指的来历。", audience=[state.investigator.id])
    state.add("keeper", "大家接着沿街走向钟楼。")

    material = progress.material(module, state)
    assert "下午见面" in material and "沿街走向钟楼" in material
    assert "黑曜戒指" not in material
    assert private in state.log


def test_keeper_only_lines_are_not_a_source_for_public_progress():
    module, state = table()
    state.add("keeper", "幕后安排：午夜先将证人藏到报社。", audience=[])

    assert "幕后安排" not in progress.material(module, state)
    assert "没有新的事" in progress.material(module, state)
