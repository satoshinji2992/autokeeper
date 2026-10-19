from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import yaml

from ..harness.tools import Tool, ToolError, Workspace
from .runtime import verify
from .schema import Plugin
from .storage import load, save

if TYPE_CHECKING:
    from ..engine.state import GameState
    from ..module.schema import Module

HOUSE_RULE_TRIES = 2  # how many times the author may rewrite a failing plugin


def install(module: Module, state: GameState, plugin: Plugin) -> None:
    """Add a tested plugin to THIS game only: its source rides along the save and its manifest
    lives in memory, never in the module's files. An upgrade of a plugin already here must keep
    the same id and raise its version (improv.py / the engine enforce the same rule)."""
    module.extensions[plugin.id] = plugin
    if plugin.id not in module.plugins:
        module.plugins.append(plugin.id)
    for key, meaning in plugin.flags.items():
        module.flags.setdefault(key, meaning)
    state.plugin_sources[plugin.id] = plugin.model_dump(mode="json")
    state.plugin_errors.pop(plugin.id, None)  # a rewrite clears an earlier pause


async def house_rule(
    cast, module: Module, state: GameState, request: str, *, on_step: Callable[[str], Awaitable[None]] | None = None
) -> tuple[bool, str]:
    """Write a plugin from a player's plain-words house rule, run its tests in the sandbox, and
    install it into this save only if it passes. Returns (ok, message); a failure says what went
    wrong so the caller can offer to try again. The author uses the configured models; tests
    pass a canned cast whose author_plugin returns a prepared plugin."""
    problems: list[str] = []
    for attempt in range(1, HOUSE_RULE_TRIES + 1):
        if on_step:
            await on_step("守秘人在写规则…" if attempt == 1 else f"没跑通，再改一版（第 {attempt} 次）…")
        plugin = await cast.author_plugin(state, request, problems=problems or None)
        if plugin is None:
            problems = ["没能写出可用的规则"]
            continue
        if plugin.id in module.extensions:
            existing = module.extensions[plugin.id]
            if existing.fingerprint != plugin.fingerprint and _ver(plugin.version) <= _ver(existing.version):
                problems = [f"已有同名规则 {plugin.id}，升级要提高 version 并兼容旧状态"]
                continue
        if on_step:
            await on_step(f"在沙箱里跑 {len(plugin.tests)} 项测试…")
        if problems := await verify(plugin):
            continue
        install(module, state, plugin)
        return True, f"已装上「{plugin.name}」：{plugin.description.split('。')[0]}。"
    return False, "没能装上这条规则：" + "；".join(problems[:4])


def _ver(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def plugin_tool(workspace: Workspace) -> Tool:
    async def write_plugin(content: str) -> str:
        try:
            plugin = Plugin.model_validate(json.loads(content))
            module_path = workspace.resolve("module.yaml", write=True)
            raw = yaml.safe_load(module_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("先写 module.yaml")
            folder = workspace.resolve(f"plugins/{plugin.id}/manifest.json", write=True).parent
            workspace.resolve(f"plugins/{plugin.id}/plugin.py", write=True)
            if (folder / "manifest.json").exists():
                previous = load(workspace.root / "plugins", plugin.id)
                if previous.fingerprint != plugin.fingerprint and tuple(map(int, plugin.version.split("."))) <= tuple(map(int, previous.version.split("."))):
                    raise ValueError("修改已有插件必须增加 version")
            identities = raw.setdefault("plugins", [])
            if not isinstance(identities, list) or (plugin.id not in identities and len(identities) >= 8):
                raise ValueError("plugins 必须为列表，每模组最多 8 个")
            if problems := await verify(plugin):
                raise ValueError("；".join(problems))
            save(plugin, workspace.root / "plugins")
            if plugin.id not in identities:
                identities.append(plugin.id)
            module_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
            return f"插件 {plugin.id} v{plugin.version} 已通过 {len(plugin.tests)} 项测试，保存源码与清单，并登记到 module.yaml"
        except (ValueError, OSError, TypeError) as exc:
            raise ToolError(f"插件未保存：{str(exc)[:3000]}") from None

    return Tool("write_plugin", "开发模组规则插件；content 为完整插件 JSON（含 code 和 tests），实际执行测试后保存并登记", ("content",), write_plugin, ("content",))
