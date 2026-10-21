"""What the keeper's people can perceive in a fight (the keeper knows all, an NPC does not: guide #25),
and the circumstances of a shot as the book counts them (Keeper Rulebook pp. 95–96)."""

from __future__ import annotations

import pytest

from autokeeper.engine import combat as battle
from autokeeper.engine.encounter import plan_check, views
from autokeeper.engine.encounter.model import Engagement
from autokeeper.engine.encounter.plan import CoverNote, DeclaredAction, Distance, EncounterStart, EncounterTurnPlan
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Creature, Module
from autokeeper.paths import EXAMPLE
from autokeeper.world.record import Fact

from .encounter_kit import arrange, attack, begin, fight_game

DEFEND = DeclaredAction(do="defend", summary="守着")
GUN = ".38 左轮手枪"
WARDEN = "楼梯口打盹的老人"


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


def refused(game) -> list[str]:
    """What plan_check says of the warden going for 周衡 now."""
    return [p for p in plan_check.land(game, EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng"))]), act=False).unsettled
            if "看不见" in p]


async def act(game, text: str) -> Turn:
    game.asking = False
    game.state.turn += 1
    action = await game.encounter_input(text)
    return await game.resolve(action, await game.rule(action))


async def started(module, fake, **kw):
    game, cast, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", DEFEND)]), **kw)
    await begin(game, ["warden"])
    return game, cast, events


# -- each source of not being seen ---------------------------------------------------------------------
async def test_a_position_the_keeper_declares_hides_them(module, fake):
    game, _, _ = await started(module, fake)
    assert not refused(game)
    await game._plan_unit("cover", EncounterTurnPlan(cover=[CoverNote(who="zhouheng", what="大钟背后", full=True)]).model_dump(
        mode="json", by_alias=True), [], Turn())
    assert refused(game) == ["warden 的第 1 个行动（attack）：warden 看不见 zhouheng：藏在掩体后面，看不见"]
    assert "zhouheng（藏在掩体后面，看不见）" in views.keeper_text(game)


async def test_a_hide_that_works_hides_them_until_they_strike(module, fake):
    game, _, _ = await started(module, fake)
    fake.queue(5)  # 潜行 (base 20) against the warden's Spot Hidden: an extreme success
    turn = await act(game, "躲藏｜被攻击时闪避")
    assert any("躲了起来" in line for line in turn.fight) and refused(game)
    fake.queue(90, 90)  # out of hiding, a swing that misses
    await act(game, f"攻击 {WARDEN}（徒手）｜被攻击时闪避")
    assert not refused(game)  # striking gave them away


async def test_the_dark_hides_whoever_carries_no_light_but_not_from_a_creature(module, fake):
    module.location("tower").weather = ["torch"]
    game, _, _ = await started(module, fake)
    assert refused(game) and "四下漆黑" in refused(game)[0]
    game.state.investigator.inventory.append("手电筒")
    assert not refused(game)  # a light they carry: seen
    game.state.investigator.inventory.remove("手电筒")
    game.state.encounter.scene.engaged.append(Engagement(a="warden", b="zhouheng", since=1))
    assert not refused(game)  # close enough to grab
    module.creatures.append(Creature(id="hound", name="猎犬", description="影子", sanity="0/0", stats={"生命": 8},
                                     attacks=["咬 30%，伤害 1D4"]))
    fake.queue(10)
    game2, _, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("hound", DEFEND)]))
    await begin(game2, ["hound"])
    landed = plan_check.land(game2, EncounterTurnPlan(npcs=[arrange("hound", attack("zhouheng"))]), act=False)
    assert landed.npcs  # it hunts in the dark


async def test_someone_hidden_in_the_old_chase_cannot_be_gone_after(module, fake):
    game, _, _ = await started(module, fake)
    me, warden = battle.from_investigator(game.state.investigator), battle.from_npc(module.npc("warden"))
    game.state.chase = battle.Chase(runners=[me, warden], track=battle.default_track("street"), quarry="party", hidden=[me.id])
    assert refused(game) and "躲起来了" in refused(game)[0]


async def test_what_an_npc_was_told_of_someone_is_in_the_brief(module, fake):
    game, _, _ = await started(module, fake)
    game.world.rec.facts["told:zhou"] = Fact(id="told:zhou", text="周衡是报社派来打听钟楼的记者", about=["zhouheng"],
                                             known_by=["warden"])
    text = views.keeper_text(game)
    assert "他听说过：周衡：周衡是报社派来打听钟楼的记者" in text


# -- a shot's circumstances ------------------------------------------------------------------------------
async def gunfight(module, fake, react: str = "fight_back", do: DeclaredAction = DEFEND):
    module.npc("warden").stats = {"生命": 30, "移动": 8}
    game, _, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", do, react=react)]))
    game.state.investigator.inventory.append(GUN)
    await begin(game, ["warden"])
    return game, events


SHOOT = f"射击 {WARDEN}（{GUN}）｜被攻击时闪避"


async def test_the_range_band_follows_the_distance_the_keeper_set(module, fake):
    game, events = await gunfight(module, fake)
    await game._plan_unit("d", EncounterTurnPlan(distances=[Distance(a="zhouheng", b="warden", yards=50)]).model_dump(
        mode="json", by_alias=True), [], Turn())
    fake.queue(4)  # an extreme success is what 50 yards (beyond twice 15) needs; 射击(手枪) 20: 4 is it
    fake.queue_totals(6)
    await act(game, SHOOT)
    act_ev = next(e for e in events if e["type"] == "fight_act")
    assert act_ev["roll"]["difficulty"] == "极难" and "超射程（50 码）" in act_ev["line"]
    game.state.encounter.scene.distance["zhouheng|warden"] = 100  # past four times its range
    await act(game, SHOOT)
    assert "打不着" in [e for e in events if e["type"] == "fight_act"][-1]["line"]


async def test_aiming_a_round_gives_the_next_shot_a_bonus_die(module, fake):
    game, events = await gunfight(module, fake)
    await act(game, f"瞄准 {WARDEN}｜被攻击时闪避")
    fake.queue(90)
    await act(game, SHOOT)
    shot = next(e for e in events if e["type"] == "fight_act")
    assert shot["roll"]["bonus"] == 1 and "瞄准了一轮" in shot["line"]


async def test_a_dive_leaves_the_target_half_hidden_for_the_next_shot(module, fake):
    game, events = await gunfight(module, fake, "cover")
    fake.queue(10, 90)  # he dives for cover and makes it; the shot misses
    await act(game, SHOOT)
    fake.queue(90, 90)  # next round his second dive fails; he is still half behind the cover he reached
    await act(game, SHOOT)
    second = [e for e in events if e["type"] == "fight_act"][-1]
    assert "目标一半以上被遮住" in second["line"] and second["roll"]["bonus"] == -1


async def test_a_man_running_flat_out_is_harder_to_hit(module, fake):
    game, events = await gunfight(module, fake, "none", DeclaredAction(do="flee", summary="往楼下跑"))
    fake.queue(90)  # the gun goes first (+50 DEX); he is already running for the stairs
    await act(game, SHOOT)
    shot = next(e for e in events if e["type"] == "fight_act")
    assert "目标在全速奔跑" in shot["line"] and shot["roll"]["bonus"] == -1


async def test_a_big_target_is_easier_and_a_fumble_into_a_melee_hits_a_friend(module, fake):
    module.creatures.append(Creature(id="beast", name="巨兽", description="一堵墙似的东西", sanity="0/0",
                                     stats={"生命": 40, "敏捷": 10, "力量": 200, "体型": 200}, attacks=["撞 30%，伤害 1D6"]))
    game, _, events = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[
        arrange("beast", DEFEND), arrange("preacher", DEFEND)]))
    game.state.investigator.inventory.append(GUN)
    fake.queue(10)
    assert await game._encounter_open(EncounterStart(mode="fight", foes=["beast"], allies=["preacher"], pace=6, reason="x"), Turn())
    game.state.encounter.scene.engaged.append(Engagement(a="preacher", b="beast", since=0))
    fake.queue(100).queue_totals(4)  # a fumble: the shot goes into the preacher wrestling it
    await act(game, f"射击 {views.Ctx.of(game).name('beast', 'players')}（{GUN}）｜被攻击时闪避")
    shot = next(e for e in events if e["type"] == "fight_act")
    assert "目标体型巨大" in shot["line"] and "朝扭打中的人开枪" in shot["line"]
    assert "失手打中了自己人" in shot["line"] and game.world.rec.entities["preacher"].life.hp < game.world.rec.entities["preacher"].life.hp_max
