"""Command line: `autokeeper` with no arguments opens the game."""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
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
def main(
    ctx: typer.Context,
    preset: Annotated[str | None, typer.Option(help="模型预设：claude / deepseek")] = None,
    model: Annotated[str | None, typer.Option(help="所有角色改用这个模型，如 ollama:qwen3:14b、deepseek:deepseek-flash")] = None,
    search: Annotated[str | None, typer.Option(help="联网搜索：auto / tavily / ddgs / wikipedia / none")] = None,
    budget: Annotated[float | None, typer.Option(help="按量计费 API 的花费上限（美元）")] = None,
) -> None:
    # Environment, not globals: the browser server and every config.load() see the same choice.
    for name, value in (("PRESET", preset), ("MODEL", model), ("SEARCH", search), ("BUDGET_USD", budget)):
        if value is not None:
            os.environ[f"AUTOKEEPER_{name}"] = str(value)
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

    argv = [sys.executable, "-m", "autokeeper", "play", "--no-intro"]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    Server(command, port=port, title="AutoKeeper").serve()


@app.command()
def setup() -> None:
    """安装 BCDice 桥接、编译已导入模组的 Skill 包、为 Claude Code 和 Codex 链接 skills/。"""
    bridge = config.ROOT / "bridge"
    console.print("[bold]1/3[/] 安装 BCDice（npm）…")
    npm = shutil.which("npm") or "npm"  # npm.cmd on Windows
    subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=bridge, check=True)
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
        if target.is_dir():
            continue
        if target.exists() or target.is_symlink():  # e.g. a git symlink checked out as a text file on Windows
            target.unlink()
        try:
            target.symlink_to(Path("..") / "skills", target_is_directory=True)
        except OSError:  # Windows without developer mode: copy instead of linking
            shutil.copytree(config.SKILLS, target)
            console.print(f"    {dot}/skills 无法建软链接，已改为复制（skills/ 更新后重新运行 setup）")
    console.print("[green]完成。[/] 运行 [bold]uv run autokeeper[/] 开始游戏。")


KEYS = {"deepseek": ("DEEPSEEK_API_KEY",), "openai": ("OPENAI_API_KEY",), "anthropic": ("ANTHROPIC_API_KEY",),
        "google_genai": ("GOOGLE_API_KEY", "GEMINI_API_KEY")}


@app.command()
def doctor(ping: Annotated[bool, typer.Option(help="给每个用到的模型发一句话，测通不通、多快")] = False) -> None:
    """检查运行环境、每个角色的模型路由、密钥、搜索后端和花费。"""
    from rich.table import Table

    from .harness import spend
    from .harness.search import resolve
    from .rules.bcdice import BCDice

    ok = True
    cfg = config.load()
    found = shutil.which("node")
    console.print(f"{'✓' if found else '✗'} node: {found or '未找到（BCDice 需要）'}")
    ok &= bool(found)
    try:
        with BCDice() as dice:
            roll = dice.roll("CC<=50")  # starts the bridge, which reports its version
            console.print(f"✓ BCDice {dice.version}: {roll.text}")
    except Exception as exc:
        ok = False
        console.print(f"✗ BCDice: {exc}")
    table = Table(title=f"角色路由（预设：{cfg.preset}）", title_justify="left")
    for col in ("角色", "后端", "模型", "思考", "上下文窗口"):
        table.add_column(col)
    for name in sorted(cfg.roles):
        r = cfg.role(name)
        table.add_row(name, r.backend, r.model, str(r.effort or "关"), str(r.window or "默认"))
    console.print(table)
    routes = {(r.backend, r.model) for r in cfg.roles.values()}
    if any(b == "claude" for b, _ in routes):
        found = shutil.which("claude")
        console.print(f"{'✓' if found else '·'} claude CLI: {found or '未找到（用 Agent SDK 自带的 CLI）'}")
    for provider in sorted({m.partition(':')[0] for b, m in routes if b == 'langchain'}):
        names = KEYS.get(provider, ())
        have = [n for n in names if os.environ.get(n)]
        if names:
            console.print(f"{'✓' if have else '✗'} {provider}: {'、'.join(have) if have else '缺少 ' + ' 或 '.join(names)}")
            ok &= bool(have)
    console.print(f"· 联网搜索：{resolve(cfg.search)}（配置 {cfg.search}）")
    spent = spend.ledger()["total_usd"]
    console.print(f"· API 花费估计：${spent:.4f}" + (f" / 预算 ${cfg.budget_usd:g}" if cfg.budget_usd is not None else "（未设预算）"))
    if ping:
        ok &= asyncio.run(_ping(routes))
    raise typer.Exit(0 if ok else 1)


async def _ping(routes: set[tuple[str, str]]) -> bool:
    from .config import Role
    from .harness import make_model

    good = True
    for backend, model in sorted(routes):
        m = make_model(Role(backend, model, None), "你是连通性测试。", label="doctor")
        try:
            reply = await m.complete([{"role": "user", "content": "只回答两个字：收到"}])
            console.print(f"✓ {backend}:{model}  {reply.seconds:.1f}s  {reply.text.strip()[:20]}")
        except Exception as exc:
            good = False
            console.print(f"✗ {backend}:{model}  {type(exc).__name__}: {str(exc)[:200]}")
        finally:
            await m.close()
    return good


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


@art_app.command("fetch")
def art_fetch(
    module: Path,
    only: Annotated[list[str] | None, typer.Option(help="只处理这些图名")] = None,
    force: bool = False,
) -> None:
    """从 Wikimedia Commons 找真实图片（公有领域 / CC），读图员挑图裁剪，存成 art/<名字>.jpg 并记录署名。"""
    from .imagery import fetch_folder
    from .module.schema import Module

    m = Module.load(module)
    era = f"{m.era}，{m.setting}"
    targets = [(loc.art, f"{loc.name}（{era}）：{loc.read_aloud.strip()[:160]}", "scene") for loc in m.locations if loc.art]
    targets += [(n.portrait, f"{n.name}，{n.role}；{n.look}（{era}）", "portrait") for n in m.npcs if n.portrait]
    targets += [(p.portrait, f"{p.name}，{p.occupation}，{p.age} 岁，{p.backstory.get('个人描述', '')}（{era}）", "portrait") for p in m.pregens if p.portrait]
    if only:
        targets = [t for t in targets if t[0] in only]
    done = asyncio.run(fetch_folder(config.load(), module / "art", targets, force=force, log=console.print))
    console.print(f"找到 {len(done)}/{len(targets)} 张图")


@art_app.command("credits")
def art_credits(module: Path) -> None:
    """列出模组里真实图片的来源与授权。"""
    from .imagery import credits

    for c in credits(module / "art"):
        console.print(f"{c['name']}: {c['title']}  {c['license']}  {c['artist']}  {c['page']}")


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
    """导入 Agent 读模组、写 module.yaml、画彩色 ASCII 图，校验通过后编译成 Skill 包（模型看 importer 角色）。"""
    from .module.importer import run_import

    asyncio.run(run_import(file, id, pages=pages, console=console))


@app.command()
def index(module: Annotated[Path, typer.Argument(help="模组目录")]) -> None:
    """用 LightRAG 给模组原文和模组图谱建知识库（GraphRAG）。"""
    from .knowledge import KnowledgeBase
    from .module.schema import Module

    async def go() -> dict:
        async with KnowledgeBase(Module.load(module)) as kb:
            return await kb.build(module)

    console.print_json(data=asyncio.run(go()))


@app.command()
def ask(
    module: Annotated[Path, typer.Argument(help="模组目录")],
    question: Annotated[str, typer.Argument(help="关于模组的问题")],
    context: Annotated[bool, typer.Option("--context", help="只看检索到的上下文")] = False,
) -> None:
    """向模组知识库提问（LightRAG mix 检索）。"""
    from .knowledge import KnowledgeBase
    from .module.schema import Module

    async def go() -> str:
        async with KnowledgeBase(Module.load(module)) as kb:
            return await (kb.context(question) if context else kb.answer(question))

    console.print(asyncio.run(go()))


@app.command()
def generate(
    seed: Annotated[str, typer.Argument(help="一句话种子，如“1925 年上海，一家照相馆的底片里多了一个人”")],
    id: Annotated[str, typer.Option("--id", help="模组的英文短名")],
) -> None:
    """联网考据后原创一个模组：考据卡 → 编剧 → 校验 → 画图 → 编译。"""
    from .module.generator import run_generate

    asyncio.run(run_generate(seed, id, console=console))


@app.command()
def research(module: Annotated[Path, typer.Argument(help="生成的模组目录（含 research.md）")]) -> None:
    """联网逐条核实考据卡，更新出处说明。"""
    from .module.generator import verify_research

    asyncio.run(verify_research(module, console=console))


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


@eval_app.command("lore")
def eval_lore(
    module: Path = Path("modules/local/zhuishuren"),
    n: int = 24,
    only: Annotated[list[str] | None, typer.Option(help="只重跑这些策略，其余沿用上次的结果")] = None,
) -> None:
    """原文细节问答：总览 / 整本 / 向量检索 / GraphRAG（LightRAG mix）四种上下文的准确率与上下文大小。"""
    from .eval.lore import STRATEGIES
    from .eval.lore import run as run_lore

    out = config.SAVES / "eval" / f"lore_{module.name}.json"
    console.print_json(data=asyncio.run(run_lore(module, out, n=n, only=tuple(only or STRATEGIES))))
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
