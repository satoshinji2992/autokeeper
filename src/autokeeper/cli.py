"""Command line: `autokeeper` with no arguments opens the game."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import artkit, config

app = typer.Typer(help="AutoKeeper：自动化 CoC 7 版中文跑团", no_args_is_help=False, add_completion=False)
console = Console()


def _module_dirs() -> list[Path]:
    dirs = []
    for base in (config.MODULES, config.LOCAL_MODULES):
        if base.is_dir():
            dirs += [p for p in sorted(base.iterdir()) if (p / "module.yaml").exists()]
    return dirs


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        play()


@app.command()
def play(
    theme: Annotated[str | None, typer.Option(help="配色：amber / phosphor / lavender / blood")] = None,
    intro: Annotated[bool, typer.Option(help="启动时播放标题特效")] = True,
) -> None:
    """打开终端界面。"""
    from .tui.app import AutoKeeperApp

    if intro and sys.stdout.isatty():
        _intro()
    AutoKeeperApp(theme=theme).run()


def _intro() -> None:
    try:
        from terminaltexteffects.effects.effect_decrypt import Decrypt

        from .tui import art

        lines = "\n".join(art.title_lines(os.get_terminal_size().columns)) + "\n\n" + art.TAGLINE
        effect = Decrypt(lines)
        effect.effect_config.typing_speed = 4
        with effect.terminal_output() as terminal:
            for frame in effect:
                terminal.print(frame)
    except Exception:  # the intro is decoration; never block the game on it
        pass


@app.command()
def serve(port: int = 8000) -> None:
    """在浏览器里玩（textual-serve）。"""
    from textual_serve.server import Server

    Server(f"{sys.executable} -m autokeeper play --no-intro", port=port, title="AutoKeeper").serve()


@app.command()
def setup() -> None:
    """安装 BCDice 桥接、编译内置模组的 Skill 包、为 Claude Code 和 Codex 链接 skills/。"""
    bridge = config.ROOT / "bridge"
    console.print("[bold]1/3[/] 安装 BCDice（npm）…")
    subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=bridge, check=True)
    console.print("[bold]2/3[/] 编译模组 Skill 包…")
    from .module.compiler import compile_module
    from .module.schema import Module

    for path in _module_dirs():
        root = compile_module(Module.load(path), config.SKILLS)
        console.print(f"    {root.relative_to(config.ROOT)}")
    console.print("[bold]3/3[/] 链接 .claude/skills 与 .agents/skills …")
    for dot in (".claude", ".agents"):
        target = config.ROOT / dot / "skills"
        target.parent.mkdir(exist_ok=True)
        if not target.exists():
            target.symlink_to(Path("..") / "skills")
    console.print("[green]完成。[/] 运行 [bold]uv run autokeeper[/] 开始游戏。")


@app.command()
def doctor() -> None:
    """检查运行环境。"""
    import shutil

    from .rules.bcdice import BCDice

    ok = True
    for tool in ("node", "claude"):
        found = shutil.which(tool)
        console.print(f"{'✓' if found else '✗'} {tool}: {found or '未找到'}")
        ok &= bool(found)
    try:
        with BCDice() as dice:
            r = dice.roll("CC<=50")
            console.print(f"✓ BCDice {dice.version}: {r.text}")
    except Exception as exc:
        ok = False
        console.print(f"✗ BCDice: {exc}")
    cfg = config.load()
    for role in ("keeper", "arbiter", "npc", "intent", "importer"):
        r = cfg.role(role)
        console.print(f"  {role:<12} {r.backend}:{r.model} effort={r.effort}")
    raise typer.Exit(0 if ok else 1)


@app.command()
def check(module: Annotated[Path, typer.Argument(help="模组目录")]) -> None:
    """校验模组：引用、可达性、线索冗余、ASCII 图和配色蒙版。"""
    from .module import graph
    from .module.schema import Module

    m = Module.load(module)
    issues = [str(i) for i in graph.audit(m)]
    portraits = {n.portrait for n in m.npcs} | {p.portrait for p in m.pregens}
    for name, text in m.art.items():
        issues += [f"[图] {i}" for i in artkit.check(name, text, m.art_colors.get(name), portrait=name in portraits)]
    errors = [i for i in issues if "错误" in i or i.startswith("[图]")]
    for i in issues:
        console.print(i)
    console.print(f"{'[red]有错误[/]' if errors else '[green]通过[/]'}：{len(issues)} 条提示，{len(errors)} 条需要修")
    raise typer.Exit(1 if errors else 0)


@app.command(name="compile")
def compile_cmd(module: Annotated[Path, typer.Argument(help="模组目录")]) -> None:
    """把模组编译成 Agent Skill 包（渐进式披露）。"""
    from .module.compiler import compile_module
    from .module.schema import Module

    root = compile_module(Module.load(module), config.SKILLS)
    console.print(f"已生成 {root}")


art_app = typer.Typer(help="ASCII 美术")
app.add_typer(art_app, name="art")


@art_app.command("color")
def art_color(module: Path, force: bool = False) -> None:
    """让美术 Agent 给模组里的 ASCII 图上色。"""
    from .agents.artist import color_folder
    from .module.schema import Module

    m = Module.load(module)
    hints = {loc.art: f"场景：{loc.name}。{loc.read_aloud.strip()[:120]}" for loc in m.locations if loc.art}
    hints |= {n.portrait: f"人物头像：{n.name}，{n.role}，{n.look}" for n in m.npcs if n.portrait}
    hints |= {p.portrait: f"调查员头像：{p.name}，{p.occupation}" for p in m.pregens if p.portrait}
    done = asyncio.run(color_folder(config.load(), module / "art", hints, force=force))
    console.print(f"上色完成：{', '.join(done) or '无需上色'}")


@art_app.command("show")
def art_show(module: Path, name: str) -> None:
    """在终端里预览一张彩色 ASCII 图。"""
    from .module.schema import Module

    m = Module.load(module)
    console.print(artkit.colorize(m.art[name], m.art_colors.get(name)))


@app.command(name="import")
def import_cmd(
    file: Annotated[Path, typer.Argument(help="模组文件：PDF / Word / Markdown")],
    id: Annotated[str, typer.Option("--id", help="模组的英文短名，如 zhuishuren")],
    pages: Annotated[str | None, typer.Option(help="只取这些页，如 7-18")] = None,
) -> None:
    """让 Opus 读模组、写 module.yaml、画彩色 ASCII 图，校验通过后编译成 Skill 包。"""
    from .module.importer import run_import

    asyncio.run(run_import(file, id, pages=pages, console=console))


@app.command()
def generate(
    seed: Annotated[str, typer.Argument(help="一句话种子，如“1925 年上海，一家照相馆的底片里多了一个人”")],
    id: Annotated[str, typer.Option("--id", help="模组的英文短名")],
) -> None:
    """联网考据后原创一个模组：考据卡 → 编剧 → 校验 → 画图 → 编译。"""
    from .module.generator import run_generate

    asyncio.run(run_generate(seed, id, console=console))


@app.command()
def autoplay(
    module: Annotated[Path, typer.Argument(help="模组目录")],
    party: int = 1,
    turns: int = 40,
    slot: str | None = None,
) -> None:
    """无界面自动跑团：全部由 AI 调查员行动，输出跑团记录。"""
    from .engine.autoplay import run

    asyncio.run(run(module, party=party, max_turns=turns, slot=slot, console=console))


eval_app = typer.Typer(help="评测")
app.add_typer(eval_app, name="eval")


@eval_app.command("rhetoric")
def eval_rhetoric(module: Path = Path("modules/local/zhuishuren"), limit: int | None = None) -> None:
    """修辞诱导对照：单 Agent 裁定 vs 叙事与裁定分离。"""
    from .eval.rhetoric import run as run_rhetoric

    out = config.SAVES / "eval" / "rhetoric.json"
    summary = asyncio.run(run_rhetoric(module, out, limit=limit))
    console.print_json(data=summary)
    console.print(f"明细：{out}")


@eval_app.command("context")
def eval_context(modules: list[Path]) -> None:
    """渐进式披露的上下文账本：整本模组 vs 总览 + 当前地点文件。"""
    from .eval.context import ledger

    for m in modules:
        console.print_json(data=ledger(m))


@app.command()
def mcp() -> None:
    """以 MCP 服务运行（给 Claude Code / Codex 的嵌入模式用）。"""
    from .mcp_server import server

    server.run()


if __name__ == "__main__":
    app()
