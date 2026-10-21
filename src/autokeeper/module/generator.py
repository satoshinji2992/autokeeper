"""Generate an original module from a one-line seed, grounded by web research.

    research (harness Agent + web tools) ─→ the import pipeline's build ⇄ validate ─→ compile

The researcher is a harness Agent with the harness's own web_search / fetch_url tools,
so it works on any model. With `search = "none"` (no network) it still runs: the cards
come from the model's own knowledge and are all marked 未核实, and `autokeeper research`
can verify them later when a network is available.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from rich.console import Console

from .. import artkit, config, paths, skills
from ..harness import Agent
from ..harness.search import resolve
from ..harness.tools import Workspace, file_tools, web_tools
from .importer import pipeline, printer

RESEARCH = """你是 CoC 跑团模组的考据员。种子：{seed}

用 web_search / fetch_url 查这个年代、这个地方的真实细节：街道与建筑、物价与交通、报纸与行业、风俗与俚语、
当时发生过的真实事件，以及可以借用的公有领域怪谈或文学（比如钱伯斯、坡、洛夫克拉夫特已进入公有领域的作品）。
把结果写成 research.md：10–15 张考据卡，每张包含“标题、事实（一两句）、可以怎么用进模组、出处链接”。
查到出处的标【已核实】；查不到的、或联网已关闭时凭自己知识写的，一律标【未核实】，不要编造链接。
写完调用 finish。"""

GENERATE = """你是 AutoKeeper 的模组编剧。根据种子和考据，原创一个 CoC 7 版短模组，写进工作目录的 module.yaml，并画彩色 ASCII 图。

种子：{seed}
考据：research.md（先读它；用到的史实在 source 字段里注明出处，未核实的写明“未核实”）

## 设计要求
- 1–3 名调查员，2–3 小时；6–9 个地点，4–6 个 NPC，12–18 条线索。
- 有一个清楚的真相和一个时钟（不处理就会恶化的倒计时事件）。
- 留出可玩的抵达与引入：start 选在调查开始前的一站（车站、街口、旅店等），opening 写旅途、来意与眼前的日常，停在玩家能行动的一刻。
  这个地点要真实连进 locations/exits。提供食宿、交通或闲聊的生活空间，让玩家可以停留或直接赶路；不要让所有普通人和店铺都藏着案情。
- 每个结论至少两条独立线索（三线索原则更好），关键线索标 core；至少一个 NPC 会撒谎、需要社交检定才松口。
- 至少一个“让玩家画出来”的 sketches 环节；至少三种结局，由剧情标记触发。
- 在重要节点写 5–15 个 showcases（大场景，整屏一张大图）：开场（when: opening）、突发和危机（event:）、关键地点（visit:）、
  怪物现身和最终 boss（event: 或 creature:）、每个结局（ending:）。title 是不剧透的短标题，prompt 写画面。
- 有怪物或会动手的人时，战斗和追逐由引擎按规则结算，不写成插件；照 _example/schema.json 的写法把它要的数值写全：
  怪物的 called（调查员还不知道它是什么时的叫法，只写看得见的样子，不写学名和真相）、stats（生命、敏捷必写）、attacks（一条一种：“爪击 50%，伤害 1D6+DB”）、notes 里的特性（护甲 2、枪伤减半、每轮恢复 1 点）；
  会动手的 NPC 写 stats，带的武器写进 look 或 items；它出没、最可能发生追逐的地点写 chase 路线（4–12 段，约一半有险阻或路障）；
  剧情里写定的袭击在那个事件写 fight（谁动手）和 surprise。数值照 CoC 7 版的尺度：普通人生命 10–12、格斗 25–50。
- 全部原创，可以借用公有领域作品的元素，但不要照搬任何受版权保护的模组、怪物设定或插图。
- 模组 id 用 "{module_id}"。格式照着 _example/module.yaml，字段含义见 _example/schema.json。

## 美术
- 每个地点画场景图（≤56 列、≤14 行），每个 NPC 和预设调查员画头像（≤18 列、7 行），各配同形状 .color 蒙版。颜色键：{palette}
{art_skill}

## 流程（每一步调用一个工具）
先把设计要点用 append_file 记进 notes.md，再用 write_file / append_file 写 module.yaml、画 art/。
写完调用 check，按输出改到没有“错误”和“[图]”为止。最后 finish，用三五句话介绍这个模组的卖点。"""

RESEARCHER = "你是严谨的历史考据员，所有输出用简体中文。"


def _researcher(work: Path, cfg: config.Config, console: Console, writable: tuple[str, ...], max_steps: int) -> Agent:
    ws = Workspace(work, writable=writable)
    tools = [*web_tools(cfg.search), *file_tools(ws)]
    return Agent("importer", RESEARCHER, tools, cfg=cfg, max_steps=max_steps, memory=80_000, on_event=printer(console, work))


async def run_generate(seed: str, module_id: str, *, console: Console | None = None) -> Path:
    console = console or Console()
    cfg = config.load()
    work = paths.LOCAL_MODULES / module_id

    async def research(console: Console) -> None:
        console.print(f"[bold]考据[/] {seed}（搜索：{resolve(cfg.search)}）")
        agent = _researcher(work, cfg, console, ("research.md",), 60)
        try:
            await agent.run(RESEARCH.format(seed=seed))
        finally:
            await agent.close()
        cards = (work / "research.md").read_text(encoding="utf-8") if (work / "research.md").exists() else ""
        console.print(f"[bold]考据完成[/] research.md {len(cards)} 字，已核实 {cards.count('已核实')} 处，未核实 {cards.count('未核实')} 处")

    work.mkdir(parents=True, exist_ok=True)
    brief = GENERATE.format(seed=seed, module_id=module_id, palette=artkit.PALETTE_DOC, art_skill=skills.body("ascii-art"))
    key = f"generate:{module_id}:{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"  # the same seed goes on where it stopped
    return await pipeline(work, brief=brief, prepare=research, console=console, key=key)


VERIFY = """逐条核实 research.md 里的考据卡：用 web_search 找出处，必要时用 fetch_url 打开核对。
- 核实无误的改成【已核实】并附出处链接；
- 有出入的更正事实，标【已更正】并附出处；
- 查不到可靠出处的保持【未核实】。
改完 research.md，再把 module.yaml 里 source 字段中关于核实情况的说明改成实际情况（只改 source 字段）。最后 finish。"""


async def verify_research(module_dir: Path, *, console: Console | None = None) -> None:
    """Re-run the researcher with web tools over an existing research.md."""
    console = console or Console()
    cfg = config.load()
    if resolve(cfg.search) == "none":
        raise ValueError("search = none：核实需要联网，换一个搜索后端（auto / ddgs / wikipedia / tavily）再来")
    agent = _researcher(Path(module_dir), cfg, console, ("research.md", "module.yaml"), 80)
    try:
        await agent.run(VERIFY)
    finally:
        await agent.close()
    text = (Path(module_dir) / "research.md").read_text(encoding="utf-8")
    console.print(f"[bold]核实完成[/] 已核实 {text.count('已核实')} 处，已更正 {text.count('已更正')} 处，未核实 {text.count('未核实')} 处")
