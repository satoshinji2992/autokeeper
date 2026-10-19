"""Game-screen widgets: scene art, story log with a typewriter, dice tray, sheet, notebook."""

from __future__ import annotations

import asyncio
import random
import time
from functools import partial

from rich.cells import set_cell_size
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.containers import VerticalScroll
from textual.suggester import SuggestFromList
from textual.widgets import Static

from .. import artkit, atmosphere, imagery, weather
from ..module.schema import Module
from ..rules.catalog import CHARACTERISTICS, SKILL_BASE
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


def center(art_text: Text, width: int, covered: int = 0) -> Text:
    """Centre a block of art in the panel; with the right `covered` columns hidden, move it over
    just enough to stay clear of them."""
    lines = art_text.split("\n")
    pad = _pad(width, max((ln.cell_len for ln in lines), default=0), covered)
    return Text("\n").join(Text(" " * pad) + ln for ln in lines) if pad else art_text


def _pad(width: int, wide: int, covered: int = 0) -> int:
    pad = (width - wide) // 2
    return max(0, min(pad, width - covered - wide) if covered else pad)


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
        self.effects = True  # weather animation (the Ctrl+P menu switches it)
        self.madness = 0.0
        self._burst = 0
        self._art: Text | None = None  # the current art, ready to paint
        self._atmo: atmosphere.Atmosphere | None = None
        self._hold = 0.0  # weather waits until then (a glitch or a flicker owns the panel)
        self._fitted = (0, 0)  # the size the art was last drawn for (not _size: Textual owns that)
        self.covered = 0  # columns on the right that a speaker's portrait stands over (tui/speaker.py)

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

    def cover(self, cols: int) -> None:
        """A speaker's portrait stands over the right `cols` columns. Hand-drawn art moves over to
        stay in sight; a picture fills the panel and stays put, the portrait in front of it."""
        if cols == self.covered:
            return
        before, self.covered = self.covered, cols
        if self.picture and not self._uses_image():
            w, wide = self._fit()[0], max(map(len, self.picture.split("\n")))
            if _pad(w, wide, before) != _pad(w, wide, cols):  # only if the drawing has to move
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
            return center(artkit.colorize(art.corrupt(self.picture, rate), self.mask, when=when, grade=graded), w, self.covered)
        return Text()

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
        self.update(art_text)  # where and when are on the top bar

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


class ItemCard(Static):
    """A prop in the story; an illustration arriving later updates the same card."""

    DEFAULT_CSS = "ItemCard { border-left: thick $secondary; padding: 0 1; height: auto; }"

    def __init__(self, module: Module, event: dict, style: str):
        super().__init__()
        self.module, self.art_key, self.style_name = module, event.get("art") or "", style
        self.item_id = event["id"]
        self.title, self.description = event["name"], event["text"]

    def on_mount(self) -> None:
        self.redraw()

    def _show(self, picture: Text) -> None:
        self.update(Group(Text(self.title, style="bold"), picture, Text(self.description)))

    def redraw(self) -> None:
        self._show(artkit.colorize(self.module.art.get(self.art_key, ""), self.module.art_colors.get(self.art_key)))
        if self.art_key in self.module.pictures and imagery.available():
            self.run_worker(self._photo, group="item-picture", exclusive=True, exit_on_error=False)

    async def _photo(self) -> None:
        picture = await asyncio.to_thread(imagery.render, self.module.pictures[self.art_key], 42, 12, self.style_name)
        if self.is_attached:
            self._show(Text.from_ansi(picture))


class StoryLog(VerticalScroll):
    """Entries mount as static blocks; the keeper's narration types itself out."""

    MINE, OTHERS = "bold #ffd166", "bold #7fd1b9"  # your investigator's name, the others'
    MARKED = ("keeper", "npc", "performance", "echo", "opening")  # where names light up

    def __init__(self, **kw):
        super().__init__(**kw)
        self._live: Static | None = None
        self._shown = ""
        self._pending = ""
        self._typing = False
        self.names: dict[str, str] = {}  # a name, or the part of it people go by -> its style
        self.on_entry = None  # told of every block added: (kind, body, block); the history panel

    def mark(self, party, me: str | None) -> None:
        """Whose names to light up in the telling: yours in one colour, the party's in another.
        "贝蒂·康西丁" is also called 贝蒂."""
        names: dict[str, str] = {}
        for inv in party:
            style = self.MINE if inv.id == me else self.OTHERS
            for part in [inv.name, *inv.name.split("·")]:
                if len(part) >= 2:
                    names.setdefault(part, style)
        self.names = dict(sorted(names.items(), key=lambda kv: -len(kv[0])))  # the full name before its parts

    def lit(self, text: Text) -> Text:
        for name, style in self.names.items():
            text.highlight_words([name], style)
        return text

    def add(self, kind: str, body: Text | str | Group | Static, *, who: str = "") -> Static:
        if isinstance(body, Static):  # a block that draws itself, such as a roll
            block = body
            block.add_class("entry", kind)
        else:
            if isinstance(body, str):
                text = Text()
                if who:
                    text.append(f"{who}：", style="bold")
                text.append(body)
                body = text
            if kind in self.MARKED and isinstance(body, Text):
                body = self.lit(body.copy())
            block = Static(body, classes=f"entry {kind}")
        self.mount(block)
        self.scroll_end(animate=False)
        if self.on_entry is not None:
            self.on_entry(kind, body, block)
        return block

    # -- typewriter ----------------------------------------------------------------
    def begin_keeper(self, again: Static | None = None) -> Static:
        """A narration starts: a new block that types itself out, or `again`, the block of a
        narration being told again, whose old words fade until the new ones come in."""
        if again is not None and again.parent is self:
            again.styles.text_opacity = 0.45
            self._live = again
        else:
            self._live = self.add("keeper", Text(""))
        self._shown, self._pending = "", ""
        return self._live

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
        self._unfade(self._live)
        self._live.update(self.lit(Text(self._shown + "▌")))
        self.scroll_end(animate=False)
        self.set_timer(0.03, self._type)

    def end_keeper(self, full: str) -> None:
        if self._live is None:
            self._live = self.add("keeper", Text(""))
        self._pending = ""
        self._typing = False
        self._unfade(self._live)
        self._live.update(self.lit(Text(full)))
        self._live = None
        self.scroll_end(animate=False)

    @staticmethod
    def _unfade(block: Static) -> None:
        if block.styles.text_opacity < 1:
            block.styles.text_opacity = 1.0


PASS, MISS = "#3cb371", "#ff6347"


def roll_card(v: dict, *, spinning: bool = False, led_to: list[str] | tuple[str, ...] = ()) -> Text:
    """A check as the story shows it: who rolled what, the tens and units dice (with the
    extra bonus or penalty tens die, and which one counted), the number against the
    regular / hard / extreme thresholds, the level, and under it what the roll led to.
    `v` is a dice event (engine/game.py _check_view)."""
    target, value, bonus = int(v.get("target") or 0), int(v.get("value") or 0), int(v.get("bonus") or 0)
    need = v.get("difficulty") or "常规"
    out = Text("◈ ", style="bold")
    if v.get("pushed"):
        out.append("孤注一掷 ", style=f"bold {MISS}")
    out.append(f"{v.get('who', '')} · {v.get('skill', '')} {target}", style="bold")
    if need != "常规":
        out.append(f" · {need}", style="bold")
    if bonus:
        out.append(f" · {'奖励' if bonus > 0 else '惩罚'}骰×{abs(bonus)}", style="bold")
    tens = v.get("tens") or [value // 10 % 10 * 10]
    kept = int(v.get("kept") or 0)
    out.append("\n  十位", style="dim")
    for i, t in enumerate(tens):
        face = random.randrange(10) * 10 if spinning else t
        out.append(" ")
        out.append(f" {face:02d} ", style="dim" if spinning else "bold reverse" if i == kept else "dim strike")
    out.append("  个位 ", style="dim")
    units = random.randrange(10) if spinning else int(v.get("units", value % 10))
    out.append(f" {units} ", style="dim" if spinning else "bold reverse")
    if spinning:
        return out
    out.append(f"  = {value}", style="bold")
    if len(tens) > 1:
        out.append(f"（{'奖励骰取小的' if bonus > 0 else '惩罚骰取大的'}）", style="dim")
    out.append("\n ")
    for name, limit in (("常规", target), ("困难", v.get("hard", target // 2)), ("极难", v.get("extreme", target // 5))):
        ok = value <= limit
        out.append(f" {name}≤{limit} {'✓' if ok else '✗'}", style=f"{'bold underline ' if name == need else ''}{PASS if ok else MISS}")
    level = v.get("level", "")
    out.append("   ")
    out.append(f" {level} ", style=LEVEL_STYLE.get(level, "bold"))
    out.append(" 过了" if v.get("passed") else " 没过", style=f"bold {PASS if v.get('passed') else MISS}")
    for line in led_to:
        out.append(f"\n  ↳ {line}", style="bold")
    return out


class RollCard(Static):
    """One check in the story: the dice tumble for a moment (well under a second), settle
    on BCDice's result, and what the roll led to is written underneath once the engine
    knows. The LAN client draws the same card from the same event."""

    FRAMES = 9  # tumbling frames, 0.07 s each

    def __init__(self, dice: dict, **kw):
        super().__init__(roll_card(dice, spinning=True), **kw)
        self.dice = dice
        self.led_to: list[str] = []
        self._frames = self.FRAMES

    def on_mount(self) -> None:
        self._tumble()

    def _tumble(self) -> None:
        if self._frames > 0:
            self._frames -= 1
            self.update(roll_card(self.dice, spinning=True))
            self.set_timer(0.07, self._tumble)
            return
        self._settled()

    def settle(self, lines: list[str]) -> None:
        """Write what the roll led to under it (once the tumble is over, if it still runs)."""
        self.led_to += lines
        if self._frames <= 0:
            self._settled()

    def _settled(self) -> None:
        self.update(roll_card(self.dice, led_to=self.led_to))
        if isinstance(self.parent, StoryLog):  # the card grew: keep the newest lines in view
            self.parent.scroll_end(animate=False)


def command_suggester(inv: Investigator | None = None, actions: list[str] | None = None) -> SuggestFromList:
    """Completions for the command box, accepted with →: `.ra 侦` becomes `.ra 侦查`. The
    investigator's own skills come first, best first, then every other skill. `actions` are
    the player-facing hints of the active rule plugins' custom actions, so those extra things a
    player can try are discoverable here as well as in the Ctrl+K window."""
    mine = sorted(inv.skills, key=lambda k: -inv.skills[k]) if inv else []
    names = list(dict.fromkeys([*mine, *SKILL_BASE, *CHARACTERISTICS, "幸运", "灵感", "知识"]))
    checks = [f"{cmd} {name}" for cmd in (".ra", ".rab", ".rap", ".rc") for name in names]
    checks += [f".ra {level}{name}" for level in ("困难", "极难") for name in names]
    lookups = [".r 1d100", ".r 1d6", ".st", ".en", ".coc", ".ti", ".li"]
    slash = ["/draw", "/notes", "/map", "/sheet", "/suggest", "/recap", "/retell", "/retry", "/debrief", "/save", "/usage", "/prefs", "/help", "/theme", "/quit"]
    return SuggestFromList([*checks, *lookups, *slash, *(actions or [])], case_sensitive=False)


class DiceTray(Static):
    """Two spinning d10s that settle on BCDice's result."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.display = False  # nothing to show before the first roll
        self._frames = 0
        self._view: dict | None = None

    def roll(self, view: dict) -> None:
        self.display = True
        self._view = view
        self._frames = 12
        self._spin()

    def _spin(self) -> None:
        v = self._view or {}
        if self._frames > 0:
            self._frames -= 1
            self._paint(random.choice(art.DIGITS) + "0", random.choice(art.DIGITS), rolling=True)
            self.set_timer(0.06 + 0.012 * (12 - self._frames), self._spin)  # slowing down
            return
        value = int(v.get("value") or 0)
        tens, units = (0, 0) if value == 100 else divmod(value, 10)
        self._paint(f"{tens}0", str(units), rolling=False)

    def redraw(self) -> None:
        """The skin changed: the last roll again in the new one."""
        if self._view is not None and self._frames <= 0:
            self._spin()

    def _paint(self, tens: str, units: str, *, rolling: bool) -> None:
        from . import dice

        v = self._view or {}
        faces = dice.pair(tens, units, dice.skin(getattr(self.app, "dice_skin", None)), rolling=rolling, level=v.get("level", ""))
        head = f"{v.get('skill', '')} {v.get('target', '')}"
        if v.get("difficulty") and v.get("difficulty") != "常规":
            head += f" · {v['difficulty']}"
        if v.get("bonus"):
            head += f" · {'奖励' if v['bonus'] > 0 else '惩罚'}骰×{abs(v['bonus'])}"
        if v.get("pushed"):
            head = "孤注一掷 · " + head
        out = Text(head + "\n", style="bold")
        out.append_text(faces)
        out.append("\n")
        if not rolling:
            level = v.get("level", "")
            out.append(f"  {v.get('value')}  ", style="bold")
            out.append(f" {level} ", style=LEVEL_STYLE.get(level, "bold"))
            cands = v.get("candidates") or []
            if len(cands) > 1:
                out.append(f"  ({' / '.join(map(str, cands))})", style="dim")
        self.update(out)


PORTRAIT = (16, 8)  # the side column's thumbnail; the whole card (F4) has the big one
SKILLS_SHOWN = 10


class SheetPanel(Static):
    """The acting investigator at a glance: a small portrait beside the name and the bars,
    the best skills, and the rest of the party. A click opens the whole card (F4)."""

    async def on_click(self) -> None:
        await self.screen.run_action("sheet")

    def show(self, inv: Investigator, portrait: Text | None = None, party: list[Investigator] | None = None) -> None:
        width = max(24, self.content_size.width or 32)
        pictured = portrait is not None and bool(portrait.plain.strip())
        beside = width - (PORTRAIT[0] + 1 if pictured else 0)
        head = Text()
        head.append(set_cell_size(inv.name, beside).rstrip() + "\n", style="bold")
        head.append(set_cell_size(f"{inv.occupation.split('（')[0]}  {inv.age}岁", beside).rstrip() + "\n", style="dim")
        long = max(5, min(12, beside - 11))
        hp_style = "bold red" if inv.hp * 3 <= inv.hp_max else "bold"
        san_ratio = inv.san / max(1, inv.characteristics.get("意志", 50))
        san_style = "bold red" if san_ratio < 0.6 else "bold magenta" if san_ratio < 0.85 else "bold"
        head.append("HP  ")
        head.append(art.bar(inv.hp, inv.hp_max, long), style=hp_style)
        head.append(f" {inv.hp}/{inv.hp_max}\n")
        head.append("SAN ")
        head.append(art.bar(inv.san, inv.characteristics.get("意志", inv.san) or 1, long), style=san_style)
        head.append(f" {inv.san}\n")
        head.append(f"MP {inv.mp}  幸运 {inv.luck}\nMOV {inv.move}  DB {inv.damage_bonus}")
        if inv.conditions:
            head.append("\n" + "、".join(inv.conditions), style="bold red")
        parts: list = []
        if pictured:
            grid = Table.grid(padding=(0, 1))
            grid.add_column(width=PORTRAIT[0], no_wrap=True)
            grid.add_column(width=beside)
            grid.add_row(portrait, head)
            parts.append(grid)
        else:
            parts.append(head)
        parts.append(Text("─" * width, style="dim"))
        parts.append(skill_rows(inv, width))
        others = [p for p in party or [] if p.id != inv.id]
        if others:
            parts.append(Text("─" * width, style="dim"))
            parts.append(party_rows([inv, *others], inv.id, width))
        self.update(Group(*parts))


def skill_rows(inv: Investigator, width: int) -> Text:
    """The best skills in two columns, lined up by display width (CJK takes two cells)."""
    col = width // 2
    out = Text()
    top = sorted(inv.skills.items(), key=lambda kv: -kv[1])[:SKILLS_SHOWN]
    for i in range(0, len(top), 2):
        row = "".join(set_cell_size(k, col - 5) + f"{v:>3}  " for k, v in top[i : i + 2])
        out.append(row.rstrip() + ("\n" if i + 2 < len(top) else ""))
    return out


def party_rows(party: list[Investigator], acting: str, width: int) -> Text:
    out = Text()
    for n, p in enumerate(party):
        name = set_cell_size(p.name.split("（")[0], max(4, width - 18))
        style = "bold" if p.id == acting else "dim" if not p.can_act else ""
        out.append(("▸ " if p.id == acting else "  ") + name, style=style)
        out.append(f" HP{p.hp:>3} SAN{p.san:>3}", style="bold red" if p.hp * 3 <= p.hp_max else style)
        if n + 1 < len(party):
            out.append("\n")
    return out


class HistoryPanel(VerticalScroll):
    """What has happened, a line each: who did what, the checks and what they found, under
    the round it was in. Not the telling (that is in the story); a line clicked scrolls the
    story to it. Built from the blocks the story shows, so the LAN screen gets it too."""

    KEEP = 80  # lines kept; the story has the rest

    def __init__(self, **kw):
        super().__init__(**kw)
        self.round = -1

    def compose(self):
        yield Static(Text("点一行，故事跳到那里 · Ctrl+G 翻页", style="dim"), classes="history-head")

    def note(self, line: Text, block: Static | None = None, turn: int | None = None) -> None:
        """`turn`: the round the line belongs to (GameState.turn once it is played). Rounds only
        go up: a dice-bot roll (.r) after an action stays under that action's round."""
        if turn is not None and turn > self.round:
            self.round = turn
            self.mount(Static(Text(f"— 第 {turn} 轮 —", style="dim"), classes="history-round"))
        entry = HistoryLine(line, block)
        self.mount(entry)
        lines = list(self.query(HistoryLine))
        for old in lines[: max(0, len(lines) - self.KEEP)]:
            old.remove()
        self.scroll_end(animate=False)


class HistoryLine(Static):
    def __init__(self, line: Text, block: Static | None):
        super().__init__(line)
        self.block = block

    def on_click(self) -> None:
        if self.block is not None and self.block.parent is not None:
            self.block.parent.scroll_to_widget(self.block, animate=False, top=True)


def history_line(kind: str, body, block=None) -> Text | None:
    """A story block as one line of the history panel, or None when it is not an event worth
    a line (the narration, NPC talk, system notes)."""
    if kind == "dice" and isinstance(block, RollCard):
        d = block.dice
        return Text.assemble(("  ◈ ", "dim"), (f"{d.get('skill', '')} ", ""), (str(d.get("level", "")), PASS if d.get("passed") else MISS))
    plain = body.plain if isinstance(body, Text) else ""
    first = plain.strip().split("\n", 1)[0]
    if not first:
        return None
    if kind == "player":
        line = Text(first.removeprefix("▶ "), style="bold")
        line.truncate(120, overflow="ellipsis")
        return line
    if kind == "dice":
        return Text("  " + first, style="dim")
    if kind == "clue":
        return Text.assemble(("  ↳ ", "dim"), (first, "bold #e9c46a"))
    if kind in ("sanity", "handout", "ending") or (kind == "system" and first.startswith(("✚", "◎"))):
        return Text("  " + first, style="bold" if kind == "ending" else "")
    return None


def log_line(entry) -> Text | None:
    """A saved log entry (state.log, the LAN backlog) as a history line."""
    if entry.kind == "player":
        return Text(f"{entry.who}：{entry.text}"[:120], style="bold")
    if entry.kind == "dice":
        return Text(f"  ◈ {entry.text}", style="dim")
    if entry.kind == "clue":
        return Text.assemble(("  ↳ ", "dim"), (entry.text.split("〕", 1)[0] + "〕", "bold #e9c46a"))
    first = entry.text.split("\n", 1)[0]
    if entry.kind in ("sanity", "damage"):  # whose, as the live line has it
        return Text(f"  {'※' if entry.kind == 'sanity' else '✚'} {entry.who + ' ' if entry.who else ''}{first}")
    if entry.kind == "ending":
        return Text("  " + first)
    return None


class CluePanel(Static):
    """What has been found, newest last. How many there are to find is not the player's to know."""

    def show(self, titles: list[str]) -> None:
        out = Text()
        out.append(f"线索本 · {len(titles)} 条", style="bold")
        out.append("   F2 展开\n", style="dim")
        for t in titles[-12:]:
            out.append("〔", style="dim")
            out.append(t, style="bold")
            out.append("〕\n", style="dim")
        if not titles:
            out.append("还什么都没有发现。\n", style="dim")
        self.update(out)
