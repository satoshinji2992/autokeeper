"""What rule plugins put on screen: their panels in the right column, short shows over the
scene or in the story, and the questions and little games they ask a player.

A plugin never draws anything itself. It hands the host data (plugins/schema.py: Panel, a
show's frames, a prompt) and these widgets draw it, so a plugin is free in what it shows and
never in what it can reach. Everything is labelled with the plugin's name, small; Ctrl+K says
what each one is.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, SelectionList, Static

if TYPE_CHECKING:
    from .app import GameScreen

LABEL = "#8ec7d6"  # the plugins' own mark, as on their lines in the story
SKIP, CLOSED = "__skip__", "__closed__"  # the player passed; the host closed the prompt meanwhile
BAR = 12  # cells of a panel's bar
STAGE_HOLD = 2.5  # seconds a stage show's only frame stays up when animations are off


def colour(name: str, theme: Any = None) -> str:
    """A plugin's colour as Rich takes it: a theme colour by name, or its own hex."""
    if not name:
        return ""
    if name == "dim":
        return "dim"
    if name.startswith("#"):
        return name
    value = getattr(theme, name, None) if theme is not None else None
    return str(value) if value else ""


# -- panels ------------------------------------------------------------------------------------
def tab_label(panels: dict[str, list[dict]]) -> str:
    """The page's tab: the panel's own title when there is one panel, else 更多."""
    every = [p for group in panels.values() for p in group]
    return every[0]["title"] if len(every) == 1 else "更多"


def panel_view(panels: dict[str, list[dict]], theme: Any = None) -> RenderableType:
    """Every plugin's panels, one section each, with the plugin's name over it."""
    parts: list[RenderableType] = []
    for name, group in panels.items():
        if parts:
            parts.append(Text(""))
        parts.append(Text(f"〔{name}〕", style=f"dim {LABEL}"))
        for panel in group:
            parts.append(Text(panel.get("title", ""), style="bold"))
            parts += [_row(row, theme) for row in panel.get("rows", [])]
    return Group(*parts) if parts else Text("")


def _row(row: dict, theme: Any) -> RenderableType:
    kind, text, style = row.get("kind", "text"), row.get("text", ""), colour(row.get("color", ""), theme)
    if kind == "bar":
        top = max(1, int(row.get("max", 100)))
        value = max(0, min(top, int(row.get("value", 0))))
        filled = round(BAR * value / top)
        line = Text()
        if text:
            line.append(f"{text} ")
        line.append("█" * filled, style=style or "bold")
        line.append("░" * (BAR - filled), style="dim")
        line.append(f" {row.get('value', 0)}/{top}")
        return line
    if kind == "list":
        out = Text()
        if text:
            out.append(f"{text}\n", style="bold")
        out.append("\n".join(f"· {item}" for item in row.get("items", [])), style=style)
        return out
    if kind == "table":
        cells = row.get("cells", [])
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1), title=text or None, title_justify="left",
                      header_style="bold", style=style or "")
        width = max((len(r) for r in cells), default=0)
        for head in (cells[0] if cells else []) + [""] * (width - len(cells[0]) if cells else 0):
            table.add_column(head)
        for r in cells[1:]:
            table.add_row(*(r + [""] * (width - len(r))))
        return table
    if kind == "art":
        out = Text()
        if text:
            out.append(f"{text}\n", style="bold")
        out.append(row.get("art", ""), style=style)
        return out
    return Text(text, style=style)


# -- shows -------------------------------------------------------------------------------------
class ShowPlayer(Static):
    """A plugin's short show: its frames one after another, over the scene (stage) or as a
    block in the story (inline). Click to skip; with animations off only the last frame shows."""

    DEFAULT_CSS = """
    ShowPlayer {
        height: auto;
        padding: 0 1;
    }
    ShowPlayer.stage {
        background: $background;
        content-align: center middle;
    }
    ShowPlayer.inline {
        border-left: thick #8ec7d6;
    }
    """

    def __init__(self, ev: dict, *, animate: bool, stage: bool, theme: Any = None, on_done=None, rows: int = 0):
        super().__init__(classes="stage" if stage else "inline")
        self.ev, self.animate, self.stage, self.theme = ev, animate, stage, theme
        self.frames = ev.get("frames", []) or [{"text": ev.get("title", ""), "ms": 1500}]
        self.index = 0 if animate else len(self.frames) - 1
        self.on_done, self.done = on_done, False
        self._timer = None
        if stage and rows:
            self.styles.height = rows

    def on_mount(self) -> None:
        self._paint()
        if self.animate and len(self.frames) > 1:
            self._timer = self.set_timer(self._ms(self.index), self._next)
        elif self.stage:  # one frame: up for its time (a short while with animations off), then gone
            self._timer = self.set_timer(self._ms(self.index) if self.animate else STAGE_HOLD, self.finish)
        else:
            self.done = True

    def _ms(self, i: int) -> float:
        return max(0.2, int(self.frames[i].get("ms", 1500)) / 1000)

    def _next(self) -> None:
        if self.index < len(self.frames) - 1:
            self.index += 1
            self._paint()
            self._timer = self.set_timer(self._ms(self.index), self._next if self.index < len(self.frames) - 1 else self._last)
        else:
            self._last()

    def _last(self) -> None:
        if self.stage:
            self.finish()
        else:
            self.done = True

    def _paint(self) -> None:
        frame = self.frames[self.index]
        style = colour(frame.get("color", ""), self.theme)
        body = Text(justify="center" if self.stage else "left")
        if not self.stage:
            body.append(f"⚙ 〔{self.ev.get('name', '')}〕", style=f"bold {LABEL}")
            body.append(f"{self.ev.get('title', '')}\n", style="bold")
        if frame.get("art"):
            body.append(frame["art"].rstrip("\n") + "\n", style=style or "bold")
        if frame.get("text"):
            body.append(frame["text"], style=style if not frame.get("art") else "")
        if self.stage:
            body.append(f"\n\n〔{self.ev.get('name', '')}〕{self.ev.get('title', '')} · 点一下或按 Esc 跳过", style=f"dim {LABEL}")
        self.update(body)

    def skip(self) -> None:
        """Straight to the end: the last frame stays in the story; over the scene, the show goes."""
        if self.done:
            return
        if self._timer is not None:
            self._timer.stop()
        self.index = len(self.frames) - 1
        self._paint()
        if self.stage:
            self.finish()
        else:
            self.done = True

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        if self._timer is not None:
            self._timer.stop()
        if self.on_done:
            self.on_done(self)

    def on_click(self) -> None:
        self.skip()


# -- prompts -----------------------------------------------------------------------------------
class PromptModal(ModalScreen[Any]):
    """A plugin's question or little game for this player: buttons, a short answer, a number,
    putting things in order, picking some, or a board of cells to pick from. 先不玩 passes;
    the answer goes back to the plugin as its next step."""

    BINDINGS = [("escape", "skip", "先不玩")]
    DEFAULT_CSS = """
    PromptModal .cells {
        height: auto;
        align: center middle;
    }
    PromptModal .cell {
        min-width: 6;
        height: 3;
        margin: 0 0;
    }
    PromptModal .choices {
        height: auto;
        align: center middle;
    }
    PromptModal .choices Button {
        margin: 0 1 1 0;
    }
    PromptModal #prompt-left {
        color: $text-muted;
        margin-top: 1;
    }
    PromptModal SelectionList {
        height: auto;
        max-height: 12;
    }
    """

    def __init__(self, ev: dict):
        super().__init__()
        self.ev, self.pid = ev, ev["id"]
        self.ends = time.monotonic() + max(1, int(ev.get("timeout", 180)))
        self.order: list[str] = []

    def compose(self) -> ComposeResult:
        ev, style = self.ev, self.ev.get("style", "choice")
        with Vertical(classes="modal", id="prompt-modal"):
            head = Text()
            head.append(f"〔{ev.get('name', '')}〕 ", style=f"dim {LABEL}")
            head.append(ev.get("title") or "", style="bold")
            yield Static(head, classes="modal-title")
            yield Static(Text(ev.get("text", "")), classes="modal-body")
            with VerticalScroll(classes="modal-body"):
                if style == "choice":
                    with Horizontal(classes="choices"):
                        for i, option in enumerate(ev.get("options", [])):
                            yield Button(option, id=f"opt-{i}", variant="primary" if i == 0 else "default")
                elif style in ("text", "number"):
                    hint = f"{ev.get('low', 0)} 到 {ev.get('high', 9)} 之间的数" if style == "number" else "写下你的回答，回车确定"
                    yield Input(placeholder=hint, id="prompt-input", type="integer" if style == "number" else "text",
                                max_length=80)
                elif style == "pick":
                    yield SelectionList[int](*[(o, i) for i, o in enumerate(ev.get("options", []))], id="prompt-pick")
                elif style == "order":
                    yield Static(self._order_text(), id="prompt-order")
                    with Horizontal(classes="choices"):
                        for i, option in enumerate(ev.get("options", [])):
                            yield Button(option, id=f"ord-{i}")
                elif style == "board":
                    for r, row in enumerate(ev.get("board", [])):
                        with Horizontal(classes="cells"):
                            for c, cell in enumerate(row):
                                yield Button(cell or "·", id=f"cell-{r}-{c}", classes="cell")
            with Horizontal(classes="buttons"):
                if style in ("text", "number", "pick", "order"):
                    yield Button("确定", id="prompt-ok", variant="primary")
                if style == "order":
                    yield Button("重排", id="prompt-reset")
                yield Button("先不玩", id="prompt-skip")
            yield Static("", id="prompt-left")

    def on_mount(self) -> None:
        self._tick()
        self.set_interval(1.0, self._tick)
        first = next(iter(self.query("#prompt-input").results(Input)), None) or next(iter(self.query(Button)), None)
        if first is not None:
            first.focus()

    def _tick(self) -> None:
        left = max(0, int(self.ends - time.monotonic()))
        self.query_one("#prompt-left", Static).update(f"还有 {left // 60}:{left % 60:02d}，没答就按默认算")

    def _order_text(self) -> Text:
        if not self.order:
            return Text("依次点下面的按钮，排出顺序", style="dim")
        return Text(" → ".join(self.order), style="bold")

    def action_skip(self) -> None:
        self.dismiss(SKIP)

    @on(Input.Submitted, "#prompt-input")
    def typed(self) -> None:
        self._ok()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        bid, ev = event.button.id or "", self.ev
        if bid == "prompt-skip":
            self.dismiss(SKIP)
        elif bid == "prompt-ok":
            self._ok()
        elif bid == "prompt-reset":
            self.order = []
            for button in self.query(Button):
                if (button.id or "").startswith("ord-"):
                    button.disabled = False
            self.query_one("#prompt-order", Static).update(self._order_text())
        elif bid.startswith("opt-"):
            self.dismiss(ev["options"][int(bid[4:])])
        elif bid.startswith("ord-"):
            self.order.append(ev["options"][int(bid[4:])])
            event.button.disabled = True
            self.query_one("#prompt-order", Static).update(self._order_text())
            if len(self.order) == len(ev["options"]):
                self.dismiss(list(self.order))
        elif bid.startswith("cell-"):
            _, r, c = bid.split("-")
            self.dismiss([int(r), int(c)])

    def _ok(self) -> None:
        style, ev = self.ev.get("style"), self.ev
        if style == "text":
            self.dismiss(self.query_one("#prompt-input", Input).value.strip()[:80])
        elif style == "number":
            raw = self.query_one("#prompt-input", Input).value.strip()
            try:
                value = int(raw)
            except ValueError:
                self.app.notify("填一个整数", severity="warning")
                return
            if not ev.get("low", 0) <= value <= ev.get("high", 9):
                self.app.notify(f"要在 {ev.get('low', 0)} 到 {ev.get('high', 9)} 之间", severity="warning")
                return
            self.dismiss(value)
        elif style == "pick":
            chosen = self.query_one("#prompt-pick", SelectionList).selected
            self.dismiss([ev["options"][i] for i in sorted(chosen)])
        elif style == "order":
            left = [o for o in ev["options"] if o not in self.order]
            self.dismiss([*self.order, *left])


# -- the game screen's side of it -------------------------------------------------------------
class Plays:
    """Shows and prompts for one game screen. Prompts for this player wait until the turn on
    the screen is over, then come up one at a time; their answers go through the screen
    (GameScreen.plugin_answer: the game here, the hub at a shared table)."""

    def __init__(self, screen: GameScreen):
        self.screen = screen
        self.waiting: list[dict] = []
        self.asking: PromptModal | None = None
        self.stage: ShowPlayer | None = None

    @property
    def theme(self) -> Any:
        try:
            return self.screen.app.current_theme
        except Exception:  # no app yet (tests that build pieces by hand)
            return None

    def handle(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "plugin_show":
            self.show(ev)
        elif t == "plugin_prompt":
            if self.screen.prompt_is_mine(ev.get("who", "")):
                self.waiting = [w for w in self.waiting if w["id"] != ev["id"]] + [ev]
                line = Text()
                line.append(f"⚙ 〔{ev.get('name', '')}〕", style=f"bold {LABEL}")
                line.append(ev.get("title") or "有件事要你来定", style="bold")
                self.screen.story.add("system", line)
                self.ready()
        elif t == "plugin_closed":
            self.waiting = [w for w in self.waiting if w["id"] != ev.get("id")]
            if self.asking is not None and self.asking.pid == ev.get("id"):
                self.asking.dismiss(CLOSED)
        elif t == "plugin_answered":
            how = {"ai": "（AI）选了", "timeout": "没来得及答，按默认", "skip": "先不玩了，按默认"}.get(ev.get("by", ""), "答")
            line = Text()
            line.append(f"⚙ 〔{ev.get('name', '')}〕", style=f"bold {LABEL}")
            line.append(f"{ev.get('who', '')}{how}：{ev.get('text', '')}", style="dim")
            self.screen.story.add("system", line)

    # -- shows
    def show(self, ev: dict) -> None:
        screen = self.screen
        animate = bool(screen.query_one("#scene").effects)  # the animations setting, as the weather keeps it
        if ev.get("shape") != "stage":
            screen.story.add("event", ShowPlayer(ev, animate=animate, stage=False, theme=self.theme))
            return
        self.end_stage()
        scene, stage = screen.query_one("#scene"), screen.query_one("#stage")
        rows = max(9, scene.size.height)
        player = ShowPlayer(ev, animate=animate, stage=True, theme=self.theme, on_done=self._stage_done, rows=rows)
        self.stage = player
        scene.display = False
        stage.mount(player, before=scene)
        line = Text()
        line.append(f"⚙ 〔{ev.get('name', '')}〕", style=f"bold {LABEL}")
        line.append(ev.get("title") or "演出")
        screen.story.add("system", line)

    def _stage_done(self, player: ShowPlayer) -> None:
        if self.stage is player:
            self.stage = None
        if player.is_attached:
            player.remove()
        if self.stage is None:
            for scene in self.screen.query("#scene"):
                scene.display = True

    def end_stage(self) -> None:
        if self.stage is not None:
            self.stage.finish()

    def skip(self) -> bool:
        """Esc: a show still playing ends (over the scene) or jumps to its last frame (in the story)."""
        playing = [p for p in self.screen.query(ShowPlayer) if not p.done]
        for p in playing:
            p.skip()
        return bool(playing)

    # -- prompts
    def ready(self) -> None:
        """Bring up the next prompt for this player, when nothing else is on."""
        screen = self.screen
        if self.asking is not None or not self.waiting or screen.busy or not screen.is_current:
            return
        ev = self.waiting.pop(0)
        self.asking = PromptModal(ev)
        screen.app.push_screen(self.asking, lambda value, ev=ev: self._answered(ev, value))

    def _answered(self, ev: dict, value: Any) -> None:
        self.asking = None
        if value == CLOSED:
            self.screen.call_later(self.ready)
            return
        if value == SKIP:
            self.screen.plugin_answer(ev["id"], None, "skip")
        else:
            line = Text()
            line.append(f"⚙ 〔{ev.get('name', '')}〕", style=f"bold {LABEL}")
            shown = f"第 {value[0] + 1} 行第 {value[1] + 1} 格" if ev.get("style") == "board" else (
                "、".join(map(str, value)) if isinstance(value, list) else str(value))
            line.append(f"你的回答：{shown}", style="dim")
            self.screen.story.add("system", line)
            self.screen.plugin_answer(ev["id"], value, "player")
        self.screen.call_later(self.ready)
