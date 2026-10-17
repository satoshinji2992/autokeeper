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

from .. import config
from ..agents.cast import Cast
from ..module.compiler import compile_module, skill_name
from ..module.schema import Module
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
    lines = "\n".join(f"- {e.who or e.kind}：{e.text[:180]}" for e in recent)
    stay = sum(1 for e in st.log[-12:] if e.kind == "player")
    return (
        f"现在 {st.clock}，你们在{loc.name}。这里能查：{'、'.join(loc.features) or '无'}；在场的人：{npcs}。\n"
        f"能去：{exits}。已有线索：{clues}。\n最近发生的事：\n{lines}\n"
        f"（你们在这一带已经行动了约 {stay} 次。）你下一步做什么？"
    )


async def run(
    module_dir: Path,
    *,
    party: int = 1,
    max_turns: int = 40,
    slot: str | None = None,
    console: Console | None = None,
) -> dict[str, Any]:
    console = console or Console()
    m = Module.load(module_dir)
    if not (config.SKILLS / skill_name(m) / "SKILL.md").exists():
        compile_module(m, config.SKILLS)
    investigators = [p.model_copy(deep=True) for p in m.pregens[: max(1, party)]]
    slot = slot or f"auto-{m.id}-{time.strftime('%m%d-%H%M%S')}"
    state = Game.new(m, investigators, slot=slot, human=[])
    cast = Cast(config.load(), m)
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
        elif t == "ending":
            console.print(f"[bold reverse] 结局：{ev['title']} [/]\n{ev['text']}")

    game = Game(m, state, cast, Rules(), emit=emit)
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
    }
    (out_dir / f"{slot}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print(f"\n[bold]本局小结[/] {json.dumps({k: v for k, v in summary.items() if k != 'usage'}, ensure_ascii=False)}")
    return summary
