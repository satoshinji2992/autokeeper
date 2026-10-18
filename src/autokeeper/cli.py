"""Command line: `autokeeper` with no arguments opens the game."""

from __future__ import annotations

import asyncio
import contextlib
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
    fallback: Annotated[str | None, typer.Option(help="备用模型：主模型出错或超预算时改用它，如 ollama:qwen3:14b")] = None,
    improv: Annotated[str | None, typer.Option(help="自由度：即兴（默认，守秘人可以加人加线索加结局）/ 严格（只用模组写好的）")] = None,
    mode: Annotated[str | None, typer.Option(help="模式：标准（默认）/ 高级剧情（守秘人、导演、NPC、AI 队友多想一层）/ 经济（所有角色少想一层）")] = None,
) -> None:
    # Environment, not globals: the browser server and every config.load() see the same choice.
    for name, value in (("PRESET", preset), ("MODEL", model), ("SEARCH", search), ("BUDGET_USD", budget), ("FALLBACK", fallback), ("IMPROV", improv), ("MODE", mode)):
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
    _run(AutoKeeperApp(theme=theme))


def _run(app) -> None:
    """Run a full-screen app with file descriptor 2 sent to saves/stderr.log: C libraries
    (numba, onnxruntime) write warnings there directly, over the game. A crash's traceback
    lands in the log too, and is shown once the screen is back."""
    log = config.SAVES / "stderr.log"
    try:
        config.SAVES.mkdir(parents=True, exist_ok=True)
        start = log.stat().st_size if log.exists() else 0
        sink = open(log, "a", encoding="utf-8")  # held open while the app runs
        sys.stderr.flush()
        saved = os.dup(2)
        os.dup2(sink.fileno(), 2)
    except OSError:  # no saves folder, an odd terminal: run as is
        app.run()
        return
    try:
        app.run()
    finally:
        sys.stderr.flush()
        os.dup2(saved, 2)
        os.close(saved)
        sink.close()
    if app.return_code:
        with open(log, encoding="utf-8", errors="replace") as f:
            f.seek(start)
            sys.stderr.write(f.read())


def _intro() -> None:
    try:
        from terminaltexteffects.effects.effect_decrypt import Decrypt

        from .tui import art

        lines = "\n".join(art.title_lines(os.get_terminal_size().columns)) + "\n\n" + art.TAGLINE + "\n\n按任意键跳过"
        effect = Decrypt(lines)
        effect.effect_config.typing_speed = 4
        with _keypress() as pressed, effect.terminal_output() as terminal:
            for frame in effect:
                if pressed():
                    break
                terminal.print(frame)
    except Exception:  # the intro is decoration; never block the game on it
        pass


@contextlib.contextmanager
def _keypress():
    """A check for "was a key pressed", with the key swallowed so the game does not see it."""
    if os.name == "nt":
        import msvcrt

        def pressed() -> bool:
            hit = msvcrt.kbhit()
            while msvcrt.kbhit():
                msvcrt.getwch()
            return hit

        yield pressed
        return
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    def pressed() -> bool:
        hit = False
        while select.select([fd], [], [], 0)[0]:
            os.read(fd, 1024)
            hit = True
        return hit

    try:
        yield pressed
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


@app.command()
def serve(port: int = 8000) -> None:
    """在浏览器里玩（textual-serve）。"""
    from textual_serve.server import Server

    argv = [sys.executable, "-m", "autokeeper", "play", "--no-intro"]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    Server(command, port=port, title="AutoKeeper").serve()


def _lan_addresses() -> list[str]:
    """This machine's addresses on the local network, for the others to type in."""
    import socket

    found: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # picks the outgoing interface; nothing is sent
            found.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        found.update(a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(ip for ip in found if not ip.startswith("127.")) or ["127.0.0.1"]


@app.command()
def host(
    module: Annotated[Path, typer.Argument(help="模组目录")] = Path("modules/zhuishuren"),
    seats: Annotated[str, typer.Option(help="按预设调查员的顺序填“玩家 / AI / 不上场”，逗号分隔；默认第一位玩家，其余 AI")] = "",
    port: Annotated[int, typer.Option(help="网页端口：朋友用浏览器打开")] = 8000,
    hub_port: Annotated[int, typer.Option(help="联机端口：终端里 autokeeper join 连这个")] = 8765,
    load: Annotated[str | None, typer.Option(help="接着玩一个存档（存档名）")] = None,
    web_ui: Annotated[bool, typer.Option("--web/--no-web", help="同时开网页，朋友不用装任何东西")] = True,
) -> None:
    """开一个局域网房间：同一个网络里的朋友用浏览器打开地址就能加入。"""
    import threading
    import time

    from .engine.state import GameState
    from .net.hub import Hub

    plan = [x.strip() for x in seats.replace("，", ",").split(",") if x.strip()] or None
    state = GameState.model_validate_json((config.SAVES / f"{load}.json").read_text(encoding="utf-8")) if load else None
    room: dict = {}

    def run_hub() -> None:
        try:
            room["hub"] = hub = Hub(module, seats=plan, state=state)
            asyncio.run(hub.serve("0.0.0.0", hub_port))
        except Exception as exc:  # a seat plan that makes no sense, the port already taken
            room["error"] = exc

    threading.Thread(target=run_hub, name="hub", daemon=True).start()
    while not room.get("error") and not getattr(room.get("hub"), "port", 0):
        time.sleep(0.05)
    if room.get("error"):
        console.print(f"[red]房间没开成：{room['error']}[/]")
        raise typer.Exit(1)
    hub: Hub = room["hub"]
    ips = _lan_addresses()
    console.print(f"[bold]《{hub.m.title}》的房间开好了[/]")
    for inv in hub.state.investigators:
        console.print(f"  {inv.name}（{inv.occupation}）：{'玩家' if inv.id in hub.sits else 'AI'}")
    if web_ui:
        console.print("把这个地址发给同一个网络里的朋友，用浏览器打开：")
        for ip in ips:
            console.print(f"  [bold]http://{ip}:{port}[/]")
    console.print(f"装了 AutoKeeper 的人也可以在终端里加入：[bold]uv run autokeeper join {ips[0]}:{hub_port}[/]")
    console.print("[dim]人齐了自动开局；也可以在大厅里选“现在开始”，空座交给 AI。Ctrl+C 关闭房间。[/]")
    console.print(f"[dim]每回合自动存档，下次用 --load {hub.state.slot} 接着玩。[/]")
    if not web_ui:
        try:
            while not room.get("error"):
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return
    from textual_serve.server import Server

    argv = [sys.executable, "-m", "autokeeper", "join", f"127.0.0.1:{hub_port}"]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    Server(command, host="0.0.0.0", port=port, title="AutoKeeper 联机", public_url=f"http://{ips[0]}:{port}").serve()


@app.command()
def join(
    address: Annotated[str, typer.Argument(help="房主的地址，如 192.168.1.5 或 192.168.1.5:8765")] = "",
    name: Annotated[str, typer.Option(help="你的名字")] = "",
) -> None:
    """加入局域网房间。"""
    from .tui.app import AutoKeeperApp

    _run(AutoKeeperApp(join=(address, name)))


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
    table = Table(title=f"角色路由（预设：{cfg.preset} · 模式：{cfg.mode}）", title_justify="left")
    for col in ("角色", "后端", "模型", "思考", "上下文窗口", "备用"):
        table.add_column(col)
    for name in sorted(cfg.roles):
        r = cfg.role(name)
        table.add_row(name, r.backend, r.model, str(r.effort or "关"), str(r.window or "默认"), r.fallback or "—")
    console.print(table)
    routes = {(r.backend, r.model) for r in cfg.roles.values()} | {config.route(r.fallback) for r in cfg.roles.values() if r.fallback}
    if any(b == "claude" for b, _ in routes):
        found = shutil.which("claude")
        console.print(f"{'✓' if found else '·'} claude CLI: {found or '未找到（用 Agent SDK 自带的 CLI）'}")
    if any(b == "codex" for b, _ in routes):
        found = shutil.which("codex")
        console.print(f"{'✓' if found else '✗'} codex CLI: {found or '未找到（npm i -g @openai/codex，再 codex login）'}")
        ok &= bool(found)
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


def _count(x: float) -> str:
    return f"{x / 1e6:.2f}M" if x >= 1e6 else f"{x / 1e3:.1f}k" if x >= 1e3 else f"{x:.0f}"


def _usd(x: float) -> str:
    return f"${x:.2f}" if x >= 0.995 else f"${x:.4f}"


@app.command(name="trace")
def trace_cmd(
    run: Annotated[str | None, typer.Option(help="只看一次运行：运行编号（autoplay 用存档名），last 表示最近一次")] = None,
    day: Annotated[str | None, typer.Option(help="哪天的追踪，如 2026-10-18；all 为全部；默认最近一天")] = None,
    by: Annotated[str, typer.Option(help="按什么汇总：agent / model / backend / run")] = "agent",
    runs: Annotated[bool, typer.Option("--runs", help="列出每次运行")] = False,
) -> None:
    """汇总模型调用追踪：每个 Agent 的调用、token、缓存命中、延迟、修复、重试、降级、错误和花费。"""
    import datetime as dt

    from rich import box
    from rich.table import Table

    from .harness import trace

    rows = trace.read("all" if day is None and run not in (None, "last") else day)
    name = ""
    if run:
        name = trace.runs(rows)[-1]["run"] if run == "last" and rows else run
        rows = [r for r in rows if r.get("run", "?") == name]
    if not rows:
        console.print(f"没有追踪记录（{config.SAVES / 'traces'}）")
        raise typer.Exit(1)
    if runs:
        table = Table(box=box.SIMPLE_HEAD, padding=(0, 1))
        for col in ("运行", "开始", "结束", "调用", "错误", "花费"):
            table.add_column(col, justify="left" if col == "运行" else "right", no_wrap=col != "运行", overflow="fold")
        for r in trace.runs(rows):
            label = "（没有编号）" if r["run"] == "?" else r["run"]
            table.add_row(label, r["start"].replace("T", " "), r["end"][11:], str(r["calls"]), str(r["errors"] or ""), _usd(r["cost"]))
        console.print(table)
        return
    groups, total = trace.summarize(rows, by), trace.summarize(rows, None)[0]
    if run:
        first, last = dt.datetime.fromisoformat(rows[0]["at"]), dt.datetime.fromisoformat(rows[-1]["at"])
        span = (last - first).total_seconds() + rows[0].get("seconds", 0)
        scope = f"运行 {name} · 历时 {span // 60:.0f} 分 {span % 60:.0f} 秒"
    else:
        scope = f"{rows[0]['at'][:10]} 至 {rows[-1]['at'][:10]}" if day == "all" else rows[0]["at"][:10]
    console.print(f"[bold]{scope}[/] · {total['calls']} 次调用 · 调用耗时合计 {total['seconds']:.0f}s · 花费 {_usd(total['cost'])}")
    console.print(
        f"输入 {_count(total['input'])} token（缓存命中 {total['cache_rate']:.0%}）· 输出 {_count(total['output'])} token"
        + (f" · 思考 {_count(total['thinking'])} 字" if total["thinking"] else "")
    )
    extra = [(k, h) for k, h in (("repairs", "修复"), ("retries", "重试"), ("fallbacks", "降级"), ("errors", "错误")) if total[k]]
    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1))
    table.add_column({"agent": "Agent", "model": "模型", "backend": "后端", "run": "运行"}.get(by, by), overflow="fold")
    for col in ("调用", "输入", "缓存", "输出", *(h for _, h in extra), "延迟 p50/p95", "花费"):
        table.add_column(col, justify="right", no_wrap=True)
    for i, g in enumerate([*groups, total]):
        if i == len(groups):
            table.add_section()
        key = g["key"].split(":", 1)[-1] if by == "model" else "（没有编号）" if g["key"] == "?" and by == "run" else g["key"]
        table.add_row(
            key, str(g["calls"]), _count(g["input"]), f"{g['cache_rate']:.0%}" if g["cached"] else "—", _count(g["output"]),
            *(str(g[k]) if g[k] else "" for k, _ in extra), f"{g['p50']:.1f}/{g['p95']:.1f}s", _usd(g["cost"]),
            style="bold" if i == len(groups) else None,
        )
    console.print(table)
    if any(r.get("backend") == "claude" for r in rows):
        console.print("[dim]Claude Code 的花费是它报告的等价 API 价，订阅登录时不另外计费；DeepSeek 按峰时价估算。[/]")


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
    file: Annotated[Path, typer.Argument(help="模组文件：PDF / Word / Markdown / 纯文本，或装着它们的 .zip")],
    id: Annotated[str | None, typer.Option("--id", help="模组的英文短名，如 zhuishuren；不填就按文件名取，中文转拼音")] = None,
    pages: Annotated[str | None, typer.Option(help="只取这些页，如 7-18")] = None,
) -> None:
    """导入 Agent 读模组、写 module.yaml、画彩色 ASCII 图，校验通过后编译成 Skill 包（模型看 importer 角色）。"""
    import tempfile

    import click

    from .module import sources
    from .module.importer import run_import

    module_id = id or sources.module_id(file.stem)
    if not id:
        console.print(f"模组短名：{module_id}（用 --id 换一个）")
    if file.suffix.lower() not in sources.ARCHIVES:
        asyncio.run(run_import(file, module_id, pages=pages, console=console))
        return
    with tempfile.TemporaryDirectory(prefix="autokeeper-zip-") as tmp:
        found = sources.unpack(file, Path(tmp))
        if not found.documents:
            console.print(f"[red]{file.name} 里没有 PDF、Word、Markdown 或纯文本文件[/]")
            raise typer.Exit(1)
        doc = found.documents[0]
        if len(found.documents) > 1:
            for i, d in enumerate(found.documents, 1):
                console.print(f"  {i}. {d.relative_to(tmp).as_posix()}")
            doc = found.documents[typer.prompt("用哪一份作模组正文", default=1, type=click.IntRange(1, len(found.documents))) - 1]
        asyncio.run(run_import(doc, module_id, pages=pages, images=found.images, console=console))


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
    record: Annotated[Path | None, typer.Option(help="把这局录成录像：每个 Agent 的回复和每一次掷骰")] = None,
    replay: Annotated[Path | None, typer.Option(help="回放一盘录像，不调用任何模型")] = None,
) -> None:
    """无界面自动跑团：全部由 AI 调查员行动，输出跑团记录。"""
    from .engine.autoplay import run

    summary = asyncio.run(run(module, party=party, max_turns=turns, slot=slot, console=console, record=record, replay=replay))
    if replay is not None:
        from .harness.replay import cassette

        same = summary["fingerprint"] == cassette(replay).summary
        console.print(f"{'[green]回放与录像一致[/]' if same else '[red]回放与录像不一致[/]'}：{replay}")
        raise typer.Exit(0 if same else 1)


eval_app = typer.Typer(help="评测")
app.add_typer(eval_app, name="eval")


@eval_app.command("rhetoric")
def eval_rhetoric(
    module: Path = Path("modules/zhuishuren"),
    limit: int | None = None,
    referee: Annotated[list[str] | None, typer.Option(help="裁定员模型，可写多个；“模型@强度”比较思考强度，如 deepseek:deepseek-flash@low")] = None,
    out: Annotated[Path | None, typer.Option(help="明细写到哪里")] = None,
) -> None:
    """修辞诱导对照：单 Agent 裁定 vs 叙事与裁定分离。"""
    from .eval.rhetoric import run as run_rhetoric

    out = out or config.SAVES / "eval" / "rhetoric.json"
    kw = {"referees": tuple(referee)} if referee else {}
    summary = asyncio.run(run_rhetoric(module, out, limit=limit, **kw))
    console.print_json(data=summary)
    console.print(f"明细：{out}")


@eval_app.command("lore")
def eval_lore(
    module: Path = Path("modules/zhuishuren"),
    n: int = 24,
    only: Annotated[list[str] | None, typer.Option(help="只重跑这些策略，其余沿用上次的结果")] = None,
) -> None:
    """原文细节问答：总览 / 整本 / 向量检索 / GraphRAG（LightRAG mix）四种上下文的准确率与上下文大小。"""
    from .eval.lore import STRATEGIES
    from .eval.lore import run as run_lore

    out = config.SAVES / "eval" / f"lore_{module.name}.json"
    console.print_json(data=asyncio.run(run_lore(module, out, n=n, only=tuple(only or STRATEGIES))))
    console.print(f"明细：{out}")


@eval_app.command("cliche")
def eval_cliche(files: Annotated[list[Path], typer.Argument(help="存档、autoplay 记录，或 AUTOKEEPER_TRACE=full 记下的追踪")]) -> None:
    """数守秘人叙事里的套话：不是……而是……、仿佛、一丝、某种、似乎等，每千字多少次。"""
    from rich import box
    from rich.table import Table

    from .eval.style import CLICHES, CORE, count, narration

    stats = {f.name: count(narration(f)) for f in files}
    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1))
    table.add_column("每千字")
    for name in stats:
        table.add_column(name, justify="right", overflow="fold")
    for phrase in CLICHES:
        table.add_row(phrase, *(f"{s['per_1000'][phrase]:.2f}（{s['hits'][phrase]}）" for s in stats.values()),
                      style="bold" if phrase in CORE else "dim")  # fmt: skip
    table.add_section()
    table.add_row("前五项合计", *(f"{s['density']:.2f}（{s['core']}）" for s in stats.values()), style="bold")
    table.add_row("全部套话", *(f"{s['all']:.2f}" for s in stats.values()))
    table.add_row("比喻和揣测（像）", *(f"{s['similes_per_1000']:.2f}（{s['similes']}）" for s in stats.values()))
    table.add_row("叙事段数 / 字数", *(f"{s['narrations']} / {s['chars']}" for s in stats.values()))
    console.print(table)
    if not any(s["narrations"] for s in stats.values()):
        console.print("[dim]没找到守秘人的叙事：追踪要用 AUTOKEEPER_TRACE=full 记录才有原文。[/]")


@eval_app.command("style")
def eval_style(
    module: Path = Path("modules/zhuishuren"),
    label: Annotated[str, typer.Option(help="这次运行的名字，如 before / after")] = "",
    runs: Annotated[int, typer.Option(help="跑几遍")] = 1,
) -> None:
    """守秘人文风：同样的行动、裁定、NPC 台词和骰子，只有守秘人是真的，数它的套话密度。"""
    from .eval.style import run as run_style

    async def go() -> None:  # one event loop for every run: the API clients outlive a single run
        for i in range(runs):
            name = f"{label or 'run'}-{i + 1}"
            out = config.SAVES / "eval" / f"style-{name}.json"
            s = await run_style(module, label=name, out=out)
            u = s["usage"]
            console.print(
                f"[bold]{name}[/] 守秘人 {s['keeper']} · 提示词 {s['prompt']} · {s['narrations']} 段 {s['chars']} 字 · "
                f"套话 {s['core']} 处，每千字 {s['density']:.2f}（全部 {s['all']:.2f}）· 比喻 {s['similes_per_1000']:.2f} · "
                f"{int(u.get('calls', 0))} 次调用 ${u.get('cost_usd', 0.0):.4f}"
            )
            console.print(f"明细：{out}")

    asyncio.run(go())


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
