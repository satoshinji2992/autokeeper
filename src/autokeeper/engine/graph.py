"""Execute one durable turn; LangGraph transports events, choices and final snapshots.

The pending-turn journal owns the stage, tool receipts and any frozen player choice.
Only execute/choice remain in the graph: it cannot retain a competing parse/resolve
cursor. Existing rule and world tools keep their own committed side effects.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..agents.cast import Action, Ruling
from ..rules.commands import instant
from ..world.store import digest
from .game import TURN_RECORD, Game, Turn
from .observability import record_event
from .offers import public_push

TURN = TURN_RECORD


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


class Execution:
    """One durable turn journal; the graph only transports events and player choices.

    A stage advances after its work is saved. Tool-level receipts still own partial
    side effects, so cancelling a later thought never rewinds a roll or an NPC reply.
    No graph channel or in-memory Turn is an alternative execution authority.
    """

    def __init__(self, game: Game):
        self.game = game
        game.turn_checkpoint = self.keep

    @property
    def journal(self) -> dict:
        return self.game.state.pending_turn

    def keep(self, turn: Turn | None = None) -> None:
        if turn is not None:
            self.journal["turn"] = TURN.dump_python(turn, mode="json")
        if self.game.checkpoint:
            self.game.checkpoint()

    def begin(self, state: TurnState) -> None:
        game, journal = self.game, self.journal
        text = state["input"]
        if journal:
            if text not in (journal["input"], "/retry", "继续这一回合"):
                raise ValueError("上一回合尚未讲完，请输入 /retry 继续，避免重复结算")
            if journal.get("request", {}).pop("ready", False):
                self.keep()
        else:
            if text in ("/retry", "继续这一回合"):
                raise ValueError("目前没有未完成的回合")
            if not instant(text) and (why := game._focus_blocked()):
                raise ValueError(why)
            journal.update(input=text, by_hand=state.get("by_hand", False), active=game.state.active)
        journal.setdefault("id", uuid4().hex)
        journal.setdefault("stage", "command" if instant(journal["input"]) else "parse")
        game.state.active = journal.get("active", game.state.active)
        self.keep()
        game.visuals.pause()

    def advance(self, stage: str, turn: Turn | None = None) -> None:
        self.journal.pop("awaiting", None)
        self.journal["stage"] = stage
        self.keep(turn)

    def turn(self) -> Turn:
        return TURN.validate_python(self.journal["turn"])

    def answer(self, key: str, payload: dict, *, request: str = "") -> tuple[bool, Any]:
        """Freeze one pending choice; only the choice transport obtains its response."""
        expected = {"key": key, "payload": payload, "request": request}
        waiting = self.journal.get("awaiting", {})
        if any(waiting.get(field) != value for field, value in expected.items()):
            # A cross-scene arrangement can change the action while its roll prompt
            # is on screen. An answer to that old question cannot confirm the new one.
            waiting = self.journal["awaiting"] = expected
        if "answer" not in waiting:
            self.keep()
            return False, None
        answer = waiting["answer"]
        del self.journal["awaiting"]
        return True, answer

    async def run(self, state: TurnState) -> dict:
        self.begin(state)
        game = self.game
        while True:
            journal, stage = self.journal, self.journal["stage"]
            if stage == "command":
                await game.command(journal["input"])
                self.advance("remember")
            elif stage == "parse":
                if "action" not in journal:
                    turn, count, logged, deeds = game.state.turn, len(game.state.log), game.state.logged, list(game.state.deeds)
                    try:
                        action = await game.parse(journal["input"])
                    except BaseException:
                        if rollback := getattr(game.state, "rollback_parse", None):
                            rollback(turn, count, logged)
                        else:
                            game.state.turn, game.state.logged, game.state.deeds = turn, logged, deeds
                            del game.state.log[count:]
                        raise
                    journal["action"] = action.model_dump(mode="json")
                    self.keep()
                    await game.emit({"type": "action", **action.model_dump()})
                self.advance("prepare")
            elif stage == "prepare":
                if "turn" not in journal and not journal.get("resolution"):
                    await game.prepare(Action.model_validate(journal["action"]))
                self.advance("rule")
            elif stage == "rule":
                action = Action.model_validate(journal["action"])
                if "ruling" not in journal:
                    ruling = await game.rule(action)
                    journal["action"] = action.model_dump(mode="json")
                    journal["ruling"] = ruling.model_dump(mode="json") if ruling else None
                    self.keep()
                    if ruling:
                        await game.emit({"type": "ruling", **ruling.model_dump()})
                elif journal["ruling"] is not None and "turn" not in journal and not journal.get("resolution"):
                    ruling = await game.checked_skill(action, Ruling.model_validate(journal["ruling"]), at=game._heading(action))
                    journal["ruling"] = ruling.model_dump(mode="json")
                    self.keep()
                if game.time_reserve is not None:
                    game.time_reserve(Action.model_validate(journal["action"]),
                                      Ruling.model_validate(journal["ruling"]) if journal["ruling"] else None)
                self.advance("resolve")
            elif stage == "resolve":
                if "turn" not in journal:
                    if game.time_wait is not None:
                        await game.time_wait(None)
                    action = Action.model_validate(journal["action"])
                    ruling = Ruling.model_validate(journal["ruling"]) if journal.get("ruling") else None
                    rolled = journal.get("resolution", {}).get("steps", {}).get("check", {}).get("done")
                    if (journal.get("by_hand") and ruling is not None and ruling.needs_roll and ruling.skill
                            and not ruling.repeat and action.declared is None and not journal.get("roll_confirmed") and not rolled):
                        ready, _ = self.answer("roll", {"kind": "roll", **game.roll_prompt(action, ruling)},
                                               request=digest([game.state.investigator.id, action.model_dump(mode="json"),
                                                               ruling.model_dump(mode="json")]))
                        if not ready:
                            return self.view()
                        journal["roll_confirmed"] = True
                        self.keep()
                    turn = await game.resolve(action, ruling)
                    self.keep(turn)
                self.advance("offer")
            elif stage == "offer":
                turn = self.turn()
                if turn.push and not journal.get("push_merged"):
                    if "push_choice" not in journal:
                        ready, value = self.answer("push", {"kind": "push", **public_push(game.state.pending_push or {})})
                        if not ready:
                            return self.view()
                        journal["push_choice"] = value
                        self.keep()
                    extra = await game.push(journal["push_choice"])
                    turn.absorb(extra)
                    turn.push = None
                    journal["push_merged"] = True
                    self.keep(turn)
                    continue
                accepted = next((key for key in journal if key.startswith("choice:")
                                 and not key.endswith(":merged") and not journal.get(f"{key}:merged")), None)
                if accepted is not None:
                    # The encounter can consume its pending choice before all damage
                    # and notifications finish. Its accepted answer remains the work
                    # to resume even when pending_choice() already returns None.
                    turn.absorb(await game.respond(accepted.removeprefix("choice:"), journal[accepted]))
                    journal[f"{accepted}:merged"] = True
                    self.keep(turn)
                    continue
                choice = game.pending_choice()
                if choice is not None:
                    key = f"choice:{choice.id}"
                    ready, value = self.answer(key, game.offer_payload(choice))
                    if not ready:
                        return self.view()
                    journal[key] = value
                    self.keep()
                    continue
                self.advance("talk")
            elif stage == "talk":
                turn = self.turn()
                await game.talk(turn)
                self.advance("narrate", turn)
            elif stage == "narrate":
                turn = self.turn()
                await game.narrate(turn)
                self.advance("remember", turn)
            elif stage == "remember":
                await game.remember()
                self.advance("finish")
            elif stage == "finish":
                view = self.view()
                await game.finish_request(self.turn() if "turn" in journal else None)
                if game.time_release is not None:
                    game.time_release()
                self.keep()
                game.visuals.resume()
                await game.emit({"type": "sheet"})
                return {**view, "pending": False, "snapshot": game.state.model_dump(mode="json")}
            else:
                raise ValueError(f"未知执行阶段：{stage}")

    def view(self) -> dict:
        return {"input": self.journal["input"], "by_hand": self.journal.get("by_hand", False),
                "action": self.journal.get("action"), "ruling": self.journal.get("ruling"),
                "pending": bool(self.journal.get("awaiting"))}


def build(game: Game, checkpointer=None):
    execution = Execution(game)

    async def execute(state: TurnState) -> dict:
        _bind(game)
        return await execution.run(state)

    async def choice(state: TurnState) -> dict:
        _bind(game)
        waiting = execution.journal["awaiting"]
        if "answer" not in waiting:
            waiting["answer"] = interrupt(waiting["payload"])
            execution.keep()
        return {}

    graph = StateGraph(TurnState)
    graph.add_node("execute", execute)
    graph.add_node("choice", choice)
    graph.add_edge(START, "execute")
    graph.add_conditional_edges("execute", lambda state: "choice" if state.get("pending") else END, ["choice", END])
    graph.add_edge("choice", "execute")
    return graph.compile(checkpointer=checkpointer)
