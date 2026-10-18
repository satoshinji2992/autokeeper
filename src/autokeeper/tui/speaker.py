"""Whoever is talking, on the stage, the way a visual novel shows its speaker: their portrait
over the right side of the scene and their name under it, for as long as the talk goes on.

The portrait is the module's picture (chafa, in the art style Ctrl+A picks) or its hand-drawn
art. It takes the colours of the line's mood (mood.py reads it off the NPC's own stage
directions and words; artkit.TONES grades it), and three moods move for a moment: fear
trembles, anger flashes red, a fluster jolts. Each motion is over within half a second and
plays only while the weather is animated (F6 switches both).

The panel goes when the scene moves on:
- the party moves, an event takes the stage, or the story ends;
- a turn is told without them: the keeper starts a narration and they have not spoken since
  the narration before (the one right after their line keeps them);
- LINGER seconds after their last line, unless a turn is under way.
One speaker at a time: when two NPCs talk in one exchange, the latest takes the panel, and
the story keeps who said what.

The panel sits on a layer of its own, docked over the scene, so the scene keeps its width,
its weather, its light and its scan-in. It is as tall as the scene: a place without a picture,
or with a short drawing, grows to hold the face. Its portrait is drawn again, off the UI
thread, when that height or the art style changes; every variant is cached.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from functools import lru_cache, partial

from rich.text import Text
from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Vertical
from textual.widgets import Static

from .. import artkit, imagery, mood
from ..module.schema import NPC, Module
from .widgets import SceneArt

LINGER = 120.0  # seconds after their last line, with nothing going on
WIDEST = 0.4  # the panel's share of the stage's width, at most
NARROWEST = 48  # a stage narrower than this has no room for a panel
PICTURE_ROWS = 8  # a picture portrait's least height, in rows
CHROME = 3  # the panel's border and padding, in columns
# Motions per mood: seconds between steps, and the face's offset at each step.
MOTIONS: dict[str, tuple[float, list[tuple[int, int]]]] = {
    "害怕": (0.05, [(1, 0), (-1, 0), (1, 0), (-1, 0), (1, 0), (0, 0)]),  # a tremble
    "慌乱": (0.08, [(-1, 0), (0, 0), (1, 0), (0, 0), (-1, 0), (0, 0)]),  # jolts this way and that
}
FLASH = (0.55, 0.42, 0.3, 0.18, 0.08, 0.0)  # anger: a red tint fading out, a step every 0.06 s
RED = (255, 60, 40)


@dataclass(frozen=True)
class Portrait:
    """What the module has for a face: a picture (art/<name>.jpg) and/or hand-drawn art."""

    picture: str = ""
    art: str = ""
    mask: str | None = None

    @classmethod
    def of(cls, m: Module, npc: NPC) -> Portrait | None:
        name = npc.portrait or ""
        picture = m.pictures.get(name, "") if imagery.available() else ""
        art = m.art.get(name, "")
        return cls(picture, art, m.art_colors.get(name)) if picture or art else None


@lru_cache(maxsize=128)
def face(p: Portrait, feeling: str, size: tuple[int, int] = (0, 0), style: str = "") -> Text:
    """A portrait in a mood, cached per (portrait, mood, size, style). A picture is rendered by
    chafa into `size` (cols × rows, its shape kept), so call it off the UI thread; hand-drawn
    art keeps its own size and has no style."""
    graded = feeling if feeling in artkit.TONES else ""
    if p.picture:
        return Text.from_ansi(imagery.render(p.picture, *size, style, graded=graded))
    return artkit.colorize(p.art, p.mask, grade=graded)


def _width(text: Text) -> int:
    return max((line.cell_len for line in text.split("\n")), default=0)


class Speaker(Vertical):
    """The speaker's panel. The game screen hands it every event (follow)."""

    DEFAULT_CSS = """
    Speaker {
        layer: speaker;
        dock: right;
        height: 100%;
        display: none;
        padding: 0 1;
        background: $background;
        border-left: tall $accent;
    }
    Speaker .face {
        width: 100%;
        height: 1fr;
        content-align: center bottom;
    }
    Speaker .who {
        width: 100%;
        height: 1;
        content-align: center middle;
        text-style: bold;
        color: $accent;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.who = ""  # their name as the story gives it
        self.npc_id = ""
        self.portrait: Portrait | None = None
        self.feeling = mood.CALM
        self.fresh = False  # they have spoken since the keeper's last narration
        self.since = 0.0  # when they last spoke
        self._fitted: tuple = ()  # the face on show: (portrait, mood, size, style)

    def compose(self) -> ComposeResult:
        yield Static(classes="face")
        yield Static(classes="who")

    def on_mount(self) -> None:
        self.set_interval(1.0, self._linger)
        self.run_worker(partial(asyncio.to_thread, mood.warm), group="speaker-warm", exit_on_error=False)

    @property
    def scene(self) -> SceneArt | None:
        # During teardown the scene sibling may be gone before this widget's worker
        # returns from its thread. Treat that exactly like a stage with no room.
        if not self.is_attached:
            return None
        return next(iter(self.screen.query(SceneArt)), None)

    # -- the rules ---------------------------------------------------------------------------
    def follow(self, ev: dict, m: Module) -> None:
        """One event from the game screen: who speaks, and whether the scene has moved on."""
        t = ev.get("type")
        if t in ("npc", "performance"):
            self.said(m, ev)
        elif t == "art" and self.npc_id:
            npc = next((n for n in m.npcs if n.id == self.npc_id), None)
            if npc is not None and npc.portrait == ev.get("key"):
                self.portrait = Portrait.of(m, npc)
                self.refit()
        elif t in ("move", "event", "ending"):
            self.leave()
        elif t == "keeper_start" and not ev.get("again"):  # told again, it is still the same turn
            if not self.fresh:
                self.leave()
            self.fresh = False

    def said(self, m: Module, ev: dict) -> None:
        npc = next((n for n in m.npcs if n.id == ev.get("npc")), None)
        portrait = Portrait.of(m, npc) if npc else None
        if npc is None or portrait is None:  # someone without a face: the last one would be wrong
            self.leave()
            return
        self.who, self.portrait = ev.get("name") or npc.name, portrait
        self.npc_id = npc.id
        self.fresh, self.since = True, time.monotonic()
        performing = ev.get("type") == "performance"  # their words are a story about others
        self.run_worker(partial(self._speak, ev.get("text", ""), npc.look, performing), group="speaker", exclusive=True)

    def leave(self) -> None:
        self.workers.cancel_group(self, "speaker")
        self.workers.cancel_group(self, "speaker-fit")
        self.portrait, self.fresh = None, False
        self.npc_id = ""
        self._hide()

    def _linger(self) -> None:
        if self.display and not getattr(self.screen, "busy", False) and time.monotonic() - self.since > LINGER:
            self.leave()

    # -- drawing -----------------------------------------------------------------------------
    def on_resize(self) -> None:
        self.refit()

    def refit(self) -> None:
        """Draw the face again if the stage's size or the art style changed (the screen calls
        this on a resize and on Ctrl+A). A face put away for lack of room comes back with it."""
        if self.portrait is not None and self._wanted() != self._fitted:
            self.run_worker(self._draw, group="speaker-fit", exclusive=True)

    def _wanted(self) -> tuple:
        """What should be on show: (portrait, mood, face size, style, panel rows); () for nothing,
        as on a stage too narrow for a panel. The panel is as tall as the scene, or as the face
        needs on a bare or short stage; a picture gets the panel's height but the name's row."""
        p, scene = self.portrait, self.scene
        if p is None or scene is None:
            return ()
        width = scene.outer_size.width
        if width < NARROWEST:
            return ()
        here = scene.outer_size.height
        if not p.picture:
            return (p, self.feeling, (0, 0), "", max(here, len(p.art.split("\n")) + 2))
        rows = max(here, 1 + max(PICTURE_ROWS, round(scene._fit()[1] * 0.7)))
        return (p, self.feeling, (int(width * WIDEST) - CHROME, rows - 1), self.app.art_style, rows)  # type: ignore[attr-defined]

    async def _speak(self, text: str, look: str, performing: bool) -> None:
        self.feeling = await asyncio.to_thread(mood.read, text, habits=look, words=not performing)
        if await self._draw():
            self._move()

    async def _draw(self) -> bool:
        p = self.portrait
        while True:  # the stage may change while chafa works: then again, for the stage as it is now
            wanted = self._wanted()
            if self.portrait is not p or not wanted:  # someone else spoke meanwhile, or no room
                if not wanted:
                    self._hide()  # the story still has their name and words
                return False
            art = await asyncio.to_thread(face, *wanted[:4])
            if self.portrait is p and self._wanted() == wanted:
                break
        scene, rows = self.scene, wanted[4]
        if scene is None:
            return False
        name = Text(self.who, style="bold")
        width = min(max(_width(art), name.cell_len) + CHROME, int(scene.outer_size.width * WIDEST))
        self.query_one(".face", Static).update(art)
        self.query_one(".who", Static).update(name)
        self.styles.width = width
        self.parent.styles.min_height = rows + 1 if rows > scene.outer_size.height else None  # type: ignore[union-attr]  # + its border
        scene.cover(width)
        self.display = True
        self._fitted = wanted
        return True

    def _hide(self) -> None:
        self._fitted = ()
        if self.display:
            self.display = False
            if self.parent is not None:
                self.parent.styles.min_height = None
            if (scene := self.scene) is not None:
                scene.cover(0)

    # -- motion ------------------------------------------------------------------------------
    def _move(self) -> None:
        scene = self.scene
        if scene is None or not scene.effects:  # F6: no animation at all
            return
        if self.feeling in MOTIONS:
            step, path = MOTIONS[self.feeling]
            for i, at in enumerate(path):
                self.set_timer(step * (i + 1), partial(self._nudge, at))
        elif self.feeling == "发怒":
            for i, alpha in enumerate(FLASH):
                self.set_timer(0.06 * i + 0.01, partial(self._flash, alpha))

    def _nudge(self, at: tuple[int, int]) -> None:
        self.query_one(".face", Static).styles.offset = at

    def _flash(self, alpha: float) -> None:
        self.query_one(".face", Static).styles.tint = Color(*RED, alpha)
