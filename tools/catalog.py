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
from PIL import Image

from autokeeper.module import library_tree
from autokeeper.module.schema import Module


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value) or "other"


def label(value) -> str:
    return str(value).replace("\n", " ").replace("[", "\\[").replace("]", "\\]").replace("|", "\\|")


COVER = "cover.webp"
COVER_LIMIT = 1024 * 1024


def check_cover(folder: Path) -> None:
    path = folder / COVER
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{folder.name}：请提供 {COVER}。")
    if path.stat().st_size > COVER_LIMIT:
        raise ValueError(f"{folder.name}：封面不得超过 1 MiB。")
    with Image.open(path) as cover:
        if cover.format != "WEBP" or cover.is_animated:
            raise ValueError(f"{folder.name}：封面必须是静态 WebP。")
        cover.load()


def check_art(folder: Path) -> None:
    """Reject private import material and assets without public distribution permission."""
    if (folder / "module" / "source").exists():
        raise ValueError(f"{folder.name}：请在本地保留 source/ 原书与导入资料。")
    for directory in (folder / "module" / "art", folder / "cache" / "showcases"):
        path = directory / "credits.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        pictures = (data or {}).get("pictures", {})
        image_keys = {(image.stem if image.stem in pictures else image.stem.removesuffix(".photo"))
                      for image in directory.rglob("*")
                      if image.is_file() and image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}}
        for key in sorted(image_keys):
            record = pictures.get(key)
            if not isinstance(record, dict) or not record.get("license"):
                raise ValueError(f"{folder.name}：{key} 缺少图片署名或许可记录。")
            while isinstance(record, dict):
                license_name = str(record.get("license") or "")
                if "只在本机使用" in license_name or (not license_name and (record.get("original") or record.get("source"))):
                    raise ValueError(f"{folder.name}：{key} 缺少公开分发授权。")
                record = record.get("based_on")


def thumb(module_id: str, prefix: str = "", width: int = 224) -> str:
    return f'<img src="{prefix}modules/{module_id}/{COVER}" width="{width}" alt="">'


def build(root: Path, *, browse_only: bool = False) -> None:
    groups = defaultdict(lambda: defaultdict(list))
    for folder in sorted((root / "modules").iterdir()):
        if not (folder / "module/module.yaml").is_file():
            continue
        module = Module.load(folder / "module")
        check_cover(folder)
        check_art(folder)
        if not browse_only:
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
              f"![《{label(module.title)}》封面]({COVER})\n\n[封面来源](../../catalog/cover-credits.md#{module.id})\n\n{module.subtitle}\n\n| 规则 | 时代 | 人数 | 时长 |\n| --- | --- | --- | --- |\n"
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
                          "\n".join(f"- [《{label(m.title)}》](modules/{m.id}/README.md) · {label(m.players)} · {label(m.duration)}\n  <br>{thumb(m.id)}"
                                    for m in modules) + "\n\n</details>")
            rows.extend(f"| {thumb(m.id, '../../', 96)} | [《{label(m.title)}》](../../modules/{m.id}/README.md) | {label(era)} | {label(m.players)} | {label(m.duration)} |"
                        for m in modules)
        menu.append(f"<details open>\n<summary>{html.escape(system)}</summary>\n\n" + "\n\n".join(nested) + "\n\n</details>")
        write(root / "catalog" / slug(system) / "README.md",
              f"# {label(system)}\n\n[返回模组库](../../README.md)\n\n| 封面 | 模组 | 时代 | 人数 | 时长 |\n| --- | --- | --- | --- | --- |\n" + "\n".join(rows))
    write(root / "README.md",
          "# AutoKeeper 公开模组库\n\n"
          "此分支专门存放模组原文件。无需密码；在游戏中打开「导入模组 → 模组库」，或运行 `autokeeper library list`。\n\n"
          "下载只取所选模组的文件，并校验发布目录的签名、文件大小和哈希。目录固定到 Git 提交，不会在更新时混入不同版本。\n\n"
          "## 浏览模组\n\n"
          "[在官网按封面浏览](https://satoshinji2992.github.io/autokeeper/#modules)，或在下方按规则和时代展开。"
          "各模组提供玩家简介，主持资料含剧透。"
          "下载超时时见[备用下载](https://github.com/satoshinji2992/autokeeper/blob/main/docs/module-library.md#备用下载)。\n\n" +
          "\n\n".join(menu) +
          "\n\n## 上传与修改\n\n"
          "[投稿流程与目录说明](CONTRIBUTING.md) · [模组格式](https://github.com/satoshinji2992/autokeeper/blob/main/docs/module-format.md)\n\n"
          "Fork 仓库后向 `mod_library` 分支提交 PR。提交可读的原文件，不需要压缩包、密码或签名密钥。维护者审阅、合并并发布目录后，客户端即可安装新版本。\n\n"
          "各模组保留原作者、来源和许可信息；公开浏览不改变其原有授权范围。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path("."))
    parser.add_argument("--browse-only", action="store_true", help="只更新浏览页和封面检查")
    args = parser.parse_args()
    build(args.root.resolve(), browse_only=args.browse_only)
