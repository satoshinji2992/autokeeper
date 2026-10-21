"""Fights and chases as the game runs them (engine/fighting.py), through the turn graph."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from rich.cells import cell_len

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
    warden = game.world.rec.entities["warden"]  # knocked out by a major wound, written into the record as it is
    assert warden.status == "重伤" and warden.life.hp == 2 and {"重伤", "昏迷"} <= set(warden.life.conditions)
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
    fake.queue(10, 60, 20, 5).queue_totals(1, 2)  # 周衡 hard: 1; the warden hits, 周衡 fights back with an extreme success
    events, _ = await run_turn(app, "攻击 老守钟人（徒手）｜被攻击时反击", THREAD)
    assert len(cast.ruled) == ruled  # an order in the screen's words needs no arbiter
    acts = [e for e in events if e["type"] == "fight_act"][-2:]
    assert acts[1]["reaction"] == "fight" and acts[1]["winner"] == "defender" and acts[1]["hurt"] == "warden"
    # fighting back does normal damage however well it rolls (Keeper Rulebook p. 87): 1D3, not its most
    assert acts[1]["damage"] == 2 and "反击得手" in acts[1]["line"]
    assert inv.hp == inv.hp_max - 3 and st.fight.get("warden").hp == 11 - 1 - 2
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
    from autokeeper import config
    from autokeeper.engine import combat as battle
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.widgets.fight import Arena, BloodFlash
    from autokeeper.tui.widgets.panels import SceneArt

    from .test_tui import until

    config.load().battle_timer = True  # 设置: 战斗与追逐限时选择 (off unless switched on)
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
    from autokeeper import config
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens import game as screens

    from .test_tui import until

    config.load().battle_timer = True
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


def test_a_chase_is_drawn_as_the_way_run_and_in_sight_with_who_is_where(module):
    from autokeeper.engine import combat as battle
    from autokeeper.engine.game import Game
    from autokeeper.tui.widgets.fight import chase_route

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)], slot="route")
    me, warden = battle.from_investigator(state.investigators[0]), battle.from_npc(module.npc("warden"))
    me.pos, warden.pos, me.speed, warden.speed, me.actions, warden.actions = 4, 2, 8, 9, 1, 2
    ch = battle.Chase(runners=[me, warden], track=battle.default_track("graveyard"), quarry="party", round=2)
    text = chase_route(battle.chase_view(ch), 44, 24, 1, was={me.id: 2, warden.id: 0}).plain
    lines = text.splitlines()
    legs = [leg.name for leg in ch.track.legs]
    row = {name: next(line for line in lines if name in line) for name in legs[:7]}
    assert "在身后 2 段" in lines[1] and "再领先 1 段即可脱离" in lines[2] and "可躲藏" in lines[2]
    assert lines.index(row[legs[6]]) < lines.index(row[legs[0]])  # the way ahead at the top
    assert "◆" in row[legs[4]] and "◉◉" in row[legs[2]] and "老守钟人" in row[legs[2]]
    assert "┆" in row[legs[3]] and "┆" in row[legs[1]]  # run through this round
    assert "▦攀爬" in row[legs[4]] and "⚠敏捷" in row[legs[6]]  # the fence they are at; the next thing in the way
    # Past the frozen field (the first thing in the way ahead) nothing is known yet: not its name, not
    # where the way ends; and the stretch where they would lose it is a count, never a name.
    assert legs[7] not in text and "前方：尚未看清" in lines[4] and "脱离" not in row[legs[5]]
    assert any("每轮跑 2 段" in line for line in lines)
    me.pos = 6  # the end of the way in sight
    assert "跑出此路线即可脱离" in chase_route(battle.chase_view(ch), 44, 24).plain
    narrow = chase_route(battle.chase_view(ch), 28, 24).plain.splitlines()  # a narrow panel: shorter columns
    assert all(cell_len(line) <= 28 for line in narrow) and any("周衡" in line for line in narrow)


# -- windows over the fight: only the one in front ever goes (Textual's dismiss pops the top) ---------
OFFER = {"kind": "roll", "who": "周衡", "skill": "闪避", "target": 32, "difficulty": "常规", "reason": "老守钟人扑了过来"}


@pytest.mark.usefixtures("canned")
async def test_caught_in_a_chase_the_blood_then_its_face_and_the_screen_comes_back(module, fake, tmp_path, monkeypatch):
    from PIL import Image

    from autokeeper import imagery
    from autokeeper.engine import combat as battle
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.showcase import ShowcaseScreen
    from autokeeper.tui.widgets.fight import BloodFlash

    from .test_tui import until

    monkeypatch.setattr(imagery, "render", lambda *a, **k: "◉")  # the face, without chafa
    face = tmp_path / "warden.png"
    Image.new("RGB", (60, 80), (90, 40, 30)).save(face)
    module.pictures["face-warden"] = str(face)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, me, warden = fight_screen(module)
        st = screen.state
        st.fight, me.pos = None, 2
        st.chase = battle.Chase(runners=[me, warden], track=battle.default_track("street"), quarry="party", round=1)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        screen.busy = True  # the round is being settled: the engine ends the chase and starts the fight
        st.chase, st.fight = None, battle.Fight(fighters=[me, warden], place="tower", reason="追逐中被追上")
        await screen.handle({"type": "chase_end", "end": "caught", "text": "追逐结束：老守钟人追上了周衡", "hunter": "warden",
                             "hunter_name": "老守钟人", "portrait": "face-warden"})
        await screen.handle({"type": "fight_start", "fight": battle.view(st.fight), "line": "【战斗开始】周衡与老守钟人扭打在一起"})
        await pilot.pause(0.05)
        assert isinstance(app.screen, BloodFlash)  # the blood first
        assert await until(pilot, lambda: isinstance(app.screen, ShowcaseScreen))  # then its face, over everything
        assert not any(isinstance(s, BloodFlash) for s in app.screen_stack)  # the flash went before it came
        await pilot.pause(1.0)
        assert isinstance(app.screen, ShowcaseScreen)  # shown its time, not closed by the flash's going
        assert await until(pilot, lambda: app.screen is screen)
        screen.busy = False
        screen.query_one("#command").focus()
        await pilot.press("x")  # nothing left over the screen eating the keys
        await pilot.pause(0.1)
        assert app.screen is screen and screen.query_one("#command").value.endswith("x")


@pytest.mark.usefixtures("canned")
async def test_a_roll_asked_while_the_blood_drains_is_answered_and_the_turn_goes_on(module, fake):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.dialogs import HandoutModal, RollModal
    from autokeeper.tui.widgets.fight import BloodFlash

    from .test_tui import until

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, _, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        answered: list[str] = []

        async def turn() -> None:  # as the turn asks: out of the way, then the prompt (GameScreen._run_turn)
            await screen.make_way()
            answered.append(await app.push_screen_wait(RollModal(OFFER)))

        screen._flash(0.6)
        assert isinstance(app.screen, BloodFlash)
        screen.run_worker(turn(), group="turn")
        assert await until(pilot, lambda: isinstance(app.screen, RollModal))
        assert not any(isinstance(s, BloodFlash) for s in app.screen_stack)
        await pilot.pause(0.5)
        await pilot.press("enter")
        assert await until(pilot, lambda: answered == ["roll"])  # the turn waiting on it goes on
        # A window pushed straight over the flash: the flash drains under it, and waits to go.
        screen._flash(0.6)
        app.push_screen(HandoutModal("日记", "……钟声停了以后，它们就来了。", [], kind="信件"))
        await pilot.pause(0.8)  # the flash's time is up under the handout
        assert [type(s) for s in app.screen_stack[-2:]] == [BloodFlash, HandoutModal]
        await pilot.press("escape")  # the handout closed by the player, then the flash goes by itself
        assert await until(pilot, lambda: app.screen is screen)
        assert not any(isinstance(s, (BloodFlash, HandoutModal)) for s in app.screen_stack)


@pytest.mark.usefixtures("canned")
async def test_a_handout_over_a_big_scene_stays_until_closed_and_the_scene_goes_after_it(module, fake, tmp_path, monkeypatch):
    from PIL import Image

    from autokeeper import imagery
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.dialogs import HandoutModal
    from autokeeper.tui.modals.showcase import ShowcaseScreen

    from .test_tui import until

    monkeypatch.setattr(imagery, "render", lambda *a, **k: "◉")
    pic = tmp_path / "big.png"
    Image.new("RGB", (40, 30), (40, 40, 60)).save(pic)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, _, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        app.push_screen(ShowcaseScreen(str(pic), "钟楼顶上", seconds=0.5))
        await pilot.pause(0.2)
        app.push_screen(HandoutModal("日记", "……钟声停了以后，它们就来了。", [], kind="信件"))
        await pilot.pause(0.8)  # the picture's time ran out under the handout
        assert [type(s) for s in app.screen_stack[-2:]] == [ShowcaseScreen, HandoutModal]
        await pilot.press("escape")
        assert await until(pilot, lambda: app.screen is screen)  # the handout closed, the picture went after it


# -- the round's list and its clock -------------------------------------------------------------------
@pytest.mark.usefixtures("canned")
async def test_an_order_typed_over_the_list_closes_it_and_nothing_of_it_is_left_after_the_fight(module, fake):
    import asyncio

    from autokeeper import config
    from autokeeper.tui.app import AutoKeeperApp

    from .test_tui import until

    config.load().battle_timer = True
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, me, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        picker, box = screen.query_one("#picker"), screen.query_one("#command")
        assert await until(pilot, lambda: picker.display and screen._clock is not None)
        sent, seen = [], []

        async def turn(text: str) -> None:  # the round being settled, then the fight is over
            sent.append(text)
            screen._busy(True)
            await asyncio.sleep(0.4)
            seen.append(screen.state.fight.get(me.id).stance)
            screen.state.fight = None
            await screen.handle({"type": "fight_end", "end": "won", "text": "调查员一方占了上风"})
            screen._busy(False)

        screen.play = lambda text: screen.run_worker(turn(text), group="turn", exclusive=True)
        await pilot.press("我")  # typed straight in, as players do
        assert await until(pilot, lambda: box.has_focus and box.value == "我")
        assert not picker.display and not screen._battle_picking
        assert screen._clock is None  # an order of their own takes the time it takes
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("tab")  # mid-turn: no stance of the fight being settled changes
        assert await until(pilot, lambda: not screen.busy and seen)
        assert sent == ["我"] and seen == ["dodge"] and me.stance == "dodge"
        await pilot.pause(0.2)
        assert not picker.display and not screen._battle_picking  # nothing of the fight's list after it
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert sent == ["我"]


@pytest.mark.usefixtures("canned")
async def test_the_rounds_clock_stands_still_behind_other_windows_and_tab_keeps_its_deadline(module, fake, monkeypatch):
    from autokeeper import config
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens import game as screens

    from .test_tui import until

    config.load().battle_timer = True
    monkeypatch.setattr(screens, "BATTLE_SECONDS", 0.8)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, me, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        picker = screen.query_one("#picker")
        assert await until(pilot, lambda: picker.display)
        sent: list[str] = []
        screen.send_line = sent.append
        await pilot.press("f1")  # the help over the screen: the round waits
        await pilot.pause(1.4)
        assert sent == [] and picker.display
        await pilot.press("escape")
        assert await until(pilot, lambda: sent, tries=20)  # back: it runs out from where it stood
        assert sent[0].startswith("防守") and not picker.display
        screen._offer_orders()  # the next round
        for _ in range(4):  # Tab does not start it over
            await pilot.pause(0.3)
            await pilot.press("tab")
        assert len(sent) == 2 and me.stance == "dodge"  # the stance is in the words, not in the fight
        screen._offer_orders()
        await pilot.pause(0.2)
        config.load().battle_timer = False  # 设置: 限时选择 off, with a clock running
        screen._settings_saved(app.art_style)
        await pilot.pause(1.2)
        assert len(sent) == 2 and screen._clock is None and picker.display and "秒" not in str(picker.border_title)


@pytest.mark.usefixtures("canned")
async def test_an_ending_in_the_middle_of_a_fight_takes_the_stage_back(module, fake):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.widgets.fight import Arena
    from autokeeper.tui.widgets.panels import SceneArt

    from .test_tui import until

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, _, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        picker = screen.query_one("#picker")
        assert await until(pilot, lambda: picker.display and screen._beating)
        assert screen._clock is None and "秒" not in str(picker.border_title)  # no clock unless switched on (设置)
        screen.state.ending = "lost"  # every investigator down: the game is over, the fight still in the state
        await screen.handle({"type": "ending", "title": "钟声", "text": "钟楼重归寂静。"})
        await pilot.pause(0.2)
        assert not picker.display and screen._clock is None and not screen.query_one(Arena).display
        assert screen.query_one(SceneArt).display and not screen.has_class("-fight")
        screen._offer_orders()
        assert not picker.display
        assert await until(pilot, lambda: not screen._beating)


# -- the stage on a small terminal ---------------------------------------------------------------------
def _crowd(module):
    """Two investigators against the warden and two of his helpers."""
    from autokeeper.engine import combat as battle

    screen, me, warden = fight_screen(module)
    st = screen.state
    friend = st.investigators[0].model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    st.investigators.append(friend)
    helpers = [warden.model_copy(update={"id": f"helper{k}", "name": f"守钟人的帮手{k}"}) for k in (1, 2)]
    st.fight = battle.Fight(fighters=[me, battle.from_investigator(friend), warden, *helpers], place="tower", round=1)
    return screen


def _all_on_screen(screen, height: int) -> None:
    """The input box above the footer, the round's list above the box, a few rows of story."""
    box, picker, story = screen.query_one("#command"), screen.query_one("#picker"), screen.query_one("#story")
    assert box.region.bottom <= height - 1, (box.region, height)
    assert picker.display and picker.region.height >= 5 and picker.region.bottom <= box.region.y
    assert story.region.height >= 3


@pytest.mark.usefixtures("canned")
@pytest.mark.parametrize("size", [(80, 24), (100, 30)])
async def test_on_a_small_terminal_the_rounds_list_and_the_box_stay_on_screen(module, fake, size):
    from autokeeper.tui.app import AutoKeeperApp

    from .test_tui import until

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=size) as pilot:
        screen = _crowd(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        assert await until(pilot, lambda: screen.query_one("#picker").display)
        await pilot.pause(0.3)
        _all_on_screen(screen, size[1])
        side = str(screen.query_one(".side").render())
        assert side.count("11/11") == 2 and "调查员" in side and "你们" not in side  # every investigator's blood bar


@pytest.mark.usefixtures("canned")
async def test_a_fight_shrunk_mid_round_fits_its_new_size_and_a_narrow_chase_keeps_its_picture(module, fake):
    from autokeeper.engine import combat as battle
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.widgets.fight import Arena

    from .test_tui import until

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, me, warden = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        assert await until(pilot, lambda: screen.query_one("#picker").display)
        arena = screen.query_one(Arena)
        assert await until(pilot, lambda: arena.region.height == 22)  # half the screen when it has room
        await pilot.resize_terminal(100, 24)
        await pilot.pause(0.5)
        _all_on_screen(screen, 24)
        assert arena.region.height < 22
        screen.state.fight, me.pos = None, 2
        await screen.handle({"type": "fight_end", "end": "fled", "text": "调查员逃离了"})
        screen.state.chase = battle.Chase(runners=[me, warden], track=battle.default_track("street"), quarry="party")
        await screen.handle({"type": "chase_start", "chase": battle.chase_view(screen.state.chase), "line": "【追逐开始】"})
        await pilot.resize_terminal(96, 30)  # the right column is up: the route narrows, the picture keeps room
        await pilot.pause(0.5)
        assert arena.canvas.size.width >= 28 and arena.query_one(".side").size.width >= 26


# -- what a fight costs the screen, and what the settings stop -------------------------------------------
@pytest.mark.usefixtures("canned")
async def test_a_fight_paints_its_picture_without_layouts_and_holds_still_while_the_player_types(module, fake, monkeypatch):
    from textual.css.stylesheet import Stylesheet

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.widgets.fight import Arena, Canvas
    from autokeeper.tui.widgets.panels import SceneArt

    from .test_tui import until

    painted, restyled = [], []
    real_update, real_restyle = Canvas.update, Stylesheet.update_nodes

    def update(self, content="", *, layout=True):
        painted.append(layout)
        real_update(self, content, layout=layout)

    def restyle(self, nodes, animate=False):
        nodes = list(nodes)
        restyled.append(len(nodes))
        return real_restyle(self, nodes, animate=animate)

    monkeypatch.setattr(Canvas, "update", update)
    monkeypatch.setattr(Stylesheet, "update_nodes", restyle)
    tower = module.location("tower")
    tower.art, tower.weather = "square", ["rain"]  # a picture with weather behind the stage
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, _, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        canvas, scene = screen.query_one(Arena).canvas, screen.query_one(SceneArt)
        assert await until(pilot, lambda: canvas.looks and len(painted) > 3 and scene._atmo is not None)
        assert not any(painted[1:])  # frames painted, never a layout of the screen for one
        weather = []
        monkeypatch.setattr(SceneArt, "_paint", lambda self, *a, **k: weather.append(1))
        restyled.clear()
        await pilot.pause(1.5)
        assert not weather  # the hidden scene composes nothing
        assert restyled and max(restyled) < 40  # a heartbeat restyles the stage, not the whole screen's widgets
        await pilot.press("我")  # the player types: the stage holds still
        await pilot.pause(0.2)
        painted.clear()
        await pilot.pause(0.8)
        assert not painted
        canvas.hold, canvas.moving = 0.0, False  # 设置's animations off: a still picture is painted once
        await pilot.pause(0.3)
        painted.clear()
        await pilot.pause(0.6)
        assert not painted


@pytest.mark.usefixtures("canned")
async def test_with_animations_off_nothing_lurches_or_claws_and_without_big_scenes_no_face_comes_up(module, fake, tmp_path,
                                                                                                    monkeypatch):
    from PIL import Image

    from autokeeper import config, imagery
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.showcase import ShowcaseScreen
    from autokeeper.tui.widgets.fight import Arena
    from autokeeper.tui.widgets.panels import SceneArt

    from .test_tui import until

    monkeypatch.setattr(imagery, "render", lambda *a, **k: "◉")
    face = tmp_path / "face.png"
    Image.new("RGB", (40, 60), (90, 40, 30)).save(face)
    module.pictures["face-warden"] = str(face)
    cfg = config.load()
    cfg.animations = False
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, me, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        screen._settings_saved(app.art_style)  # 设置 saved with animations off
        canvas = screen.query_one(Arena).canvas
        assert not screen.query_one(SceneArt).effects and not canvas.moving  # at once, not at the next blow
        await screen.handle({**HURT, "defender": me.id, "defender_name": me.name, "hurt": me.id})
        await pilot.pause(0.05)
        assert screen.query_one("#main").styles.offset.x.value == 0 and not canvas.claw_from and not canvas.lunge_until
        assert app.screen is screen  # no flood of red either (the stage's own flash instead)
        screen._title("战斗")
        await pilot.pause(0.1)
        assert screen.query(".battle-title")  # the word stands still for a moment, without the glitch
        assert not screen.query_one("#stage").has_class("-beat")
        await pilot.pause(1.5)
        assert not screen.query(".battle-title") and not screen._beat_on  # no heartbeat
        cfg.showcase = False  # 大场景 off: what is first seen does not come up over the whole screen
        await screen._reveal("face-warden", "老守钟人", 1.0)
        await pilot.pause(0.2)
        assert not isinstance(app.screen, ShowcaseScreen)


@pytest.mark.usefixtures("canned")
async def test_a_picture_the_canvas_cannot_draw_is_the_shape_in_the_dark_not_the_app_gone(module, fake, tmp_path, monkeypatch):
    import subprocess

    from PIL import Image

    from autokeeper import imagery
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.widgets import fight as stage

    from .test_tui import until

    def fails(*args, **kwargs):
        raise subprocess.TimeoutExpired("chafa", 10)

    monkeypatch.setattr(imagery, "available", lambda: True)
    monkeypatch.setattr(stage, "variants", fails)
    face = tmp_path / "warden.png"
    Image.new("RGB", (40, 60), (90, 40, 30)).save(face)
    module.pictures["warden"] = str(face)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen, _, _ = fight_screen(module)
        screen.state.fight.fighters[1].portrait = "warden"
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        canvas = screen.query_one(stage.Arena).canvas
        assert await until(pilot, lambda: canvas.looks)
        assert "●" in canvas.looks[""].plain and app.is_running  # two eyes in the dark


@pytest.mark.usefixtures("canned")
async def test_the_story_tells_each_exchange_once_and_a_wound_on_its_card(module, fake):
    from autokeeper.engine import combat as battle
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.game import GameScreen

    from .test_tui import story_text, until

    ruling = Ruling(needs_roll=True, skill="格斗(斗殴)", combat=["warden"])
    game, _, _ = fighters(module, fake, {"hit": (HIT, ruling)})
    fake.queue(90, 60, 20, 50).queue_totals(3)  # 周衡 misses; the warden hits him for 3
    events, _ = await run_turn(turn_graph.build(game, InMemorySaver()), "hit", THREAD)
    acts = [e for e in events if e["type"] == "fight_act"]
    assert len(acts) == 2 and any(e["type"] == "damage" for e in events)
    state = game.state.model_copy(deep=True)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(150, 44)) as pilot:
        screen = GameScreen(module, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        for ev in events:  # the round as the screen hears it
            if ev["type"] in ("damage", "fight_round", "fight_act", "fight_state"):
                await screen.handle(ev)
        await screen.handle({"type": "fight_state", "fight": battle.view(state.fight),
                             "lines": [acts[1]["line"], "周衡还吊着一口气（体质 30）"]})  # the round's end: something new
        await pilot.pause(0.3)
        text = story_text(screen)
        assert text.count("受到 3 点伤害") == 1  # on the warden's card, not again as a line, nor as a ✚ line
        assert not any(act["line"] in text for act in acts)  # each exchange is its card
        assert "还吊着一口气" in text  # what the cards do not say still comes


async def test_a_turn_cut_short_after_its_fight_closed_only_sends_what_it_owed(module, fake):
    """The user's review, 2026-10-21: the ending notice failed, the retry opened the fight again and
    the warden, knocked out, struck once more."""
    from autokeeper.engine import combat as battle

    module.events, module.endings = [], []
    game, _, events = fighters(module, fake, {})
    me = battle.from_investigator(game.state.investigator)
    foe = battle.from_npc(module.npc("warden"))
    foe.hp = 1
    game.state.fight = battle.Fight(fighters=[me, foe], place="tower", started=1, orders={foe.id: battle.Order(do="hold")})
    action = Action(kind="attack", summary="攻击老守钟人", npc=foe.id,
                    order=battle.Order(do="attack", target=foe.id, weapon="徒手").model_dump())
    ruling = Ruling(needs_roll=True, skill="格斗(斗殴)")
    original = game.emit

    async def broken(ev):
        if ev["type"] == "fight_end":
            raise ConnectionError("the ending notice did not go out")
        await original(ev)

    game.emit = broken
    fake.queue(10, 90).queue_totals(1)
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    assert game.state.fight is None
    game.emit = original
    hp = game.state.investigator.hp
    resumed = await game.resolve(action, ruling)  # nothing queued: a die thrown again would fail here
    assert game.state.fight is None and game.state.investigator.hp == hp and not resumed.checks
    assert [e["type"] for e in events].count("fight_start") == 0 and any(e["type"] == "fight_end" for e in events)
