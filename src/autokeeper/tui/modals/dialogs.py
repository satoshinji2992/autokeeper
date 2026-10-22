"""Modal screens: the roll prompt, push offer, handouts, sketch canvas, help, models, ending.
The notebook (F2) is notebook.py's."""

from __future__ import annotations

import asyncio
import io
import subprocess
import time
from pathlib import Path

from PIL import Image
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import MouseDown, MouseMove, MouseUp
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static
from textual_canvas import Canvas

from ... import imagery
from ..drawing import paper


def roll_ask(a: dict) -> Text:
    """What the keeper asks for, from a roll prompt (Game.roll_prompt): the attempt, why it
    is in doubt, the skill and the number to beat, all three thresholds, and the extra dice."""
    need = a.get("difficulty") or "常规"
    body = Text()
    if a.get("action"):
        body.append(f"{a['action']}\n", style="italic")
    if a.get("why"):
        body.append(f"{a['why']}\n", style="dim")
    body.append(f"\n{a.get('skill')} {a.get('target')}", style="bold")
    body.append(f" · {need}：掷出 ≤{a.get('need')} 就算过\n", style="bold")
    for name, limit in (("常规", a.get("target")), ("困难", a.get("hard")), ("极难", a.get("extreme"))):
        body.append(f"{name} ≤{limit}", style="bold underline" if name == need else "dim")
        body.append("   ")
    bonus = int(a.get("bonus") or 0)
    if bonus:
        body.append(f"\n{'奖励' if bonus > 0 else '惩罚'}骰 {abs(bonus)} 颗：", style="bold")
        body.append(f"多掷{'一' if abs(bonus) == 1 else '两'}个十位骰，取{'小' if bonus > 0 else '大'}的那个")
    return body


class Timed:
    """A window the host waits on only so long (a LAN table's `closes_in`, in seconds: the two machines'
    clocks need not agree). The countdown runs while the window is in front and stands still under
    another one; at its end, or when the host settles it (Hub "offer_closed"), the window closes with
    nothing (None) and sends nothing. It only ever closes itself: Textual's `dismiss()` pops whatever
    screen is in front, so a timeout under the command palette or a face closed that instead and left
    the window up, answered and dead to keys. Under another screen it waits, and goes once in front."""

    CLOCK = "剩余 {left} 秒"

    def timed(self, offer: dict) -> None:
        self.left: float | None = float(offer["closes_in"]) if offer.get("closes_in") else None
        self.answered = False
        self._due: tuple | None = None  # (answer,) waiting until this window is in front again
        self._last = time.monotonic()

    def start_clock(self) -> None:
        if self.left is not None:
            self._last = time.monotonic()
            self._tick()
            self.set_interval(0.25, self._tick)  # type: ignore[attr-defined]

    def _tick(self) -> None:
        now = time.monotonic()
        if self.left is None or self.answered or not self.is_active:  # type: ignore[attr-defined]
            self._last = now
            return
        self.left -= now - self._last
        self._last = now
        if self.left <= 0:
            self.settled()
            return
        for clock in self.query(".timed-clock").results(Static):  # type: ignore[attr-defined]
            clock.update(self.CLOCK.format(left=int(self.left + 0.99)))

    def settled(self) -> None:
        """Closed with nothing to send: the time ran out, or the host settled it."""
        self._close(None)

    def _close(self, answer) -> None:
        """Only this window goes, and once: at once in front, else when it is in front again."""
        if self.answered and (self._due is None or self._due[0] is not None):
            return  # answered already: a later settling changes nothing
        self.answered = True
        if not self.is_active:  # type: ignore[attr-defined]
            self._due = (answer,)
            return
        self._due = None
        self.dismiss(answer)  # type: ignore[attr-defined]

    def on_screen_resume(self) -> None:
        if self._due is not None:
            answer, self._due = self._due[0], None
            self.dismiss(answer)  # type: ignore[attr-defined]


class RollModal(Timed, ModalScreen[str | None]):
    """The keeper calls for a check and the player throws the dice. There is no backing out:
    Escape only says so, and a key already on its way when the prompt opens does not count."""

    BINDINGS = [
        Binding("enter", "throw", "掷骰", priority=True),
        Binding("space", "throw", "掷骰", priority=True),
        Binding("escape", "stay", show=False),
    ]
    SETTLE = 0.4  # seconds before a key counts as a throw

    CLOCK = "剩余 {left} 秒，超时由房主代掷"

    def __init__(self, ask: dict):
        super().__init__()
        self.ask = ask
        self.opened = time.monotonic()
        self.timed(ask)

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(f"等待掷骰 · {self.ask.get('who') or '当前调查员'}", classes="modal-title")
            yield Static(roll_ask(self.ask), classes="modal-body")
            if self.left is not None:
                yield Static("", classes="timed-clock")
            with Horizontal(classes="buttons"):
                yield Button("掷骰（回车 / 空格）", id="throw", variant="primary")

    def on_mount(self) -> None:
        self.opened = time.monotonic()
        self.start_clock()

    def action_throw(self) -> None:
        if time.monotonic() - self.opened >= self.SETTLE:
            self._close("roll")

    def action_stay(self) -> None:
        self.app.notify("按回车或空格完成这次检定。", timeout=3)

    @on(Button.Pressed, "#throw")
    def thrown(self) -> None:
        self._close("roll")


class PushModal(Timed, ModalScreen[str | dict | None]):
    """A failed check's second chance: push (saying how, as the rulebook asks, p. 73), spend luck,
    or accept the failure. A push answers {"choice": "push", "how": ...}; the others their word;
    None when a LAN host stopped waiting (Timed)."""

    BINDINGS = [("escape", "drop", "放弃")]

    def __init__(self, offer: dict):
        super().__init__()
        self.offer = offer
        self.timed(offer)

    def on_mount(self) -> None:
        self.start_clock()

    def action_drop(self) -> None:
        self._close("drop")

    def compose(self) -> ComposeResult:
        o = self.offer
        need = o.get("need", o.get("target"))
        with Vertical(classes="modal"):
            yield Static(f"{o.get('skill')} 检定失败（掷出 {o.get('value')}，目标 ≤{need}）", classes="modal-title")
            body = Text()
            if o.get("can_push"):
                body.append("孤注一掷：", style="bold")
                risk = o.get("risk") or "会有更严重的后果"
                body.append(f"写明调查员如何行动以孤注一掷（多花时间、加倍努力或冒更大的险）。通常仍按 ≤{need} 检定，"
                            f"所述做法改变了局势时，技能、难度与失败后果可能随之调整。再次失败：{risk}。"
                            "所述做法无法实现目标时不掷骰，按放弃处理。同一次检定只能孤注一掷一次，之后不能花幸运。\n")  # fmt: skip
            if o.get("luck") is not None:
                left = o.get("left")
                body.append(f"花 {o['luck']} 点幸运：", style="bold")
                body.append("本次结果调整为恰好成功。")
                body.append(f"幸运 {left + o['luck']} → {left}，" if left is not None else "")
                body.append("花费后会降低当前幸运值。\n")
            body.append("放弃：", style="bold")
            body.append("接受这次失败。可以换个办法、换个人再试。")
            yield Static(body, classes="modal-body")
            if self.left is not None:
                yield Static("", classes="timed-clock")
            if o.get("can_push"):
                yield Input(placeholder="孤注一掷的做法，例如：把整排书架逐本翻完", max_length=200, id="push-how")
            with Horizontal(classes="buttons"):
                if o.get("can_push"):
                    yield Button("孤注一掷", id="push", variant="error")
                if o.get("luck") is not None:
                    yield Button(f"花 {o['luck']} 点幸运", id="luck", variant="warning")
                yield Button("放弃", id="drop")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        if event.button.id == "push":
            self._push()
        else:
            self._close(event.button.id or "drop")

    @on(Input.Submitted, "#push-how")
    def submitted(self) -> None:
        self._push()

    def _push(self) -> None:
        box = self.query_one("#push-how", Input)
        how = " ".join(box.value.split())
        if not how:
            self.app.notify("请先写明如何孤注一掷。", severity="warning", timeout=3)
            box.focus()
            return
        self._close({"choice": "push", "how": how})


class HandoutModal(ModalScreen[None]):
    """The handout on its kind of paper (drawing/paper.py), the ink soaking in."""

    BINDINGS = [("escape", "dismiss", "关闭"), ("enter", "dismiss", "关闭")]

    def __init__(self, title: str, text: str, pictures: list[str] | None = None, kind: str = "信件"):
        super().__init__()
        self.sheet = paper.sheet(kind, title, text, width=60)
        self.revealed = 0
        self.pictures = pictures or []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="handout-modal"):
            with VerticalScroll(classes="modal-body"):
                for i, _ in enumerate(self.pictures):
                    yield Static("正在加载原图……", id=f"handout-image-{i}")
                    yield Button(f"查看原图 {i + 1}", id=f"original-{i}")
                yield Static("", id="paper")
            yield Static("Esc 收起", classes="system")

    def on_mount(self) -> None:
        self.set_interval(0.02, self._develop)
        if self.pictures:
            self.run_worker(self._load_pictures())

    async def _load_pictures(self) -> None:
        for i, path in enumerate(self.pictures):
            try:
                preview = await asyncio.to_thread(imagery.render, path, 56, 18, getattr(self.app, "art_style", "photo")) if imagery.available() else ""
            except (OSError, ValueError, subprocess.TimeoutExpired):
                preview = ""
            if not self.is_mounted:
                return
            self.query_one(f"#handout-image-{i}", Static).update(
                Text.from_ansi(preview) if preview else Text("原图可通过下方按钮查看。", style="dim")
            )

    @on(Button.Pressed)
    def open_original(self, event: Button.Pressed) -> None:
        if (key := event.button.id or "").startswith("original-"):
            path = Path(self.pictures[int(key.removeprefix("original-"))]).resolve()
            if not path.is_file():
                self.app.notify("原图暂时不可用，正文仍可阅读。", severity="warning")
            elif self.app.is_web:
                self.app.deliver_binary(path, open_method="browser")
            else:
                self.app.open_url(path.as_uri())

    def _develop(self) -> None:
        size = self.sheet.size
        if self.revealed >= size:
            return
        self.revealed = min(size, self.revealed + max(4, size // 60))
        self.query_one("#paper", Static).update(self.sheet.render(self.revealed))


class SketchCanvas(Canvas):
    """Left button draws, right button erases."""

    def __init__(self, **kw):
        super().__init__(70, 40, canvas_color=Color(18, 14, 8), pen_color=Color(255, 176, 0), **kw)
        self.drawing: int | None = None
        self.last: tuple[int, int] | None = None

    def _pt(self, event) -> tuple[int, int]:
        return max(0, min(69, event.x - 1)), max(0, min(39, (event.y - 1) * 2))

    def on_mouse_down(self, event: MouseDown) -> None:
        self.capture_mouse()
        self.drawing = event.button
        self.last = self._pt(event)
        self._dot(*self.last)

    def on_mouse_move(self, event: MouseMove) -> None:
        if self.drawing is None:
            return
        pt = self._pt(event)
        color = self._color()
        if self.last:
            self.draw_line(*self.last, *pt, color)
            self.draw_line(self.last[0], self.last[1] + 1, pt[0], pt[1] + 1, color)
        self.last = pt

    def on_mouse_up(self, event: MouseUp) -> None:
        self.release_mouse()
        self.drawing, self.last = None, None

    def _color(self) -> Color:
        return Color(18, 14, 8) if self.drawing == 3 else Color(255, 176, 0)

    def _dot(self, x: int, y: int) -> None:
        for dy in (0, 1):
            self.set_pixel(x, min(39, y + dy), self._color())

    def png(self) -> bytes:
        img = Image.new("RGB", (70, 40), (255, 255, 255))
        for x in range(70):
            for y in range(40):
                c = self.get_pixel(x, y)
                if c and (c.r, c.g, c.b) != (18, 14, 8):
                    img.putpixel((x, y), (0, 0, 0))
        img = img.resize((560, 320), Image.Resampling.NEAREST)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


class SketchModal(ModalScreen[bytes | None]):
    BINDINGS = [("escape", "dismiss(None)", "取消")]

    def __init__(self, ask: str):
        super().__init__()
        self.ask = ask

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(f"手绘 · {self.ask}", classes="modal-title")
            yield SketchCanvas(id="canvas")
            yield Static("左键绘制，右键擦除。完成后选择“交给守秘人”。", classes="system")
            with Horizontal(classes="buttons"):
                yield Button("交给守秘人", id="done", variant="primary")
                yield Button("清空", id="clear")
                yield Button("取消", id="cancel")

    @on(Button.Pressed, "#done")
    def done(self) -> None:
        self.dismiss(self.query_one(SketchCanvas).png())

    @on(Button.Pressed, "#clear")
    def clear(self) -> None:
        self.query_one(SketchCanvas).clear()

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


HELP = Text.from_markup(
    """[b]输入行动或对话，按回车发送。[/b]
例如：“我查看柜台上的账本”“我去警察局”“我向店主问路”。

[b]骰娘指令[/b]
  .ra 侦查        请求技能检定，由守秘人按情境裁定
  .rab 侦查       请求奖励骰      .rap 侦查        请求惩罚骰
  .ra 困难聆听    请求困难检定
  .r 1d6+1        掷骰            .coc             掷一组属性
  .st             查看人物卡      .en              结局后成长，每局一次
  .ti / .li       查看疯狂发作表，不改变人物状态

需要检定时，弹窗会说明技能、理由和目标值，按回车或空格掷骰。
同一尝试不能反复重掷；可用的补救选项会在失败后列出。
理智检定由守秘人发起。人物卡在游戏中不能直接改数值。
输入时按 → 补全指令和技能名。

[b]按键[/b]
  F1 玩法说明       F2 笔记与资料      F3 地图
  F4 人物卡         F5 行动建议        F6 重讲上一段
  F7 前情回顾       F8 手绘            F9 模型
  F10 结局后复盘
  Ctrl+S 存档       Ctrl+O 叙事偏好    Ctrl+Q 离开
  Ctrl+A 画法       Ctrl+T 配色        Ctrl+B 用量
  Ctrl+G 右栏翻页：经过、进展、背景
  Ctrl+Y 整理进展   Ctrl+L 灵感检定    Ctrl+N 收起画面
  Esc 菜单（存档、读档、玩法说明、回到标题、设置）

[b]战斗与追逐[/b]
  ↑↓ 回车 选择本轮行动    Esc 自行输入
  Tab 切换被攻击时的应对，不占用行动

F5 的建议只填入输入框，确认后再发送。
F6 只改叙述，骰子与 NPC 对话保留。

[b]输入框命令[/b]
  /draw /notes /map /sheet /suggest /recap /retell
  /debrief /save /prefs /usage /help /theme /quit
  /记     后接内容，记到笔记末尾，不占回合
  /灵感   灵感检定，每个游戏日一次
  /retry  接续模型中断的回合"""
)
