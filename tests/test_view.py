"""What the AI players and the action parser are shown, where a guessed name leads, and ideas
on demand (F5). No model calls: scripted casts, canned models, a real Hub on localhost."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.widgets import Input

from autokeeper import config, paths
from autokeeper.agents.cast import Action, Cast, Ruling
from autokeeper.config import Role
from autokeeper.engine import fog
from autokeeper.engine.autoplay import situation
from autokeeper.engine.game import NOWHERE, Game
from autokeeper.engine.state import GameState
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Location, Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.prefs import Prefs
from autokeeper.rules.coc import Rules
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.suggest import IdeasModal
from autokeeper.tui.screens.game import GameScreen
from autokeeper.tui.screens.net import NetGameScreen

from .test_engine import FakeCast
from .test_net import TableCast, closed, opened
from .test_tui import canned, until  # noqa: F401  (canned is a fixture)

ZHUI = Path(__file__).resolve().parents[1] / "modules" / "zhuishuren"


@pytest.fixture
def zhui() -> Module:
    return Module.load(ZHUI)


def begun(m: Module) -> GameState:
    """After the arrival: settled at the house, Thomas has offered to show the way."""
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.location, st.visited = "jinboer_jia", ["jinboer_jia"]
    st.add("keeper", "托马斯把你领进空卧室，提起书房、邻居、墓地、图书馆、警局和报社，可以随时为你指路。")
    return st


# -- the AI investigator's view -------------------------------------------------------------
def test_ai_players_see_what_the_players_were_shown_and_no_more(zhui):
    st = begun(zhui)
    view = situation(zhui, st)
    assert "你们到这里时看到：艾尔斯伯里大街走到头，218 号是一栋 L 形的旧木屋" in view and "图书馆（听说过）" in view
    assert not any(s in view for s in ("陵墓", "隧道", "食尸鬼", "屋后通往公墓的小路"))  # unheard places, the keeper's list of things to search
    st.visited.append("mudi")
    st.location = "mudi"
    view = situation(zhui, st)
    assert "一个园艺工正弯腰清理杂草" in view and "金博尔家（去过）" in view
    assert not any(s in view for s in ("杰弗逊", "外套口袋", "陵墓", "隧道"))  # nobody introduced, no pocket, no way on
    st.clues.append("c_footprints")  # the hoofprints lead to a mausoleum's door
    view = situation(zhui, st)
    assert "陵墓（听说过）" in view and "隧道" not in view
    # Not the module's words for it: the keeper's telling carries those, as it does to the human beside it
    # (the user, 2026-10-22: no clue summaries handed to the players).
    assert "通向一座陵墓的门前" not in view and "墓碑旁的怪足迹" not in view and "已有线索" not in view
    st.visited += ["lingmu", "suidao"]
    st.location = "suidao"  # down in the tunnels, where the man they are after is too
    view = situation(zhui, st)
    assert "泥土通道又低又窄" in view and not any(s in view for s in ("道格拉斯", "半食尸鬼", "隧道口"))
    st.npc("douglas").met = True
    assert "道格拉斯·金博尔（失踪的叔叔" in situation(zhui, st)  # once he has spoken, they know who he is


@pytest.mark.usefixtures("canned")
def test_the_parser_gets_only_where_they_could_head_and_by_the_names_they_know(zhui):
    st = begun(zhui)
    assert fog.bearings(zhui, st) == {  # what Thomas offered, and the police station the town centre shows
        "shufang": "书房", "jiefang": "艾尔斯伯里大街", "mudi": "公墓", "zhenzhongxin": "阿诺兹堡镇中心", "tushuguan": "图书馆", "jingju": "阿诺兹堡警察局", "baoshe": "报社", "huochezhan": "阿诺兹堡火车站",
    }  # fmt: skip
    st.visited.append("mudi")
    st.location = "mudi"
    assert fog.bearings(zhui, st)["lingmu"] == "陵墓" and "suidao" not in fog.bearings(zhui, st)  # next door, by its plainest name
    scene = Cast(config.load(), zhui).bearings(st)
    assert "陵墓(lingmu)" in scene and "一个园艺工" in scene and "jefferson" in scene
    assert "梅洛迪亚斯·杰弗逊" not in scene  # a visible stranger is addressed by appearance, not his secret name
    assert "外套口袋" not in scene and "可调查" not in scene  # its summary and first beat reach the player's screen
    st.visited.append("lingmu")
    st.location = "lingmu"
    assert fog.bearings(zhui, st)["suidao"] == "隧道网" and "食尸鬼" not in Cast(config.load(), zhui).bearings(st)


@pytest.mark.usefixtures("canned")
def test_the_house_answers_to_the_address_on_the_letter(zhui):
    """The letter gives 艾尔斯伯里大街 218 号; "I knock at 218" has to lead to the Kimballs' house."""
    assert fog.mentions(zhui, ["信封上写着艾尔斯伯里大街 218 号"]) == {"jiefang": "艾尔斯伯里大街", "jinboer_jia": "218 号"}
    st = Game.new(zhui, [zhui.pregens[0].model_copy(deep=True)])
    st.location, st.visited = "jiefang", ["huochezhan", "zhenzhongxin", "jiefang"]
    st.add("keeper", "托马斯的信上写着地址：艾尔斯伯里大街 218 号。")
    assert "金博尔家(jinboer_jia，也叫托马斯家、218 号)" in Cast(config.load(), zhui).bearings(st)  # the name they have (the setting's), then the rest
    st.location, st.visited = "zhenzhongxin", ["huochezhan", "zhenzhongxin"]
    st.log.clear()
    assert "lingmu" not in Cast(config.load(), zhui).bearings(st)  # other names give away nothing new


def test_plain_sight_is_what_is_seen_in_the_open(zhui):
    assert fog.landmarks(zhui) == {
        "zhenzhongxin": "阿诺兹堡镇中心", "mudi": "公墓", "jinboer_jia": "金博尔家", "tushuguan": "图书馆", "jingju": "警察局",
        "jiefang": "艾尔斯伯里大街",  # named from the end of the street, where the house stands
    }  # fmt: skip
    zhui.location("suidao").read_aloud += "通道尽头，隐约是一座地下墓城。"  # seen only down a secret way
    zhui.locations.append(Location(id="city", name="地下墓城", read_aloud="", exits=["suidao"]))
    assert "city" not in fog.landmarks(zhui) and "city" not in fog.bearings(zhui, begun(zhui))


def test_opening_material_is_not_knowledge_until_the_keeper_says_it(zhui):
    st = Game.new(zhui, [zhui.pregens[0].model_copy(deep=True)])
    zhui.opening += " 路人告诉你地底隧道网就在脚下。"
    assert "suidao" not in fog.known(zhui, st)
    st.add("keeper", "路人告诉你地下有一片隧道网。")
    assert fog.known(zhui, st)["suidao"] == "隧道网"


# -- where a name leads -----------------------------------------------------------------------
async def test_a_guessed_name_takes_no_one_anywhere(zhui, fake):
    st = begun(zhui)
    look = Action(kind="investigate", summary="哈罗德去陵墓查看石门", target="石门", destination="lingmu")
    cast = FakeCast({"x": (look, Ruling(needs_roll=False, minutes=10))})
    game = Game(zhui, st, cast, Rules(dice=fake))  # type: ignore[arg-type]

    def go(place: str = "") -> Action:
        return Action(kind="move", summary=f"哈罗德去{place or '地底下'}", destination=place)

    moved = Ruling(needs_roll=False, minutes=0, reason="移动")
    turn = await game.resolve(go("suidao"), moved)  # named from across town, never heard of
    assert st.location == "jinboer_jia" and "suidao" not in st.visited and NOWHERE in turn.notes
    ruling = await game.rule(look)
    assert cast.ruled_at is None  # ruled where they stand, not at the secret place
    turn = await game.resolve(look, ruling)
    assert st.location == "jinboer_jia" and NOWHERE in turn.notes
    turn = await game.resolve(go(), Ruling(needs_roll=False, minutes=5))  # a way the parser could not name
    assert st.location == "jinboer_jia" and NOWHERE in turn.notes
    await game.resolve(go("tushuguan"), moved)  # heard of: they set out from here
    assert st.location == "tushuguan"
    await game.resolve(go("jingju"), moved)  # never named in play, but in plain sight in the town centre
    assert st.location == "jingju"
    st.location = "mudi"
    turn = await game.resolve(go("lingmu"), moved)  # unheard of, but next door: going that way finds it
    assert st.location == "lingmu" and NOWHERE not in turn.notes


# -- ideas on demand (F5) -------------------------------------------------------------------------
class Thinker(hm.Model):
    """Answers the ideas call: one idea in quotes, one said twice. Keeps what it was asked."""

    asked: list[str] = []

    async def _call(self, messages, *, schema, images, on_delta):
        Thinker.asked.append(messages[-1]["content"])
        data = {"look": "“我拉开旧书桌的抽屉翻一翻”", "ask": "我问托马斯：叔叔平时在哪儿读书？", "dare": "我问托马斯：叔叔平时在哪儿读书？"}
        return Reply(text=json.dumps(data, ensure_ascii=False), data=data if schema else None)


async def test_ideas_are_written_from_the_players_view_and_kept_out_of_the_save(zhui, monkeypatch, tmp_path, skills_dir):
    monkeypatch.setattr(paths, "DATA", tmp_path)  # no preferences saved: the defaults
    monkeypatch.setitem(hm.MODELS, "thinker", Thinker)
    monkeypatch.setattr(Thinker, "asked", [])
    cast = Cast(config.Config(roles={r: Role("thinker", "thinker", None) for r in config.DEFAULT_ROLES}), zhui)
    st = begun(zhui)
    lead = st.investigators[0]
    view = situation(zhui, st)
    ideas = await cast.suggest(st, lead.id, view)
    assert ideas == ["我拉开旧书桌的抽屉翻一翻", "我问托马斯：叔叔平时在哪儿读书？"]  # quotes off, the same idea once
    system = cast.sessions[f"suggest:{lead.id}"].system
    assert lead.name in system and "托马斯·金博尔的人联系了你" in system and zhui.truth.strip()[:20] not in system  # the hook, not the truth
    assert not st.transcripts and st.usage["suggest"]["calls"] == 1  # nothing to remember; the cost is counted
    assert Thinker.asked == [view]
    cast.prefs = Prefs(lines=("动物受伤",))  # the player's lines ride along, this call only
    await cast.suggest(st, lead.id, view)
    assert Thinker.asked[-1].startswith(view) and "动物受伤" in Thinker.asked[-1]
    await cast.close()


IDEAS = ["我凑近告示栏，看那张泛黄的告示", "我问那个黑衣人：钟声是怎么回事？", "我先在广场边上等到天黑"]


def playing(m: Module) -> tuple[GameScreen, GameState]:
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="ideas", human=[lead.id])
    return GameScreen(m, state, resume=True), state


@pytest.mark.usefixtures("canned")
async def test_f5_puts_the_chosen_idea_in_the_box_and_esc_leaves_it(monkeypatch):
    asked: list[str] = []

    async def suggest(self, state, inv_id, view):
        asked.append(view)
        return IDEAS

    monkeypatch.setattr(Cast, "suggest", suggest)
    screen, state = playing(Module.load(EXAMPLE))
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        box = screen.query_one("#command", Input)
        await pilot.press("f5")
        assert await until(pilot, lambda: isinstance(app.screen, IdeasModal))
        assert asked[0] == situation(screen.m, state)  # the AI players' view, nothing more
        await pilot.press("down", "enter")
        assert await until(pilot, lambda: app.screen is screen)
        assert box.value == IDEAS[1] and box.has_focus and state.turn == 0 and not state.log  # in the box, not sent
        screen.send_line("/suggest")  # the same moment: the same ideas, at once
        assert await until(pilot, lambda: isinstance(app.screen, IdeasModal)) and len(asked) == 1
        await pilot.press("escape")
        assert await until(pilot, lambda: app.screen is screen)
        assert box.value == IDEAS[1]  # Esc leaves the box as it was
        screen.busy = True  # mid-turn: no ideas until it is told
        await pilot.press("f5")
        await pilot.pause(0.2)
        assert app.screen is screen and len(asked) == 1


@pytest.mark.usefixtures("canned")
async def test_f5_thinks_without_getting_in_the_way_of_typing(monkeypatch):
    gate = asyncio.Event()

    async def suggest(self, state, inv_id, view):
        await gate.wait()
        return IDEAS

    monkeypatch.setattr(Cast, "suggest", suggest)
    screen, _ = playing(Module.load(EXAMPLE))
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        box = screen.query_one("#command", Input)
        await pilot.press("f5")
        assert await until(pilot, lambda: "生成行动建议" in screen.status_text)  # the spinner line
        await pilot.press("w", "o")  # the box is still open for typing
        gate.set()
        assert await until(pilot, lambda: not screen.status_text)
        await pilot.pause(0.2)
        assert app.screen is screen and box.value == "wo"  # done, but not in front of what was being typed
        await pilot.press("f5")  # ready and waiting
        assert await until(pilot, lambda: isinstance(app.screen, IdeasModal))
        await pilot.press("space")
        assert await until(pilot, lambda: app.screen is screen) and box.value == IDEAS[0]


class IdeaCast(TableCast):
    def __init__(self) -> None:
        super().__init__()
        self.views: list[tuple[str, str]] = []

    async def suggest(self, state, inv_id, situation):
        self.views.append((inv_id, situation))
        return IDEAS


async def test_f5_at_a_shared_table_asks_the_host(monkeypatch, tmp_path, skills_dir, fake):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    cast = IdeaCast()
    hub = Hub(EXAMPLE, seats=["玩家"], cast=cast, rules=Rules(dice=fake))
    task = await opened(hub)
    app = AutoKeeperApp(theme="amber", join=(f"127.0.0.1:{hub.port}", "小明"))
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("enter")  # the free seat
        await pilot.pause(0.1)
        await pilot.press("y")  # ready: the game begins
        assert await until(pilot, lambda: isinstance(app.screen, NetGameScreen) and app.screen.my_turn)
        screen = app.screen
        await pilot.press("f5")
        assert await until(pilot, lambda: isinstance(app.screen, IdeasModal))
        await pilot.press("enter")
        assert await until(pilot, lambda: app.screen is screen)
        assert screen.query_one("#command", Input).value == IDEAS[0]
        assert cast.views == [("zhouheng", situation(hub.m, hub.state))]  # thought for this seat, from the players' view
    await closed(task)
