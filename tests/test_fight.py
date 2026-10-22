"""Fights and chases as the game runs them (engine/encounter), through the turn graph, and the stage the
screens draw them on. A chase's rules in detail: tests/test_encounter_chase.py."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from rich.cells import cell_len

from autokeeper.agents.cast import Action, Ruling
from autokeeper.agents.encounter import EncounterHint
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.encounter import telling, views
from autokeeper.engine.encounter.plan import AttackPick, DeclaredAction, EncounterStart, EncounterTurnPlan
from autokeeper.module.schema import Creature, Event, Leg, Module, Obstacle, Stake
from autokeeper.paths import EXAMPLE
from autokeeper.tui.widgets.fight import chase_route

from .encounter_kit import THREAD, Keeper, arrange, attack, play
from .test_engine import make
from .test_tui import canned  # noqa: F401  (a fixture, used by name)


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


HIT = Action(kind="attack", summary="周衡一拳打向老守钟人", npc="warden", target="老守钟人")
PUNCH = DeclaredAction(do="attack", attacks=[AttackPick(target="warden", with_="natural:徒手")], summary="周衡一拳打向守钟人")
WARDEN = arrange("warden", attack("zhouheng", summary="守钟人挥拳还击"))


def fighters(module, fake, script, *, starts=(), hints=None, location="tower", **modes):
    """A game in the clock tower with the scripted keeper: the everyday script, the encounters it
    announces (hints, starts) and the encounter plan's answers by mode."""
    game, _, events = make(module, fake, {}, location=location)
    cast = Keeper(script, hints=hints or {}, starts=list(starts), **modes)
    game.cast, cast.world = cast, game.world
    return game, cast, events


def fight_now(text: str = "hit"):
    return {text: EncounterHint(mode="fight", **{"with": ["warden"]})}


def opening(stakes=()) -> EncounterStart:
    return EncounterStart(mode="fight", foes=["warden"], pace=6, reason="周衡动了手", opening=PUNCH, npcs=[WARDEN], stakes=list(stakes))


async def test_a_blow_starts_a_fight_and_a_knockout_ends_it_with_its_stakes(module, fake):
    module.npc("warden").stats = {"生命": 4, "体质": 30}
    module.flags["warden_down"] = "守钟人被打倒在地"
    game, cast, _ = fighters(module, fake, {"hit": (HIT, Ruling(needs_roll=False))}, hints=fight_now(),
                             starts=[opening([Stake(when="down:warden", flags=["warden_down"])])])
    fake.queue(10, 60, 80).queue_totals(2)  # 周衡 hard; the warden fights back and fails; 2 damage; his CON fails
    app = turn_graph.build(game, InMemorySaver())
    out, asked = await play(app, "hit")
    st = game.state
    kinds = [e["type"] for e in out]
    assert not asked and kinds.index("fight_start") < kinds.index("fight_act") < kinds.index("fight_end")
    act = next(e for e in out if e["type"] == "fight_act")
    assert act["winner"] == "attacker" and act["damage"] == 2 and act["reaction"] == "fight_back"
    assert act["dice"]["value"] == 10  # the investigator's own roll goes to the dice tray
    assert st.encounter is None and "warden_down" in st.flags  # over, archived: the flag its stake brought about
    warden = game.world.rec.entities["warden"]  # knocked out by a major wound, written into the record as it is
    assert warden.status == "重伤" and warden.life.hp == 2 and {"重伤", "昏迷"} <= set(warden.life.conditions)
    report = cast.reports[-1]
    assert "【战斗 · 第 1 轮】" in report and "【战斗结束】" in report and "不复述数字" in report.replace("不要复述数字", "不复述数字")
    assert st.investigator.hp == st.investigator.hp_max  # he never got to swing back


async def test_a_round_goes_in_dex_order_and_the_answer_meets_the_blow(module, fake):
    game, cast, _ = fighters(module, fake, {"hit": (HIT, Ruling(needs_roll=False))}, hints=fight_now(), starts=[opening()],
                             arrange=lambda *a: EncounterTurnPlan(npcs=[WARDEN]))
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90, 60, 20, 50).queue_totals(3)  # 周衡 misses; the warden fights back (no use); then hits; 周衡's dodge fails
    events, asked = await play(app, "hit", answers=["dodge"])  # the first blow at him: he is asked, before its dice
    st = game.state
    inv = st.investigator
    assert [a["kind"] for a in asked] == ["reaction"]
    assert st.encounter.round == 1 and inv.hp == inv.hp_max - 3
    order = next(e for e in events if e["type"] == "fight_round")["order"]
    assert order == ["zhouheng", "f1"]  # DEX 65 before 50; the warden by his seat, never his own id
    ruled = len(cast.ruled)
    fake.queue(10, 60, 20, 5).queue_totals(1, 2)  # 周衡 hard: 1; the warden hits, 周衡 fights back with an extreme success
    assert game.set_react("zhouheng", "fight_back") == ""  # Tab beside the list: no action spent, no model asked
    assert len(cast.ruled) == ruled and game.state.encounter.round == 1
    events, asked = await play(app, "攻击 楼梯口打盹的老人（徒手，1D3）", thread={**THREAD, "configurable": {"thread_id": "r2"}})
    assert len(cast.ruled) == ruled and not asked  # a button needs no arbiter; the answer set beside it stands
    acts = [e for e in events if e["type"] == "fight_act"][-2:]
    assert acts[1]["reaction"] == "fight_back" and acts[1]["winner"] == "defender" and acts[1]["hurt"] == "f1"
    # fighting back does normal damage however well it rolls (Keeper Rulebook p. 87): 1D3, not its most
    assert acts[1]["damage"] == 2 and "反击得手" in acts[1]["line"]
    assert inv.hp == inv.hp_max - 3 and game.world.rec.entities["warden"].life.hp == 11 - 1 - 2
    assert "【战斗 · 第 2 轮】" in cast.reports[-1]


RUN = Action(kind="move", summary="周衡拔腿就跑")
CHASE = {"run": EncounterHint(mode="chase", **{"with": ["warden"]})}


def chased(stakes=()) -> EncounterStart:
    return EncounterStart(mode="chase", foes=["warden"], quarry="party", pace=15, reason="守钟人追了上来", stakes=list(stakes))


async def test_a_quarry_much_faster_is_gone_at_once(module, fake):
    module.flags["got_away"] = "调查员从守钟人手里逃脱"
    game, _cast, _ = fighters(module, fake, {"run": (RUN, Ruling(needs_roll=False))}, hints=CHASE,
                             starts=[chased([Stake(when="escaped:players", flags=["got_away"])])])
    fake.queue(40, 80)  # 周衡's CON regular: 8; the warden's fails: 7
    app = turn_graph.build(game, InMemorySaver())
    out, _ = await play(app, "run")
    kinds = [e["type"] for e in out]
    assert "chase_start" in kinds and "chase_end" in kinds and game.state.encounter is None
    assert "got_away" in game.state.flags and game.state.encounter_log[-1].ended.how == "fled"


async def test_a_chase_begins_on_the_map_and_hiding_ends_it(module, fake):
    """No route written for the tower: the chase runs over the map around it; hiding works at any time
    (p. 119), and a quarry every pursuer lost has got away."""
    game, cast, _ = fighters(module, fake, {"run": (RUN, Ruling(needs_roll=False))}, hints=CHASE, starts=[chased()],
                             arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(
                                 do="pursue", target="zhouheng", summary="守钟人追上去"), react="dodge")]))
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(40, 40)  # speeds 8 and 8
    await play(app, "run")
    enc = game.state.encounter
    assert enc.mode == "chase" and enc.chase.site.source == "location_graph"
    assert [n.id for n in enc.chase.site.nodes] == ["tower", "square", "office"] and enc.chase.site.nodes[-1].exit
    assert any(label == "躲藏" for label, _note in views.table(game)["choices"]["zhouheng"])
    fake.queue(10)  # 潜行 against the warden's Spot Hidden (25: regular)
    out, _ = await play(app, "躲藏", thread={**THREAD, "configurable": {"thread_id": "c2"}})
    assert any(e["type"] == "chase_end" and e["end"] == "fled" for e in out)
    assert game.state.encounter is None and game.state.encounter_log[-1].ended.how == "fled"
    assert "躲了起来" in cast.reports[-1]


async def test_words_in_a_chase_are_the_keeper_plans_never_parsed(module, fake):
    """The review: in a chase, 打 at the start of the words turned to fight."""
    door = EncounterTurnPlan(action=DeclaredAction(do="other", summary="周衡想推开路边的门躲进去"))
    game, cast, _ = fighters(module, fake, {"run": (RUN, Ruling(needs_roll=False))}, hints=CHASE, starts=[chased()], act=door)
    cast.ruling_for["周衡想推开路边的门躲进去"] = Ruling(needs_roll=False, reason="门没锁")
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(40, 40)
    await play(app, "run")
    out, _ = await play(app, "打开门躲进去", thread={**THREAD, "configurable": {"thread_id": "c3"}})
    assert ("act", "打开门躲进去") in [c[:2] for c in cast.calls] and game.state.encounter.mode == "chase"
    assert not any(e["type"] == "fight_start" for e in out) and "周衡想推开路边的门躲进去" in cast.reports[-1]


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
    game, cast, _ = fighters(module, fake, {"wait": (WAIT, Ruling(needs_roll=False, minutes=10))},
                             arrange=lambda *a: EncounterTurnPlan(npcs=[WARDEN], pace=6))
    app = turn_graph.build(game, InMemorySaver())
    out, _ = await play(app, "wait")  # 14:10: the attack at 14:05 has come
    st = game.state
    kinds = [e["type"] for e in out]
    assert kinds.index("event") < kinds.index("fight_start") and "fight_act" not in kinds  # begun, nothing settled yet
    enc = st.encounter
    assert enc is not None and enc.round == 0 and enc.get("zhouheng").surprised
    assert "【战斗开始】" in cast.reports[-1] and telling.BEGIN_TELL in cast.reports[-1]
    ruled = len(cast.ruled)
    fake.queue(90, 60, 10).queue_totals(2)  # 周衡 misses, the warden fights back for nothing, then strikes 周衡, who cannot answer
    out, asked = await play(app, "攻击 楼梯口打盹的老人（徒手，1D3）", thread={**THREAD, "configurable": {"thread_id": "e2"}})
    assert len(cast.ruled) == ruled and enc is not None and st.encounter.round == 1 and not asked  # surprised: nothing to ask
    blow = [e for e in out if e["type"] == "fight_act"][-1]
    assert blow["attacker"] == "f1" and blow["reaction"] == "none" and st.investigator.hp == st.investigator.hp_max - 2
    assert not st.encounter.get("zhouheng").surprised  # the first round only


async def test_a_scripted_attack_waits_for_someone_to_meet_it_and_joins_a_fight_going_on(module, fake):
    from autokeeper.engine.game import Turn

    module.events.append(AMBUSH.model_copy())
    game, _, events = fighters(module, fake, {})
    game.state.investigator.add_condition("昏迷")  # nobody here who can still act: nothing begins
    await game._fire(module.events[-1], Turn())
    assert game.state.encounter is None and "e_ambush" in game.state.fired
    game.state.investigator.conditions.clear()
    module.npc("warden").status = "死亡"
    second = AMBUSH.model_copy(update={"id": "e_again"})
    await game._fire(second, Turn())
    assert game.state.encounter is None  # the dead attack no one
    module.npc("warden").status = ""
    await game._fire(AMBUSH.model_copy(update={"id": "e_third"}), Turn())
    assert [p.id for p in game.state.encounter.participants] == ["zhouheng", "warden"]
    turn = Turn()
    await game._fire(Event(id="e_more", title="布道者也冲了上来", when="flag:x", text="", fight=["preacher", "warden"]), turn)
    assert [p.id for p in game.state.encounter.participants] == ["zhouheng", "warden", "preacher"]  # joined, nobody twice
    assert any("街头布道者也扑了上来" in line for line in turn.fight) and events[-1]["type"] == "fight_state"
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
    assert resumed.state.encounter is not None  # it began before the line broke
    turn = await resumed.resolve(WAIT, ruling)
    assert [p.id for p in resumed.state.encounter.participants] == ["zhouheng", "warden"]
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
    assert kinds.count("sanity") == 1 and "reveal" in kinds and game.state.sightings == {"zhouheng": ["coil"]}
    assert views.Ctx.of(game).view("coil").attacks()[0].name == "缠绕"


async def test_a_chase_runs_the_modules_way_from_the_place_or_the_place_it_is_in(module, fake):
    from autokeeper.engine.game import Turn

    module.location("square").chase = STAIRS  # the tower is in the square: a chase from it runs this way too
    hounds = Event(id="e_run", title="守钟人追了上来", when="flag:x", text="", chase=["warden"])
    game, _, events = fighters(module, fake, {})
    fake.queue(40, 40)  # both CON regular: speeds 8 and 8, no one gone at once
    await game._fire(hounds, Turn())
    enc = game.state.encounter
    assert enc is not None and enc.mode == "chase" and enc.chase.site.source == "module"
    assert [n.name for n in enc.chase.site.nodes] == [leg.name for leg in STAIRS]
    door = next(o for o in enc.chase.site.obstacles.values() if o.what == "沉重的木门")
    assert door.state == "intact" and "open" not in module.location("square").chase[3].obstacle.model_dump()  # the chase's own
    assert any(e["type"] == "chase_start" for e in events)


async def test_running_from_a_fight_with_someone_after_you_is_the_same_way(module, fake):
    module.location("square").chase = STAIRS
    game, _cast, _events = fighters(module, fake, {"hit": (HIT, Ruling(needs_roll=False))}, hints=fight_now(),
                                  starts=[opening().model_copy(update={"opening": None, "npcs": [
                                      arrange("warden", attack("zhouheng"), pursue=["zhouheng"])]})],
                                  arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng"), pursue=["zhouheng"])]))
    app = turn_graph.build(game, InMemorySaver())
    await play(app, "hit")
    fake.queue(40, 40)  # the speeds
    _out, _ = await play(app, "逃跑", thread={**THREAD, "configurable": {"thread_id": "f2"}})
    enc = game.state.encounter
    assert enc.mode == "chase" and enc.switches == ["r1:chase"] and "又窄又陡的楼梯" in [n.name for n in enc.chase.site.nodes]


class Runner:
    """Someone on a chase's way, as the stage's view names them."""

    def __init__(self, ident: str, name: str, side: str, pos: int = 0, speed: int = 8, actions: int = 1):
        self.id, self.name, self.side, self.pos, self.speed, self.actions = ident, name, side, pos, speed, actions

    def row(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": "investigator" if self.side == "party" else "npc", "side": self.side,
                "pos": self.pos, "speed": self.speed, "actions": self.actions, "status": "", "wound": "无伤", "hidden": False,
                "hp": 11 if self.side == "party" else None, "hp_max": 11 if self.side == "party" else None, "portrait": ""}


WAY = [("墓园小径", None), ("墓碑间的窄道", {"kind": "hazard", "what": "倒伏的墓碑", "skill": "跳跃"}),
       ("新挖的墓穴", {"kind": "hazard", "what": "敞口的墓穴", "skill": "敏捷"}), ("柏树下", None),
       ("铁栅栏", {"kind": "barrier", "what": "锈蚀的铁栅栏", "skill": "攀爬"}), ("墓园外的土路", None),
       ("田埂", {"kind": "hazard", "what": "冻硬的田埂", "skill": "敏捷"}), ("远处的树林", None)]


def chase_of(*runners: Runner, round: int = 1, quarry: str = "party") -> dict:
    """A chase's view as the engine sends it (engine/encounter/chase.view)."""
    legs = [{"name": n, "look": "", "exit": i == len(WAY) - 1, "obstacle": {**ob, "difficulty": "常规", "open": False} if ob else None}
            for i, (n, ob) in enumerate(WAY)]
    return {"round": round, "quarry": quarry, "legs": legs, "runners": [r.row() for r in runners]}


def fight_screen(module):
    from autokeeper.engine.game import Game
    from autokeeper.tui.screens.game import GameScreen

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)], slot="fight-screen")
    state.location, state.prologue, state.turn = "tower", "（开场已讲过）", 3
    state.add("keeper", "钟楼里只有齿轮的声音。")
    me = Runner(state.investigators[0].id, state.investigators[0].name, "party", pos=2)
    warden = Runner("f1", "楼梯口打盹的老人", "foe")
    begun(module, state, ["warden"], round=1)
    return GameScreen(module, state, resume=True), me, warden


def begun(module, state, foes, *, round: int = 0) -> None:
    """A fight already going on in `state`, as a save holds one (engine/encounter)."""
    from autokeeper.engine.encounter.plan import EncounterStart
    from autokeeper.engine.game import Game, Turn
    from autokeeper.rules.coc import Rules

    from .test_engine import FakeCast

    game = Game(module, state, FakeCast({}), Rules())  # type: ignore[arg-type]
    game._begin("e1", EncounterStart(mode="fight", foes=foes, pace=6, reason="动起手来"), foes, Turn(), None)
    state.encounter.round, state.encounter.cursor = round, 2 if round else 0


HURT = {"type": "fight_act", "round": 1, "attacker": "warden", "attacker_name": "老守钟人", "attacker_side": "foe",
        "defender_side": "party", "hurt_side": "party", "kind": "melee", "attack": "徒手", "roll": None, "answer": None,
        "reaction": "none", "winner": "attacker", "damage": 2, "damage_text": "1D3 = 2", "effect": "", "extreme": False,
        "jammed": False, "empty": False, "notes": [], "line": "", "dice": None}


@pytest.mark.usefixtures("canned")
async def test_the_stage_shows_the_fight_and_offers_the_rounds_choices(module, fake):
    from autokeeper import config
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
        assert any(w.startswith("攻击 楼梯口打盹的老人（徒手，1D3）") for w in words) and any(w.startswith("逃跑") for w in words)
        assert not any("老守钟人" in w for w in words)  # the name the players know, never the keeper's
        assert not any("被攻击时" in w for w in words) and str(picker.border_subtitle).startswith("被攻击时：[b]闪避[/b]")
        rnd, clock = state.encounter.round, screen._clock
        await pilot.press("tab")  # the answer to blows, set at once: the same choices, the same round, the same clock
        await pilot.pause()
        assert state.encounter.get(me.id).react == "fight_back" and state.encounter.round == rnd and not state.encounter.receipts
        assert "反击" in str(picker.border_subtitle) and screen._clock is clock and picker.display
        assert [picker.get_option_at_index(i).id for i in range(picker.option_count)] == words
        await pilot.press("shift+tab")
        await pilot.pause()
        assert state.encounter.get(me.id).react == "dodge"
        await pilot.press("tab")
        await pilot.pause()
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
        state.encounter = None
        await screen.handle({"type": "fight_end", "end": "won", "text": "调查员一方占了上风"})
        await pilot.pause()
        assert not arena.display and not screen.has_class("-fight") and screen.query_one(SceneArt).display
        await screen.handle({"type": "chase_start", "chase": chase_of(me, warden), "line": "【追逐开始】"})
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
    me = Runner("zhouheng", "周衡", "party", pos=4, actions=1)
    warden = Runner("f1", "楼梯口打盹的老人", "foe", pos=2, speed=9, actions=2)
    view = chase_of(me, warden, round=2)
    text = chase_route(view, 44, 24, 1, was={me.id: 2, warden.id: 0}).plain
    lines = text.splitlines()
    legs = [n for n, _ in WAY]
    row = {name: next(line for line in lines if name in line) for name in legs[:7]}
    assert "在身后 2 段" in lines[1] and "到出口或甩掉追兵即脱离" in lines[2] and "随时可躲藏" in lines[2]
    assert lines.index(row[legs[6]]) < lines.index(row[legs[0]])  # the way ahead at the top
    assert "◆" in row[legs[4]] and "◉◉" in row[legs[2]] and "楼梯口打盹的老人" in row[legs[2]]
    assert "┆" in row[legs[3]] and "┆" in row[legs[1]]  # run through this round
    assert "▦攀爬" in row[legs[4]] and "⚠敏捷" in row[legs[6]]  # the fence they are at; the next thing in the way
    # Past the frozen field (the first thing in the way ahead) nothing is known yet: not its name, not where it ends.
    assert legs[7] not in text and "前方：尚未看清" in lines[4]
    assert any("行动点 2" in line for line in lines)
    me.pos = 6  # the way out in sight
    assert "跑到出口即可脱离" in chase_route(chase_of(me, warden), 44, 24).plain
    assert all(cell_len(line) <= 44 for line in chase_route(chase_of(me, warden), 44, 24).plain.splitlines())
    narrow = chase_route(chase_of(me, warden), 28, 24).plain.splitlines()  # a narrow panel: shorter columns
    assert all(cell_len(line) <= 28 for line in narrow) and any("周衡" in line for line in narrow)
    marks = [line for line in narrow if "⚠" in line or "▦" in line]  # a stretch's name cut short never runs into its tag
    assert marks and all(" ⚠" in line or " ▦" in line for line in marks), marks


async def test_the_chase_view_comes_from_the_site_by_shown_names_with_the_hazards_ahead(module, fake):
    """The engine's chase as the stage draws it: a straight way through the site from the pursuer to the
    quarry and on to the way out; the foe by the name the players know, no harm told in advance."""
    from autokeeper.engine.game import Turn

    game, _, _ = fighters(module, fake, {}, location="office")
    fake.queue(40, 40)
    await game._fire(Event(id="e_run", title="守钟人追了上来", when="flag:x", text="", chase=["warden"]), Turn())
    view = views.chase_view(game)
    assert [leg["name"] for leg in view["legs"]] == ["档案室", "报社的楼梯", "报社大门", "去广场的街", "雾里的广场"]
    assert view["legs"][-1]["exit"] and view["legs"][1]["obstacle"]["what"] == "堆着报纸的楼梯"
    assert "harm" not in view["legs"][1]["obstacle"]
    who = {r["name"]: r["pos"] for r in view["runners"]}
    assert who == {"周衡": 2, "楼梯口打盹的老人": 0} and "老守钟人" not in str(view)  # never the keeper's name for him
    text = chase_route(view, 44, 24).plain
    assert "楼梯口打盹的老人" in text and "⚠敏捷" in text


# -- windows over the fight: only the one in front ever goes (Textual's dismiss pops the top) ---------
OFFER = {"kind": "roll", "who": "周衡", "skill": "闪避", "target": 32, "difficulty": "常规", "reason": "老守钟人扑了过来"}


@pytest.mark.usefixtures("canned")
async def test_caught_in_a_chase_the_blood_then_its_face_and_the_screen_comes_back(module, fake, tmp_path, monkeypatch):
    from PIL import Image

    from autokeeper import imagery
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
        screen, _me, _warden = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        screen.busy = True  # the round is being settled: the engine ends the chase and starts the fight
        await screen.handle({"type": "chase_end", "end": "caught", "text": "追逐结束：老守钟人追上了周衡", "hunter": "warden",
                             "hunter_name": "老守钟人", "portrait": "face-warden"})
        await screen.handle({"type": "fight_start", "fight": views.fight_view(screen.game), "line": "【追逐转为战斗】扭打在一起"})
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
            seen.append(screen.state.encounter.get(me.id).react)
            screen.state.encounter = None
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
        assert sent == ["我"] and seen == [None] and (screen.state.encounter is None or screen.state.encounter.get(me.id).react in (None, "dodge"))
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
        assert len(sent) == 2 and screen.state.encounter.get(me.id).react == "dodge"  # four times round: back where it was
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
    from autokeeper.engine.game import Game
    from autokeeper.tui.screens.game import GameScreen

    for k in (1, 2):  # two more of the warden's kind
        module.npcs.append(module.npc("warden").model_copy(update={"id": f"helper{k}", "name": f"守钟人的帮手{k}"}))
    lead = module.pregens[0].model_copy(deep=True)
    state = Game.new(module, [lead, lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)], slot="crowd")
    state.location, state.prologue, state.turn = "tower", "（开场已讲过）", 3
    state.add("keeper", "钟楼里只有齿轮的声音。")
    begun(module, state, ["warden", "helper1", "helper2"], round=1)
    return GameScreen(module, state, resume=True)


def _all_on_screen(screen, height: int) -> None:
    """The input box above the footer, the round's list above the box, a few rows of story."""
    box, picker, story = screen.query_one("#command"), screen.query_one("#picker"), screen.query_one("#story")
    assert box.region.bottom <= height - 1, (box.region, height)
    assert picker.display and picker.region.height >= 5 and picker.region.bottom <= box.region.y
    assert story.region.height >= 3


def _rows(widget) -> list[str]:
    """What a widget shows, a row a line, as it is drawn on the screen (not what it was given)."""
    return [widget.render_line(y).text for y in range(widget.size.height)]


@pytest.mark.usefixtures("canned")
@pytest.mark.parametrize("size", [(80, 24), (100, 30), (40, 24)])
async def test_on_a_small_terminal_the_rounds_list_and_the_box_stay_on_screen(module, fake, size):
    from autokeeper.tui.app import AutoKeeperApp

    from .test_tui import until

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=size) as pilot:
        screen = _crowd(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        assert await until(pilot, lambda: screen.query_one("#picker").display)
        await pilot.pause(0.5)
        if size[0] >= 80:
            _all_on_screen(screen, size[1])
        side = screen.query_one(".side")
        rows = _rows(side)
        shown = "\n".join(rows)
        # every investigator's blood bar, drawn for the column's own width: no row wrapped or cut into the next
        assert shown.count("11/11") == 2 and "你们" not in shown, shown
        assert "调查员" in shown or side.size.height < 6  # its line where the stage has room for it
        assert sum("闪避" in r for r in rows) == 2  # each bar whole, its answer to blows after it
        assert any("楼梯口打盹的" in r for r in rows), shown  # the foe faced, at least


@pytest.mark.usefixtures("canned")
async def test_a_fight_shrunk_mid_round_fits_its_new_size_and_a_narrow_chase_keeps_its_picture(module, fake):
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
        tall = screen._stage_rows()[0]
        assert await until(pilot, lambda: arena.region.height == tall)
        await pilot.resize_terminal(100, 24)
        await pilot.pause(0.5)
        _all_on_screen(screen, 24)
        assert arena.region.height < tall
        screen.state.encounter, me.pos = None, 2
        await screen.handle({"type": "fight_end", "end": "fled", "text": "调查员逃离了"})
        await screen.handle({"type": "chase_start", "chase": chase_of(me, warden), "line": "【追逐开始】"})
        await pilot.resize_terminal(96, 30)  # the right column is up: the route narrows, the picture keeps room
        await pilot.pause(0.5)
        assert arena.canvas.size.width >= 28 and arena.query_one(".side").size.width >= 26


@pytest.mark.usefixtures("canned")
@pytest.mark.parametrize("size", [(100, 30), (120, 36)])
async def test_the_story_keeps_room_to_read_and_ctrl_n_folds_the_stage_then_the_list(module, fake, size):
    """Review 2026-10-22: the stage took half the screen and the list ten rows, leaving the story four."""
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.widgets.fight import Arena

    from .test_tui import until

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=size) as pilot:
        screen = _crowd(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        picker, story, stage = screen.query_one("#picker"), screen.query_one("#story"), screen.query_one("#stage")
        assert await until(pilot, lambda: picker.display)
        await pilot.pause(0.3)
        _all_on_screen(screen, size[1])
        assert story.region.height >= max(8, round(size[1] * 0.36)), (story.region, size)
        assert screen.query_one(Arena).region.height >= 4 and picker.region.height >= 5
        rows = [picker.get_option_at_index(i).prompt for i in range(picker.option_count)]
        assert all(r.no_wrap and r.overflow == "ellipsis" for r in rows)  # one row each, never wrapped
        before = story.region.height
        await pilot.press("ctrl+n")  # the stage folds away: the story has its rows
        await pilot.pause(0.2)
        assert not stage.display and story.region.height > before and picker.display
        await pilot.press("ctrl+n")  # then the round's list
        await pilot.pause(0.2)
        assert not picker.display and not screen._battle_picking
        await pilot.press("ctrl+n")  # both back, the list with this round's choices
        await pilot.pause(0.3)
        assert stage.display and picker.display and story.region.height == before


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
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        canvas = screen.query_one(stage.Arena).canvas
        assert await until(pilot, lambda: canvas.looks)
        assert "●" in canvas.looks[""].plain and app.is_running  # two eyes in the dark


@pytest.mark.usefixtures("canned")
async def test_the_story_tells_each_exchange_once_and_a_wound_on_its_card(module, fake):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.game import GameScreen

    from .test_tui import story_text, until

    game, _, _ = fighters(module, fake, {"hit": (HIT, Ruling(needs_roll=False))}, hints=fight_now(), starts=[opening()])
    fake.queue(90, 60, 20, 50).queue_totals(3)  # 周衡 misses; the warden hits him for 3
    events, _ = await play(turn_graph.build(game, InMemorySaver()), "hit", answers=["dodge"])
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
        await screen.handle({"type": "fight_state", "fight": views.fight_view(screen.game),
                             "lines": [acts[1]["line"], "周衡还吊着一口气（体质 30）"]})  # the round's end: something new
        await pilot.pause(0.3)
        text = story_text(screen)
        assert text.count("受到 3 点伤害") == 1  # on the warden's card, not again as a line, nor as a ✚ line
        assert not any(act["line"] in text for act in acts)  # each exchange is its card
        assert "还吊着一口气" in text  # what the cards do not say still comes


@pytest.mark.usefixtures("canned")
async def test_a_blow_aimed_at_the_player_is_asked_in_a_window_and_escape_keeps_their_answer(module, fake):
    from textual.widgets import Button

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.reaction import ChoiceModal

    from .test_tui import until

    offer = {"kind": "reaction", "id": "e1:r1:warden:0:react:0", "who": "zhouheng", "name": "周衡",
             "prompt": "楼梯口打盹的老人向你扑来，你怎么应对？", "default": "dodge",
             "options": [{"id": "dodge", "label": "闪避", "note": ""}, {"id": "fight_back", "label": "反击", "note": ""}]}
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen, _, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        answers: list[str] = []
        app.push_screen(ChoiceModal(offer), answers.append)
        await pilot.pause(0.1)
        assert [b.label.plain for b in app.screen.query(Button)] == ["闪避（原定）", "反击", "按原定"]
        await pilot.click("#o1")
        assert await until(pilot, lambda: answers == ["fight_back"])
        app.push_screen(ChoiceModal(offer), answers.append)
        await pilot.pause(0.1)
        await pilot.press("escape")
        assert await until(pilot, lambda: answers == ["fight_back", "dodge"])  # what they set for the round


@pytest.mark.usefixtures("canned")
async def test_the_reaction_window_says_what_each_answer_rolls_asks_every_time_when_told_and_counts_down(module, fake):
    from textual.widgets import Checkbox, Static

    from autokeeper import config
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.reaction import ChoiceModal

    from .test_tui import until

    offer = {"kind": "reaction", "id": "e1:r1:warden:0:react:0", "who": "zhouheng", "name": "周衡", "ask": "major",
             "prompt": "楼梯口打盹的老人向你扑来，你怎么应对？", "default": "dodge",
             "options": [{"id": "dodge", "label": "闪避", "note": "闪避 32 · 成功等级不低于对方即躲开"},
                         {"id": "fight_back", "label": "反击", "note": "格斗(斗殴) 25 · 成功等级高于对方即打中对方"}]}
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        answers: list = []
        app.push_screen(ChoiceModal(offer), answers.append)
        await pilot.pause(0.2)
        assert "闪避 32" in "".join(str(w.render()) for w in app.screen.query(Static))
        app.screen.query_one(Checkbox).value = True  # 每次被攻击都询问
        await pilot.click("#o1")
        assert await until(pilot, lambda: answers == [{"choice": "fight_back", "text": "", "ask": "always"}])
        assert config.ui_settings()["ask_every_blow"] is True  # kept in 设置 too
        timed = {**offer, "ask": "always", "closes_in": 0.6}
        app.push_screen(ChoiceModal(timed), answers.append)
        await pilot.pause(0.2)
        assert "超时按原定" in "".join(str(w.render()) for w in app.screen.query(Static))
        assert await until(pilot, lambda: len(answers) == 2, tries=30)
        assert answers[1] is None  # nobody answered in time: nothing is sent, the host takes what they set


@pytest.mark.usefixtures("canned")
@pytest.mark.parametrize("kind", ["reaction", "roll"])
async def test_a_timed_window_under_another_closes_only_itself_once_in_front(fake, kind):
    """Review 2026-10-22: the dialog's timeout under Ctrl+P (or a face) popped that screen instead and
    stayed up, answered and dead to Esc and clicks."""
    from textual.screen import ModalScreen
    from textual.widgets import Static

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.dialogs import RollModal
    from autokeeper.tui.modals.reaction import ChoiceModal

    from .test_tui import until

    class Over(ModalScreen):
        def compose(self):
            yield Static("over")

    offer = {"kind": "redeclare", "id": "x", "respond_id": "x", "who": "zhouheng", "prompt": "p", "default": "dodge",
             "options": [{"id": "dodge", "label": "闪避", "note": ""}], "closes_in": 0.5, "free_text": False}
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 40)) as pilot:
        got: list = []
        window = ChoiceModal(offer) if kind == "reaction" else RollModal({**OFFER, **offer, "kind": "roll"})
        app.push_screen(window, got.append)
        await pilot.pause(0.2)
        over = Over()
        app.push_screen(over)
        await pilot.pause(1.0)
        assert app.screen is over and not got  # its clock stood still underneath, and nothing else was closed
        app.pop_screen()
        assert await until(pilot, lambda: got == [None] and window not in app.screen_stack, tries=30)  # its own time ran out in front
        window2 = ChoiceModal({**offer, "closes_in": 30}) if kind == "reaction" else RollModal({**OFFER, **offer, "closes_in": 30})
        app.push_screen(window2, got.append)
        await pilot.pause(0.2)
        app.push_screen(over := Over())
        window2.settled()  # the host settled it while something else was in front
        await pilot.pause(0.2)
        assert app.screen is over and len(got) == 1
        app.pop_screen()
        assert await until(pilot, lambda: got == [None, None] and window2 not in app.screen_stack)


@pytest.mark.usefixtures("canned")
async def test_no_face_comes_up_over_a_fights_question(module, fake):
    from textual.screen import ModalScreen

    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.reaction import ChoiceModal

    from .test_tui import until

    offer = {"kind": "reaction", "id": "r", "who": "zhouheng", "prompt": "p", "default": "dodge",
             "options": [{"id": "dodge", "label": "闪避", "note": ""}]}
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen, _, _ = fight_screen(module)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        app.push_screen(question := ChoiceModal(offer))
        await pilot.pause(0.1)
        await screen._over_all(ModalScreen())  # type: ignore[arg-type]  # a face coming up, as a reveal brings one
        await pilot.pause(0.1)
        assert app.screen is question
