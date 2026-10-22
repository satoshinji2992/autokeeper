"""A request's prepared segments use separate graph runs and the ordinary interrupt protocol."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_engine import make


@pytest.fixture
def scenario() -> Module:
    return Module.load(EXAMPLE)


class RecordingGraph:
    def __init__(self, graph):
        self.graph, self.calls = graph, []
        self.checkpointer = graph.checkpointer

    async def astream(self, input, config, **kwargs):
        self.calls.append((copy.deepcopy(input), config, kwargs))
        async for event in self.graph.astream(input, config, **kwargs):
            yield event

    async def aget_state(self, config):
        return await self.graph.aget_state(config)


async def test_prepared_segments_end_separately_keep_the_seat_and_consume_ready_before_parse(scenario, fake, monkeypatch):
    game, *_ = make(scenario, fake, {})
    teammate = game.state.investigator.model_copy(update={"id": "lin", "name": "林"}, deep=True)
    game.state.investigators.append(teammate)
    game.state.active = 1
    parsed, remembered, finished, checkpoints = [], [], [], []
    count = 12  # more than 100 nodes in total; each ordinary graph run remains below its bound

    async def parse(text):
        assert not game.state.pending_turn.get("request", {}).get("ready")
        parsed.append((text, game.state.active))
        game.state.turn += 1
        return Action(kind="daily", summary=f"实际第{len(parsed)}段")

    async def prepare(action):
        pass

    async def rule(action):
        return Ruling(needs_roll=False)

    async def resolve(action, ruling):
        return Turn(action=action, ruling=ruling)

    async def play(turn):
        pass

    async def remember():
        remembered.append(len(parsed))

    async def finish(turn):
        assert remembered[-1] == len(parsed)
        finished.append(turn.action.summary)
        journal = game.state.pending_turn
        active, by_hand = journal["active"], journal["by_hand"]
        journal.clear()
        if len(parsed) < count:
            journal.update(input="原请求", active=active, by_hand=by_hand,
                           request={"ready": True, "part": len(parsed)})
            return True
        return False

    for name, method in {"parse": parse, "prepare": prepare, "rule": rule, "resolve": resolve,
                         "talk": play, "narrate": play, "remember": remember, "finish_request": finish}.items():
        monkeypatch.setattr(game, name, method)
    game.checkpoint = lambda: checkpoints.append(copy.deepcopy(game.state.pending_turn))
    app = RecordingGraph(turn_graph.build(game, InMemorySaver()))
    config = {"configurable": {"thread_id": "request"}, "recursion_limit": 20}
    try:
        events = [event async for event in turn_graph.stream(
            app, game, {"input": "原请求", "by_hand": True, "seat": "lin"}, config,
            stream_mode=["custom", "updates"])]
        assert len(app.calls) == count and len(parsed) == count
        assert parsed == [("原请求", 1)] * count
        assert finished == [f"实际第{i}段" for i in range(1, count + 1)]  # no old holder survives route
        assert all(cfg is config and kw == {"stream_mode": ["custom", "updates"]} for _, cfg, kw in app.calls)
        assert all(inp["by_hand"] and inp["seat"] == "lin" for inp, _, _ in app.calls)
        assert [inp["input"] for inp, _, _ in app.calls] == ["原请求"] + ["/retry"] * (count - 1)
        assert not game.state.pending_turn and game.state.active == 1
        assert sum(mode == "custom" and event.get("type") == "sheet" for mode, event in events) == count
        assert sum(row.get("request", {}).get("ready", False) for row in checkpoints) == count - 1
        for part in range(1, count):
            consumed = [row for row in checkpoints if row.get("request", {}).get("part") == part
                        and "ready" not in row["request"] and "action" not in row]
            assert consumed  # a durable checkpoint exists before that segment's parse receipt
    finally:
        await game.close()


class StubGraph:
    checkpointer = None

    def __init__(self, event):
        self.event, self.calls = event, []

    async def astream(self, input, config, **kwargs):
        self.calls.append((input, config, kwargs))
        yield self.event


@pytest.mark.parametrize("blocker", ["interrupt", "encounter", "choice", "push", "ending", "not-ready"])
async def test_stream_forwards_events_and_does_not_restart_unfinished_or_ordinary_runs(scenario, fake, monkeypatch, blocker):
    game, *_ = make(scenario, fake, {})
    game.state.pending_turn = {"input": "原请求", "request": {"ready": blocker != "not-ready"}}
    event = ("updates", {"__interrupt__": (object(),)}) if blocker == "interrupt" else ("custom", {"type": "notice"})
    app = StubGraph(event)
    if blocker == "encounter":
        monkeypatch.setattr(game, "battling", lambda: True)
    elif blocker == "choice":
        monkeypatch.setattr(game, "pending_choice", lambda: object())
    elif blocker == "push":
        game.state.pending_push = {"skill": "侦查"}
    elif blocker == "ending":
        game.state.ending = "finished"
    config, kwargs = {"configurable": {"thread_id": "one"}}, {"stream_mode": ["custom", "updates"]}
    try:
        events = [part async for part in turn_graph.stream(app, game, {"input": "原请求"}, config, **kwargs)]
        assert events == [event] and events[0] is event and len(app.calls) == 1
        assert app.calls[0][1] is config and app.calls[0][2] == kwargs
    finally:
        await game.close()


async def test_custom_only_stream_does_not_restart_a_checkpointed_interrupt(scenario, fake):
    game, *_ = make(scenario, fake, {})
    game.state.pending_turn = {"input": "原请求", "request": {"ready": True}}
    app = StubGraph({"type": "notice"})
    app.checkpointer = object()

    async def checkpoint(config):
        return SimpleNamespace(next=("ask_roll",))

    app.aget_state = checkpoint
    try:
        events = [event async for event in turn_graph.stream(app, game, {"input": "原请求"}, stream_mode="custom")]
        assert events == [{"type": "notice"}] and len(app.calls) == 1
    finally:
        await game.close()


async def test_instant_command_finishes_without_a_turn_or_graph_owned_cleanup(scenario, fake, monkeypatch):
    game, *_ = make(scenario, fake, {})
    finished = []

    async def command(text):
        assert text == ".r 1D6"

    async def remember():
        pass

    async def finish(turn):
        finished.append(turn)
        game.state.pending_turn.clear()
        return False

    monkeypatch.setattr(game, "command", command)
    monkeypatch.setattr(game, "remember", remember)
    monkeypatch.setattr(game, "finish_request", finish)
    monkeypatch.setattr(game, "complete_party_action", lambda: pytest.fail("finish_request owns party completion"))
    try:
        app = turn_graph.build(game, InMemorySaver())
        _ = [event async for event in turn_graph.stream(app, game, {"input": ".r 1D6"},
                                                      {"configurable": {"thread_id": "command"}}, stream_mode="updates")]
        assert finished == [None] and not game.state.pending_turn
    finally:
        await game.close()
