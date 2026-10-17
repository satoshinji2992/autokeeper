"""Game-screen widgets: scene art, story log with a typewriter, dice tray, sheet, notebook."""

from __future__ import annotations

import asyncio
import random

from rich.console import Group
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from .. import artkit, imagery
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


class SceneArt(Static):
    """Location name and its art: a real picture rendered by chafa to fit the panel, or the
    hand-drawn ASCII fallback. Glitches when sanity drops."""

    def __init__(self, **kw):
        super().__init__("", **kw)
        self.place = ""
        self.picture = ""
        self.mask: str | None = None
        self.image: str | None = None
        self.style_name = "picture"
        self.madness = 0.0
        self._burst = 0

    def on_mount(self) -> None:
        self.set_interval(1.6, self._idle_flicker)

    def on_resize(self) -> None:
        self._paint()

    def show(self, place: str, picture: str, mask: str | None = None, image: str | None = None) -> None:
        self.place, self.picture, self.mask, self.image = place, picture, mask, image
        self._paint()

    def _fit(self) -> tuple[int, int]:
        return max(24, (self.size.width or 72) - 2), 15

    def _uses_image(self) -> bool:
        return bool(self.image) and imagery.available()

    def _paint(self, rate: float = 0.0, frame: str | None = None) -> None:
        body = Text()
        body.append(f"◆ {self.place}\n", style="bold")
        if frame is not None:
            body.append_text(Text.from_ansi(frame))
        elif self._uses_image():
            body.append_text(Text.from_ansi(imagery.render(self.image, *self._fit(), self.style_name)))
        elif self.picture:
            body.append_text(artkit.colorize(art.corrupt(self.picture, rate), self.mask))
        self.update(body)

    def glitch(self, frames: int = 14) -> None:
        self.add_class("dread")
        if self._uses_image():
            self.run_worker(self._picture_glitch(min(8, max(3, frames // 3))), exclusive=True, group="glitch")
            return
        self._burst = frames
        self._tick()

    async def _picture_glitch(self, frames: int) -> None:
        w, h = self._fit()
        for i in range(frames):
            strength = 1.0 - i / frames
            frame = await asyncio.to_thread(imagery.glitch, self.image, w, h, strength, self.style_name)
            self._paint(frame=frame)
            await asyncio.sleep(0.05)
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
            self.run_worker(self._picture_glitch(2), exclusive=True, group="glitch")
            return
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
