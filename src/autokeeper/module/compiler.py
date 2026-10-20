"""Compile a module into an Agent Skill package (progressive disclosure).

    skills/module-<id>/
      SKILL.md                      overview the keeper always carries: truth, clock, indexes
      references/locations/<id>.md  one file per location: read-aloud text, clues, checks, NPCs present
      references/npcs/<id>.md       one file per NPC: persona, secrets, lies, gated clues
      references/handouts/<id>.md   handout full text
      references/events.md          timed and triggered events
      references/endings.md         endings and rewards
      references/pregens.md         pre-generated investigators
      references/situation.md       the situation compile time prepared: relations, what each important
                                    NPC means to do and on what grounds, signs, the past, and the
                                    module's own rules with who keeps each (core rules, a plugin, the keeper)
      assets/ascii/<name>.txt       ASCII art
      .stamp                        what it was compiled from (see stamp())

Claude Code reads it from .claude/skills, Codex from .agents/skills and deepagents from
the path we pass; all three follow the Agent Skills spec, so nothing here is custom
loading logic.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from .. import credits
from .graph import supporting_clues
from .schema import NPC, Clue, Location, Module


def skill_name(m: Module) -> str:
    return f"module-{m.id}"


def version(m: Module) -> str:
    raw = yaml.safe_dump(m.model_dump(mode="json"), allow_unicode=True, sort_keys=True)
    raw += json.dumps({key: plugin.fingerprint for key, plugin in sorted(m.extensions.items())}, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def stamp(m: Module, source: Path | None = None) -> str:
    """The module, its plugins, the compiler and the plugin API it was compiled with: a skill
    package whose stamp differs is stale."""
    from ..plugins import api_text

    h = hashlib.sha1(version(m).encode())
    h.update(Path(__file__).read_bytes())
    h.update(api_text().encode())
    if source is not None and source.is_dir():
        for f in sorted(source.rglob("*")):
            if f.is_file():
                h.update(f"{f.relative_to(source).as_posix()}:{f.stat().st_size}".encode())
    return h.hexdigest()[:16]


def ensure_compiled(m: Module, folder: Path, skills_root: Path | None = None) -> Path:
    """The skill package of the module loaded from `folder`, compiled again when it is missing
    or stale. Every place that loads a module to play calls this, so they all agree on the stamp."""
    from .. import paths

    skills_root = paths.SKILLS if skills_root is None else skills_root
    source = Path(folder) / "source"
    root = Path(skills_root) / skill_name(m)
    try:
        if (root / ".stamp").read_text(encoding="utf-8").strip() == stamp(m, source):
            return root
    except OSError:
        pass
    return compile_module(m, skills_root, source=source)


def compile_module(m: Module, skills_root: Path, *, source: Path | None = None) -> Path:
    root = Path(skills_root) / skill_name(m)
    if root.exists():
        shutil.rmtree(root)
    (root / "references" / "locations").mkdir(parents=True)
    (root / "references" / "npcs").mkdir(parents=True)
    (root / "references" / "handouts").mkdir(parents=True)
    _write(root / "SKILL.md", _skill_md(m))
    for loc in m.locations:
        _write(root / "references" / "locations" / f"{loc.id}.md", _location_md(m, loc))
    for npc in m.npcs:
        _write(root / "references" / "npcs" / f"{npc.id}.md", _npc_md(m, npc))
    for h in m.handouts:
        images = "\n".join(f"![{h.title}](../../assets/images/{Path(m.pictures[key]).name})" for key in h.images if key in m.pictures)
        _write(root / "references" / "handouts" / f"{h.id}.md", f"# {h.title}（{h.kind}）\n\n```text\n{h.text.rstrip()}\n```\n{images}\n")
    _write(root / "references" / "events.md", _events_md(m))
    _write(root / "references" / "endings.md", _endings_md(m))
    if m.pregens:
        _write(root / "references" / "pregens.md", _pregens_md(m))
    if _has_situation(m):
        _write(root / "references" / "situation.md", _situation_md(m))
    if m.art:
        (root / "assets" / "ascii").mkdir(parents=True)
        for name, text in m.art.items():
            _write(root / "assets" / "ascii" / f"{name}.txt", text + "\n")
    if m.pictures:
        destination = root / "assets" / "images"
        destination.mkdir(parents=True, exist_ok=True)
        for path in m.pictures.values():
            picture = Path(path)
            shutil.copy2(picture, destination / picture.name)
            if (record := credits.get(picture)) is not None:
                credits.put(destination / picture.name, record)
    if m.extensions:
        from ..plugins import api_text
        from ..plugins.storage import save as save_plugin

        for plugin in m.extensions.values():
            save_plugin(plugin, root / "assets" / "plugins")
        _write(root / "references" / "plugins.md", api_text())
    if source is not None and source.is_dir():
        shutil.copytree(source, root / "assets" / "source")
        with (root / "SKILL.md").open("a", encoding="utf-8") as output:
            output.write("\n## 完整原始资料（仅守秘人可读，含剧透）\n\n")
            output.write("原文、逐页扫描和未展示图片保留在 `assets/source/`；细节有疑问时回查原文，不用整理摘要代替全部资料。\n")
            output.write("- [原文](assets/source/source.md)\n- [逐页扫描](assets/source/pages/)\n- [原图档案](assets/source/images/)\n")
    _write(root / ".stamp", stamp(m, source) + "\n")
    return root


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _check(c: Clue) -> str:
    if not c.check:
        return "搜到即得"
    s = f"{c.check.skill}（{c.check.difficulty}）"
    if c.check.bonus:
        s += f"，{'奖励' if c.check.bonus > 0 else '惩罚'}骰 {abs(c.check.bonus)}"
    return s


def _skill_md(m: Module) -> str:
    support = supporting_clues(m)
    desc = (
        f"CoC 7 版模组《{m.title}》（{m.era}，{m.setting}，{m.players}，{m.duration}）的守秘人资料。"
        f"跑这个模组、回答它的剧情与规则问题、或需要其中某个地点、NPC、线索、手稿的细节时使用。"
    )
    front = {
        "name": skill_name(m),
        "description": desc[:1000],
        "license": m.license or "proprietary",
        "metadata": {"module": m.id, "version": version(m), "source": m.source},
    }
    lines = [
        "---",
        yaml.safe_dump(front, allow_unicode=True, sort_keys=False).rstrip(),
        "---",
        "",
        f"# 《{m.title}》守秘人总览",
        "",
        f"{m.subtitle}。{m.era}，{m.setting}；{m.players}，{m.duration}。",
        "",
        "## 真相（不要直接告诉玩家）",
        "",
        m.truth.rstrip(),
        "",
        "## 开场钩子（可以读给玩家）",
        "",
        m.hook.rstrip(),
        "",
        "## 怎么跑",
        "",
        m.keeper_notes.rstrip(),
        "",
        f"开局时间 {m.start_time}，开局地点 `{m.start}`。",
        "",
        "## 开场素材与引入",
        "",
        m.opening.rstrip(),
        "",
        "从抵达、生活和来意自然引入，在起始地点把行动交给玩家；尚未发生的赶路、拜访和调查不能替玩家完成。",
        "日常可以没有线索和危险，玩家想停留就展开，想赶路就简述。",
        "",
        "## 地点（调查员进入时再读对应文件）",
        "",
        "| id | 地点 | 文件 |",
        "|---|---|---|",
    ]
    lines += [f"| `{loc.id}` | {loc.name} | [references/locations/{loc.id}.md](references/locations/{loc.id}.md) |" for loc in m.locations]
    lines += ["", "## NPC（对话前再读对应文件）", "", "| id | 名字 | 身份 | 文件 |", "|---|---|---|---|"]
    lines += [f"| `{n.id}` | {n.name} | {n.role} | [references/npcs/{n.id}.md](references/npcs/{n.id}.md) |" for n in m.npcs]
    lines += ["", "## 结论与支撑线索", ""]
    for k in m.conclusions:
        lines.append(f"- **{k.id}** {k.text} ← {', '.join(f'`{c}`' for c in support.get(k.id, [])) or '（无）'}")
    lines += ["", "## 剧情标记", ""]
    lines += [f"- `{flag}`：{meaning}" for flag, meaning in m.flags.items()]
    lines += [
        "",
        "## 其他资料",
        "",
        "- 时间线与事件：[references/events.md](references/events.md)",
        "- 结局：[references/endings.md](references/endings.md)",
        "- 手稿全文：`references/handouts/<id>.md`",
    ]
    if m.pregens:
        lines.append("- 预设调查员：[references/pregens.md](references/pregens.md)")
    if _has_situation(m):
        lines.append("- 局面（人物关系、各人的打算与依据、伏笔、前史、规则清单）：[references/situation.md](references/situation.md)")
    if m.mechanics:
        lines += ["", "## 规则清单（谁来执行）", ""]
        lines += [f"- **{x.name}**（{x.executor}）：{x.rules.strip()}" if x.executor == "守秘人" else f"- **{x.name}**（{x.executor}）"
                  for x in m.mechanics]
        lines += ["", "写着「守秘人」的由你临场裁定执行；核心规则和插件已经结算的，不再口头重复结算。"]
    if m.art:
        lines.append("- ASCII 插图：`assets/ascii/<name>.txt`")
    if m.pictures:
        lines += ["", "## 原作图片（按场景或手稿解锁，不提前展示）", ""]
        lines += [f"- `{key}`：[原图](assets/images/{Path(path).name})" for key, path in m.pictures.items()]
    if m.extensions:
        lines += ["", "## 自定义规则插件（由引擎执行）", ""]
        lines += [f"- `{p.id}` v{p.version}：{p.description}" for p in m.extensions.values()]
        lines += ["", "源码与可执行测试见 `assets/plugins/<id>/`；接口见 [references/plugins.md](references/plugins.md)。",
                  "引擎已结算的插件效果不能再口头重复结算；手动主持时先读源码和原文核对。"]
    lines += ["", "需要检定时配合 `coc7-core`，理智检定配合 `coc7-sanity`，叙事风格见 `keeper-craft`。", ""]
    return "\n".join(lines)


def _location_md(m: Module, loc: Location) -> str:
    out = [f"# {loc.name}（`{loc.id}`）", "", "## 读给玩家", "", loc.read_aloud.rstrip(), ""]
    if loc.keeper:
        out += ["## 守秘人须知", "", loc.keeper.rstrip(), ""]
    if loc.features:
        out += ["## 可调查", "", "、".join(loc.features), ""]
    for item in m.items:
        if item.where == loc.id:
            out += [f"物件 {item.name}（{item.id}）：{item.description}", ""]
    exits = [f"`{e}` {m.location(e).name}" for e in loc.exits if any(x.id == e for x in m.locations)]
    if exits:
        out += ["## 出口", "", "；".join(exits), ""]
    outer = next((x for x in m.locations if x.id == loc.within), None)
    inner = [f"`{x.id}` {x.name}" for x in m.locations if x.within == loc.id]
    if outer or inner:  # what lies in what: the players' map draws it once they know both
        out += ["## 位置", "", *([f"在 `{outer.id}` {outer.name} 里面"] if outer else []), *([f"里面有 {'；'.join(inner)}"] if inner else []), ""]
    npcs = m.npcs_at(loc.id)
    if npcs:
        out += ["## 在场 NPC", ""] + [f"- `{n.id}` {n.name}：{n.role}" for n in npcs] + [""]
    clues = m.clues_at(loc.id)
    if clues:
        out += ["## 线索", ""]
        for c in clues:
            where = c.feature or (m.npc(c.npc).name if c.npc else "")
            extra = []
            if c.core:
                extra.append("关键")
            if c.sanity:
                extra.append(f"理智 {c.sanity}")
            if c.handout:
                extra.append(f"手稿 `{c.handout}`")
            if c.requires:
                extra.append("前置条件全部满足：" + "、".join(c.requires))
            tag = f"（{'，'.join(extra)}）" if extra else ""
            out.append(f"- `{c.id}` **{c.title}**｜{where}｜{_check(c)}{tag}")
            out.append(f"  {c.text.strip()}")
        out.append("")
    if loc.art:
        out += [f"插图：`assets/ascii/{loc.art}.txt`", ""]
    return "\n".join(out)


def _npc_md(m: Module, n: NPC) -> str:
    out = [f"# {n.name}（`{n.id}`）", "", f"**身份**：{n.role}"]
    for label, value in (("外貌", n.look), ("性格", n.personality), ("说话方式", n.voice), ("想要", n.goal)):
        if value:
            out.append(f"**{label}**：{value}")
    if n.location:
        out.append(f"**通常在**：{m.location(n.location).name}")
    for label, items in (("知道", n.knows), ("秘密（不被逼问不说）", n.secrets), ("会撒的谎", n.lies)):
        if items:
            out += ["", f"## {label}", ""] + [f"- {x}" for x in items]
    if n.clues:
        out += ["", "## 能给的线索", ""]
        for cid in n.clues:
            c = m.clue(cid)
            gate = n.gates.get(cid)
            need = f"需要 {gate.skill}（{gate.difficulty}）" if gate else "问到就说"
            out.append(f"- `{cid}` {c.title}：{need}")
    if n.stats:
        out += ["", "## 数值", "", "，".join(f"{k} {v}" for k, v in n.stats.items())]
    if n.portrait:
        out += ["", f"头像：`assets/ascii/{n.portrait}.txt`"]
    return "\n".join(out) + "\n"


def _events_md(m: Module) -> str:
    out = ["# 时间线与事件", ""]
    for e in m.events:
        out.append(f"## {e.when}　{e.title}（`{e.id}`）")
        out.append("")
        if e.read_aloud:
            out += [f"> {e.read_aloud.strip()}", ""]
        out += [e.text.strip(), ""]
        meta = []
        if e.sanity:
            meta.append(f"理智 {e.sanity}")
        if e.move_to:
            meta.append(f"地点 `{e.move_to}`")
        if e.performer:
            meta.append(f"表演者 `{e.performer}`：{e.performance}")
        if e.ending:
            meta.append(f"进入结局 `{e.ending}`")
        if e.sets:
            meta.append("设置标记 " + "、".join(f"`{f}`" for f in e.sets))
        if meta:
            out += ["；".join(meta), ""]
    return "\n".join(out)


def _endings_md(m: Module) -> str:
    out = ["# 结局", ""]
    for e in m.endings:
        out += [f"## {e.title}（`{e.id}`）", "", f"条件：{e.condition}", "", e.text.strip(), ""]
        if e.reward:
            out += [f"奖励 / 代价：{e.reward}", ""]
    return "\n".join(out)


def _has_situation(m: Module) -> bool:
    return bool(m.relations or m.agendas or m.foreshadow or m.ties or m.history or m.mechanics)


def _step_md(s: Any) -> str:
    """One step of an agenda with all it rests on: when, what it brings about, whose it is to play."""
    if s.ruling:
        kind = "要守秘人裁定后才发生"
    elif s.meet:
        kind = "见面反应，" + {True: "只做一次", False: "一贯如此，碰上了才带出"}.get(s.once, "可以这样演")
    else:
        kind = "幕后" + ("，到时直接照做" if s.does and s.when else "，到时由他自己决定")
    meta = [kind, f"条件 `{s.when}`" if s.when else "", f"做成以后 `{s.does}`" if s.does else "",
            f"只在：{s.only_if}" if s.only_if else "", f"依据：{s.source}" if s.source else ""]
    return f"- `{s.id}` {s.text.strip()}（{'；'.join(x for x in meta if x)}）"


def _situation_md(m: Module) -> str:
    names = {n.id: n.name for n in m.npcs} | {"players": "调查员们"}
    out = ["# 局面", "", "编译期备好的人物动机、日程、触发条件与规则，供临场取用：见面反应是可以这样演，不是剧本；"
           "写着「要守秘人裁定」的只在你裁定以后才发生；原作的检定与前置条件照原作。", ""]
    if m.relations:
        out += ["## 人物关系", ""] + [f"- {names.get(b.a, b.a)} → {names.get(b.b, b.b)}：{b.kind} {b.value:+d}" + (f"，{b.why}" if b.why else "")
                                     for b in m.relations] + [""]
    for a in m.agendas:
        out += [f"## {names.get(a.npc, a.npc)}（`{a.npc}`）的打算", ""]
        if a.goals:
            out += [f"想要：{'；'.join(a.goals)}", ""]
        out += [_step_md(s) for s in a.steps] + [""]
    if m.foreshadow:
        out += ["## 伏笔", ""] + [f"- `{f.id}` 在 `{f.where}`：{f.hint} → `{f.pays_off}`" for f in m.foreshadow] + [""]
    if m.history:
        out += ["## 前史", ""]
        for p in m.history:
            who = "、".join(names.get(x, x) for x in p.who)
            also = "、".join(names.get(x, x) for x in p.known_by)
            out.append(f"- {p.when}：{p.text}" + (f"（牵涉 {who}）" if who else "") + (f"（另外知道：{also}）" if also else ""))
        out.append("")
    if m.ties:
        out += ["## 私人牵连", ""] + [f"- `{t.id}` {t.hook}" + (f"（{t.npc or t.place}）" if t.npc or t.place else "") for t in m.ties] + [""]
    if m.mechanics:
        out += ["## 规则清单", ""]
        for x in m.mechanics:
            out += [f"### {x.name}（{x.executor}执行）", "", x.rules.strip(), ""] + ([f"用在：{x.why}", ""] if x.why else [])
    return "\n".join(out)


def _pregens_md(m: Module) -> str:
    out = ["# 预设调查员", ""]
    for p in m.pregens:
        stats = "，".join(f"{k}{v}" for k, v in p.characteristics.items())
        skills = "，".join(f"{k}{v}" for k, v in sorted(p.skills.items(), key=lambda kv: -kv[1]))
        out += [f"## {p.name}（{p.occupation}，{p.age} 岁）", "", stats + f"，幸运{p.luck}", "", skills, ""]
    return "\n".join(out)
