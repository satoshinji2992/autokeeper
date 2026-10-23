"""LangGraph orchestration of a turn.

    START ─┬─ command ──────────────────────────────────────────────────────┐
           └─ parse → rule ─┬─ ask_roll ─┬─ resolve ─┬─ offer ⟲ ─┐             │
                            └────────────┘           └───────────┴─ talk → narrate → remember → END

* parse / rule / talk / narrate call agents; resolve is pure engine code.
* command answers the dice-bot commands that stand outside the story (.r, .st, .coc, ...)
  at once. A check the player asks for (.ra 恐吓) is an action like any other: it goes
  through parse, so the keeper rules it in context and the dice are rolled once.
* ask_roll lets a player throw their own dice: when the turn came in with by_hand and the
  ruling calls for a check, the graph pauses with interrupt() showing what is asked and
  why, and resolve rolls once the UI resumes it. AI investigators, autoplay and replays
  never set by_hand, so their dice fall at once, in the same order as ever.
* offer pauses the graph with interrupt() and waits for the player: a push (孤注一掷 / 花幸运 /
  放弃, its resume value passed to Game.push as it came), or a choice a fight waits on (how to
  answer a blow, a roll of their own, a new declaration: Game.respond). One interrupt per run of
  the node, as LangGraph matches resume values by their place; it loops while another is pending.
  In a fight, "/encounter" (the LAN table's settling, the screen's 继续) goes on with the round.
* Every node streams UI events through get_stream_writer(); the TUI consumes
  stream_mode="custom".
* The checkpointer stores a snapshot of GameState after every turn, so a save slot
  is just a LangGraph thread.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import TypeAdapter

from ..agents.cast import Action, Ruling
from ..rules.commands import instant
from .game import Game, Turn
from .observability import record_event
from .offers import public_push

TURN = TypeAdapter(Turn)


class TurnState(TypedDict, total=False):
    input: str
    by_hand: bool  # a player at the screen throws this turn's dice; pass it with every input
    action: dict | None
    ruling: dict | None
    pending: bool
    snapshot: dict


def _bind(game: Game) -> None:
    writer = get_stream_writer()

    async def emit(event: dict[str, Any]) -> None:
        record_event(game, event)
        writer(event)

    game.emit = emit


def _interrupted(chunk: Any) -> bool:
    if isinstance(chunk, Mapping):
        return "__interrupt__" in chunk
    return isinstance(chunk, tuple) and any(_interrupted(part) for part in chunk)


async def stream(graph: Any, game: Game, input: Any, config: Any = None, **kwargs: Any) -> AsyncIterator[Any]:
    """Forward graph events unchanged, continuing only a fully finished request segment.

    Each segment is a separate invocation with the same config and actor, so the graph's
    ordinary recursion bound and interruption protocol still apply. finish_request owns
    completion/party receipts and replaces the journal atomically before setting ready.
    """
    current = input
    original = dict(input) if isinstance(input, Mapping) else {}
    while True:
        interrupted = False
        async for chunk in graph.astream(current, config, **kwargs):
            interrupted = interrupted or _interrupted(chunk)
            yield chunk
        journal = game.state.pending_turn
        if (interrupted or not journal.get("request", {}).get("ready") or game.state.ending
                or game.battling() or game.pending_choice() is not None or game.state.pending_push):
            return
        # custom-only streams need not expose LangGraph's interrupt update. A saved next
        # node is unfinished work, even if a caller accidentally retained a ready marker.
        if getattr(graph, "checkpointer", None) is not None:
            snapshot = await graph.aget_state(config)
            if snapshot.next:
                return
        current = {**original, "input": "/retry", "by_hand": journal.get("by_hand", original.get("by_hand", False))}


def build(game: Game, checkpointer=None):
    holder: dict[str, Turn] = {}

    def keep(turn: Turn | None = None) -> None:
        if turn is not None:
            game.state.pending_turn["turn"] = TURN.dump_python(turn, mode="json")
        if game.checkpoint:
            game.checkpoint()

    game.turn_checkpoint = keep

    def held() -> Turn:
        if "turn" not in holder:
            holder["turn"] = TURN.validate_python(game.state.pending_turn["turn"])
        return holder["turn"]

    async def route(state: TurnState) -> dict:
        holder.clear()
        journal = game.state.pending_turn
        if journal:
            if state["input"] not in (journal["input"], "/retry", "继续这一回合"):
                raise ValueError("上一回合尚未讲完，请输入 /retry 继续，避免重复结算")
            if journal.get("request", {}).pop("ready", False):
                keep()  # consuming a prepared next segment is durable before parse can run
        else:
            if state["input"] in ("/retry", "继续这一回合"):
                raise ValueError("目前没有未完成的回合")
            if not instant(state["input"]) and (why := game._focus_blocked()):
                raise ValueError(why)  # a refused remote seat must not acquire an unfinished turn
            journal.update(input=state["input"], by_hand=state.get("by_hand", False), active=game.state.active)
            keep()
        game.state.active = journal.get("active", game.state.active)
        game.visuals.pause()
        return {"input": journal["input"], "by_hand": journal.get("by_hand", False),
                "action": None, "ruling": None, "pending": False}

    def which(state: TurnState) -> str:
        return "command" if instant(state["input"]) else "parse"

    async def command(state: TurnState) -> dict:
        _bind(game)
        await game.command(state["input"])
        return {}

    async def parse(state: TurnState) -> dict:
        _bind(game)
        journal = game.state.pending_turn
        if "action" in journal:
            return {"action": journal["action"]}
        turn, count, logged, deeds = game.state.turn, len(game.state.log), game.state.logged, list(game.state.deeds)
        try:
            action = await game.parse(state["input"])
        except BaseException:
            game.state.turn, game.state.logged, game.state.deeds = turn, logged, deeds
            del game.state.log[count:]
            raise
        journal["action"] = action.model_dump(mode="json")
        keep()
        await game.emit({"type": "action", **action.model_dump()})
        return {"action": action.model_dump()}

    async def rule(state: TurnState) -> dict:
        _bind(game)
        journal = game.state.pending_turn
        if "ruling" in journal:
            if journal["ruling"] is not None and "turn" not in journal and not journal.get("resolution"):
                action = Action.model_validate(journal["action"])
                ruling = await game.checked_skill(action, Ruling.model_validate(journal["ruling"]), at=game._heading(action))
                journal["ruling"] = ruling.model_dump(mode="json")
                keep()
            return {"action": journal["action"], "ruling": journal["ruling"]}
        action = Action.model_validate(state["action"])
        ruling = await game.rule(action)
        journal["action"] = action.model_dump(mode="json")
        journal["ruling"] = ruling.model_dump(mode="json") if ruling else None
        keep()
        if ruling:
            await game.emit({"type": "ruling", **ruling.model_dump()})
        return {"action": journal["action"], "ruling": ruling.model_dump() if ruling else None}

    async def prepare(state: TurnState) -> dict:
        _bind(game)
        await game.prepare(Action.model_validate(state["action"]))
        return {}

    def after_rule(state: TurnState) -> str:
        if (state.get("action") or {}).get("declared") is not None:
            return "resolve"  # a fight's dice are thrown at the investigator's slot (engine/encounter)
        r = state.get("ruling") or {}
        rolls = r.get("needs_roll") and r.get("skill") and not r.get("repeat")
        return "ask_roll" if rolls and state.get("by_hand") else "resolve"

    async def ask_roll(state: TurnState) -> dict:
        # Runs again from the top when resumed: nothing before interrupt() may change state.
        _bind(game)
        journal = game.state.pending_turn
        rolled = journal.get("resolution", {}).get("steps", {}).get("check", {}).get("done")
        if "turn" not in journal and not journal.get("roll_confirmed") and not rolled:
            interrupt({"kind": "roll", **game.roll_prompt(Action.model_validate(state["action"]), Ruling.model_validate(state["ruling"]))})
            journal["roll_confirmed"] = True
            keep()
        return {}

    async def resolve(state: TurnState) -> dict:
        _bind(game)
        if "turn" not in game.state.pending_turn:
            ruling = Ruling.model_validate(state["ruling"]) if state.get("ruling") else None
            holder["turn"] = await game.resolve(Action.model_validate(state["action"]), ruling)
            keep(holder["turn"])
        return {"pending": pending()}

    def pending() -> bool:
        journal = game.state.pending_turn
        return (bool(held().push) and not journal.get("push_merged")) or game.pending_choice() is not None

    def after_resolve(state: TurnState) -> str:
        return "offer" if state.get("pending") else "talk"

    async def offer(state: TurnState) -> dict:
        # Runs again from the top when resumed: nothing before interrupt() may change state.
        _bind(game)
        journal = game.state.pending_turn
        if held().push and not journal.get("push_merged"):
            if "push_choice" not in journal:
                offer = public_push(game.state.pending_push or {})
                journal["push_choice"] = interrupt({"kind": "push", **offer})  # as it came: a word, or {choice, how}
                keep()
            extra = await game.push(journal["push_choice"])
            turn = held()
            turn.absorb(extra)  # the events a failure set off too: who comes to stop them speaks this turn
            turn.push = None
            journal["push_merged"] = True
            keep(turn)
            return {"pending": pending()}
        choice = game.pending_choice()
        if choice is None:
            return {"pending": False}
        key = f"choice:{choice.id}"
        if key not in journal:
            journal[key] = interrupt(game.offer_payload(choice))
            keep()
        if not journal.get(f"{key}:merged"):
            extra = await game.respond(choice.id, journal[key])
            held().absorb(extra)
            journal[f"{key}:merged"] = True
            keep(held())
        return {"pending": pending()}

    def after_offer(state: TurnState) -> str:
        return "offer" if state.get("pending") else "talk"

    async def talk(state: TurnState) -> dict:
        _bind(game)
        if not game.state.pending_turn.get("talk_done"):
            await game.talk(held())
            game.state.pending_turn["talk_done"] = True
            keep(held())
        return {}

    async def narrate(state: TurnState) -> dict:
        _bind(game)
        if not game.state.pending_turn.get("narrated"):
            await game.narrate(held())
            game.state.pending_turn["narrated"] = True
            keep(held())
        return {}

    async def remember(state: TurnState) -> dict:
        _bind(game)
        await game.remember()
        # finish_request alone settles the old party token and clears or replaces its
        # journal. keep() must never write the preceding Turn into that new segment.
        await game.finish_request(held() if "turn" in game.state.pending_turn else None)
        keep()
        game.visuals.resume()
        await game.emit({"type": "sheet"})
        return {"snapshot": game.state.model_dump(mode="json")}

    g = StateGraph(TurnState)
    g.add_node("route", route)
    g.add_node("command", command)
    g.add_node("parse", parse)
    g.add_node("rule", rule)
    g.add_node("prepare", prepare)
    g.add_node("ask_roll", ask_roll)
    g.add_node("resolve", resolve)
    g.add_node("offer", offer)
    g.add_node("talk", talk)
    g.add_node("narrate", narrate)
    g.add_node("remember", remember)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", which, ["command", "parse"])
    g.add_edge("command", "remember")
    g.add_edge("parse", "prepare")
    g.add_edge("prepare", "rule")
    g.add_conditional_edges("rule", after_rule, ["ask_roll", "resolve"])
    g.add_edge("ask_roll", "resolve")
    g.add_conditional_edges("resolve", after_resolve, ["offer", "talk"])
    g.add_conditional_edges("offer", after_offer, ["offer", "talk"])
    g.add_edge("talk", "narrate")
    g.add_edge("narrate", "remember")
    g.add_edge("remember", END)
    return g.compile(checkpointer=checkpointer)
