"""Generate an original module from a one-line seed, grounded by web research.

    research (Claude with WebSearch / WebFetch) ─→ the import pipeline's build ⇄ validate ─→ compile

The researcher writes research.md: dated, sourced fact cards about the era and place
(prices, streets, newspapers, customs, real events). The builder then writes an
original scenario that uses those facts, cites them in `source`, and draws the art.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .. import artkit, config, skills
from ..llm.backends import ClaudeSession
from .importer import _check_cmd, pipeline

RESEARCH = """你是 CoC 跑团模组的考据员。种子：{seed}

用 WebSearch / WebFetch 查这个年代、这个地方的真实细节：街道与建筑、物价与交通、报纸与行业、风俗与俚语、
当时发生过的真实事件，以及可以借用的公有领域怪谈或文学（比如钱伯斯、坡、洛夫克拉夫特已进入公有领域的作品）。
把结果写成 research.md：10–15 张考据卡，每张包含“标题、事实（一两句）、可以怎么用进模组、出处链接”。
只写查得到出处的事实；查不到的写“未核实”。"""

GENERATE = """你是 AutoKeeper 的模组编剧。根据种子和考据，原创一个 CoC 7 版短模组，写进当前目录的 module.yaml，并画彩色 ASCII 图。

种子：{seed}
考据：research.md（先读它；用到的史实在 source 字段里注明出处）

## 设计要求
- 1–3 名调查员，2–3 小时；6–9 个地点，4–6 个 NPC，12–18 条线索。
- 有一个清楚的真相和一个时钟（不处理就会恶化的倒计时事件）。
- 每个结论至少两条独立线索（三线索原则更好），关键线索标 core；至少一个 NPC 会撒谎、需要社交检定才松口。
- 至少一个“让玩家画出来”的 sketches 环节；至少三种结局，由剧情标记触发。
- 全部原创，可以借用公有领域作品的元素，但不要照搬任何受版权保护的模组、怪物设定或插图。
- 模组 id 用 "{module_id}"。格式照着 _example/module.yaml，字段含义见 _example/schema.json。

## 美术
- 每个地点画场景图（≤56 列、≤14 行），每个 NPC 和预设调查员画头像（≤18 列、7 行），各配同形状 .color 蒙版。颜色键：{palette}
{art_skill}

## 流程
写完运行 `{check}`，按输出改到没有“错误”和“[图]”为止。最后用三五句话介绍这个模组的卖点。"""


async def run_generate(seed: str, module_id: str, *, console: Console | None = None) -> Path:
    console = console or Console()
    cfg = config.load()
    work = config.LOCAL_MODULES / module_id

    async def research(console: Console) -> None:
        console.print(f"[bold]考据[/] {seed}")

        async def on_tool(name: str, data: dict) -> None:
            console.print(f"  [dim]· {name}[/] {str(data.get('query') or data.get('url') or data.get('file_path') or '')[:100]}")

        tools = ["WebSearch", "WebFetch", "Write", "Read"]
        session = ClaudeSession(
            "researcher",
            cfg.role("importer"),
            "你是严谨的历史考据员，所有输出用简体中文。",
            tools=tools,
            cwd=work,
            extra={"permission_mode": "acceptEdits", "allowed_tools": tools, "max_turns": 60},
        )
        try:
            await session.send(RESEARCH.format(seed=seed), on_tool=on_tool)
        finally:
            await session.close()
        cards = (work / "research.md").read_text(encoding="utf-8") if (work / "research.md").exists() else ""
        console.print(f"[bold]考据完成[/] research.md {len(cards)} 字")

    work.mkdir(parents=True, exist_ok=True)
    brief = GENERATE.format(
        seed=seed, module_id=module_id, palette=artkit.PALETTE_DOC, art_skill=skills.body("ascii-art"), check=_check_cmd()
    )
    return await pipeline(work, brief=brief, prepare=research, console=console)
