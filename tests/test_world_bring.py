"""Everyday things registered as they come up (world/ops.Bring, world/adapters.bring_ops): by the
keeper, an NPC or the arbiter, only where the investigators are, never under a name the module keeps
for its own things, never what this turn's action lacks (Ruling.missing)."""

from __future__ import annotations

import json

import pytest

from autokeeper import paths
from autokeeper.agents.cast import Action, Dialogue, Ruling, Thing, TravelDialogue
from autokeeper.engine import check
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Handout, Item, Module
from autokeeper.world import adapters
from autokeeper.world import ops as O
from autokeeper.world.record import Entity, WorldRecord
from autokeeper.world.store import World

from .test_engine import make


@pytest.fixture
def module() -> Module:
    m = Module.load(paths.EXAMPLE)
    m.items.append(Item(id="lantern", name="提灯", description="守钟人的铁皮提灯", where="tower", owner="warden"))
    m.handouts.append(Handout(id="h_map", title="小镇地图", text="手绘的街道"))
    return m


async def test_a_thing_handed_over_is_registered_and_carried_into_later_turns(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    w, st, inv = game.world, game.state, game.state.investigator
    done = w.apply(O.Bring(name="手电筒", look="黄铜外壳，玻璃有道裂纹", holder=inv.id, actor="keeper"))
    assert done.ok, done.problems
    thing = w.rec.entities[done.data]
    assert (thing.kind, thing.important, thing.at, thing.look) == ("item", False, inv.id, "黄铜外壳，玻璃有道裂纹")
    assert "手电筒" in inv.inventory and st.item_owners[thing.id] == inv.id
    assert w.rec.events[-1].kind == "bring" and w.rec.events[-1].visibility == "public"
    assert not w.rec.events[-1].reactive  # seen, but it wakes nobody to think again
    assert "【实际持有】手电筒" in game._report(Turn(action=Action(kind="other", summary="周衡看看四周")))
    assert "周衡的随身物品：手电筒" in check.account(module, st, st.turn, {}, None)
    loaded = GameState.model_validate_json(st.model_dump_json())
    assert loaded.record.entities[thing.id].name == "手电筒" and "手电筒" in loaded.investigator.inventory
    # Given back by the name the inventory shows, its record goes along.
    assert w.apply(O.Transfer(item="手电筒", to="warden")).ok
    assert w.rec.entities[thing.id].at == "warden" and st.item_owners[thing.id] == "warden" and "手电筒" not in inv.inventory
    assert "老守钟人：手电筒" in check.account(module, st, st.turn, {}, None)
    await game.close()


@pytest.mark.parametrize("name, kind", [("守钟人的日记", "线索"), ("日记", "线索"), ("小镇地图", "手稿"),
                                        ("提灯", "重要物件"), ("一盏提灯", "重要物件")])
async def test_a_thing_named_like_a_clue_a_handout_or_an_important_item_is_refused(module, fake, name, kind):
    game, _, _ = make(module, fake, {}, location="tower")
    w = game.world
    before = json.dumps(w.domain(), sort_keys=True)
    r = w.apply(O.Bring(name=name, holder=game.state.investigator.id, actor="keeper"))
    assert not r.ok and any("重名" in p and kind in p for p in r.problems), r.problems
    assert json.dumps(w.domain(), sort_keys=True) == before
    await game.close()


async def test_a_thing_is_registered_only_on_someone_or_somewhere_in_the_scene(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")  # the preacher is out on the square
    w = game.world
    r = w.apply(O.Bring(name="圣经", holder="preacher", actor="keeper"))
    assert not r.ok and "街头布道者不在调查员所在的钟楼" in r.problems[0]
    r = w.apply(O.Bring(name="拨火棍", place="office", actor="keeper"))
    assert not r.ok and "不是调查员此刻所在的地方" in r.problems[0]
    r = w.apply(O.Bring(name="拨火棍", actor="keeper"))
    assert not r.ok and "填一个" in r.problems[0]
    assert w.apply(O.Bring(name="拨火棍", place="tower", actor="keeper")).ok
    assert w.apply(O.Bring(name="油壶", holder="warden", actor="keeper")).ok
    r = w.apply(O.Bring(name="一根拨火棍", place="tower", actor="keeper"))
    assert not r.ok and "已经有拨火棍了" in r.problems[0]  # one of it is enough
    await game.close()


async def test_what_the_action_lacks_cannot_be_brought_out_that_turn(module, fake):
    action = Action(kind="investigate", summary="周衡想撬开齿轮间的门", target="齿轮间")
    ruling = Ruling(needs_roll=False, missing=["撬棍"], reason="手边没有撬棍")
    game, _, _ = make(module, fake, {"撬门": (action, ruling)}, location="tower")
    w, me = game.world, game.state.investigator.id
    turn = await game.resolve(action, ruling)
    assert "这次行动缺撬棍，做不成；叙述和场面里都不能把它变出来。" in turn.notes  # the keeper hears what it lacked
    r = w.apply(O.Bring(name="一根旧撬棍", holder="warden", actor="keeper"))
    assert not r.ok and "正缺撬棍" in r.problems[0]
    found = adapters.bring_ops(w, [{"name": "撬棍", "look": "铁的"}], actor="arbiter", holder=me)
    assert not found.ops and found.ids == [""] and "正缺撬棍" in found.problems[0]
    found = adapters.bring_ops(w, ["梯子"], actor="keeper", holder=me, missing=["木梯子"])  # a caller's own list counts too
    assert not found.ops and "正缺木梯子" in found.problems[0]
    game.state.turn += 1  # another turn, another action: it may come up
    assert w.apply(O.Bring(name="撬棍", holder="warden", actor="keeper")).ok
    await game.close()


async def test_the_arbiter_puts_what_the_investigator_takes_up_in_their_hands(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    take = Action(kind="use", summary="周衡拿起墙角的手杖", target="手杖")
    await game.resolve(take, Ruling(needs_roll=False, takes=[Thing(name="手杖", look="乌木杖身，铜包头")]))
    cane = next(e for e in game.world.rec.entities.values() if e.name == "手杖")
    assert "手杖" in inv.inventory and cane.at == inv.id and cane.look == "乌木杖身，铜包头"
    game.state.turn += 1
    dig = Action(kind="investigate", summary="周衡想撬开柜子", target="守钟人的柜子")
    turn = await game.resolve(dig, Ruling(needs_roll=False, missing=["撬棍"], takes=[Thing(name="撬棍")]))
    assert "撬棍" not in inv.inventory
    assert any(n.startswith("【未落实】周衡没拿到：撬棍") and "正缺撬棍" in n for n in turn.notes)
    await game.close()


async def test_a_turn_registers_a_loose_number_of_things(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    w = game.world
    for i in range(O.MAX_BRING):
        assert w.apply(O.Bring(name=f"杂物{i}", place="tower", actor="keeper")).ok
    r = w.apply(O.Bring(name="铁皮桶", place="tower", actor="keeper"))
    assert not r.ok and f"这一轮已经登记了 {O.MAX_BRING} 件东西" in r.problems
    game.state.turn += 1
    assert w.apply(O.Bring(name="铁皮桶", place="tower", actor="keeper")).ok
    await game.close()


async def test_the_scene_registrar_leaves_what_is_there_and_picks_up_what_lies_about(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    w, inv = game.world, game.state.investigator
    inv.inventory.append("随身小螺丝刀与钳子")
    poker = w.apply(O.Bring(name="拨火棍", place="tower", actor="keeper")).data
    before = json.dumps(w.domain(), sort_keys=True)
    found = adapters.bring_ops(w, ["螺丝刀", {"name": "拨火棍"}, {"name": "手电筒", "look": "黄铜的"}, {"name": "一支手电筒"},
                                   {"name": "怀表", "holder": "老守钟人"}, {"name": "茶杯", "place": "钟楼"}],
                               actor="keeper", holder=inv.id)
    assert json.dumps(w.domain(), sort_keys=True) == before  # pure: nothing lands until the ops are applied
    assert found.ids[:2] == ["", poker]  # part of an inventory bundle is not the whole object
    assert "随身小螺丝刀与钳子" in found.problems[0] and "完整名称" in found.problems[0]
    assert found.ids[3] == "" and found.problems[1:] == ["一支手电筒：同一件东西写了两次"]
    assert adapters.bring_ops(w, ["随身小螺丝刀与钳子"], actor="keeper", holder=inv.id) == ([], [], ["随身小螺丝刀与钳子"])
    assert [op.kind for op in found.ops] == ["transfer", "bring", "bring", "bring"]
    assert [(op.holder, op.place) for op in found.ops[1:]] == [(inv.id, ""), ("warden", ""), ("", "tower")]  # names read as ids
    for op in found.ops:
        assert w.apply(op).ok
    assert w.rec.entities[poker].at == inv.id and {"拨火棍", "手电筒"} <= set(inv.inventory)
    assert w.rec.entities[found.ids[4]].at == "warden" and w.rec.entities[found.ids[5]].at == "tower"
    assert adapters.bring_ops(w, ["手电筒", "拨火棍"], actor="keeper", holder=inv.id) == ([], [], [found.ids[2], poker])
    await game.close()


def test_saves_and_replies_from_before_the_new_fields_load_as_ever(module):
    ruling = Ruling.model_validate({"needs_roll": False, "reason": "旧裁定"})
    assert ruling.missing == [] and ruling.takes == []
    reply = Dialogue.model_validate({"text": "好。"})
    assert reply.items == [] and reply.leave == ""
    assert TravelDialogue.model_validate({"text": "走吧。", "accompany": "square"}).leave == ""
    assert WorldRecord.model_validate({"seq": 3}).missing_turn == -1
    assert Entity.model_validate({"id": "coat", "kind": "item", "name": "雨衣"}).look == ""
    st = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    st.location = "tower"
    World(module, st)
    old = json.loads(st.model_dump_json())
    for key in ("missing", "missing_turn"):
        old["record"].pop(key)
    for entity in old["record"]["entities"].values():
        entity.pop("look")
    loaded = GameState.model_validate(old)
    w = World(module, loaded)
    assert w.rec.missing == [] and w.apply(O.Bring(name="手电筒", holder=loaded.investigator.id, actor="keeper")).ok


async def test_a_module_item_of_no_importance_lying_here_is_taken_up_not_doubled(module, fake):
    module.items.append(Item(id="cane", name="手杖", description="靠在墙角的旧手杖", where="tower", important=False))
    game, _, _ = make(module, fake, {}, location="tower")
    inv = game.state.investigator
    found = adapters.bring_ops(game.world, ["手杖"], actor="arbiter", holder=inv.id)
    assert [(op.kind, op.item) for op in found.ops] == [("transfer", "cane")]
    assert game.world.apply(found.ops[0]).ok and inv.inventory == ["手杖"] and game.state.item_owners["cane"] == inv.id
    await game.close()
