"""Fights and chases as the game runs them (engine/fighting.py), through the turn graph."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.agents.cast import Action, Outcome, Ruling
from autokeeper.engine import graph as turn_graph
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_engine import FakeCast, make, run_turn
from .test_tui import canned  # noqa: F401  (a fixture, used by name)

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


def fight_screen(module):
    from autokeeper.engine import combat as battle
    from autokeeper.engine.game import Game
    from autokeeper.tui.screens.game import GameScreen

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)], slot="fight-screen")
    state.location, state.prologue, state.turn = "tower", "（开场已讲过）", 3
    state.add("keeper", "钟楼里只有齿轮的声音。")
    warden = battle.from_npc(module.npc("warden"))
    me = battle.from_investigator(state.investigators[0])
    state.fight = battle.Fight(fighters=[me, warden], place="tower", round=1)
    return GameScreen(module, state, resume=True), me, warden


HURT = {"type": "fight_act", "round": 1, "attacker": "warden", "attacker_name": "老守钟人", "attacker_side": "foe",
        "defender_side": "party", "hurt_side": "party", "kind": "melee", "attack": "徒手", "roll": None, "answer": None,
        "reaction": "none", "winner": "attacker", "damage": 2, "damage_text": "1D3 = 2", "effect": "", "extreme": False,
        "jammed": False, "empty": False, "notes": [], "line": "", "dice": None}


@pytest.mark.usefixtures("canned")
async def test_the_stage_shows_the_fight_and_offers_the_rounds_choices(module, fake):
    from autokeeper.engine import combat as battle
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.widgets.fight import Arena, BloodFlash
    from autokeeper.tui.widgets.panels import SceneArt

    from .test_tui import until

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, me, warden = fight_screen(module)
        state = screen.state
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        await pilot.pause(0.3)
        arena = screen.query_one(Arena)
        assert arena.display and screen.has_class("-fight")  # a save opened mid-fight comes back to it
        assert not screen.query_one(SceneArt).display  # the stage is the fight's
        assert await until(pilot, lambda: arena.canvas.looks)  # drawn, though asked twice while drawing
        picker = screen.query_one("#picker")
        assert await until(pilot, lambda: picker.display)
        assert screen._clock is not None and "秒" in str(picker.border_title)  # the choice is on a clock
        words = [picker.get_option_at_index(i).id for i in range(picker.option_count)]
        assert any(w.startswith("攻击 老守钟人（徒手）") for w in words) and any(w.startswith("逃跑") for w in words)
        await pilot.press("tab")  # the other way of answering a blow, every choice with it
        await pilot.pause()
        assert all("被攻击时反击" in picker.get_option_at_index(i).id for i in range(picker.option_count - 2))
        words = [picker.get_option_at_index(i).id for i in range(picker.option_count)]
        sent = []
        screen.send_line = sent.append  # what picking sends, without playing it
        picker.highlighted = 0
        await pilot.press("enter")
        await pilot.pause()
        assert sent == [words[0]] and not picker.display and screen._clock is None
        await screen.handle({**HURT, "defender": me.id, "defender_name": me.name, "hurt": me.id})
        await pilot.pause(0.05)
        assert isinstance(app.screen, BloodFlash)  # hurt: blood over the whole screen
        await pilot.press("x")  # typing goes on under it
        await pilot.pause(0.05)
        assert app.screen is screen
        assert screen.query(".fightcard")
        state.fight = None
        await screen.handle({"type": "fight_end", "end": "won", "text": "调查员一方占了上风"})
        await pilot.pause()
        assert not arena.display and not screen.has_class("-fight") and screen.query_one(SceneArt).display
        await screen.handle({"type": "chase_start", "chase": battle.chase_view(battle.Chase(
            runners=[me, warden], track=battle.default_track("street"), quarry="party")), "line": "【追逐开始】"})
        await pilot.pause()
        assert arena.display and arena.mode == "chase" and screen.has_class("-chase")  # the place, and what is behind you in it
        assert not screen.query_one(SceneArt).display and "追逐" in str(arena.query_one(".side").render())
        await screen.handle({"type": "chase_end", "end": "escaped", "text": "甩掉了"})
        await pilot.pause()
        assert not arena.display and screen.query_one(SceneArt).display


@pytest.mark.usefixtures("canned")
async def test_a_choice_not_made_in_time_only_defends(module, fake, monkeypatch):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens import game as screens

    from .test_tui import until

    monkeypatch.setattr(screens, "BATTLE_SECONDS", 0.6)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, _, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        picker = screen.query_one("#picker")
        assert await until(pilot, lambda: picker.display)
        sent = []
        screen.send_line = sent.append
        assert await until(pilot, lambda: sent, tries=20)
        assert sent[0].startswith("防守") and not picker.display


async def test_the_modules_creatures_are_drawn_once_for_all_its_games(module, tmp_path, monkeypatch):
    from pathlib import Path

    from PIL import Image

    from autokeeper import config, imagegen, paths, scenery
    from autokeeper.engine.game import Game
    from autokeeper.engine.visuals import Visuals
    from autokeeper.module.schema import Creature

    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(imagegen, "status", lambda: imagegen.Status(True, "", "codex"))
    asked = []

    async def make(text, target, *, kind, module, **kwargs):
        asked.append((kind, text))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (300, 400)).save(target)
        return Path(target)

    async def quiet(event):
        pass

    monkeypatch.setattr(imagegen, "make", make)
    module.creatures = [Creature(id="hound", name="廷达罗斯猎犬", description="从角落里渗出来的瘦长之物")]
    scenery.fill_creatures(module)
    assert scenery.painted(module.pictures["creature-hound"])  # painted by program until it is drawn
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)], slot="beasts")
    visuals = Visuals(module, state, config.Config(), quiet)
    visuals.creatures()
    await visuals.tasks["creatures"]
    drawn = paths.creatures(module.id) / "hound.jpg"
    assert module.pictures["creature-hound"] == str(drawn)
    assert asked[0][0] == "creature" and "pure black background" in asked[0][1] and "廷达罗斯猎犬" in asked[0][1]
    again = Visuals(module, state, config.Config(), quiet)
    again.creatures()
    await again.tasks["creatures"]
    assert len(asked) == 1  # once for the module, every game after shows it
    other = Module.load(EXAMPLE)
    other.creatures = module.creatures
    scenery.fill_creatures(other)
    assert other.pictures["creature-hound"] == str(drawn)


def test_a_chase_is_drawn_as_the_way_with_who_is_where_and_where_they_lose_you(module):
    from autokeeper.engine import combat as battle
    from autokeeper.engine.game import Game
    from autokeeper.tui.widgets.fight import chase_route

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)], slot="route")
    me, warden = battle.from_investigator(state.investigators[0]), battle.from_npc(module.npc("warden"))
    me.pos, warden.pos, me.speed, warden.speed, me.actions, warden.actions = 4, 2, 8, 9, 1, 2
    ch = battle.Chase(runners=[me, warden], track=battle.default_track("graveyard"), quarry="party", round=2)
    lines = chase_route(battle.chase_view(ch), 44, 24, 1, was={me.id: 2, warden.id: 0}).plain.splitlines()
    legs = [leg.name for leg in ch.track.legs]
    row = {name: next(line for line in lines if name in line) for name in legs}
    assert "在你身后 2 段" in lines[1] and "再拉开 1 段就甩掉了" in lines[2] and "可以躲藏" in lines[2]
    assert lines.index(row[legs[-1]]) < lines.index(row[legs[0]])  # the way ahead at the top
    assert "◆" in row[legs[4]] and "◉◉" in row[legs[2]] and "老守钟人" in row[legs[2]]
    assert "┆" in row[legs[3]] and "┆" in row[legs[1]]  # run through this round
    assert "到这里就甩开" in row[legs[5]] and "▦攀爬" in row[legs[4]]  # three ahead of it; the fence in the way
    assert any("每轮跑 2 段" in line for line in lines)
