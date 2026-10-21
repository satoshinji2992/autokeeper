"""A fight and a chase on the stage (engine/fighting.py runs them; these only show them).

The scene stays where it is, graded and moving with what is happening (SceneArt.set_mode: a
fight's blood-red cast with blood running down it and a heartbeat that darkens it, a chase's cold
with the world streaking past). Over it, the way the speaker's portrait stands over it:

- FightStage, on the right: the round and who acts in what order (the one acting lit), a card for
  every foe (their portrait, painted by program for a creature that has none: scenery.creature;
  how hurt they look in words, never their numbers; what they hold), and under them the party's
  hit points. A blow makes a card flinch and flash; one who falls goes dark, a cross on them.
- ChaseStage, along the bottom: the way as a strip of film, a small painting of every stretch of it,
  what is in the way on each (a hazard in amber, a barrier in red, an opened one hollow), who is
  where (the investigators as diamonds, the pursuers as a pair of eyes that do not keep still), and
  the dark ahead that nobody has reached yet.

The story keeps the record in cards (fight_card, chase_card), the dice the investigators threw go
to the tray (the screen's), and the screen shakes and flashes when an investigator is hurt."""

from __future__ import annotations

import asyncio
from functools import lru_cache

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Static

from ... import imagery, scenery
from ...module.schema import Module
from ...rules.combat import STANCES

BLOOD, DEEP, EMBER, AMBER, BONE, ASH, BILE = "#c1121f", "#7a0a0a", "#ff6b35", "#ffb000", "#e8dec4", "#8a7f72", "#9bd770"
WOUND = {"无伤": 4, "轻伤": 3, "受伤": 2, "重伤": 1}  # segments of a foe's meter, in words
DOWN = ("昏迷", "濒死", "死亡", "倒下")
GONE = ("逃走", "投降")
FACE_ROWS = (4, 9)  # a foe's portrait: least and most rows
LEG_ROWS = 4  # a stretch of the chase's way: the rows of its painting


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
    return Text.assemble(("▰" * filled, f"bold {colour}"), ("▱" * (width - filled), DEEP))


@lru_cache(maxsize=96)
def _face(path: str, cols: int, rows: int, style: str) -> Text:
    """A portrait in the card's size (chafa, so off the UI thread), in its own colours: a creature's
    sick light is part of it."""
    try:
        return Text.from_ansi(imagery.render(path, cols, rows, style, face=True))
    except (OSError, ValueError):
        return Text()


def _faceless(rows: int, cols: int, creature: bool) -> Text:
    """No picture to show: a shape in the dark, two eyes."""
    out = Text()
    for y in range(rows):
        if y == rows // 3:
            gap = max(1, cols // 4)
            line = " " * max(0, (cols - gap * 2 - 2) // 2) + "●" + " " * gap * 2 + "●"
            out.append(line.ljust(cols) + "\n", style=f"bold {BILE if creature else AMBER}")
        else:
            shade = "▓" if y > rows // 2 else "░"
            width = max(2, int(cols * (0.35 + 0.5 * y / max(1, rows))))
            out.append((" " * ((cols - width) // 2) + shade * width).ljust(cols) + ("\n" if y < rows - 1 else ""), style=DEEP)
    return out


class FoeCard(Horizontal):
    DEFAULT_CSS = f"""
    FoeCard {{ height: auto; min-height: 4; border: round #3a0a0a; padding: 0 0; margin: 0 0 0 0; }}
    FoeCard.-acting {{ border: round {BLOOD}; }}
    FoeCard.-struck {{ background: #4a0808; border: heavy {BLOOD}; }}
    FoeCard.-down {{ opacity: 0.45; }}
    FoeCard .face {{ width: auto; height: auto; }}
    FoeCard .info {{ width: 1fr; height: auto; padding: 0 1; }}
    """

    def __init__(self, fid: str, **kw):
        super().__init__(**kw)
        self.fid = fid
        self.face = Static(classes="face")
        self.info = Static(classes="info")
        self._shake: Timer | None = None
        self._drawn: tuple = ()

    def compose(self) -> ComposeResult:
        yield self.face
        yield self.info

    def show(self, f: dict, m: Module, rows: int, style: str) -> None:
        name = Text(f["name"], style=f"bold {BONE}")
        if f.get("prone"):
            name.append(" 倒地", style=EMBER)
        if f.get("held"):
            name.append(" 被制住", style=EMBER)
        lines = [name, wound_meter(f["wound"], f["status"]),
                 Text(f"{f['weapon']} · {STANCES.get(f['stance'], '')}", style=ASH)]
        if f.get("look"):
            lines.append(Text(f["look"], style=f"italic {ASH}", overflow="ellipsis", no_wrap=True))
        self.info.update(Text("\n").join(lines))
        self.set_class(f["status"] in DOWN or f["status"] in GONE, "-down")
        cols = max(6, min(18, rows * 2))
        key = (f.get("portrait", ""), rows, cols, style)
        if key != self._drawn:
            self._drawn = key
            path = m.pictures.get(f.get("portrait", "") or "", "")
            if path and imagery.available():
                self.run_worker(self._paint(path, cols, rows, style), exclusive=True, group="face")
            else:
                art = m.art.get(f.get("portrait", "") or "")
                self.face.update(Text(art, style=ASH) if art else _faceless(rows, cols, f["kind"] == "creature"))

    async def _paint(self, path: str, cols: int, rows: int, style: str) -> None:
        drawn = await asyncio.to_thread(_face, path, cols, rows, style)
        if self.is_attached:
            self.face.update(drawn)

    def struck(self) -> None:
        """A blow landed: a flash and a flinch, over in a third of a second."""
        self.add_class("-struck")
        steps = iter([(1, 0), (-1, 0), (1, 0), (0, 0)])

        def step() -> None:
            try:
                self.styles.offset = next(steps)
            except StopIteration:
                self.remove_class("-struck")
                if self._shake is not None:
                    self._shake.stop()

        self._shake = self.set_interval(0.07, step)


class FightStage(Vertical):
    """The fight over the right of the stage."""

    DEFAULT_CSS = f"""
    FightStage {{
        layer: speaker;
        dock: right;
        height: 100%;
        width: 48;
        display: none;
        background: #0b0505;
        border-left: thick {DEEP};
        padding: 0 1;
    }}
    FightStage .head {{ height: auto; }}
    FightStage .foes {{ height: 1fr; }}
    FightStage .party {{ height: auto; border-top: dashed #5c0606; }}
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.view: dict | None = None
        self.order: list[str] = []
        self.acting = ""
        self.m: Module | None = None
        self.style = "photo"

    def compose(self) -> ComposeResult:
        yield Static(classes="head")
        yield Vertical(classes="foes")
        yield Static(classes="party")

    def show(self, view: dict, m: Module, style: str = "photo") -> None:
        """The fight as it stands (engine/combat.view)."""
        self.view, self.m, self.style = view, m, style
        self.display = True
        if self.parent is not None and (room := self.parent.size.width):  # two fifths of the stage, the scene keeps the rest
            self.styles.width = max(34, min(54, room * 2 // 5))
        foes = [f for f in view["fighters"] if f["side"] != "party"]
        box = self.query_one(".foes", Vertical)
        cards = {c.fid: c for c in box.query(FoeCard)}
        for fid in [k for k in cards if k not in {f["id"] for f in foes}]:
            cards.pop(fid).remove()
        rows = max(FACE_ROWS[0], min(FACE_ROWS[1], (max(8, self.size.height or 20) - 6) // max(1, len(foes)) - 2))
        for f in foes:
            card = cards.get(f["id"])
            if card is None:
                card = FoeCard(f["id"])
                box.mount(card)
                cards[f["id"]] = card
            card.set_class(f["id"] == self.acting, "-acting")
            card.call_after_refresh(card.show, f, m, rows, style) if not card.is_mounted else card.show(f, m, rows, style)
        self._head()
        self._party()

    def _head(self) -> None:
        view = self.view or {}
        names = {f["id"]: f["name"] for f in view.get("fighters", [])}
        head = Text()
        head.append("⚔ 战斗", style=f"bold {BLOOD}")
        head.append(f" · 第 {max(1, view.get('round', 1))} 轮\n", style=f"bold {BONE}")
        chain = Text("先后 ", style=ASH)
        for i, fid in enumerate(self.order or [f["id"] for f in view.get("fighters", [])]):
            if i:
                chain.append(" ▸ ", style=DEEP)
            side = next((f["side"] for f in view.get("fighters", []) if f["id"] == fid), "foe")
            colour = AMBER if side == "party" else BLOOD
            chain.append(names.get(fid, fid), style=f"bold reverse {colour}" if fid == self.acting else colour)
        head.append_text(chain)
        self.query_one(".head", Static).update(head)

    def _party(self) -> None:
        out = Text()
        for f in (self.view or {}).get("fighters", []):
            if f["side"] != "party":
                continue
            if out.plain:
                out.append("\n")
            out.append(f"{f['name'][:6]:<6}", style=f"bold {AMBER}" if f["id"] != self.acting else f"bold reverse {AMBER}")
            out.append(" ")
            if f["hp"] is not None:
                out.append_text(hp_bar(f["hp"], f["hp_max"]))
                out.append(f" {f['hp']}/{f['hp_max']}", style=BONE)
            tag = f["status"] if f["status"] else STANCES.get(f["stance"], "")
            out.append(f"  {tag}", style=f"bold {BLOOD}" if f["status"] else ASH)
        self.query_one(".party", Static).update(out)

    def round_begins(self, order: list[str], view: dict) -> None:
        self.order, self.acting = order, ""
        if self.m is not None:
            self.show(view, self.m, self.style)

    def act(self, ev: dict) -> None:
        """An exchange: who acts lit, the one hurt flinches."""
        self.acting = ev.get("attacker", "")
        self._head()
        self._party()
        for card in self.query(FoeCard):
            card.set_class(card.fid == self.acting, "-acting")
            if card.fid == ev.get("hurt") and ev.get("damage"):
                card.struck()

    def hide(self) -> None:
        self.display = False
        self.view, self.order, self.acting = None, [], ""
        for card in self.query(FoeCard):
            card.remove()


def cell_text(text: str, width: int, style: str = "") -> Text:
    """`text` in exactly `width` cells (a Chinese character takes two): cut short or padded."""
    out = Text(text, style=style, no_wrap=True)
    out.truncate(width, overflow="crop", pad=True)
    return out


# -- the chase ---------------------------------------------------------------------------------
@lru_cache(maxsize=64)
def _leg(name: str, look: str, cols: int, rows: int, style: str, when: str = "") -> Text:
    """A small painting of a stretch of the way (scenery, by its name), lit for the hour."""
    try:
        from ... import paths

        key = "leg-" + "".join(f"{ord(c):x}" for c in name)[:40]
        path = scenery.picture(paths.scenery() / "chase", key, name, look)
        return Text.from_ansi(imagery.render(str(path), cols, rows, style, when, fill=True))
    except (OSError, ValueError):
        return Text()


class ChaseStage(Static):
    """The chase along the bottom of the stage."""

    DEFAULT_CSS = f"""
    ChaseStage {{
        layer: speaker;
        dock: bottom;
        height: auto;
        display: none;
        background: #07080c;
        border-top: thick #2a3448;
        padding: 0 1;
    }}
    ChaseStage.-close {{ border-top: thick {BLOOD}; }}
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.view: dict | None = None
        self.style = "photo"
        self.when = ""
        self._paintings: dict[tuple, Text] = {}
        self._blink = 0

    def on_mount(self) -> None:
        self.set_interval(0.45, self._flicker)

    def show(self, view: dict, style: str = "photo", when: str = "") -> None:
        self.view, self.style, self.when = view, style, when
        self.display = True
        self._draw()
        self.run_worker(self._paint_legs(), exclusive=True, group="legs")

    def hide(self) -> None:
        self.display = False
        self.view = None

    def _window(self) -> tuple[int, int, int]:
        """Which stretches show (from, to) and how wide each is: from the hindmost runner to two past the
        foremost, as many as fit."""
        view = self.view or {}
        legs = view.get("legs", [])
        runners = [r for r in view.get("runners", []) if r["status"] not in DOWN]
        lo = max(0, min((r["pos"] for r in runners), default=0) - 1)
        hi = min(len(legs), max((r["pos"] for r in runners), default=0) + 3)
        width = max(30, (self.size.width or 100) - 2)
        cell = max(10, min(20, width // max(1, hi - lo)))
        while (hi - lo) * cell > width and hi - lo > 2:
            hi -= 1
        return lo, hi, cell

    async def _paint_legs(self) -> None:
        view = self.view or {}
        lo, hi, cell = self._window()
        for i in range(lo, hi):
            leg = view["legs"][i] if i < len(view["legs"]) else None
            if leg is None or not imagery.available():
                continue
            key = (leg["name"], cell, self.style)
            if key not in self._paintings:
                self._paintings[key] = await asyncio.to_thread(_leg, leg["name"], leg.get("look", ""), cell - 1, LEG_ROWS,
                                                               self.style, self.when)
                if self.is_attached:
                    self._draw()

    def _flicker(self) -> None:
        if self.display and self.view is not None:
            self._blink += 1
            self._draw()

    def _draw(self) -> None:
        view = self.view
        if view is None:
            return
        lo, hi, width = self._window()
        legs, runners = view["legs"], view["runners"]
        quarry = view.get("quarry", "party")
        ahead = max((r["pos"] for r in runners if r["side"] == quarry), default=0)
        behind = [r for r in runners if r["side"] != quarry and r["status"] not in DOWN]
        gap = min((ahead - r["pos"] for r in behind), default=9)
        head = Text()
        head.append("⟫ 追逐", style=f"bold {'#9fb4c8' if gap > 1 else BLOOD}")
        head.append(f" · 第 {max(1, view.get('round', 1))} 轮", style=f"bold {BONE}")
        if behind:
            near = "就在身后" if gap <= 0 else f"相隔 {gap} 段"
            who = "、".join(r["name"] for r in behind) if quarry == "party" else "你们"
            head.append(f"   {who}：{near}", style=f"bold {BLOOD}" if gap <= 1 else ASH)
        self.set_class(gap <= 1, "-close")
        tops, names, marks = Text(), Text(), Text()
        paintings: list[list[Text]] = [[] for _ in range(LEG_ROWS)]
        for i in range(lo, hi):
            leg = legs[i] if i < len(legs) else None
            dark = i > ahead + 2 or leg is None
            ob = (leg or {}).get("obstacle") or None
            if dark:
                tops.append_text(cell_text("", width))
            elif ob:
                icon = "▢" if ob.get("open") else "▦" if ob["kind"] == "barrier" else "⚠"
                colour = ASH if ob.get("open") else BLOOD if ob["kind"] == "barrier" else AMBER
                tops.append_text(cell_text(f"{icon}{ob['what']}", width - 1, f"bold {colour}"))
                tops.append(" ")
            else:
                tops.append_text(cell_text("", width))
            picture = self._paintings.get(((leg or {}).get("name", ""), width, self.style)) if not dark else None
            rows = picture.split("\n") if picture is not None else []
            for r in range(LEG_ROWS):
                if dark:
                    paintings[r].append(Text("░" * (width - 1) + " ", style="#1a1a22"))
                elif r < len(rows) and rows[r].cell_len:
                    piece = rows[r].copy()
                    piece.truncate(width - 1, pad=True)
                    paintings[r].append(Text.assemble(piece, " "))
                else:
                    paintings[r].append(Text("▁" * (width - 1) + " ", style="#2a3448"))
            names.append_text(cell_text("？" if dark else (leg or {}).get("name", ""), width - 1, ASH if not dark else DEEP))
            names.append(" ")
            here = [r for r in runners if r["pos"] == i and r["status"] not in GONE]
            mark = Text()
            for r in here:
                if r["side"] == quarry and r.get("hidden"):
                    mark.append("… ", style=ASH)
                elif r["side"] == "party":
                    mark.append(f"◆{r['name'][:3]} ", style=f"bold {AMBER}")
                else:
                    eyes = "◉◉" if (self._blink + i) % 3 else "◎◎"
                    mark.append(f"{eyes}{r['name'][:2]} ", style=f"bold {BLOOD}")
            mark.truncate(width, pad=True)
            marks.append_text(mark)
        out = Text("\n").join([head, tops, *(Text("").join(row) for row in paintings), names, marks])
        self.update(out)


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
        out.append("被反击 · " if back else "命中 · ", style=f"bold {BLOOD}")
        out.append(ev.get("damage_text", ""), style=f"bold {BONE}")
        if ev.get("extreme"):
            out.append("  极难成功", style=f"bold {EMBER}")
        notes = [n for n in ev.get("notes", []) if n]
        if notes:
            out.append("  " + "、".join(notes), style=f"bold {BLOOD}")
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
        out.append(line, style=BONE if "追上" not in line and "栽" not in line else f"bold {BLOOD}")
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
        frames.append(Text.assemble((bar + "  ", DEEP), (line, f"bold {BLOOD}"), ("  " + bar, DEEP), justify="center"))
    return frames


def pulse_rate(hp: int, top: int) -> float:
    """Seconds between heartbeats for someone at `hp` of `top`: quicker the worse it is."""
    share = hp / max(1, top)
    return 0.35 + 0.65 * max(0.0, min(1.0, share)) ** 0.7


__all__ = ["ChaseStage", "FightStage", "banner", "chase_lines", "fight_card", "pulse_rate", "title_frames"]
