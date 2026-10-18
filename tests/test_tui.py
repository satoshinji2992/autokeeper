"""Terminal UI smoke tests (no model calls): title, setup flow, small terminals, pictures."""

from __future__ import annotations

import os
import shutil

import pytest
from PIL import Image
from rich.text import Text
from textual.widgets import Input, RadioButton

from autokeeper import artkit, config, imagery
from autokeeper.config import EXAMPLE, Role
from autokeeper.engine.game import Game
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.tui import app as tui
from autokeeper.tui.app import AutoKeeperApp, GameScreen, LoadScreen, SetupScreen, TitleScreen
from autokeeper.tui.widgets import SceneArt


@pytest.mark.parametrize("size", [(140, 44), (80, 24)])
async def test_title_and_setup_flow(monkeypatch, size, skills_dir):
    monkeypatch.setattr(tui, "modules", lambda: [EXAMPLE])
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


async def test_loading_a_save_opens_the_game_at_night(monkeypatch, tmp_path, skills_dir):
    for var in ("AUTOKEEPER_PRESET", "AUTOKEEPER_MODEL", "AUTOKEEPER_FALLBACK", "AUTOKEEPER_MODE"):  # F3 sets these; undo afterwards
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    monkeypatch.setattr(config, "SAVES", tmp_path)
    monkeypatch.setattr(tui, "modules", lambda: [EXAMPLE])
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="night", human=[lead.id])
    state.minutes = 23 * 60 + 10
    tui.write_save(state, m)
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
        await pilot.press("f6")
        await pilot.pause(0.2)
        assert scene._atmo is None  # weather off
        screen = app.screen
        before, memory = screen.cast, screen.state.transcripts
        await pilot.press("f3")  # change models mid-game
        await pilot.pause()
        app.screen.query(RadioButton).last().value = True  # 高级剧情
        await pilot.click("#apply")
        await pilot.pause(0.3)
        assert screen.cast is not before and screen.game.cast is screen.cast and os.environ["AUTOKEEPER_MODE"] == "高级剧情"
        assert screen.state.transcripts is memory  # the agents' memories carry over
        screen.busy = True  # mid-turn: the next action can be typed, but waits for the turn
        box = screen.query_one("#command", Input)
        box.value = "我爬上钟楼"
        await pilot.press("enter")
        await pilot.pause()
        assert box.value == "我爬上钟楼" and not box.disabled
        screen.busy = False


def test_weather_is_read_off_the_place_and_the_hour():
    from autokeeper import weather

    assert weather.infer("公墓", "墓碑之间点缀着灌木。", "深夜") == ["fog"]
    assert weather.infer("公墓", "墓碑之间点缀着灌木。", "上午") == []
    assert weather.infer("书房", "窗外就是公墓的树影。", "深夜") == []  # the graveyard is only out of the window
    assert weather.infer("隧道", "手电的光只能照出前面几步。", "上午") == ["torch"]
    assert weather.infer("大街", "梧桐叶子在人行道上打着旋。", "下午") == ["leaves"]
    assert weather.infer("图书馆", "长桌上方吊着绿罩台灯。", "深夜") == ["flicker"]
    assert weather.infer("码头", "雨后的石板路。", "下午") == []
    assert [weather.outdoors(n) for n in ("公墓", "艾尔斯伯里大街", "广场", "道格拉斯的书房", "警察局", "食尸鬼隧道网", "钟楼")] == [
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
def test_scene_picture_fills_the_panel_without_losing_the_subject(tmp_path):
    img = tmp_path / "tall.png"
    Image.new("RGB", (200, 300), (180, 60, 40)).save(img)
    framed = imagery.frame(Image.open(img).convert("RGB"), 80, 15)
    assert abs(framed.width / framed.height - 80 * 0.5 / 15) < 0.05  # the panel's shape
    assert framed.getpixel((framed.width // 2, framed.height // 2)) == (180, 60, 40)  # subject intact
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
