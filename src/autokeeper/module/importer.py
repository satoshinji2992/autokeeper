"""Import a published scenario: PDF / Word / Markdown -> module.yaml + coloured ASCII art -> Skill package.

    prepare ─→ build ─→ validate ─┬─→ compile (adapt, then the skill) ─→ END
                         ↑        │
                         └─ fix ←─┘   (at most three rounds)

`prepare` extracts the source here, plus any pictures that came with it (a zip's maps and
handouts); module generation (generator.py) reuses the same pipeline with web research as
its `prepare` step.

`build` and `fix` are one harness Agent (harness/loop.py) on the importer role, so any
model can do it: Claude through Claude Code, DeepSeek, or a local model with no
internet. It works inside modules/local/<id>/ with sandboxed file tools (it may write
only module.yaml, notes.md and art/), a view_image tool backed by the vision role, and
a check tool that runs the module validator in-process. It reads the extracted text,
keeps notes, writes module.yaml, draws every location and portrait as ASCII art with
colour masks, and checks its own work until the module passes. Its draft is one
module.yaml and a flat art/ folder; `compile` files it into the module layout
(package.normalize), the original text and the notes in source/. Imported material stays
in modules/local/ and skills/module-<id>/, both git-ignored.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from rich.console import Console
from rich.markup import escape

from .. import artkit, config, credits, imagegen, imagery, paths, skills
from ..agents.writing import INTERFACE_COPY
from ..harness import Agent, make_model
from ..harness.tools import Tool, ToolError, Workspace, check_tool, file_tools, image_tool
from . import graph as module_graph
from . import package
from .compiler import compile_module
from .schema import Module

MAX_FIX_ROUNDS = 3
PROGRESS = ".import.json"  # how far an import came, so running it again goes on from there
STAGES = ("prepared", "built", "validated", "compiled")
RESUME = """

## 接着上次做
上一次构建在中途停下了。工作区里已经有上次写的 notes.md、module.yaml，和 art/ 里画好的图。
先读 notes.md 和 module.yaml，弄清已经做到哪一步，再接着把剩下的做完：不要从头重读整份原文，
不要重写已经写好的部分，不要删掉已经画好的图。还缺什么、哪里没核对，可以回原文查对应的段落。"""


def progress(work: Path) -> dict:
    try:
        data = json.loads((work / PROGRESS).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def mark(work: Path, key: str, stage: str) -> None:
    """Record that an import of this source got this far (key: what it was made from)."""
    (work / PROGRESS).write_text(json.dumps({"key": key, "stage": stage}, ensure_ascii=False), encoding="utf-8")


def reached(work: Path, key: str) -> int:
    """How many stages an earlier import of the same source got through (0: start afresh)."""
    done = progress(work)
    if not key or done.get("key") != key or done.get("stage") not in STAGES:
        return 0
    return STAGES.index(done["stage"]) + 1
VISION = "你是跑团模组整理员的读图助手。看图，用简体中文如实回答问题；看不清的地方直说。"

# What a fight and a chase read of a module (engine/combat.py, engine/fighting.py), for the brief.
FIGHTS = """- 战斗和追逐由引擎按 CoC 7 版规则一轮一轮结算，不用插件；数值从模组里读，照 _example/schema.json 里这几个字段的写法写：
  - creatures：called 写调查员还不知道它是什么时的叫法，只写看得见的样子（“墓地里的黑影”“披着人皮的东西”），不写学名、真名和剧情真相，屏幕上给玩家看的是它；
    stats 写战斗数值（生命、敏捷一定要有，还有力量、体质、体型、闪避、移动），原文的数值照抄，原文没写的按 CoC 7 版规则书里同类怪物的数值；
    attacks 一条一种，写成“爪击 50%，伤害 1D6+DB”“射击（步枪）30%，射程 110，伤害 2D6+4”，命中率带 %，
    每轮不止一次攻击另写一条“每轮 3 次”，闪避写进 stats 不写在这里；notes 写规则用得着的特性：护甲 2、枪伤减半、每轮恢复 1 点、
    只被拳头打 6 点就会打晕、伤害加值 +1D4，调查员逃跑时它不追就写“不追”。成群出现、整群当一个对手打的不写数值，notes 写明成群。
  - 原文写明会动手、或剧情里会被卷进打斗的 NPC，stats 写战斗数值（格斗(斗殴)、闪避、会用的武器技能），他带着的武器写进 look，或在 items 里交给他。
  - 最可能发生追逐的 1–3 个地点（怪物的巢穴、它够得着的户外、原文写到有人逃跑或追赶的地方）写 chase：从那里跑出去的 4–12 段路，
    每段一个名字、一句样子，大约一半有险阻或路障，写清用什么技能；只写那里和周围真有的东西，追逐时玩家会看到跑过和看得见的路段，不写线索和秘密。
  - 原文写明某件事发生时有人或怪物当场扑上来动手的，在那个事件写 fight（谁动手）和 surprise（谁被偷袭）；写明一开始就是追逐的，写 chase 和 chase_party。
    只是可能动手、要看调查员怎么做的不写，由裁定员临场判。"""


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
        pictures, by_digest = [], {}
        (src / "images").mkdir()
        for n in chosen:
            page = pdf[n - 1]
            text = page.get_textpage().get_text_range().replace("\r\n", "\n")
            parts.append(f"\n\n---------- 第 {n} 页（图片：source/pages/p{n:02d}.jpg）----------\n\n{text.strip()}")
            image = page.render(scale=1.25).to_pil().convert("RGB")
            image.save(src / "pages" / f"p{n:02d}.jpg", quality=72, optimize=True)
            # Extract actual embedded artwork rather than a page containing keeper notes.
            for index, obj in enumerate(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]), 1):
                try:
                    bitmap = obj.get_bitmap().to_pil().convert("RGB")
                    if min(bitmap.size) < 120 or bitmap.width * bitmap.height < 40000:
                        continue
                    digest = hashlib.sha256(f"{bitmap.width}x{bitmap.height}:".encode() + bitmap.tobytes()).hexdigest()
                    if digest in by_digest:
                        if n not in by_digest[digest]["pages"]:
                            by_digest[digest]["pages"].append(n)
                        continue
                    name = f"p{n:02d}_image{index:02d}.png"
                    bitmap.save(src / "images" / name)
                    record = {"path": f"source/images/{name}", "pages": [n], "width": bitmap.width, "height": bitmap.height}
                    pictures.append(record)
                    by_digest[digest] = record
                except (ValueError, RuntimeError):
                    continue  # the rendered page remains available if an embedded object cannot be decoded
        (src / "source.md").write_text("".join(parts).strip() + "\n", encoding="utf-8")
        (src / "images" / "index.json").write_text(json.dumps(pictures, ensure_ascii=False, indent=2), encoding="utf-8")
        if pictures:
            with (src / "source.md").open("a", encoding="utf-8") as output:
                output.write("\n\n## 原模组内嵌图片清单\n见 source/images/index.json；优先用 use_image 保留原图，避免整页图片暴露周围的守秘人资料。\n")
        return len(chosen), sum(len(p) for p in parts)
    from markitdown import MarkItDown

    text = MarkItDown().convert(str(file)).text_content
    (src / "source.md").write_text(text, encoding="utf-8")
    return 0, len(text)


def attach(images: Sequence[Path], work: Path) -> None:
    """Pictures that came with the document go to source/images/, listed at the top of
    source.md so the agent knows they are there while it reads."""
    folder = work / "source" / "images"
    folder.mkdir(parents=True, exist_ok=True)
    names: dict[str, str] = {}  # by lowercase name: some filesystems ignore case
    for img in images:
        name, n = img.name, 1
        while name.lower() in names:  # the same name from two folders of a zip
            n += 1
            name = f"{img.stem}_{n}{img.suffix}"
        names[name.lower()] = name
        shutil.copy(img, folder / name)
    md = work / "source" / "source.md"
    listing = "\n".join(f"- source/images/{name}" for name in names.values())
    head = f"（随模组附带 {len(names)} 张图片，地图、手稿、人物像可以用 view_image 看）\n{listing}\n\n"
    md.write_text(head + md.read_text(encoding="utf-8"), encoding="utf-8")


def _prepare(work: Path) -> None:
    """Reference material the agent may read: an example module and the schema."""
    ex = work / "_example"
    ex.mkdir(exist_ok=True)
    (ex / "module.yaml").write_text(package.joined_text(paths.EXAMPLE), encoding="utf-8")  # the agent writes one file
    for f in (paths.EXAMPLE / "art" / "ascii").glob("*"):
        shutil.copy(f, ex / f.name)
    (ex / "schema.json").write_text(json.dumps(Module.model_json_schema(), ensure_ascii=False, indent=1), encoding="utf-8")
    from ..plugins import api_text
    from ..plugins.schema import Output

    (ex / "plugins.md").write_text(api_text(), encoding="utf-8")
    (ex / "plugin-output.schema.json").write_text(json.dumps(Output.model_json_schema(), ensure_ascii=False, indent=1), encoding="utf-8")


BRIEF = """你是 AutoKeeper 的模组构建员。把 source/source.md 这份 CoC 7 版模组（页面图片在 source/pages/）
整理成 AutoKeeper 的模组格式，写进工作目录的 module.yaml，并给它画彩色 ASCII 图。

## 格式
- 照着 _example/module.yaml（字段齐全的格式样例）写；字段含义见 _example/schema.json。
- 模组 id 用 "{module_id}"。所有 id 用小写英文或拼音加下划线。
- 只整理模组里有的东西；剧情、真相、线索内容、检定要求、理智损失、结局都按原文，不要改编。
  原文没写的细节（NPC 的说话方式、地点的可调查物）可以合理补全。
- 保留原文的开局引入，别把调查员直接放进任务清单。原文直接从委托或调查开始时，可补一处不改变案情的抵达地点与日常接触，
  在 keeper_notes 注明“AutoKeeper 补充”；start、opening、locations/exits 要一致，玩家可以问路、歇脚或直接赴约。
  不补新的案情线索、怪异预兆和强制检定，不改原作必须发生的开局事件与时限。
- clues 要全：写清在哪（where、feature）、要什么检定（check，技能名用中文，如 侦查、图书馆使用、外语(拉丁文)）、
  指向哪个结论（points_to），推进剧情必需的标 core: true。NPC 口中的线索写 npc，并在该 NPC 的 clues 和 gates 里登记。
- NPC 的 knows / secrets / lies 分清楚，NPC 只知道原文里他知道的事。
- 只使用 schema 声明的字段，不发明 clues.sets 等不会执行的字段；复杂条件和暂未支持的规则完整写入 keeper_notes。
- 有时间点的事情写进 events（when 用 "D1 21:00"，或 visit:地点id、flag:标记）；结局 endings 和奖励照原文；
  用到的剧情标记都要在 flags 里声明。
""" + FIGHTS + """
- 原文没有预设调查员的话，按模组的年代和地点设计两名，写进 pregens，backstory 里注明“AutoKeeper 补充”。
- 原文有通用引擎未支持的持续机制时，读 _example/plugins.md，用 write_plugin 编写并运行测试，不能只在 keeper_notes 写“请手动执行”就宣称自动支持。
  插件只补原文规则，不改写原作，不替玩家作决定；保留专属规则全文与具体限制。插件失败可修复后重试，不跳过校验。
- 原文里有适合做成面板、小游戏或演出的东西（仪式倒数、谜题、密码锁、降神会、调查板、报纸号外），也鼓励写插件：panels 放在右栏，prompt 让玩家自己解，show 放几秒演出。
  这些玩家都看得见，只放玩家能知道的内容；小游戏可玩可不玩，原文的关键线索照旧有别的来路。
  例：原文有三位数密码锁。commands 写 [{{"name":"开锁","description":"拨保险箱的三位密码"}}]，用 style 为 number 的 prompt 一位一位问，三位都对时给出原文那条线索（clue）。
- 原文里如果有适合让玩家“画出来”的东西（符号、地图、人像），在 sketches 里写一两个。
- 原文明确写到某个地点或人物之后会明显变样（被烧毁、被水淹、被洗劫、受伤、变异、死去），而且有剧情标记表示这件事发生了，
  可以在那个地点或 NPC 的 looks 里登记：when 写 flag:标记，change 写画面上看得见的变化。全模组最多 6 处；昼夜不用登记（画面按时间自动调光）。
- 在故事的重要节点写 5–15 个 showcases（大场景：整屏展示一张大图，每局每个只出现一次）：开场氛围（when: opening）、
  突发事件和危机（event:事件id）、关键地点（visit:地点id）、怪物现身（event:事件id 或 creature:怪物id）、最终 boss、
  每个结局（ending:结局id）。title 是此刻玩家看得见的短标题，不剧透；prompt 写画面。原模组有正好合适的插图就用 art 指向它，
  想照某张原图重画就把它写进 base。

## 美术
- 先读 source/images/index.json（如果存在）。原模组已有的地图、病历、信件、人物或场景插图优先用 use_image 保存，不用新画的图替代原图。
- 场景原图使用与 locations.art 相同的图片键，无剧透的原作封面用 cover 绑定。pictures/art/art_colors 是运行时加载字段，不在 YAML 里写图片描述。
  use_image 的 name 是图片键；场景/NPC 用同一键的 art/portrait，手稿通过 images: [图片键] 关联。ASCII 图仍作为终端与离线回退。
  不能把带守秘人笔记、未发现地点或后期标注的整页提前发给玩家。完整标注地图等资料在相应线索解锁时再给。
- 每个地点写 art 字段，在 art/<名字>.txt 画场景图（≤56 列、≤14 行）；每个 NPC 和预设调查员写 portrait，画头像（≤18 列、7 行）。
- 每张图配同名 .color 配色蒙版：与图同形状，每个字符一个颜色键，“.”为默认色。颜色键：{palette}
- 只用 ASCII 字符，自己构图，不要照搬原书插图。_example/ 里有图和蒙版的格式样例。
{art_skill}

## 流程（每一步调用一个工具）
1. 用 read_file 分段通读 source/source.md（每次 200–400 行）。边读边用 append_file 把要点记进 notes.md：
   地点、人物、线索（带检定）、时间线、结局。上下文会被压缩，notes.md 是你的长期记忆，写 module.yaml 时以它为准。
2. 需要看地图、手稿时，用 view_image 看 source/pages/ 下对应页的图片。
3. 用 write_file 写 module.yaml（内容多就先写前半，再用 append_file 续写），之后用 edit_file 修改。
4. 画 art/ 下的图和 .color 蒙版。
   推荐用 write_art，一次提交多张 ASCII 图的 JSON 对象（键是图名，不含扩展名；值是图的文本），工具会校验尺寸并自动配色，省去逐个写蒙版。
5. 调用 check 校验，按输出改到没有“错误”和“[图]”为止（“提醒”可以留着）。
6. 最后 finish，在 answer 里用三五句话说明：哪些内容是你补充的，哪里原文含糊、你做了什么取舍。""" + "\n\n以下文体规范仅用于新编写的界面元数据；引用的手稿、信件和模组原文保持原貌。\n" + INTERFACE_COPY


ART_BY_ARTIST = """

## 这次画图交给美术 Agent
这次的字符画由美术 Agent（{model}）来画，取代上面“美术”一节里自己画图的要求和流程第 4 步：
- 不要自己写 art/*.txt 和 .color，也没有 write_art。
- 在 module.yaml 里写好 art 和 portrait 键之后，用 draw_art 提交 JSON：键是图名，值是一句画面描述。
  场景写地点的样子、光线和最显眼的东西；头像写这个人的外貌、年纪和神情。不写剧透。
- 原模组已有的图照样用 use_image 保存。"""


def drawn_by_artist(cfg: config.Config) -> bool:
    """Whether the art goes to the artist role: when the module is built by a lesser model,
    the pictures still come from its vendor's strongest one (config.art_role)."""
    importer, artist = cfg.role("importer"), cfg.role("artist")
    return (importer.backend, importer.model) != (artist.backend, artist.model)


def validate(work: Path) -> list[str]:
    try:
        m = Module.load(work)
    except Exception as exc:  # malformed YAML or schema errors go straight back to the agent
        return [f"[错误] module.yaml 读不进来：{exc}"[:2000]]
    raw = package.read(work)
    schema = Module.model_json_schema()
    issues = _unknown_fields(raw, schema, schema.get("$defs", {}))
    for field in ("art", "art_colors", "pictures", "extensions", "extension_errors"):
        if raw.get(field):
            issues.append(f"[格式] {field} 是运行时自动加载字段，不应在 module.yaml 中填写描述或路径；用图片键或 plugins 列表关联")
    issues += [str(i) for i in module_graph.audit(m) if i.level == "error"]
    issues += [f"[插件] {key}：{error}" for key, error in m.extension_errors.items()]
    portraits = {n.portrait for n in m.npcs} | {p.portrait for p in m.pregens}
    for name, text in m.art.items():
        issues += [f"[图] {i}" for i in artkit.check(name, text, m.art_colors.get(name), portrait=name in portraits)]
    for name in [loc.art for loc in m.locations if loc.art] + [x for x in portraits if x]:
        if name not in m.art:
            issues.append(f"[图] 缺少 art/{name}.txt")
    for handout in m.handouts:
        for name in handout.images:
            if name not in m.pictures:
                issues.append(f"[图] 手稿 {handout.id} 缺少原图 art/{name}")
    if m.cover and m.cover not in m.pictures:
        issues.append(f"[图] 缺少封面原图 art/{m.cover}")
    return issues


async def validate_all(work: Path) -> list[str]:
    issues = validate(work)
    if not issues:
        from ..plugins.runtime import verify

        for plugin in Module.load(work).extensions.values():
            issues += [f"[插件] {plugin.id}：{problem}" for problem in await verify(plugin)]
    return issues


def _unknown_fields(value: object, schema: dict, definitions: dict, path: str = "module") -> list[str]:
    """Catch data that Pydantic would silently ignore before declaring an import complete."""
    if reference := schema.get("$ref"):
        schema = definitions.get(reference.rsplit("/", 1)[-1], {})
    issues = []
    if isinstance(value, dict) and "properties" in schema:
        fields = schema["properties"]
        for key, item in value.items():
            if key not in fields:
                issues.append(f"[格式] {path}.{key} 是未支持的字段；请用现有字段表达，专属规则完整记入 keeper_notes")
            else:
                issues += _unknown_fields(item, fields[key], definitions, f"{path}.{key}")
    elif isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            issues += _unknown_fields(item, schema["items"], definitions, f"{path}[{index}]")
    return issues


def report(issues: list[str]) -> str:
    return "校验通过：没有错误。" if not issues else f"还有 {len(issues)} 个问题：\n" + "\n".join(issues[:40])


def printer(console: Console, work: Path) -> Callable[[str, dict], None]:
    """Agent events as console lines: the model's thought, then the tool it ran."""

    def on_event(kind: str, data: dict) -> None:
        if kind == "think" and data.get("thought"):
            fixed = "  [yellow](修复格式 1 次)[/]" if data.get("repairs") else ""
            console.print(f"  [italic dim]{escape(data['thought'][:160])}[/]{fixed}")
        elif kind == "act":
            args = data.get("args", {})
            target = args.get("path") or args.get("query") or args.get("url") or ""
            first = data.get("observation", "").splitlines()[0][:80] if data.get("observation") else ""
            console.print(f"  [cyan]· {data['tool']}[/] {escape(str(target)[:80])}  [dim]{escape(first)}[/]")

    return on_event


def builder(work: Path, cfg: config.Config, console: Console, *, writable=("module.yaml", "notes.md", "art/*", "plugins/*"), max_steps: int = 160) -> Agent:
    ws = Workspace(work, writable=writable)
    eye = make_model(cfg.role("vision"), VISION, label="vision")

    async def see(png: bytes, query: str) -> str:
        return (await eye.complete([{"role": "user", "content": query}], images=[png])).text.strip()

    from ..plugins.author import plugin_tool

    async def checked():
        return report(await validate_all(work))

    art = artist_tool(ws, cfg) if drawn_by_artist(cfg) else artwork_tool(ws)
    tools = [*file_tools(ws), art, original_image_tool(ws), plugin_tool(ws), image_tool(ws, see), check_tool(checked)]
    system = "你是一个仔细、守规矩的 TRPG 模组整理员。所有输出用简体中文。"
    agent = Agent("importer", system, tools, cfg=cfg, max_steps=max_steps, memory=150_000, on_event=printer(console, work))
    agent.closers.append(eye.close)
    return agent


def artwork_tool(ws: Workspace) -> Tool:
    async def write_art(content: str) -> str:
        try:
            pictures = json.loads(content)
            module = Module.load(ws.root)
        except (ValueError, OSError) as exc:
            raise ToolError(f"先写好 module.yaml；content 须为图名到图文的 JSON 对象：{type(exc).__name__}") from None
        if not isinstance(pictures, dict) or not pictures or len(pictures) > 80:
            raise ToolError("一次提交 1–80 张图，格式为图名到 ASCII 文本的 JSON 对象")
        portraits = {n.portrait for n in module.npcs} | {p.portrait for p in module.pregens}
        outputs = []
        for name, art in pictures.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name) or not isinstance(art, str):
                raise ToolError("图名只能用小写字母、数字与下划线，图文必须是字符串")
            art = "\n".join(line.rstrip() for line in art.strip("\n").splitlines())
            mask = artkit.auto_mask(art)
            if errors := artkit.check(name, art, mask, portrait=name in portraits):
                raise ToolError("；".join(map(str, errors)))
            outputs += [(ws.resolve(f"art/{name}.txt", write=True), art), (ws.resolve(f"art/{name}.color", write=True), mask)]
        # Validate every path and illustration before writing any part of the batch.
        for path, text in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")
        return f"已写入 {len(pictures)} 张图及其自动配色蒙版：{'、'.join(pictures)}"

    return Tool("write_art", "批量保存 ASCII 场景、头像与自动配色；先写 module.yaml，content 为图名到图文的 JSON 对象", ("content",), write_art, ("content",))


def artist_tool(ws: Workspace, cfg: config.Config) -> Tool:
    """The pictures drawn by the artist role from one line each; the tool checks and colours
    them as write_art does. A few at a time, so a batch of thirty does not open thirty sessions."""
    from ..agents import artist

    async def draw_art(content: str) -> str:
        try:
            wanted = json.loads(content)
            module = Module.load(ws.root)
        except (ValueError, OSError) as exc:
            raise ToolError(f"先写好 module.yaml；content 须为图名到画面描述的 JSON 对象：{type(exc).__name__}") from None
        if not isinstance(wanted, dict) or not wanted or len(wanted) > 40:
            raise ToolError("一次提交 1–40 张图，格式为图名到一句画面描述的 JSON 对象")
        for name, subject in wanted.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name) or not isinstance(subject, str) or not subject.strip():
                raise ToolError("图名只能用小写字母、数字与下划线，描述必须是非空字符串")
        portraits = {n.portrait for n in module.npcs} | {p.portrait for p in module.pregens}
        gate = asyncio.Semaphore(3)

        async def one(name: str, subject: str) -> tuple[str, tuple[str, str] | None, str]:
            async with gate:
                try:
                    drawing = await artist.draw(cfg, f"{subject}（{module.era}，{module.setting}）", portrait=name in portraits)
                except Exception as exc:  # one picture failing leaves the rest
                    return name, None, type(exc).__name__
            return name, drawing, ""

        done, failed = [], []
        for name, drawing, why in await asyncio.gather(*(one(n, s) for n, s in wanted.items())):
            if drawing is None:
                failed.append(f"{name}（{why}）")
                continue
            for path, text in zip((f"art/{name}.txt", f"art/{name}.color"), drawing, strict=True):
                target = ws.resolve(path, write=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text + "\n", encoding="utf-8")
            done.append(name)
        out = f"美术 Agent 画好 {len(done)} 张：{'、'.join(done) or '无'}"
        return out + (f"；没画成：{'、'.join(failed)}，换个描述再交" if failed else "")

    return Tool(
        "draw_art", "让美术 Agent 画 ASCII 场景和头像并配色；先写 module.yaml，content 为图名到一句画面描述的 JSON 对象",
        ("content",), draw_art, ("content",),
    )


def original_image_tool(ws: Workspace) -> Tool:
    async def use_image(path: str, name: str) -> str:
        source = ws.resolve(path)
        if not source.is_relative_to(ws.root / "source") or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not source.is_file():
            raise ToolError("只使用 source/ 内已有的原图文件")
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise ToolError("图片键只用小写字母、数字和下划线")
        target = ws.resolve(f"art/{name}{source.suffix.lower()}", write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        credits.put(target, {"source": ws.rel(source), "original": True})
        return f"已保留原图 {ws.rel(target)}；场景或头像用键 {name}，手稿写 images: [{name}]；注意正确解锁时机。"

    return Tool("use_image", "把 source/ 中的原模组图片原样保存到 art/，供场景、人物或手稿使用", ("path", "name"), use_image, ("path", "name"))


async def pipeline(
    work: Path,
    *,
    brief: str,
    prepare: Callable[[Console], Awaitable[None]] | None = None,
    console: Console | None = None,
    cfg: config.Config | None = None,
    index: bool = True,
    photos: bool = True,
    adapting: bool = True,
    key: str = "",
) -> Path:
    """prepare → build → validate ⇄ fix → compile, shared by import and generation.

    `key` names what the module is made from (the source file and pages, or the seed). Each stage
    that finishes is written down in the workspace (PROGRESS); running the same import again goes
    on from there: no new extraction or research, and an interrupted build is taken up by the
    agent from its own notes.md and draft (RESUME), not started over. Without a key: from scratch."""
    console = console or Console()
    cfg = cfg or config.load()
    work.mkdir(parents=True, exist_ok=True)
    agent = builder(work, cfg, console)
    done = reached(work, key)  # stages an earlier run of the same import got through

    async def n_prepare(_: ImportState) -> dict:
        if done and (work / "source").is_dir():
            console.print(f"[bold]接着上次[/] 已经做到「{STAGES[done - 1]}」，原文不再重新提取")
        else:
            if prepare:
                await prepare(console)
            package.start_draft(work)
        _prepare(work)
        if key and not done:
            mark(work, key, "prepared")
        return {"work": str(work), "rounds": 0}

    async def n_build(_: ImportState) -> dict:
        if done >= STAGES.index("built") + 1:
            console.print("[bold]构建[/] 上次已经完成，接着校验")
            return {}
        role = cfg.role("importer")
        console.print(f"[bold]构建[/] {role.backend}:{role.model}（effort={role.effort}）开始读原文、写 module.yaml、画图…")
        task = brief
        if drawn_by_artist(cfg):
            art = cfg.role("artist")
            console.print(f"[bold]美术[/] 图由美术 Agent 画：{art.backend}:{art.model}")
            task += ART_BY_ARTIST.format(model=art.model)
        notes = work / "notes.md"
        if done and ((work / "module.yaml").is_file() or (notes.is_file() and notes.read_text(encoding="utf-8").strip())):
            console.print("[bold]构建[/] 接着上次的笔记和草稿做")
            task += RESUME
        result = await agent.run(task)
        console.print(f"[bold]构建结束[/] {result.steps} 步{'' if result.finished else '（到步数上限）'}  {escape(result.answer[:300])}")
        if key:
            mark(work, key, "built")
        return {}

    async def n_validate(_: ImportState) -> dict:
        issues = await validate_all(work)
        console.print(f"[bold]校验[/] {'通过' if not issues else f'{len(issues)} 个问题'}")
        for i in issues[:20]:
            console.print(f"  {escape(i)}")
        if key and not issues and STAGES.index(progress(work).get("stage", "prepared")) < STAGES.index("validated"):
            mark(work, key, "validated")
        return {"issues": issues}

    def after_validate(state: ImportState) -> str:
        if not state.get("issues"):
            return "compile"
        return "fix" if state.get("rounds", 0) < MAX_FIX_ROUNDS else END

    async def n_fix(state: ImportState) -> dict:
        rounds = state.get("rounds", 0) + 1
        console.print(f"[bold]修复[/] 第 {rounds} 轮")
        await agent.run(f"校验还有这些问题，请逐条修好，用 check 确认后再 finish：\n{report(state.get('issues', []))}")
        return {"rounds": rounds}

    async def n_compile(_: ImportState) -> dict:
        package.normalize(work)
        from .adapt import adapt, score

        if adapting:
            console.print("[bold]改编[/] 补上跑团用得着、原文没写的：人物、冲突图、各人的打算、伏笔、线索网、私人牵连、额外结局、战斗数值与追逐路线…")
            await adapt(work, cfg=cfg, log=console.print)  # added only, checked pass by pass
            console.print(f"[bold]可玩度[/] {score(Module.load(work)).total}/100")
        from .simulate import playthrough

        for line in playthrough(Module.load(work)).issues:  # what a playthrough with no model finds: for the author to look at
            console.print(f"  [yellow]推演[/] {escape(line)}")
        m = Module.load(work)
        root = compile_module(m, paths.SKILLS, source=work / "source")
        console.print(
            f"[bold green]完成[/] 《{m.title}》：{len(m.locations)} 个地点、{len(m.npcs)} 个 NPC、{len(m.clues)} 条线索、"
            f"{len(m.art)} 张 ASCII 图 → {root}"
        )
        if photos:
            await find_photos(m, work, cfg, console)
            await find_showcases(Module.load(work), cfg, console)  # with the photos just found as references
        from .. import knowledge

        if index and knowledge.available():
            console.print("[bold]建库[/] LightRAG：抽取原文实体与关系，并灌入模组图谱…")
            try:
                async with knowledge.KnowledgeBase(m, cfg) as kb:
                    stats = await kb.build(work)
                console.print(f"[bold green]知识库[/] {stats}")
            except Exception as exc:
                console.print(f"[yellow]模组与 Skill 已完成；可选知识库未建成（{type(exc).__name__}），可以稍后重建。[/]")
        if key:
            mark(work, key, "compiled")
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
        result = await g.compile().ainvoke({}, {"recursion_limit": 20})
        if result.get("issues"):
            raise ValueError(f"模组仍有 {len(result['issues'])} 个问题，未编译；草稿保留在 {work}")
    finally:
        await agent.close()
    return work


async def find_photos(m: Module, work: Path, cfg: config.Config, console: Console) -> list[str]:
    """The scenes and faces as real photos (Wikimedia Commons, licensed), drawn in characters
    like 《追书人》's. Whatever finds nothing gets a picture Codex draws from its description
    (imagegen.py, when codex is here), else keeps its hand-drawn art; then the changed states
    the module describes (Location.looks, NPC.looks) are drawn from those pictures. Offline
    (search = none) or without chafa there is nothing to do; `autokeeper art fetch` does the
    same later."""
    wanted = imagery.targets(m)
    if not wanted or cfg.search == "none" or not imagery.available():
        return []
    fill = imagegen.filler(cfg, m, work / "art")
    rest = "找不到的由 AI 画" if fill else "找不到的保留手绘"
    console.print(f"[bold]找照片[/] {len(wanted)} 处场景和人物，在 Wikimedia Commons 找有授权的照片，{rest}…")
    try:
        done = await imagery.fetch_folder(cfg, work / "art", wanted, log=console.print, generate=fill)
    except Exception as exc:  # the module is done either way
        console.print(f"[yellow]照片没找成（{type(exc).__name__}），可以稍后用 autokeeper art fetch 补。[/]")
        return []
    console.print(f"[bold green]照片[/] 找到 {len(done)}/{len(wanted)} 张，来源和授权记在 art/credits.yaml")
    if fill and any(x.looks for x in [*m.locations, *m.npcs]):
        console.print("[bold]变样图[/] 照原图画出剧情里会变样的地点和人物…")
        drawn = await imagegen.looks(cfg, m, work / "art", log=console.print)
        console.print(f"[bold green]变样图[/] 画好 {len(drawn)} 张")
    return done


async def find_showcases(m: Module, cfg: config.Config, console: Console) -> list[str]:
    """The big scenes' pictures (engine/showcase.py), one at a time, the opening first, kept
    in data/cache/showcases/<id>/ outside the module; `autokeeper showcase` does the same later."""
    from ..engine.showcase import prepare_module

    if not m.showcases or not imagery.available() or not (imagegen.enabled(cfg) or cfg.search != "none"):
        return []
    console.print(f"[bold]大场景[/] {len(m.showcases)} 个，" + (
        "AI 一张一张画，每张大约一分钟…" if imagegen.enabled(cfg) else "不用 AI 画，只用模组里的图和找到的照片…"))
    try:
        done = await prepare_module(cfg, m, log=console.print)
    except Exception:  # the module is done either way
        console.print("[yellow]大场景的图没准备好，可以稍后用 autokeeper showcase 补。[/]")
        return []
    console.print(f"[bold green]大场景[/] 准备好 {len(done)}/{len(m.showcases)} 个")
    return done


def import_config(model: str = "", vision_model: str = "") -> config.Config:
    """Per-import routing; keep the game's models and the user's saved configuration intact."""
    cfg = config.load()
    cfg = replace(cfg, roles=dict(cfg.roles))
    for name, choice in (("importer", model), ("vision", vision_model)):
        if choice:
            backend, routed = config.route(choice)
            old = cfg.role(name)
            same_provider = backend == old.backend and routed.split(":", 1)[0] == old.model.split(":", 1)[0]
            cfg.roles[name] = replace(old, backend=backend, model=routed, base_url=old.base_url if same_provider else None)
    if model:
        # Long documents and optional indexing must not quietly fall back to the game's
        # Claude roles when the user has explicitly chosen another import backend.
        for name in ("summarizer", "lore"):
            cfg.roles[name] = replace(cfg.role("importer"), effort=None)
        # Pictures go to that vendor's strongest model, not the game's (a Codex import draws
        # on GPT-6-Astra); a reading model chosen for this import keeps its place.
        cfg.roles["artist"] = config.art_role(cfg.role("importer"))
        if not vision_model:
            cfg.roles["vision"] = config.art_role(cfg.role("importer"), seeing=True)
    return cfg


async def run_import(
    file: Path, module_id: str, *, pages: str | None = None, images: Sequence[Path] = (),
    console: Console | None = None, model: str = "", vision_model: str = "", photos: bool = True, fresh: bool = False,
) -> Path:
    """`fresh`: forget an earlier, unfinished import of this module and start over."""
    work = paths.LOCAL_MODULES / module_id
    if fresh:
        for name in (PROGRESS, "notes.md", "module.yaml"):
            (work / name).unlink(missing_ok=True)

    async def prepare(console: Console) -> None:
        # Rendering a long PDF takes a while; off the event loop, the TUI keeps drawing meanwhile.
        n, size = await asyncio.to_thread(extract, Path(file), work, pages)
        console.print(f"[bold]提取[/] {Path(file).name}：{n} 页，{size} 字 → {work}/source/")
        if images:
            attach(images, work)
            console.print(f"[bold]附图[/] {len(images)} 张 → {work}/source/images/")

    brief = BRIEF.format(module_id=module_id, palette=artkit.PALETTE_DOC, art_skill=skills.body("ascii-art"))
    digest = hashlib.sha256(Path(file).read_bytes()).hexdigest()[:16]
    key = f"import:{module_id}:{digest}:{pages or ''}:{len(images)}"  # the same file again goes on where it stopped
    return await pipeline(work, brief=brief, prepare=prepare, console=console, cfg=import_config(model, vision_model), photos=photos,
                          key=key)  # fmt: skip
