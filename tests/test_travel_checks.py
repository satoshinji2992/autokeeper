"""Entering can depend on the action's success; destination work keeps its ordinary order."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from autokeeper import config
from autokeeper.agents.cast import Action, Cast, Ruling
from autokeeper.engine import graph
from autokeeper.engine.game import TURN_RECORD, Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import Follow

from .test_engine import FakeCast
from .test_harness import SCRIPT, SCRIPTED, SEEN
from .test_harness import harness_sandbox as harness_sandbox

TEXT = "避开看守，进入钟楼"


def moving(**changes) -> Action:
    return Action(kind="move", summary=TEXT, destination="tower", arrival_after_success=True, **changes)


def ruled(**changes) -> Ruling:
    return Ruling.model_validate({"needs_roll": True, "skill": "潜行", "minutes": 3,
                                  "why": "确认你能否避开看守进入钟楼",
                                  "on_fail": {"minutes": 2},
                                  "on_push_fail": {"minutes": 4, "risk": "看守可能发现你并叫你退回"}, **changes})


class TravelCast(FakeCast):
    def __init__(self, action: Action, ruling: Ruling, *, consent: str | None = None):
        super().__init__({TEXT: (action, ruling)})
        self.contexts: list[tuple[str, str | None, list[str]]] = []
        self.consent = consent

    async def arbiter(self, state, action, at=None, **kwargs):
        self.contexts.append((state.location, at, list(state.visited)))
        return await super().arbiter(state, action, at)

    async def npc(self, state, npc_id, message, **kwargs):
        self.npc_calls.append((npc_id, message))
        if self.consent:
            assert self.world.apply(Follow(npc=npc_id, leader=state.investigator.id,
                                           destination=self.consent, actor=npc_id)).ok
        return "我陪你去。" if self.consent else "我留在这里。"


@pytest.fixture
def module() -> Module:
    module = Module.load(EXAMPLE)
    module.events = [Event(id="entered", title="进入钟楼", when="visit:tower", where="tower",
                           text="实际抵达后才发生", read_aloud="你已经到了钟楼里面。", sets=["entered_tower"])]
    module.flags["entered_tower"] = "已经抵达钟楼"
    module.flags["sneaked"] = "已经通过本次阻碍"
    module.endings, module.agendas, module.foreshadow = [], [], []
    module.showcases = []
    module.location("square").travel_minutes["tower"] = 7
    return module


def made(module, fake, action=None, ruling=None, *, state=None, consent=None):
    cast = TravelCast(action or moving(), ruling or ruled(), consent=consent)
    state = state or Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.investigator.skills["潜行"] = 40
    events = []

    async def emit(event):
        events.append(event)

    return Game(module, state, cast, Rules(dice=fake), emit=emit), cast, events


def arrived_once(game, turn, events, start, spent):
    assert game.state.location == "tower" and turn.arrived == "tower"
    assert game.state.minutes == start + spent
    assert game.state.visited.count("tower") == game.state.fired.count("entered") == 1
    assert "entered_tower" in game.state.flags
    assert sum(event["type"] == "move" for event in events) == 1


async def test_plain_move_is_still_free_of_arbiter_calls(module, fake):
    action = Action(kind="move", summary="走进钟楼", destination="tower")
    game, cast, events = made(module, fake, action)
    start = game.state.minutes
    ruling = await game.rule(action)
    turn = await game.resolve(action, ruling)
    assert not cast.ruled and not turn.checks and turn.primary is None
    arrived_once(game, turn, events, start, 7)
    await game.close()


async def test_the_new_order_is_not_a_command_to_roll(module, fake):
    action = moving()
    assert action.arrival_after_success
    game, cast, events = made(module, fake, action, ruled(needs_roll=False))
    start = game.state.minutes
    ruling = await game.rule(action)
    assert cast.contexts == [("square", None, ["square"])]
    turn = await game.resolve(action, ruling)
    assert not turn.checks and turn.primary is None
    arrived_once(game, turn, events, start, 10)
    await game.close()


async def test_a_real_primary_check_precedes_arrival_and_uses_the_origin(module, fake):
    game, cast, events = made(module, fake.queue(1))
    start = game.state.minutes
    action = await game.parse(TEXT)
    await game.prepare(action)
    ruling = await game.rule(action)
    assert cast.contexts == [("square", None, ["square"])]
    assert game.state.location == "square" and game.state.minutes == start and "tower" not in game.state.visited
    turn = await game.resolve(action, ruling)
    assert game.state.checks[-1].at == "square" and turn.primary == 0
    assert next(i for i, e in enumerate(events) if e["type"] == "dice") < next(
        i for i, e in enumerate(events) if e["type"] == "move")
    arrived_once(game, turn, events, start, 10)
    await game.close()


async def test_destination_search_still_arrives_before_its_check(module, fake):
    action = Action(kind="investigate", summary="去钟楼查看齿轮", target="齿轮间", destination="tower")
    game, cast, events = made(module, fake.queue(1), action, ruled(skill="侦查"))
    start = game.state.minutes
    ruling = await game.rule(action)
    assert cast.contexts == [("square", "tower", ["square"])]
    turn = await game.resolve(action, ruling)
    assert game.state.checks[-1].at == "tower"
    assert next(i for i, e in enumerate(events) if e["type"] == "move") < next(
        i for i, e in enumerate(events) if e["type"] == "dice")
    arrived_once(game, turn, events, start, 10)
    await game.close()


@pytest.mark.parametrize("choice,rolls,spent", [
    (None, [99], 5),
    ("drop", [45], 5),
    ({"choice": "push", "how": "贴着墙根，等看守转身再试"}, [45, 99], 17),
])
async def test_failed_or_dropped_entry_keeps_the_origin_and_does_not_charge_the_route(
        module, fake, choice, rolls, spent):
    game, _, events = made(module, fake.queue(*rolls))
    start = game.state.minutes
    action = await game.parse(TEXT)
    turn = await game.resolve(action, await game.rule(action))
    if choice is not None:
        assert turn.push and turn.push["place"] == "square"
        turn.absorb(await game.push(choice))
    assert game.state.location == "square" and game.state.minutes == start + spent
    assert not turn.arrived and "tower" not in game.state.visited and "entered" not in game.state.fired
    assert not any(e["type"] == "move" for e in events) and not fake.values
    await game.close()


@pytest.mark.parametrize("mode,spent", [("luck", 10), ("push", 20)])
@pytest.mark.parametrize("interrupted", [False, True])
async def test_saved_entry_choice_reuses_the_action_and_only_success_reaches_the_destination(
        module, fake, mode, spent, interrupted):
    game, _, first_events = made(module, fake.queue(45, *([1] if mode == "push" else [])))
    game.state.pending_turn = {"input": TEXT}
    start = game.state.minutes
    action = await game.parse(TEXT)
    ruling = await game.rule(action)
    turn = await game.resolve(action, ruling)
    assert turn.push and turn.push["action_record"]["arrival_after_success"]
    assert game.state.location == "square" and game.state.minutes == start + 3
    original = TURN_RECORD.dump_json(turn)
    before_luck = game.state.investigator.luck
    state = GameState.model_validate_json(game.state.model_dump_json())
    await game.close()
    game, cast, events = made(module, fake, state=state)
    choice = "luck" if mode == "luck" else {"choice": "push", "how": "贴墙等待看守转身再穿过去"}
    if interrupted:
        snapshots: list[str] = []
        game.checkpoint = lambda: snapshots.append(game.state.model_dump_json())

        async def disconnected(event):
            if event["type"] == "move":
                raise ConnectionError("arrival committed before its delivery failed")
            events.append(event)

        game.emit = disconnected
        with pytest.raises(ConnectionError):
            await game.push(choice)
        state = GameState.model_validate_json(snapshots[-1])
        await game.close()
        first_events += events
        game, cast, events = made(module, fake, state=state)
    extra = await game.push(choice)
    turn = TURN_RECORD.validate_json(original)
    turn.absorb(extra)
    arrived_once(game, turn, first_events + events, start, spent)
    assert not cast.ruled and not fake.values
    assert game.state.investigator.luck == before_luck - (5 if mode == "luck" else 0)
    await game.push(choice)
    assert game.state.minutes == start + spent and sum(e["type"] == "move" for e in events) == 1
    await game.close()


async def test_checked_stroll_stops_at_the_next_place_without_completing_the_final_destination(module, fake):
    action = Action(kind="move", summary="避开阻拦，沿路看看并去报社", destination="office",
                    travel="stroll", arrival_after_success=True)
    game, cast, events = made(module, fake.queue(1), action, ruled(set_flags=["sneaked"]))
    game.state.location = "tower"
    game.state.visited.append("tower")
    game.state.heard["office"] = "报社"
    start = game.state.minutes
    turn = await game.resolve(action, await game.rule(action))
    assert len(cast.ruled) == 1 and game.state.checks[-1].at == "tower"
    assert game.state.location == "square" and game.state.journey == "office"
    assert game.state.minutes == start + 10 and turn.arrived == "square"
    assert "office" not in game.state.visited and "sneaked" not in game.state.flags
    assert "尚未抵达报社" in game._report(turn)
    assert sum(e["type"] == "move" for e in events) == 1
    await game.close()


async def test_retry_keeps_the_original_entry_conditions_after_an_arrival_event_moves_the_party_elsewhere(module, fake):
    module.events[0].move_to = "office"
    module.location("office").exits = []  # the event can carry them there, but they cannot walk back along the old route
    game, _, events = made(module, fake.queue(1))
    game.state.pending_turn = {"input": TEXT}
    saved: list[str] = []
    game.checkpoint = lambda: saved.append(game.state.model_dump_json())
    action = await game.parse(TEXT)
    ruling = await game.rule(action)
    start = game.state.minutes

    async def disconnected(event):
        if event["type"] == "move" and event["location"] == "office":
            raise ConnectionError("the arrival event moved the party before its delivery failed")
        events.append(event)

    game.emit = disconnected
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    state = GameState.model_validate_json(saved[-1])
    assert state.location == "office" and state.visited.count("tower") == 1
    await game.close()
    game, cast, resumed_events = made(module, fake, state=state)
    turn = await game.resolve(action, ruling)
    assert game.state.location == turn.arrived == "office" and game.state.minutes == start + 10
    assert game.state.visited.count("tower") == game.state.visited.count("office") == 1
    assert game.state.fired.count("entered") == 1 and "entered_tower" in game.state.flags
    assert len(turn.checks) == 1 and not fake.values and not cast.ruled
    assert sum(e["type"] == "move" for e in events + resumed_events) == 2
    await game.close()


@pytest.mark.parametrize("consent", [None, "office", "tower"])
async def test_companion_consent_stays_before_the_entry_attempt_and_is_not_repeated(module, fake, consent):
    action = moving(companions=["preacher"])
    game, cast, events = made(module, fake.queue(*([1] if consent == "tower" else [])), action, consent=consent)
    game.state.pending_turn = {"input": TEXT}
    start = game.state.minutes
    await game.prepare(action)
    await game.prepare(action)
    assert [npc for npc, _ in cast.npc_calls] == ["preacher"]
    ruling = await game.rule(action)
    turn = await game.resolve(action, ruling)
    if consent == "tower":
        arrived_once(game, turn, events, start, 10)
        assert game.world.place("preacher") == "tower" and len(cast.ruled) == 1
    else:
        assert game.state.location == game.world.place("preacher") == "square"
        assert game.state.minutes == start and not turn.checks and not cast.ruled and not turn.arrived
        assert not any(e["type"] == "move" for e in events)
        if consent:
            assert game.world.entity("preacher").follow_to == "office"
    await game.close()


@pytest.mark.parametrize("blocked", ["missing", "unreachable"])
async def test_no_roll_does_not_bypass_missing_conditions_or_an_invalid_route(module, fake, blocked):
    action = moving()
    ruling = ruled(needs_roll=False, set_flags=["sneaked"], missing=["攀越所需绳索"] if blocked == "missing" else [])
    if blocked == "unreachable":
        module.location("square").exits = ["office"]
        module.location("office").exits = ["square"]
    game, _, events = made(module, fake, action, ruling)
    turn = await game.resolve(action, await game.rule(action))
    assert game.state.location == "square" and not turn.arrived
    assert "sneaked" not in game.state.flags and "tower" not in game.state.visited
    assert not any(e["type"] == "move" for e in events)
    await game.close()


@pytest.mark.parametrize("interrupted", ["dice", "move", "flag"])
async def test_graph_retry_resumes_a_saved_entry_without_repeating_the_roll_time_or_visit(module, fake, interrupted):
    game, cast, _ = made(module, fake.queue(1))
    start = game.state.minutes
    saved: list[str] = []
    failed = False

    def checkpoint():
        nonlocal failed
        saved.append(game.state.model_dump_json())
        reached = {"dice": bool(game.state.checks), "move": game.state.location == "tower",
                   "flag": "entered_tower" in game.state.flags}[interrupted]
        if reached and not failed:
            failed = True
            raise ConnectionError("interrupted after the corresponding state was saved")

    game.checkpoint = checkpoint
    app = graph.build(game, InMemorySaver())
    run_config = {"configurable": {"thread_id": "entry"}}
    first = [row async for row in app.astream({"input": TEXT, "by_hand": True}, run_config)]
    assert any("__interrupt__" in row for row in first)
    assert game.state.location == "square" and "tower" not in game.state.visited
    delivered = []
    with pytest.raises(ConnectionError):
        async for row in app.astream(Command(resume=True), run_config, stream_mode="custom"):
            delivered.append(row)
    state = GameState.model_validate_json(saved[-1])
    await game.close()
    game, reloaded_cast, _ = made(module, fake, state=state)
    fresh = graph.build(game, InMemorySaver())
    delivered += [row async for row in fresh.astream({"input": "/retry"},
                                                    {"configurable": {"thread_id": "entry_resumed"}}, stream_mode="custom")]
    assert game.state.location == "tower" and game.state.minutes == start + 10
    assert game.state.visited.count("tower") == game.state.fired.count("entered") == 1
    assert len(game.state.checks) == 1 and game.state.checks[0].at == "square"
    assert len(cast.ruled) == 1 and not reloaded_cast.ruled and not reloaded_cast.heard and not fake.values
    assert sum(e["type"] == "move" for e in delivered) == 1
    await game.close()


async def test_actual_arbiter_reads_the_origin_and_does_not_claim_the_destination_was_reached(module, fake, harness_sandbox):
    module.location("square").keeper = "广场的当前哨卡。"
    module.location("tower").keeper = "钟楼内部尚未到达的房间。"
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}, visual_limit=0, minds=0), module)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    game = Game(module, state, cast, Rules(dice=fake))
    SCRIPT.append({"needs_roll": True, "skill": "潜行", "minutes": 3})
    try:
        ruling = await game.rule(moving())
        assert ruling is not None and ruling.needs_roll
        prompt = SEEN[-1]["messages"][-1]["content"]
        assert "【本地守秘人资料】\n广场的当前哨卡。" in prompt
        assert "调查员为此先走到了钟楼" not in prompt and "钟楼内部尚未到达的房间。" not in prompt
        assert "arrival_after_success" in prompt
        assert game.state.location == "square" and game.state.usage["arbiter"]["calls"] == 1
    finally:
        await game.close()
        await cast.close()
