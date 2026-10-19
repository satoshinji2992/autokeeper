from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rich.cells import cell_len

Hook = Literal["start", "turn", "visit", "action", "answer", "timer", "roll", "clue", "command"]
Capability = Literal[
    "dice", "note", "flag", "attribute", "condition", "damage", "sanity", "clue",
    "panel", "show", "prompt", "timer", "item", "hint", "attitude",
]
EffectKind = Literal[
    "note", "flag", "attribute", "condition", "damage", "sanity", "clue",
    "show", "prompt", "timer", "schedule", "item", "hint", "attitude",
]

# What a plugin may put on the players' screens. Everything is data the app draws itself; the
# caps keep a panel inside the right column, a show short, and a prompt answerable.
MAX_PANELS = 2  # panels per plugin
PANEL_ROWS = 12
PANEL_ART = (10, 36)  # lines, columns of a panel's art block
SHOW_FRAMES = 12
SHOW_MS = 20000  # a show's total length
SHOW_ART = (16, 60)
OPTIONS = 8  # buttons, or items to arrange or pick
BOARD = 8  # rows and columns of a board
STEPS = 30  # answers in one mini-game (prompts opened by answers in a row)
PROMPTS_PER_TURN = 2  # prompts a plugin may open per turn, besides answers to its own game
TIMEOUT = (10, 900)  # seconds a player has to answer; then the default stands
# Colours a plugin may name: the theme's own, so the art keeps to the player's palette, or hex.
THEME_COLORS = ("primary", "secondary", "accent", "warning", "error", "success", "foreground", "dim")
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
_CLOCK = re.compile(r"D\d+ \d{1,2}:\d{2}")


# What each kind of plugin power looks like to a player, for the Ctrl+K card.
DOES = {"panel": "在右栏放一页", "show": "放一小段演出", "prompt": "请你回答问题或玩个小游戏",
        "timer": "到点触发", "hint": "提醒守秘人一句", "item": "给或拿走物品"}


def does(capabilities: list[str]) -> list[str]:
    return [DOES[c] for c in DOES if c in capabilities]


def color_ok(value: str) -> bool:
    return not value or value in THEME_COLORS or bool(_HEX.fullmatch(value))


def _art_fits(art: str, size: tuple[int, int]) -> bool:
    lines = art.split("\n")
    return len(lines) <= size[0] and all(cell_len(line) <= size[1] for line in lines)


class Row(BaseModel):
    """One row of a panel: a line of text, a labelled bar, a short list, a small table or a
    small block of ASCII art. `text` is the line, the bar's label or a caption."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "bar", "list", "table", "art"] = "text"
    text: str = Field("", max_length=200)
    value: int = Field(0, ge=-100000, le=100000)
    max: int = Field(100, ge=1, le=100000)
    color: str = ""
    items: list[str] = Field(default_factory=list, max_length=OPTIONS)
    cells: list[list[str]] = Field(default_factory=list, max_length=7, description="table：第一行是表头")
    art: str = Field("", max_length=600)

    @model_validator(mode="after")
    def _fits(self) -> Row:
        if not color_ok(self.color):
            raise ValueError(f"颜色 {self.color} 不认识：用 {'/'.join(THEME_COLORS)} 或 #rrggbb")
        if any(cell_len(item) > 60 for item in self.items):
            raise ValueError("list 每项最多 60 个字符宽")
        if any(len(row) > 4 or any(cell_len(c) > 24 for c in row) for row in self.cells):
            raise ValueError("table 最多 4 列，每格最多 24 个字符宽")
        if self.art and not _art_fits(self.art, PANEL_ART):
            raise ValueError(f"面板 art 最多 {PANEL_ART[0]} 行、每行 {PANEL_ART[1]} 列")
        return self


class Panel(BaseModel):
    """A small page in the right column, kept up to date by the plugin's own output."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")
    title: str = Field(min_length=1, max_length=12)
    rows: list[Row] = Field(default_factory=list, max_length=PANEL_ROWS)


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    art: str = Field("", max_length=1600)
    text: str = Field("", max_length=200)
    color: str = ""
    ms: int = Field(1500, ge=200, le=8000)

    @model_validator(mode="after")
    def _fits(self) -> Frame:
        if not color_ok(self.color):
            raise ValueError(f"颜色 {self.color} 不认识：用 {'/'.join(THEME_COLORS)} 或 #rrggbb")
        if self.art and not _art_fits(self.art, SHOW_ART):
            raise ValueError(f"演出画面最多 {SHOW_ART[0]} 行、每行 {SHOW_ART[1]} 列")
        if not (self.art or self.text):
            raise ValueError("演出的每一帧要有 art 或 text")
        return self


class Command(BaseModel):
    name: str = Field(pattern=r"^[\w\u4e00-\u9fff]{1,10}$", description="不带斜杠，例如 占卜")
    description: str = Field(min_length=1, max_length=80, description="一句话：玩家什么时候用")


class Action(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    description: str = Field(min_length=1, max_length=1000)


class Case(BaseModel):
    name: str
    event: dict[str, Any] = Field(default_factory=lambda: {"hook": "turn"})
    state: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    dice: list[int] = Field(default_factory=list)
    checks: list[bool] = Field(default_factory=list)
    expected_state: dict[str, Any] = Field(default_factory=dict)
    expected_effects: list[dict[str, Any]] = Field(default_factory=list)
    expected_panels: list[dict[str, Any]] | None = None  # checked as a subset of each panel, in order


class Plugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    api_version: Literal[1] = 1
    version: str = Field(default="0.1.0", pattern=r"^\d+\.\d+\.\d+$")
    hooks: list[Hook] = Field(default_factory=lambda: ["turn"], max_length=9)
    capabilities: list[Capability] = Field(default_factory=lambda: ["note"])
    actions: list[Action] = Field(default_factory=list, max_length=12)
    commands: list[Command] = Field(default_factory=list, max_length=3)
    flags: dict[str, str] = Field(default_factory=dict)
    initial: dict[str, Any] = Field(default_factory=dict)
    code: str = Field(min_length=1, max_length=32000)
    tests: list[Case] = Field(min_length=1, max_length=32)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class Call(BaseModel):
    plugin: str
    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Effect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EffectKind
    who: str = ""
    key: str = ""
    amount: int = Field(default=0, ge=-100, le=100)
    text: str = Field(default="", max_length=2000)
    success: str = "0"
    failure: str = "1D6"
    # show / prompt
    title: str = Field(default="", max_length=24)
    # attitude
    npc: str = ""
    # timer / schedule
    minutes: int = Field(default=0, ge=0, le=10080)
    rounds: int = Field(default=0, ge=0, le=200)
    at: str = ""
    # show
    shape: Literal["stage", "inline"] = "inline"
    frames: list[Frame] = Field(default_factory=list, max_length=SHOW_FRAMES)
    # prompt
    style: Literal["choice", "text", "number", "order", "pick", "board"] = "choice"
    options: list[str] = Field(default_factory=list, max_length=OPTIONS)
    board: list[list[str]] = Field(default_factory=list, max_length=BOARD)
    low: int = Field(default=0, ge=-9999, le=9999)
    high: int = Field(default=9, ge=-9999, le=9999)
    default: Any = None
    timeout: int = Field(default=180, ge=TIMEOUT[0], le=TIMEOUT[1])
    ai: Literal["default", "random"] = "default"

    @model_validator(mode="after")
    def _shape(self) -> Effect:
        """The rules of each kind, in words a plugin author can fix."""
        k = self.kind
        if k == "show":
            if not self.frames:
                raise ValueError("show 至少要有一帧 frames")
            if sum(f.ms for f in self.frames) > SHOW_MS:
                raise ValueError(f"show 全部帧加起来最多 {SHOW_MS // 1000} 秒")
        elif k == "prompt":
            if not _KEY.fullmatch(self.key):
                raise ValueError("prompt 需要 key（小写字母数字下划线），回答时原样带回")
            if not self.text.strip() or len(self.text) > 200:
                raise ValueError("prompt 的 text 是要问玩家的话，1–200 字")
            if self.style in ("choice", "order", "pick") and not 2 <= len(self.options) <= OPTIONS:
                raise ValueError(f"{self.style} 需要 2–{OPTIONS} 个 options")
            if any(not o.strip() or cell_len(o) > 30 for o in self.options) or len(set(self.options)) != len(self.options):
                raise ValueError("options 每项 1–30 个字符宽，不能重复")
            if self.style == "board" and (not self.board or any(not 1 <= len(r) <= BOARD for r in self.board)
                                          or any(cell_len(c) > 4 for r in self.board for c in r)):
                raise ValueError(f"board 是 1–{BOARD} 行、每行 1–{BOARD} 格的二维列表，每格最多 4 个字符宽")
            if self.style == "number" and self.low > self.high:
                raise ValueError("number 的 low 不能大于 high")
            if self.default is not None and answer_problem(self, self.default):
                raise ValueError(f"prompt 的 default 不是合法答案：{answer_problem(self, self.default)}")
        elif k in ("timer", "schedule"):
            if not _KEY.fullmatch(self.key):
                raise ValueError(f"{k} 需要 key（小写字母数字下划线），到时原样带回")
            if k == "schedule" and not _CLOCK.fullmatch(self.at):
                raise ValueError("schedule 的 at 写成游戏时间，例如 D2 06:00")
            if k == "timer" and self.minutes and self.rounds:
                raise ValueError("timer 只填 minutes 或 rounds 其中一个；都不填表示取消")
        elif k == "item":
            if not self.key.strip() or len(self.key) > 40 or self.amount not in (-1, 1):
                raise ValueError("item 需要物品名 key（最多 40 字），amount 为 1 给予或 -1 拿走")
        elif k == "hint":
            if not self.text.strip() or len(self.text) > 80:
                raise ValueError("hint 是给守秘人的一句话，1–80 字")
        elif k == "attitude":
            if not self.npc or not 1 <= abs(self.amount) <= 3 or len(self.text) > 60:
                raise ValueError("attitude 需要 npc，amount 为 -3…3 且不为 0，text 最多 60 字")
        return self


def default_answer(prompt: Effect) -> Any:
    """The answer that stands when nobody gives one: the prompt's own default, else the first
    option, the low end, an empty text, the options as listed, nothing picked, the first cell."""
    if prompt.default is not None:
        return prompt.default
    return {"choice": prompt.options[0] if prompt.options else "", "text": "", "number": prompt.low,
            "order": list(prompt.options), "pick": [], "board": [0, 0]}[prompt.style]


def answer_problem(prompt: Effect, value: Any) -> str:
    """Why `value` is not an answer to this prompt ("" when it is)."""
    s = prompt.style
    if s == "choice":
        return "" if value in prompt.options else "要选给出的一项"
    if s == "text":
        return "" if isinstance(value, str) and len(value) <= 80 else "要一段最多 80 字的话"
    if s == "number":
        ok = isinstance(value, int) and not isinstance(value, bool) and prompt.low <= value <= prompt.high
        return "" if ok else f"要 {prompt.low} 到 {prompt.high} 之间的整数"
    if s == "order":
        ok = isinstance(value, list) and sorted(map(str, value)) == sorted(prompt.options) and len(value) == len(prompt.options)
        return "" if ok else "要把全部选项排成一个顺序"
    if s == "pick":
        ok = isinstance(value, list) and all(v in prompt.options for v in value) and len(set(map(str, value))) == len(value)
        return "" if ok else "要从选项里挑若干项，不重复"
    ok = (isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
          and 0 <= value[0] < len(prompt.board) and 0 <= value[1] < len(prompt.board[value[0]]))
    return "" if ok else "要 [行, 列]，从 0 数起，落在格子里"


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: dict[str, Any]
    effects: list[Effect] = Field(default_factory=list, max_length=40)
    repeat: bool = False
    # An opt-in public one-liner for the Ctrl+K window, e.g. "风寒：第 2 天". The plugin
    # decides what to show; private counters stay in state unless named here.
    status: str = Field(default="", max_length=120)
    # The plugin's panels as they should look now: None leaves them as they are, [] takes them down.
    panels: list[Panel] | None = Field(default=None, max_length=MAX_PANELS)


# Attributes that walk out of plain data into frames, code objects or module globals. They do
# not start with an underscore, so the dunder rule misses them; forbidding them keeps a plugin
# from reaching builtins through a generator's frame or a str.format traversal.
ESCAPE_ATTRS = frozenset({
    "gi_frame", "gi_code", "gi_yieldfrom", "gi_running", "cr_frame", "cr_code", "cr_await",
    "cr_running", "cr_origin", "ag_frame", "ag_code", "ag_running", "f_globals", "f_locals",
    "f_builtins", "f_back", "f_code", "f_trace", "f_lasti", "f_lineno", "tb_frame", "tb_next",
    "tb_lasti", "tb_lineno", "func_globals", "func_code", "func_dict", "func_closure",
    "func_defaults", "func_builtins", "format", "format_map",
})
# Names that reach introspection, class creation or the import machinery. Most are not in the
# worker's allow-list either, but refusing them here gives a plain reason instead of a crash.
BANNED_NAMES = frozenset({
    "open", "exec", "eval", "compile", "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "input", "breakpoint", "type", "object", "super", "classmethod", "staticmethod",
    "property", "memoryview", "bytearray", "bytes", "__import__", "help", "dir", "exit", "quit",
})


# The game's own slash commands (tui/screens/game.py, tui/screens/net.py): a plugin command may not shadow them.
BUILTIN_COMMANDS = frozenset({
    "draw", "notes", "map", "sheet", "help", "recap", "save", "debrief", "prefs", "suggest", "usage",
    "retry", "undo", "retell", "theme", "quit", "done", "pass",
})


def check(plugin: Plugin) -> list[str]:
    problems = []
    try:
        tree = ast.parse(plugin.code)
    except SyntaxError as exc:
        return [f"Python 语法错误，第 {exc.lineno} 行：{exc.msg}"]
    handlers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "handle"]
    if len(handlers) != 1 or len(handlers[0].args.args) != 3:
        problems.append("必须定义 handle(event, ctx, api)，返回 state 和 effects")
    forbidden = (ast.Import, ast.ImportFrom, ast.ClassDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal)
    for node in ast.walk(tree):
        if isinstance(node, forbidden):
            problems.append(f"第 {node.lineno} 行不支持 {type(node).__name__}；使用纯 Python 与 api，不导入模块")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in ESCAPE_ATTRS):
            problems.append(f"第 {node.lineno} 行不能访问属性 {node.attr}")
        if isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("__")):
            problems.append(f"第 {node.lineno} 行不能使用 {node.id}")
    if len({action.name for action in plugin.actions}) != len(plugin.actions):
        problems.append("插件动作名重复")
    if plugin.actions and "action" not in plugin.hooks:
        problems.append("有 actions 时必须订阅 action 钩子")
    names = [command.name for command in plugin.commands]
    if len(set(names)) != len(names):
        problems.append("插件命令名重复")
    if taken := [name for name in names if name.lower() in BUILTIN_COMMANDS]:
        problems.append(f"命令 /{taken[0]} 和游戏自带的命令重名")
    if plugin.commands and "command" not in plugin.hooks:
        problems.append("有 commands 时必须订阅 command 钩子")
    if "prompt" in plugin.capabilities and "answer" not in plugin.hooks:
        problems.append("有 prompt 能力时必须订阅 answer 钩子，才收得到玩家的回答")
    if "timer" in plugin.capabilities and "timer" not in plugin.hooks:
        problems.append("有 timer 能力时必须订阅 timer 钩子，才收得到到点的事件")
    for key in plugin.flags:
        if not key or len(key) > 80:
            problems.append("标记名须为 1–80 字符")
    if len(json.dumps(plugin.initial)) > 32000:
        problems.append("插件初始状态过大")
    return list(dict.fromkeys(problems))
