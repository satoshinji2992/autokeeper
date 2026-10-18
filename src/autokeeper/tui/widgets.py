"""Game-screen widgets: scene art, story log with a typewriter, dice tray, sheet, notebook."""

from __future__ import annotations

import asyncio
import random
import time
from functools import partial

from rich.console import Group
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from .. import artkit, atmosphere, imagery, weather
from ..rules.coc import Investigator
from . import art

LEVEL_STYLE = {
    "大成功": "bold #ffd700 reverse",
    "极难成功": "bold #7CFC00",
    "困难成功": "bold #3cb371",
    "常规成功": "bold #e0e0e0",
    "失败": "bold #ff6347",
    "大失败": "bold #ffffff on #8b0000",
}


LIGHT_MARK = {"清晨": "◌", "上午": "☀", "晌午": "☀", "下午": "☀", "傍晚": "◐", "夜里": "☾", "深夜": "☾"}


def center(art_text: Text, width: int) -> Text:
    """Centre a block of art in the panel."""
    lines = art_text.split("\n")
    pad = max(0, (width - max((ln.cell_len for ln in lines), default=0)) // 2)
    return Text("\n").join(Text(" " * pad) + ln for ln in lines) if pad else art_text


class SceneArt(Static):
    """Location name and its art: a real picture rendered by chafa to fill the panel, or the
    hand-drawn ASCII fallback. Both are lit for the time of day (artkit.LIGHTS), scan in when
    the party arrives somewhere new, carry the place's weather (atmosphere.py) and glitch
    when sanity drops."""

    TICK = 0.15  # weather frames, about seven a second
    ROWS = (9, 26)  # the art's height: about 38% of the terminal's, within these bounds

    def __init__(self, **kw):
        super().__init__("", **kw)
        self.place = ""
        self.picture = ""
        self.mask: str | None = None
        self.image: str | None = None
        self.weather: list[str] = []  # the module's own; empty: read off the description
        self.description = ""
        self.sky = ""  # the narrated weather, for places under the open sky
        self.style_name = "picture"
        self.when = ""  # time of day, lights the art
        self.effects = True  # weather animation, F6
        self.madness = 0.0
        self._burst = 0
        self._art: Text | None = None  # the current art, ready to paint
        self._atmo: atmosphere.Atmosphere | None = None
        self._hold = 0.0  # weather waits until then (a glitch or a flicker owns the panel)
        self._fitted = (0, 0)  # the size the art was last drawn for (not _size: Textual owns that)

    def on_mount(self) -> None:
        self.set_interval(1.6, self._idle_flicker)
        self.set_interval(self.TICK, self._weather)

    def on_resize(self) -> None:
        self.refit()

    def refit(self) -> None:
        """Redraw when the art's size changes: the panel's width, or the terminal's height,
        which sets the art's (the screen calls this). The widget's own height follows the
        content, so reacting to it would rebuild the scene every time a frame is painted."""
        if self._fit() != self._fitted:
            self._fitted = self._fit()
            self._refresh()

    def show(
        self,
        place: str,
        picture: str,
        mask: str | None = None,
        image: str | None = None,
        *,
        weather: list[str] | None = None,
        description: str = "",
        sky: str = "",
    ) -> None:
        arrived = place != self.place
        self.place, self.picture, self.mask, self.image = place, picture, mask, image
        self.weather, self.description, self.sky = list(weather or []), description, sky
        self._refresh(reveal=arrived)

    def set_sky(self, sky: str) -> None:
        if sky != self.sky:
            self.sky = sky
            self._refresh()

    def set_light(self, when: str) -> None:
        if when != self.when:
            self.when = when
            self._refresh()

    def toggle_effects(self) -> bool:
        self.effects = not self.effects
        self._refresh()
        return self.effects

    def kinds(self) -> list[str]:
        """The weather on screen now: what the keeper said the sky is doing, then the place's
        own (the module's, else read off the place for this hour). At most two."""
        if not self.effects or not (self.picture or self._uses_image()):
            return []
        own = self.weather[:2] or weather.infer(self.place, self.description, self.when)
        if not self.sky:
            return own
        wet = {"rain", "storm"}
        return [self.sky, *(k for k in own if k != self.sky and not (k in wet and self.sky in wet))][:2]

    def _fit(self) -> tuple[int, int]:
        rows = round(self.app.size.height * 0.38)
        return max(24, (self.size.width or 72) - 2), max(self.ROWS[0], min(self.ROWS[1], rows))

    def _uses_image(self) -> bool:
        return bool(self.image) and imagery.available()

    def _draw(self, rate: float = 0.0, graded: str = "") -> Text:
        """The art alone, lit and centred. Pictures go through chafa, so call off the UI thread.
        `graded` makes a copy for the weather layer; "beam" is plain white light (a torch)."""
        w, h = self._fit()
        when, graded = ("", "") if graded == "beam" else (self.when, graded)
        if self._uses_image():
            return center(Text.from_ansi(imagery.render(self.image, w, h, self.style_name, when, True, graded)), w)
        if self.picture:
            return center(artkit.colorize(art.corrupt(self.picture, rate), self.mask, when=when, grade=graded), w)
        return Text()

    def _title(self) -> Text:
        t = Text()
        t.append(f"◆ {self.place}", style="bold")
        if self.when:
            t.append(f"   {LIGHT_MARK.get(self.when, '')} {self.when}", style="dim")
        return t

    def _paint(self, rate: float = 0.0, frame: str | None = None, rows: int | None = None, art_text: Text | None = None) -> None:
        if art_text is not None:
            pass
        elif frame is not None:
            art_text = center(Text.from_ansi(frame), self._fit()[0])
        elif rate or self._art is None:
            art_text = self._draw(rate) if not self._uses_image() else (self._art or Text())
        else:
            art_text = self._art
        if rows is not None:  # scan-in: the rows below the beam are still dark
            lines = art_text.split("\n")
            beam = Text("▔" * min(self._fit()[0], max((ln.cell_len for ln in lines), default=0)), style="dim")
            art_text = Text("\n").join([*lines[:rows], beam] if rows < len(lines) else lines)
        self.update(Text("\n").join([self._title(), art_text]))

    def _refresh(self, reveal: bool = False) -> None:
        self.run_worker(partial(self._load, reveal), exclusive=True, group="art")

    def _build(self, kinds: list[str]) -> tuple[Text, atmosphere.Atmosphere | None]:
        """The lit art, and the weather over it with the graded copies it needs (thread)."""
        base = self._draw()
        if not kinds or not base.plain.strip():
            return base, None
        w, h = self._fit()[0], len(base.split("\n"))  # as tall as the art, so the panel keeps its size
        layers = {"": base} | {g: self._draw(graded=g) for g in atmosphere.grades(kinds)}
        return base, atmosphere.Atmosphere(kinds, layers, w, h, when=self.when)

    async def _load(self, reveal: bool) -> None:
        self._atmo = None  # no weather over a half-loaded scene
        self._art, atmo = await asyncio.to_thread(self._build, self.kinds())
        if reveal:
            total = len(self._art.split("\n"))
            for rows in range(0, total, 2):
                self._paint(rows=rows)
                await asyncio.sleep(0.035)
        self._atmo = atmo
        self._paint(art_text=atmo.compose() if atmo else None)

    def _weather(self) -> None:
        if self._atmo is None or self._burst or time.monotonic() < self._hold:
            return
        self._paint(art_text=self._atmo.frame())

    def quiet(self, seconds: float) -> None:
        """Hold the weather still for a while (the player is typing)."""
        self._hold = max(self._hold, time.monotonic() + seconds)

    def glitch(self, frames: int = 14) -> None:
        self.add_class("dread")
        if self._uses_image():
            self.run_worker(partial(self._picture_glitch, min(8, max(3, frames // 3))), exclusive=True, group="art")
            return
        self._burst = frames
        self._tick()

    async def _picture_glitch(self, frames: int) -> None:
        w, h = self._fit()
        self._hold = time.monotonic() + 60  # until the glitch is over
        try:
            for i in range(frames):
                strength = 1.0 - i / frames
                frame = await asyncio.to_thread(imagery.glitch, self.image, w, h, strength, self.style_name, self.when, True)
                self._paint(frame=frame)
                await asyncio.sleep(0.05)
        finally:
            self._hold = 0.0
        self.remove_class("dread")
        self._paint()

    def _tick(self) -> None:
        if self._burst <= 0:
            self.remove_class("dread")
            self._paint()
            return
        self._paint(0.08 + 0.4 * self._burst / 14)
        self._burst -= 1
        self.set_timer(0.06, self._tick)

    def _idle_flicker(self) -> None:
        if self._burst or self.madness <= 0 or random.random() > self.madness:
            return
        if self._uses_image():
            self.run_worker(partial(self._picture_glitch, 2), exclusive=True, group="art")
            return
        self._hold = time.monotonic() + 0.12
        self._paint(0.03 + self.madness * 0.08)
        self.set_timer(0.12, self._paint)


class StoryLog(VerticalScroll):
    """Entries mount as static blocks; the keeper's narration types itself out."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._live: Static | None = None
        self._shown = ""
        self._pending = ""
        self._typing = False

    def add(self, kind: str, body: Text | str | Group, *, who: str = "") -> Static:
        if isinstance(body, str):
            text = Text()
            if who:
                text.append(f"{who}：", style="bold")
            text.append(body)
            body = text
        block = Static(body, classes=f"entry {kind}")
        self.mount(block)
        self.scroll_end(animate=False)
        return block

    # -- typewriter ----------------------------------------------------------------
    def begin_keeper(self) -> None:
        self._live = self.add("keeper", Text(""))
        self._shown, self._pending = "", ""

    def feed(self, text: str) -> None:
        self._pending += text
        if not self._typing:
            self._typing = True
            self._type()

    def _type(self) -> None:
        if not self._pending or self._live is None:
            self._typing = False
            return
        step = max(1, len(self._pending) // 12)
        self._shown += self._pending[:step]
        self._pending = self._pending[step:]
        self._live.update(Text(self._shown + "▌"))
        self.scroll_end(animate=False)
        self.set_timer(0.03, self._type)

    def end_keeper(self, full: str) -> None:
        if self._live is None:
            self._live = self.add("keeper", Text(""))
        self._pending = ""
        self._typing = False
        self._live.update(Text(full))
        self._live = None
        self.scroll_end(animate=False)


class DiceTray(Static):
    """Two spinning d10s that settle on BCDice's result."""

    def __init__(self, **kw):
        super().__init__(Text("  .ra 技能  掷一次检定\n  /draw 手绘  /notes 线索本", style="dim"), **kw)
        self._frames = 0
        self._view: dict | None = None

    def roll(self, view: dict) -> None:
        self._view = view
        self._frames = 12
        self._spin()

    def _spin(self) -> None:
        v = self._view or {}
        if self._frames > 0:
            self._frames -= 1
            self._paint(random.choice(art.DIGITS), random.choice(art.DIGITS), rolling=True)
            self.set_timer(0.055, self._spin)
            return
        value = int(v.get("value") or 0)
        tens, units = ("0", "0") if value == 100 else divmod(value, 10)
        self._paint(str(tens), str(units), rolling=False)

    def _paint(self, tens: str, units: str, *, rolling: bool) -> None:
        v = self._view or {}
        left, right = art.die_face(tens), art.die_face(units)
        faces = "\n".join(f"  {a}  {b}" for a, b in zip(left, right, strict=True))
        head = f"{v.get('skill', '')} {v.get('target', '')}"
        if v.get("difficulty") and v.get("difficulty") != "常规":
            head += f" · {v['difficulty']}"
        if v.get("bonus"):
            head += f" · {'奖励' if v['bonus'] > 0 else '惩罚'}骰×{abs(v['bonus'])}"
        if v.get("pushed"):
            head = "孤注一掷 · " + head
        out = Text(head + "\n", style="bold")
        out.append(faces + "\n", style="dim" if rolling else "bold")
        if not rolling:
            level = v.get("level", "")
            out.append(f"  {v.get('value')}  ", style="bold")
            out.append(f" {level} ", style=LEVEL_STYLE.get(level, "bold"))
            cands = v.get("candidates") or []
            if len(cands) > 1:
                out.append(f"  ({' / '.join(map(str, cands))})", style="dim")
        self.update(out)


class SheetPanel(Static):
    async def on_click(self) -> None:
        await self.screen.run_action("sheet")  # the whole card (F7)

    def show(self, inv: Investigator, portrait: Text | None = None) -> None:
        out = Text()
        if portrait is not None and portrait.plain:
            out.append_text(portrait)
            out.append("\n")
        out.append(f"{inv.name}", style="bold")
        out.append(f"  {inv.occupation}  {inv.age}岁\n", style="dim")
        hp_style = "bold red" if inv.hp * 3 <= inv.hp_max else "bold"
        san_ratio = inv.san / max(1, inv.characteristics.get("意志", 50))
        san_style = "bold red" if san_ratio < 0.6 else "bold magenta" if san_ratio < 0.85 else "bold"
        out.append("HP  ")
        out.append(art.bar(inv.hp, inv.hp_max), style=hp_style)
        out.append(f" {inv.hp}/{inv.hp_max}\n")
        out.append("SAN ")
        out.append(art.bar(inv.san, inv.characteristics.get("意志", inv.san) or 1), style=san_style)
        out.append(f" {inv.san}/{inv.san_max}\n")
        out.append(f"MP {inv.mp}  幸运 {inv.luck}  MOV {inv.move}  DB {inv.damage_bonus}\n")
        if inv.conditions:
            out.append("状态：" + "、".join(inv.conditions) + "\n", style="bold red")
        top = sorted(inv.skills.items(), key=lambda kv: -kv[1])[:10]
        out.append("─" * 32 + "\n", style="dim")
        for i in range(0, len(top), 2):
            row = "".join(f"{k[:6]:　<6}{v:>3}  " for k, v in top[i : i + 2])
            out.append(row.rstrip() + "\n")
        self.update(out)


class CluePanel(Static):
    def show(self, titles: list[str], total: int, clock: str = "") -> None:
        out = Text()
        out.append(f"线索本  {len(titles)}/{total}", style="bold")
        out.append("   F2 展开\n", style="dim")
        for t in titles[-12:]:
            out.append("  〔", style="dim")
            out.append(t, style="bold")
            out.append("〕\n", style="dim")
        if not titles:
            out.append("  还什么都没有发现。\n", style="dim")
        self.update(out)
