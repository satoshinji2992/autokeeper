"""Build the browsing pages and metadata of a mod_library checkout.
Run with Python 3.12 and AutoKeeper 0.0.9.7 or newer installed.
"""
from __future__ import annotations

import argparse
import html
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

import yaml

from autokeeper.module import library_tree
from autokeeper.module.schema import Module


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value) or "other"


def label(value) -> str:
    return str(value).replace("\n", " ").replace("[", "\\[").replace("]", "\\]").replace("|", "\\|")


def build(root: Path) -> None:
    groups = defaultdict(lambda: defaultdict(list))
    for folder in sorted((root / "modules").iterdir()):
        if not (folder / "module/module.yaml").is_file():
            continue
        module = Module.load(folder / "module")
        library_tree.prepare(folder)
        groups[module.system][module.era or "未注明时代"].append(module)
        sections = []
        for kind, title, objects in (("places", "地点", module.locations), ("people", "人物", module.npcs),
                                     ("clues", "线索", module.clues)):
            links = []
            for item in objects:
                name = getattr(item, "name", None) or getattr(item, "title", None) or item.id
                filename = slug(item.id) + ".md"
                payload = yaml.safe_dump(item.model_dump(mode="json", exclude_defaults=True), allow_unicode=True,
                                         sort_keys=False, width=100)
                write(folder / "browse" / kind / filename,
                      f"# {label(name)}\n\n[返回《{label(module.title)}》](../../README.md) · 主持资料，含剧透\n\n"
                      f"```yaml\n{payload}```\n")
                links.append(f"- [{label(name)}](browse/{kind}/{quote(filename)})")
            sections.append(f"<details>\n<summary>{title}（{len(links)}）</summary>\n\n" +
                            "\n".join(links) + "\n\n</details>")
        raw = [(p.name, f"module/{quote(p.name)}") for p in sorted((folder / "module").iterdir()) if not p.name.startswith(".")]
        write(folder / "README.md",
              f"# {label(module.title)}\n\n[返回模组库](../../README.md) · [按规则浏览](../../catalog/{slug(module.system)}/README.md)\n\n"
              f"{module.subtitle}\n\n| 规则 | 时代 | 人数 | 时长 |\n| --- | --- | --- | --- |\n"
              f"| {label(module.system)} | {label(module.era)} | {label(module.players)} | {label(module.duration)} |\n\n"
              f"{module.hook.strip()}\n\n来源：{module.source}\n\n版权说明：{module.license or '请查看模组原始说明'}\n\n"
              "## 主持资料\n\n下列内容含剧透。人物、地点和线索可分层展开查看；修改时以 module/ 内的原文件为准。\n\n" +
              "\n\n".join(sections) + "\n\n## 原文件与素材\n\n" +
              "\n".join(f"- [{label(name)}]({url})" for name, url in raw) +
              f"\n\n## 游戏内安装\n\n```sh\nautokeeper library get {module.id}\n```\n")
    menu = []
    for system, eras in sorted(groups.items()):
        rows = []
        nested = []
        for era, modules in sorted(eras.items()):
            nested.append(f"<details>\n<summary>{html.escape(era)}</summary>\n\n" +
                          "\n".join(f"- [《{label(m.title)}》](modules/{m.id}/README.md) · {label(m.players)} · {label(m.duration)}"
                                    for m in modules) + "\n\n</details>")
            rows.extend(f"| [《{label(m.title)}》](../../modules/{m.id}/README.md) | {label(era)} | {label(m.players)} | {label(m.duration)} |"
                        for m in modules)
        menu.append(f"<details open>\n<summary>{html.escape(system)}</summary>\n\n" + "\n\n".join(nested) + "\n\n</details>")
        write(root / "catalog" / slug(system) / "README.md",
              f"# {label(system)}\n\n[返回模组库](../../README.md)\n\n| 模组 | 时代 | 人数 | 时长 |\n| --- | --- | --- | --- |\n" + "\n".join(rows))
    write(root / "README.md",
          "# AutoKeeper 公开模组库\n\n"
          "此分支专门存放模组原文件。无需密码；在游戏中打开「导入模组 → 模组库」，或运行 `autokeeper library list`。\n\n"
          "下载只取所选模组的文件，并校验发布目录的签名、文件大小和哈希。目录固定到 Git 提交，不会在更新时混入不同版本。\n\n"
          "## 浏览模组\n\n按规则、时代、模组逐级展开，再进入人物、地点和线索页面。简介不揭示真相；主持资料含剧透。\n\n" +
          "\n\n".join(menu) +
          "\n\n## 上传与修改\n\n"
          "[投稿流程与目录说明](CONTRIBUTING.md) · [模组格式](https://github.com/satoshinji2992/autokeeper/blob/main/docs/module-format.md)\n\n"
          "Fork 仓库后向 `mod_library` 分支提交 PR。提交可读的原文件，不需要压缩包、密码或签名密钥。维护者审阅、合并并发布目录后，客户端即可安装新版本。\n\n"
          "各模组保留原作者、来源和许可信息；公开浏览不改变其原有授权范围。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path("."))
    build(parser.parse_args().root.resolve())
