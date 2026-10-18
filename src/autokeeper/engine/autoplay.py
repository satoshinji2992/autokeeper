"""Headless autoplay: AI investigators play a whole module; the run doubles as a playtest.

Each round, every investigator who can still act proposes an action in parallel
(LangGraph Send fan-out); the proposals then go through the normal turn graph one
by one. Pushes are taken whenever allowed. The transcript and a summary land in
saves/autoplay/<slot>.jsonl and <slot>.summary.json.
"""

from __future__ import annotations

import json
import operator
import time
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send
from rich.console import Console

from .. import config, weather
from ..agents.cast import Cast
from ..config import Role
from ..harness import models as channels
from ..harness import replay as tapes
from ..harness import trace
from ..module.compiler import compile_module, skill_name
from ..module.schema import Module
from ..rules.bcdice import shared
from ..rules.coc import Investigator, Rules
from . import fog
from . import graph as turn_graph
from .game import Game
from .state import GameState, time_of_day


class PartyState(TypedDict, total=False):
    situation: str
    ids: list[str]
    proposals: Annotated[list[tuple[str, str]], operator.add]


def party_graph(cast: Cast, state: GameState):
    """Fan out: every AI investigator proposes an action at the same time."""

    def fan_out(ps: PartyState) -> list[Send]:
        return [Send("propose", {"situation": ps["situation"], "ids": [inv_id]}) for inv_id in ps["ids"]]

    async def propose(ps: PartyState) -> dict:
        inv_id = ps["ids"][0]
        text = await cast.investigator(state, inv_id, ps["situation"])
        return {"proposals": [(inv_id, text)]}

    g = StateGraph(PartyState)
    g.add_node("propose", propose)
    g.add_conditional_edges(START, fan_out, ["propose"])
    g.add_edge("propose", END)
    return g.compile()


TOLD = ("opening", "keeper", "npc", "player", "dice", "clue", "handout", "event", "sanity", "damage")  # log lines the players read
TELLER = {"opening": "开场", "clue": "线索", "handout": "手稿", "event": "事件"}


def situation(m: Module, st: GameState) -> str:
    """What an AI player sees, and no more than the players have been shown: where they are
    and what they saw on arriving, the places they know (engine/fog.py: been to, or heard of
    by the name they heard), the people they have met and where, the clues and handouts they
    found, the party's state, and the game so far as it was told. Not the place's list of
    things to search, its exits, or the people there they have not met: those are the
    keeper's. A companion who knew them would play another game than the human beside it,
    and autoplay would finish modules no table could.

    The first line is what the investigator's memory keeps of each turn (Cast.investigator)."""
    loc = m.location(st.location)
    known, there = fog.known(m, st), set(fog.been(m, st))
    places = "、".join(f"{name}（{'去过' if pid in there else '听说过'}）" for pid, name in known.items() if pid != loc.id)
    met = [n for n in m.npcs if n.id in st.npcs and st.npcs[n.id].met]
    people = "、".join(
        f"{n.name}（{n.role + '，' if n.role else ''}"
        f"{'就在这里' if n.location == loc.id else f'在{known[n.location]}' if n.location in known else '不知道在哪'}）"
        for n in met
    )  # fmt: skip
    clues = "\n".join(f"- 〔{m.clue(c).title}〕{_flat(m.clue(c).text, 240)}" for c in st.clues)
    handouts = "；".join(f"〔{m.handout(h).title}〕{_flat(m.handout(h).text, 160)}" for h in st.handouts)
    party = "\n".join(f"- {_status(inv)}" for inv in st.investigators)
    recent = [e for e in st.log if e.kind in TOLD][-10:]
    last = max((i for i, e in enumerate(recent) if e.kind == "keeper"), default=-1)  # where the keeper left the scene: in full
    lines = "\n".join(f"- {e.who or TELLER.get(e.kind, e.kind)}：{_flat(e.text) if i == last else _tail(e.text, 160)}" for i, e in enumerate(recent))
    stay = sum(1 for e in st.log[-12:] if e.kind == "player")
    out = [
        f"现在 {st.clock}（{time_of_day(st.minutes)}），你们在{loc.name}。",
        f"你们到这里时看到：{_flat(loc.read_aloud)}",
        f"知道的地方：{places or '只知道这里'}。",
    ]
    if people:  # talking to someone who is not here wastes a turn
        out.append(f"见过的人：{people}。不在这里的人，要先过去才说得上话。")
    out.append(f"已有线索：\n{clues}" if clues else "已有线索：无")
    if handouts:
        out.append(f"手上的文件：{handouts}")
    out.append(f"队伍：\n{party}")
    if st.memory:
        out.append(f"更早的经过：{st.memory}")
    out.append(f"最近发生的事：\n{lines or '（刚开场）'}\n（你们在这一带已经行动了约 {stay} 次。）你下一步做什么？")
    return "\n".join(out)


def _status(inv: Investigator) -> str:
    out = f"{inv.name}，{inv.occupation}，HP {inv.hp}/{inv.hp_max}，理智 {inv.san}，幸运 {inv.luck}"
    if inv.conditions:
        out += "，" + "、".join(inv.conditions)
    if inv.inventory:
        out += "，带着" + "、".join(inv.inventory)
    return out


def _flat(text: str, limit: int = 0) -> str:
    """Module or log text on one line (YAML wraps it), cut after `limit` characters."""
    out = "".join(line.strip() for line in text.strip().splitlines())
    return out if not limit or len(out) <= limit else out[: limit - 1] + "…"


def _tail(text: str, limit: int) -> str:
    """The end of a long line of play, where it left things."""
    out = _flat(text)
    return out if len(out) <= limit else "…" + out[-limit:]


def fingerprint(state: GameState) -> dict[str, Any]:
    """What a game came to: compared between a recording and its replay."""
    return {
        "ending": state.ending,
        "turn": state.turn,
        "minutes": state.minutes,
        "location": state.location,
        "visited": state.visited,
        "clues": state.clues,
        "flags": state.flags,
        "fired": state.fired,
        "investigators": [[i.id, i.hp, i.san] for i in state.investigators],
    }


async def run(
    module_dir: Path,
    *,
    party: int = 1,
    max_turns: int = 40,
    slot: str | None = None,
    console: Console | None = None,
    record: Path | None = None,
    replay: Path | None = None,
) -> dict[str, Any]:
    """`record` writes a cassette of the game (every reply and die); `replay` plays one
    back with no model at all (harness/replay.py)."""
    console = console or Console()
    m = Module.load(module_dir)
    if not (config.SKILLS / skill_name(m) / "SKILL.md").exists():
        compile_module(m, config.SKILLS)
    investigators = [p.model_copy(deep=True) for p in m.pregens[: max(1, party)]]
    slot = slot or f"auto-{m.id}-{time.strftime('%m%d-%H%M%S')}"
    trace.label_run(slot)
    state = Game.new(m, investigators, slot=slot, human=[])
    recorder = None
    if replay is not None:
        tape = tapes.cassette(replay)
        cfg = config.Config(roles={r: Role("replay", str(replay), None) for r in config.DEFAULT_ROLES}, preset="replay")
        cfg.improv = tape.meta.get("improv", "严格")  # tapes from before improvisation were strict
        dice: Any = tapes.ReplayDice(tape)
    else:
        cfg, dice = config.load(), shared()
        if record is not None:
            recorder = tapes.Recorder(record, meta={"improv": cfg.improv})
            dice = tapes.RecordingDice(dice, recorder)
    cast = Cast(cfg, m)
    out_dir = config.SAVES / "autoplay"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = (out_dir / f"{slot}.jsonl").open("w", encoding="utf-8")
    started = time.monotonic()

    async def emit(ev: dict) -> None:
        log.write(json.dumps({"turn": state.turn, "clock": state.clock, **ev}, ensure_ascii=False, default=str) + "\n")
        t = ev.get("type")
        if t == "keeper_end":
            console.print(f"[bold]守秘人[/] {ev['text']}\n")
        elif t == "npc":
            console.print(f"[bold magenta]{ev['name']}[/] {ev['text']}\n")
        elif t == "performance":
            console.print(f"[bold magenta]{ev['name']}（登台）[/] {ev['text']}\n")
        elif t == "dice":
            console.print(f"[yellow]◈ {ev['who']} {ev['text']}[/]")
        elif t == "repeat":
            console.print(f"[yellow]◈ {ev['who']} 不重掷：{ev['text']}[/]")
        elif t == "outcome":
            console.print(f"[yellow]  ↳ {'；'.join(ev['lines'])}[/]")
        elif t == "clue":
            console.print(f"[green]〔{ev['title']}〕[/] {ev['text']}")
        elif t == "sanity":
            console.print(f"[red]※ {ev['who']} {ev['text']}[/]")
        elif t == "event":
            console.print(f"[bold]━━ {ev['title']} ━━[/] {ev.get('text', '')}")
        elif t == "sky":
            console.print(f"[dim]☁ 天气：{weather.KINDS.get(ev['sky'], '转晴')}[/]")
        elif t == "improv":
            console.print(f"[cyan]✦ 导演即兴（{ev['kind']}）{('：' + ev['summary']) if ev['summary'] else ''}[/] [dim]{ev['reason']}[/]")
        elif t == "damage":
            console.print(f"[red]✚ {ev['who']} {ev['text']}[/]")
        elif t == "ending":
            console.print(f"[bold reverse] 结局：{ev['title']} [/]\n{ev['text']}")

    def fell_back(note: str) -> None:
        console.print(f"[yellow]⚠ {note}[/]")

    channels.listeners.append(fell_back)
    game = Game(m, state, cast, Rules(dice=dice), emit=emit)
    app = turn_graph.build(game, InMemorySaver())
    party_app = party_graph(cast, state)
    thread = {"configurable": {"thread_id": slot}}

    try:
        await game.start()
        while not state.ending and state.turn < max_turns:
            alive = [inv.id for inv in state.investigators if inv.can_act]
            if not alive:
                break
            result = await party_app.ainvoke({"situation": situation(m, state), "ids": alive, "proposals": []})
            for inv_id, text in result["proposals"]:
                if state.ending or state.turn >= max_turns:
                    break
                state.active = next(i for i, inv in enumerate(state.investigators) if inv.id == inv_id)
                console.print(f"[dim]▶ {state.investigator.name}：{text}[/]", soft_wrap=True)
                await emit({"type": "player", "who": state.investigator.name, "text": text})
                inp: Any = {"input": text}
                while True:
                    offer = None
                    async for mode, chunk in app.astream(inp, thread, stream_mode=["custom", "updates"]):
                        if mode == "custom":
                            await emit(chunk)
                        elif isinstance(chunk, dict) and "__interrupt__" in chunk:
                            offer = chunk["__interrupt__"][0].value
                    if offer is None:
                        break
                    inp = Command(resume="push" if offer.get("can_push") else "luck" if offer.get("luck") is not None else "drop")
                game.emit = emit
    finally:
        channels.listeners.remove(fell_back)
        await game.close()
        await cast.close()
        log.close()

    summary = {
        "module": m.id,
        "slot": slot,
        "party": [inv.name for inv in state.investigators],
        "ending": state.ending,
        "turns": state.turn,
        "clock": state.clock,
        "clues": f"{len(state.clues)}/{len(m.clues)}",
        "core_clues": f"{len([c for c in state.clues if m.clue(c).core])}/{len([c for c in m.clues if c.core])}",
        "visited": f"{len(state.visited)}/{len(m.locations)}",
        "investigators": [{"name": i.name, "hp": i.hp, "san": i.san, "conditions": i.conditions} for i in state.investigators],
        "usage": state.usage,
        "seconds": round(time.monotonic() - started, 1),
        "fingerprint": fingerprint(state),
    }
    if recorder is not None:
        recorder.close(summary["fingerprint"])
    (out_dir / f"{slot}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print(f"\n[bold]本局小结[/] {json.dumps({k: v for k, v in summary.items() if k != 'usage'}, ensure_ascii=False)}")
    return summary
