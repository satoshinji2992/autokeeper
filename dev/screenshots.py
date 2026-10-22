"""Screenshots for the README: fixed scenes of the game, run headless, saved as PNG.

    uv run --extra claude python dev/screenshots.py            # all of them
    uv run --extra claude python dev/screenshots.py map game   # some

Each scene starts from a bundled module with a few turns already in the log; nothing calls a
model (the recap is fixed text) and nothing is written to the real data folder. The fights and the
chases are the real encounter engine's: a scripted keeper plan and scripted dice (as the tests' own,
tests/encounter_kit.py) play a round before the screen opens on it, and the screen is given what that
round showed. Textual saves an
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
os.environ.setdefault("COLORTERM", "truecolor")  # pictures in 24-bit colour, as most terminals show them

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


async def in_game(name: str, mid: str, location: str, visited: list[str], met: tuple[str, ...] = (), then=None,
                  size: tuple[int, int] = SIZE, settle: float = 2.0, prepare=None) -> Path:
    """A game screen on the module at `location`. `prepare(m, st)` plays what comes before the screen
    opens (a fight's first round) and returns what `then(app, pilot, screen, m, played)` shows on it."""
    m, st = game_state(mid, location, visited, met)
    played = await prepare(m, st) if prepare is not None else None
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=size) as pilot:
        screen = GameScreen(m, st, resume=True)
        app.push_screen(screen)
        await until(pilot, lambda: screen.game is not None and not screen.busy)
        await pilot.pause(3.0)  # the picture drawn, in a thread
        if then is not None:
            await (then(app, pilot, screen, m, played) if prepare is not None else then(app, pilot, screen, m))
            await pilot.pause(settle)
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


LIBRARY = ("阅览室比外面更冷，窗子开得很高，光从上头斜斜落下来。你顺着那排皮脊合订本摸过去，指尖沾了一层细细的书灰，"
           "只有靠里一册的书脊被摸得发亮，像常有人抽它。\n\n"
           "你把它搬到长桌上翻开。纸页发黄发脆，铅字挤得很密。翻到十多年前的一期，一篇不起眼的短讯夹在农产品价格中间。")


async def _library(app, pilot, screen, m):
    """A check in the library as a turn shows it: the action, the dice thrown for real, the clue they
    found and the keeper's telling."""
    from rich.text import Text

    from autokeeper.engine.game import _check_view
    from autokeeper.rules.coc import Rules
    from autokeeper.rules.physdice import PhysDice

    inv = screen.state.investigator
    screen.story.add("player", Text(f"▶ {inv.name}：请管理员拿来十多年前的《阿诺兹堡广告报》合订本，一期一期往下翻"))
    seed = 0
    while (check := Rules(dice=PhysDice(seed=seed)).check(inv, "图书馆使用")).level.zh != "常规成功":
        seed += 1  # a throw that passes plainly: the same picture every time
    await screen.handle({"type": "dice", "who": inv.name, **_check_view(check)})
    await pilot.pause(4.0)  # the dice at rest
    clue = next(c for c in m.clues if c.id == "c_library_article")
    await screen.handle({"type": "outcome", "who": inv.name, "lines": [f"获得线索〔{clue.title}〕"]})
    screen.state.clues.append(clue.id)  # found, as the turn would have it before the screen hears of it
    await screen.handle({"type": "clue", "id": clue.id, "title": clue.title, "text": clue.text.strip()})
    await screen.handle({"type": "keeper_start"})
    await screen.handle({"type": "keeper_end", "text": LIBRARY})


async def _talk(app, pilot, screen, m):
    """A conversation as it comes in: the cook's reply, the keeper's telling with a passer-by's line."""
    await screen.handle({"type": "npc", "npc": "doreen_ratger", "name": "多琳·拉特格", "portrait": "",
                         "text": "（手停在围裙上，把面粉拍了拍）死人？这种话在我们这儿可不兴这么大声说。年头不好，"
                                 "老人孩子一场病就过去了……您打听这个做什么？您是从城里来的吧，看您这双鞋就知道。"})
    await screen.handle({"type": "keeper_start"})
    await screen.handle({"type": "keeper_end", "text": "弗朗西斯把碗往桌上一放，窗外有辆卡车慢慢停在街角，车斗上盖着油布。\n\n"
                         "【开卡车的年轻人】（探进门来，哈出一口白气）拉特格太太，赫琴斯让我问一声，今晚还留不留汤？\n\n"
                         "多琳朝他摆摆手，他又咳了两声，退回雪里去了。"})


# -- fights and chases: the real encounter engine, with a scripted keeper plan and scripted dice ----------
class Dice:
    """Scripted dice, as the tests' FakeDice: the d100 values and the totals queued, then steady ones."""

    LABEL = {"critical": "大成功", "extreme": "極限成功", "hard": "困難成功", "regular": "一般成功", "failure": "失敗",
             "fumble": "大失敗"}

    def __init__(self, values=(), totals=(), *, then: int = 70, total: int = 2):
        from collections import deque

        self.values, self.totals, self.then, self.rest = deque(values), deque(totals), then, total

    def check(self, target: int, bonus: int = 0):
        from autokeeper.rules.bcdice import Roll

        v = self.values.popleft() if self.values else self.then
        fumble_at = 100 if target >= 50 else 96
        label = ("critical" if v == 1 else "fumble" if v >= fumble_at else "extreme" if v <= target // 5
                 else "hard" if v <= target // 2 else "regular" if v <= target else "failure")
        text = f"(1D100<={target}) 獎勵・懲罰骰[{bonus}] ＞ {v} ＞ {v} ＞ {self.LABEL[label]}"
        return Roll(f"CC<={target}", text, success=v <= target, failure=v > target)

    def roll(self, expr: str):
        from autokeeper.rules.bcdice import Roll

        return Roll(expr, f"({expr}) ＞ {self.total(expr)}")

    def total(self, expr: str) -> int:
        return self.totals.popleft() if self.totals else self.rest

    def table(self, name: str) -> str:
        return f"{name} 表结果"


class Keeper:
    """The keeper plan, scripted: every round the same arrangement for the keeper's people; nothing else
    is asked of a model in these scenes."""

    def __init__(self, npcs: list) -> None:
        self.npcs = npcs

    async def plan(self, state, text, *, encounter=None, mode="act", check=None, ask=""):
        from autokeeper.agents.encounter import Checked
        from autokeeper.engine.encounter.plan import EncounterTurnPlan

        return Checked(value=EncounterTurnPlan(npcs=self.npcs), problems=[], raw=None)

    async def encounter_choice(self, state, inv_id, situation, options):
        from autokeeper.agents.encounter import Checked

        return Checked(value=None, problems=["没选"])

    async def keeper(self, state, report, on_delta=None, lore=""):
        return ""


def arranged(who: str, d, react: str = "fight_back"):
    from autokeeper.engine.encounter.plan import NpcArrangement

    return NpcArrangement(who=who, goal="夺书离开", actions=[d], react=react)  # type: ignore[arg-type]


def claw(target: str):
    from autokeeper.engine.encounter.plan import AttackPick, DeclaredAction

    return DeclaredAction(do="attack", attacks=[AttackPick(target=target, with_="")], summary="佝偻的身影扑了上来")


async def engine(m, st, npcs: list, dice: Dice):
    """A game over this state with the scripted keeper and dice, and what it shows (its events)."""
    from autokeeper.engine.game import Game
    from autokeeper.rules.coc import Rules

    shown: list[dict] = []

    async def emit(ev: dict) -> None:
        shown.append(ev)

    game = Game(m, st, Keeper(npcs), Rules(dice=dice), emit=emit)  # type: ignore[arg-type]
    game.asking = False  # the answer set stands: the window is its own scene (_reaction)
    return game, shown


async def a_round(game, words: str) -> None:
    """One round as a button press plays it (engine/encounter.Encounters.encounter_input)."""
    game.state.turn += 1
    action = await game.encounter_input(words)
    await game.resolve(action, await game.rule(action))


TOLD = ("书房里只剩煤油灯晃动的光。那东西被你一拳砸在肩上，踉跄着撞翻了书架，旧书哗啦啦落了一地；"
        "它喉咙里挤出一声像狗又像人的呜咽，又扑了回来。爪子划开你的外套，袖子一下子湿透了。")


async def _fight_round(m, st, *, asking: bool = False):
    """The ghoul in the study (《追书人》 shufang): a round fought, the ghoul hurt, the investigator too."""
    from autokeeper.engine.encounter import choices
    from autokeeper.engine.encounter.plan import EncounterStart
    from autokeeper.engine.game import Turn

    me = st.investigators[0]
    npcs = [arranged("douglas", claw(me.id), react="dodge")]
    # his punch lands hard and the ghoul's dodge fails: 3; its claws land hard and his dodge fails: 4
    game, shown = await engine(m, st, npcs, Dice([20, 71, 12, 58], [3, 4]))
    start = EncounterStart(mode="fight", foes=["douglas_ghoul"], pace=6, reason="书房的窗户被撬开，一个佝偻的身影扑了进来", npcs=npcs)
    await game._encounter_open(start, Turn())
    words = next(o.label for o in choices.options(game, me.id) if o.label.startswith("攻击"))
    if asking:  # the round stops at the blow aimed at him: the question the window asks
        game.asking = True
        await a_round(game, words)
        return game.offer_payload(game.state.encounter.pending)
    await a_round(game, words)
    return [ev for ev in shown if ev["type"] not in ("fight_start", "reveal", "sanity", "showcase")]


async def _replay(screen, pilot, events: list[dict], *, upto: int | None = None) -> None:
    for ev in events[:upto]:
        await screen.handle(ev)
        await pilot.pause(0.3)


async def _fight(app, pilot, screen, m, played):
    """The round as the screen shows it, the keeper's telling after it, the next round's choices up."""
    await _replay(screen, pilot, played)
    await screen.handle({"type": "keeper_start"})
    await screen.handle({"type": "keeper_end", "text": TOLD})
    await pilot.pause(2.5)
    screen._offer_orders()


async def _wounded(app, pilot, screen, m, played):
    """The moment the ghoul's claws land: blood over the screen, the claws across the picture."""
    hit = max(i for i, ev in enumerate(played) if ev["type"] == "fight_act" and ev.get("hurt_side") == "party")
    await _replay(screen, pilot, played, upto=hit)
    await pilot.pause(2.5)
    await screen.handle(played[hit])
    await pilot.pause(0.12)


async def _reaction(app, pilot, screen, m, played):
    """The window a blow aimed at the player stops for, before any of its dice."""
    from autokeeper.tui.modals.reaction import ChoiceModal

    app.push_screen(ChoiceModal(played))
    await pilot.pause(1.0)


async def _ask_first(m, st):
    """The question, asked of a fight at its first round (the screen shows the round before it)."""
    offer = await _fight_round(m, st.model_copy(deep=True), asking=True)
    await _fight_round_open(m, st)
    return offer


async def _fight_round_open(m, st):
    from autokeeper.engine.encounter.plan import EncounterStart
    from autokeeper.engine.game import Turn

    me = st.investigators[0]
    npcs = [arranged("douglas", claw(me.id), react="dodge")]
    game, _ = await engine(m, st, npcs, Dice())
    await game._encounter_open(EncounterStart(mode="fight", foes=["douglas_ghoul"], pace=6, reason="书房的窗户被撬开", npcs=npcs), Turn())


async def _chase_round(m, st, *, clean: bool = False):
    """The investigator after the ghoul through the graveyard (《追书人》 mudi's route): it runs for the
    Kimball house and the books, over the shrubs and the low wall; a round run, in which he falls at the
    wall (or, `clean`, gets over it and is at its heels)."""
    from autokeeper.engine.encounter import choices
    from autokeeper.engine.encounter.plan import DeclaredAction, EncounterStart
    from autokeeper.engine.game import Turn

    me = st.investigators[0]
    home = DeclaredAction(do="move", to="back_door", summary="佝偻的身影朝金博尔家的后门跑")
    npcs = [arranged("douglas", home, react="dodge")]
    # speeds: his CON regular (8), the ghoul's extreme (6); through the shrubs (DEX 45), then the wall (Climb)
    game, shown = await engine(m, st, npcs, Dice([40, 5, 45, 10 if clean else 45], then=45))
    start = EncounterStart(mode="chase", foes=["douglas_ghoul"], quarry="foe", pace=15, reason="那东西抱着书逃进了墓园", npcs=npcs)
    await game._encounter_open(start, Turn())
    run = choices.options(game, me.id)
    await a_round(game, next((o.label for o in run if o.label.startswith("追赶")), run[0].label))
    return [ev for ev in shown if ev["type"] not in ("chase_start", "fight_start", "reveal", "sanity", "showcase")]


async def _chase(app, pilot, screen, m, played):
    await _replay(screen, pilot, played)
    await pilot.pause(4.0)


async def _horde_open(m, st):
    """Every creature of the module in one fight: their faces as the fight's column shows them."""
    from autokeeper.engine.encounter.plan import DeclaredAction, EncounterStart
    from autokeeper.engine.game import Turn

    hold = DeclaredAction(do="defend", summary="逼近")
    foes = [c.id for c in m.creatures]
    npcs = [arranged(c.npc or c.id, hold) for c in m.creatures]
    game, _ = await engine(m, st, npcs, Dice(then=20))
    await game._encounter_open(EncounterStart(mode="fight", foes=foes, pace=6, reason="墓园里的东西围了上来", npcs=npcs), Turn())
    return []


async def _horde(app, pilot, screen, m, played):
    await pilot.pause(4.0)


SHOTS = {
    "title": title,
    "game": lambda: in_game("game", "zhuishuren", "tushuguan", ZHUI, then=_library),
    "found": lambda: in_game("found", "dongxuezhixia", "post_office", DONG),  # a place with a picture found for it (pictures.py)
    "map": lambda: in_game("map", "dongxuezhixia", "main_street", DONG, then=_map),
    "handout": lambda: in_game("handout", "zhuishuren", "shufang", ZHUI, then=_handout("h_diary")),
    "telegram": lambda: in_game("telegram", "dongxuezhixia", "main_street", DONG, then=_handout("h_telegram")),
    "models": lambda: in_game("models", "zhuishuren", "tushuguan", ZHUI, then=_models),
    "developer": lambda: in_game("developer", "zhuishuren", "jingju", ZHUI, met=("jingguan",), then=_developer),
    "host": host,
    "talk": lambda: in_game("talk", "dongxuezhixia", "ratger_house", DONG, met=("doreen_ratger",), then=_talk),
    "fight": lambda: in_game("fight", "zhuishuren", "shufang", ZHUI, prepare=_fight_round, then=_fight),
    "fight-narrow": lambda: in_game("fight-narrow", "zhuishuren", "shufang", ZHUI, prepare=_fight_round, then=_fight, size=(100, 30)),
    "fight-hit": lambda: in_game("fight-hit", "zhuishuren", "shufang", ZHUI, prepare=_fight_round, then=_wounded, settle=0.0),
    "reaction": lambda: in_game("reaction", "zhuishuren", "shufang", ZHUI, prepare=_ask_first, then=_reaction),
    "chase": lambda: in_game("chase", "zhuishuren", "mudi", ZHUI, prepare=_chase_round, then=_chase),
    "chase-close": lambda: in_game("chase-close", "zhuishuren", "mudi", ZHUI,
                                   prepare=lambda m, st: _chase_round(m, st, clean=True), then=_chase),
    "creatures": lambda: in_game("creatures", "zhuishuren", "mudi", ZHUI, prepare=_horde_open, then=_horde),
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
    found = ROOT / "data" / "cache" / "art"
    if found.is_dir():  # pictures already found or drawn here for the modules (pictures.py): shown as a game shows them
        shutil.copytree(found, paths.module_art("_").parent)
    for name in names or list(SHOTS):
        svg = await SHOTS[name]()
        print(png(svg))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
