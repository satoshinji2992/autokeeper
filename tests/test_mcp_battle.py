"""Fights and chases played through the MCP tools: the engine settles every round, as on the
screens; the host is handed the account, the state after and the choices for the next round."""

from __future__ import annotations

import pytest

from autokeeper import mcp_server as server
from autokeeper import paths
from autokeeper.agents.encounter import Pursuit, PursuitAnswer
from autokeeper.engine.encounter.plan import EncounterTurnPlan
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Creature, Module
from autokeeper.rules.coc import Rules

from .encounter_kit import Keeper, arrange, attack
from .test_engine import make

HIT = "攻击 楼梯口打盹的老人（徒手）｜被攻击时闪避"
HOUND = Creature(id="hound", name="猎犬", description="墙角里渗出来的瘦长黑影", sanity="0/1D3",
                 stats={"敏捷": 40, "体质": 50, "移动": 6}, attacks=["撕咬 40%，伤害 1D6"])


@pytest.fixture
def module() -> Module:
    return Module.load(paths.EXAMPLE)


@pytest.fixture
async def table(module, fake, monkeypatch):
    """A game the MCP tools play in the clock tower, with the scripted cast and dice: the keeper plan
    arranges the foes (each attacks the investigator; a creature gives chase when asked)."""
    opened: list[Game] = []

    def sit(flags=()):
        game, _, events = make(module, fake, {}, location="tower")
        cast = Keeper({}, arrange=lambda state, text, brief: EncounterTurnPlan(npcs=[
            arrange(p.id, attack("zhouheng")) for p in state.encounter.participants if p.controller == "keeper"]),
            pursuit=lambda state, text, brief: PursuitAnswer(decisions=[
                Pursuit(who=p.id, pursue=["zhouheng"]) for p in state.encounter.participants if p.controller == "keeper"]))
        game.cast, cast.world = cast, game.world
        game.state.slot = "mcp-battle"
        monkeypatch.setattr(server.S, "game", game)
        monkeypatch.setattr(server.S, "module", module)
        game.checkpoint, game.turn_checkpoint = server._save, server._keep_turn
        opened.append(game)
        return game, cast, events

    yield sit
    for game in opened:
        await game.close()


def reload(game: Game, fake) -> Game:
    """The save on disk opened again, as after a crash: a new game takes over the tools."""
    loaded = GameState.model_validate_json(paths.save(game.state.slot).read_text(encoding="utf-8"))
    resumed = Game(Module.load(paths.EXAMPLE), loaded, game.cast, Rules(dice=fake))
    server.S.game, server.S.module = resumed, resumed.m
    resumed.checkpoint, resumed.turn_checkpoint = server._save, server._keep_turn
    return resumed


def foes(now: dict) -> list[dict]:
    return [f for f in now.get("fighters", now.get("runners", [])) if f["side"] == "foe"]


async def test_a_fight_begins_with_the_first_rounds_choices_and_rolls_nothing(table, fake):
    game, _, _ = table()
    started = await server.start_fight(["warden"], reason="守钟人抄起扳手扑了过来")
    battle = started["battle"]
    now = battle["now"]
    assert battle["lines"][0].startswith("【战斗开始】") and battle["over"] is None and not battle["exchanges"]
    assert [p["name"] for p in battle["portraits"]] == ["楼梯口打盹的老人"] and battle["portraits"][0]["art"]  # the face it is with
    assert now["kind"] == "fight" and now["round"] == 0 and now["initiative"] == ["周衡", "楼梯口打盹的老人"]  # DEX 65 before 50
    assert HIT in [c["order"] for c in now["choices"]] and any(c["order"].startswith("逃跑") for c in now["choices"])
    assert [(f["hp"], f["hp_max"], f["wound"]) for f in foes(now)] == [(None, None, "无伤")]  # a foe's wounds in words only
    st = game.state
    assert st.encounter is not None and st.encounter.round == 0 and st.turn == 1 and not st.pending_mcp
    assert server.scene()["battle"]["choices"] == now["choices"]  # the state, read again
    for refused in (lambda: server.skill_check("侦查"), lambda: server.move("square"), lambda: server.advance_time(10),
                    lambda: server.npc_say("warden", "住手！"), lambda: server.start_fight(["warden"])):
        with pytest.raises(ValueError, match="战斗或追逐中"):
            await refused()
    for done_by_hand in (lambda: server.apply_damage("3"), lambda: server.dice(".r 1d6")):  # never a blow made up
        with pytest.raises(ValueError, match="battle_round"):
            done_by_hand()
    assert st.encounter.round == 0 and st.turn == 1


async def test_a_round_in_the_screens_words_is_settled_whole_and_reported(table, fake):
    game, _, _ = table()
    await server.start_fight(["warden"])
    # 周衡 hard; the warden fights back and fails: 1 damage; the warden hits, 周衡's dodge fails: 3
    fake.queue(10, 60, 20, 50).queue_totals(1, 3)
    out = await server.battle_round(HIT)
    battle = out["battle"]
    assert battle["order"] == f"周衡：{HIT}" and battle["lines"][0] == "【战斗 · 第 1 轮】先后：周衡 → 老守钟人"
    assert "portraits" not in battle  # shown as it began
    mine, theirs = battle["exchanges"]
    assert (mine["attacker"], mine["attack"], mine["defender"]) == ("周衡", "徒手", "楼梯口打盹的老人")  # as the players know him
    assert mine["reaction"] == "反击" and mine["result"] == "命中" and mine["hurt"] == "楼梯口打盹的老人" and mine["damage"] == "1D3 = 1"
    assert "10" in mine["roll"] and "60" in mine["answer"]  # both rolls, as the fight card shows them
    assert theirs["hurt"] == "周衡" and theirs["reaction"] == "闪避" and "HP 11 → 8" in theirs["notes"][0]
    now = battle["now"]
    me = next(f for f in now["fighters"] if f["id"] == "zhouheng")
    assert now["round"] == 1 and me["hp"] == 8 and battle["over"] is None and HIT in [c["order"] for c in now["choices"]]
    assert [(f["hp"], f["wound"]) for f in foes(now)] == [(None, "轻伤")]
    assert game.state.investigator.hp == 8 and game.world.rec.entities["warden"].life.hp == 10
    assert not fake.values and not fake.totals and game.state.turn == 2


async def test_the_fight_ends_with_its_flags_and_play_goes_on(table, fake, module):
    module.npc("warden").stats = {"生命": 4, "体质": 30}
    module.flags["warden_down"] = "守钟人被打倒在地"
    game, _cast, _ = table(flags=["warden_down"])
    await server.start_fight(["warden"], surprise="foe", reason="周衡趁守钟人背过身去扑了上去")
    fake.queue(10, 80).queue_totals(2)  # 周衡 hard against a man caught unawares: 2 damage; his CON fails
    out = await server.battle_round(HIT)
    battle = out["battle"]
    (blow,) = battle["exchanges"]
    assert blow["answer"] == "" and blow["reaction"] == "" and blow["notes"] == ["重伤倒地", "昏死过去"]  # no answer to a surprise
    assert battle["over"]["kind"] == "fight" and battle["over"]["result"] == "won" and "楼梯口打盹的老人昏迷" in battle["over"]["text"]
    assert battle["now"] is None and battle["lines"][-1].startswith("【战斗结束】")
    assert game.world.rec.entities["warden"].status == "重伤" and game.state.encounter is None  # a major wound, kept
    assert server.scene()["battle"] is None
    fake.queue(30)
    assert (await server.skill_check("侦查"))["checks"]  # out of the fight: everything else works again
    with pytest.raises(ValueError, match="没有战斗或追逐"):
        await server.battle_round(HIT)


async def test_words_the_engine_does_not_read_are_the_hosts_to_rule(table, fake):
    game, _, _ = table()
    await server.start_fight(["warden"])
    with pytest.raises(ValueError, match="skill"):
        await server.battle_round("把油灯砸向老守钟人", harm="1D6", target="warden")
    with pytest.raises(ValueError, match="target"):
        await server.battle_round("把油灯砸向老守钟人", skill="投掷", harm="1D6", target="preacher")
    fake.queue(5, 20, 50).queue_totals(4, 3)  # 周衡's throw: 4 damage; the warden hits, the dodge fails: 3
    out = await server.battle_round("把油灯砸向老守钟人", skill="投掷", harm="1D6", target="warden")
    battle = out["battle"]
    assert battle["order"] == "周衡：把油灯砸向老守钟人" and len(battle["exchanges"]) == 1  # the warden's blow
    assert any("把油灯砸向老守钟人" in line for line in battle["lines"]) and any("老守钟人受到 1D6 = 4" in line for line in battle["lines"])
    assert game.world.rec.entities["warden"].life.hp == 7 and [f["wound"] for f in foes(battle["now"])] == ["受伤"]
    assert not fake.values and not fake.totals


async def test_a_retried_round_goes_on_from_where_it_broke_and_rolls_nothing_again(table, fake):
    game, _, _ = table()
    await server.start_fight(["warden"])
    fake.queue(10, 60, 20, 50).queue_totals(1, 3)
    shown = game.emit

    async def broken(event):
        if event["type"] == "fight_act" and event["attacker"] == "warden":
            raise ConnectionError("connection lost")
        await shown(event)

    game.emit = broken
    with pytest.raises(ConnectionError):
        await server.battle_round(HIT)
    assert game.state.pending_mcp and not fake.values and not fake.totals  # every roll of the round made, once
    with pytest.raises(ValueError, match="原参数"):
        await server.battle_round("逃跑｜被攻击时闪避")  # another order cannot take the broken one's place
    resumed = reload(game, fake)
    await game.close()
    out = await server.battle_round(HIT)  # the same call again: nothing is rolled (the dice would run out)
    assert [x["attacker"] for x in out["battle"]["exchanges"]] == ["周衡", "楼梯口打盹的老人"]  # reported whole all the same
    loaded = resumed.state
    assert loaded.investigator.hp == 8 and loaded.encounter.round == 1 and resumed.world.rec.entities["warden"].life.hp == 10
    assert loaded.turn == 2 and not loaded.pending_mcp and not loaded.pending_resolution
    assert out["battle"]["now"]["round"] == 1 and out["battle"]["over"] is None
    await resumed.close()


async def test_a_creature_seen_first_costs_sanity_once_though_the_start_is_retried(table, fake, module):
    module.creatures = [HOUND]
    game, _, _ = table()
    fake.queue(30)  # 周衡's sanity holds
    shown = game.emit

    async def broken(event):
        if event["type"] == "sanity":
            raise ConnectionError("connection lost")
        await shown(event)

    game.emit = broken
    with pytest.raises(ConnectionError):
        await server.start_fight(["hound"], surprise="party")
    assert game.state.encounter is not None and game.state.pending_mcp and not fake.values
    game.emit = shown
    started = await server.start_fight(["hound"], surprise="party")  # begun already: the retry goes on, it is not refused
    battle = started["battle"]
    assert battle["seen"] == [{"name": "猎犬", "look": HOUND.description}] and len(started["sanity"]) == 1
    assert game.state.sightings == {"zhouheng": ["hound"]} and battle["now"]["kind"] == "fight" and not game.state.pending_mcp


async def test_running_from_a_creature_turns_the_fight_into_a_chase(table, fake, module):
    module.creatures = [HOUND]
    game, _, _ = table()
    fake.queue(30)
    started = await server.start_fight(["hound"], surprise="party")
    flee = next(c["order"] for c in started["battle"]["now"]["choices"] if c["order"].startswith("逃跑"))
    fake.queue(40, 40)  # 周衡 runs before it can move; asked, the hound gives chase; the chase's speeds: 8 and 6
    out = await server.battle_round(flee)
    battle = out["battle"]
    assert battle["over"] is None and any(line.startswith("【战斗转为追逐】") for line in battle["lines"])  # judged when it ends
    now = battle["now"]
    assert now["kind"] == "chase" and now["quarry"] == "party" and now["gap"] == 2  # it comes after them
    assert "全力奔跑" in [c["order"] for c in now["choices"]] and game.state.chase is not None and not fake.values


async def test_a_chase_runs_leg_by_leg_and_hiding_ends_it(table, fake):
    game, _, _ = table()
    fake.queue(40, 40)  # CON for speed: 8 and 8
    started = await server.start_chase(["warden"], party="逃", reason="守钟人提着灯追了出来")
    battle = started["battle"]
    now = battle["now"]
    assert battle["lines"][0].startswith("【追逐开始】") and any("速度 8" in line for line in battle["lines"])
    assert now["kind"] == "chase" and now["round"] == 0 and now["quarry"] == "party" and now["gap"] == 2
    assert {"全力奔跑", "躲藏", "设置障碍"} <= {c["order"] for c in now["choices"]}
    assert [r["hp"] for r in foes(now)] == [None] and server.scene()["battle"]["kind"] == "chase"
    # 周衡 (at 2) crosses the crates (闪避 10); the warden slips on the ice (敏捷 90) and loses 2
    fake.queue(10, 90).queue_totals(2)
    ran = await server.battle_round("全力奔跑")
    now = ran["battle"]["now"]
    places = {r["id"]: r["pos"] for r in now["runners"]}
    assert places == {"zhouheng": 3, "warden": 1} and now["round"] == 1 and now["gap"] == 2
    assert any("越过了倒塌的木箱" in line for line in ran["battle"]["lines"])
    assert any("栽了跟头" in line for line in ran["battle"]["lines"]) and ran["battle"]["over"] is None
    fake.queue(10, 20)  # out of sight: 潜行 hard against 侦查 regular
    hid = await server.battle_round("躲藏")
    over = hid["battle"]["over"]
    assert over["kind"] == "chase" and over["result"] == "escaped" and hid["battle"]["now"] is None
    assert game.state.chase is None and not fake.values and not fake.totals


async def test_only_the_people_here_and_the_modules_creatures_can_be_fought(table, fake):
    game, _, _ = table()
    with pytest.raises(ValueError, match="preacher"):
        await server.start_fight(["preacher"])  # he is in the square
    with pytest.raises(ValueError, match="没有填对象"):
        await server.start_chase([" "])
    with pytest.raises(ValueError, match="逃"):
        await server.start_chase(["warden"], party="跑")
    game.state.investigator.conditions.append("昏迷")
    with pytest.raises(ValueError, match="无法行动"):
        await server.start_fight(["warden"])
    assert server.scene()["creatures"] == [] and server.scene()["battle"] is None and game.state.turn == 0
    tools = {tool.name: tool.inputSchema["properties"] for tool in await server.server.list_tools()}
    assert tools["start_fight"]["foes"]["type"] == tools["start_chase"]["others"]["type"] == "array"
    assert set(tools["battle_round"]) == {"order", "skill", "difficulty", "harm", "target"}
