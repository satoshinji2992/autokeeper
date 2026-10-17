"""keeper-mcp: the game engine as MCP tools, for playing inside Claude Code or Codex.

In this mode the coding agent itself is the keeper: it reads the module Skill package,
narrates, and calls these tools for everything that must not be made up — dice
(BCDice), clue bookkeeping, sanity, damage, time, events and endings. NPC lines come
from their own isolated sessions (Haiku by default), so the keeper cannot leak an
NPC's secrets by accident and NPCs remember what they were told.

Register it with Claude Code via the plugin's .mcp.json, or with Codex:

    [mcp_servers.autokeeper]
    command = "uv"
    args = ["run", "--directory", "/path/to/autokeeper", "autokeeper", "mcp"]
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import artkit, config
from .agents.cast import Action, Cast
from .engine.game import Game, Turn
from .engine.state import GameState
from .module.compiler import compile_module, skill_name
from .module.schema import Module
from .rules import commands
from .rules.coc import DIFFICULTY_ZH, Rules

server = FastMCP("autokeeper", instructions="CoC 7 版跑团引擎：掷骰、线索、理智、时间与结局都在这里结算，叙事由你负责。")


class _Session:
    module: Module | None = None
    game: Game | None = None


S = _Session()


def _dirs() -> dict[str, Path]:
    out = {}
    for base in (config.MODULES, config.LOCAL_MODULES):
        if base.is_dir():
            out |= {p.name: p for p in sorted(base.iterdir()) if (p / "module.yaml").exists()}
    return out


def _need() -> Game:
    if S.game is None:
        raise ValueError("还没开局：先调用 new_game 或 load_game")
    return S.game


def _save() -> None:
    g = _need()
    config.SAVES.mkdir(parents=True, exist_ok=True)
    (config.SAVES / f"{g.state.slot}.json").write_text(g.state.model_dump_json(indent=1), encoding="utf-8")


def _art(name: str | None) -> str:
    m = S.module
    return m.art.get(name or "", "") if m else ""


def _turn_report(turn: Turn) -> dict:
    g = _need()
    m = g.m
    return {
        "checks": [c.describe() for c in turn.checks],
        "clues": [{"id": c, "title": m.clue(c).title, "text": m.clue(c).text.strip()} for c in turn.clues],
        "sanity": [f"{who}：{s.describe()}" + (f"；疯狂发作（日文表，请用中文演出）：{s.bout}" if s.bout else "") for who, s in turn.sanity],
        "events": [{"title": e.title, "read_aloud": e.read_aloud.strip(), "keeper": e.text.strip()} for e in turn.events],
        "arrived": turn.arrived,
        "ending": turn.ending,
        "notes": turn.notes,
        "time": g.state.clock,
    }


@server.tool()
def list_modules() -> list[dict]:
    """列出可以玩的模组。"""
    out = []
    for mid, path in _dirs().items():
        m = Module.load(path)
        out.append({"id": mid, "title": m.title, "era": m.era, "players": m.players, "duration": m.duration, "skill": skill_name(m)})
    return out


@server.tool()
def list_pregens(module: str) -> list[dict]:
    """列出模组的预设调查员。"""
    m = Module.load(_dirs()[module])
    return [{"id": p.id, "name": p.name, "occupation": p.occupation, "age": p.age, "skills": p.skills} for p in m.pregens]


@server.tool()
def new_game(module: str, investigator: str, slot: str = "mcp") -> dict:
    """开局：选择模组和一名预设调查员。返回开场朗读、钩子、起始地点和插图（插图请原样放进代码块）。"""
    path = _dirs()[module]
    m = Module.load(path)
    if not (config.SKILLS / skill_name(m) / "SKILL.md").exists():
        compile_module(m, config.SKILLS)
    inv = next(p for p in m.pregens if p.id == investigator).model_copy(deep=True)
    state = Game.new(m, [inv], slot=slot)
    S.module = m
    S.game = Game(m, state, Cast(config.load(), m), Rules())
    loc = m.location(state.location)
    _save()
    return {
        "skill": skill_name(m),
        "opening": m.opening.strip(),
        "hook": m.hook.strip(),
        "time": state.clock,
        "location": {"id": loc.id, "name": loc.name, "read_aloud": loc.read_aloud.strip()},
        "art": _art(loc.art),
        "investigator": inv.model_dump(include={"name", "occupation", "age", "skills", "characteristics", "luck"}),
    }


@server.tool()
def load_game(slot: str) -> dict:
    """读档。"""
    state = GameState.model_validate_json((config.SAVES / f"{slot}.json").read_text(encoding="utf-8"))
    m = Module.load(_dirs()[state.module])
    S.module = m
    S.game = Game(m, state, Cast(config.load(), m), Rules())
    return scene()


@server.tool()
def scene() -> dict:
    """当前场景：地点、时间、可调查的东西、在场 NPC、出口、已得线索、调查员状态。"""
    g = _need()
    st, m = g.state, g.m
    loc = m.location(st.location)
    inv = st.investigator
    return {
        "time": st.clock,
        "location": {"id": loc.id, "name": loc.name, "features": loc.features},
        "npcs": [{"id": n.id, "name": n.name, "role": n.role} for n in m.npcs_at(loc.id)],
        "exits": [{"id": e, "name": m.location(e).name} for e in loc.exits],
        "clues_found": [m.clue(c).title for c in st.clues],
        "flags": st.flags,
        "investigator": {"name": inv.name, "hp": f"{inv.hp}/{inv.hp_max}", "san": inv.san, "luck": inv.luck, "conditions": inv.conditions},
        "ending": st.ending,
    }


@server.tool()
async def move(location: str) -> dict:
    """带调查员去另一个地点（按路程扣时间，触发到达事件）。返回地点朗读文字和插图。"""
    g = _need()
    turn = await g.resolve(Action(kind="move", summary=f"前往 {location}", destination=location), _no_roll())
    loc = g.m.location(g.state.location)
    _save()
    return {**_turn_report(turn), "read_aloud": loc.read_aloud.strip(), "art": _art(loc.art)}


def _no_roll():
    from .agents.cast import Ruling

    return Ruling(needs_roll=False, minutes=0)


@server.tool()
async def skill_check(skill: str, difficulty: str = "常规", bonus: int = 0, clues_on_success: list[str] | None = None, minutes: int = 15) -> dict:
    """技能检定（BCDice 掷骰）。difficulty：常规/困难/极难；bonus：奖励骰为正、惩罚骰为负。
    clues_on_success 填成功后交出的线索 id（只能是当前地点的线索）。"""
    from .agents.cast import Ruling

    g = _need()
    ruling = Ruling(needs_roll=True, skill=skill, difficulty=difficulty if difficulty in DIFFICULTY_ZH else "常规",  # type: ignore[arg-type]
                    bonus=bonus, clues_on_success=clues_on_success or [], minutes=minutes)
    turn = await g.resolve(Action(kind="investigate", summary=skill), ruling)
    _save()
    report = _turn_report(turn)
    if turn.push:
        report["can_push_or_luck"] = turn.push
    return report


@server.tool()
async def push_or_luck(choice: str) -> dict:
    """对上一次失败的检定选择：push（孤注一掷）、luck（花幸运）或 drop（放弃）。"""
    g = _need()
    turn = await g.push(choice)
    _save()
    return _turn_report(turn)


@server.tool()
async def reveal_clue(clue_id: str) -> dict:
    """交出一条不需要检定的线索（必须在当前地点或在场 NPC 手里）。"""
    from .agents.cast import Ruling

    g = _need()
    turn = await g.resolve(Action(kind="investigate", summary=clue_id), Ruling(needs_roll=False, auto_clues=[clue_id], minutes=5))
    _save()
    return _turn_report(turn)


@server.tool()
async def npc_say(npc: str, line: str, approach: str = "", check_skill: str = "", difficulty: str = "常规") -> dict:
    """让在场 NPC 回应调查员的话。需要社交检定时填 check_skill（如 说服、心理学）；成功会解锁该 NPC 门槛后的线索。
    NPC 的台词由它自己的会话生成，请原样转述给玩家。"""
    from .agents.cast import Ruling

    g = _need()
    m = g.m
    n = m.npc(npc)
    gated = [c for c in n.clues if c not in g.state.clues]
    ruling = Ruling(needs_roll=bool(check_skill), skill=check_skill, difficulty=difficulty if difficulty in DIFFICULTY_ZH else "常规",  # type: ignore[arg-type]
                    clues_on_success=[c for c in gated if c in n.gates], auto_clues=[c for c in gated if c not in n.gates], minutes=10)
    action = Action(kind="talk", summary=f"对 {n.name} 说话", npc=npc, speech=line, approach=approach)  # type: ignore[arg-type]

    turn = await g.resolve(action, ruling)
    await g.talk(turn)
    _save()
    return {**_turn_report(turn), "npc_lines": [{"name": m.npc(i).name, "line": t} for i, t in turn.npc_lines], "portrait": _art(n.portrait)}


@server.tool()
def sanity_check(success_loss: str, failure_loss: str) -> dict:
    """理智检定，如 sanity_check("0", "1D6")。"""
    g = _need()
    s = g.rules.sanity(g.state.investigator, success_loss, failure_loss)
    g.state.add("sanity", s.describe(), who=g.state.investigator.name)
    _save()
    return {"result": s.describe(), "bout": s.bout}


@server.tool()
def apply_damage(amount: str) -> dict:
    """结算伤害，amount 可以是数字或骰式（如 1D6+1）。"""
    g = _need()
    d = g.rules.damage(g.state.investigator, int(amount) if amount.isdigit() else amount)
    g.state.add("dice", d.describe(), who=g.state.investigator.name)
    _save()
    return {"result": d.describe()}


@server.tool()
def dice(command: str) -> dict:
    """骰娘指令：.ra 侦查、.rab 侦查、.sc 1/1d6、.st 侦查60、.r 1d6 等。"""
    g = _need()
    r = commands.run(command, g.state.investigator, g.rules)
    _save()
    return {"kind": r.kind, "text": r.text}


@server.tool()
async def advance_time(minutes: int) -> dict:
    """推进游戏时间，返回期间触发的事件（及可能的结局）。"""
    g = _need()
    g.state.minutes += max(0, minutes)
    turn = Turn()
    await g._time(turn)
    await g._check_ending(turn)
    _save()
    return _turn_report(turn)


@server.tool()
async def set_flag(flag: str) -> dict:
    """标记剧情变化（只能用模组声明过的标记），可能触发事件或结局。"""
    g = _need()
    turn = Turn()
    await g._flags([flag], turn)
    await g._check_ending(turn)
    _save()
    return {**_turn_report(turn), "flags": g.state.flags, "declared": g.m.flags}


@server.tool()
def handout(handout_id: str) -> dict:
    """取手稿全文（已获得的才能看）。"""
    g = _need()
    if handout_id not in g.state.handouts:
        raise ValueError("调查员还没拿到这份手稿")
    h = g.m.handout(handout_id)
    return {"title": h.title, "kind": h.kind, "text": h.text}


@server.tool()
def lookup(query: str, hops: int = 1) -> list[dict]:
    """在模组知识图谱里检索：按名字命中地点、NPC、线索、结论，再带出相邻节点（谁知道、在哪、指向什么）。
    只给守秘人参考，不要把未发现的内容直接告诉玩家。"""
    from .module import graph as module_graph

    g = _need()
    return module_graph.search(g.m, query, hops=hops)


@server.tool()
def show_art(name: str) -> str:
    """取模组里的一张 ASCII 图（原样放进代码块展示）。"""
    if not S.module or name not in S.module.art:
        raise ValueError(f"没有这张图：{name}")
    return S.module.art[name]


@server.tool()
def check_art(text: str, portrait: bool = False) -> list[str]:
    """检查一张 ASCII 图能否在终端里正常显示（字符集与尺寸）。"""
    return [str(i) for i in artkit.check("art", text, portrait=portrait)]


if __name__ == "__main__":
    server.run()
