"""Import a published scenario: PDF / Word / Markdown -> module.yaml + coloured ASCII art -> Skill package.

    prepare ─→ build ─→ validate ─┬─→ compile ─→ END
                         ↑        │
                         └─ fix ←─┘   (at most three rounds)

`prepare` extracts the source here; module generation (generator.py) reuses the same
pipeline with web research as its `prepare` step.

`build` and `fix` are one Claude agent session (Opus, medium effort by default) with
file tools, working inside modules/local/<id>/. It reads the extracted text and the
rendered page images, writes module.yaml, draws every location and portrait as ASCII
art with colour masks, and runs `autokeeper check` itself until the module passes.
Imported material stays in modules/local/ and skills/module-<id>/, both git-ignored.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from rich.console import Console

from .. import artkit, config, skills
from ..llm.backends import ClaudeSession, Session, open_session
from . import graph as module_graph
from .compiler import compile_module
from .schema import Module

MAX_FIX_ROUNDS = 3


class ImportState(TypedDict, total=False):
    work: str
    issues: list[str]
    rounds: int


def _pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return [p for p in out if 1 <= p <= total]


def extract(file: Path, work: Path, pages: str | None) -> tuple[int, int]:
    """Text per page plus page images (PDF), or MarkItDown's Markdown for anything else."""
    src = work / "source"
    if src.exists():
        shutil.rmtree(src)
    (src / "pages").mkdir(parents=True)
    if file.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(file))
        chosen = _pages(pages, len(pdf))
        parts = []
        for n in chosen:
            page = pdf[n - 1]
            text = page.get_textpage().get_text_range().replace("\r\n", "\n")
            parts.append(f"\n\n---------- 第 {n} 页（图片：source/pages/p{n:02d}.jpg）----------\n\n{text.strip()}")
            image = page.render(scale=1.25).to_pil().convert("RGB")
            image.save(src / "pages" / f"p{n:02d}.jpg", quality=72, optimize=True)
        (src / "source.md").write_text("".join(parts).strip() + "\n", encoding="utf-8")
        return len(chosen), sum(len(p) for p in parts)
    from markitdown import MarkItDown

    text = MarkItDown().convert(str(file)).text_content
    (src / "source.md").write_text(text, encoding="utf-8")
    return 0, len(text)


def _prepare(work: Path) -> None:
    """Reference material the agent may read: an example module and the schema."""
    ex = work / "_example"
    ex.mkdir(exist_ok=True)
    shutil.copy(config.EXAMPLE / "module.yaml", ex / "module.yaml")
    for f in (config.EXAMPLE / "art").glob("*"):
        shutil.copy(f, ex / f.name)
    (ex / "schema.json").write_text(json.dumps(Module.model_json_schema(), ensure_ascii=False, indent=1), encoding="utf-8")


def _check_cmd() -> str:
    return f"{sys.executable} -m autokeeper check ."


BRIEF = """你是 AutoKeeper 的模组构建员。把 source/source.md 这份 CoC 7 版模组（页面图片在 source/pages/）
整理成 AutoKeeper 的模组格式，写进当前目录的 module.yaml，并给它画彩色 ASCII 图。

## 格式
- 照着 _example/module.yaml（字段齐全的格式样例）写；字段含义见 _example/schema.json。
- 模组 id 用 "{module_id}"。所有 id 用小写英文或拼音加下划线。
- 只整理模组里有的东西；剧情、真相、线索内容、检定要求、理智损失、结局都按原文，不要改编。
  原文没写的细节（NPC 的说话方式、地点的可调查物）可以合理补全。
- clues 要全：写清在哪（where、feature）、要什么检定（check，技能名用中文，如 侦查、图书馆使用、外语(拉丁文)）、
  指向哪个结论（points_to），推进剧情必需的标 core: true。NPC 口中的线索写 npc，并在该 NPC 的 clues 和 gates 里登记。
- NPC 的 knows / secrets / lies 分清楚，NPC 只知道原文里他知道的事。
- 有时间点的事情写进 events（when 用 "D1 21:00"，或 visit:地点id、flag:标记）；结局 endings 和奖励照原文；
  用到的剧情标记都要在 flags 里声明。
- 原文没有预设调查员的话，按模组的年代和地点设计两名，写进 pregens，backstory 里注明“AutoKeeper 补充”。
- 原文里如果有适合让玩家“画出来”的东西（符号、地图、人像），在 sketches 里写一两个。

## 美术
- 每个地点写 art 字段，在 art/<名字>.txt 画场景图（≤56 列、≤14 行）；每个 NPC 和预设调查员写 portrait，画头像（≤18 列、7 行）。
- 每张图配同名 .color 配色蒙版：与图同形状，每个字符一个颜色键，“.”为默认色。颜色键：{palette}
- 只用 ASCII 字符，自己构图，不要照搬原书插图。_example/ 里有图和蒙版的格式样例。
{art_skill}

## 流程
1. 通读 source/source.md；需要看地图、手稿时用 Read 打开 source/pages/ 下对应页的图片。
2. 写 module.yaml。
3. 画 art/ 下的图和蒙版。
4. 运行 `{check}` 校验，按输出改到没有“错误”和“[图]”为止（“提醒”可以留着）。
5. 最后用三五句话说明：哪些内容是你补充的，哪里原文含糊、你做了什么取舍。"""


def _session(cfg, work: Path) -> Session:
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

    check = _check_cmd()

    readonly = ("ls", "cat ", "head ", "tail ", "wc ")

    async def can_use_tool(name, data, _context):
        if name == "Bash":
            cmd = str(data.get("command", "")).strip()
            if cmd.startswith(check) or (cmd.startswith(readonly) and not any(c in cmd for c in ";&|>`$")):
                return PermissionResultAllow()
            return PermissionResultDeny(message=f"这里只允许只读命令和校验命令：{check}")
        return PermissionResultAllow()

    role = cfg.role("importer")
    if role.backend != "claude":
        return open_session("importer", role, "你是模组构建员。")
    return ClaudeSession(
        "importer",
        role,
        "你是一个仔细、守规矩的 TRPG 模组整理员。所有输出用简体中文。",
        tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        cwd=work,
        extra={"permission_mode": "acceptEdits", "can_use_tool": can_use_tool, "max_turns": 200, "max_buffer_size": 32 * 1024 * 1024},
    )


def validate(work: Path) -> list[str]:
    try:
        m = Module.load(work)
    except Exception as exc:  # malformed YAML or schema errors go straight back to the agent
        return [f"[错误] module.yaml 读不进来：{exc}"[:2000]]
    issues = [str(i) for i in module_graph.audit(m) if i.level == "error"]
    portraits = {n.portrait for n in m.npcs} | {p.portrait for p in m.pregens}
    for name, text in m.art.items():
        issues += [f"[图] {i}" for i in artkit.check(name, text, m.art_colors.get(name), portrait=name in portraits)]
    for name in [loc.art for loc in m.locations if loc.art] + [x for x in portraits if x]:
        if name not in m.art:
            issues.append(f"[图] 缺少 art/{name}.txt")
    return issues


async def pipeline(
    work: Path,
    *,
    brief: str,
    prepare: Callable[[Console], Awaitable[None]] | None = None,
    console: Console | None = None,
) -> Path:
    """prepare → build → validate ⇄ fix → compile, shared by import and generation."""
    console = console or Console()
    cfg = config.load()
    work.mkdir(parents=True, exist_ok=True)
    session = _session(cfg, work)

    async def on_tool(name: str, data: dict) -> None:
        target = data.get("file_path") or data.get("command") or data.get("pattern") or data.get("query") or data.get("url") or ""
        console.print(f"  [dim]· {name}[/] {str(target).replace(str(work) + '/', '')[:100]}")

    async def on_delta(text: str) -> None:
        console.print(text, end="", style="italic", markup=False, highlight=False)

    async def n_prepare(state: ImportState) -> dict:
        if prepare:
            await prepare(console)
        _prepare(work)
        return {"work": str(work), "rounds": 0}

    async def n_build(state: ImportState) -> dict:
        role = cfg.role("importer")
        console.print(f"[bold]构建[/] {role.model}（effort={role.effort}）开始写 module.yaml、画图…")
        await session.send(brief, on_delta=on_delta, on_tool=on_tool)  # type: ignore[call-arg]
        console.print()
        return {}

    async def n_validate(state: ImportState) -> dict:
        issues = validate(work)
        console.print(f"[bold]校验[/] {'通过' if not issues else f'{len(issues)} 个问题'}")
        for i in issues[:20]:
            console.print(f"  {i}")
        return {"issues": issues}

    def after_validate(state: ImportState) -> str:
        if not state.get("issues"):
            return "compile"
        return "fix" if state.get("rounds", 0) < MAX_FIX_ROUNDS else END

    async def n_fix(state: ImportState) -> dict:
        rounds = state.get("rounds", 0) + 1
        console.print(f"[bold]修复[/] 第 {rounds} 轮")
        problems = "\n".join(state.get("issues", []))
        await session.send(  # type: ignore[call-arg]
            f"校验还有这些问题，请逐条修好，再运行 `{_check_cmd()}` 确认：\n{problems}", on_delta=on_delta, on_tool=on_tool
        )
        console.print()
        return {"rounds": rounds}

    async def n_compile(state: ImportState) -> dict:
        m = Module.load(work)
        root = compile_module(m, config.SKILLS)
        console.print(
            f"[bold green]完成[/] 《{m.title}》：{len(m.locations)} 个地点、{len(m.npcs)} 个 NPC、{len(m.clues)} 条线索、"
            f"{len(m.art)} 张 ASCII 图 → {root}"
        )
        return {}

    g = StateGraph(ImportState)
    g.add_node("prepare", n_prepare)
    g.add_node("build", n_build)
    g.add_node("validate", n_validate)
    g.add_node("fix", n_fix)
    g.add_node("compile", n_compile)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "build")
    g.add_edge("build", "validate")
    g.add_conditional_edges("validate", after_validate, ["compile", "fix", END])
    g.add_edge("fix", "validate")
    g.add_edge("compile", END)
    try:
        await g.compile().ainvoke({}, {"recursion_limit": 20})
    finally:
        await session.close()
    return work


async def run_import(file: Path, module_id: str, *, pages: str | None = None, console: Console | None = None) -> Path:
    work = config.LOCAL_MODULES / module_id

    async def prepare(console: Console) -> None:
        n, size = extract(Path(file), work, pages)
        console.print(f"[bold]提取[/] {Path(file).name}：{n} 页，{size} 字 → {work}/source/")

    brief = BRIEF.format(module_id=module_id, palette=artkit.PALETTE_DOC, art_skill=skills.body("ascii-art"), check=_check_cmd())
    return await pipeline(work, brief=brief, prepare=prepare, console=console)
