"""Terminal UI smoke tests (no model calls): title, setup flow, small terminals, pictures."""

from __future__ import annotations

import asyncio
import io
import os
import shutil

import pytest
from PIL import Image
from rich.text import Text
from textual.widgets import Button, Input, RadioButton, Static

from autokeeper import artkit, config, imagery, paths
from autokeeper.config import Role
from autokeeper.engine.game import Game
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.tui import library
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.dialogs import HandoutModal, PushModal, RollModal
from autokeeper.tui.modals.menu import Confirm, GameMenu
from autokeeper.tui.screens.game import GameScreen
from autokeeper.tui.screens.saves import LoadScreen
from autokeeper.tui.screens.setup import SetupScreen
from autokeeper.tui.screens.title import TitleScreen
from autokeeper.tui.widgets.panels import SceneArt, StoryLog, command_suggester
from autokeeper.tui.widgets.speaker import Speaker


@pytest.mark.parametrize("size", [(140, 44), (80, 24)])
async def test_title_and_setup_flow(monkeypatch, size, skills_dir):
    monkeypatch.setattr(paths, "module_folders", lambda: [EXAMPLE])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, TitleScreen)
        await pilot.press("space")  # space confirms like Enter
        await pilot.pause()
        assert isinstance(app.screen, SetupScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.step == "investigator"
        await pilot.press("space")
        await pilot.pause()
        assert app.screen.step == "companions"


class Canned(hm.Model):
    async def _call(self, messages, *, schema, images, on_delta):
        text = "前情回顾：周衡还在钟楼底下。"
        if on_delta:
            await on_delta(text)
        return Reply(text=text, data={} if schema else None)


@pytest.fixture
def canned(monkeypatch, tmp_path, skills_dir):
    """Every agent answers from a can, saves go to a temp folder."""
    for var in ("AUTOKEEPER_PRESET", "AUTOKEEPER_MODEL", "AUTOKEEPER_FALLBACK", "AUTOKEEPER_MODE"):  # F9 sets these; undo afterwards
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "module_folders", lambda: [EXAMPLE])
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    return cfg


def story_text(screen) -> str:
    return "".join(str(w.render()) for w in screen.query_one(StoryLog).children)


async def until(pilot, test, tries: int = 40) -> bool:
    for _ in range(tries):
        if test():
            return True
        await pilot.pause(0.1)
    return test()


@pytest.mark.parametrize("renderer", [True, False])
async def test_handout_keeps_full_text_ascii_preview_and_original_access(monkeypatch, tmp_path, renderer):
    original = tmp_path / "map.png"
    Image.new("RGB", (800, 1200)).save(original)
    rendered, opened = [], []

    def render(*args):
        rendered.append(args)
        return "  /___\\\n  |___|"

    monkeypatch.setattr(imagery, "available", lambda: renderer)
    monkeypatch.setattr(imagery, "render", render)
    app = AutoKeeperApp(theme="amber")
    monkeypatch.setattr(app, "open_url", opened.append)
    text = "\n".join(f"病历第{i}项：完整记录" for i in range(100))
    async with app.run_test(size=(80, 24)) as pilot:
        modal = HandoutModal("原作病历", text, [str(original)])
        app.push_screen(modal)
        await pilot.pause()
        modal.revealed = len(modal.paper) - 1
        modal._develop()
        paper = str(modal.query_one("#paper", Static).render())
        assert "病历第0项" in paper and "病历第99项" in paper
        if renderer:
            assert rendered[0][3] == "photo" and "/___\\" in str(modal.query_one("#handout-image-0", Static).render())
        else:
            assert "下方按钮" in str(modal.query_one("#handout-image-0", Static).render())
        modal.open_original(Button.Pressed(modal.query_one("#original-0", Button)))
        assert opened == [original.as_uri()]
        await pilot.press("escape")
        assert isinstance(app.screen, TitleScreen)


async def test_a_player_throws_the_dice_and_sees_what_it_led_to(canned, fake):
    from .test_engine import HARD_TALK, PLEAD, FakeCast

    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="roll", human=[lead.id])
    state.location = "tower"
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and "钟楼底下" in story_text(screen) and not screen.busy)  # recap told
        screen.game.cast, screen.game.rules = FakeCast({"我劝守钟人说实话": (PLEAD, HARD_TALK)}), Rules(dice=fake)
        fake.queue(8)  # 说服 40, hard: an extreme success
        screen.send_line("我劝守钟人说实话")
        assert await until(pilot, lambda: isinstance(app.screen, RollModal))
        modal = app.screen
        shown = "".join(str(w.render()) for w in modal.query(Static))
        assert "说服 40 · 困难：掷出 ≤20 就算过" in shown and "极难 ≤8" in shown and "守钟人就在一旁盯着你" in shown
        assert fake.values  # the dice wait for the player
        await pilot.pause(0.5)
        await pilot.press("escape")  # no way out
        assert app.screen is modal and fake.values
        await pilot.press("enter")
        assert await until(pilot, lambda: "↳ 老守钟人松了口：〔守钟人的日记〕" in story_text(screen))
        assert "十位" in story_text(screen) and "极难成功" in story_text(screen) and not fake.values
        assert await until(pilot, lambda: isinstance(app.screen, HandoutModal))  # the diary came with it
        await pilot.press("escape")
        assert await until(pilot, lambda: app.screen is screen and not screen.busy)
        box = screen.query_one("#command", Input)
        box.focus()
        box.value = ".ra 侦"
        box.cursor_position = len(box.value)  # as if typed
        await pilot.pause(0.2)
        await pilot.press("right")  # → takes the suggestion
        assert box.value == ".ra 侦查"
        offer = {"kind": "push", "skill": "图书馆使用", "target": 60, "need": 60, "value": 63, "can_push": True, "luck": 3, "left": 52,
                 "risk": "管理员下班锁门，你被关在馆里过夜"}
        app.push_screen(PushModal(offer))
        await pilot.pause()
        stakes = "".join(str(w.render()) for w in app.screen.query(Static))
        assert "再次失败：管理员下班锁门，你被关在馆里过夜" in stakes and "幸运 55 → 52" in stakes  # the price, before the choice
        assert "接受这次失败" in stakes and "仍可取得" not in stakes  # giving up finds nothing


async def test_after_the_ending_only_the_dice_lookups_work(canned, monkeypatch):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="ended", human=[lead.id])
    state.ending = "stopped"
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(GameScreen(m, state, resume=True))
        await pilot.pause(0.5)
        screen = app.screen
        settled = asyncio.Event()
        run_turn = screen._run_turn

        async def finish_then_wait(*args, **kwargs):
            await run_turn(*args, **kwargs)
            await settled.wait()  # the result is visible while the worker is still finishing

        monkeypatch.setattr(screen, "_run_turn", finish_then_wait)
        screen.send_line("我再上一趟钟楼")  # the story is over
        screen.send_line(".en")  # growth rolls are not
        for _ in range(20):
            await pilot.pause(0.1)
            if "没有需要成长检定的技能" in story_text(screen):
                break
        assert "没有需要成长检定的技能" in story_text(screen) and "我再上一趟钟楼" not in story_text(screen)
        assert state.turn == 0 and state.developed == [lead.id] and "回顾这一局" not in story_text(screen)
        await pilot.press("escape")  # as the last lines say: the Esc menu, 回到标题 ready for Enter
        await pilot.pause()
        assert isinstance(app.screen, GameMenu)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, Confirm)  # the .en is still being settled: wait for it, or go now
        await pilot.press("enter")  # 讲完再走
        await pilot.pause()
        assert app.screen is screen  # the pending command finishes before the screen leaves
        settled.set()
        assert await until(pilot, lambda: not isinstance(app.screen, GameScreen))


async def test_loading_a_save_opens_the_game_at_night(canned):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="night", human=[lead.id])
    state.minutes = 23 * 60 + 10
    library.write_save(state, m)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        await pilot.press("down", "enter")  # 继续调查
        await pilot.pause()
        assert isinstance(app.screen, LoadScreen)
        await pilot.press("enter")  # the newest save is highlighted
        await pilot.pause(0.5)
        assert isinstance(app.screen, GameScreen)
        scene = app.screen.query_one(SceneArt)
        assert scene.when == "深夜" and scene.place
        for _ in range(20):  # the scan-in, then the square's fog starts drifting
            await pilot.pause(0.1)
            if scene._atmo is not None and scene._atmo.t > 1:
                break
        assert scene._atmo is not None and scene._atmo.kinds == ["fog"] and scene._atmo.t > 1
        assert scene._fit()[1] == 17  # the art takes about 38% of the terminal's height
        assert app.art_style == "photo"  # plain blocks by default: the style players read best
        await pilot.press("ctrl+a")  # the input box has focus, but Ctrl+A still changes the style
        assert app.art_style == "ascii" and scene.style_name == "ascii"
        await app.screen.run_action("weather")  # the switch in Ctrl+P and in 设置
        await pilot.pause(0.2)
        assert scene._atmo is None  # weather off
        screen = app.screen
        before, memory = screen.cast, screen.state.transcripts
        await pilot.press("f9")  # change models mid-game
        await pilot.pause()
        app.screen.query(RadioButton).last().value = True  # 高级剧情
        await pilot.click("#apply")
        await pilot.pause(0.3)
        assert screen.cast is not before and screen.game.cast is screen.cast and os.environ["AUTOKEEPER_MODE"] == "高级剧情"
        assert screen.state.transcripts is memory  # the agents' memories carry over
        screen.busy = True  # mid-turn: the next action can be typed ahead; it goes in once the keeper is done
        box = screen.query_one("#command", Input)
        box.value = "我爬上钟楼"
        await pilot.press("enter")
        await pilot.pause(0.2)
        status = screen.query_one("#status", Static)
        assert box.value == "" and not box.disabled and screen.queued == "我爬上钟楼"
        assert "待叙述结束后提交：我爬上钟楼" in str(status.render())
        box.value = "我再去报社"
        await pilot.press("enter")
        await pilot.pause()
        assert box.value == "我再去报社" and screen.queued == "我爬上钟楼"  # one at a time: this one stays in the box
        box.value = "/undo"
        await pilot.press("enter")
        await pilot.pause()
        assert box.value == "我爬上钟楼" and screen.queued is None  # back in the box, to change or drop
        await pilot.press("enter")
        sent: list[str] = []
        screen.send_line = sent.append
        screen.state.pending_turn = {"input": "我推开门"}  # a turn broke off: /retry goes on with it first
        screen._busy(False)
        await pilot.pause(0.2)
        assert not sent and screen.queued == "我爬上钟楼" and "/retry" in str(status.render())
        screen.state.pending_turn = {}
        screen._busy(False)
        await pilot.pause(0.2)
        assert sent == ["我爬上钟楼"] and screen.queued is None
        del screen.send_line
        for size, rows in (((200, 60), 23), ((80, 24), 9)):  # a taller terminal, a taller picture
            await pilot.resize_terminal(*size)
            await pilot.pause(0.2)
            assert scene._fitted[1] == rows


def test_weather_is_read_off_the_place_and_the_hour():
    from autokeeper import weather

    assert weather.infer("公墓", "墓碑之间点缀着灌木。", "深夜") == ["fog"]
    assert weather.infer("公墓", "墓碑之间点缀着灌木。", "上午") == []
    assert weather.infer("书房", "窗外就是公墓的树影。", "深夜") == []  # the graveyard is only out of the window
    assert weather.infer("隧道", "手电的光只能照出前面几步。", "上午") == ["torch"]
    assert weather.infer("大街", "梧桐叶子在人行道上打着旋。", "下午") == ["leaves"]
    assert weather.infer("图书馆", "长桌上方吊着绿罩台灯。", "深夜") == ["flicker"]
    assert weather.infer("码头", "雨后的石板路。", "下午") == []
    assert [weather.outdoors(n) for n in ("公墓", "艾尔斯伯里大街", "广场", "道格拉斯的书房", "警察局", "地底隧道网", "钟楼")] == [
        True, True, True, False, False, False, False,
    ]
    names = ("后弄堂", "南京路街口", "霞飞路·特卡琴科西点铺", "江西路口·总巡捕房", "徐家汇路百代厂房·整理间")
    assert [weather.outdoors(n) for n in names] == [True, True, False, False, False]  # after the dot is the place
    assert weather.infer("南市老茶馆", "老虎灶的水汽把半间屋子糊成白的。") == ["fog"]
    assert weather.infer("宝光照相馆·暗室", "门一关，只剩一盏红灯。") == ["redlight"]
    assert weather.infer("大世界·哈哈镜厅", "一排变形的镜子，把人照得忽胖忽瘦。") == ["warp"]


def test_the_keepers_words_set_the_sky():
    from autokeeper import weather

    assert weather.sky("天色暗下来，下起了雨，雨点打在墓碑上。") == ("rain", True)
    assert weather.sky("远处滚过一阵闷雷。", "rain") == ("storm", True)
    assert weather.sky("闷雷过后，雨点砸在石板上。") == ("storm", True)  # rain in a storm is the storm
    assert weather.sky("雷声渐渐远了，只剩细雨。", "storm") == ("rain", True)
    assert weather.sky("雨停了，屋檐还在滴水。", "storm") == ("", True)
    assert weather.sky("雾散了些。", "rain") == ("rain", True)  # clearing fog leaves the rain
    assert weather.sky("看样子快下雨了。", "") == ("", False)  # not yet
    assert weather.sky("他把手电光挪到封面上，闪电般合上了书。") == ("", False)  # a torch, a figure of speech
    assert weather.sky("推开窗，雾还没散干净，公墓的石碑浮在里面。") == ("fog", True)
    assert weather.sky("窗外的雾淡了，日头照进来。", "fog") == ("", True)
    assert weather.sky("编辑部里烟雾缭绕，他听得一头雾水。") == ("", False)
    assert weather.sky("他把信递给你。", "fog") == ("fog", False)


def test_weather_frames_keep_the_panel_and_light_the_torch_beam():
    from autokeeper import atmosphere

    art = Text("\n".join(["#" * 40] * 10), style="#808080")
    dim = Text("\n".join(["#" * 40] * 10), style="#202020")
    beam = Text("\n".join(["#" * 40] * 10), style="#ffffff")
    a = atmosphere.Atmosphere(["torch", "dust"], {"": art, "dim": dim, "beam": beam}, 48, 12, when="深夜", seed=1)
    frame = a.frame()
    lines = frame.split("\n")
    assert len(lines) == 12 and all(ln.cell_len == 48 for ln in lines)
    index, _ = a.layer_map()
    assert index[0, 0] == atmosphere.LAYERS.index("dim")  # a corner is outside the beam
    cx, cy, _ = a.beam()
    assert index[int(cy), int(cx)] == atmosphere.LAYERS.index("beam")
    rain = atmosphere.Atmosphere(["rain"], {"": art}, 48, 12, seed=2)
    assert "/" in rain.frame().plain
    stripes = Text("\n".join(["".join("ab"[(x // 3) % 2] for x in range(48))] * 12))
    mirror = atmosphere.Atmosphere(["warp", "redlight"], {"": stripes, "red": stripes}, 48, 12, seed=3)
    bent = mirror.frame().split("\n")
    assert all(ln.cell_len == 48 for ln in bent) and bent[0].plain != stripes.split("\n")[0].plain  # the mirror bends it
    assert (mirror.layer_map()[0] == atmosphere.LAYERS.index("red")).all()  # all under the safelight


def test_ascii_art_keeps_lamplight_after_dark():
    lit = artkit.colorize("#", "Y", when="深夜")
    dim = artkit.colorize("|", "n", when="深夜")
    assert str(lit.spans[0].style) == artkit.PALETTE["Y"]  # a lit window stays amber
    assert str(dim.spans[0].style) != artkit.PALETTE["n"]  # wood darkens at night


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
def test_scene_picture_fills_the_panel(tmp_path):
    img = tmp_path / "tall.png"
    Image.new("RGB", (200, 300), (180, 60, 40)).save(img)
    framed = imagery.frame(Image.open(img).convert("RGB"), 80, 15)
    assert abs(framed.width / framed.height - 80 * 0.5 / 15) < 0.05  # the panel's shape, no blurred sides
    assert framed.getpixel((framed.width // 2, framed.height // 2)) == (180, 60, 40)
    out = imagery.render(str(img), 80, 15, "picture", "深夜", True)
    assert max(len(Text.from_ansi(line).plain) for line in out.split("\n")) >= 70


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
def test_picture_renders_to_requested_size(tmp_path):
    img = tmp_path / "scene.jpg"
    Image.radial_gradient("L").convert("RGB").resize((320, 160)).save(img)
    out = imagery.render(str(img), 40, 10, "picture")
    lines = [line for line in out.split("\n") if line]
    assert 1 <= len(lines) <= 10
    assert "\x1b[" in out
    assert imagery.render(str(img), 40, 10, "ascii").isascii()


def test_the_sheet_lists_every_skill_and_the_specialisms():
    from autokeeper.tui.modals.sheet import skill_rows

    inv = Module.load(EXAMPLE).pregens[0].model_copy(deep=True)
    inv.skills["外语(英语)"] = 40
    inv.marks = ["侦查"]
    rows = {name: (value, raised, ticked) for name, value, raised, ticked in skill_rows(inv)}
    assert "外语(英语)" in rows and "外语" not in rows  # the specialism replaces the general entry
    assert rows["侦查"][2] and rows["攀爬"] == (20, False, False)  # ticked; an untouched base chance
    assert rows["闪避"][0] == inv.characteristics["敏捷"] // 2 or rows["闪避"][1]


async def test_f7_opens_the_whole_sheet_and_walks_the_party(monkeypatch, tmp_path, skills_dir):
    from autokeeper.tui.modals.sheet import SheetModal

    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    m = Module.load(EXAMPLE)
    party = [p.model_copy(deep=True) for p in m.pregens[:2]]
    state = Game.new(m, party, slot="sheet", human=[party[0].id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        app.push_screen(GameScreen(m, state, resume=False))
        await pilot.pause(0.5)
        await pilot.press("f4")
        await pilot.pause()
        sheet = app.screen
        assert isinstance(sheet, SheetModal) and sheet.party[sheet.at].id == party[0].id
        await pilot.press("right")
        assert sheet.party[sheet.at].id == party[-1].id
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, GameScreen)


async def test_the_command_box_completes_dice_commands_and_skills():
    m = Module.load(EXAMPLE)
    suggest = command_suggester(m.pregens[0]).get_suggestion
    assert await suggest(".ra 侦") == ".ra 侦查"
    assert await suggest(".rab 图") == ".rab 图书馆使用"  # the investigator's own skills first
    assert await suggest(".ra 困难聆") == ".ra 困难聆听"  # every skill, not only theirs
    assert await suggest(".s") == ".st" and await suggest("/n") == "/notes"


@pytest.mark.parametrize("text, expected", [
    (".ra 侦", ".ra 侦查"), (".rab 图", ".rab 图书馆使用"),
    (".rap2 困难聆", ".rap2 困难聆听"), ("。RCp2 极难力", ".rcp2 极难力量"),
])
def test_skill_completion_keeps_dice_modifiers(text, expected):
    from autokeeper.tui.widgets.panels import skill_completions

    inv = Module.load(EXAMPLE).pregens[0]
    assert expected in skill_completions(text, inv)
    assert not skill_completions("我想侦查", inv)
    assert not skill_completions(".ra 侦查50", inv)


@pytest.mark.usefixtures("canned")
async def test_tab_cycles_skills_without_sending_an_action(monkeypatch):
    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="tab-skills")
    app = AutoKeeperApp(theme="amber")
    opened = []
    monkeypatch.setattr(app, "open_url", lambda url: opened.append(url))
    async with app.run_test(size=(120, 40)) as pilot:
        screen = GameScreen(m, st, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        box = screen.query_one("#command", Input)
        box.value = ".ra"
        box.cursor_position = len(box.value)
        before = st.model_dump_json()
        await pilot.press("tab")
        first = box.value
        assert first.startswith(".ra ") and box.has_focus
        await pilot.press("tab")
        assert box.value != first and box.has_focus
        await pilot.press("shift+tab")
        assert box.value == first
        box.value = ".rap2 困难聆"
        box.cursor_position = len(box.value)
        await pilot.press("tab")
        assert box.value == ".rap2 困难聆听"
        assert st.model_dump_json() == before
        await pilot.press("f12")
        assert await until(pilot, lambda: bool(opened))
        assert app.screen is screen and box.has_focus  # the private webpage leaves the terminal playable
        await pilot.press("tab")
        assert st.model_dump_json() == before


async def test_an_npc_line_shows_their_face_never_the_keepers_note_on_them(canned):
    m = Module.load(paths.MODULES / "zhuishuren")
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="npc-line", human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(GameScreen(m, state, resume=True))
        await pilot.pause(0.5)
        screen = app.screen
        await screen.handle({"type": "npc", "npc": "douglas", "name": "道格拉斯", "text": "……走。"})
        await screen.handle({"type": "npc", "npc": "douglas", "name": "道格拉斯", "text": "别回头。"})
        shown = story_text(screen)
        assert "圆框眼镜" in shown and shown.count("圆框眼镜") == 1  # what they see, the first time
        assert "偷书贼" not in shown and "半食尸鬼" not in shown  # the role is the keeper's note
        speaker = screen.query_one(Speaker)
        assert await until(pilot, lambda: speaker.display and speaker.who == "道格拉斯")  # the face is on the stage


def test_each_turn_s_tokens_are_kept():
    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    reply = Reply(text="", usage={"input_tokens": 1000, "cache_read": 800, "output_tokens": 100}, cost_usd=0.001)
    st.record_usage("keeper", reply)
    st.turn = 1
    st.tally()
    st.record_usage("keeper", reply)
    st.record_usage("arbiter", reply)
    st.turn = 2
    st.tally()
    st.tally()  # nothing new: no row
    assert [(t["turn"], t["calls"], t["input"]) for t in st.turn_usage] == [(1, 1, 1000), (2, 2, 2000)]


async def test_ctrl_b_shows_where_the_tokens_went(canned):
    from autokeeper.tui.modals.usage import UsageModal

    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="usage", human=[lead.id])
    state.record_usage("keeper", Reply(text="", usage={"input_tokens": 12000, "cache_read": 9000, "output_tokens": 800}, cost_usd=0.002))
    state.turn = 1
    state.tally()
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(GameScreen(m, state, resume=True))
        await pilot.pause(0.5)
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert isinstance(app.screen, UsageModal)
    from rich.console import Console

    from autokeeper.tui.modals.usage import usage_view

    console = Console(file=io.StringIO(), width=120, record=True)
    console.print(usage_view(state))
    shown = console.export_text()
    assert "守秘人" in shown and "12.0k" in shown and "75%" in shown and "每回合" in shown


async def test_typing_lands_in_the_command_box_wherever_the_focus_went(canned):
    from textual import events

    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="focus", human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(GameScreen(m, state, resume=True))
        await pilot.pause(0.5)
        screen = app.screen
        box = screen.query_one("#command", Input)
        screen.query_one(StoryLog).focus()  # a click on the story
        await pilot.pause()
        assert not box.has_focus
        await pilot.press("a", "b")
        assert box.value == "ab" and box.has_focus
        screen.query_one(StoryLog).focus()
        await pilot.pause()
        screen.post_message(events.Paste("侦查\n第二行"))
        await pilot.pause()
        assert box.value == "ab侦查" and box.has_focus


async def test_meta_lines_stay_off_the_story_and_a_waived_check_is_said(canned):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="meta", human=[lead.id])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        app.push_screen(GameScreen(m, state, resume=True))
        await pilot.pause(0.5)
        screen = app.screen
        await screen.handle({"type": "improv", "kind": "展开", "summary": "报童小六在钟楼下卖报", "reason": "想找人打听"})
        await screen.handle({"type": "action", "kind": "investigate", "summary": "仔细看楼梯", "asked": {"skill": "侦查"}})
        await screen.handle({"type": "ruling", "needs_roll": True, "skill": "侦查", "difficulty": "常规"})
        await screen.handle({"type": "waived", "who": lead.name, "skill": "侦查"})
        shown = story_text(screen)
        assert "模组没写到" not in shown and "裁定员" not in shown and "需要检定" not in shown and "报童小六" not in shown
        assert f"{lead.name} 的侦查不用掷" in shown


def test_names_light_up_in_the_telling_and_the_history_keeps_a_line_per_event():
    from autokeeper.engine.state import LogEntry
    from autokeeper.tui.widgets.panels import HistoryPanel, StoryLog, history_line, log_line

    m = Module.load(EXAMPLE)
    me = m.pregens[0]
    friend = me.model_copy(update={"id": "friend", "name": "贝蒂·康西丁"})
    story = StoryLog()
    story.mark([me, friend], me.id)
    lit = story.lit(Text(f"贝蒂回头看了{me.name}一眼"))
    styles = {lit.plain[s.start:s.end]: str(s.style) for s in lit.spans}
    assert styles == {"贝蒂": StoryLog.OTHERS, me.name: StoryLog.MINE}  # "贝蒂" alone is still her
    assert history_line("player", Text("▶ 贝蒂·康西丁（sq）：仔细听"), None).plain == "贝蒂·康西丁（sq）：仔细听"
    assert history_line("keeper", Text("一大段叙述"), None) is None  # the telling stays in the story
    clue = LogEntry(turn=5, time="19:20", kind="clue", text="〔有人入室〕后屋传来窗框被撬开的吱呀声")
    assert log_line(clue).plain == "  ↳ 〔有人入室〕"
    assert HistoryPanel.KEEP > 0


def test_dice_skins_draw_both_dice_in_the_pixel_font():
    from autokeeper import config as cfg_mod
    from autokeeper.tui.drawing import dice

    assert set(dice.SKINS) == {"bone", "obsidian", "blood", "brass", "eldritch", "phosphor"}
    assert dice.digits("30") == ["▀▀█ █▀█", " ▀█ █ █", "▀▀▀ ▀▀▀"]
    for key, skin in dice.SKINS.items():
        shown = dice.pair("30", "7", skin, level="常规成功").plain.split("\n")
        assert len(shown) == dice.HEIGHT + 2 and len({len(line) for line in shown}) == 1, key  # both dice, aligned
    assert dice.skin("nonsense") is dice.SKINS[dice.DEFAULT]
    assert cfg_mod.Config().dice == dice.DEFAULT


@pytest.mark.parametrize("width", [100, 70, 55, 40])
def test_a_sheet_s_photo_keeps_its_rows_in_a_narrow_pane(width):
    """Squeezed into a narrow column, each line of a drawn photo used to wrap in two: stripes."""
    from rich.console import Console

    from autokeeper.tui.modals.sheet import sheet_view

    inv = Module.load(EXAMPLE).pregens[0]
    face = Text("\n".join("▓" * 30 for _ in range(14)))
    console = Console(file=io.StringIO(), width=width, record=True, color_system=None)
    console.print(sheet_view(inv, face, width))
    rows = [line for line in console.export_text().splitlines() if "▓" in line]
    assert len(rows) == 14 and all("▓" * 30 in line for line in rows)
