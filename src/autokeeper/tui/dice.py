"""The percentile dice on the side column, in the skin the player picked.

A check's dice are thrown for real (rules/physdice.py) and the tray replays that very throw:
each frame painted (diceart.py) and rendered by chafa in the player's art style like the
scenes, in a worker thread as fast as the machine allows and shown on the throw's own clock,
frames that cannot be ready in time skipped. The numbers are the dice's own, cut into every
face; once the dice lie still, those on the top faces turn yellow.

Without chafa, or for a roll that was not thrown (the plain dice it falls back to), the tray
draws the pixel-font dice instead: two solid d10s with a shadow, tumbling a moment, then the
result. A digit is 3 pixels wide and 5 tall, drawn with half blocks (▀ ▄ █) so a terminal cell
holds two pixel rows: 3 columns by 3 rows a digit. The tens die shows two digits (30), as a
real tens die does; the units die one.
"""

from __future__ import annotations

import atexit
import io
import itertools
import logging
import os
import pickle
import queue
import random
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from rich.style import Style
from rich.text import Text

FONT = {  # 3 x 5 pixels, row by row
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"),
    "3": ("###", "..#", ".##", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", ".#.", ".#.", ".#."),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"),
}
HALF = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}
FRAMES = {  # corners and edges: top-left, top, top-right, side, bottom-left, bottom, bottom-right
    "rounded": "╭─╮│╰─╯",
    "heavy": "┏━┓┃┗━┛",
    "double": "╔═╗║╚═╝",
    "square": "┌─┐│└─┘",
    "eldritch": "╭≈╮┆╰≈╯",
}
LEVEL_EDGE = {"大成功": "#ffd700", "极难成功": "#7cfc00", "困难成功": "#3cb371", "常规成功": "#3cb371",
              "失败": "#ff6347", "大失败": "#b00020"}  # fmt: skip
GOLD = "#ffd700"  # the top faces' numbers once the dice lie still
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())  # recorded if logging is set up, never printed over the game


@dataclass(frozen=True)
class Skin:
    name: str
    body: str | None  # the die's colour in the pixel dice; None: see-through, only its outline
    ink: str  # the numbers
    edge: str  # the outline while it tumbles
    shadow: str
    frame: str  # FRAMES


SKINS: dict[str, Skin] = {  # painted (diceart.py) as:
    "bone": Skin("骨白", "#e6d8b8", "#3b2614", "#a8977a", "#3a3026", "rounded"),  # carved bone, tentacles inked into it
    "obsidian": Skin("黑曜石", "#1a1622", "#e9c46a", "#6d5a96", "#000000", "heavy"),  # black glass, a green eye on every face
    "blood": Skin("血珀", "#5e0b0b", "#ffd9cc", "#c1121f", "#1c0303", "rounded"),  # amber with a tentacle caught inside
    "brass": Skin("黄铜", "#b58f52", "#2a1a08", "#e3c17d", "#3d2c12", "double"),  # R'lyeh glyphs cut in, verdigris
    "eldritch": Skin("青石", "#0e3a38", "#b8f5da", "#7b5ea7", "#041413", "eldritch"),  # green stone, Elder Signs, a tentacle round it
    "phosphor": Skin("磷光", None, "#33ff66", "#1fbf4c", "#062b0f", "square"),  # a vector screen's wireframe
}
DEFAULT = "bone"
WIDTH, HEIGHT = 11, 5  # a die: its border, three rows of digits between blank margins


def digits(number: str) -> list[str]:
    """`number` in the pixel font, as three rows of half blocks, a column between digits."""
    rows = ["", "", ""]
    for i, ch in enumerate(number):
        glyph = (*FONT.get(ch, FONT["0"]), "...")  # six pixel rows: three cell rows
        for r in range(3):
            top, bottom = glyph[2 * r], glyph[2 * r + 1]
            rows[r] += ("" if i == 0 else " ") + "".join(HALF[(a == "#", b == "#")] for a, b in zip(top, bottom, strict=True))
    return rows


def die(number: str, skin: Skin, edge: str | None = None) -> list[Text]:
    """One die, HEIGHT rows of WIDTH + 1 cells (the last column and a row under it: shadow)."""
    tl, t, tr, side, bl, b, br = FRAMES[skin.frame]
    edge_style = Style(color=edge or skin.edge, bold=True)
    body = Style(bgcolor=skin.body) if skin.body else Style()
    ink = Style(color=skin.ink, bgcolor=skin.body, bold=True) if skin.body else Style(color=skin.ink, bold=True)
    shade = Style(color=skin.shadow)
    inner = WIDTH - 2
    face = digits(number)
    pad = (inner - len(face[0])) // 2
    lines = [Text.assemble((tl + t * inner + tr, edge_style), (" ", ""))]
    for row in face:
        text = Text(side, style=edge_style)
        text.append(" " * pad, style=body)
        text.append(row, style=ink)
        text.append(" " * (inner - pad - len(row)), style=body)
        text.append(side, style=edge_style)
        text.append("▌", style=shade)
        lines.append(text)
    lines.append(Text.assemble((bl + b * inner + br, edge_style), ("▌", shade)))
    lines.append(Text.assemble((" ", ""), ("▀" * WIDTH, shade)))
    return lines


def pair(tens: str, units: str, skin: Skin, *, rolling: bool = False, level: str = "") -> Text:
    """Both dice side by side. Tumbling, each bounces a row on its own; settled, their
    outline takes the result's colour."""
    edge = None if rolling else LEVEL_EDGE.get(level)
    left, right = die(tens, skin, edge), die(units, skin, edge)
    blank = Text(" " * (WIDTH + 1))
    if rolling:  # one die up, the other down, at random: they tumble
        lift = random.choice((0, 1))
        left = ([blank] * lift) + left + ([blank] * (1 - lift))
        right = ([blank] * (1 - lift)) + right + ([blank] * lift)
    else:
        left, right = [*left, blank], [*right, blank]
    out = Text()
    for i, (a, b) in enumerate(zip(left, right, strict=True)):
        out.append("  ")
        out.append_text(a)
        out.append("  ")
        out.append_text(b)
        if i < len(left) - 1:
            out.append("\n")
    return out


def skin(name: str | None) -> Skin:
    return SKINS.get(name or DEFAULT, SKINS[DEFAULT])


# -- chafa ----------------------------------------------------------------------------------------
def chafa(im, cols: int, rows: int, symbols: list[str], *, work: int = 5) -> str:
    """A frame through chafa with the art style's symbols (imagery.STYLES), as scenes are, but
    made for small pictures many times a second: an uncompressed PNG, one thread (a thread pool's
    start costs more than such a picture). Padded to `rows` lines: chafa may come a row short."""
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=0)
    colors = "full" if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") else "240"
    cmd = ["chafa", "-f", "symbols", "-c", colors, "--font-ratio", "1/2", "--work", str(work), "--threads", "1",
           "--size", f"{cols}x{rows}", *symbols, "-"]
    ansi = subprocess.run(cmd, input=buf.getvalue(), capture_output=True, timeout=10).stdout.decode("utf-8", "replace")
    ansi = ansi.replace("\x1b[?25l", "").replace("\x1b[?25h", "").rstrip("\n")
    return ansi + ("\n" + " " * cols) * max(0, rows - (ansi.count("\n") + 1))


def text(ansi: str) -> Text:
    """A frame's ANSI as text for the screen."""
    return Text.from_ansi(ansi, no_wrap=True, overflow="crop", end="")


# -- a throw on the tray -----------------------------------------------------------------------
BUDGET = 1.4  # seconds: a longer throw is played faster, so it never drags


@dataclass
class Replay:
    """A thrown check as the tray shows it: the recording, and which dice's numbers turn gold
    (the units die and the tens die that counted)."""

    kinds: list[str]
    labels: list[str]
    tray: tuple[float, float]
    fps: float
    centres: np.ndarray  # (frames, dice, 3)
    turns: np.ndarray  # (frames, dice, 3, 3)
    gold: list[bool]

    @property
    def frames(self) -> int:
        return len(self.centres)

    @property
    def speed(self) -> float:
        return max(1.0, (self.frames - 1) / self.fps / BUDGET)

    def due(self, k: int) -> float:
        """When frame k is shown, in seconds from the start."""
        return k / self.fps / self.speed


def replay(view: dict) -> Replay | None:
    """The throw in a dice event, or None (plain dice; an old or broken recording)."""
    from ..rules.physdice import decode

    t = view.get("throw")
    try:
        centres, turns = decode(t)  # type: ignore[arg-type]
        kinds, labels = list(t["kinds"]), list(t["labels"])  # type: ignore[index]
        if not len(centres) or len(kinds) != centres.shape[1] or len(labels) != len(kinds):
            return None
        kept = int(view.get("kept") or 0)
        tens = [i for i, k in enumerate(kinds) if k == "tens"]
        gold = [k == "units" or (len(tens) > 1 and tens.index(i) == kept) or len(tens) == 1 for i, k in enumerate(kinds)]
        return Replay(kinds, labels, (float(t["tray"][0]), float(t["tray"][1])), float(t["fps"]), centres, turns, gold)  # type: ignore[index]
    except (TypeError, KeyError, ValueError, IndexError):
        return None


@dataclass
class Timing:
    """What a throw's frames took, per frame in ms: painting them, and chafa (with reading its
    output back as text)."""

    paint: list[float] = field(default_factory=list)
    chafa: list[float] = field(default_factory=list)


def frame(r: Replay, k: int, skin_key: str, cols: int, rows: int, symbols: list[str], timing: Timing | None = None) -> str:
    """Frame k of the throw (ANSI): painted, then through chafa with the art style's `symbols`.
    In the last the dice lie still and the numbers on the top faces of those that count are yellow."""
    from . import diceart

    t0 = time.perf_counter()
    last = k == r.frames - 1
    dice = [(kind, r.centres[k, i], r.turns[k, i]) for i, kind in enumerate(r.kinds)]
    im, _ = diceart.picture(skin_key, cols, rows, r.tray, dice, gold=r.gold if last else None)
    t1 = time.perf_counter()
    out = chafa(im, cols, rows, symbols, work=9 if last else 5)
    if timing is not None:
        timing.paint.append((t1 - t0) * 1000)
        timing.chafa.append((time.perf_counter() - t1) * 1000)
    return out


def _read(stream, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError("the painter has gone")
        data += chunk
    return data


def _send(stream, obj) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(len(data).to_bytes(4, "little") + data)
    stream.flush()


def _serve() -> None:
    """The painter's loop (python -m autokeeper.tui.dice). Requests come in on stdin, each a
    pickle after its length: ("warm", (skin, cols, rows)) or ("throw", (id, job, animate)). A
    throw's frames go out on stdout as they fall due, each (id, k, ansi, last, stats); a new
    request in the middle of a throw ends it."""
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    requests: queue.Queue = queue.Queue()

    def listen() -> None:
        while True:
            try:
                requests.put(pickle.loads(_read(stdin, int.from_bytes(_read(stdin, 4), "little"))))
            except (EOFError, OSError):
                requests.put(None)
                return

    threading.Thread(target=listen, daemon=True).start()
    while (request := requests.get()) is not None:
        kind, args = request
        if kind == "warm":
            try:
                warm(*args)
            except Exception:  # only a head start: the throw paints what it needs anyway
                log.info("could not warm the tray", exc_info=True)
            continue
        tid, job, animate = args
        timing = Timing()
        showing = Showing(job[0], lambda k, job=job, timing=timing: frame(job[0], k, *job[1:], timing=timing), animate=animate)

        def show(k: int, ansi: str, last: bool, tid=tid, showing=showing, timing=timing) -> None:
            stats = {"shown": showing.shown, "paint": timing.paint, "chafa": timing.chafa, "ms": showing.ms} if last else None
            _send(stdout, (tid, k, ansi, last, stats))
            if not requests.empty():  # another throw, or a skin to warm: this one is over
                showing.stop.set()

        try:
            showing.run(show)
        except Exception:
            log.info("a throw could not be painted", exc_info=True)
            _send(stdout, (tid, -1, None, True, None))


class Painter:
    """A process of its own that paints the throws and keeps their time. A thread of the game's
    would share the interpreter with the screen: while the story types or a card tumbles, every
    small step of painting waits its turn, and the frames crawl (and the screen with them). In
    its own process the painting keeps its pace and the screen keeps its; a reader thread here
    only hands each frame on as it comes. The process is this module, run with python -m. If it
    cannot be had, or dies, throw() says so and the tray paints here instead."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._listener: tuple[int, Callable] | None = None  # the throw waiting for its frames
        self.broken = False

    def _post(self, request: tuple) -> None:
        if self._proc is None or self._proc.poll() is not None:
            proc = subprocess.Popen([sys.executable, "-m", "autokeeper.tui.dice"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._proc = proc
            threading.Thread(target=self._listen, args=(proc,), daemon=True).start()
        assert self._proc.stdin is not None
        _send(self._proc.stdin, request)

    def _listen(self, proc: subprocess.Popen) -> None:
        """Frames as the painter sends them, to the throw waiting for them."""
        assert proc.stdout is not None
        try:
            while True:
                tid, k, ansi, last, stats = pickle.loads(_read(proc.stdout, int.from_bytes(_read(proc.stdout, 4), "little")))
                listener = self._listener
                if listener is not None and listener[0] == tid:
                    listener[1](k, ansi, last, stats)
        except Exception:
            if proc is self._proc:  # it died, not closed: paint here from now on
                log.warning("the dice painter stopped; painting in the game's own process", exc_info=True)
                self.broken = True
                listener, self._listener = self._listener, None
                if listener is not None:
                    listener[1](-1, None, True, None)

    def throw(self, job: tuple, animate: bool, show: Callable[[int, str | None, bool, dict | None], None]) -> bool:
        """Start painting a throw: show(k, ansi, last, stats) gets its frames, in another thread,
        on the throw's own clock (ansi None: it failed). False: no painter, paint it here."""
        if self.broken:
            return False
        with self._lock:
            self._listener = (next(self._ids), show)
            try:
                self._post(("throw", (self._listener[0], job, animate)))
                return True
            except Exception:
                log.warning("no dice painter; painting in the game's own process", exc_info=True)
                self.broken, self._listener = True, None
                return False

    def warm(self, skin_key: str, cols: int, rows: int) -> None:
        """Start the process and paint the empty tray at this size ahead of the first throw."""
        if self.broken:
            warm(skin_key, cols, rows)
            return
        with self._lock:
            try:
                self._post(("warm", (skin_key, cols, rows)))
            except Exception:
                log.warning("no dice painter; painting in the game's own process", exc_info=True)
                self.broken = True

    def close(self) -> None:
        with self._lock:
            proc, self._proc, self._listener = self._proc, None, None
        if proc is not None and proc.poll() is None:
            if proc.stdin:
                proc.stdin.close()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()


_painter: Painter | None = None


def painter() -> Painter:
    global _painter
    if _painter is None:
        _painter = Painter()
        atexit.register(_painter.close)
    return _painter


@dataclass
class Ticket:
    """A throw on its way to the tray, painted by the painter or here: the frame last sent, and
    once it is over, what it took."""

    posted: int = -1
    done: bool = False
    stats: dict | None = None
    stop: threading.Event = field(default_factory=threading.Event)


class Showing:
    """One throw on the tray: its frames rendered (as ANSI) in a worker thread and handed to `show`
    on the throw's own clock. A frame that could not be ready before the next one is due is skipped,
    so a slow machine sees stop motion instead of a slow throw; the last frame, the dice at rest
    with their numbers, is always shown. Without animation only the last is rendered."""

    def __init__(self, r: Replay, render: Callable[[int], str], *, animate: bool = True,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 stop: threading.Event | None = None):
        self.replay, self.render, self.animate = r, render, animate
        self.clock, self.sleep = clock, sleep
        self.stop = stop or threading.Event()
        self.shown: list[int] = []  # frame indices, in the order shown
        self.ms = 0.0  # from the start to the dice at rest on the screen
        self.done = False

    def run(self, show: Callable[[int, str, bool], None]) -> None:
        try:
            self._run(show)
        finally:
            self.done = True

    def _run(self, show: Callable[[int, str, bool], None]) -> None:
        r = self.replay
        last = r.frames - 1
        began = self.clock()
        start: float | None = None  # the throw's clock starts with its first frame on the screen
        guess = 0.0  # how long a frame takes here, learnt as they come (the first warms caches: not a guide)
        k = 0 if self.animate else last
        while not self.stop.is_set():
            if start is not None and k < last and self.clock() - start + guess > r.due(k + 1):
                k += 1  # it would come too late: on to a later one
                continue
            t0 = self.clock()
            try:
                text = self.render(k)
            except (OSError, subprocess.TimeoutExpired):
                if k == last:
                    return
                k += 1
                continue
            if start is not None:
                took = self.clock() - t0
                guess = took if not guess else 0.7 * guess + 0.3 * took
                wait = r.due(k) - (self.clock() - start)
                if wait > 0:
                    self.sleep(wait)
            if self.stop.is_set():
                return
            self.shown.append(k)
            if k == last:
                self.ms = (self.clock() - began) * 1000
            show(k, text, k == last)
            if k == last:
                return
            if start is None:
                start = self.clock() - r.due(k)
            k += 1


def warm(skin_key: str, cols: int, rows: int) -> None:
    """Paint the empty tray at this size ahead of the first throw: the camera and the room."""
    from ..rules.physdice import TRAYS
    from . import diceart

    if cols >= 5 and rows >= 5:
        for n in (2, 3):
            diceart.room(skin_key, cols, rows, TRAYS[n])


def preview() -> dict:
    """A throw to look at a skin with: real dice, thrown for nothing, shaped like a check's event."""
    from ..rules import physdice

    try:
        t = physdice.throw(random.SystemRandom().getrandbits(63))
    except Exception:  # no throw, no matter: the pixel dice show the skin
        log.info("no throw for the preview", exc_info=True)
        return {"skill": "试掷", "value": 37}
    tens, units = int(t.labels[0]), int(t.labels[1])
    return {"skill": "试掷", "value": physdice.percentile(tens, units), "tens": [tens], "units": units, "kept": 0, "throw": t.encode()}


if __name__ == "__main__":  # the painter's process: the module proper does the work
    from autokeeper.tui import dice as module

    module._serve()
