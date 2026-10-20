"""Command line: `autokeeper` with no arguments opens the game."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import artkit, config, paths

app = typer.Typer(help="AutoKeeper：自动化 CoC 7 版中文跑团", no_args_is_help=False, add_completion=False)
console = Console()


def _version(value: bool) -> None:
    if value:
        from . import __version__

        print(f"autokeeper {__version__}")
        raise typer.Exit()


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
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True, help="显示版本号")] = False,
) -> None:
    # Environment, not globals: the browser server and every config.load() see the same choice.
    for name, value in (("PRESET", preset), ("MODEL", model), ("SEARCH", search), ("BUDGET_USD", budget), ("FALLBACK", fallback), ("IMPROV", improv), ("MODE", mode)):
        if value is not None:
            os.environ[f"AUTOKEEPER_{name}"] = str(value)
    paths.adopt_old()
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
    """Run a full-screen app with file descriptor 2 sent to data/logs/stderr.log: C libraries
    (numba, onnxruntime) write warnings there directly, over the game. Textual's Unix
    driver writes to sys.__stderr__, so keep that stream connected to the terminal.
    A crash's traceback lands in the log and is shown once the screen is back."""
    log = paths.stderr_log()
    original = sys.__stderr__
    with contextlib.ExitStack() as stack:
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            start = log.stat().st_size if log.exists() else 0
            sink = stack.enter_context(open(log, "a", encoding="utf-8"))
            sys.stderr.flush()
            terminal = stack.enter_context(os.fdopen(os.dup(2), "w", encoding="utf-8", buffering=1))
            os.dup2(sink.fileno(), 2)
        except OSError:  # no saves folder, an odd terminal: run as is
            app.run()
            return
        try:
            sys.__stderr__ = terminal
            app.run()
        finally:
            terminal.flush()
            sys.stderr.flush()
            sys.__stderr__ = original
            os.dup2(terminal.fileno(), 2)
    if app.return_code:
        with open(log, encoding="utf-8", errors="replace") as f:
            f.seek(start)
            sys.stderr.write(f.read())


def _intro() -> None:
    try:
        from terminaltexteffects.effects.effect_decrypt import Decrypt

        from .tui.drawing import art

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
    from .net.web import Page, command

    os.environ["COLORTERM"] = "truecolor"  # the browser's terminal shows 24-bit colour; the game it starts inherits this
    Page(command("play", "--no-intro"), port=port, title="AutoKeeper").serve()


@app.command()
def host(
    module: Annotated[Path, typer.Argument(help="模组目录")] = Path("modules/zhuishuren"),
    seats: Annotated[str, typer.Option(help="按预设调查员的顺序填“玩家 / AI / 不上场”，逗号分隔；多填的“玩家”是空座位，玩家自己带角色；默认第一位玩家，其余 AI")] = "",
    port: Annotated[int, typer.Option(help="网页端口：朋友用浏览器打开")] = 8000,
    hub_port: Annotated[int, typer.Option(help="联机端口：终端里 autokeeper join 连这个")] = 8765,
    load: Annotated[str | None, typer.Option(help="接着玩一个存档（存档名）")] = None,
    web_ui: Annotated[bool, typer.Option("--web/--no-web", help="同时开网页版")] = True,
) -> None:
    """开局域网房间，和标题画面的「开房间」一样。"""
    from rich.cells import cell_len

    from .net import room as lan

    plan = [x.strip() for x in seats.replace("，", ",").split(",") if x.strip()] or None
    try:
        state = lan.saved(load) if load else None
    except (OSError, ValueError):
        console.print(f"[red]读不了存档 {load}（存档在 {paths.games()}）[/]")
        raise typer.Exit(1) from None
    room = lan.Room(module, seats=plan, state=state, port=hub_port, web_port=port)
    try:
        hub = room.open()
        if web_ui:
            room.start_web()  # its log lines show here
    except lan.RoomError as exc:
        room.close()
        console.print(f"[red]{exc}。[/]" + (f"[dim]{exc.detail}[/]" if exc.detail else ""))
        raise typer.Exit(1) from None
    rows = [(ip, label) for ip, label in room.shared["addresses"]]
    console.print(f"[bold]《{hub.m.title}》的房间开好了[/]")
    for inv in hub.state.investigators:
        who = "玩家" if inv.id in hub.sits else "AI"
        console.print(f"  {who}{' ' * (6 - cell_len(who))}{inv.name}（{inv.occupation}）")
    for _ in hub.blank:
        console.print("  玩家  空座位（自己带角色）")
    if web_ui:
        console.print("网页版")
        for ip, label in rows:
            console.print(f"  [bold]{lan.page_url(ip, port)}[/]  {label}")
    target = lan.join_target(rows[0][0], room.port)
    console.print(f"加入房间 [bold]{target}[/]（终端里 uv run autokeeper join {target}）")
    console.print(f"房主自己要玩就另开一个终端，运行 [bold]uv run autokeeper join {lan.join_target('127.0.0.1', room.port)}[/]，进来的是房主，可以按 F9 换模型。")
    if room.shared.get("hint"):
        console.print(f"[yellow]{room.shared['hint']}[/]")
    players = len(hub.sits)
    if players and hub.bringing:
        console.print(f"[dim]每轮 {lan.round_size(players)} 条行动。每位玩家可以在准备房间里换成自己的角色，都准备好了就开局。[/]")
    elif players:
        console.print(f"[dim]每轮 {lan.round_size(players)} 条行动。人齐了自动开局，也可以在准备房间里现在开始，空座交给 AI。[/]")
    console.print(f"[dim]每回合自动存档，下次用 --load {hub.state.slot} 接着玩。Ctrl+C 关闭房间。[/]")
    try:
        room.wait()
    except KeyboardInterrupt:
        pass
    finally:
        console.print("[dim]正在关闭房间……[/]")
        room.close()


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
    bridge = paths.ROOT / "bridge"
    console.print("[bold]1/3[/] 安装 BCDice（npm）…")
    npm = shutil.which("npm") or "npm"  # npm.cmd on Windows
    subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=bridge, check=True)
    console.print("[bold]2/3[/] 编译模组 Skill 包…")
    from .module.compiler import compile_module
    from .module.schema import Module

    for path in paths.module_folders():
        root = compile_module(Module.load(path), paths.SKILLS, source=path / "source")
        console.print(f"    {root.relative_to(paths.ROOT)}")
    console.print("[bold]3/3[/] 链接 .claude/skills 与 .agents/skills …")
    for dot in (".claude", ".agents"):
        target = paths.ROOT / dot / "skills"
        target.parent.mkdir(exist_ok=True)
        if target.is_dir():
            continue
        if target.exists() or target.is_symlink():  # e.g. a git symlink checked out as a text file on Windows
            target.unlink()
        try:
            target.symlink_to(Path("..") / "skills", target_is_directory=True)
        except OSError:  # Windows without developer mode: copy instead of linking
            shutil.copytree(paths.SKILLS, target)
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
    from . import imagery

    found = shutil.which("chafa")
    console.print(f"{'✓' if found else '✗'} chafa: {found or '未找到：场景和头像只能显示手绘字符画，联机时朋友的终端也一样。安装：' + imagery.install_hint()}")
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
    for name in sorted(cfg.role_names):
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
    from . import imagegen

    off, codex = imagegen.why_off(cfg), imagegen.status()
    console.print(f"{'✓' if not off else '·'} AI 画图（Codex）：" + (
        f"可用，每个模组最多 {imagegen.LIMIT} 张，每局最多 {imagegen.PER_GAME} 张" if not off else off))
    if off and codex.fix:
        console.print(f"    {codex.fix}")
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
        console.print(f"没有追踪记录（{paths.traces()}）")
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
    """校验模组：引用、可达性、线索冗余、ASCII 图和配色蒙版，再不用模型推演一遍：走不到的地方、到不了的结局、只靠一处的结论。"""
    from .module import graph
    from .module.importer import validate_all
    from .module.schema import Module
    from .module.simulate import playthrough

    m = Module.load(module)
    errors = asyncio.run(validate_all(module))
    issues = errors + [str(i) for i in graph.audit(m) if i.level != "error"] + [f"[推演] {i}" for i in playthrough(m).issues]
    for i in issues:
        console.print(i)
    console.print(f"{'[red]有错误[/]' if errors else '[green]通过[/]'}：{len(issues)} 条提示，{len(errors)} 条需要修")
    raise typer.Exit(1 if errors else 0)


@app.command(name="compile")
def compile_cmd(module: Annotated[Path, typer.Argument(help="模组目录")]) -> None:
    """把模组编译成 Agent Skill 包（渐进式披露）。"""
    from .module.compiler import compile_module
    from .module.importer import validate_all
    from .module.schema import Module

    if issues := asyncio.run(validate_all(module)):
        for issue in issues:
            console.print(issue)
        raise typer.Exit(1)
    root = compile_module(Module.load(module), paths.SKILLS, source=module / "source")
    console.print(f"已生成 {root}")


plugin_app = typer.Typer(help="规则插件")
app.add_typer(plugin_app, name="plugin")


@plugin_app.command("list")
def plugin_list(module: Annotated[Path, typer.Argument(help="模组目录")]) -> None:
    """列出模组里的规则插件：名称、版本、钩子、自定义动作。"""
    from .module.schema import Module

    m = Module.load(module)
    if not m.plugins:
        console.print("这个模组没有规则插件。")
        return
    for identity in m.plugins:
        plugin = m.extensions.get(identity)
        if plugin is None:
            console.print(f"[red]{identity}[/]：{m.extension_errors.get(identity, '未载入')}")
            continue
        console.print(f"[bold]{plugin.name}[/] [dim]{identity} v{plugin.version}[/]")
        console.print(f"  {plugin.description}")
        console.print(f"  钩子：{'、'.join(plugin.hooks)}；能力：{'、'.join(plugin.capabilities) or '无'}")
        for a in plugin.actions:
            console.print(f"  动作 {a.name}：{a.description}")


@plugin_app.command("test")
def plugin_test(path: Annotated[Path, typer.Argument(help="插件目录（含 manifest.json）或模组目录")]) -> None:
    """在沙箱里实际运行插件的测试。"""
    import json

    from .module.schema import Module
    from .plugins.runtime import verify
    from .plugins.schema import Plugin

    if (path / "manifest.json").exists():
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        code = (path / "plugin.py").read_text(encoding="utf-8")
        plugins = [Plugin.model_validate({**manifest, "code": code})]
    else:
        plugins = list(Module.load(path).extensions.values())
    if not plugins:
        console.print("没有找到可测试的插件。")
        raise typer.Exit(1)
    failed = 0
    for plugin in plugins:
        problems = asyncio.run(verify(plugin))
        if problems:
            failed += 1
            console.print(f"[red]{plugin.id} v{plugin.version} 未通过[/]：")
            for problem in problems:
                console.print(f"  - {problem}")
        else:
            console.print(f"[green]{plugin.id} v{plugin.version}[/]：{len(plugin.tests)} 项测试通过")
    raise typer.Exit(1 if failed else 0)


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
    codex: Annotated[bool, typer.Option("--codex/--no-codex", help="找不到照片时用 AI 画，并画出模组里写的变样图（只用于本机导入的模组）")] = True,
) -> None:
    """从 Wikimedia Commons 找真实图片（公有领域 / CC），读图员挑图裁剪，存成 art/<名字>.jpg 并记录署名。"""
    from . import imagegen
    from .imagery import fetch_folder
    from .imagery import targets as photo_targets
    from .module.schema import Module

    cfg, m = config.load(), Module.load(module)
    targets = photo_targets(m)
    if only:
        targets = [t for t in targets if t[0] in only]
    fill = imagegen.filler(cfg, m, module / "art") if codex else None

    async def run() -> tuple[list[str], list[str]]:
        done = await fetch_folder(cfg, module / "art", targets, force=force, log=console.print, generate=fill)
        drawn = await imagegen.looks(cfg, m, module / "art", log=console.print) if fill is not None and not only else []
        return done, drawn

    done, drawn = asyncio.run(run())
    console.print(f"找到 {len(done)}/{len(targets)} 张图" + (f"，变样图 {len(drawn)} 张" if drawn else "")
                  + (f"（这个模组还能用 AI 画 {imagegen.left(m.id)} 张）" if fill else ""))


@app.command()
def showcase(module: Annotated[str, typer.Argument(help="模组目录或 id，如 zhuishuren")]) -> None:
    """提前准备模组的大场景图（能用 AI 画图时一张一张画，开场优先）。"""
    from . import imagegen
    from .engine.showcase import prepare_module
    from .module.schema import Module

    path = Path(module)
    if not (path / "module.yaml").is_file():
        path = next((p for p in paths.module_folders() if p.name == module), path)
    if not (path / "module.yaml").is_file():
        console.print(f"[red]找不到模组：{module}[/]")
        raise typer.Exit(1)
    m, cfg = Module.load(path), config.load()
    if not m.showcases:
        console.print(f"《{m.title}》没有大场景")
        return
    off = imagegen.why_off(cfg)
    console.print(f"[bold]大场景[/] 《{m.title}》{len(m.showcases)} 个，"
                  + ("AI 一张一张画，每张大约一分钟" if not off else f"{off}，只用模组里的图和找到的照片"))
    done = asyncio.run(prepare_module(cfg, m, log=console.print))
    console.print(f"准备好 {len(done)}/{len(m.showcases)} 个" + ("" if off else f"；这个模组还能用 AI 画 {imagegen.left(m.id)} 张"))


@app.command()
def upgrade(
    module: Annotated[str, typer.Argument(help="模组目录或 id，如 zhuishuren")],
    only: Annotated[str, typer.Option(help="只跑这几步，逗号分开：npcs,relations,agendas,meet,foreshadow,clueweb,ties,endings,mechanics,history,prelude")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只看打算补多少，不写文件")] = False,
    review: Annotated[bool, typer.Option("--review", help="补完后请模型像守秘人那样审读一遍（一次调用），列出跑起来会出问题的地方")] = False,
) -> None:
    """编译期改编：给模组补上跑团用得着、原文没写的东西（人物、冲突图、各人的打算、伏笔、线索网、私人牵连、额外结局、插件清单）。只补没有的，原作不改；中断了再跑一次就接着补。"""
    from .module import adapt
    from .module.schema import Module
    from .module.simulate import playthrough

    path = Path(module)
    if not (path / "module.yaml").is_file():
        path = next((p for p in paths.module_folders() if p.name == module), path)
    if not (path / "module.yaml").is_file():
        console.print(f"[red]找不到模组：{module}[/]")
        raise typer.Exit(1)
    chosen = tuple(x.strip() for x in only.split(",") if x.strip()) or adapt.PASSES
    if unknown := [x for x in chosen if x not in adapt.PASSES]:
        console.print(f"[red]没有这一步：{'、'.join(unknown)}（可选：{','.join(adapt.PASSES)}）[/]")
        raise typer.Exit(1)
    before = adapt.score(Module.load(path))
    asyncio.run(adapt.adapt(path, passes=chosen, log=console.print, dry=dry_run))
    after = adapt.score(Module.load(path))
    console.print(f"[bold]可玩度[/] {before.total} → {after.total} / 100")
    for name, (got, out_of, why) in after.parts.items():
        console.print(f"  {name} {got}/{out_of}  [dim]{why}[/]")
    run = playthrough(Module.load(path))
    if found := run.issues:
        console.print(f"[bold]推演[/]（不用模型，把能走的路都走一遍）{len(found)} 处")
        for line in found:
            console.print(f"  · {line}")
    if run.drift.timeline or run.drift.endings:
        from .engine.state import format_time

        names = {n.id: n.name for n in Module.load(path).npcs}
        console.print("[bold]没人插手时[/]（各人的打算按时间走一遍，不用模型）")
        for d in run.drift.timeline[:12]:
            console.print(f"  {format_time(d.t)} {names.get(d.npc, d.npc)}{d.text}")
        for ending, t in run.drift.endings.items():
            console.print(f"  {format_time(t)} 走到结局 {ending}")
    if review:
        found = asyncio.run(adapt.review(path))
        console.print(f"[bold]审读[/] {len(found.findings)} 条")
        for f in found.findings:
            console.print(f"  [{'red' if f.severity == '高' else 'yellow'}]{f.severity}[/] {f.where}：{f.problem}\n     [dim]补：{f.fix}[/]")


@art_app.command("credits")
def art_credits(module: Path) -> None:
    """列出模组里真实图片的来源与授权。"""
    from .credits import listing

    for c in listing(module / "art"):
        console.print(f"{c['name']}: " + "  ".join(str(c.get(k) or "") for k in ("title", "license", "artist", "page", "source")).strip())


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
    model: Annotated[str, typer.Option(help="这次导入的模型，如 codex:default、deepseek:deepseek-flash")] = "",
    vision_model: Annotated[str, typer.Option(help="这次导入的读图模型；留空沿用 vision 配置")] = "",
    photos: Annotated[bool, typer.Option("--photos/--no-photos", help="导完去找真实照片转成字符画（同 art fetch）")] = True,
    fresh: Annotated[bool, typer.Option("--fresh", help="上次没导完也从头来；默认同一个文件接着上次做")] = False,
) -> None:
    """导入 Agent 读模组、写 module.yaml、画彩色 ASCII 图，校验通过后编译成 Skill 包（模型看 importer 角色）。同一个文件再导一次，从上次停下的地方接着做。"""
    import tempfile

    import click

    from .module import sources
    from .module.importer import run_import

    module_id = id or sources.module_id(file.stem)
    models: dict = {key: value for key, value in (("model", model), ("vision_model", vision_model)) if value}
    models["photos"] = photos
    models["fresh"] = fresh
    if not id:
        console.print(f"模组短名：{module_id}（用 --id 换一个）")
    if file.suffix.lower() not in sources.ARCHIVES:
        asyncio.run(run_import(file, module_id, pages=pages, console=console, **models))
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
        asyncio.run(run_import(doc, module_id, pages=pages, images=found.images, console=console, **models))


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


@eval_app.callback()
def eval_main(
    prompts: Annotated[Path | None, typer.Option(help="换一组提示词再评测：YAML 文件，写法见 eval/variants.py；不改任何文件")] = None,
) -> None:
    """评测。加 --prompts 时，这一次评测用文件里写的提示词变体。"""
    if prompts is not None:
        from .eval import variants

        v = variants.load(prompts)
        variants.apply(v)
        console.print(f"提示词变体：{v.name}（{v.digest}）{v.note}，换了 {'、'.join(v.texts)}")


@eval_app.command("rhetoric")
def eval_rhetoric(
    module: Path = Path("modules/zhuishuren"),
    limit: int | None = None,
    referee: Annotated[list[str] | None, typer.Option(help="裁定员模型，可写多个；“模型@强度”比较思考强度，如 deepseek:deepseek-flash@low")] = None,
    out: Annotated[Path | None, typer.Option(help="明细写到哪里")] = None,
    system: Annotated[list[str] | None, typer.Option(help="比哪几种：baseline（单 Agent 读原话）、split（裁定员只看解析后的行动）")] = None,
) -> None:
    """修辞诱导对照：单 Agent 裁定 vs 叙事与裁定分离。"""
    from .eval.rhetoric import run as run_rhetoric

    out = out or paths.dev_output("eval", "rhetoric.json")
    kw: dict = {"referees": tuple(referee)} if referee else {}
    if system:
        kw["systems"] = tuple(system)
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

    out = paths.dev_output("eval", f"lore_{module.name}.json")
    console.print_json(data=asyncio.run(run_lore(module, out, n=n, only=tuple(only or STRATEGIES))))
    console.print(f"明细：{out}")


def _style_table(stats: dict[str, dict], *, columns: bool = True):
    """What eval cliche and eval style show: each file (or run) against the human rates, and
    with several of them, their mean and standard deviation. `columns`: one per file too."""
    from rich import box
    from rich.table import Table

    from .eval.style import HUMAN_CV, HUMAN_SHORT, PATTERNS, SIMILE, spread

    runs = list(stats.values())
    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1))
    table.add_column("每千字（×人类）")
    table.add_column("人类", justify="right")
    for name in stats if columns else ():
        table.add_column(name, justify="right", overflow="fold")
    if len(runs) > 1:
        table.add_column(f"{len(runs)} 份：均值 ± 标准差", justify="right")

    def row(label: str, human: str, values: list[float], cell, mean=None, **kw) -> None:
        cells = [cell(v, s) for v, s in zip(values, runs, strict=True)] if columns else []
        if len(runs) > 1:
            m, sd = spread(values)
            cells.append(mean(m, sd) if mean else f"{m:.2f} ± {sd:.2f}")
        table.add_row(label, human, *cells, **kw)

    if any(s["narrations"] for s in runs):
        quiet = [p for p in PATTERNS if not any(s["hits"][p] for s in runs)]
        for p, (_, human) in PATTERNS.items():
            if p in quiet:
                continue
            row(p, f"{human:.2f}", [s["per_1000"][p] for s in runs], lambda v, s, p=p: f"{v:.2f}（×{s['ratio'][p]:g}）",
                lambda m, sd, h=human: f"{m:.2f} ± {sd:.2f}（×{m / max(h, 0.008):.1f}）")  # fmt: skip
        if quiet:
            table.add_row(f"其余 {len(quiet)} 项", "", *([""] * (len(table.columns) - 2)), style="dim")
        table.add_section()
        row("超出人类的部分", "0", [s["excess"] for s in runs], lambda v, s: f"{v:.2f}", style="bold")
        row("比喻和揣测（像）", f"{SIMILE[1]:.2f}", [s["similes_per_1000"] for s in runs], lambda v, s: f"{v:.2f}")
        row("句长变化", f"{HUMAN_CV:.2f}", [s["sentence_cv"] for s in runs], lambda v, s: f"{v:.2f}")
        row("五字以内的句子", f"{HUMAN_SHORT:.2f}", [s["short"] for s in runs], lambda v, s: f"{v:.2f}")
        row("照搬示范的词", "", [sum(s["borrowed"].values()) for s in runs],
            lambda v, s: "、".join(f"{w}{n}" for w, n in list(s["borrowed"].items())[:2]) or "0")  # fmt: skip
        row("沿用上一段的词", "", [sum(s["carried"].values()) for s in runs],
            lambda v, s: "、".join(f"{w}{n}" for w, n in list(s["carried"].items())[:2]) or "0")  # fmt: skip
        row("段数 / 字数", "", [s["chars"] for s in runs], lambda v, s: f"{s['narrations']} / {s['chars']}",
            lambda m, sd: f"{m:.0f} ± {sd:.0f} 字")  # fmt: skip
    if any(s.get("voice") for s in runs):
        table.add_section()
        npc = [s.get("voice") or {"lines": 0, "line_chars": 0, "action_first": 0.0, "action_first_later": 0.0, "repeats": 0.0} for s in runs]

        def share(key: str) -> list[float]:
            return [v[key] for v in npc]

        def pct(v: float, s: dict) -> str:
            return f"{v:.0%}"

        row("NPC 台词（句）", "", share("lines"), lambda v, s: f"{v:.0f}", lambda m, sd: f"{m:.0f} ± {sd:.0f}")
        row("动作开头", "", share("action_first"), pct, lambda m, sd: f"{m:.0%} ± {sd:.0%}")
        row("第二句起动作开头", "", share("action_first_later"), pct, lambda m, sd: f"{m:.0%} ± {sd:.0%}")
        row("两句内重复口癖", "", share("repeats"), pct, lambda m, sd: f"{m:.0%} ± {sd:.0%}")
        row("每句字数", "", share("line_chars"), lambda v, s: f"{v:.0f}", lambda m, sd: f"{m:.0f} ± {sd:.0f}")
    return table


@eval_app.command("cliche")
def eval_cliche(files: Annotated[list[Path], typer.Argument(help="存档、autoplay 记录、AUTOKEEPER_TRACE=full 记下的追踪，或 eval style 的明细")]) -> None:
    """守秘人叙事比照人类小说（鲁迅、老舍）：每种套话是人类用量的几倍、句子长短、照搬示范的词；NPC 的动作开头和口癖重复。"""
    from .eval.style import score

    stats = {f.name: score(f) for f in files}
    console.print(_style_table(stats))
    if not any(s["narrations"] or s["voice"] for s in stats.values()):
        console.print("[dim]没找到守秘人的叙事或 NPC 的台词：追踪要用 AUTOKEEPER_TRACE=full 记录才有原文。[/]")


@eval_app.command("style")
def eval_style(
    module: Path = Path("modules/zhuishuren"),
    label: Annotated[str, typer.Option(help="这次运行的名字，如 before / after")] = "",
    runs: Annotated[int, typer.Option(help="跑几遍；一遍只有两三千字，比较时至少跑 5 遍")] = 1,
    npc: Annotated[bool, typer.Option("--npc", help="改测 NPC：同样的问话问三个 NPC，只有 NPC 是真的")] = False,
) -> None:
    """守秘人文风：同样的行动、裁定、NPC 台词和骰子，只有守秘人是真的，比照人类小说量它的叙事。"""
    from .eval.style import run as run_style
    from .eval.style import score, talk

    done: dict[str, dict] = {}

    async def go() -> None:  # one event loop for every run: the API clients outlive a single run
        for i in range(runs):
            name = f"{label or 'run'}-{i + 1}"
            out = paths.dev_output("eval", f"{'talk' if npc else 'style'}-{name}.json")
            if npc:
                s = await talk(module, label=name, out=out)
                u = s["usage"]
                console.print(
                    f"[bold]{name}[/] NPC {s['npc']} · 提示词 {s['prompt']} · {s['lines']} 句，每句 {s['line_chars']} 字 · "
                    f"动作开头 {s['action_first']:.0%}（第二句起 {s['action_first_later']:.0%}）· 重复 {s['repeats']:.0%} · "
                    f"{int(u.get('calls', 0))} 次调用 ${u.get('cost_usd', 0.0):.4f}"
                )
            else:
                s = await run_style(module, label=name, out=out)
                u = s["usage"]
                console.print(
                    f"[bold]{name}[/] 守秘人 {s['keeper']} · 提示词 {s['prompt']} · {s['narrations']} 段 {s['chars']} 字 · "
                    f"超出人类 {s['excess']:.2f}/千字 · 不是……而是 {s['per_1000']['不是……而是……']:.2f} · "
                    f"句长变化 {s['sentence_cv']:.2f} · {int(u.get('calls', 0))} 次调用 ${u.get('cost_usd', 0.0):.4f}"
                )
            console.print(f"明细：{out}")
            done[name] = score(out)

    asyncio.run(go())
    if len(done) > 1:
        console.print(_style_table(done, columns=len(done) <= 3))


@eval_app.command("blind")
def eval_blind(
    a: Annotated[str, typer.Option(help="对照组：eval style 用的 --label")],
    b: Annotated[str, typer.Option(help="改动组的 --label")],
    npc: Annotated[bool, typer.Option("--npc", help="比 NPC 的问话（eval style --npc 的结果）")] = False,
    judge: Annotated[str, typer.Option(help="裁判模型")] = "claude-opus-5-5",
) -> None:
    """盲评两组 eval style 的结果：同一段两两配对，先后各评一次，裁判不知道哪段是哪组。"""
    from .eval.blind import judge as blind

    out = paths.dev_output("eval", f"blind-{'talk' if npc else 'style'}-{a}-vs-{b}.json")
    s = asyncio.run(blind(a, b, npc=npc, model=judge, out=out))
    console.print(f"{s['pairs']} 对：{b} 胜 {s['b_wins']}，{a} 胜 {s['a_wins']}，相当或换了先后就变 {s['ties']}；符号检验 p = {s['p']:.3f}")
    console.print(f"明细：{out}")


@eval_app.command("context")
def eval_context(modules: list[Path]) -> None:
    """渐进式披露的上下文账本：整本模组 vs 总览 + 当前地点文件。"""
    from .eval.context import ledger

    for m in modules:
        console.print_json(data=ledger(m))


@eval_app.command("experience")
def eval_experience(
    module: Path = Path("modules/zhuishuren"),
    actions: Annotated[Path | None, typer.Option(help="每行一句玩家行动的 UTF-8 文件；默认使用追书人到站与日常路线")] = None,
    turns: Annotated[int, typer.Option(min=1, help="最多体验多少轮")] = 12,
    out: Annotated[Path, typer.Option(help="含全文、逐轮等待、调用量和待审问题的报告")] = paths.dev_output("eval", "experience.json"),
) -> None:
    """用真实模型连续体验开场、闲逛和人情，不将文风分数当成好玩程度。"""
    from .eval.experience import ARRIVAL, run

    lines = [line.strip() for line in actions.read_text(encoding="utf-8").splitlines() if line.strip()] if actions else ARRIVAL
    report = asyncio.run(run(module, lines[:turns], out))
    console.print(f"完成 {report['completed']}/{report['requested']} 轮；报告：{out}")
    if report["error"]:
        console.print(f"[yellow]体验中断：{report['error']}；当前结果与恢复记录已保留。[/]")
        raise typer.Exit(1)


@eval_app.command("session")
def eval_session(
    slot: Annotated[str, typer.Argument(help="存档名，如 zhuishuren-1019-224053")],
    turns: Annotated[str, typer.Option(help="只看这些回合，如 70-80,99")] = "",
    recorded: Annotated[bool, typer.Option("--recorded", help="只核对当时的记录，不重放（只调用核对模型）")] = False,
    parallel: Annotated[int, typer.Option(min=1, max=8, help="同时重放几轮")] = 3,
    out: Annotated[Path | None, typer.Option(help="报告写到哪里；默认 dev/output/eval/session-<存档>-<时间>.json")] = None,
    blind: Annotated[bool, typer.Option("--blind-keeper", help="重放时守秘人看不到底牌，用来和看得到时对照")] = False,
    baseline: Annotated[bool, typer.Option("--baseline/--no-baseline", help="也核对当时的记录；比两组提示词时不需要，省一半核对调用")] = True,
    given: Annotated[Path | None, typer.Option(help="另一份报告：同一轮的行动和裁定照它的用（模组内的才用），比守秘人或 NPC 提示词时两边面对同样的裁定")] = None,
) -> None:
    """拿一局真人存档逐轮重放：从每轮之前的局面、用玩家原话和当时的骰子，跑现在的流程；核对叙述和世界记录对不对得上、有没有说漏秘密，和当时比。"""
    from .eval.session import run

    report = asyncio.run(run(slot, turns=turns, recorded_only=recorded, parallel=parallel, out=out, log=console.print, blind=blind,
                             baseline=baseline, given=given))
    for side, label in (("recorded", "当时"), ("replayed", "现在")):
        got = report["summary"].get(side)
        if not got:
            continue
        line = (f"[bold]{label}[/] {got['turns']} 轮：{got['turns_with_problems']} 轮有矛盾（共 {got['problems']} 处 {got['by_kind']}），"
                f"{got.get('turns_with_leaks', 0)} 轮说漏秘密（共 {got.get('leaks', 0)} 处，照抄原文 {got.get('verbatim_leaks', 0)} 处），"
                f"NPC 说漏 {got.get('turns_with_npc_leaks', 0)} 轮，"
                f"凭空多出的细节 {got.get('findings', {})}，"
                f"换了玩家点名的人 {got['target_swaps']} 轮，NPC 开口后叙述复述 {got['echo_when_npc_spoke']:.0%}，叙述平均 {got['keeper_chars']} 字")
        if "cost_usd" in got:
            line += (f"；每轮 {got['calls_per_turn']} 次调用，模型耗时 {got['model_seconds_per_turn']} 秒，"
                     f"其中玩家要等的 {got['wait_seconds_per_turn']} 秒；共 ${got['cost_usd']}")
        if "woke_pairs" in got:  # each turn starts from the save, so the backstage call count is not a game's
            line += f"；幕后唤醒 {got['woke_pairs']} 种（人物，原因）"
        console.print(line)
        if sc := got.get("scene"):
            npc, fields, arb, clues = got["npc_checks"], got["fields"], got["arbiter"], got["clue_slips"]
            console.print(f"  场面快照 {sc['told']} 次：第一次就通过 {sc['held']}，退回 {sc['sent_back']}，"
                          f"显示后才不通过 {sc['kept_partial']}，读不出 {sc['no_scene']}；第二次仍不通过 {sc['second_failed']}；"
                          f"退回原因 {sc['reasons']}")
            console.print(f"  NPC 回应 {npc['replies']} 次：重问 {npc['reasks']} 次，落进未落实 {npc['unsettled']} 次 {npc['reasons']}；"
                          f"嘴上说了没填字段 {fields['said_missing']} 处，和守秘人场面对不上 {fields['scene_vs_reply']} 处，"
                          f"见面安排给了 {fields['meetings_offered']} 次、填了 played {fields['meetings_marked']} 次")
            console.print(f"  裁定员修 JSON {arb['repairs']} 次，用不上的线索 id {arb['invalid_clues']} 个；"
                          f"检定失败还讲出该得的线索 {clues['missed_told']} 处，讲出没找到的检定线索原文 {clues['early_told']} 处")
    if report["summary"]["failed"]:
        console.print(f"[yellow]{report['summary']['failed']} 轮重放失败，见报告[/]")
    if report["summary"].get("halted"):
        console.print(f"[red]撞到额度上限，后面的回合没有重放：{report['summary']['halted']}[/]")
        raise typer.Exit(2)
    console.print(f"报告：{report['out']}")


@eval_app.command("compare")
def eval_compare(
    a: Annotated[list[Path], typer.Option(help="变体 A 的 eval session 报告，可写多次或用通配符")],
    b: Annotated[list[Path], typer.Option(help="变体 B 的报告")],
    out: Annotated[Path | None, typer.Option(help="比较结果写到哪里")] = None,
) -> None:
    """两组提示词变体的重放报告逐轮配对：每项指标各遍合计的均值 ± 标准差，以及逐轮的符号检验（B 更少算 B 好）。"""
    import glob
    import json

    from rich.table import Table

    from .eval.session import compare

    def reports(paths_: list[Path]) -> list[dict]:
        files = [Path(f) for p in paths_ for f in (sorted(glob.glob(str(p))) or [str(p)])]
        return [json.loads(f.read_text(encoding="utf-8")) for f in files]

    got = compare(reports(a), reports(b))
    table = Table(title=f"{len(got['turns'])} 轮 · A {got['runs'][0]} 遍 · B {got['runs'][1]} 遍", title_justify="left")
    for col in ("指标", "A 每遍", "B 每遍", "B 更少的轮", "B 更多的轮", "p"):
        table.add_column(col)
    for k, v in got["metrics"].items():
        table.add_row(k, f"{v['a'][0]} ± {v['a'][1]}", f"{v['b'][0]} ± {v['b'][1]}", str(v["b_better"]), str(v["b_worse"]), f"{v['p']:.3f}")
    console.print(table)
    console.print(f"文风护栏 A {got['style']['a']}；B {got['style']['b']}")
    if out:
        out.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
        console.print(f"明细：{out}")


@eval_app.command("recheck")
def eval_recheck(reports: Annotated[list[Path], typer.Argument(help="eval session 写的报告，就地改写")]) -> None:
    """按现在的规则重算报告里不经裁判的那几项（规则核对、线索原文），不调用模型。"""
    from .eval.session import recheck

    for path in reports:
        got = recheck(path)["summary"].get("replayed", {})
        console.print(f"{path.name}：规则核对 {got.get('rules')}，线索原文 {got.get('clue_slips')}")


@eval_app.command("rejudge")
def eval_rejudge(report: Annotated[Path, typer.Argument(help="eval session 写的报告")]) -> None:
    """用现在的核对模型重新核对一份 eval session 报告（不重放，只调用核对模型）。"""
    from .eval.session import rejudge

    done = asyncio.run(rejudge(report))
    for side, label in (("recorded", "当时"), ("replayed", "重放")):
        if got := done["summary"].get(side):
            console.print(f"[bold]{label}[/] {got['turns']} 轮：{got['turns_with_problems']} 轮有矛盾（共 {got['problems']} 处 {got['by_kind']}）")
    console.print(f"报告：{done['out']}")


@app.command()
def mcp() -> None:
    """以 MCP 服务运行（给 Claude Code / Codex 的嵌入模式用）。"""
    from .mcp_server import server

    server.run()


if __name__ == "__main__":
    app()
