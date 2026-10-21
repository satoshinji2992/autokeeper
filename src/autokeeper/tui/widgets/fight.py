"""A fight and a chase on the stage (engine/fighting.py runs them; these only show them).

Both take the stage in place of the scene (Arena): a picture (Canvas) and a column beside it.

In a fight the picture is whoever the investigators face, taller than the stage, lit red from behind
(imagery.staged). Blood runs down it and it darkens on every heartbeat; it lunges when it strikes,
and when it gets you its claws rake across the picture as across your eyes; struck, it flashes white
under a number in blood; down, it goes grey under a cross. The column: the round and who acts in
what order, how hurt every foe looks (in words, never numbers), the investigators' blood bars.

In a chase the picture is the place, cold and smeared with running, and what is behind you in it: far
off a shape, then a face, at your heels a face that fills the stage, lunging every time it gains. The
column: how many stretches are left between you in figures five rows tall, the way between, and
what is in the way ahead.

The screen does the rest (tui/screens/game.py): the whole screen floods red when an investigator
is hurt (BloodFlash), the screen lurches, a creature first seen comes up over the whole screen before
the sanity roll, the one that catches you does too, and the round's choices wait only so long."""

from __future__ import annotations

import asyncio
import time
from functools import lru_cache

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Static

from ... import atmosphere, imagery
from ...module.schema import Module
from ...rules.combat import STANCES

BLOOD, DEEP, EMBER, AMBER, BONE, ASH, BILE = "#c1121f", "#7a0a0a", "#ff6b35", "#ffb000", "#e8dec4", "#8a7f72", "#9bd770"
GORE = "#ff1a1a"  # fresh blood: the claw marks, the numbers
WOUND = {"无伤": 4, "轻伤": 3, "受伤": 2, "重伤": 1}  # segments of a foe's meter, in words
DOWN = ("昏迷", "濒死", "死亡", "倒下")
GONE = ("逃走", "投降")
TICK = 0.12  # seconds a frame of a canvas stays up


def wound_meter(word: str, status: str) -> Text:
    """How hurt a foe looks: four marks and the word, no numbers."""
    out = Text()
    if status in DOWN or status in GONE:
        mark = "✝" if status == "死亡" else "✕" if status in DOWN else "↗"
        out.append(f"{mark} {status}", style=f"bold {BLOOD if status in DOWN else ASH}")
        return out
    full = WOUND.get(word, 4)
    colour = BILE if full == 4 else AMBER if full == 3 else EMBER if full == 2 else BLOOD
    out.append("▰" * full, style=f"bold {colour}")
    out.append("▱" * (4 - full), style=DEEP)
    out.append(f" {word}", style=colour)
    return out


def hp_bar(hp: int, top: int, width: int = 10) -> Text:
    top = max(1, top)
    filled = max(0, min(width, round(width * hp / top)))
    share = hp / top
    colour = BILE if share > 0.66 else AMBER if share > 0.34 else BLOOD
    return Text.assemble(("█" * filled, f"bold {colour}"), ("░" * (width - filled), DEEP))


def cell_text(text: str, width: int, style: str = "") -> Text:
    """`text` in exactly `width` cells (a Chinese character takes two): cut short or padded."""
    out = Text(text, style=style, no_wrap=True)
    out.truncate(width, overflow="crop", pad=True)
    return out


# -- drawing over a frame ---------------------------------------------------------------------
Mark = tuple[str, Style]


def paint_over(frame: Text, width: int, height: int, marks: dict[tuple[int, int], Mark]) -> Text:
    """A frame with some cells replaced: claw marks, a number, a word. A wide character takes its cell
    and the next ("" there)."""
    rows = atmosphere.grid(frame, width, height)
    for (x, y), cell in marks.items():
        if 0 <= y < height and 0 <= x < width:
            rows[y][x] = cell
    out = Text(no_wrap=True)
    for y, row in enumerate(rows):
        run, style = [], None
        for ch, st in row:
            if st is not style and run:
                out.append("".join(run), style=style)
                run = []
            style = st
            run.append(ch)
        if run:
            out.append("".join(run), style=style)
        if y < height - 1:
            out.append("\n")
    return out


DIGITS = {  # a number in blood: three cells wide, five tall
    "0": ["███", "█ █", "█ █", "█ █", "███"], "1": [" █ ", "██ ", " █ ", " █ ", "███"],
    "2": ["███", "  █", "███", "█  ", "███"], "3": ["███", "  █", "███", "  █", "███"],
    "4": ["█ █", "█ █", "███", "  █", "  █"], "5": ["███", "█  ", "███", "  █", "███"],
    "6": ["███", "█  ", "███", "█ █", "███"], "7": ["███", "  █", "  █", " █ ", " █ "],
    "8": ["███", "█ █", "███", "█ █", "███"], "9": ["███", "█ █", "███", "  █", "███"],
    "-": ["   ", "   ", "███", "   ", "   "],
}


def stamp(text: str, word: str, width: int, height: int) -> dict[tuple[int, int], Mark]:
    """A number in big blood-red figures, and a word under it, in the middle of the frame."""
    marks: dict[tuple[int, int], Mark] = {}
    big = Style(color=GORE, bold=True)
    figures = [DIGITS[c] for c in text if c in DIGITS]
    span = len(figures) * 4 - 1
    x0, y0 = max(0, (width - span) // 2), max(0, (height - 7) // 2)
    for i, rows in enumerate(figures):
        for dy, row in enumerate(rows):
            for dx, ch in enumerate(row):
                if ch != " ":
                    marks[x0 + i * 4 + dx, y0 + dy] = ("█", big)
    if word:
        plate = Style(color="#ffffff", bgcolor="#8a0000", bold=True)
        cells: list[str] = []
        for ch in f" {word} ":
            cells += [ch, ""] if ord(ch) > 0x2E80 else [ch]
        x = max(0, (width - len(cells)) // 2)
        for i, ch in enumerate(cells):
            marks[x + i, y0 + 6] = (ch, plate)
    return marks


def claws(width: int, height: int, progress: float) -> dict[tuple[int, int], Mark]:
    """Three claw marks raking down and across, drawn as far as `progress` (0 to 1) has got."""
    marks: dict[tuple[int, int], Mark] = {}
    fresh, edge = Style(color=GORE, bold=True), Style(color="#ffd0d0", bold=True)
    reach = int(height * min(1.0, progress))
    for k in (-3, 0, 3):
        for y in range(reach):
            x = int(width * 0.28 + y * (width * 0.44 / max(1, height))) + k
            marks[x, y] = ("╲", edge if y == reach - 1 else fresh)
            if y % 3 == 1:
                marks[x + 1, y] = ("▚", Style(color=BLOOD))
    return marks


# -- a portrait that lives ------------------------------------------------------------------------
@lru_cache(maxsize=24)
def variants(path: str, cols: int, rows: int, style: str, scene: str = "", near: float = 1.0) -> dict[str, Text]:
    """Every way a canvas shows its picture (imagery.staged; chafa, so off the UI thread): as it is,
    darker on the heartbeat, white in a flash of being struck, grey when down, closer in a lunge."""
    def one(graded: str, closer: float = 0.0) -> Text:
        return Text.from_ansi(imagery.render_staged(path, cols, rows, style, graded, scene, near + closer))

    return {"": one(""), "half": one("half"), "flash": one("flash"), "dead": one("难过+dim"), "zoom": one("", 0.35)}


def faceless(cols: int, rows: int, creature: bool, tone: str = "") -> Text:
    """No picture to show: a shape in the dark, two eyes."""
    eye = {"flash": "#ffffff", "dead": "#3a3a3a"}.get(tone, BILE if creature else AMBER)
    body = {"flash": "#c0c0c0", "dead": "#1a1a1a", "half": "#3a0606"}.get(tone, DEEP)
    out = Text()
    for y in range(rows):
        if y == rows // 3:
            gap = max(1, cols // 6)
            line = " " * max(0, (cols - gap * 2 - 2) // 2) + "●" + " " * gap * 2 + "●"
            out.append(line.ljust(cols), style=f"bold {eye}")
        else:
            shade = "▓" if y > rows // 2 else "░"
            width = max(2, int(cols * (0.3 + 0.55 * y / max(1, rows))))
            out.append((" " * ((cols - width) // 2) + shade * width).ljust(cols), style=body)
        if y < rows - 1:
            out.append("\n")
    return out


class Canvas(Static):
    """The stage's picture, alive: blood running down it (in a chase, the place rushing past), a
    heartbeat, and what the fight does to it."""

    DEFAULT_CSS = """
    Canvas { width: 1fr; height: 100%; content-align: center middle; background: #050303; }
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.key: tuple = ()  # what is shown
        self.loading: tuple | None = None  # what is being drawn, off the UI thread
        self.wanted: tuple | None = None  # what was asked for, at whatever size the canvas has
        self.cols = self.rows = 0
        self.looks: dict[str, Text] = {}
        self.atmo: atmosphere.Atmosphere | None = None
        self.kinds = ["blood", "pulse"]
        self.moving = True  # 设置's animations
        self.dead = False
        self.lunge_until = self.flash_until = self.claw_from = self.stamp_until = 0.0
        self.stamped: dict[tuple[int, int], Mark] = {}

    def on_mount(self) -> None:
        self.set_interval(TICK, self._tick)

    def on_resize(self) -> None:
        if self.wanted is not None:
            self.want(*self.wanted)

    def want(self, path: str, style: str, creature: bool, scene: str = "", near: float = 1.0) -> None:
        """Draw this now (from the picture, or a shape with eyes), unless it is already up or on its way;
        drawn again at its new size when the stage is laid out or resized."""
        self.wanted = (path, style, creature, scene, near)
        cols, rows = self.size.width, self.size.height
        key = (path, scene, round(near, 2), cols, rows, style)
        if key in (self.key, self.loading) or cols < 8 or rows < 4:
            return
        self.loading = key
        self.run_worker(self._load(key, creature), group="canvas")

    async def _load(self, key: tuple, creature: bool) -> None:
        path, scene, near, cols, rows, style = key
        looks = None
        if path and imagery.available():
            try:
                looks = await asyncio.to_thread(variants, path, cols, rows, style, scene, near)
            except OSError:
                looks = None
        if looks is None:
            looks = {t: faceless(cols, rows, creature, t) for t in ("", "half", "flash", "dead")}
            looks["zoom"] = looks[""]
        if key != self.loading:  # something else came forward meanwhile
            return
        self.loading = None
        self.key, self.cols, self.rows, self.looks = key, cols, rows, looks
        self.atmo = atmosphere.Atmosphere(self.kinds, {"": looks[""], "half": looks["half"]}, cols, rows)
        self._tick()

    def lunge(self) -> None:
        self.lunge_until = time.monotonic() + 0.45
        self._shake([(2, 0), (-1, 0), (1, 0), (0, 0)])

    def slashed(self) -> None:
        """It got you: claws rake across the picture, as across your eyes, and blood spatters it."""
        self.lunge_until = self.claw_from = time.monotonic()
        self.lunge_until += 0.3
        if self.atmo is not None:
            self.atmo.splash(26)
        self._shake([(3, 1), (-3, 0), (2, -1), (-1, 0), (0, 0)])

    def struck(self, damage: int, word: str) -> None:
        """A blow landed on it: a white flash, the damage in blood over it, its blood on the picture."""
        now = time.monotonic()
        self.flash_until, self.stamp_until = now + 0.12, now + 1.6
        self.stamped = stamp(f"-{damage}" if damage else "", word, self.cols, self.rows)
        if self.atmo is not None:
            self.atmo.splash(18)
        self._shake([(-2, 0), (2, 0), (-1, 0), (0, 0)])

    def fall(self, dead: bool) -> None:
        self.dead = dead
        self._tick()

    def _shake(self, steps: list[tuple[int, int]]) -> None:
        seq = iter(steps)

        def step() -> None:
            try:
                self.styles.offset = next(seq)
                self.set_timer(0.05, step)
            except StopIteration:
                pass

        step()

    def _tick(self) -> None:
        if not self.looks:
            return
        now = time.monotonic()
        if self.dead:
            frame = self.looks["dead"]
        elif now < self.flash_until:
            frame = self.looks["flash"]
        elif now < self.lunge_until:
            frame = self.looks["zoom"]
        elif self.atmo is not None and self.moving:
            frame = self.atmo.frame()
        else:
            frame = self.looks[""]
        marks: dict[tuple[int, int], Mark] = {}
        if self.claw_from and now - self.claw_from < 1.2:
            marks |= claws(self.cols, self.rows, (now - self.claw_from) / 0.25)
        if now < self.stamp_until:
            marks |= self.stamped
        if self.dead:
            marks |= stamp("", "✝", self.cols, self.rows)
        self.update(paint_over(frame, self.cols, self.rows, marks) if marks else frame)

    def clear(self) -> None:
        self.key, self.loading, self.wanted, self.looks, self.atmo, self.dead = (), None, None, {}, None, False
        self.update("")


def big_number(n: int, style: str) -> list[Text]:
    """A number five rows tall (DIGITS), a line a row."""
    figures = [DIGITS[c] for c in str(n) if c in DIGITS]
    return [Text(" ".join(f[row] for f in figures), style=style) for row in range(5)]


# -- the stage: a fight, a chase ------------------------------------------------------------------
class Arena(Horizontal):
    """A fight or a chase in place of the scene: the picture (Canvas) and, beside it, who acts in what
    order and how hurt everyone is, or how far behind it is and what is in the way."""

    DEFAULT_CSS = """
    Arena { height: 20; display: none; background: #050303; }
    Arena .side { width: 38; height: 100%; padding: 0 1; border-left: tall #3a0a0a; background: #0b0505; }
    Arena.-chase .side { border-left: tall #2a3448; background: #07080c; }
    Arena.-close .side { border-left: tall #c1121f; background: #160506; }
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.view: dict | None = None
        self.m: Module | None = None
        self.style = "photo"
        self.order: list[str] = []
        self.acting = ""
        self.facing = ""
        self.mode = ""  # "fight" or "chase"
        self.scene = ""  # a chase's place, behind what chases
        self.last_gap = 9
        self.blink = 0

    def compose(self) -> ComposeResult:
        yield Canvas()
        yield Static(classes="side")

    def on_mount(self) -> None:
        self.set_interval(0.35, self._flicker)

    @property
    def canvas(self) -> Canvas:
        return self.query_one(Canvas)

    def _open(self, mode: str, view: dict, m: Module, style: str, rows: int | None) -> None:
        if mode != self.mode:
            self.canvas.clear()
            self.canvas.kinds = ["rush", "pulse"] if mode == "chase" else ["blood", "pulse"]
            self.order, self.acting, self.facing, self.last_gap = [], "", "", 9
        self.mode, self.view, self.m, self.style = mode, view, m, style
        self.set_class(mode == "chase", "-chase")
        if rows:
            self.styles.height = rows
        self.display = True

    def show(self, view: dict, m: Module, style: str = "photo", rows: int | None = None) -> None:
        """A fight: whoever they face, as large as the stage holds."""
        self._open("fight", view, m, style, rows)
        self.set_class(False, "-close")
        foes = [f for f in view["fighters"] if f["side"] != "party"]
        standing = [f for f in foes if f["status"] not in DOWN and f["status"] not in GONE]
        if self.facing not in {f["id"] for f in foes} or (self.facing not in {f["id"] for f in standing} and standing):
            self.facing = (standing or foes or [{"id": ""}])[0]["id"]
        self._side()
        self.call_after_refresh(self._draw)

    def show_chase(self, view: dict, m: Module, style: str = "photo", rows: int | None = None, scene: str = "") -> None:
        """A chase: the place rushing past, and what is behind you in it, the bigger the nearer."""
        self._open("chase", view, m, style, rows)
        self.scene = scene
        gap = chase_gap(view)
        if gap < self.last_gap < 9:
            self.canvas.lunge()  # it gained on you
        self.last_gap = gap
        self.set_class(gap <= 1, "-close")
        them = [r for r in view.get("runners", []) if r["side"] != "party" and r["status"] not in DOWN]
        if them:
            quarry = view.get("quarry", "party")
            self.facing = (max(them, key=lambda r: r["pos"]) if quarry == "party" else min(them, key=lambda r: r["pos"]))["id"]
        self._side()
        self.call_after_refresh(self._draw)

    def _draw(self) -> None:
        view, m = self.view, self.m
        if view is None or m is None or not self.is_attached:
            return
        people = view["fighters"] if self.mode == "fight" else view.get("runners", [])
        f = next((x for x in people if x["id"] == self.facing), None)
        if f is None:
            return
        path = m.pictures.get(f.get("portrait") or "", "")
        creature = f.get("kind", "creature") == "creature"
        if self.mode == "chase":
            self.canvas.want(path, self.style, creature, self.scene, NEAR.get(chase_gap(view), 0.2))
            return
        self.canvas.want(path, self.style, creature)
        self.canvas.fall(f["status"] == "死亡" or (f["status"] in DOWN and creature))

    def _flicker(self) -> None:
        self.blink += 1
        if self.display and self.mode == "chase" and self.view is not None:
            self._side()

    def _side(self) -> None:
        side = self.query_one(".side", Static)
        side.update(self._chase_side() if self.mode == "chase" else self._fight_side())

    def _fight_side(self) -> Text:
        view = self.view or {}
        people = view.get("fighters", [])
        names = {f["id"]: f["name"] for f in people}
        out = Text()
        out.append("⚔ 战斗", style=f"bold {GORE}")
        out.append(f"  第 {max(1, view.get('round', 1))} 轮\n", style=f"bold {BONE}")
        out.append("先后 ", style=ASH)
        for i, fid in enumerate(self.order or [f["id"] for f in people]):
            if i:
                out.append(" ▸ ", style=DEEP)
            side = next((f["side"] for f in people if f["id"] == fid), "foe")
            colour = AMBER if side == "party" else BLOOD
            out.append(names.get(fid, fid)[:6], style=f"bold reverse {colour}" if fid == self.acting else colour)
        out.append("\n\n")
        for f in people:
            if f["side"] == "party":
                continue
            out.append("▶ " if f["id"] == self.facing else "  ", style=f"bold {GORE}")
            out.append(f["name"][:12] + "\n", style=f"bold {BONE}" if f["id"] == self.facing else BONE)
            out.append("  ")
            out.append_text(wound_meter(f["wound"], f["status"]))
            out.append(f"  {f['weapon']}\n", style=ASH)
        out.append("━━ 你们 ━━\n", style=DEEP)
        for f in people:
            if f["side"] != "party":
                continue
            out.append(f["name"][:10] + "\n", style=f"bold reverse {AMBER}" if f["id"] == self.acting else f"bold {AMBER}")
            if f["hp"] is not None:
                out.append_text(hp_bar(f["hp"], f["hp_max"], 16))
                out.append(f" {f['hp']}/{f['hp_max']}", style=f"bold {BONE}")
            tag = f["status"] or STANCES.get(f["stance"], "")
            out.append(f"  {tag}\n", style=f"bold {GORE}" if f["status"] else ASH)
        return out

    def _chase_side(self) -> Text:
        view = self.view or {}
        quarry = view.get("quarry", "party")
        runners, legs = view.get("runners", []), view.get("legs", [])
        gap = chase_gap(view)
        close = gap <= 1
        them = [r for r in runners if r["side"] != "party" and r["status"] not in DOWN]
        out = Text()
        out.append("⟫ 追逐", style=f"bold {GORE if close else '#9fb4c8'}")
        out.append(f"  第 {max(1, view.get('round', 1))} 轮\n\n", style=f"bold {BONE}")
        out.append("、".join(r["name"][:10] for r in them) or "它", style=f"bold {BONE}")
        if quarry == "party":
            out.append("\n就在你背后！\n" if gap <= 0 else "\n在你身后\n", style=f"bold {GORE if close else AMBER}")
        else:
            out.append("\n在你们前面\n", style=f"bold {AMBER}")
        if 0 < gap < 9:
            hot = GORE if close else AMBER
            lines = big_number(gap, f"bold {'#5a0000' if close and self.blink % 2 else hot}")
            for i, line in enumerate(lines):
                out.append("  ")
                out.append_text(line)
                out.append("  段\n" if i == 4 else "\n", style=f"bold {hot}")
        out.append("\n")
        span = max(1, gap) if gap < 9 else 6
        cell = max(2, min(8, 24 // span))
        out.append("◉◉ " if self.blink % 3 else "◎◎ ", style=f"bold {GORE}")
        for i in range(span):
            heat = 1 - i / max(1, span)
            shade = "▓" if heat > 0.66 else "▒" if heat > 0.33 else "░"
            out.append(shade * (cell - 1) + " ", style=GORE if close else DEEP if heat > 0.5 else "#3a3f52")
        out.append("◆你\n" if quarry == "party" else "◆你们\n", style=f"bold {AMBER}")
        me = max((r["pos"] for r in runners if r["side"] == quarry), default=0)
        nxt = legs[me + 1] if me + 1 < len(legs) else None
        if nxt and nxt.get("obstacle") and not nxt["obstacle"].get("open"):
            ob = nxt["obstacle"]
            out.append("\n前方 ", style=ASH)
            out.append(f"{'▦' if ob['kind'] == 'barrier' else '⚠'} {ob['what']}", style=f"bold {BLOOD if ob['kind'] == 'barrier' else AMBER}")
            out.append(f"\n     {ob['skill']}\n", style=ASH)
        return out

    def round_begins(self, order: list[str], view: dict) -> None:
        self.order, self.acting = order, ""
        if self.m is not None:
            self.show(view, self.m, self.style)

    def act(self, ev: dict) -> None:
        """An exchange: the one faced comes forward if it acts or is struck, lunges and claws across the
        picture when it gets you, takes the flash and the number when it is struck."""
        self.acting = ev.get("attacker", "")
        foe = ev["attacker"] if ev.get("attacker_side") != "party" else ev["defender"] if ev.get("defender_side") != "party" else ""
        if foe and foe != self.facing:
            self.facing = foe
            self._draw()
        self._side()
        canvas = self.canvas
        landed = ev.get("winner") not in ("none", None) and ev.get("damage")
        if ev.get("attacker_side") != "party" and landed and ev.get("hurt_side") == "party":
            canvas.slashed()
        elif ev.get("attacker_side") != "party":
            canvas.lunge()
        if ev.get("hurt") and ev.get("hurt_side") != "party" and ev.get("damage"):
            words = [n for n in ev.get("notes", []) if n in ("重伤", "昏死过去", "倒地不起", "濒临死亡", "死了", "当场毙命", "倒下了", "被打晕了")]
            canvas.struck(ev["damage"], words[-1] if words else "")

    def hide(self) -> None:
        self.display = False
        self.view, self.order, self.acting, self.facing, self.mode = None, [], "", "", ""
        self.canvas.clear()
        self.remove_class("-chase", "-close")


NEAR = {0: 1.0, 1: 0.78, 2: 0.55, 3: 0.38}  # how large what chases you stands, by stretches between you


def chase_gap(view: dict) -> int:
    """Stretches between the nearest pursuer and the hindmost of those it chases (9: none)."""
    quarry = view.get("quarry", "party")
    ahead = [r["pos"] for r in view.get("runners", []) if r["side"] == quarry and r["status"] not in DOWN and not r.get("hidden")]
    behind = [r["pos"] for r in view.get("runners", []) if r["side"] != quarry and r["status"] not in DOWN]
    return min(ahead) - max(behind) if ahead and behind else 9


class BloodFlash(ModalScreen[None]):
    """The whole screen floods red and drains, when an investigator is hurt: `strength` 0 to 1."""

    DEFAULT_CSS = "BloodFlash { background: #8a0000 50%; }"

    def __init__(self, strength: float = 0.5):
        super().__init__()
        self.alphas = [min(0.8, 0.25 + 0.55 * strength) * k for k in (1.0, 0.8, 0.6, 0.42, 0.28, 0.15, 0.0)]

    def on_mount(self) -> None:
        self._step()

    def _step(self) -> None:
        if not self.alphas:
            self._gone()
            return
        self.styles.background = Color(150, 0, 0, self.alphas.pop(0))
        self.set_timer(0.06, self._step)

    def _gone(self) -> None:
        if self.is_current:
            self.alphas = []
            self.dismiss()

    def on_key(self, event: events.Key) -> None:
        """Typing goes on through the blood: the key reaches whatever had it under the flash."""
        event.stop()
        event.prevent_default()
        below = self.app.screen_stack[-2] if len(self.app.screen_stack) > 1 else None
        self._gone()
        if below is not None:
            (below.focused or below).post_message(events.Key(event.key, event.character))


# -- the story's cards --------------------------------------------------------------------------
def fight_card(ev: dict) -> Text:
    """An exchange as the story keeps it: who struck whom with what, both rolls, what it did."""
    out = Text()
    out.append("⚔ ", style=f"bold {BLOOD}")
    side = {"party": f"bold {AMBER}", "foe": f"bold {BONE}"}
    out.append(ev["attacker_name"], style=side.get(ev.get("attacker_side", ""), f"bold {BONE}"))
    out.append(f" ─{ev['attack']}→ ", style=ASH)
    out.append(ev["defender_name"], style=side.get(ev.get("defender_side", ""), f"bold {BONE}"))
    rolls = []
    if ev.get("roll"):
        r = ev["roll"]
        rolls.append((f"{r['skill']} {r['target']}：{r['value']} {r['level']}", BILE if r["passed"] else ASH))
    if ev.get("answer"):
        a = ev["answer"]
        how = {"dodge": "闪避", "fight": "反击", "cover": "扑向掩体"}.get(ev["reaction"], "")
        rolls.append((f"{how} {a['target']}：{a['value']} {a['level']}", BILE if a["passed"] else ASH))
    if rolls:
        out.append("\n  ")
        for i, (text, style) in enumerate(rolls):
            if i:
                out.append("  ⟷  ", style=DEEP)
            out.append(text, style=style)
    out.append("\n  ")
    if ev.get("jammed"):
        out.append("卡壳", style=f"bold {EMBER}")
    elif ev.get("empty"):
        out.append("没有子弹", style=f"bold {EMBER}")
    elif ev.get("winner") == "none":
        out.append("落空", style=ASH)
    elif ev.get("effect"):
        out.append(f"{ev['effect']}得手", style=f"bold {EMBER}")
    else:
        back = ev.get("hurt") == ev.get("attacker")
        out.append(" 被反击 " if back else " 命中 ", style=f"bold #ffffff on {DEEP}")
        out.append(" ")
        out.append(ev.get("damage_text", ""), style=f"bold {BONE}")
        if ev.get("damage"):
            out.append("  " + "█" * min(12, ev["damage"]), style=f"bold {GORE}")
        if ev.get("extreme"):
            out.append("  极难成功", style=f"bold {EMBER}")
        notes = [n for n in ev.get("notes", []) if n]
        if notes:
            out.append("  " + "、".join(notes), style=f"bold {GORE}")
    return out


def banner(text: str, *, colour: str = BLOOD) -> Text:
    """The start or the end of a fight or a chase, across the story."""
    out = Text(justify="center")
    out.append("━━━  ", style=DEEP)
    out.append(text, style=f"bold {colour}")
    out.append("  ━━━", style=DEEP)
    return out


def chase_lines(lines: list[str]) -> Text:
    out = Text()
    for i, line in enumerate(lines):
        if i:
            out.append("\n")
        out.append("⟫ ", style="#5a6478")
        bad = any(w in line for w in ("追上", "栽", "挡住", "受了"))
        out.append(line, style=f"bold {GORE}" if bad else BONE)
    return out


def title_frames(word: str, width: int) -> list[Text]:
    """The title that comes up when a fight starts: glitching in, then steady."""
    import random

    rng = random.Random(word)
    spaced = "  ".join(word)
    frames = []
    for k in range(7):
        noise = max(0, 5 - k)
        line = "".join(ch if ch == " " or rng.random() > noise / 7 else rng.choice("▓▒░█") for ch in spaced)
        bar = "━" * max(4, (width - len(spaced) * 2) // 4)
        frames.append(Text.assemble((bar + "  ", DEEP), (line, f"bold {GORE}"), ("  " + bar, DEEP), justify="center"))
    return frames


def pulse_rate(hp: int, top: int) -> float:
    """Seconds between heartbeats for someone at `hp` of `top`: quicker the worse it is."""
    share = hp / max(1, top)
    return 0.35 + 0.65 * max(0.0, min(1.0, share)) ** 0.7


__all__ = ["Arena", "BloodFlash", "Canvas", "banner", "chase_gap", "chase_lines", "fight_card", "pulse_rate", "title_frames"]
