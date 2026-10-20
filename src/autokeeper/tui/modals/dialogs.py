"""Modal screens: the roll prompt, push offer, handouts, notebook, sketch canvas, map, help,
models, ending."""

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
from textual.widgets import Button, Input, OptionList, RadioButton, RadioSet, Static
from textual.widgets.option_list import Option
from textual_canvas import Canvas

from ... import imagery
from ..drawing import art


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
            yield Static(f"等待掷骰 · {self.ask.get('who') or '当前调查员'}", classes="modal-title")
            yield Static(roll_ask(self.ask), classes="modal-body")
            with Horizontal(classes="buttons"):
                yield Button("掷骰（回车 / 空格）", id="throw", variant="primary")

    def on_mount(self) -> None:
        self.opened = time.monotonic()

    def action_throw(self) -> None:
        if time.monotonic() - self.opened >= self.SETTLE:
            self.dismiss("roll")

    def action_stay(self) -> None:
        self.app.notify("按回车或空格完成这次检定。", timeout=3)

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
            yield Static(f"{o.get('skill')} 检定失败（掷出 {o.get('value')}，目标 ≤{need}）", classes="modal-title")
            body = Text()
            if o.get("can_push"):
                body.append("孤注一掷：", style="bold")
                risk = o.get("risk") or "会有更严重的后果"
                body.append(f"采用更冒险的方式重试，目标仍为 ≤{need}。再次失败：{risk}。"
                            "同一次检定只能孤注一掷一次，之后不能花幸运。\n")  # fmt: skip
            if o.get("luck") is not None:
                left = o.get("left")
                body.append(f"花 {o['luck']} 点幸运：", style="bold")
                body.append("本次结果调整为恰好成功。")
                body.append(f"幸运 {left + o['luck']} → {left}，" if left is not None else "")
                body.append("花费后会降低当前幸运值。\n")
            body.append("放弃：", style="bold")
            body.append("接受这次失败。可以换个办法、换个人再试。")
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

    def __init__(self, title: str, text: str, pictures: list[str] | None = None):
        super().__init__()
        self.paper = art.frame(text, title, width=60)
        self.revealed = 0
        self.pictures = pictures or []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
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
        if self.revealed >= len(self.paper):
            return
        self.revealed = min(len(self.paper), self.revealed + max(4, len(self.paper) // 60))
        shown = self.paper[: self.revealed]
        rest = art.corrupt("".join(c if c in "\n" else " " for c in self.paper[self.revealed :]), 0.0)
        self.query_one("#paper", Static).update(Text(shown + rest))


class NotebookModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "dismiss(None)", "关闭")]

    def __init__(self, clues: list[tuple[str, str]], handouts: list[tuple[str, str]], facts: list[str] | None = None):
        super().__init__()
        self.clues, self.handouts = clues, handouts
        self.facts = facts or []

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(f"线索本 · {len(self.clues)} 条", classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                if not self.clues:
                    yield Static("暂无已发现的线索。", classes="system")
                for title, text in self.clues:
                    t = Text()
                    t.append(f"〔{title}〕\n", style="bold")
                    t.append(text)
                    yield Static(t, classes="entry clue")
                if self.facts:
                    yield Static("人情、约定与物品往来", classes="modal-title")
                    for fact in self.facts:
                        yield Static(Text(fact), classes="entry")
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


MODE_NOTES = {
    "经济": "降低各角色的思考强度",
    "标准": "使用各角色的预设强度",
    "高级剧情": "提高叙事相关角色的思考强度，可能增加等待和用量",
}


class EngineModal(ModalScreen[dict | None]):
    """Switch models mid-game. Every agent's memory lives in the save, not in the model, so
    the new one carries on where the old one stopped."""

    BINDINGS = [("escape", "dismiss(None)", "取消")]

    def __init__(self, presets: dict[str, str], preset: str, model: str, fallback: str, mode: str = "标准", *, in_game: bool = True):
        super().__init__()
        self.presets, self.preset, self.model, self.fallback, self.mode = presets, preset, model, fallback, mode
        self.in_game = in_game

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal"):
            yield Static("模型", classes="modal-title")
            yield Static(Text("切换模型后保留本局记录。" if self.in_game else "选择模型方案，可另设思考强度和备用模型。", style="dim"))
            options = [Option(Text.assemble((f"{name:<9}", "bold"), desc), id=name) for name, desc in self.presets.items()]
            yield OptionList(*options, id="preset")
            with RadioSet(id="mode"):
                for name, note in MODE_NOTES.items():
                    yield RadioButton(Text.assemble((name.ljust(4, "　"), "bold"), "  ", note), value=name == self.mode, name=name)
            yield Input(self.model, placeholder="统一模型（可选），例如 ollama:qwen3:14b", id="model")
            yield Input(self.fallback, placeholder="备用模型（可选）：主模型出错或超出预算时使用", id="fallback")
            yield Input(placeholder="API Key（可选；已经填过就留空）", password=True, id="key")
            yield Static("", id="ping-result")
            with Horizontal(classes="buttons"):
                yield Button("应用", id="apply", variant="primary")
                yield Button("测试连接", id="ping")
                yield Button("取消", id="cancel")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.highlighted = list(self.presets).index(self.preset) if self.preset in self.presets else 0

    def _chosen(self) -> str:
        ol = self.query_one(OptionList)
        return ol.get_option_at_index(ol.highlighted or 0).id or self.preset

    def _key(self, preset: str) -> None:
        """A key typed in: kept where every later start finds it (config.save_key), for this preset."""
        from ... import config

        key = self.query_one("#key", Input).value.strip()
        if key and (name := config.PRESET_KEYS.get(preset)):
            config.save_key(name, key)

    @on(Button.Pressed, "#ping")
    async def test(self) -> None:
        from ... import config

        preset = self._chosen()
        self._key(preset)
        result = self.query_one("#ping-result", Static)
        result.update(Text(f"正在连接 {preset}…", style="dim"))
        problem = await config.ping(preset)
        result.update(Text(f"✓ {preset} 连通了" if not problem else f"✗ {preset} 没连通：{problem}",
                           style="green" if not problem else "red"))

    @on(Button.Pressed, "#apply, #cancel")
    def choose(self, event: Button.Pressed) -> None:
        if event.button.id != "apply":
            self.dismiss(None)
            return
        preset = self._chosen()
        self._key(preset)
        pressed = self.query_one(RadioSet).pressed_button
        mode = pressed.name if pressed is not None and pressed.name else self.mode
        self.dismiss({"preset": preset, "mode": mode, "model": self.query_one("#model", Input).value.strip(), "fallback": self.query_one("#fallback", Input).value.strip()})


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
  F1 玩法说明       F2 线索本与手稿    F3 地图
  F4 人物卡         F5 行动建议        F6 重讲上一段
  F7 前情回顾       F8 手绘            F9 模型
  F10 结局后复盘
  Ctrl+S 存档       Ctrl+O 叙事偏好    Ctrl+Q 离开
  Ctrl+A 画法       Ctrl+T 配色        Ctrl+B 用量
  Ctrl+K 规则插件   Ctrl+G 右栏翻页：经过、进展、背景
  Ctrl+Y 整理进展   Esc 菜单（存档、读档、设置、回到标题）

F5 的建议只填入输入框，确认后再发送。
F6 只改叙述，骰子、线索与 NPC 对话保留。

[b]输入框命令[/b]
  /draw /notes /map /sheet /suggest /recap /retell
  /debrief /save /prefs /usage /help /theme /quit
  /retry  接续模型中断的回合"""
)
