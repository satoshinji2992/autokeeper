"""Fights and chases as the game runs them (engine/fighting.py), through the turn graph."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.agents.cast import Action, Outcome, Ruling
from autokeeper.engine import fighting
from autokeeper.engine import graph as turn_graph
from autokeeper.module.schema import Creature, Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.chase import Leg, Obstacle

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


# -- what the module scripts: an event's attack, the way a chase from a place runs -------------------
AMBUSH = Event(id="e_ambush", title="守钟人扑了上来", when="D1 14:05", text="守钟人从楼梯口扑了过来。",
               read_aloud="楼梯口的人影一下子扑了过来。", fight=["warden"], surprise="party")
WAIT = Action(kind="wait", summary="周衡在楼梯口等一会儿")
STAIRS = [Leg(name="齿轮间门口", look="咔哒声就在头顶"),
          Leg(name="又窄又陡的楼梯", obstacle=Obstacle(kind="hazard", what="陡窄的楼梯", skill="敏捷", harm="1D3")),
          Leg(name="楼梯口", look="守钟人打盹的椅子翻倒在地"),
          Leg(name="钟楼大门", obstacle=Obstacle(kind="barrier", what="沉重的木门", skill="力量")),
          Leg(name="广场", look="告示栏在雾里只剩一个影子"),
          Leg(name="去报社的街", look="路灯一盏接一盏")]


async def test_an_event_that_scripts_an_attack_begins_the_fight_and_the_next_input_is_its_first_round(module, fake):
    module.events.append(AMBUSH.model_copy())
    game, cast, _ = fighters(module, fake, {"wait": (WAIT, Ruling(needs_roll=False, minutes=10))})
    app = turn_graph.build(game, InMemorySaver())
    out, _ = await run_turn(app, "wait", THREAD)  # 14:10: the attack at 14:05 has come
    st = game.state
    kinds = [e["type"] for e in out]
    assert kinds.index("event") < kinds.index("fight_start") and "fight_act" not in kinds  # begun, nothing settled yet
    assert st.fight is not None and st.fight.round == 0 and st.fight.get("zhouheng").surprised
    assert "【战斗开始】" in cast.reports[-1] and fighting.BEGIN_TELL in cast.reports[-1]
    ruled = len(cast.ruled)
    fake.queue(90, 60, 10).queue_totals(2)  # 周衡 misses, the warden fights back for nothing, then strikes 周衡, who cannot answer
    out, _ = await run_turn(app, "攻击 老守钟人（徒手）｜被攻击时闪避", THREAD)
    assert len(cast.ruled) == ruled and st.fight.round == 1  # the first round's order, with no arbiter
    blow = [e for e in out if e["type"] == "fight_act"][-1]
    assert blow["attacker"] == "warden" and blow["reaction"] == "none" and st.investigator.hp == st.investigator.hp_max - 2
    assert not st.fight.get("zhouheng").surprised  # the first round only


async def test_a_scripted_attack_waits_for_someone_to_meet_it_and_joins_a_fight_going_on(module, fake):
    from autokeeper.engine.game import Turn

    module.events.append(AMBUSH.model_copy())
    game, _, events = fighters(module, fake, {})
    game.state.investigator.add_condition("昏迷")  # nobody here who can still act: nothing begins
    await game._fire(module.events[-1], Turn())
    assert game.state.fight is None and "e_ambush" in game.state.fired
    game.state.investigator.conditions.clear()
    module.npc("warden").status = "死亡"
    second = AMBUSH.model_copy(update={"id": "e_again"})
    await game._fire(second, Turn())
    assert game.state.fight is None  # the dead attack no one
    module.npc("warden").status = ""
    await game._fire(AMBUSH.model_copy(update={"id": "e_third"}), Turn())
    assert [f.id for f in game.state.fight.fighters] == ["zhouheng", "warden"]
    turn = Turn()
    await game._fire(Event(id="e_more", title="布道者也冲了上来", when="flag:x", text="", fight=["preacher", "warden"]), turn)
    assert [f.id for f in game.state.fight.fighters] == ["zhouheng", "warden", "preacher"]  # joined, nobody twice
    assert any("布道者也扑了上来" in line for line in turn.fight) and events[-1]["type"] == "fight_state"
    assert game.world.rec.entities["preacher"].at == game.state.location  # the record has him come where they are


async def test_a_resumed_turn_does_not_begin_the_scripted_fight_again(module, fake):
    from .test_resolution_recovery import reload_game, snapshots

    module.events.append(AMBUSH.model_copy())
    game, _, _ = make(module, fake, {}, location="tower")
    saved = snapshots(game)

    async def broken(event: dict) -> None:
        if event["type"] == "fight_start":
            raise ConnectionError("disconnected as the fight began")

    game.emit = broken
    ruling = Ruling(needs_roll=False, minutes=10)
    with pytest.raises(ConnectionError):
        await game.resolve(WAIT, ruling)
    resumed, events = reload_game(module, saved[-1], fake)
    assert resumed.state.fight is not None  # it began before the line broke
    turn = await resumed.resolve(WAIT, ruling)
    assert [f.id for f in resumed.state.fight.fighters] == ["zhouheng", "warden"]
    assert sum(e["type"] == "fight_start" for e in events) == 1 and turn.fight.count(turn.fight[0]) == 1
    assert not resumed.state.pending_resolution
    await game.close()
    await resumed.close()


async def test_a_creature_the_event_already_paid_for_is_shown_not_paid_for_twice(module, fake):
    from autokeeper.engine.game import Turn

    module.creatures.append(Creature(id="coil", name="齿轮背后的藤", description="缠在齿轮背后、还在动的东西", sanity="1/1D6",
                                     stats={"生命": 9, "敏捷": 60}, attacks=["缠绕 45%，伤害 1D4"]))
    seen = Event(id="e_coil", title="藤爬了出来", when="flag:x", text="", sanity="0/1D3", fight=["coil"])
    game, _, events = fighters(module, fake, {})
    fake.queue(10)  # the event's own sanity roll, passed: the only one
    await game._fire(seen, Turn())
    kinds = [e["type"] for e in events]
    assert kinds.count("sanity") == 1 and "reveal" in kinds and game.state.seen == ["coil"]
    assert game.state.fight.get("coil").attacks[0].name == "缠绕"


async def test_a_chase_runs_the_modules_way_from_the_place_or_the_place_it_is_in(module, fake):
    from autokeeper.engine.game import Turn

    module.location("square").chase = STAIRS  # the tower is in the square: a chase from it runs this way too
    hounds = Event(id="e_run", title="守钟人追了上来", when="flag:x", text="", chase=["warden"])
    game, _, events = fighters(module, fake, {})
    fake.queue(40, 40)  # both CON regular: speeds 8 and 8, no one gone at once
    await game._fire(hounds, Turn())
    ch = game.state.chase
    assert ch is not None and ch.quarry == "party" and [leg.name for leg in ch.track.legs] == [leg.name for leg in STAIRS]
    ch.track.legs[3].obstacle.open = True  # what a chase does to its way stays in that chase
    assert not module.location("square").chase[3].obstacle.open
    assert any(e["type"] == "chase_start" for e in events)
    game.state.chase = None
    from autokeeper.engine import combat as battle

    fight = battle.Fight(fighters=[battle.from_investigator(game.state.investigator), battle.from_npc(module.npc("warden"))])
    fake.queue(40, 40)  # the runners' CON for speed
    game._chase_from_fight(fight, ["zhouheng"], ["warden"])
    assert game.state.chase.track.legs[1].name == "又窄又陡的楼梯"  # fled from a fight: the same way
    module.location("square").chase = STAIRS[:3]  # too short for the rules: the one for this kind of place
    game.state.chase = None
    fake.queue(40, 40)
    game._chase_from_fight(fight, ["zhouheng"], ["warden"])
    assert game.state.chase.track.legs[0].name != "齿轮间门口"


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
