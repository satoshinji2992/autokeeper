"""Confirmed world-state defects retained as strict expected failures during review."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, PushRuling, Ruling
from autokeeper.engine.game import Game
from autokeeper.engine.scenes import SceneView
from autokeeper.engine.state import SceneState
from autokeeper.rules.coc import Rules
from autokeeper.world import adapters
from autokeeper.world.ops import Bring
from autokeeper.world.record import Entity
from autokeeper.world.store import World
from autokeeper.world.tools import Gateway

from .test_engine import FakeCast
from .test_harness import SCRIPT, harness_sandbox  # noqa: F401
from .test_npc_acts import setup as npc_setup
from .test_world import fresh


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="Gateway publication replaces investigators held across another scene's model await")
async def test_parallel_npc_handover_preserves_pushed_success_growth_mark(fake):
    w = fresh("office")
    root = w.st
    lead = root.investigator
    lead.skills["侦查"] = 60
    friend = lead.model_copy(update={"id": "friend", "name": "另一名调查员"}, deep=True)
    root.investigators.append(friend)
    root.positions = {lead.id: "office", friend.id: "square"}
    root.scenes = {
        "inside": SceneState(id="inside", members=[lead.id], place="office", at=root.minutes, active=0),
        "outside": SceneState(id="outside", members=[friend.id], place="square", at=root.minutes, active=1),
    }
    entered, resume = asyncio.Event(), asyncio.Event()

    class PausingCast(FakeCast):
        async def repush(self, state, pending, how):
            entered.set()
            await resume.wait()
            return PushRuling(achievable=True, minutes=1)

    game = Game(w.m, SceneView(root, root.scenes["inside"]), PausingCast({}), Rules(dice=fake), restore=False)
    outside = World(w.m, SceneView(root, root.scenes["outside"]))
    fake.queue(90, 5)
    pending = None
    try:
        first = await game.resolve(Action(kind="investigate", summary="查阅书架", target="书架"),
                                   Ruling(needs_roll=True, skill="侦查"))
        assert first.push
        pending = asyncio.create_task(game.push({"choice": "push", "how": "移开书架重新找"}))
        await asyncio.wait_for(entered.wait(), 2)
        result = Gateway(outside, "preacher").settle(
            [{"kind": "bring", "name": "纸杯", "holder": "preacher"},
             {"kind": "transfer", "item": "纸杯", "to": friend.id, "giver": "preacher"}],
            request_id="parallel-cup",
        )
        assert result["ok"], result
        resume.set()
        pushed = await pending
        assert pushed.checks[0].passed
        assert "纸杯" in root.find(friend.id).inventory
        assert "侦查" in root.find(lead.id).marks
    finally:
        resume.set()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await game.close()


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="Substring item resolution transfers an entire container for an unspecified portion")
def test_serving_soup_does_not_transfer_the_whole_pot():
    w = fresh("tower")
    me = w.st.investigator.id
    assert w.apply(Bring(name="一锅热汤", holder="warden", actor="warden")).ok
    groups, problems = adapters.reply_ops(w, "warden", items=[{"name": "热汤", "to": me}])
    for index, group in enumerate(groups):
        result = Gateway(w, "warden").settle(group, request_id=f"serve-one-bowl:{index}")
        assert result["ok"], result
    # An unresolved portion may be refused or represented separately, never the whole pot.
    assert "一锅热汤" not in w.st.investigator.inventory, problems
    assert any(e.kind == "item" and e.name == "一锅热汤" and e.at == "warden" for e in w.rec.entities.values())


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="NPC item lookup selects the first of multiple important objects matching one name")
def test_ambiguous_important_item_name_requires_disambiguation(reverse):
    w = fresh("tower")
    me = w.st.investigator.id
    objects = [Entity(id="steel-key", name="钢钥匙", kind="item", important=True, at="warden"),
               Entity(id="brass-key", name="黄铜钥匙", kind="item", important=True, at="warden")]
    for item in reversed(objects) if reverse else objects:
        w.rec.entities[item.id] = item
    groups, problems = adapters.reply_ops(w, "warden", items=[{"name": "钥匙", "to": me}])
    assert problems and not groups


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="Transferring one same-named entity erases the inventory entry for another still owned")
def test_transferring_one_of_two_same_named_items_keeps_the_other_visible():
    w = fresh("tower")
    me = w.st.investigator.id
    for ident in ("key-a", "key-b"):
        w.rec.entities[ident] = Entity(id=ident, name="钥匙", kind="item", important=True, at=me)
        w.st.item_owners[ident] = me
    w.st.investigator.inventory.append("钥匙")
    result = Gateway(w, "keeper").settle(
        [{"kind": "transfer", "item": "key-a", "to": "warden", "giver": me}], request_id="one-of-two")
    assert result["ok"], result
    assert w.rec.entities["key-a"].at == "warden"
    assert w.rec.entities["key-b"].at == me
    assert "钥匙" in w.st.investigator.inventory


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="An NPC items reply accepts a held item as its own recipient, creating recursive ownership")
async def test_npc_reply_rejects_an_item_as_its_own_holder(fake):
    game, cast = npc_setup(fake)
    game.world.rec.entities["key-a"] = Entity(id="key-a", name="钥匙", kind="item", important=True, at="warden")
    reply = {"text": "钥匙给您。", "items": [{"name": "钥匙", "to": "key-a"}]}
    SCRIPT.extend([reply, reply, reply])  # enough attempts if a repair asks for a valid recipient
    try:
        assert await cast.npc(game.state, "warden", "把钥匙给我。") == "钥匙给您。"
        assert game.world.rec.entities["key-a"].at == "warden"
        assert game.world.place("key-a") == "tower"
    finally:
        await game.close()
