"""What an NPC says it does on the spot lands in the same turn (agents/cast.Dialogue.items, .leave):
through its own world gateway (world/tools.Gateway), checked first, told why when it cannot, its words
kept; and asked along before leaving, it answers whether it comes (TravelDialogue)."""

from __future__ import annotations

from autokeeper import config
from autokeeper.agents.cast import Action, BeforeTravel, Cast, Ruling
from autokeeper.engine import check
from autokeeper.engine.game import Game, Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import Follow

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401


def setup(fake, location: str = "tower") -> tuple[Game, Cast]:
    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    state.location = location
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    return Game(m, state, cast, Rules(dice=fake)), cast


async def test_an_npc_hands_over_a_torch_it_takes_out_and_the_investigator_carries_it(fake):
    game, cast = setup(fake)
    st, me = game.state, game.state.investigator.id
    SCRIPT.append({"text": "（从柜子里摸出一支手电）拿着，楼上黑。",
                   "items": [{"name": "手电筒", "look": "黄铜外壳，玻璃有道裂纹", "to": me}]})
    said = await cast.npc(st, "warden", "楼上太黑了，看不清。")
    assert said == "（从柜子里摸出一支手电）拿着，楼上黑。" and len(SEEN) == 1 and not cast.unsettled
    torch = next(e for e in game.world.rec.entities.values() if e.name == "手电筒")
    assert torch.at == me and not torch.important and st.item_owners[torch.id] == me and "手电筒" in st.investigator.inventory
    acts = [e for e in game.world.rec.events if e.kind in ("bring", "transfer")]
    assert [e.kind for e in acts] == ["bring", "transfer"] and all(e.visibility == "public" for e in acts)
    assert all(e.op.startswith("world-tool:warden:") for e in acts)  # through the warden's own gateway, this turn
    assert "周衡的随身物品：手电筒" in check.account(game.m, st, st.turn, {}, None)
    assert "【实际持有】手电筒" in game._report(Turn(action=Action(kind="talk", summary="周衡向守钟人借光", npc="warden")))
    SCRIPT.append({"text": "灯油还够。"})
    await cast.npc(st, "warden", "你自己还有灯吗？")
    assert "你手边的日常物件" not in SEEN[-1]["messages"][-1]["content"]  # it gave the torch away
    await game.close()


async def test_an_npc_who_says_it_goes_off_is_gone_the_same_turn(fake):
    game, cast = setup(fake)
    SCRIPT.append({"text": "我得下去看看广场上布道的。", "leave": "square"})
    await cast.npc(game.state, "warden", "你要去哪？")
    assert game.world.place("warden") == "square" and game.m.npc("warden").location == "square"
    assert not cast.unsettled and "老守钟人" not in game._report(Turn(action=Action(kind="wait", summary="周衡等着")))
    await game.close()


async def test_a_companion_who_goes_off_ends_the_company_in_front_of_them(fake):
    game, cast = setup(fake)
    me = game.state.investigator.id
    assert game.world.apply(Follow(npc="warden", actor="warden", leader=me, destination="square")).ok
    SCRIPT.append({"text": "你们慢慢来，我先下去。", "leave": "square"})
    await cast.npc(game.state, "warden", "走吧。")
    warden = game.world.rec.entities["warden"]
    assert warden.at == "square" and not warden.following and not cast.unsettled
    await game.close()


async def test_an_act_it_cannot_do_is_told_why_and_asked_again(fake):
    game, cast = setup(fake)
    SCRIPT.extend([{"text": "我去报社一趟。", "leave": "office"}, {"text": "那我下楼去广场。", "leave": "square"}])
    await cast.npc(game.state, "warden", "你要去哪？")
    assert len(SEEN) == 2 and "老守钟人不知道 office 这个地方" in SEEN[-1]["messages"][-1]["content"]
    assert game.world.place("warden") == "square" and not cast.unsettled
    await game.close()


async def test_an_act_never_made_doable_keeps_its_words_and_lands_what_it_can(fake):
    game, cast = setup(fake, location="square")
    st, me = game.state, game.state.investigator.id
    bad = {"text": "日记和这盏灯，都拿去吧。", "items": [{"name": "日记", "to": me}, {"name": "煤油灯", "to": me}]}
    SCRIPT.extend([bad, bad, bad])
    said = await cast.npc(st, "preacher", "能把那本日记给我看看吗？")
    assert said == "日记和这盏灯，都拿去吧。" and len(SEEN) == 3  # its words stand; told why, twice
    fix = SEEN[-1]["messages"][-1]["content"]
    assert "「日记」不是随手就有的日常物件" in fix and "守钟人的日记" not in fix  # why, without the module's clue
    assert "日记" not in st.investigator.inventory and "煤油灯" in st.investigator.inventory  # the lamp it could give
    assert any("日记" in p for p in cast.unsettled["preacher"])
    note = game._unsettled("preacher")
    assert note and note[0].startswith("【未落实】街头布道者")  # the keeper tells how it really went
    await game.close()


async def test_a_companion_also_spoken_to_before_leaving_says_whether_it_comes_and_does(fake):
    game, _ = setup(fake)
    action = Action(kind="move", summary="对守钟人说陪他去广场走走，然后一起去", destination="square", companions=["warden"],
                    before=[BeforeTravel(npc="warden", speech="我陪您去广场走走吧。")])
    SCRIPT.append({"text": "也好，一起走。", "accompany": "square"})
    await game.prepare(action)
    assert len(SEEN) == 1  # one answer, not asked along a second time
    assert "accompany" in SEEN[-1]["schema"]["required"]
    assert "我陪您去广场走走吧" in SEEN[-1]["messages"][-1]["content"] and "一同前往广场" in SEEN[-1]["messages"][-1]["content"]
    warden = game.world.rec.entities["warden"]
    assert (warden.following, warden.follow_to) == (game.state.investigator.id, "square")
    turn = await game.resolve(action, Ruling(needs_roll=False))
    assert game.state.location == "square" and game.world.place("warden") == "square"
    assert any("老守钟人与调查员一同抵达广场" in n for n in turn.notes)
    await game.close()


async def test_words_said_with_an_invitation_are_answered_once_along_with_it(fake):
    game, _ = setup(fake)
    action = Action(kind="talk", summary="周衡邀守钟人去广场，顺便问钟的事", npc="warden", speech="陪我去广场吧，路上说说钟的事。",
                    destination="square", companions=["warden"])
    SCRIPT.append({"text": "走吧，钟的事路上说。", "accompany": "square"})
    await game.prepare(action)
    assert len(SEEN) == 1 and "accompany" in SEEN[-1]["schema"]["required"]
    assert "路上说说钟的事" in SEEN[-1]["messages"][-1]["content"]  # its answer is to what was actually said
    turn = await game.resolve(action, Ruling(needs_roll=False))
    await game.talk(turn)
    assert len(SEEN) == 1 and game.world.place("warden") == "square"  # answered before leaving, not asked again there
    await game.close()


async def test_a_companion_who_will_not_come_holds_the_party_back_once_asked(fake):
    game, _ = setup(fake)
    action = Action(kind="move", summary="对守钟人说陪他去广场，然后一起去", destination="square", companions=["warden"],
                    before=[BeforeTravel(npc="warden", speech="我陪您去广场走走吧。")])
    SCRIPT.append({"text": "我得守着钟，走不开。", "accompany": ""})
    await game.prepare(action)
    journal = game.state.pending_turn["preparation"]
    assert len(SEEN) == 1 and journal["blocked"] and any("尚未同意同行" in n for n in journal["notes"])
    assert {"before:0", "companion:warden"} <= set(journal["done"])
    await game.close()


async def test_the_arbiter_sees_everyday_things_here_and_on_the_people_here(fake):
    from autokeeper.world import ops as O

    game, cast = setup(fake)
    poker = game.world.apply(O.Bring(name="拨火棍", place="tower", actor="keeper")).data
    can = game.world.apply(O.Bring(name="油壶", holder="warden", actor="keeper")).data
    SCRIPT.append({"needs_roll": False})
    await cast.arbiter(game.state, Action(kind="use", summary="周衡拿起拨火棍", target="拨火棍"))
    sent = SEEN[-1]["messages"][-1]["content"]
    assert f"{poker} 拨火棍，持有者 tower" in sent and f"{can} 油壶，持有者 warden" in sent
    await game.close()
