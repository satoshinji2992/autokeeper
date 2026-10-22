"""A fight and a chase on the stage (engine/encounter runs them; these only show them).

Both take the stage in place of the scene (Arena): a picture (Canvas) and a column beside it.

In a fight the picture is whoever the investigators face, taller than the stage, lit red from behind
(imagery.staged). Blood runs down it and it darkens on every heartbeat; it lunges when it strikes,
and when it gets you its claws rake across the picture as across your eyes; struck, it flashes white
under a number in blood; down, it goes grey under a cross. The column: the round and who acts in
what order, how hurt every foe looks (in words, never numbers), the investigators' blood bars.

In a chase the picture is the place, cold and smeared with running, and what is behind you in it: far
off a shape, then a face, at your heels a face that fills the stage, lunging every time it gains. The
column: how far behind it is, the way run and in sight with who is where, and what is in the way.

Their styles are in fight.tcss (loaded with the app's: a widget has no stylesheet of its own).

The screen does the rest (tui/screens/game.py): the whole screen floods red when an investigator
is hurt (BloodFlash), the screen lurches, a creature first seen comes up over the whole screen before
the sanity roll, the one that catches you does too, and the round's choices wait only so long."""

from __future__ import annotations

import asyncio
import time
from functools import lru_cache

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.await_complete import AwaitComplete
from textual.color import Color
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Static

from ... import atmosphere, imagery
from ...module.schema import Module
from ...rules.combat import STANCES
from .panels import rich_content

BLOOD, DEEP, EMBER, AMBER, BONE, ASH, BILE = "#c1121f", "#7a0a0a", "#ff6b35", "#ffb000", "#e8dec4", "#8a7f72", "#9bd770"
GORE = "#ff1a1a"  # fresh blood: the claw marks, the numbers
CHILL, MIST, SAFE = "#9fb4c8", "#5a6478", "#7fd18b"  # a chase: the cold of it, the way behind, the way out
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
@lru_cache(maxsize=4)  # a few faces at one size: each is megabytes of Text, and chafa's own output is cached under it
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
    heartbeat, and what the fight does to it.

    Cheap on the UI thread: a frame is painted without laying out the screen (the canvas's size is
    the stage's), a frame that did not change (a still picture, the dead under their cross) is not
    painted again, and each frame's styles are turned into Textual's once (panels.rich_content).
    The picture stands still while the player types (quiet) and, with 设置's animations off, it
    neither moves nor lunges, shakes or claws; the blows' numbers still come up."""

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
        self.hold = 0.0  # the player is typing: the picture stands still until then
        self._last: Text | None = None  # the frame last painted (kept, so a new one is never taken for it)
        self._marks: tuple = ()  # and what was over it: the same again is not painted
        self._styles: dict = {}  # rich style -> Textual's, for this picture's frames
        self._settle: Timer | None = None  # a resize's redraw, once the size has stopped changing

    def on_mount(self) -> None:
        self.set_interval(TICK, self._tick)

    def on_resize(self) -> None:
        """Drawn again at the new size once it has settled: dragging a window's edge used to start
        five chafa renders for every size it passed through."""
        if self.wanted is None:
            return
        if not self.key:  # nothing up yet: no reason to wait
            self.want(*self.wanted)
            return
        if self._settle is not None:
            self._settle.stop()
        self._settle = self.set_timer(0.25, self._again)

    def _again(self) -> None:
        self._settle = None
        if self.wanted is not None:
            self.want(*self.wanted)

    def want(self, path: str, style: str, creature: bool, scene: str = "", near: float = 1.0) -> None:
        """Draw this now (from the picture, or a shape with eyes), unless it is already up or on its way;
        drawn again at its new size when the stage is laid out or resized. One drawing at a time: a
        newer one takes the place of one under way."""
        self.wanted = (path, style, creature, scene, near)
        cols, rows = self.size.width, self.size.height
        key = (path, scene, round(near, 2), cols, rows, style)
        if key in (self.key, self.loading) or cols < 8 or rows < 4:
            return
        self.loading = key
        self.run_worker(self._load(key, creature), group="canvas", exclusive=True, exit_on_error=False)

    async def _load(self, key: tuple, creature: bool) -> None:
        path, scene, near, cols, rows, style = key
        looks = None
        if path and imagery.available():
            try:
                looks = await asyncio.to_thread(variants, path, cols, rows, style, scene, near)
            except Exception:  # chafa timed out, a picture PIL cannot read: the shape with eyes, never the app gone
                looks = None
        if looks is None:
            looks = {t: faceless(cols, rows, creature, t) for t in ("", "half", "flash", "dead")}
            looks["zoom"] = looks[""]
        if key != self.loading:  # something else came forward meanwhile
            return
        self.loading = None
        self.key, self.cols, self.rows, self.looks, self._styles, self._last = key, cols, rows, looks, {}, None
        self.atmo = atmosphere.Atmosphere(self.kinds, {"": looks[""], "half": looks["half"]}, cols, rows)
        self._tick()

    def lunge(self) -> None:
        if not self.moving:
            return
        self.lunge_until = time.monotonic() + 0.45
        self._shake([(2, 0), (-1, 0), (1, 0), (0, 0)])

    def slashed(self) -> None:
        """It got you: claws rake across the picture, as across your eyes, and blood spatters it."""
        if not self.moving:
            return
        self.lunge_until = self.claw_from = time.monotonic()
        self.lunge_until += 0.3
        if self.atmo is not None:
            self.atmo.splash(26)
        self._shake([(3, 1), (-3, 0), (2, -1), (-1, 0), (0, 0)])

    def struck(self, damage: int, word: str) -> None:
        """A blow landed on it: a white flash, the damage in blood over it, its blood on the picture
        (without animations, the number alone)."""
        now = time.monotonic()
        self.stamp_until = now + 1.6
        self.stamped = stamp(f"-{damage}" if damage else "", word, self.cols, self.rows)
        if self.moving:
            self.flash_until = now + 0.12
            if self.atmo is not None:
                self.atmo.splash(18)
            self._shake([(-2, 0), (2, 0), (-1, 0), (0, 0)])
        self._tick()

    def fall(self, dead: bool) -> None:
        self.dead = dead
        self._tick()

    def quiet(self, seconds: float) -> None:
        """Hold the picture still for a while (the player is typing: every frame moves the terminal's
        cursor, and the caret and the IME box jump with it). What a blow does still shows."""
        self.hold = max(self.hold, time.monotonic() + seconds)

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
        if not self.looks or not self.display:
            return
        now = time.monotonic()
        clawing = bool(self.claw_from) and now - self.claw_from < 1.2
        stamped = now < self.stamp_until
        claw = round(min(1.0, (now - self.claw_from) / 0.25), 2) if clawing else -1.0
        marked = (claw, stamped, self.dead)
        if self.dead:
            frame = self.looks["dead"]
        elif now < self.flash_until:
            frame = self.looks["flash"]
        elif now < self.lunge_until:
            frame = self.looks["zoom"]
        elif self.atmo is not None and self.moving and now >= self.hold:
            frame = self.atmo.frame()
        elif self._last is not None and marked == self._marks == (-1.0, False, False):
            return  # standing still (typing, animations off), nothing over it: what is up stays up
        else:
            frame = self.looks[""]
        if frame is self._last and marked == self._marks:
            return  # the same frame under the same marks: nothing to paint
        self._last, self._marks = frame, marked
        marks: dict[tuple[int, int], Mark] = {}
        if clawing:
            marks |= claws(self.cols, self.rows, claw)
        if stamped:
            marks |= self.stamped
        if self.dead:
            marks |= stamp("", "✝", self.cols, self.rows)
        shown = paint_over(frame, self.cols, self.rows, marks) if marks else frame
        self.update(rich_content(shown, self._styles, self.app.ansi_theme), layout=False)  # the stage's size, not the frame's

    def clear(self) -> None:
        self.key, self.loading, self.wanted, self.looks, self.atmo, self.dead = (), None, None, {}, None, False
        self._last, self._marks, self._styles = None, (), {}
        self.update("")


# -- the stage: a fight, a chase ------------------------------------------------------------------
class Arena(Horizontal):
    """A fight or a chase in place of the scene: the picture (Canvas) and, beside it, who acts in what
    order and how hurt everyone is, or how far behind it is and what is in the way. The screen sets
    its height (GameScreen._stage_rows); the column takes a share of its width, so a narrow screen
    keeps the picture some room, and writes itself again for every new size."""

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
        self.was: dict[str, int] = {}  # a chase's runners where they were before the last round
        self._column: Text | None = None  # what the column shows: the same again is not painted

    def compose(self) -> ComposeResult:
        yield Canvas()
        yield Column(classes="side")

    def on_mount(self) -> None:
        self.set_interval(0.35, self._flicker)

    def on_resize(self) -> None:
        self._fit_side()  # the column's own resize (Column) writes it again for its new size

    def _fit_side(self) -> None:
        if width := self.size.width:
            share, least, most = (0.45, 30, 46) if self.mode == "chase" else (0.42, 28, 38)
            self.query_one(".side", Static).styles.width = max(least, min(most, round(width * share)))

    @property
    def canvas(self) -> Canvas:
        return self.query_one(Canvas)

    def _open(self, mode: str, view: dict, m: Module, style: str, rows: int | None) -> None:
        if mode != self.mode:
            self.canvas.clear()
            self.canvas.kinds = ["rush", "pulse"] if mode == "chase" else ["blood", "pulse"]
            self.order, self.acting, self.facing, self.last_gap, self.was = [], "", "", 9, {}
        self.mode, self.view, self.m, self.style = mode, view, m, style
        self.set_class(mode == "chase", "-chase")
        self._fit_side()
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
        before = self.view if self.mode == "chase" else None
        if before is not None and before.get("round") != view.get("round"):  # a round run: the trail it left
            self.was = {r["id"]: r["pos"] for r in before.get("runners", [])}
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

    def quiet(self, seconds: float) -> None:
        """The stage holds still while the player types (Canvas.quiet; the route stops blinking)."""
        self.canvas.quiet(seconds)

    def _flicker(self) -> None:
        if not self.display or self.mode != "chase" or self.view is None or time.monotonic() < self.canvas.hold:
            return
        if self.canvas.moving:  # the pursuer's mark blinks; with animations off it stands
            self.blink += 1
        self._side()

    def _side(self) -> None:
        if not self.query_one(".side", Static).content_size.width:
            return  # not laid out yet: written once it has its size (Column.on_resize)
        column = self._chase_side() if self.mode == "chase" else self._fight_side()
        if column != self._column:  # a blink that changed nothing, a round's view that is the same: not painted
            self._column = column
            self.query_one(".side", Static).update(column, layout=False)  # the column's size is its own, not its text's

    def _fight_side(self) -> Text:
        size = self.query_one(".side", Static).content_size
        return fight_column(self.view or {}, self.order, self.acting, self.facing, size.width, size.height)

    def _chase_side(self) -> Text:
        size = self.query_one(".side", Static).content_size
        return chase_route(self.view or {}, size.width, size.height, self.blink, self.was)

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
        self.view, self.order, self.acting, self.facing, self.mode, self._column = None, [], "", "", "", None
        self.canvas.clear()
        self.remove_class("-chase", "-close")


class Column(Static):
    """The column beside the picture: written again for every size it is given (the first one too: it
    was written before the layout, at a guessed width, and never again)."""

    def on_resize(self) -> None:
        arena = self.parent
        if isinstance(arena, Arena) and arena.display and arena.view is not None:
            arena._column = None
            arena._side()


def fight_column(view: dict, order: list[str], acting: str, facing: str, width: int, height: int) -> Text:
    """A fight beside the picture: the round and who acts in what order (`acting` lit), how hurt every
    foe looks (in words, never numbers; `facing` marked), the investigators' blood bars. Two lines a
    fighter where the column is tall enough, one where it is not (a short terminal, a crowd); foes
    past what it holds are counted, the investigators always shown, first of all: on a stage four
    rows tall, the round, the investigators and the foe faced."""
    people = view.get("fighters", [])
    names = {f["id"]: f["name"] for f in people}
    foes = [f for f in people if f["side"] != "party"]
    party = [f for f in people if f["side"] == "party"]
    roomy = 3 + 2 * len(foes) + 1 + 2 * len(party) <= height
    lines: list[Text] = []
    head = Text(no_wrap=True, overflow="crop")
    head.append("⚔ 战斗", style=f"bold {GORE}")
    head.append(f"  第 {max(1, view.get('round', 1))} 轮", style=f"bold {BONE}")
    turns = Text(no_wrap=True, overflow="crop")
    turns.append("先后 ", style=ASH)
    for i, fid in enumerate(order or [f["id"] for f in people]):
        if i:
            turns.append(" ▸ ", style=DEEP)
        colour = AMBER if next((f["side"] for f in people if f["id"] == fid), "foe") == "party" else BLOOD
        turns.append(names.get(fid, fid)[:6], style=f"bold reverse {colour}" if fid == acting else colour)
    lines += [head, turns, Text()] if roomy else [head.append("  ").append_text(turns)]
    per = 2 if roomy else 1
    rule = roomy or height - len(lines) - len(party) >= 2 + min(1, len(foes))  # the ━━ 调查员 ━━ line, where it fits
    room = max(0, height - len(lines) - int(rule) - per * len(party))  # the investigators' lines first
    shown = foes
    if per * len(foes) > room:  # the one faced and those still standing first, the rest counted where a line is left
        ranked = sorted(foes, key=lambda f: (f["id"] != facing, f["status"] in DOWN or f["status"] in GONE))
        shown = ranked[: max(min(1, room // per), (room - 1) // per)]
    for f in shown:
        row = Text(no_wrap=True, overflow="crop")
        row.append("▶ " if f["id"] == facing else "  ", style=f"bold {GORE}")
        style = f"bold {BONE}" if f["id"] == facing else BONE
        if roomy:
            row.append(f["name"][:12], style=style)
            meter = Text("  ", no_wrap=True, overflow="crop")
            meter.append_text(wound_meter(f["wound"], f["status"]))
            meter.append(f"  {f['weapon']}", style=ASH)
            lines += [row, meter]
        else:
            row.append_text(cell_text(f["name"], max(4, width - 12), style))  # the marker, a space, the meter: 12
            row.append(" ")
            row.append_text(wound_meter(f["wound"], f["status"]))
            lines.append(row)
    if len(shown) < len(foes) and per * len(shown) < room:
        lines.append(Text(f"  另有 {len(foes) - len(shown)} 名对手", style=ASH))
    if rule:
        lines.append(Text("━━ 调查员 ━━", style=DEEP))
    for f in party:
        name_style = f"bold reverse {AMBER}" if f["id"] == acting else f"bold {AMBER}"
        bars = Text(no_wrap=True, overflow="crop")
        tag = f["status"] or STANCES.get(f["stance"], "")
        tag = f"  {tag}" if roomy else f" {tag}"
        if f["hp"] is not None:
            hp = f" {f['hp']}/{f['hp_max']}"
            used = cell_len(hp) + cell_len(tag) + (0 if roomy else 9)  # one line: the name (8) and a space before the bar
            bars.append_text(hp_bar(f["hp"], f["hp_max"], max(4, min(16, width - used))))
            bars.append(hp, style=f"bold {BONE}")
        bars.append(tag, style=f"bold {GORE}" if f["status"] else ASH)
        if roomy:
            lines += [Text(f["name"][:10], style=name_style), bars]
        else:
            lines.append(cell_text(f["name"], 8, name_style).append(" ").append_text(bars))
    return Text("\n", no_wrap=True, overflow="crop").join(lines)  # a line a row: the count above holds


def short_name(name: str) -> str:
    """A name as a crowded line has room for: the part before the 「·」 (哈罗德·布伦南: 哈罗德)."""
    return name.replace("・", "·").split("·")[0] or name


SEEN = 2  # stretches ahead of the frontmost runner anyone can make out, unless something is in the way first


def in_sight(legs: list[dict], front: int) -> int:
    """The farthest stretch of a chase anyone in it can make out: the next one or two ahead of the
    frontmost runner, and no further than the first thing in the way (a fence hides what is behind
    it). The rest of the way is not known yet: not its names, not what is on it, not where it ends."""
    end = min(len(legs) - 1, front)
    for i in range(front + 1, min(len(legs), front + 1 + SEEN)):
        end = i
        if legs[i].get("obstacle"):
            break
    return end


def chase_route(view: dict, width: int, height: int, blink: int = 0, was: dict[str, int] | None = None) -> Text:
    """A chase as a way up the column: the way ahead at the top, where it began at the bottom. The
    stretches run and those in sight (in_sight) by name, with what is in the way on them and who is
    on them, a trail over the stretches each ran this round (`was`: where they were before it); past
    that one line, 「前方：尚未看清」. How far behind it is and how many more stretches it takes to
    get away (a count: the stretch is not named); everyone's speed and how many stretches a round
    that makes. The columns narrow with the panel."""
    quarry = view.get("quarry", "party")
    runners, legs = view.get("runners", []), view.get("legs", [])
    gap = chase_gap(view)
    close, fleeing = gap <= 1, quarry == "party"
    ahead = [r for r in runners if r["side"] == quarry and r["status"] not in DOWN]
    behind = [r for r in runners if r["side"] != quarry and r["status"] not in DOWN]
    them = [r for r in runners if r["side"] != "party" and r["status"] not in DOWN]
    hot = GORE if close else AMBER
    leg_w, tag_w = (14, 10) if width >= 40 else (10, 8) if width >= 32 else (8, 6)
    out = Text(no_wrap=True, overflow="crop")
    out.append("⟫ 追逐", style=f"bold {GORE if close else CHILL}")
    out.append(f"  第 {max(1, view.get('round', 1))} 轮\n", style=f"bold {BONE}")
    if gap >= 9:
        status = "已脱离" if fleeing else "已跟丢"
    elif fleeing:
        status = "已追至身后" if gap <= 0 else f"在身后 {gap} 段"
    else:
        status = f"在前方 {gap} 段"
    who = "、".join(r["name"] for r in them) or "对方"
    out.append_text(cell_text(who, max(4, width - cell_len(status) - 1), f"bold {BONE}"))
    out.append(" ")
    out.append(status, style=f"bold {'reverse ' if gap <= 0 and blink % 2 else ''}{hot}")
    out.append("\n")
    if 0 <= gap < 9:
        if fleeing:
            goal = "到出口或甩掉追兵即脱离" + (" · 随时可躲藏" if width >= 40 else "")
        else:
            goal = "已在同一处" if gap <= 0 else "追至同一处才能近身攻击"
        out.append(goal + "\n", style=SAFE if fleeing and gap >= 2 else ASH)
    else:
        out.append("\n")
    out.append("─" * width + "\n", style=DEEP)
    # the way: the stretches run and those in sight, as many as the column holds, round the runners
    room = max(3, height - 6 - len(runners))
    marks = [r["pos"] for r in runners if r["status"] not in DOWN and not (r.get("hidden") and r["side"] != "party")] or [0]
    front, hind = max(marks), min(marks)
    hi = in_sight(legs, front)
    lo = max(0, min(hind - 1, hi - room + 1))  # as far back as the column holds, one behind the hindmost at least
    while hi - lo + 1 > room and lo < hi:  # short of room: the stretch behind the hindmost goes first, then those ahead
        if lo < hind or hi <= front:
            lo += 1
        else:
            hi -= 1
    if legs and hi == len(legs) - 1 and legs[-1].get("exit", True):  # the way out is in sight
        out.append("   ⇡ 跑到出口即可脱离\n" if fleeing else "   ⇡ 对方跑到出口即脱离\n", style=SAFE if fleeing else MIST)
    else:
        out.append("   ⋯ 前方：尚未看清\n", style=MIST)
    for i in range(hi, lo - 1, -1):
        leg = legs[i]
        up = [r for r in ahead if r["pos"] == i and not r.get("hidden")]
        down = [r for r in behind if r["pos"] == i]
        hid = [r for r in ahead if r["pos"] == i and r.get("hidden")]
        if up and down:
            mark, mark_style = "✖", f"bold reverse {GORE}"
        elif down:
            mark, mark_style = ("◉◉" if blink % 3 else "◎◎"), f"bold {GORE}"
        elif up:
            mark, mark_style = "◆", f"bold {AMBER if fleeing else BLOOD}"
        elif hid:
            mark, mark_style = "◌", ASH
        elif ran := [r for r in runners if (was or {}).get(r["id"], r["pos"]) < i < r["pos"]]:
            mark, mark_style = "┆", f"bold {AMBER if ran[0]['side'] == 'party' else GORE}"  # run through this round
        else:
            mark, mark_style = "│" if hind <= i <= front else "·", DEEP if hind <= i <= front else MIST
        hunter_row = bool(down)
        danger = min(r["pos"] for r in behind) <= i <= max(r["pos"] for r in ahead) if ahead and behind else False
        name_style = (f"bold {GORE}" if hunter_row else f"bold {AMBER}" if up else "#c48a8a" if danger
                      else ASH if i < hind else BONE)
        row = Text(no_wrap=True)
        row.append_text(cell_text(f" {mark}", 4, mark_style))
        row.append_text(cell_text(leg["name"], leg_w - 1, name_style))  # a cut name never runs into its tag
        row.append(" ")
        ob = leg.get("obstacle")
        if ob:
            if ob.get("open"):
                tag, tag_style = f"▢ {ob['what']}", ASH
            else:
                hard = "" if ob.get("difficulty", "常规") == "常规" else ob["difficulty"][:1]
                tag = f"{'▦' if ob['kind'] == 'barrier' else '⚠'}{ob['skill']}{hard}"
                tag_style = f"bold {BLOOD}" if ob["kind"] == "barrier" else AMBER
        elif leg.get("exit"):
            tag, tag_style = "⇡ 出口", SAFE
        else:
            tag, tag_style = "", ASH
        row.append_text(cell_text(tag, tag_w, tag_style))
        here = [*down, *up, *hid]
        names = "、".join(short_name(r["name"]) + ("（藏）" if r.get("hidden") else "") for r in here)
        who_style = f"bold {GORE}" if down else f"bold {AMBER}" if up else ASH
        row.append_text(cell_text(names, max(0, width - 4 - leg_w - tag_w), who_style))
        if hunter_row:
            row.stylize("on #2a0505")
        out.append_text(row)
        out.append("\n")
    out.append("─" * width, style=DEEP)
    for r in [r for r in runners if r["side"] == "party"] + [r for r in runners if r["side"] != "party"]:
        mine = r["side"] == "party"
        out.append("\n")
        out.append_text(cell_text(("◆ " if mine else "◉ ") + short_name(r["name"]), max(7, min(17, width - 21)),
                                  f"bold {AMBER if mine else BLOOD}"))
        out.append(f" 速度 {r['speed']}", style=BONE)
        if r.get("actions"):
            out.append(f" · 行动点 {r['actions']}", style=ASH)
        if r["status"] in DOWN:
            out.append(f" · {r['status']}", style=f"bold {GORE}")
    return out


NEAR = {0: 1.0, 1: 0.78, 2: 0.55, 3: 0.38}  # how large what chases you stands, by stretches between you


def chase_gap(view: dict) -> int:
    """Stretches between the nearest pursuer and the hindmost of those it chases (9: none)."""
    quarry = view.get("quarry", "party")
    ahead = [r["pos"] for r in view.get("runners", []) if r["side"] == quarry and r["status"] not in DOWN and not r.get("hidden")]
    behind = [r["pos"] for r in view.get("runners", []) if r["side"] != quarry and r["status"] not in DOWN]
    return min(ahead) - max(behind) if ahead and behind else 9


class BloodFlash(ModalScreen[None]):
    """The whole screen floods red and drains, when an investigator is hurt: `strength` 0 to 1.

    It goes only from the front. Textual's `is_current` holds for a screen under another one too,
    and `dismiss()` pops whatever is in front: a flash that drained under a roll prompt pushed
    meanwhile closed the prompt (the turn waiting on it never went on), stayed behind, see-through,
    eating the keys, and the next key dismissed it a second time (InvalidStateError, the app gone).
    Under another window it drains all the same and goes once it is in front again."""

    def __init__(self, strength: float = 0.5):
        super().__init__()
        self.alphas = [min(0.8, 0.25 + 0.55 * strength) * k for k in (1.0, 0.8, 0.6, 0.42, 0.28, 0.15, 0.0)]
        self._left = False  # dismissed: never twice
        self._due = False  # drained under another window: it goes when it is in front again

    def on_mount(self) -> None:
        self._step()

    def _step(self) -> None:
        if not self.alphas:
            self.finish()
            return
        self.styles.background = Color(150, 0, 0, self.alphas.pop(0))
        self.set_timer(0.06, self._step)

    def finish(self) -> AwaitComplete | None:
        """Drained at once and gone, if it is in front; else gone once it is (on_screen_resume). The
        dismissal, to await from outside the flash's own handlers (None: nothing to wait for)."""
        self.alphas = []
        self.styles.background = Color(150, 0, 0, 0.0)
        if self._left:
            return None
        if not self.is_active:
            self._due = True
            return None
        self._left = True
        return self.dismiss()

    def on_screen_resume(self) -> None:
        if self._due:
            self.finish()

    def on_key(self, event: events.Key) -> None:
        """Typing goes on through the blood: the key reaches whatever had it under the flash."""
        event.stop()
        event.prevent_default()
        below = self.app.screen_stack[-2] if len(self.app.screen_stack) > 1 else None
        self.finish()
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


__all__ = ["Arena", "BloodFlash", "Canvas", "Column", "banner", "chase_gap", "chase_lines", "fight_card", "pulse_rate", "title_frames"]
