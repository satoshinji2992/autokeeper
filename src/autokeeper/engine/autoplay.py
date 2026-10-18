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
from ..rules.coc import Rules
from . import graph as turn_graph
from .game import Game
from .state import GameState


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


def situation(m: Module, st: GameState) -> str:
    """What an AI player sees: the scene, where they have been, what they hold, what just happened."""
    recent = [e for e in st.log if e.kind in ("keeper", "npc", "clue", "event", "dice", "player")][-8:]
    clues = "；".join(m.clue(c).title for c in st.clues) or "无"
    loc = m.location(st.location)
    exits = "、".join(f"{m.location(e).name}{'（去过）' if e in st.visited else ''}" for e in loc.exits)
    npcs = "、".join(n.name for n in m.npcs_at(loc.id)) or "无"
    # People met elsewhere, and where: talking to someone who is not here wastes a turn.
    away = [n for n in m.npcs if n.location and n.location != loc.id and st.npcs.get(n.id) and st.npcs[n.id].met]
    elsewhere = "、".join(f"{n.name}（在{m.location(n.location).name}）" for n in away)
    lines = "\n".join(f"- {e.who or e.kind}：{e.text[:180]}" for e in recent)
    stay = sum(1 for e in st.log[-12:] if e.kind == "player")
    return (
        f"现在 {st.clock}，你们在{loc.name}。这里能查：{'、'.join(loc.features) or '无'}；在场的人：{npcs}。\n"
        f"能去：{exits}。已有线索：{clues}。"
        + (f"见过但不在这里的人：{elsewhere}，要找他们得先过去。" if elsewhere else "")
        + f"\n最近发生的事：\n{lines}\n（你们在这一带已经行动了约 {stay} 次。）你下一步做什么？"
    )


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
        dice: Any = tapes.ReplayDice(tape)
    else:
        cfg, dice = config.load(), shared()
        if record is not None:
            recorder = tapes.Recorder(record)
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
        elif t == "clue":
            console.print(f"[green]〔{ev['title']}〕[/] {ev['text']}")
        elif t == "sanity":
            console.print(f"[red]※ {ev['who']} {ev['text']}[/]")
        elif t == "event":
            console.print(f"[bold]━━ {ev['title']} ━━[/] {ev.get('text', '')}")
        elif t == "sky":
            console.print(f"[dim]☁ 天气：{weather.KINDS.get(ev['sky'], '转晴')}[/]")
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
