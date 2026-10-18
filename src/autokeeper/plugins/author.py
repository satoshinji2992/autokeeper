from __future__ import annotations

import json

import yaml

from ..harness.tools import Tool, ToolError, Workspace
from .runtime import verify
from .schema import Plugin
from .storage import load, save


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
