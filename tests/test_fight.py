"""Fights and chases as the game runs them (engine/fighting.py), through the turn graph."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.agents.cast import Action, Outcome, Ruling
from autokeeper.engine import graph as turn_graph
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_engine import FakeCast, make, run_turn

THREAD = {"configurable": {"thread_id": "fight"}}


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)
HIT = Action(kind="attack", summary="周衡一拳打向老守钟人", npc="warden", target="老守钟人")


class Fighters(FakeCast):
    """The scripted cast, with the end of a fight ruled: the flags it is told to give."""

    def __init__(self, script, flags=()):
        super().__init__(script)
        self.flags, self.results = list(flags), []

    async def outcome(self, state, result):
        self.results.append(result)
        return Outcome(flags=self.flags)


def fighters(module, fake, script, flags=(), location="tower"):
    game, _, events = make(module, fake, script, location=location)
    cast = Fighters(script, flags)
    game.cast = cast
    cast.world = game.world
    return game, cast, events


async def test_a_blow_starts_a_fight_and_a_knockout_ends_it_with_its_flags(module, fake):
    module.npc("warden").stats = {"生命": 4, "体质": 30}
    module.flags["warden_down"] = "守钟人被打倒在地"
    ruling = Ruling(needs_roll=True, skill="格斗(斗殴)", combat=["warden"], minutes=0)
    game, cast, _ = fighters(module, fake, {"hit": (HIT, ruling)}, flags=["warden_down"])
    fake.queue(10, 60, 80).queue_totals(2)  # 周衡 hard; the warden fights back and fails; 2 damage; his CON fails
    app = turn_graph.build(game, InMemorySaver())
    out, interrupts = await run_turn(app, "hit", THREAD)
    st = game.state
    kinds = [e["type"] for e in out]
    assert not interrupts and kinds.index("fight_start") < kinds.index("fight_act") < kinds.index("fight_end")
    act = next(e for e in out if e["type"] == "fight_act")
    assert act["winner"] == "attacker" and act["damage"] == 2 and act["reaction"] == "fight"
    assert act["dice"]["value"] == 10  # the investigator's own roll goes to the dice tray
    assert st.fight is None and "warden_down" in st.flags  # over: the flag the outcome brought about
    assert game.world.rec.entities["warden"].status == "受伤"  # knocked out, written into the record
    assert "老守钟人" in cast.results[0] and "昏迷" in cast.results[0]
    report = cast.reports[-1]
    assert "【战斗 · 第 1 轮】" in report and "【战斗结束】" in report and "不复述数字" in report.replace("不要复述数字", "不复述数字")
    assert st.investigator.hp == st.investigator.hp_max  # he never got to swing back


async def test_a_round_goes_in_dex_order_and_the_stance_answers_the_blow(module, fake):
    ruling = Ruling(needs_roll=True, skill="格斗(斗殴)", combat=["warden"])
    game, cast, _ = fighters(module, fake, {"hit": (HIT, ruling)})
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90, 60, 20, 50).queue_totals(3)  # 周衡 misses; the warden fights back (no use); then hits; 周衡's dodge fails
    events, _ = await run_turn(app, "hit", THREAD)
    st = game.state
    inv = st.investigator
    assert st.fight is not None and st.fight.round == 1 and inv.hp == inv.hp_max - 3
    order = next(e for e in events if e["type"] == "fight_round")["order"]
    assert order == ["zhouheng", "warden"]  # DEX 65 before 50
    ruled = len(cast.ruled)
    fake.queue(10, 60, 20, 5).queue_totals(1)  # 周衡 hard: 1; the warden hits, 周衡 fights back with an extreme success
    events, _ = await run_turn(app, "攻击 老守钟人（徒手）｜被攻击时反击", THREAD)
    assert len(cast.ruled) == ruled  # an order in the screen's words needs no arbiter
    acts = [e for e in events if e["type"] == "fight_act"][-2:]
    assert acts[1]["reaction"] == "fight" and acts[1]["winner"] == "defender" and acts[1]["hurt"] == "warden"
    assert acts[1]["damage"] == 3 and "反击得手" in acts[1]["line"]  # the most fists can do
    assert inv.hp == inv.hp_max - 3 and st.fight.get("warden").hp == 11 - 1 - 3
    assert "【战斗 · 第 2 轮】" in cast.reports[-1]


async def test_a_quarry_much_faster_is_gone_at_once(module, fake):
    flee = Action(kind="move", summary="周衡拔腿就跑")
    ruling = Ruling(needs_roll=False, chase=["warden"], chase_party="逃")
    module.flags["got_away"] = "调查员从守钟人手里逃脱"
    game, cast, _ = fighters(module, fake, {"run": (flee, ruling)}, flags=["got_away"])
    fake.queue(40, 80)  # 周衡's CON regular: 8; the warden's fails: 7
    app = turn_graph.build(game, InMemorySaver())
    out, _ = await run_turn(app, "run", THREAD)
    kinds = [e["type"] for e in out]
    assert "chase_start" in kinds and "chase_end" in kinds and game.state.chase is None
    assert "got_away" in game.state.flags and "转眼就没了影" in cast.results[0]


async def test_a_chase_runs_leg_by_leg_and_hiding_ends_it(module, fake):
    flee = Action(kind="move", summary="周衡拔腿就跑")
    ruling = Ruling(needs_roll=False, chase=["warden"], chase_party="逃")
    game, cast, _ = fighters(module, fake, {"run": (flee, ruling)})
    app = turn_graph.build(game, InMemorySaver())
    # speeds 8 and 8; 周衡 (at 2) crosses the crates (闪避, 10); the warden slips on the ice (敏捷 90) and loses 2
    fake.queue(40, 40, 10, 90).queue_totals(2)
    await run_turn(app, "run", THREAD)
    ch = game.state.chase
    assert ch is not None and ch.round == 1
    me, warden = ch.get("zhouheng"), ch.get("warden")
    assert (me.pos, warden.pos) == (3, 1)
    assert any("堆满木箱的后院" in line for line in cast.reports[-1].splitlines())
    fake.queue(10, 20)  # out of sight: 潜行 hard against 侦查 regular
    out, _ = await run_turn(app, "躲藏", THREAD)
    assert game.state.chase is None and any(e["type"] == "chase_end" and e["end"] == "escaped" for e in out)
