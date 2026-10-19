"""The speaker on the stage: an NPC's mood read off their own line (no model), their portrait
in that mood, and the panel that shows it while they talk (no model calls)."""

from __future__ import annotations

import shutil
import threading
import time

import numpy as np
import pytest
from PIL import Image, ImageDraw
from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from autokeeper import artkit, imagery, mood, paths
from autokeeper.engine.game import Game
from autokeeper.module.schema import NPC, Module
from autokeeper.paths import EXAMPLE
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.game import GameScreen
from autokeeper.tui.widgets import speaker as stage
from autokeeper.tui.widgets.panels import SceneArt, StoryLog
from autokeeper.tui.widgets.speaker import Portrait, Speaker, face

from .test_tui import canned, story_text, until  # noqa: F401  (canned: a fixture, used by name)

THOMAS = "三十来岁，衬衫袖子挽到手肘，梳得一丝不苟的头发，说话时爱搓手"


@pytest.mark.parametrize(
    ("line", "felt"),
    [
        ("（声音压低了些）这事儿……我只跟您一个人说。", "警惕"),
        ("（眯起眼睛，上下打量你）你们是哪儿来的？", "警惕"),
        ("（手在发抖）别、别过来……求求你……", "害怕"),
        ("（脸色发白，往后退了一步）那……那是什么东西？", "害怕"),
        ("（一拍桌子）滚出去！我这儿不欢迎你们！", "发怒"),
        ("（攥紧拳头，咬着牙）他们会付出代价的。", "发怒"),
        ("（叹了口气，眼圈红了）叔叔他……走了这么久了。", "难过"),
        ("（笑眯眯地招手）快请进，外面冷，喝杯热茶吧。", "友善"),
        ("（慌忙把信塞进抽屉）没、没什么，是些旧账单。", "慌乱"),
        ("（猛地站起来）你说什么？！", "慌乱"),
        ("我、我什么也没看见！", "慌乱"),  # a stammer
        ("钟只是老了，没什么好看的。", "平静"),  # nothing to go on
        ("恐怕他不会回来了。", "平静"),  # 怕 inside 恐怕 is no fear
        ("我不怕你们。", "平静"),
        ("我没生气，你说吧。", "平静"),  # negated
        ("开玩笑了，这有什么可笑的。", "平静"),  # 笑 inside 可笑 is no smile
        ("（醒木一拍）列位看官！", "平静"),  # a storyteller's gesture, not a fist on the table
        ("（抬手往钟面那边指了指）上去看吧。", "平静"),
    ],
)
def test_a_line_reads_as_a_mood(line, felt):
    assert mood.read(line) == felt


def test_habits_and_performances_say_nothing_of_the_mood():
    rubbing = "（搓着手）这事儿我只跟您一个人说。"
    assert mood.read(rubbing) == "慌乱"
    assert mood.read(rubbing, habits=THOMAS) == "平静"  # he always rubs his hands when he talks
    assert mood.read("（搓着手，声音压低了些）这事儿……", habits=THOMAS) == "警惕"
    story = "（清清嗓子）话说那一夜，周先生吓得浑身发抖，哭着喊救命。"
    assert mood.read(story) == "害怕"
    assert mood.read(story, words=False) == "平静"  # a performance: the fear is the story's, not the teller's
    assert set(artkit.TONES) == set(mood.MOODS) - {mood.CALM}  # every mood but calm has its colours


def test_hand_drawn_portraits_change_colour_by_mood_and_are_cached():
    m = Module.load(EXAMPLE)
    p = Portrait.of(m, m.npc("warden"))
    assert p is not None and not p.picture and p.art
    calm, angry, afraid = face(p, "平静"), face(p, "发怒"), face(p, "害怕")
    assert calm.plain == angry.plain == afraid.plain == m.art["warden"]  # the same drawing

    def colours(t: Text) -> list:
        return [tuple(Style.parse(str(span.style)).color.triplet) for span in t.spans]

    def mean(t: Text) -> np.ndarray:
        return np.array(colours(t), dtype=float).mean(axis=0)

    assert colours(calm) != colours(angry) != colours(afraid)
    red, cold, base = mean(angry), mean(afraid), mean(calm)
    assert red[0] - red[2] > base[0] - base[2]  # redder
    assert cold[2] - cold[0] > base[2] - base[0] and cold.sum() < base.sum()  # bluer and darker
    assert face(p, "发怒") is angry  # cached


def test_pictures_take_a_mood_and_keep_their_dark_backdrop():
    im = Image.new("RGB", (60, 80), (6, 10, 12))  # a portrait's cut-out backdrop
    ImageDraw.Draw(im).ellipse([15, 10, 45, 60], fill=(200, 170, 150))  # and a face
    calm = np.asarray(im, dtype=float)
    for name, check in (
        ("发怒", lambda a: a[..., 0].mean() - a[..., 2].mean() > calm[..., 0].mean() - calm[..., 2].mean()),
        ("害怕", lambda a: a[..., 2].mean() - a[..., 0].mean() > calm[..., 2].mean() - calm[..., 0].mean() and a.mean() < calm.mean()),
        ("难过", lambda a: np.ptp(a[40, 30]) < np.ptp(calm[40, 30])),  # drained of colour
        ("友善", lambda a: a[40, 30, 0] - a[40, 30, 2] > calm[40, 30, 0] - calm[40, 30, 2]),  # warmer
    ):
        graded = np.asarray(imagery.grade(im, name), dtype=float)
        assert check(graded), name
        assert graded[2, 2].max() < 20  # the backdrop stays dark: the mood lights the face, not the air


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
def test_picture_portraits_are_rendered_once_per_mood_size_and_style(tmp_path):
    img = tmp_path / "face.jpg"
    im = Image.new("RGB", (240, 300), (6, 10, 12))
    ImageDraw.Draw(im).ellipse([50, 40, 190, 240], fill=(210, 175, 150))
    im.save(img)
    p = Portrait(picture=str(img))
    face.cache_clear()
    calm = face(p, "平静", (20, 12), "picture")
    angry = face(p, "发怒", (20, 12), "picture")
    assert calm.plain.strip() and len(calm.split("\n")) <= 12
    assert [s.style for s in calm.spans] != [s.style for s in angry.spans]  # other colours
    assert face(p, "发怒", (20, 12), "picture") is angry and face.cache_info().hits == 1
    assert face(p, "发怒", (20, 12), "ascii") is not angry  # another style is another variant


def speaking(screen) -> Speaker:
    return screen.query_one(Speaker)


# Textual lays a widget out on the refresh after it changes, so a test that reads a size or a
# region waits for that, not only for the attribute that changed (display, _art).
def placed(panel: Speaker) -> bool:
    """The panel is shown, and on screen."""
    return panel.display and panel.region.height > 0


def drawn(scene: SceneArt) -> bool:
    """The scene's art is in, and the panel has taken its height."""
    return scene._art is not None and scene.outer_size.height == scene.rows


@pytest.mark.usefixtures("canned")
async def test_a_portrait_finishing_after_the_scene_is_removed_does_not_crash(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        started.set()
        release.wait(timeout=5)
        return mood.CALM

    monkeypatch.setattr(mood, "read", delayed)
    m = Module.load(EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    app = AutoKeeperApp(theme="amber")
    try:
        async with app.run_test(size=(140, 44)) as pilot:
            screen = GameScreen(m, state, resume=True)
            app.push_screen(screen)
            assert await until(pilot, lambda: screen.game is not None and not screen.busy)
            await screen.handle({"type": "npc", "npc": "warden", "name": "守钟人", "text": "坐下喝茶吧。"})
            assert await until(pilot, started.is_set)
            panel = speaking(screen)
            await screen.query_one(SceneArt).remove()
            release.set()
            await app.workers.wait_for_complete()
            assert not panel.display
    finally:
        release.set()


@pytest.mark.usefixtures("canned")
async def test_the_speaker_steps_in_while_they_talk_and_out_when_the_scene_moves_on():
    m = Module.load(EXAMPLE)
    m.npcs.append(NPC(id="stranger", name="陌生人", role="路过的人", location="tower"))  # no portrait
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="speaker", human=[lead.id])
    state.location = "tower"  # no picture of the place: the stage grows to hold the face
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        panel, scene = speaking(screen), screen.query_one(SceneArt)
        assert not panel.display
        bare = screen.query_one("#stage").outer_size.height

        async def line(npc: str, name: str, text: str) -> None:
            await screen.handle({"type": "npc", "npc": npc, "name": name, "text": text})

        await line("warden", "老守钟人", "（一拍桌子）滚出去！钟楼不是你们该来的地方！")
        assert await until(pilot, lambda: placed(panel) and panel.feeling == "发怒")
        assert m.art["warden"].split("\n")[3].strip() in str(panel.query_one(".face", Static).render())  # the hand-drawn face
        assert str(panel.query_one(".who", Static).render()) == "老守钟人"
        stage_box = screen.query_one("#stage")
        assert stage_box.outer_size.height > bare  # grew to hold the face
        assert panel.region.right == stage_box.region.right and panel.region.height == stage_box.size.height
        assert scene.outer_size.width == stage_box.outer_size.width  # the scene keeps its width under the panel
        assert "钟楼不是你们该来的地方" in story_text(screen) and "发怒" not in story_text(screen)  # the mood is never told

        await screen.handle({"type": "keeper_start"})  # the narration of the turn they spoke in
        await screen.handle({"type": "keeper_end", "text": "老人背过身去。"})
        assert panel.display
        await screen.handle({"type": "keeper_start", "again": True})  # told again: the same turn
        await screen.handle({"type": "keeper_end", "text": "老人背过身去，不再理你。"})
        assert panel.display
        await screen.handle({"type": "keeper_start"})  # a turn told without them
        await pilot.pause()
        assert not panel.display and stage_box.outer_size.height == bare

        await line("warden", "老守钟人", "（叹了口气）那口钟啊……")
        assert await until(pilot, lambda: panel.display and panel.feeling == "难过")
        await line("preacher", "街头布道者", "（压低声音）午夜之前，离开这里。")  # two in one exchange: the latest
        assert await until(pilot, lambda: panel.who == "街头布道者" and panel.feeling == "警惕")
        await screen.handle({"type": "event", "id": "e_dusk", "title": "黄昏", "text": "钟声响了。"})  # an event takes the stage
        await pilot.pause()
        assert not panel.display

        await line("warden", "老守钟人", "上去看吧。")
        assert await until(pilot, lambda: panel.display and panel.feeling == "平静")
        await line("stranger", "陌生人", "借过。")  # no face to show: not the old one either
        await pilot.pause()
        assert not panel.display

        await line("warden", "老守钟人", "上去看吧。")
        assert await until(pilot, lambda: panel.display)
        panel.since = time.monotonic() - stage.LINGER - 1  # nothing said for a while
        assert await until(pilot, lambda: not panel.display)

        await line("warden", "老守钟人", "上去看吧。")
        assert await until(pilot, lambda: panel.display)
        await screen.handle({"type": "move", "location": "square", "name": "广场"})  # the party moves on
        await pilot.pause()
        assert not panel.display and scene.place == "广场"


@pytest.mark.usefixtures("canned")
async def test_fear_trembles_anger_flashes_and_f6_stills_them():
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="motion", human=[lead.id])
    state.location = "tower"
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        panel = speaking(screen)
        portrait = panel.query_one(".face", Static)

        async def watch(text: str) -> tuple[set, set]:
            await screen.handle({"type": "npc", "npc": "warden", "name": "老守钟人", "text": text})
            offsets, tints = set(), set()
            started = time.monotonic()
            while time.monotonic() - started < 3.0:  # every motion is over within a second (a slow runner: three)
                await pilot.pause(0.02)
                offsets.add((portrait.styles.offset.x.value, portrait.styles.offset.y.value))
                tints.add(round(portrait.styles.tint.a, 2))
            return offsets, tints

        offsets, _ = await watch("（浑身哆嗦）别、别上去……")
        why = (panel.display, panel._fitted and panel._fitted[2:], screen.query_one(SceneArt).effects, panel.portrait is not None)
        assert panel.feeling == "害怕" and len(offsets) > 1, why  # trembled
        assert await until(pilot, lambda: portrait.styles.offset.x.value == 0, 60)  # then still (a slow runner takes longer)
        _, tints = await watch("（一拍桌子）滚！")
        assert panel.feeling == "发怒" and max(tints) > 0.2  # flashed
        assert await until(pilot, lambda: portrait.styles.tint.a == 0, 60)  # then gone
        await screen.run_action("weather")  # weather off: the portraits keep still too
        offsets, tints = await watch("（浑身哆嗦）别过来！")
        assert panel.feeling == "害怕" and offsets == {(0, 0)} and max(tints) == 0


@pytest.mark.usefixtures("canned")
async def test_a_small_terminal_gets_a_smaller_speaker_and_a_tiny_one_none():
    m = Module.load(paths.MODULES / "zhuishuren")
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="small", human=[lead.id])
    state.location = "jingju"  # the police station: a wide hand-drawn scene
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy and drawn(screen.query_one(SceneArt)))
        panel, scene, stage_box = speaking(screen), screen.query_one(SceneArt), screen.query_one("#stage")
        before, room = stage_box.outer_size.height, screen.query_one(StoryLog).region.height
        await screen.handle({"type": "npc", "npc": "jingguan", "name": "值班警官", "text": "（眯起眼睛）你们又来干什么？"})
        assert await until(pilot, lambda: placed(panel) and scene.covered, 100)
        assert panel.region.width <= stage_box.region.width * stage.WIDEST and panel.region.height == stage_box.size.height
        assert stage_box.outer_size.height == before and screen.query_one(StoryLog).region.height == room  # the story keeps its room

        def right_edge() -> int:  # where the drawing ends, on screen
            return scene.region.x + 1 + max(len(ln.rstrip()) for ln in scene._art.plain.split("\n"))

        assert await until(pilot, lambda: right_edge() <= panel.region.x, 100)  # the drawing moved over to clear the face
        await screen.handle({"type": "keeper_start"})
        await screen.handle({"type": "keeper_start"})
        assert await until(pilot, lambda: not panel.display and scene.covered == 0, 100)
        await pilot.resize_terminal(44, 20)  # too narrow for a panel: the story carries on without it
        await pilot.pause(0.3)
        await screen.handle({"type": "npc", "npc": "jingguan", "name": "值班警官", "text": "走吧。"})
        await pilot.pause(0.3)
        assert not panel.display and "走吧" in story_text(screen)


@pytest.mark.skipif(shutil.which("chafa") is None, reason="chafa not installed")
@pytest.mark.usefixtures("canned")
async def test_a_photo_portrait_follows_the_stage_and_the_art_style():
    m = Module.load(paths.MODULES / "zhuishuren")
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="photo", human=[lead.id])
    state.location, state.visited = "jinboer_jia", ["jinboer_jia"]
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True)
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy and drawn(screen.query_one(SceneArt)))
        panel, stage_box = speaking(screen), screen.query_one("#stage")
        before = stage_box.outer_size.height
        await screen.handle({"type": "npc", "npc": "thomas", "name": "托马斯·金博尔", "text": "（搓着手，声音压低了些）这事儿……"})
        assert await until(pilot, lambda: placed(panel) and panel.feeling == "警惕")
        assert stage_box.outer_size.height == before  # a picture scene is tall enough already
        assert panel.region.height == stage_box.size.height and panel._fitted[2][1] == panel.region.height - 1
        assert "圆框眼镜" not in story_text(screen) and "搓手" in story_text(screen)  # his look, the first time
        await pilot.press("ctrl+a")
        assert await until(pilot, lambda: panel._fitted and panel._fitted[3] == "ascii")  # drawn again in the new style (photo → ascii)
        await pilot.resize_terminal(80, 24)
        assert await until(pilot, lambda: panel._fitted and panel.region.height == stage_box.size.height and panel._fitted[2][1] == panel.region.height - 1)
        assert stage_box.size.height < before
