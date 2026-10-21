"""Screenshots for the README: fixed scenes of the game, run headless, saved as PNG.

    uv run --extra claude python dev/screenshots.py            # all of them
    uv run --extra claude python dev/screenshots.py map game   # some

Each scene starts from a bundled module with a few turns already in the log; nothing calls a
model (the recap is fixed text) and nothing is written to the real data folder. Textual saves an
SVG; headless Chrome turns it into a PNG. The SVG names Fira Code, which has no Chinese, and a
fallback font draws Chinese narrower than its two cells, so names look letter-spaced; the SVG is
pointed at Noto Sans Mono CJK SC (two cells to a Chinese character, as a terminal draws it)
before it is drawn. Install that font and google-chrome first. PNGs go to dev/output/screenshots/;
copy the ones you want into docs/img/.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dev" / "output" / "screenshots"
os.environ.setdefault("AUTOKEEPER_PRESET", "deepseek")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-screenshots")  # never used: no turn is played

from PIL import Image, ImageChops  # noqa: E402
from rich.cells import cell_len  # noqa: E402

from autokeeper import paths  # noqa: E402
from autokeeper.agents.cast import Cast  # noqa: E402
from autokeeper.engine.game import Game  # noqa: E402
from autokeeper.module.schema import Module  # noqa: E402
from autokeeper.tui.app import AutoKeeperApp  # noqa: E402
from autokeeper.tui.modals.dialogs import HandoutModal  # noqa: E402
from autokeeper.tui.screens.game import GameScreen  # noqa: E402

SIZE = (150, 44)
FONT = '"Noto Sans Mono CJK SC", "DejaVu Sans Mono", monospace'

STORY = {
    "zhuishuren": [
        ("player", "", "我去图书馆翻翻旧报纸，看有没有关于金博尔家的报道"),
        ("keeper", "守秘人", "阅览室里只有暖气管的咔嗒声。管理员把一摞装订好的《阿诺兹堡广告报》推到你面前，"
                            "纸页黄得发脆。翻到一九一九年的秋天，一篇不起眼的短讯夹在农产品价格中间。"),
        ("npc", "图书管理员", "那几年的报纸没几个人借，您要找什么，我帮您看看索引。"),
    ],
    "dongxuezhixia": [
        ("player", "", "我推门进邮局，看看有没有寄给赫琴斯家的电报"),
        ("keeper", "守秘人", "邮局里比街上暖和不了多少。柜台后一整面墙的信格，有一半空着，有一半塞着发黄的信封。"
                            "窗外的雪又下密了，落在门口那块写着「营业」的木牌上。"),
    ],
}


RECAP = {
    "zhuishuren": "早班火车停在阿诺兹堡。你向站务员打听了金博尔家的方向，又在镇中心的图书馆翻出一篇旧报道。",
    "dongxuezhixia": "雪下了一夜。你在主街的诊所见过了安特医生，邮局的柜台后面也许有赫琴斯家没取走的电报。",
}


async def _recap(self, state, on_delta=None):
    text = RECAP.get(state.module, "")
    if on_delta:
        await on_delta(text)
    return text


Cast.recap = _recap  # type: ignore[method-assign]


def game_state(mid: str, location: str, visited: list[str], met: tuple[str, ...] = ()):
    m = Module.load(paths.MODULES / mid)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot=f"screenshot-{mid}")
    st.pending_turn, st.prologue, st.turn = {}, "（开场已讲过）", 6
    st.location, st.visited = location, list(visited)
    st.heard = {loc.id: loc.name for loc in m.locations}
    for npc in met:
        st.npc(npc).met = True
    for kind, who, text in STORY.get(mid, []):
        st.add(kind, text, who=who or st.investigator.name)
    return m, st


async def until(pilot, test, rounds: int = 80) -> None:
    for _ in range(rounds):
        if test():
            return
        await pilot.pause(0.1)


async def in_game(name: str, mid: str, location: str, visited: list[str], met: tuple[str, ...] = (), then=None) -> Path:
    m, st = game_state(mid, location, visited, met)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=SIZE) as pilot:
        screen = GameScreen(m, st, resume=True)
        app.push_screen(screen)
        await until(pilot, lambda: screen.game is not None and not screen.busy)
        await pilot.pause(3.0)  # the picture drawn, in a thread
        if then is not None:
            await then(app, pilot, screen, m)
            await pilot.pause(2.0)
        return Path(app.save_screenshot(f"{name}.svg", path=str(OUT)))


async def title() -> Path:
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(2.0)
        return Path(app.save_screenshot("title.svg", path=str(OUT)))


async def host() -> Path:
    from autokeeper.tui.screens.host import HostScreen

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(0.5)
        app.push_screen(HostScreen())
        await pilot.pause(0.5)
        menu = app.screen.query_one("#choices")
        menu.highlighted = next(i for i in range(menu.option_count) if "dongxuezhixia" in (menu.get_option_at_index(i).id or ""))
        await pilot.press("enter")
        await pilot.pause(2.0)
        return Path(app.save_screenshot("host.svg", path=str(OUT)))


ZHUI = ["huochezhan", "zhenzhongxin", "tushuguan", "jingju", "baoshe", "jiefang", "jinboer_jia", "shufang"]
DONG = ["main_street", "ratger_house", "clinic", "church", "post_office", "brucker_farm", "satin_hill", "darvill_house"]


async def _map(app, pilot, screen, m):
    await pilot.press("f3")


def _handout(hid: str):
    async def show(app, pilot, screen, m):
        h = next(x for x in m.handouts if x.id == hid)
        app.push_screen(HandoutModal(h.title, h.text, kind=h.kind))
        await pilot.pause(2.5)  # the ink soaked in
    return show


async def _models(app, pilot, screen, m):
    await pilot.press("f9")
    await pilot.pause(0.8)
    app.screen.query_one("#engine-tabs").active = "engine-roles"


async def _developer(app, pilot, screen, m):
    screen.dev_url = "http://127.0.0.1/"  # the page is not opened for a screenshot
    screen.action_developer()


async def _talk(app, pilot, screen, m):
    """A conversation as it comes in: the cook's reply, the keeper's telling with a passer-by's line."""
    await screen.handle({"type": "npc", "npc": "doreen_ratger", "name": "多琳·拉特格", "portrait": "",
                         "text": "（手停在围裙上，把面粉拍了拍）死人？这种话在我们这儿可不兴这么大声说。年头不好，"
                                 "老人孩子一场病就过去了……您打听这个做什么？您是从城里来的吧，看您这双鞋就知道。"})
    await screen.handle({"type": "keeper_start"})
    await screen.handle({"type": "keeper_end", "text": "弗朗西斯把碗往桌上一放，窗外有辆卡车慢慢停在街角，车斗上盖着油布。\n\n"
                         "【开卡车的年轻人】（探进门来，哈出一口白气）拉特格太太，赫琴斯让我问一声，今晚还留不留汤？\n\n"
                         "多琳朝他摆摆手，他又咳了两声，退回雪里去了。"})


SHOTS = {
    "title": title,
    "game": lambda: in_game("game", "zhuishuren", "tushuguan", ZHUI),
    "painted": lambda: in_game("painted", "dongxuezhixia", "post_office", DONG),
    "map": lambda: in_game("map", "dongxuezhixia", "main_street", DONG, then=_map),
    "handout": lambda: in_game("handout", "zhuishuren", "shufang", ZHUI, then=_handout("h_diary")),
    "telegram": lambda: in_game("telegram", "dongxuezhixia", "main_street", DONG, then=_handout("h_telegram")),
    "models": lambda: in_game("models", "zhuishuren", "tushuguan", ZHUI, then=_models),
    "developer": lambda: in_game("developer", "zhuishuren", "jingju", ZHUI, met=("jingguan",), then=_developer),
    "host": host,
    "talk": lambda: in_game("talk", "dongxuezhixia", "ratger_house", DONG, met=("doreen_ratger",), then=_talk),
}


def _cells(m: re.Match) -> str:
    """A run of text as long as the cells it takes: Textual measures it in characters, so a
    Chinese run was squeezed into half its room and the next run started after a gap. Spaces
    at either end become an offset: stretched with the glyphs they came out uneven."""
    head, length, tail, body = m.group(1), float(m.group(2)), m.group(3), m.group(4)
    plain = html.unescape(body)
    if not plain:
        return m.group(0)
    cell = length / len(plain)
    lead = len(plain) - len(plain.lstrip("\xa0 "))
    core = plain.strip("\xa0 ")
    if not core:
        return m.group(0)
    x = re.search(r'x="([\d.]+)"', head)
    if x:
        head = head.replace(x.group(0), f'x="{float(x.group(1)) + lead * cell:g}"', 1)
    return f'{head}lengthAdjust="spacingAndGlyphs" textLength="{cell * cell_len(core):g}"{tail}>{html.escape(core)}</text>'


def png(svg: Path) -> Path:
    """The SVG drawn by headless Chrome, at its own size, Chinese two cells wide."""
    text = re.sub(r'(<text[^>]*?)textLength="([\d.]+)"([^>]*)>([^<]*)</text>', _cells, svg.read_text(encoding="utf-8"))
    text = re.sub(r'font-family:\s*"?Fira Code"?(, monospace)?', f"font-family: {FONT}", text)
    svg.write_text(text, encoding="utf-8")
    box = re.search(r'viewBox="[\d.]+ [\d.]+ ([\d.]+) ([\d.]+)"', text)
    width, height = (int(float(v)) for v in box.groups()) if box else (1800, 1100)
    out = svg.with_suffix(".png")
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"
    subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                    f"--window-size={width},{height}", f"--screenshot={out}", svg.as_uri()],
                   check=True, capture_output=True, timeout=120)
    im = Image.open(out).convert("RGB")  # the page round the window, cut away
    box = ImageChops.difference(im, Image.new("RGB", im.size, im.getpixel((im.width - 1, im.height - 1)))).getbbox()
    if box:
        im.crop(box).save(out, optimize=True)
    return out


async def main(names: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    scratch = OUT / "data"
    shutil.rmtree(scratch, ignore_errors=True)
    paths.DATA, paths.USER_DIR = scratch, OUT / "user"  # nothing written to the real data folder
    for name in names or list(SHOTS):
        svg = await SHOTS[name]()
        print(png(svg))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
