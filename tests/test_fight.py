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

    from autokeeper.tui.app import AutoKeeperApp

    from .test_tui import until

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
        await pilot.pause()
        assert box.has_focus and box.value == "我" and not picker.display and not screen._battle_picking
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
        screen.state.ending = "lost"  # every investigator down: the game is over, the fight still in the state
        await screen.handle({"type": "ending", "title": "钟声", "text": "钟楼重归寂静。"})
        await pilot.pause(0.2)
        assert not picker.display and screen._clock is None and not screen.query_one(Arena).display
        assert screen.query_one(SceneArt).display and not screen.has_class("-fight")
        screen._offer_orders()
        assert not picker.display
        assert await until(pilot, lambda: not screen._beating)
