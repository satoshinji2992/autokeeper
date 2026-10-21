"""keeper-mcp: the game engine as MCP tools, for playing inside Claude Code or Codex.

In this mode the coding agent itself is the keeper: it reads the module Skill package,
narrates, and calls these tools for everything that must not be made up — dice
(BCDice), clue bookkeeping, sanity, damage, time, events and endings. NPC lines come
from their own isolated sessions (Haiku by default), so the keeper cannot leak an
NPC's secrets by accident and NPCs remember what they were told.

Fights and chases are the engine's too (engine/fighting.py), as on the screens: the keeper
says who it is with and plays the investigator's order each round; the foes' orders, every
roll and every wound are settled here, and what comes back is what the players' screens
show (a foe's wounds in words, never its hit points).

Register it with Claude Code via the plugin's .mcp.json, or with Codex:

    [mcp_servers.autokeeper]
    command = "uv"
    args = ["run", "--directory", "/path/to/autokeeper", "autokeeper", "mcp"]
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import artkit, config, paths
from .agents.cast import Action, Cast, Ruling
from .engine import combat as battle
from .engine.game import TURN_RECORD, Game, Turn
from .engine.state import GameState
from .module.compiler import ensure_compiled, skill_name
from .module.schema import Module
from .rules import chase as chase_rules
from .rules import combat as combat_rules
from .rules import commands
from .rules.coc import DIFFICULTY_ZH, Rules
from .world.record import DIRECTOR
from .world.tools import Gateway

server = FastMCP("autokeeper", instructions="CoC 7 版跑团引擎：掷骰、线索、理智、战斗与追逐、时间与结局都在这里结算，叙事由你负责。")

# What a fight or a chase shows the screens that its report needs besides Turn.fight (_heard).
HEARD = ("fight_start", "fight_act", "fight_end", "chase_start", "chase_end", "reveal")
REACTIONS = {"dodge": "闪避", "fight": "反击", "cover": "扑向掩体"}


class _Session:
    module: Module | None = None
    game: Game | None = None
    lock: asyncio.Lock | None = None
    lock_game: Game | None = None


S = _Session()


def _dirs() -> dict[str, Path]:
    return {p.name: p for p in paths.module_folders()}


def _need() -> Game:
    if S.game is None:
        raise ValueError("还没开局：先调用 new_game 或 load_game")
    return S.game


def _save() -> None:
    g = _need()
    paths.games().mkdir(parents=True, exist_ok=True)
    target = paths.save(g.state.slot)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(g.state.model_dump_json(indent=1), encoding="utf-8")
    temporary.replace(target)


def _keep_turn(turn: Turn) -> None:
    g = _need()
    if g.state.pending_mcp:
        g.state.pending_mcp["turn"] = TURN_RECORD.dump_python(turn, mode="json")
        _save()


async def _resolved(tool: str, arguments: dict, action: Action, ruling: Ruling, *, talk: bool = False,
                    hold: bool = False, heard: list[dict] | None = None) -> Turn:
    """One MCP operation owns its saved action and ruling until every stage has finished.

    What a fight or a chase shows the screens as it is settled goes into the request's journal
    with the stage that showed it, so a retried call reports it too (`heard` gets it). `hold`:
    the order this action declares waits for the round's own declaration (Game.holding, as at a
    LAN table), so a fight or a chase begins and no round of it is settled yet."""
    g = _need()
    if S.lock_game is not g:
        S.lock, S.lock_game = asyncio.Lock(), g
    async with S.lock:
        st = g.state
        request = {"tool": tool, "arguments": arguments}
        journal = st.pending_mcp
        if journal and journal["request"] != request:
            raise ValueError("上一项操作尚未完成，请先用原参数重试原工具。")
        if not journal:
            if st.pending_turn:
                raise ValueError("存档中有尚未完成的游戏回合，请先在原游玩界面继续。")
            # MCP actions are turns too. This keeps world budgets and event identities
            # separate without repeating the increment when the client retries.
            st.turn += 1
            journal = {"request": request, "active": st.active, "action": action.model_dump(mode="json"),
                       "ruling": ruling.model_dump(mode="json")}
            st.pending_mcp = journal
            _save()
        st.active = journal["active"]
        if "turn" in journal:
            turn = TURN_RECORD.validate_python(journal["turn"])
        else:
            emit = g.emit

            async def hear(event: dict) -> None:
                await emit(event)
                if event.get("type") in HEARD:  # kept with its stage's receipt: shown once, reported every time
                    journal.setdefault("heard", []).append(_heard(event))

            g.emit, g.holding = hear, hold
            try:
                # Use the original ruling even when a partially settled clue changed which
                # gates would be offered by a new invocation of the same tool.
                turn = await g.resolve(Action.model_validate(journal["action"]), Ruling.model_validate(journal["ruling"]))
            finally:
                g.emit, g.holding = emit, False
            _keep_turn(turn)
        if talk:
            await g.talk(turn)
            _keep_turn(turn)
        if heard is not None:
            heard += journal.get("heard", [])
        st.pending_mcp = {}
        try:
            _save()
        except BaseException:
            st.pending_mcp = journal
            raise
        return turn


def _again(tool: str, arguments: dict) -> tuple[Action, Ruling] | None:
    """The saved action and ruling of an interrupted call that this one repeats: it goes on as
    saved, and nothing is read, asked or checked again. None for a new call."""
    journal = _need().state.pending_mcp
    if not journal:
        return None
    if journal["request"] != {"tool": tool, "arguments": arguments}:
        raise ValueError("上一项操作尚未完成，请先用原参数重试原工具。")
    return Action.model_validate(journal["action"]), Ruling.model_validate(journal["ruling"])


def _calm() -> Game:
    """The game, outside a fight and a chase: in one, whatever the investigator does is that round's
    order, and every roll and wound of it is the engine's (battle_round)."""
    g = _need()
    if g.battling():
        raise ValueError("正在战斗或追逐中：这一轮的行动、检定与伤害都由 battle_round 结算")
    return g


def _art(name: str | None) -> str:
    m = S.module
    return m.art.get(name or "", "") if m else ""


async def _replace_game(module: Module, state: GameState) -> None:
    if S.game is not None:
        await S.game.close()
        await S.game.cast.close()

    S.module = module
    cfg = config.load()
    cfg.imagegen = cfg.showcase = False  # these tools hand back ASCII art: a big picture would never be seen
    S.game = Game(module, state, Cast(cfg, module), Rules())
    S.game.checkpoint = _save
    S.game.turn_checkpoint = _keep_turn
    S.game.visuals.fill_gaps = False  # pictures for what the module lacks: nobody here would see them
    S.game.visuals.schedule()


def _turn_report(turn: Turn) -> dict:
    g = _need()
    m = g.m
    return {
        "checks": [c.describe() for c in turn.checks],
        "clues": [{"id": c, "title": m.clue(c).title, "text": m.clue(c).text.strip()} for c in turn.clues],
        "sanity": [f"{who}：{s.describe()}" + (f"；疯狂发作（日文表，请用中文演出）：{s.bout}" if s.bout else "") for who, s in turn.sanity],
        "events": [{"title": e.title, "read_aloud": e.read_aloud.strip(), "keeper": e.text.strip()} for e in turn.events],
        "arrived": turn.arrived,
        "route": [{"name": m.location(pid).name, "read_aloud": m.location(pid).read_aloud} for pid in turn.route[1:-1]],
        "ending": turn.ending,
        "notes": turn.notes,
        "time": g.state.clock,
    }


# -- a fight and a chase (engine/fighting.py) ----------------------------------------------------
def _heard(event: dict) -> dict:
    """An event of a fight or a chase as the host needs it to tell the round: a blow as its card on
    the screen shows it (both rolls and what it did), a creature first seen, a start and an end."""
    kind = event["type"]
    if kind == "fight_act":
        names = {event["attacker"]: event["attacker_name"], event["defender"]: event["defender_name"]}
        roll, answer = event.get("roll"), event.get("answer")
        if event.get("jammed"):
            result = "卡壳"
        elif event.get("empty"):
            result = "没有子弹"
        elif event.get("winner") == "none":
            result = "落空"
        elif event.get("effect"):
            result = f"{event['effect']}得手"
        else:
            result = "被反击" if event.get("hurt") == event["attacker"] else "命中"
        return {"type": kind, "attacker": event["attacker_name"], "attack": event["attack"], "defender": event["defender_name"],
                "roll": roll["text"] if roll else "", "reaction": REACTIONS.get(event.get("reaction", ""), ""),
                "answer": answer["text"] if answer else "", "result": result, "hurt": names.get(event.get("hurt", ""), ""),
                "damage": event.get("damage_text", "") if event.get("hurt") else "", "notes": [n for n in event.get("notes", []) if n]}
    if kind == "reveal":
        return {"type": kind, "name": event["name"], "look": event.get("look", "")}
    if kind in ("fight_start", "chase_start"):
        return {"type": kind, "line": event.get("line", "")}
    return {"type": kind, "end": event["end"], "text": event["text"]}


def _battle_now() -> dict | None:
    """The fight or the chase going on, as the players' screens show it (a foe's wounds in words,
    never its hit points), with what the investigator may do this round in the words battle_round
    reads. None outside one."""
    st = _need().state
    me = st.investigator.id
    if st.fight is not None:
        fight = st.fight
        mine = fight.get(me)
        standing = {f.id: f.name for f in fight.fighters if f.standing}
        offer = battle.choices(mine, fight) if mine is not None and mine.standing else []
        return {"kind": "fight", **battle.view(fight),
                "initiative": [standing[i] for i in combat_rules.order([f for f in fight.fighters if f.standing])],
                "choices": [{"order": words, "note": note} for words, note in offer]}
    if st.chase is not None:
        ch = st.chase
        mine = ch.get(me)
        offer = battle.chase_choices(mine, ch) if mine is not None and mine.standing and mine.id not in ch.hidden else []
        hunters = ch.running("foe" if ch.quarry == "party" else "party")
        return {"kind": "chase", **battle.chase_view(ch), "gap": chase_rules.gap(ch.running(ch.quarry), hunters),
                "choices": [{"order": words, "note": note} for words, note in offer]}
    return None


def _battle_report(turn: Turn, heard: list[dict]) -> dict:
    """A round of a fight or a chase, or its start, as the host tells it: the account as settled
    (every blow with its rolls and wounds, every stretch run), the creatures first seen, the fight
    or chase that ended, and the one going on now with the investigator's choices. One that began
    brings the faces it is with, as the stage shows them."""
    starts = [h for h in heard if h["type"] in ("fight_start", "chase_start")]
    over = next(({"kind": h["type"].split("_")[0], "result": h["end"], "text": h["text"]}
                 for h in heard if h["type"] in ("fight_end", "chase_end")), None)
    now = _battle_now()
    report = {**_turn_report(turn), "battle": {
        "lines": [*(h["line"] for h in starts if h["line"] and h["line"] not in turn.fight), *turn.fight],
        "exchanges": [{k: v for k, v in h.items() if k != "type"} for h in heard if h["type"] == "fight_act"],
        "seen": [{"name": h["name"], "look": h["look"]} for h in heard if h["type"] == "reveal"],
        "over": over,
        "now": now,
    }}
    if starts and now is not None:
        faces = [{"name": f["name"], "art": art} for f in now.get("fighters", now.get("runners", []))
                 if f["side"] == "foe" and (art := _art(f["portrait"]))]
        if faces:
            report["battle"]["portraits"] = faces  # ASCII art, to be shown as it is, in a code block
    return report


def _opponents(ids: list[str], most: int) -> list[str]:
    """Who a fight or a chase may be with: the NPCs here, or the module's creatures; and the
    investigator, who must be able to act."""
    g = _need()
    if not (inv := g.state.investigator).can_act:
        raise ValueError(f"{inv.name}现在无法行动：{'、'.join(inv.conditions)}")
    here, beasts = set(g._present()), {c.id for c in g.m.creatures}
    ids = list(dict.fromkeys(i.strip() for i in ids if i.strip()))
    wrong = [i for i in ids if i not in here and i not in beasts]
    if not ids or wrong or len(ids) > most:
        able = [f"{n.name}({n.id})" for n in g.m.npcs if n.id in here] + [f"{c.name}({c.id})" for c in g.m.creatures]
        problem = f"不在场或不存在：{'、'.join(wrong)}" if wrong else f"最多 {most} 个对象" if ids else "没有填对象"
        raise ValueError(f"{problem}。可以参与的：{'、'.join(able) or '无'}")
    return ids


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
async def new_game(module: str, investigator: str, slot: str = "mcp") -> dict:
    """开局：选择模组和一名预设调查员。返回开场朗读、钩子、起始地点和插图（插图请原样放进代码块）。"""
    path = _dirs()[module]
    m = Module.load(path)
    ensure_compiled(m, path)
    inv = next(p for p in m.pregens if p.id == investigator).model_copy(deep=True)
    state = Game.new(m, [inv], slot=slot)
    await _replace_game(m, state)
    await S.game.plugins.run("start", Turn())
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
async def load_game(slot: str) -> dict:
    """读档。"""
    state = GameState.model_validate_json(paths.save(slot).read_text(encoding="utf-8"))
    m = Module.load(_dirs()[state.module])
    await _replace_game(m, state)
    return scene()


@server.tool()
def scene() -> dict:
    """当前场景：地点、时间、可调查的东西、在场 NPC、出口、已得线索、调查员状态；
    模组里的怪物（剧情写明它此刻在场才算在场）；进行中的战斗或追逐（battle），含调查员这一轮可选的行动。"""
    g = _need()
    st, m = g.state, g.m
    loc = m.location(st.location)
    inv = st.investigator
    return {
        "time": st.clock,
        "location": {"id": loc.id, "name": loc.name, "features": loc.features},
        "npcs": [{"id": n.id, "name": n.name, "role": n.role} for n in m.npcs_at(loc.id)],
        "creatures": [{"id": c.id, "name": c.name} for c in m.creatures],
        "exits": [{"id": e, "name": m.location(e).name} for e in loc.exits],
        "clues_found": [m.clue(c).title for c in st.clues],
        "items": [{"id": i.id, "name": i.name, "description": i.description, "art": _art(i.art)} for i in m.items if i.where == st.location],
        "flags": st.flags,
        "investigator": {"name": inv.name, "hp": f"{inv.hp}/{inv.hp_max}", "san": inv.san, "luck": inv.luck, "conditions": inv.conditions},
        "battle": _battle_now(),
        "ending": st.ending,
    }


@server.tool()
async def move(location: str) -> dict:
    """带调查员去另一个地点（按路程扣时间，触发到达事件）。返回地点朗读文字和插图。"""
    g = _calm()
    turn = await _resolved("move", {"location": location},
                           Action(kind="move", summary=f"前往 {location}", destination=location), _no_roll())
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

    _calm()
    ruling = Ruling(needs_roll=True, skill=skill, difficulty=difficulty if difficulty in DIFFICULTY_ZH else "常规",  # type: ignore[arg-type]
                    bonus=bonus, clues_on_success=clues_on_success or [], minutes=minutes)
    turn = await _resolved("skill_check", {"skill": skill, "difficulty": difficulty, "bonus": bonus,
                                         "clues_on_success": clues_on_success or [], "minutes": minutes},
                           Action(kind="investigate", summary=skill), ruling)
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

    _calm()
    turn = await _resolved("reveal_clue", {"clue_id": clue_id}, Action(kind="investigate", summary=clue_id),
                           Ruling(needs_roll=False, auto_clues=[clue_id], minutes=5))
    _save()
    return _turn_report(turn)


@server.tool()
async def npc_say(npc: str, line: str, approach: str = "", check_skill: str = "", difficulty: str = "常规") -> dict:
    """让在场 NPC 回应调查员的话。需要社交检定时填 check_skill（如 说服、心理学）；成功会解锁该 NPC 门槛后的线索。
    NPC 的台词由它自己的会话生成，请原样转述给玩家。"""
    from .agents.cast import Ruling

    g = _calm()
    m = g.m
    n = m.npc(npc)
    gated = [c for c in n.clues if c not in g.state.clues]
    ruling = Ruling(needs_roll=bool(check_skill), skill=check_skill, difficulty=difficulty if difficulty in DIFFICULTY_ZH else "常规",  # type: ignore[arg-type]
                    clues_on_success=[c for c in gated if c in n.gates], auto_clues=[c for c in gated if c not in n.gates], minutes=10)
    action = Action(kind="talk", summary=f"对 {n.name} 说话", npc=npc, speech=line, approach=approach)  # type: ignore[arg-type]

    turn = await _resolved("npc_say", {"npc": npc, "line": line, "approach": approach,
                                     "check_skill": check_skill, "difficulty": difficulty}, action, ruling, talk=True)
    _save()
    return {**_turn_report(turn), "npc_lines": [{"name": m.npc(i).name, "line": t} for i, t in turn.npc_lines], "portrait": _art(n.portrait)}


def _names(ids: list[str]) -> str:
    m = _need().m
    found = {c.id: c.name for c in m.creatures} | {n.id: n.name for n in m.npcs}
    return "、".join(found.get(i, i) for i in ids)


@server.tool()
async def start_fight(foes: list[str], surprise: str = "", reason: str = "") -> dict:
    """打起来时调用：开始一场按 7 版规则逐轮结算的战斗，这时还没有人出手。foes 填动手或挨打的在场 NPC 或怪物 id
    （见 scene 的 npcs 与 creatures）；surprise：party 调查员被偷袭，foe 对方毫无防备，留空为双方都有防备；reason 写一句起因。
    初次看见的怪物先结算理智。返回参战各方、出手先后、对手肖像（原样放进代码块）和调查员第一轮可选的行动（battle.now.choices），
    之后每一轮用 battle_round。"""
    arguments = {"foes": foes, "surprise": surprise, "reason": reason}
    saved = _again("start_fight", arguments)
    if saved is None:
        g = _calm()
        ids = _opponents(foes, 6)
        if surprise not in ("", "none", "party", "foe"):
            raise ValueError("surprise 只能是 party、foe 或留空")
        why = reason.strip() or f"与{_names(ids)}打了起来"
        saved = (Action(kind="attack", summary=why, npc=next((i for i in ids if i in g._present()), "")),
                 Ruling(needs_roll=False, combat=ids, surprise="" if surprise == "none" else surprise, reason=why))  # type: ignore[arg-type]
    heard: list[dict] = []
    # The fight begins and waits for the investigator's order (hold): every round, the first one
    # too, is battle_round's, so the keeper offers the choices before anyone strikes.
    turn = await _resolved("start_fight", arguments, *saved, hold=True, heard=heard)
    _save()
    return _battle_report(turn, heard)


@server.tool()
async def start_chase(others: list[str], party: str = "逃", reason: str = "") -> dict:
    """开始一场追逐。others 填追赶调查员或被调查员追赶的在场 NPC 或怪物 id；party：调查员一方是“逃”还是“追”；reason 写一句起因。
    每人先过体质定速度；逃的一方快得多时当场甩开，追逐随即结束（battle.over）。返回路线、各人位置、相隔段数（gap）
    和调查员这一轮可选的行动（battle.now.choices），之后每一轮用 battle_round。"""
    arguments = {"others": others, "party": party, "reason": reason}
    saved = _again("start_chase", arguments)
    if saved is None:
        g = _calm()
        ids = _opponents(others, 4)
        if party not in ("逃", "追"):
            raise ValueError("party 只能是“逃”或“追”")
        why = reason.strip() or (f"被{_names(ids)}追赶" if party == "逃" else f"追赶{_names(ids)}")
        saved = (Action(kind="move", summary=why, npc=next((i for i in ids if i in g._present()), "")),
                 Ruling(needs_roll=False, chase=ids, chase_party=party, reason=why))  # type: ignore[arg-type]
    heard: list[dict] = []
    turn = await _resolved("start_chase", arguments, *saved, hold=True, heard=heard)
    _save()
    return _battle_report(turn, heard)


@server.tool()
async def battle_round(order: str, skill: str = "", difficulty: str = "常规", harm: str = "", target: str = "") -> dict:
    """战斗或追逐中调查员这一轮的行动。order 照 battle.now.choices 的原文写（如“攻击 老守钟人（徒手）｜被攻击时闪避”），
    也可以写玩家自己的话。攻击、射击、战技、急救、装填、排除故障、防守、逃跑，追逐中的奔跑、躲藏、设置障碍与回身迎战，都由引擎掷骰。
    玩家做的是别的事（把油灯砸过去、关门）时由你裁定：skill 填检定技能，difficulty 填难度，harm 填成功时造成的伤害骰式，
    target 填受伤对象的 id；不需要检定就留空。引擎把这句话读成了什么见 battle.order。
    对手和怪物的行动、全部骰子与伤害都在这里结算，返回这一轮的经过（battle.lines 与 exchanges）、是否结束（battle.over）、
    之后的局面和下一轮可选的行动（battle.now）。"""
    g = _need()
    arguments = {"order": order, "skill": skill, "difficulty": difficulty, "harm": harm, "target": target}
    saved = _again("battle_round", arguments)
    if saved is None:
        st = g.state
        if not g.battling():
            raise ValueError("现在没有战斗或追逐：先用 start_fight 或 start_chase")
        if harm.strip() and not skill.strip():
            raise ValueError("harm 是检定成功时的伤害，需要同时填 skill")
        action = await g._battle_action(order)
        ruling = g._battle_ruling(action)
        if ruling is None:  # something else than fighting or running: the keeper rules it, as the arbiter does on the screens
            aim = target.strip() or (action.order or {}).get("target", "")
            if harm.strip() and st.fight is not None and st.fight.get(aim) is None:
                raise ValueError(f"target 不在这场战斗中：{aim or '未填'}。参战的：" + "、".join(f"{f.name}({f.id})" for f in st.fight.fighters))
            ruling = Ruling(needs_roll=bool(skill.strip()), skill=skill.strip(), harm=harm.strip(), harm_to=aim, reason="战斗中的其他行动",
                            difficulty=difficulty if difficulty in DIFFICULTY_ZH else "常规")  # type: ignore[arg-type]
        saved = (action, ruling)
    heard: list[dict] = []
    turn = await _resolved("battle_round", arguments, *saved, heard=heard)
    _save()
    report = _battle_report(turn, heard)
    report["battle"]["order"] = saved[0].summary
    return report


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
    """结算战斗以外的伤害，amount 可以是数字或骰式（如 1D6+1）。"""
    g = _calm()
    d = g.rules.damage(g.state.investigator, int(amount) if amount.isdigit() else amount)
    g.state.add("dice", d.describe(), who=g.state.investigator.name)
    _save()
    return {"result": d.describe()}


@server.tool()
def dice(command: str) -> dict:
    """骰娘指令：.ra 侦查、.rab 侦查、.sc 1/1d6、.st 侦查60、.r 1d6 等。战斗与追逐中不可用。"""
    g = _calm()
    r = commands.run(command, g.state.investigator, g.rules)
    _save()
    return {"kind": r.kind, "text": r.text}


@server.tool()
async def advance_time(minutes: int) -> dict:
    """推进游戏时间，返回期间触发的事件（及可能的结局）。"""
    g = _calm()
    await g.plugins.run("start", Turn())
    g.state.turn += 1
    g.state.minutes += max(0, minutes)
    turn = Turn()
    await g.plugins.run("turn", turn, action={"kind": "wait", "summary": "推进游戏时间"})
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
    return {"title": h.title, "kind": h.kind, "text": h.text,
            "images": [{"key": key, "path": g.m.pictures[key]} for key in h.images if key in g.m.pictures]}


@server.tool()
def list_plugins() -> list[dict]:
    """列出本局的规则插件及可调用动作；源码与私有计数不展示给玩家。"""
    g = _need()
    return [{"id": p.id, "name": p.name, "version": p.version, "description": p.description,
             "actions": [a.model_dump() for a in p.actions],
             "enabled": p.id not in g.state.plugin_errors} for p in g.m.extensions.values()]


@server.tool()
async def plugin_action(plugin: str, action: str, arguments: dict | None = None) -> dict:
    """按实际发生的行动调用插件已声明的动作；不要编造插件尚未给出的结果。"""
    g = _need()
    if plugin not in g.m.extensions or action not in {a.name for a in g.m.extensions[plugin].actions}:
        raise ValueError("插件或动作不存在，请先 list_plugins")
    g.state.turn += 1
    turn = Turn()
    await g.plugins.run("action", turn, plugin=plugin, action=action, arguments=arguments or {})
    await g._check_ending(turn)
    _save()
    return {**_turn_report(turn), "error": g.state.plugin_errors.get(plugin, "")}


@server.tool()
async def world_query(query: str = "", limit: int = 30) -> dict:
    """查询本局世界的真实位置、事实、事件、人物目标和计划，返回提交所需读取版本。
    仅供本机守秘人；隐藏信息不得直接讲给调查员。不会推进时钟或调用模型。"""
    g = _need()
    result = Gateway(g.world, DIRECTOR).inspect(query, limit)
    g._debug("world-tool", {"action": "query", "query": query, "result": result})
    return result


@server.tool()
async def world_schema() -> dict:
    """世界操作的字段定义；身份由本机宿主绑定，不能通过参数切换为其他角色。"""
    return Gateway(_need().world, DIRECTOR).schema()


@server.tool()
async def world_preview(ops: list[dict], reads: dict[str, int] | None = None) -> dict:
    """在副本中校验整组世界操作，返回预计的位置、事件与计划状态；不改动本局，不掷骰。"""
    result = Gateway(_need().world, DIRECTOR).preview(ops, reads)
    _need()._debug("world-tool", {"action": "preview", "result": result})
    return result


@server.tool()
async def world_commit(request_id: str, ops: list[dict], reads: dict[str, int]) -> dict:
    """以查询/预演返回的读取版本原子提交操作。稳定 request_id 可安全重试；旧版本或任一无效操作使整组拒绝。
    不改时钟、不重掷；新增场景、插件和图片应通过游戏引擎准备，不能在此跳过验证。"""
    g = _need()
    result = Gateway(g.world, DIRECTOR).commit(ops, reads=reads, request_id=request_id)
    g._debug("world-tool", {"action": "commit", "request_id": request_id, "result": result})
    if result["ok"] and not result.get("repeat"):
        _save()
    return result


@server.tool()
async def lookup(query: str, hops: int = 1) -> dict:
    """检索模组知识：建过 LightRAG 知识库（autokeeper index）时走 GraphRAG mix 检索原文与图谱，
    否则在结构化模组图谱里按名字命中节点并带出相邻节点。只给守秘人参考，不要把未发现的内容直接告诉玩家。"""
    from . import knowledge
    from .module import graph as module_graph

    g = _need()
    hits = module_graph.search(g.m, query, hops=hops)
    if knowledge.available() and knowledge.exists(g.m.id):
        return {"graph": hits, "lightrag": await g.cast.lore(g.state, query, max_tokens=3000)}
    return {"graph": hits}


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
