from __future__ import annotations

import json
from pathlib import Path

from .schema import Plugin, check


def load(folder: Path, identity: str) -> Plugin:
    if not identity or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in identity):
        raise ValueError("插件 id 只能用小写字母、数字、下划线")
    root = (folder / identity).resolve()
    if not root.is_relative_to(folder.resolve()):
        raise ValueError("插件目录越界")
    manifest, source = root / "manifest.json", root / "plugin.py"
    if any(not p.resolve().is_relative_to(root) or p.stat().st_size > 100000 for p in (manifest, source)):
        raise ValueError("插件文件越界或过大")
    plugin = Plugin.model_validate({**json.loads(manifest.read_text(encoding="utf-8")), "code": source.read_text(encoding="utf-8")})
    if plugin.id != identity:
        raise ValueError("插件 id 与目录不一致")
    if problems := check(plugin):
        raise ValueError("；".join(problems))
    return plugin


def save(plugin: Plugin, folder: Path) -> None:
    target = folder / plugin.id
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(plugin.model_dump_json(exclude={"code"}, indent=2), encoding="utf-8")
    (target / "plugin.py").write_text(plugin.code, encoding="utf-8")
