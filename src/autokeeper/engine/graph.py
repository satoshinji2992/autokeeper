"""LangGraph orchestration of a turn.

    START ─┬─ command ───────────────────────────────────────┐
           └─ parse → rule → resolve ─┬─ offer_push ─┐        │
                                      └──────────────┴─ talk → narrate → remember → END

* parse / rule / talk / narrate call agents; resolve is pure engine code.
* offer_push pauses the graph with interrupt() and waits for the player to choose
  孤注一掷 / 花幸运 / 放弃; the UI resumes it with Command(resume=...).
* Every node streams UI events through get_stream_writer(); the TUI consumes
  stream_mode="custom".
* The checkpointer stores a snapshot of GameState after every turn, so a save slot
  is just a LangGraph thread.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..agents.cast import Action, Ruling
from ..rules.commands import is_command
from .game import Game, Turn


class TurnState(TypedDict, total=False):
    input: str
    action: dict | None
    ruling: dict | None
    pending: bool
    snapshot: dict


def _bind(game: Game) -> None:
    writer = get_stream_writer()

    async def emit(event: dict[str, Any]) -> None:
        writer(event)

    game.emit = emit


def build(game: Game, checkpointer=None):
    holder: dict[str, Turn] = {}

    async def route(state: TurnState) -> dict:
        return {"action": None, "ruling": None, "pending": False}

    def which(state: TurnState) -> str:
        return "command" if is_command(state["input"]) else "parse"

    async def command(state: TurnState) -> dict:
        _bind(game)
        await game.command(state["input"])
        return {}

    async def parse(state: TurnState) -> dict:
        _bind(game)
        action = await game.parse(state["input"])
        await game.emit({"type": "action", **action.model_dump()})
        return {"action": action.model_dump()}

    async def rule(state: TurnState) -> dict:
        _bind(game)
        ruling = await game.rule(Action.model_validate(state["action"]))
        if ruling:
            await game.emit({"type": "ruling", **ruling.model_dump()})
        return {"ruling": ruling.model_dump() if ruling else None}

    async def resolve(state: TurnState) -> dict:
        _bind(game)
        ruling = Ruling.model_validate(state["ruling"]) if state.get("ruling") else None
        holder["turn"] = await game.resolve(Action.model_validate(state["action"]), ruling)
        return {"pending": bool(holder["turn"].push)}

    def after_resolve(state: TurnState) -> str:
        return "offer_push" if state.get("pending") else "talk"

    async def offer_push(state: TurnState) -> dict:
        _bind(game)
        choice = interrupt({"kind": "push", **(game.state.pending_push or {})})
        extra = await game.push(str(choice))
        turn = holder["turn"]
        turn.checks += extra.checks
        turn.clues += extra.clues
        turn.sanity += extra.sanity
        turn.notes += extra.notes
        turn.ending = turn.ending or extra.ending
        turn.push = None
        return {"pending": False}

    async def talk(state: TurnState) -> dict:
        _bind(game)
        await game.talk(holder["turn"])
        return {}

    async def narrate(state: TurnState) -> dict:
        _bind(game)
        await game.narrate(holder["turn"])
        return {}

    async def remember(state: TurnState) -> dict:
        _bind(game)
        await game.remember()
        await game.emit({"type": "sheet"})
        return {"snapshot": game.state.model_dump(mode="json")}

    g = StateGraph(TurnState)
    g.add_node("route", route)
    g.add_node("command", command)
    g.add_node("parse", parse)
    g.add_node("rule", rule)
    g.add_node("resolve", resolve)
    g.add_node("offer_push", offer_push)
    g.add_node("talk", talk)
    g.add_node("narrate", narrate)
    g.add_node("remember", remember)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", which, ["command", "parse"])
    g.add_edge("command", "remember")
    g.add_edge("parse", "rule")
    g.add_edge("rule", "resolve")
    g.add_conditional_edges("resolve", after_resolve, ["offer_push", "talk"])
    g.add_edge("offer_push", "talk")
    g.add_edge("talk", "narrate")
    g.add_edge("narrate", "remember")
    g.add_edge("remember", END)
    return g.compile(checkpointer=checkpointer)
