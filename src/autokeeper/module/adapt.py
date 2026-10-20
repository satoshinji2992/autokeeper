"""Compile-time adaptation: what a module needs to play well that its text leaves out.

Built as a situation, not a plot (no acts, no planned path through it): who wants what, how
they stand with each other, what comes of it if nobody steps in, what can be glimpsed early,
which threads an investigator's own past can be tied to. Each pass fills one thing the module
lacks and leaves what it has:

    npcs        人物补全    empty personality, voice, goal and what each NPC knows
    relations   冲突图      how the people see each other (Module.relations)
    agendas     局面        what the important NPCs mean to do if nobody steps in (Module.agendas)
    foreshadow  伏笔        early signs, and what each one leads to (Module.foreshadow)
    clueweb     线索网      a conclusion with fewer than three clues gets more (the three-clue rule)
    ties        私人牵连    threads an investigator's background can be tied to (Module.ties)
    endings     额外结局    the ends a table is likely to reach that the module never wrote
    mechanics   插件清单    the rules the engine does not keep by itself (Module.mechanics)
    prelude     开局演出    a few pictures and lines on the story's background, played before the
                            game screen while the director adapts (module/prelude.py); it reads
                            only what the players are told, never the keeper's book

The model calls of the passes asked for run at once; their edits land one pass at a time and a
pass whose edits break the module's checks is taken back whole. The module's truth, its clues'
words, checks and endings are never changed: everything here is added.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .. import config
from ..harness import Session
from . import package, prelude, yamledit
from .graph import audit, supporting_clues
from .schema import Agenda, Bond, Foreshadow, Mechanic, Module, PreludeFrame, Tie

PASSES = ("npcs", "relations", "agendas", "foreshadow", "clueweb", "ties", "endings", "mechanics", "prelude")
LABELS = {"npcs": "人物补全", "relations": "冲突图", "agendas": "局面（重要 NPC 的打算）", "foreshadow": "伏笔", "clueweb": "线索网",
          "ties": "私人牵连", "endings": "额外结局", "mechanics": "插件清单", "prelude": "开局演出"}  # fmt: skip

SYSTEM = """你是 AutoKeeper 的模组改编员，在编译期给一个 CoC 7 版模组补上跑团时用得着、原文却没写的东西。
- 原作的真相、线索原文、检定、结局一个字都不改；补上的东西要和原作对得上，像本来就在里面。
- 按局面来想，不按剧情：写清谁想要什么、彼此怎样、没人插手时事情会怎么发展；不预设调查员会怎么走，不分幕。
- id 用英文小写或拼音加下划线，不和已有的重复；引用的地点、NPC、线索、结论、事件、结局只用资料里有的。
- 写短，写具体：一个人、一样东西、一件事，不写空话。"""


class NpcFill(BaseModel):
    npc: str = Field(description="NPC id")
    personality: str = Field("", description="性格，一句话；原来有就留空")
    voice: str = Field("", description="说话方式、口头禅；原来有就留空")
    goal: str = Field("", description="他想要什么；原来有就留空")
    knows: list[str] = Field(default_factory=list, description="他按原作会知道、肯说的事；原来有就留空，不添新案情")


class NpcFills(BaseModel):
    npcs: list[NpcFill] = Field(default_factory=list)


class Bonds(BaseModel):
    relations: list[Bond] = Field(default_factory=list)


class Agendas(BaseModel):
    agendas: list[Agenda] = Field(default_factory=list)


class Signs(BaseModel):
    foreshadow: list[Foreshadow] = Field(default_factory=list)


class NewClue(BaseModel):
    id: str
    title: str = Field(description="线索本里的短标题")
    text: str = Field(description="调查员得到的信息（玩家可见），不比原作的关键线索说得多，不直接说出真相")
    where: str = Field(description="所在地点 id")
    feature: str = Field(description="具体在哪样东西上、哪里")
    skill: str = Field("", description="需要什么检定，如 侦查、图书馆使用；找到就有就留空")
    difficulty: Literal["常规", "困难", "极难"] = "常规"
    points_to: list[str] = Field(description="支持哪个结论 id")


class NewClues(BaseModel):
    clues: list[NewClue] = Field(default_factory=list)


class Ties(BaseModel):
    ties: list[Tie] = Field(default_factory=list)


class NewEnding(BaseModel):
    id: str
    title: str
    flag: str = Field(description="触发它的剧情标记 id，新的")
    meaning: str = Field(description="这个标记的含义：调查员的行动真正达成了什么时它才成立")
    text: str = Field(description="结局的叙述，100–200 字")


class Endings(BaseModel):
    endings: list[NewEnding] = Field(default_factory=list, max_length=2)


class Mechanics(BaseModel):
    mechanics: list[Mechanic] = Field(default_factory=list)


class Prelude(BaseModel):
    prelude: list[PreludeFrame] = Field(default_factory=list, max_length=prelude.MAX_FRAMES)


ASK: dict[str, tuple[str, type[BaseModel]]] = {
    "npcs": ("给下面标着“缺”的 NPC 补上空着的字段：personality 性格、voice 说话方式、goal 想要什么、knows 他知道的事。"
             "只补空着的，已有的不动；knows 只写他按原作会知道的事，不添新的案情。", NpcFills),
    "relations": ("画出人物之间的冲突图：谁对谁怎样看（信任、恐惧、欠情、敌对、亲属、雇佣、爱慕……），多深（-100..100，负数是反过来），为什么。"
                  "写 6–14 条，先写会推动局面的：利益冲突、隐瞒、亏欠、旧怨。b 也可以写 players：他对外来调查员的态度。", Bonds),
    "agendas": ("挑 3–6 个最有自己打算的 NPC（标了“重要”的优先），写他们想要什么，以及没人插手时接下来会怎么做，每人 2–5 步。"
                "when 写 'D1 23:00' 这样的时间，或条件 flag:标记、at:谁:地点。和原作的事件时间线对得上，不替原作的事件抢戏。"
                "有 when 的步骤写 does：做成以后实际成立的事（at:他自己:地点、owner:物件:持有者、flag:标记、gone:谁），游戏里到时直接照做；"
                "要等调查员来问、来做的事写不成条件，不写 when 也不写 does，交给人物临场决定；"
                "does 里的 flag 只用表示世界里发生了某事的标记，不用表示调查员看见、遇到、做到或知道了什么的标记（那些由裁定员按调查员的行动立）；"
                "物件只用【物件】里的 id，交给调查员写 players；别人去哪不写在他的 does 里。原作里会把剧情推向某个结局的，用那个结局的标记。", Agendas),
    "foreshadow": ("写 3–6 条伏笔：调查员早早能在某个地点看见、听见的一点端倪，最后指向某个结论、事件或结局。"
                   "端倪本身不说破真相，只让人觉得哪里不对。", Signs),
    "clueweb": ("三线索原则：每个结论至少要有三条独立的线索能推出来。下面列着每个结论现有的线索；不够三条的，补一两条新线索："
                "在哪个地点、哪样东西上，要什么检定，指向哪个结论。新线索不比原作的关键线索说得多，不直接说出真相，放在调查员会去的地方。", NewClues),
    "ties": ("写 4–8 条私人牵连：调查员背景里的重要之人、意义非凡之地、思想与信念、宝贵之物、职业，可以怎样接上这个模组的人和地方。"
             "每条牵到一个 NPC 或地点，一句话说清这层关系。开局时导演按调查员的背景挑着用。", Ties),
    "endings": ("原作的结局之外，补 1–2 个玩家很可能走到、原作却没写的结局：放弃调查离开、和对手做了交易、只救下一部分人……"
                "每个写一个新的剧情标记（写清调查员的行动真正达成什么时它才成立）和结局叙述。不改原作的结局。", Endings),
    "mechanics": ("列出模组里引擎自己不会管、要插件来管的规则：时钟和倒计时、资源、疾病、仪式进度、追逐、特殊的检定。"
                  "每条写规则本身（什么时候、怎么算、有什么后果）和模组里哪里用到。原作没有这样的规则就留空。", Mechanics),
    "prelude": ("写开局演出：进入游戏前播放的 3–5 幕，像电影开头那样一张图配一两句字幕，按先后排，只介绍故事背景："
                "时代、地方、气氛、事情是怎么找上门的。图只从【可用的图】里挑，没有合适的就留空。"
                "只用上面玩家知道的内容，不写调查员会怎么做、会发现什么，每幕不超过 60 字。", Prelude),
}


def book(m: Module) -> str:
    """The module as the adapter reads it: the keeper's whole book, short."""
    lines = [f"【模组】《{m.title}》 {m.era}，{m.setting}", f"【开场钩子】{m.hook.strip()}", f"【真相】{m.truth.strip()}"]
    if m.keeper_notes.strip():
        lines.append(f"【守秘人须知】{m.keeper_notes.strip()[:1200]}")
    lines.append("【地点】\n" + "\n".join(f"- {x.id} {x.name}：{x.read_aloud.strip()[:80]}" for x in m.locations))
    lines.append("【NPC】\n" + "\n".join(
        f"- {n.id} {n.name}（{n.role}）{'，重要' if n.important else ''}，在 {n.location}；想要：{n.goal or '缺'}；性格：{n.personality or '缺'}；"
        f"说话：{n.voice or '缺'}；知道：{'；'.join(n.knows) or '缺'}；秘密：{'；'.join(n.secrets) or '无'}" for n in m.npcs))
    if m.items:
        lines.append("【物件】\n" + "\n".join(f"- {i.id} {i.name}，在 {i.owner or i.where or '不详'}" for i in m.items))
    lines.append("【线索】\n" + "\n".join(f"- {c.id}「{c.title}」在 {c.where}{'·' + c.feature if c.feature else ''}，指向 {'、'.join(c.points_to) or '无'}"
                                        + ("，关键" if c.core else "") for c in m.clues))  # fmt: skip
    lines.append("【结论】\n" + "\n".join(f"- {k.id}：{k.text}" for k in m.conclusions))
    lines.append("【事件】\n" + "\n".join(f"- {e.id}「{e.title}」{e.when}" for e in m.events))
    lines.append("【结局】\n" + "\n".join(f"- {e.id}「{e.title}」条件 {e.condition}" for e in m.endings))
    lines.append("【剧情标记】" + "；".join(f"{k}：{v}" for k, v in m.flags.items()))
    return "\n".join(lines)


def wanted(m: Module, name: str) -> bool:
    """Whether the module lacks what a pass fills."""
    if name == "npcs":
        return any(not (n.personality and n.voice and n.goal and n.knows) for n in m.npcs)
    if name == "clueweb":
        from .simulate import playthrough

        run = playthrough(m)
        return bool(run.lean) or any(run.fragile.values()) or bool(run.drift.lost)
    if name == "endings":
        return len(m.endings) < 3
    return not getattr(m, name)


def _material(m: Module, name: str) -> str:
    if name == "clueweb":
        from .simulate import playthrough

        run = playthrough(m)  # which clues a table can actually reach, and what a conclusion cannot do without
        support = supporting_clues(m)
        rows = []
        for k, v in support.items():
            got = [c for c in v if c in run.reach.clues]
            hangs = run.fragile.get(k) or []
            rows.append(f"- {k}：拿得到的 {'、'.join(got) or '没有'}（{len(got)} 条）" + (f"；离不开{'、'.join(hangs)}，新线索放到别处、别人口中" if hangs else ""))
        text = "\n【每个结论现有的线索】\n" + "\n".join(rows)
        if run.drift.lost:  # what the agendas take away if no one steps in: the new way must not hang on it
            from .simulate import _clock

            text += "\n【没人插手时会断的结论】\n" + "\n".join(
                f"- {k}：{_clock(t)}「{why}」之后一条线索也拿不到了，新线索要放在这一步动不到的地方和人身上" for k, (t, why) in run.drift.lost.items())
        return text
    return ""


def _public(m: Module) -> str:
    """What the players are told before play, for the opening performance: never the keeper's book."""
    start = next((x for x in m.locations if x.id == m.start), None)
    lines = [f"【模组】《{m.title}》{m.subtitle}", f"【时代与地方】{m.era}，{m.setting}", f"【开场钩子（玩家知道）】{m.hook.strip()}"]
    if start is not None:
        lines.append(f"【开场地点】{start.name}：{start.read_aloud.strip()}")
    lines.append("【可用的图】\n" + ("\n".join(f"- {key}：{what}" for key, what in prelude.public_art(m).items()) or "- 没有"))
    return "\n".join(lines)


async def _ask(cfg: config.Config, name: str, m: Module) -> BaseModel:
    task, schema = ASK[name]
    s = Session("importer", cfg.role("importer"), SYSTEM, schema=schema.model_json_schema())
    material = _public(m) if name == "prelude" else f"{book(m)}{_material(m, name)}"
    try:
        reply = await s.send(f"{material}\n\n【这次要做的】{LABELS[name]}：{task}")
        return schema.model_validate(reply.data or {})
    finally:
        await s.close()


Edit = tuple[str, ...] | tuple[Any, ...]


def edits(m: Module, name: str, out: Any) -> list[tuple[Any, ...]]:
    """What a pass's answer writes: ("put", group, id, field, value), ("append", group, items), ("key", group, key, value)."""
    npcs, places = {n.id for n in m.npcs}, {x.id for x in m.locations}
    if name == "npcs":
        found = []
        for f in out.npcs:
            n = next((x for x in m.npcs if x.id == f.npc), None)
            if n is None:
                continue
            for key in ("personality", "voice", "goal"):
                if not getattr(n, key) and getattr(f, key).strip():
                    found.append(("put", "npcs", n.id, key, getattr(f, key).strip()))
            if not n.knows and f.knows:
                found.append(("put", "npcs", n.id, "knows", [k.strip() for k in f.knows if k.strip()][:6]))
        return found
    if name == "clueweb":
        taken = {c.id for c in m.clues}
        conclusions = {k.id for k in m.conclusions}
        new = []
        for c in out.clues:
            if c.id in taken or c.where not in places or not set(c.points_to) & conclusions or not c.text.strip():
                continue
            taken.add(c.id)
            item: dict[str, Any] = {"id": c.id, "title": c.title, "text": c.text.strip(), "where": c.where, "feature": c.feature}
            if c.skill:
                item["check"] = {"skill": c.skill, "difficulty": c.difficulty}
            item["points_to"] = [k for k in c.points_to if k in conclusions]
            new.append(item)
        return [("append", "clues", new)] if new else []
    if name == "endings":
        found = []
        taken = {e.id for e in m.endings}
        for e in out.endings:
            flag = re.sub(r"[^a-z0-9_]", "_", e.flag.lower()).strip("_")
            if e.id in taken or not flag or flag in m.flags or not e.text.strip():
                continue
            found.append(("key", "flags", flag, e.meaning.strip()))
            found.append(("append", "endings", [{"id": e.id, "title": e.title, "condition": f"flag:{flag}", "text": e.text.strip()}]))
        return found
    if name == "prelude":  # a shot that shows what is not public yet, or says too much, is left out
        art = prelude.public_art(m)
        shots = [f.model_copy(update={"art": f.art if f.art in art else ""}) for f in out.prelude]
        shots = [f for f in shots if not prelude.shot(m, f)][: prelude.MAX_FRAMES]
        return [("append", "prelude", [f.model_dump(mode="json", exclude_defaults=True) for f in shots])] if len(shots) >= 2 else []
    items: list[BaseModel] = getattr(out, name)
    if name == "relations":
        items = [b for b in items if b.a in npcs and (b.b in npcs or b.b == "players") and b.a != b.b]
    elif name == "agendas":
        from .graph import _outcome

        seen: set[str] = set()
        items = [a for a in items if a.npc in npcs and not (a.npc in seen or seen.add(a.npc)) and a.steps][:6]  # type: ignore[func-returns-value]
        for a in items:  # an outcome naming what the module lacks is cut down to what it has, not the whole pass
            for step in a.steps:
                parts = [x.strip() for x in step.does.replace("|", "&").split("&") if x.strip() and "!" not in x]
                # Without a time or a condition the step is the NPC's to judge in play: no outcome done for it.
                step.does = " & ".join(x for x in parts if not _outcome(m, a.npc, x)) if step.when.strip() else ""
    elif name == "foreshadow":
        targets = {k.id for k in m.conclusions} | {e.id for e in m.events} | {e.id for e in m.endings}
        items = [f for f in items if f.where in places and f.pays_off in targets]
    elif name == "ties":
        items = [t for t in items if (t.npc in npcs or not t.npc) and (t.place in places or not t.place) and t.hook.strip()]
    dumped = [x.model_dump(mode="json", exclude_defaults=True) for x in items]
    return [("append", name, dumped)] if dumped else []


def _target(folder: Path, group: str) -> Path:
    """The file a top-level group goes in: its part file when the module is split (made if new)."""
    part = next((n for n, keys in package.PARTS.items() if group in keys), None)
    split = any((folder / n).is_file() for n in package.PARTS)
    return folder / part if part and split else yamledit.where(folder, group)


def land(folder: Path, found: list[tuple[Any, ...]]) -> None:
    for edit in found:
        kind, group = edit[0], edit[1]
        path = _target(folder, group)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if kind == "put":
            text = yamledit.put(text, group, edit[2], edit[3], edit[4])
        elif kind == "append":
            text = yamledit.append(text, group, edit[2])
        else:
            text = yamledit.put_key(text, group, edit[2], edit[3])
        path.write_text(text, encoding="utf-8")


@dataclass
class Result:
    name: str
    written: int = 0
    problems: list[str] | None = None


async def adapt(folder: Path, *, passes: tuple[str, ...] = PASSES, cfg: config.Config | None = None,
                log: Callable[[str], None] = print, dry: bool = False) -> list[Result]:  # fmt: skip
    """Run the passes the module still needs: the model calls at once, the edits one pass at a time,
    each pass taken back whole if the module no longer passes its checks."""
    folder = Path(folder)
    cfg = cfg or config.load()
    m = Module.load(folder)
    todo = [name for name in passes if wanted(m, name)]
    for name in passes:
        if name not in todo:
            log(f"[dim]{LABELS[name]}：已经有了[/]")
    if not todo:
        return []
    log(f"[bold]编译期改编[/] {len(todo)} 步一起问：{'、'.join(LABELS[x] for x in todo)}…")
    answers = await asyncio.gather(*(_ask(cfg, name, m) for name in todo), return_exceptions=True)
    results = []
    for name, out in zip(todo, answers, strict=True):
        if isinstance(out, BaseException):
            log(f"  [yellow]✗ {LABELS[name]}：{type(out).__name__}[/]")
            results.append(Result(name, problems=[f"{type(out).__name__}: {out}"[:200]]))
            continue
        m = Module.load(folder)
        found = edits(m, name, out)
        count = sum(len(e[2]) if e[0] == "append" else 1 for e in found)
        if dry or not found:
            log(f"  {LABELS[name]}：{count} 处" + ("（只看不写）" if dry else ""))
            results.append(Result(name, count))
            continue
        before = {p: p.read_text(encoding="utf-8") if p.is_file() else None for p in {_target(folder, e[1]) for e in found}}
        errors_before = {str(i) for i in audit(m) if i.level == "error"}
        try:
            land(folder, found)
            new_errors = [str(i) for i in audit(Module.load(folder)) if i.level == "error" and str(i) not in errors_before]
        except Exception as exc:  # malformed YAML, a missing entry: the pass is taken back
            new_errors = [f"{type(exc).__name__}: {exc}"[:200]]
        if new_errors:
            for path, text in before.items():
                path.unlink(missing_ok=True) if text is None else path.write_text(text, encoding="utf-8")
            log(f"  [yellow]✗ {LABELS[name]}：补上以后校验不过，没有写入（{new_errors[0]}）[/]")
            results.append(Result(name, problems=new_errors))
            continue
        log(f"  [green]✓ {LABELS[name]}[/]：补了 {count} 处")
        results.append(Result(name, count))
    return results


# -- how ready a module is to play -------------------------------------------------------------
@dataclass
class Score:
    total: int  # 0–100
    parts: dict[str, tuple[int, int, str]]  # name -> (points, out of, why)


def score(m: Module) -> Score:
    """A module's readiness for play, from its structure alone: no model call. Not whether the
    fields are filled but whether a keeper can run it as a situation:

        校验      no errors
        推演      everything in it reachable, no conclusion hanging on one hidden place or one person
                  (module/simulate.py: a playthrough with no model)
        线索网    every conclusion reachable three ways, and not all of them in one place
        人物      each NPC with a want, a manner, a voice and something to say
        冲突      the important people at odds or bound to someone else, not only to the party
        局面      what they mean to do moves on its own: some of it at an hour, not only on the party's cue
        伏笔      early signs, as many as there are conclusions, and leading to more than one
        牵连      threads for the kinds of background an investigator has
        结局      more than one way out, and not every one waiting on the party
    """
    from .simulate import playthrough

    npcs = {n.id: n for n in m.npcs}
    run = playthrough(m)
    support = {k: [c for c in v if c in run.reach.clues] for k, v in supporting_clues(m).items()}
    where = {c.id: c.npc or c.where for c in m.clues}
    errors = [i for i in audit(m) if i.level == "error"]
    parts: dict[str, tuple[int, int, str]] = {}

    def part(name: str, share: float, out_of: int, why: str) -> None:
        parts[name] = (round(max(0.0, min(1.0, share)) * out_of), out_of, why)

    part("校验", 0.0 if errors else 1.0, 10, f"{len(errors)} 个错误" if errors else "通过")
    part("推演", run.share(), 15, f"{len(run.issues)} 处问题" if run.issues else "走得通，没有只靠一处的结论")
    shares = []
    for clues in support.values():
        spread = len({where[c] for c in clues})
        shares.append(min(1.0, len(clues) / 3) * (1.0 if spread >= 2 else 0.6))
    three = sum(len(v) >= 3 for v in support.values())
    part("线索网", sum(shares) / len(shares) if shares else 0, 15,
         f"{three}/{len(support)} 个结论有三条以上拿得到的线索" + ("" if all(x == 1 for x in shares) else "；有的只出自一个地方或一个人"))  # fmt: skip
    full = sum(bool(n.personality and n.voice and n.goal and n.knows) for n in npcs.values())
    part("人物", full / len(npcs) if npcs else 0, 10, f"{full}/{len(npcs)} 个 NPC 性格、口吻、想要的、知道的都有")
    important = {n.id for n in npcs.values() if n.important} | {a.npc for a in m.agendas if a.npc in npcs}
    bound = {x for b in m.relations if b.a in npcs and b.b in npcs and abs(b.value) >= 30 for x in (b.a, b.b)}
    at_odds = any(b.a in npcs and b.b in npcs and b.value <= -30 for b in m.relations)
    tied = len(important & bound) / len(important) if important else 0
    part("冲突", tied * (1.0 if at_odds else 0.6), 15,
         f"{len(important & bound)}/{len(important)} 个重要 NPC 和别的 NPC 有分量的关系" + ("" if at_odds else "；没有谁和谁真正对立"))  # fmt: skip
    steps = [s for a in m.agendas for s in a.steps]
    timed = sum(bool(re.fullmatch(r"D\d+ \d{1,2}:\d{2}", s.when.strip())) for s in steps)
    moving = min(1.0, len(m.agendas) / 3) * (1.0 if timed else 0.5)
    part("局面", moving, 20, f"{len(m.agendas)} 个 NPC 有自己的打算，{timed} 步到点就做" + ("" if timed else "（全靠调查员触发，局面不会自己动）"))
    aims = {f.pays_off for f in m.foreshadow}
    part("伏笔", min(1.0, len(m.foreshadow) / max(3, len(m.conclusions))) * (1.0 if len(aims) >= 2 else 0.6), 5,
         f"{len(m.foreshadow)} 条，指向 {len(aims)} 处")  # fmt: skip
    kinds = {k for t in m.ties for k in t.fits}
    part("牵连", min(1.0, len(m.ties) / 4) * min(1.0, len(kinds) / 3), 5, f"{len(m.ties)} 条，覆盖 {len(kinds)} 类背景")
    ends = run.reach.endings
    coming = any(how == "事件" for how in ends.values())
    part("结局", (len(ends) - 1) / 2 * (1.0 if coming else 0.6), 5,
         f"{len(ends)}/{len(m.endings)} 个到得了" + ("" if coming else "；没有一个会随时间或事件自己到来"))  # fmt: skip
    return Score(sum(p[0] for p in parts.values()), parts)


REVIEW_SYSTEM = """你是 CoC 7 版跑团的资深守秘人，替一个准备开团的同行审读模组。只挑跑起来会出问题的地方：
- 关键信息只有一条路，玩家错过就卡死；
- 前后矛盾：时间、地点、谁知道什么对不上；
- 人物动机说不通，或者没人插手时局面不会动；
- 原文写得含糊、守秘人当场没法裁定的规则或情形。
每条写清在哪、为什么会出问题、怎么补（补，不改原作真相）。没有问题的方面不要写。最多 8 条，按严重程度排。"""


class Finding(BaseModel):
    where: str = Field(description="在哪：地点、NPC、线索、事件或结局的 id，或“整体”")
    problem: str = Field(description="跑起来会出什么问题，一句话")
    fix: str = Field(description="怎么补，一句话；不改原作真相")
    severity: Literal["高", "中", "低"] = "中"


class Review(BaseModel):
    findings: list[Finding] = Field(default_factory=list, max_length=8)


async def review(folder: Path, cfg: config.Config | None = None) -> Review:
    """A keeper's read of the module before the table (a report: nothing is written)."""
    cfg = cfg or config.load()
    m = Module.load(folder)
    s = Session("importer", cfg.role("importer"), REVIEW_SYSTEM, schema=Review.model_json_schema())
    try:
        reply = await s.send(book(m))
        return Review.model_validate(reply.data or {})
    finally:
        await s.close()
