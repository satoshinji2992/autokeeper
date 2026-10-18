"""Narrative preferences: how long, how dark, how fast, and what stays off screen.

Fixed choices only (radio sets and a selection list), so nothing typed here can reach the
keeper, who knows the module's secrets. Saving writes saves/preferences.json; a game in
progress takes the new choices from its next narration on (prefs.py).
"""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, RadioButton, RadioSet, SelectionList, Static
from textual.widgets.selection_list import Selection

from ..prefs import HORRORS, LENGTHS, LINES, PACES, Prefs

SCALES = (("length", "叙事篇幅", LENGTHS), ("horror", "恐怖程度", HORRORS), ("pace", "节奏", PACES))


class PrefsModal(ModalScreen[Prefs | None]):
    """Pick and save; dismissed with the saved choices, or None when cancelled."""

    CSS_PATH = "prefs.tcss"
    BINDINGS = [Binding("escape", "dismiss(None)", "取消"), Binding("ctrl+s", "keep", "保存")]

    def __init__(self, current: Prefs | None = None):
        super().__init__()
        self.current = current if current is not None else Prefs.load()

    def compose(self) -> ComposeResult:
        p = self.current
        with VerticalScroll(classes="modal", id="prefs-modal"):
            yield Static("叙事偏好", classes="modal-title")
            yield Static(Text("调整叙述和对话的写法，不改变检定与剧情结果。", style="dim"))
            with Horizontal(id="prefs-body"):
                with Vertical(id="prefs-scales"):
                    for key, title, choices in SCALES:
                        yield Static(title, classes="prefs-label")
                        with RadioSet(id=f"prefs-{key}"):
                            for choice in choices:
                                yield RadioButton(choice, value=choice == getattr(p, key), name=choice)
                with Vertical(id="prefs-lines-box"):
                    yield Static("略去细节的内容", classes="prefs-label")
                    yield SelectionList[str](
                        *(Selection(Text.assemble((k, "bold"), "  ", (v, "dim")), k, k in p.lines) for k, v in LINES.items()),
                        id="prefs-lines",
                    )
            yield Static(
                Text(
                    "勾选后，相关事件仍会发生，但叙述会省略细节。"
                    "模组里写好的段落（手稿、事件原文）仍按原文显示。",
                    style="dim",
                ),
                id="prefs-note",
            )
            with Horizontal(classes="buttons"):
                yield Button("保存", id="keep", variant="primary")
                yield Button("取消", id="cancel")
            yield Static("Tab 换一栏 · 方向键移动 · 回车或空格选定 · Ctrl+S 保存 · Esc 取消", classes="system")

    def on_mount(self) -> None:
        self.query_one("#prefs-length", RadioSet).focus()
        self.call_after_refresh(self._cursor_on_choice)

    def _cursor_on_choice(self) -> None:
        """A radio set's cursor starts on its first button; put it on the current choice, so
        Enter right away keeps the setting instead of changing it."""
        for key, _, _ in SCALES:
            scale = self.query_one(f"#prefs-{key}", RadioSet)
            for _ in range(max(0, scale.pressed_index)):
                scale.action_next_button()

    def chosen(self) -> Prefs:
        """What the screen shows now."""
        picks = {}
        for key, _, _ in SCALES:
            pressed = self.query_one(f"#prefs-{key}", RadioSet).pressed_button
            picks[key] = pressed.name if pressed is not None and pressed.name else getattr(self.current, key)
        return Prefs(**picks, lines=self.query_one("#prefs-lines", SelectionList).selected)

    def action_keep(self) -> None:
        chosen = self.chosen()
        chosen.save()
        self.app.notify("叙事偏好已保存，从下一段生效。")
        self.dismiss(chosen)

    @on(Button.Pressed, "#keep")
    def keep(self) -> None:
        self.action_keep()

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)
