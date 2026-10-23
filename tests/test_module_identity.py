"""Stable entity ids survive compilation and ambiguous NPC names are never guessed."""

from __future__ import annotations

import pytest

from autokeeper.agents.director import Improv
from autokeeper.module import graph, improv
from autokeeper.module.identity import identity
from autokeeper.module.schema import Creature, Item, Module
from autokeeper.paths import EXAMPLE
from autokeeper.world import scene
from autokeeper.world.ops import Improvise

from .test_scene import made, sandbox  # noqa: F401


def test_compiler_audit_does_not_hide_two_npcs_with_the_same_id():
    module = Module.load(EXAMPLE)
    module.npcs.append(module.npcs[0].model_copy(update={"name": "另一个人物"}))
    assert any(issue.level == "error" and "NPC id warden 重复" in issue.text for issue in graph.audit(module))


def test_world_entities_cannot_share_an_id_but_a_creature_sheet_can_reference_its_npc():
    module = Module.load(EXAMPLE)
    module.items.append(Item(id="warden", name="钥匙", where="square", description="铜钥匙", important=True))
    assert any(issue.level == "error" and "重复用于NPC和物件" in issue.text for issue in graph.audit(module))
    module.items.pop()
    module.creatures.append(Creature(id="warden", npc="warden", name="守钟人", description="同一人物的战斗资料"))
    assert not [issue for issue in graph.audit(module) if issue.level == "error"]


def test_typographic_variants_are_the_same_full_identity_but_prefixes_are_not():
    assert identity("阿格 · 尼丝") == identity("阿格・尼丝")
    assert identity("Ｊｏｈｎ　Ｓｍｉｔｈ") == identity("John Smith")
    assert identity("阿格尼丝") != identity("阿格尼丝的邻居")


async def test_scene_and_director_reuse_an_existing_npc_despite_a_new_id(fake):
    module = Module.load(EXAMPLE)
    module.npc("preacher").name = "阿格·尼丝"
    game, _ = made("square", module=module, fake=fake)
    try:
        raw = {"people": [{"id": "maid_agnes", "name": "阿格・尼丝"}], "here": ["maid_agnes"]}
        planned = scene.plan(game.world, raw, scene.Context(travel=game._travel))
        assert any("preacher" in reason and "已有记录" in reason for reason in planned.problems)
        assert "maid_agnes" not in game.world.rec.entities
        directed = Improv(kind="展开", reason="安排来客见到现有佣人", npcs=[{
            "id": "maid_agnes", "name": "阿格・尼丝", "location": "square", "role": "佣人"}])
        assert any("preacher" in reason and "已有记录" in reason for reason in improv.check(module, directed, at="square"))
        assert not game.world.apply(Improvise(plan=directed.model_dump(mode="json"), actor="keeper", at="square")).ok
    finally:
        await game.close()


async def test_two_intentionally_same_named_npcs_require_ids_and_are_not_merged(fake):
    module = Module.load(EXAMPLE)
    module.npc("preacher").name = module.npc("warden").name = "约翰"
    module.npc("warden").location = "square"
    game, _ = made("square", module=module, fake=fake)
    try:
        ambiguous = scene.plan(game.world, {"here": ["约翰"]}, scene.Context(travel=game._travel))
        assert any("对应多位人物" in reason and "preacher" in reason and "warden" in reason for reason in ambiguous.problems)
        assert scene._npc(game.world, "约翰") is None
        assert scene._npc(game.world, "约翰（warden）") == "warden"
        explicit = scene.plan(game.world, {"here": ["preacher", "warden"]}, scene.Context(travel=game._travel))
        assert not explicit.problems
        assert len([npc for npc in game.world.rec.entities.values() if npc.kind == "npc" and npc.name == "约翰"]) == 2
    finally:
        await game.close()


@pytest.mark.parametrize("folder", ["dongxuezhixia", "zhuishuren", "fallout3-youxierenyouyanbujin"])
def test_bundled_module_entity_ids_are_valid_for_world_record_and_compilation(folder):
    from autokeeper import paths

    module = Module.load(paths.SOURCE / "modules" / folder)
    assert not [issue for issue in graph.audit(module) if issue.level == "error"]
