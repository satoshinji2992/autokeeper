from __future__ import annotations

import ast
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Hook = Literal["start", "turn", "visit", "action"]
Capability = Literal["dice", "note", "flag", "attribute", "condition", "damage", "sanity", "clue"]


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


class Plugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    api_version: Literal[1] = 1
    version: str = Field(default="0.1.0", pattern=r"^\d+\.\d+\.\d+$")
    hooks: list[Hook] = Field(default_factory=lambda: ["turn"], max_length=4)
    capabilities: list[Capability] = Field(default_factory=lambda: ["note"])
    actions: list[Action] = Field(default_factory=list, max_length=12)
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

    kind: Literal["note", "flag", "attribute", "condition", "damage", "sanity", "clue"]
    who: str = ""
    key: str = ""
    amount: int = Field(default=0, ge=-100, le=100)
    text: str = Field(default="", max_length=2000)
    success: str = "0"
    failure: str = "1D6"


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: dict[str, Any]
    effects: list[Effect] = Field(default_factory=list, max_length=40)
    repeat: bool = False


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
    banned = {"open", "exec", "eval", "compile", "getattr", "setattr", "delattr", "globals", "locals", "vars", "input", "breakpoint"}
    for node in ast.walk(tree):
        if isinstance(node, forbidden):
            problems.append(f"第 {node.lineno} 行不支持 {type(node).__name__}；使用纯 Python 与 api，不导入模块")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            problems.append(f"第 {node.lineno} 行不能访问私有属性")
        if isinstance(node, ast.Name) and (node.id in banned or node.id.startswith("__")):
            problems.append(f"第 {node.lineno} 行不能使用 {node.id}")
    if len({action.name for action in plugin.actions}) != len(plugin.actions):
        problems.append("插件动作名重复")
    if plugin.actions and "action" not in plugin.hooks:
        problems.append("有 actions 时必须订阅 action 钩子")
    for key in plugin.flags:
        if not key or len(key) > 80:
            problems.append("标记名须为 1–80 字符")
    if len(json.dumps(plugin.initial)) > 32000:
        problems.append("插件初始状态过大")
    return list(dict.fromkeys(problems))
