"""Modal screens: the roll prompt, push offer, handouts, notebook, sketch canvas, map, help,
models, ending."""

from __future__ import annotations

import io
import time

from PIL import Image
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import MouseDown, MouseMove, MouseUp
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, RadioButton, RadioSet, Static
from textual.widgets.option_list import Option
from textual_canvas import Canvas

from . import art


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


class RollModal(ModalScreen[str]):
    """The keeper calls for a check and the player throws the dice. There is no backing out:
    Escape only says so, and a key already on its way when the prompt opens does not count."""

    BINDINGS = [
        Binding("enter", "throw", "掷骰", priority=True),
        Binding("space", "throw", "掷骰", priority=True),
        Binding("escape", "stay", show=False),
    ]
    SETTLE = 0.4  # seconds before a key counts as a throw

    def __init__(self, ask: dict):
        super().__init__()
        self.ask = ask
        self.opened = time.monotonic()

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(f"守秘人要{self.ask.get('who') or '你'}掷骰", classes="modal-title")
            yield Static(roll_ask(self.ask), classes="modal-body")
            with Horizontal(classes="buttons"):
                yield Button("掷骰（回车 / 空格）", id="throw", variant="primary")

    def on_mount(self) -> None:
        self.opened = time.monotonic()

    def action_throw(self) -> None:
        if time.monotonic() - self.opened >= self.SETTLE:
            self.dismiss("roll")

    def action_stay(self) -> None:
        self.app.notify("这一掷躲不过去：按回车或空格掷骰", timeout=3)

    @on(Button.Pressed, "#throw")
    def thrown(self) -> None:
        self.dismiss("roll")


class PushModal(ModalScreen[str]):
    BINDINGS = [("escape", "dismiss('drop')", "放弃")]

    def __init__(self, offer: dict):
        super().__init__()
        self.offer = offer

    def compose(self) -> ComposeResult:
        o = self.offer
        need = o.get("need", o.get("target"))
        with Vertical(classes="modal"):
            yield Static(f"{o.get('skill')} 检定失败（掷出 {o.get('value')}，要 ≤{need}）", classes="modal-title")
            body = Text()
            if o.get("can_push"):
                body.append("孤注一掷：", style="bold")
                body.append(f"换个更冒险的做法再掷一次，还是要 ≤{need}。再失败，后果一定发生，而且比这次更糟；"
                            "推过的检定不能再推，也不能花幸运补救。\n")  # fmt: skip
            if o.get("luck") is not None:
                left = o.get("left")
                body.append(f"花 {o['luck']} 点幸运：", style="bold")
                body.append("这次直接算恰好成功。")
                body.append(f"幸运 {left + o['luck']} → {left}，" if left is not None else "")
                body.append("花掉的不会回来，以后靠运气的时候更难。\n")
            body.append("放弃：", style="bold")
            body.append("接受失败。" + ("这里的关键发现还是会到手，只是要付出代价（多花时间、弄出动静）。" if o.get("core") else "换条路。"))
            yield Static(body, classes="modal-body")
            with Horizontal(classes="buttons"):
                if o.get("can_push"):
                    yield Button("孤注一掷", id="push", variant="error")
                if o.get("luck") is not None:
                    yield Button(f"花 {o['luck']} 点幸运", id="luck", variant="warning")
                yield Button("放弃", id="drop")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "drop")


class HandoutModal(ModalScreen[None]):
    """Paper frame whose text 'develops' like ink soaking through."""

    BINDINGS = [("escape", "dismiss", "关闭"), ("enter", "dismiss", "关闭")]

    def __init__(self, title: str, text: str):
        super().__init__()
        self.paper = art.frame(text, title, width=60)
        self.revealed = 0

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("", id="paper")
            yield Static("Esc 收起", classes="system")

    def on_mount(self) -> None:
        self.set_interval(0.02, self._develop)

    def _develop(self) -> None:
        if self.revealed >= len(self.paper):
            return
        self.revealed = min(len(self.paper), self.revealed + max(4, len(self.paper) // 60))
        shown = self.paper[: self.revealed]
        rest = art.corrupt("".join(c if c in "\n" else " " for c in self.paper[self.revealed :]), 0.0)
        self.query_one("#paper", Static).update(Text(shown + rest))


class NotebookModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "dismiss(None)", "关闭")]

    def __init__(self, clues: list[tuple[str, str]], handouts: list[tuple[str, str]]):
        super().__init__()
        self.clues, self.handouts = clues, handouts

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(f"线索本 · {len(self.clues)} 条", classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                if not self.clues:
                    yield Static("还什么都没有发现。", classes="system")
                for title, text in self.clues:
                    t = Text()
                    t.append(f"〔{title}〕\n", style="bold")
                    t.append(text)
                    yield Static(t, classes="entry clue")
            if self.handouts:
                with Horizontal(classes="buttons"):
                    for hid, title in self.handouts:
                        yield Button(f"手稿：{title}", id=f"h-{hid}")

    @on(Button.Pressed)
    def open_handout(self, event: Button.Pressed) -> None:
        self.dismiss((event.button.id or "")[2:])


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
            yield Static("左键画，右键擦。画完点“交给守秘人”。", classes="system")
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


class TextModal(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "关闭"), ("enter", "dismiss", "关闭")]

    def __init__(self, title: str, body: Text | str):
        super().__init__()
        self.title_, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(self.title_, classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(self.body)
            yield Static("Esc 关闭", classes="system")


MODE_NOTES = {
    "经济": "所有角色少想一层：更快，更省",
    "标准": "按预设的分工",
    "高级剧情": "守秘人、导演、NPC 和 AI 队友多想一层：剧情更细，也更慢、更贵",
}


class EngineModal(ModalScreen[dict | None]):
    """Switch models mid-game. Every agent's memory lives in the save, not in the model, so
    the new one carries on where the old one stopped."""

    BINDINGS = [("escape", "dismiss(None)", "取消")]

    def __init__(self, presets: dict[str, str], preset: str, model: str, fallback: str, mode: str = "标准"):
        super().__init__()
        self.presets, self.preset, self.model, self.fallback, self.mode = presets, preset, model, fallback, mode

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("换模型", classes="modal-title")
            yield Static(Text("每个 Agent 的记忆都在存档里，换完接着玩，谁也不会忘事。", style="dim"))
            options = [Option(Text.assemble((f"{name:<9}", "bold"), desc), id=name) for name, desc in self.presets.items()]
            yield OptionList(*options, id="preset")
            with RadioSet(id="mode"):
                for name, note in MODE_NOTES.items():
                    yield RadioButton(Text.assemble((name.ljust(4, "　"), "bold"), "  ", note), value=name == self.mode, name=name)
            yield Input(self.model, placeholder="所有角色改用同一个模型（可空），如 ollama:qwen3:14b", id="model")
            yield Input(self.fallback, placeholder="备用模型（可空）：主模型出错或超预算时改用它", id="fallback")
            with Horizontal(classes="buttons"):
                yield Button("应用", id="apply", variant="primary")
                yield Button("取消", id="cancel")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.highlighted = list(self.presets).index(self.preset) if self.preset in self.presets else 0

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        if event.button.id != "apply":
            self.dismiss(None)
            return
        ol = self.query_one(OptionList)
        preset = ol.get_option_at_index(ol.highlighted or 0).id or self.preset
        pressed = self.query_one(RadioSet).pressed_button
        mode = pressed.name if pressed is not None and pressed.name else self.mode
        self.dismiss({"preset": preset, "mode": mode, "model": self.query_one("#model", Input).value.strip(), "fallback": self.query_one("#fallback", Input).value.strip()})


HELP = Text.from_markup(
    """[b]直接说你想做什么[/b]，比如“我翻看书台上的折扇”“我去拾遗书局”“我问小顺子钥匙在哪”。

[b]骰娘指令[/b]
  .ra 侦查        想在这里用侦查：对着谁、该不该掷，守秘人看场景裁定
  .rab 侦查       请求奖励骰      .rap 侦查        请求惩罚骰
  .ra 困难聆听    请求难度        （几颗骰、多难，最后由守秘人定）
  .r 1d6+1        随手掷骰        .coc             掷一组属性
  .st             看人物卡        .en              结局后成长检定，每局一次
  .ti / .li       查疯狂发作表，只看不算

  守秘人要你掷骰时会先说清掷什么、要掷到多少，按回车或空格自己掷。
  失败的检定不能重掷：弹出“孤注一掷”时可以再搏一次，不然就换个办法。
  理智检定由守秘人发起，人物卡在游戏中不能改。输入时按 → 补全指令和技能名。

[b]界面[/b]
  F1 帮助   F2 线索本与手稿   F3 换模型   F4 手绘   F5 地图   F6 天气动画开关   F7 人物卡
  Ctrl+R 前情回顾   Ctrl+A 画法   Ctrl+T 配色   Ctrl+S 存档   Ctrl+Q 离开
  /draw /notes /map /sheet /recap /save 也可以在输入框里用"""
)
