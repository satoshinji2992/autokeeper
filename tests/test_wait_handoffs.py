"""LAN passive waits yield to accepted actions without replaying prior work or rewinding time."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Plan, Ruling
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_request_execution import RequestCast, telling


class WaitingCast(RequestCast):
    """The scripted KP continues a bounded wait until its end, unless the test supplies a choice."""

    async def plan(self, state, text):
        if state.pending_turn.get("wait_refresh"):
            self.planned.append(text)
            return Plan(action=Action.model_validate(state.pending_turn["request"]["wait_action"]))
        return await super().plan(state, text)

    async def keeper(self, state, report, on_delta=None, lore="", *, check=None, fix=""):
        result = await super().keeper(state, report, on_delta, lore, check=check, fix=fix)
        request = state.pending_turn.get("request", {})
        window = request.get("wait_window")
        action = state.pending_turn.get("action", {})
        end = state.pending_turn.get("turn", {}).get("interval", {}).get("end", state.minutes)
        if (action.get("kind") == "wait" and window and end < window["end"]
                and result.continuation is not None and result.continuation.next is None
                and not result.continuation.pending):
            result.continuation.next = Plan(action=Action(kind="wait", summary=action["summary"],
                                                         wait_until=action.get("wait_until", "")))
        return result


def made(fake, plans, rulings, *, cast_type=WaitingCast, state=None):
    from .test_request_execution import module

    m = module()
    if state is None:
        first = m.pregens[0].model_copy(deep=True)
        second = first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
        state = Game.new(m, [first, second], slot="wait-handoff", human=[first.id, second.id])
        state.location, state.minutes = "tower", 1440 + 3 * 60 + 27
    cast = cast_type(plans, rulings, [telling() for _ in range(12)])
    hub = Hub(EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    hub.m.events.clear()
    hub.m.endings.clear()
    hub.m.foreshadow.clear()
    hub.m.agendas.clear()
    hub.sits = {"zhouheng": "a", "lin": "b"}

    def fail(what, exc):
        raise RuntimeError(f"{what}: {exc}") from exc

    async def close():
        return None

    hub._failed = fail
    cast.close = close
    return hub


def pool(hub, *declarations, automatic=()):
    hub.pool = list(declarations)
    hub.automatic = set(automatic)
    for seat, text in declarations:
        if seat not in hub.automatic:
            hub._accept_declaration(seat, text)


async def collecting(hub):
    """Open the real editable window; pure preparation, not an expired timer, seals it."""
    task = asyncio.create_task(hub._collect())
    for _ in range(100):
        if hub.open:
            return task
        if task.done():
            await task
            raise AssertionError("collection ended before an input window opened")
        await asyncio.sleep(.001)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    raise AssertionError("input window did not open")


def wait_plans():
    guard = Action(kind="wait", summary="守在门口到六点；有人求救就停止", wait_until="D2 06:00")
    hurry = Action(kind="move", summary="立即去广场找医生", destination="square", alone=True)
    return {"守候": Plan(action=guard), "急事": Plan(action=hurry)}, {
        guard.summary: Ruling(needs_roll=False, minutes=720), hurry.summary: Ruling(needs_roll=False, minutes=0),
    }


async def test_idle_peer_holds_a_long_wait_until_explicit_pass_without_replanning_or_relogging(fake):
    plans, rulings = wait_plans()
    hub = made(fake, plans, rulings)
    start = hub.state.minutes
    try:
        pool(hub, ("zhouheng", "守候"))
        await hub._settle()
        assert hub.state.minutes == start and not hub.cast.reports and not hub.state.pending_turn
        parked = hub.state.unfinished["zhouheng"]["execution"]
        assert parked["request"]["wait_window"] == {"start": start, "end": 1800}
        assert not parked["request"]["completed"]
        pool(hub, ("zhouheng", "守候"), automatic=["zhouheng"])
        hub.done = {"lin"}
        await hub._settle()
        assert hub.state.minutes == 1800 and not hub.state.unfinished and not hub.state.pending_turn
        assert hub.cast.party_calls == 1 and not hub.cast.planned and len(hub.cast.ruled) == 1
        assert [entry.text for entry in hub.state.log if entry.kind == "player"] == ["守候"]
    finally:
        await hub.game.close()


async def test_sealed_wait_rejects_late_input_then_accepts_it_in_the_next_window_before_time_can_skip(fake, monkeypatch):
    class Paused(WaitingCast):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def arbiter(self, state, action, at=None, **kwargs):
            if action.kind == "wait":
                self.entered.set()
                await self.release.wait()
            return await super().arbiter(state, action, at, **kwargs)

    plans, rulings = wait_plans()
    hub = made(fake, plans, rulings, cast_type=Paused)
    start = hub.state.minutes
    sent = []

    async def send(cid, msg):
        sent.append((cid, msg))

    monkeypatch.setattr(hub, "_send", send)
    try:
        pool(hub, ("zhouheng", "守候"))
        task = asyncio.create_task(hub._settle())
        await asyncio.wait_for(hub.cast.entered.wait(), 3)
        await hub._on("b", {"t": "act", "text": "急事"})
        assert sent[-1][1]["t"] == "action_rejected" and sent[-1][1]["text"] == "急事"
        assert not hub.early and not any(row.seat == "lin" for row in hub.state.declarations)
        hub.cast.release.set()
        await asyncio.wait_for(task, 3)
        assert hub.state.minutes == start and not hub.early
        assert not hub.cast.reports and hub.game.world.place("lin") == "tower"
        task = await collecting(hub)
        await hub._on("b", {"t": "act", "text": "急事", "round": hub.round_id})
        await asyncio.wait_for(task, 3)
        await hub._settle()
        travel = hub.game._travel("tower", "square")
        assert hub.state.minutes == start + travel and hub.game.world.place("lin") == "square"
        assert "execution" in hub.state.unfinished["zhouheng"]
        assert hub.cast.party_calls == 2 and hub.cast.planned == ["守候"]
        assert len([entry for entry in hub.state.log if entry.kind == "player" and entry.text == "守候"]) == 1
    finally:
        await hub.game.close()


@pytest.mark.parametrize("recover", [False, True])
async def test_a_saved_wait_catches_up_after_other_work_without_charging_it_again(fake, recover):
    plans, rulings = wait_plans()
    hub = made(fake, plans, rulings)
    games = [hub.game]
    start = hub.state.minutes
    try:
        pool(hub, ("zhouheng", "守候"))
        await hub._settle()
        if recover:
            state = GameState.model_validate_json(hub.state.model_dump_json())
            hub = made(fake, plans, rulings, state=state)
            games.append(hub.game)
        # A teammate's actual work advanced the world while the authorized guard stayed put.
        pool(hub, ("lin", "急事"))
        await hub._settle()
        after_journey = hub.state.minutes
        pool(hub, ("zhouheng", "守候"), automatic=["zhouheng"])
        hub.done = {"lin"}
        await hub._settle()
        assert after_journey > start and hub.state.minutes == 1800
        assert hub.game.world.place("zhouheng") == "tower" and hub.game.world.place("lin") == "square"
        assert len([entry for entry in hub.state.log if entry.kind == "player" and entry.text == "守候"]) == 1
        assert any("不重复推进" in report for report in hub.cast.reports)
        assert not hub.state.pending_turn and not hub.state.unfinished
    finally:
        for game in games:
            await game.close()


async def test_an_explicit_five_minute_wait_bounds_the_other_players_long_wait(fake):
    short = Action(kind="wait", summary="最多等五分钟", wait_limit=5)
    long = Action(kind="wait", summary="等到早晨七点", wait_until="D2 07:00")
    plans = {"短等": Plan(action=short), "长等": Plan(action=long)}
    rulings = {short.summary: Ruling(needs_roll=False, minutes=60), long.summary: Ruling(needs_roll=False, minutes=720)}
    hub = made(fake, plans, rulings)
    hub.state.minutes = 1440 + 4 * 60 + 37
    start = hub.state.minutes
    try:
        pool(hub, ("zhouheng", "短等"), ("lin", "长等"))
        await hub._settle()
        assert hub.state.minutes == start + 5
        assert hub.state.unfinished["zhouheng"]["execution"]["request"]["wait_window"]["end"] == start + 5
        assert hub.state.unfinished["lin"]["execution"]["request"]["wait_window"]["end"] == 1860
        pool(hub, ("zhouheng", "短等"), ("lin", "长等"), automatic=["zhouheng", "lin"])
        await hub._settle()
        assert hub.state.minutes == start + 5 and "zhouheng" not in hub.state.unfinished
        assert hub.state.unfinished["lin"]["execution"]["request"]["wait_window"]["end"] == 1860
        assert len([entry for entry in hub.state.log if entry.kind == "player"]) == 2
    finally:
        await hub.game.close()


@pytest.mark.parametrize("command", ["replace", "undo"])
async def test_a_new_declaration_or_undo_cancels_only_unperformed_waiting(fake, command):
    plans, rulings = wait_plans()
    hub = made(fake, plans, rulings)
    start = hub.state.minutes
    task = None
    try:
        pool(hub, ("zhouheng", "守候"))
        await hub._settle()
        assert hub.state.unfinished["zhouheng"].get("execution")
        task = await collecting(hub)
        if command == "replace":
            await hub._on("a", {"t": "act", "text": "急事", "round": hub.round_id})
            assert hub.pool == [("zhouheng", "急事")] and not hub.early
        else:
            await hub._on("a", {"t": "undo", "round": hub.round_id})
            assert not hub.pool and not hub.early
        assert "execution" not in hub.state.unfinished["zhouheng"]
        assert hub.state.minutes == start and not hub.state.checks
        assert len([entry for entry in hub.state.log if entry.kind == "player"]) == 1
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()


async def test_accepted_editable_receipts_survive_restore_and_are_not_exposed_to_clients(fake):
    plans, rulings = wait_plans()
    hub = made(fake, plans, rulings)
    task = None
    try:
        start = hub.state.minutes
        task = await collecting(hub)
        await hub._on("b", {"t": "act", "text": "急事", "round": hub.round_id})
        restored = GameState.model_validate_json(hub.state.model_dump_json())
        assert [(row.seat, row.text) for row in restored.declarations if not row.batch] == [("lin", "急事")]
        assert [(row.seat, row.text, row.at) for row in restored.declarations] == [("lin", "急事", start)]
        assert "declarations" not in hub._view("zhouheng")
        assert "unfinished" not in hub._view("lin")
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await hub.game.close()


async def test_suspended_wait_does_not_start_a_timer_and_peer_done_releases_the_existing_request(fake):
    plans, rulings = wait_plans()
    hub = made(fake, plans, rulings)
    try:
        pool(hub, ("zhouheng", "守候"))
        await hub._settle()
        task = await collecting(hub)
        assert hub.open and hub._left() is None and hub._waiting_for() == ["lin"]
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), .06)
        assert hub.open and hub.preparing is None and hub.state.minutes == 1647
        await hub._on("b", {"t": "done", "round": hub.round_id})
        await asyncio.wait_for(task, 3)
        assert hub.pool == [("zhouheng", "守候")] and hub.automatic == {"zhouheng"}
        await hub._settle()
        assert hub.state.minutes == 1800 and not hub.state.unfinished
        assert hub.cast.party_calls == 1 and not hub.cast.planned
    finally:
        await hub.game.close()


async def test_an_ordinary_walk_and_talk_never_wait_for_an_idle_peer_to_pass(fake):
    plans, rulings = wait_plans()
    hub = made(fake, plans, rulings)
    start = hub.state.minutes
    try:
        pool(hub, ("lin", "急事"))
        await hub._settle()
        assert hub.game.world.place("lin") == "square"
        assert hub.state.minutes == start + hub.game._travel("tower", "square")
        assert not hub.state.unfinished and not hub.state.pending_turn
    finally:
        await hub.game.close()


async def test_deadline_is_anchored_to_receipt_not_when_the_queued_wait_is_finally_parsed(fake):
    action = Action(kind="wait", summary="最多等五分钟", wait_limit=5)
    hub = made(fake, {"短等": Plan(action=action)}, {action.summary: Ruling(needs_roll=False, minutes=180)})
    start = hub.state.minutes
    try:
        pool(hub, ("zhouheng", "短等"))
        hub.state.minutes += 30  # another already committed action; it cannot be rolled back
        hub.done = {"lin"}
        await hub._settle()
        assert hub.state.minutes == start + 30 and not hub.state.unfinished
        assert any("剩余0分钟" in report for report in hub.cast.reports)
        assert not hub.state.checks
    finally:
        await hub.game.close()


async def test_a_prepared_continuation_yields_to_new_input_and_resumes_without_repeating_its_prefix(fake):
    work = Action(kind="use", summary="先登记工具")
    waiting = Action(kind="wait", summary="然后等十分钟", wait_limit=10)
    hurry = Action(kind="move", summary="马上去广场", destination="square", alone=True)
    plans = {"复合": Plan(action=work, remaining="然后等十分钟"), "急事": Plan(action=hurry)}
    rules = {work.summary: Ruling(needs_roll=False, minutes=2), waiting.summary: Ruling(needs_roll=False, minutes=10),
             hurry.summary: Ruling(needs_roll=False, minutes=0)}
    hub = made(fake, plans, rules)
    hub.cast.tellings.clear()
    hub.cast.tellings.extend([telling(next=Plan(action=waiting)), telling(), telling()])
    try:
        pool(hub, ("zhouheng", "复合"))
        await hub._settle()
        start = hub.state.minutes
        parked = hub.state.unfinished["zhouheng"]["execution"]
        assert parked["request"]["completed"][0]["action"] == work.summary
        assert "ruling" not in parked  # KP prepared this prefix; no rules call occurred yet
        assert parked["request"]["wait_window"]["end"] == start + 10
        task = await collecting(hub)
        await hub._on("b", {"t": "act", "text": "急事", "round": hub.round_id})
        await asyncio.wait_for(task, 3)
        await hub._settle()
        assert hub.state.minutes == start + hub.game._travel("tower", "square")
        assert len([entry for entry in hub.state.log if entry.kind == "player" and entry.text == "复合"]) == 1
        assert [action.summary for action in hub.cast.ruled].count(work.summary) == 1
        assert hub.cast.party_calls == 2 and hub.cast.planned == ["复合"]
        assert "zhouheng" not in hub.state.unfinished  # the original ten minutes elapsed during the actual journey
    finally:
        await hub.game.close()


async def test_a_late_replacement_cannot_cancel_a_sealed_wait_while_another_seat_is_being_ruled(fake, monkeypatch):
    class Paused(WaitingCast):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def arbiter(self, state, action, at=None, **kwargs):
            if action.kind == "use":
                self.entered.set()
                await self.release.wait()
            return await super().arbiter(state, action, at, **kwargs)

    plans, rulings = wait_plans()
    work = Action(kind="use", summary="先整理手边工具")
    plans["急事"] = Plan(action=work)
    rulings[work.summary] = Ruling(needs_roll=False, minutes=1)
    hub = made(fake, plans, rulings, cast_type=Paused)
    sent = []

    async def send(cid, msg):
        sent.append((cid, msg))

    monkeypatch.setattr(hub, "_send", send)
    try:
        pool(hub, ("zhouheng", "守候"))
        await hub._settle()
        pool(hub, ("lin", "急事"), ("zhouheng", "守候"), automatic=["zhouheng"])
        settling = asyncio.create_task(hub._settle())
        reached = asyncio.create_task(hub.cast.entered.wait())
        await asyncio.wait([settling, reached], timeout=3, return_when=asyncio.FIRST_COMPLETED)
        if settling.done():
            await settling
        assert reached.done(), (hub.pool, hub.cast.planned, hub.cast.ruled, hub.state.round_plan, hub.state.pending_turn, hub.state.unfinished)
        await hub._on("a", {"t": "act", "text": "急事"})
        assert sent[-1][1]["t"] == "action_rejected" and sent[-1][1]["text"] == "急事"
        assert hub.state.unfinished["zhouheng"].get("execution")
        hub.cast.release.set()
        await asyncio.wait_for(settling, 3)
        assert not hub.early
        assert hub.state.unfinished["zhouheng"]["execution"]["request"]["wait_window"]["end"] == 1800
        assert hub.cast.party_calls == 2 and hub.cast.planned == ["守候"]
        assert len([entry for entry in hub.state.log if entry.kind == "player" and entry.text == "守候"]) == 1
        assert len([entry for entry in hub.state.log if entry.kind == "player" and entry.text == "急事"]) == 1
        assert hub.state.minutes == 1648
    finally:
        await hub.game.close()


async def test_real_websocket_wait_handoff_survives_disconnection_and_reconnection(fake, monkeypatch):
    import aiohttp

    from .test_net import closed, hold_preparation, opened, pool_open, until

    plans, rulings = wait_plans()
    hub = made(fake, plans, rulings)
    hub.sits = {"zhouheng": None, "lin": None}
    ready = hold_preparation(monkeypatch, hub.game)

    task = await opened(hub)
    try:
        async with aiohttp.ClientSession() as http:
            first = await http.ws_connect(f"http://127.0.0.1:{hub.port}/ws")
            second = await http.ws_connect(f"http://127.0.0.1:{hub.port}/ws")
            for ws, name, seat in [(first, "甲", "zhouheng"), (second, "乙", "lin")]:
                await ws.send_json({"t": "hello", "name": name})
                await until(ws, lambda message: message["t"] == "welcome")
                await ws.send_json({"t": "sit", "investigator": seat})
            initial = await until(first, pool_open)
            start = hub.state.minutes
            await first.send_json({"t": "act", "text": "守候", "round": initial["round"]})
            await until(first, lambda message: pool_open(message) and message["mine"]["now"] == ["守候"])
            ready.set()
            await until(first, lambda message: pool_open(message) and "execution" in hub.state.unfinished.get("zhouheng", {}))
            assert hub.state.minutes == start
            await first.close()
            replacement = await http.ws_connect(f"http://127.0.0.1:{hub.port}/ws")
            await replacement.send_json({"t": "hello", "name": "甲"})
            await until(replacement, lambda message: message["t"] == "welcome")
            await replacement.send_json({"t": "sit", "investigator": "zhouheng"})
            seated = await until(replacement, lambda message: message["t"] == "seated")
            assert seated["pool"]["open"] and seated["pool"]["mine"]["now"] == ["守候"]
            assert seated["pool"]["closes_in"] is None and hub.preparing is None
            ready.clear()
            await second.send_json({"t": "act", "text": "急事", "round": seated["pool"]["round"]})
            await until(second, lambda message: pool_open(message) and message["mine"]["now"] == ["急事"])
            ready.set()
            current = await until(second, lambda message: pool_open(message) and hub.game.world.place("lin") == "square")
            assert hub.state.minutes == start + hub.game._travel("tower", "square")
            assert hub.game.world.place("zhouheng") == "tower"
            await second.send_json({"t": "done", "round": current["round"]})
            await until(replacement, lambda message: pool_open(message) and hub.state.minutes == 1800 and not hub.state.pending_turn)
            assert not hub.state.pending_turn and not hub.state.unfinished
            assert len([entry for entry in hub.state.log if entry.kind == "player" and entry.text == "守候"]) == 1
            # Stop collection before socket cleanup can legitimately start an offline AI turn.
            await closed(task)
            task = None
    finally:
        if task is not None:
            await closed(task)


async def test_pending_means_a_real_handoff_and_is_not_silently_overridden_by_the_wait_driver(fake):
    other = Action(kind="use", summary="同伴处理五分钟的事", alone=True)
    action = Action(kind="wait", summary="等候到六点", wait_until="D2 06:00")
    plans = {"守候": Plan(action=action), "办事": Plan(action=other)}
    rules = {other.summary: Ruling(needs_roll=False, minutes=5), action.summary: Ruling(needs_roll=False, minutes=180)}
    hub = made(fake, plans, rules)
    pending = "门口出现一个陌生人，要先决定是否回应。"
    hub.cast.tellings.clear()
    hub.cast.tellings.extend([telling(), telling(pending=pending)])
    try:
        pool(hub, ("lin", "办事"), ("zhouheng", "守候"))
        await hub._settle()
        note = hub.state.unfinished["zhouheng"]
        assert note["remaining"] == pending and "execution" not in note
        assert hub.state.minutes == 1440 + 3 * 60 + 32
        assert len([action for action in hub.cast.ruled if action.kind == "wait"]) == 1
    finally:
        await hub.game.close()


async def test_a_confirmed_wait_roll_is_not_asked_or_thrown_again_after_handoff(fake):
    from langgraph.types import Command

    from .test_request_execution import make, run

    action = Action(kind="wait", summary="警戒等十分钟", wait_limit=10)
    cast = WaitingCast({"原话": Plan(action=action)}, {action.summary: Ruling(
        needs_roll=True, skill="侦查", minutes=10,
    )}, [telling()])
    game, graph = make(fake, cast, party=True)
    game.wait_horizon = lambda actor: game.state.minutes
    fake.queue(1)
    try:
        _, offers = await run(graph, game, {"input": "原话", "by_hand": True})
        assert offers[0]["kind"] == "roll"
        _, offers = await run(graph, game, Command(resume="roll"))
        assert not offers and len(fake.values) == 1 and not game.state.checks
        saved = game.state.unfinished[game.state.investigator.id]["execution"]
        assert saved["roll_confirmed"]
        game.wait_horizon = lambda actor: None
        assert game.resume_wait(game.state.investigator.id)
        _, offers = await run(graph, game, {"input": "原话", "by_hand": True})
        assert not offers and not fake.values and len(game.state.checks) == 1
        assert cast.planned == ["原话"] and len(cast.ruled) == 1
        assert len([entry for entry in game.state.log if entry.kind == "player"]) == 1
    finally:
        await game.close()


async def test_an_authored_clock_event_interrupts_a_wait_before_its_far_later_deadline(fake):
    from autokeeper.module.schema import Event

    plans, rules = wait_plans()
    hub = made(fake, plans, rules)
    hub.m.events = [Event(id="appeal", title="有人求救", when="D2 03:32", where="tower",
                          text="有人求救，需要调查员决定是否回应。", read_aloud="楼下有人求救。")]
    hub.cast.tellings.clear()
    hub.cast.tellings.append(telling(pending="先决定是否回应求救，再继续守候。"))
    hub.done = {"lin"}
    try:
        pool(hub, ("zhouheng", "守候"))
        await hub._settle()
        assert hub.state.minutes == 1440 + 3 * 60 + 32 and hub.state.fired == ["appeal"]
        assert "execution" not in hub.state.unfinished["zhouheng"]
        assert hub.state.unfinished["zhouheng"]["remaining"].startswith("先决定")
        assert len(hub.cast.reports) == 1 and len(hub.cast.ruled) == 1
    finally:
        await hub.game.close()


@pytest.mark.parametrize("mode", ["fight", "chase"])
@pytest.mark.parametrize("during_collection", [False, True])
async def test_saved_wait_is_never_silently_accepted_as_a_new_tactical_declaration(fake, monkeypatch, mode, during_collection):
    from .test_turn_based_collection import encounter

    plans, rules = wait_plans()
    hub = made(fake, plans, rules)
    offered = []
    try:
        pool(hub, ("zhouheng", "守候"))
        await hub._settle()
        start = hub.state.minutes

        async def confirm(seat, who, offer, *, timed):
            offered.append((seat, offer["kind"], timed))
            assert not hub.pool and not hub.state.unfinished[seat].get("execution")
            assert hub.state.encounter.completed_rounds == 0 and hub.state.minutes == start
            await hub._on("b", {"t": "done"})
            return "keep"

        async def interruption():
            hub.state.encounter = encounter(mode)

        monkeypatch.setattr(hub, "_ask", confirm)
        if during_collection:
            monkeypatch.setattr(hub, "_interlude", interruption)
        else:
            await interruption()
        await asyncio.wait_for(hub._collect(), 3)
        assert offered == [("zhouheng", "redeclare", False)]
        assert hub.pool == [("zhouheng", "守候")] and not hub.automatic
        assert hub.state.round_plan["encounter"] == ["sudden", mode, 1]
        assert hub.state.minutes == start and hub.state.encounter.completed_rounds == 0
        assert not hub.state.checks
    finally:
        await hub.game.close()


async def test_new_world_facts_reach_the_same_kp_before_a_saved_wait_can_advance_time(fake):
    from autokeeper.world.ops import SetFlag
    from autokeeper.world.record import KEEPER

    plans, rules = wait_plans()
    stopped = Action(kind="other", summary="来人已按约敲钟，停止守候并确认眼前情况")
    rules[stopped.summary] = Ruling(needs_roll=False, minutes=0)

    class Current(WaitingCast):
        async def plan(self, state, text):
            if text == "守候" and "bell_stopped" in state.flags:
                self.planned.append(text)
                assert state.pending_turn["request"]["wait_window"]["end"] == 1800
                assert "当前记录检查原停止条件" in state.unfinished[state.investigator.id]["reason"]
                return Plan(action=stopped)
            return await super().plan(state, text)

    hub = made(fake, plans, rules, cast_type=Current)
    try:
        start = hub.state.minutes
        pool(hub, ("zhouheng", "守候"))
        await hub._settle()
        assert hub.game.world.apply(SetFlag(flag="bell_stopped", actor=KEEPER)).ok
        pool(hub, ("zhouheng", "守候"), automatic=["zhouheng"])
        hub.done = {"lin"}
        await hub._settle()
        assert hub.state.minutes == start and not hub.state.unfinished and not hub.state.pending_turn
        assert [action.summary for action in hub.cast.ruled] == [plans["守候"].action.summary, stopped.summary]
        assert hub.cast.party_calls == 1 and hub.cast.planned == ["守候"]
        assert len([entry for entry in hub.state.log if entry.kind == "player"]) == 1
        assert not hub.state.checks
    finally:
        await hub.game.close()


async def test_sealed_checkpoint_recovery_keeps_the_wait_marker_deadline_and_one_original_input(fake):
    plans, rules = wait_plans()
    hub = made(fake, plans, rules)
    games, snapshots = [hub.game], []
    try:
        pool(hub, ("zhouheng", "守候"))
        await hub._settle()
        deadline = hub.state.unfinished["zhouheng"]["execution"]["request"]["wait_window"]
        pool(hub, ("zhouheng", "守候"), automatic=["zhouheng"])

        def sealed_checkpoint():
            receipt = hub.state.round_plan
            if receipt.get("planned") and any(step.get("resuming_wait") for step in receipt.get("steps", [])):
                snapshots.append(hub.state.model_dump_json())
                raise ConnectionError("sealed waiting batch saved")

        hub.game.checkpoint = sealed_checkpoint
        with pytest.raises(ConnectionError, match="sealed waiting batch"):
            await hub._settle()
        restored = GameState.model_validate_json(snapshots[-1])
        assert restored.round_plan["steps"][0]["resuming_wait"]
        hub = made(fake, plans, rules, state=restored)
        games.append(hub.game)
        assert not hub.automatic and hub.game.pending_party() == [("zhouheng", "守候")]
        hub.done = {"lin"}
        await hub._settle()
        assert hub.state.minutes == deadline["end"]
        assert hub.cast.planned == [] and hub.cast.ruled == []
        assert not hub.state.pending_turn and not hub.state.unfinished
        assert len([entry for entry in hub.state.log if entry.kind == "player"]) == 1
    finally:
        for game in games:
            await game.close()


async def test_redeclaring_with_new_text_replaces_the_old_input_timestamp(fake, monkeypatch):
    plans, rules = wait_plans()
    hub = made(fake, plans, rules)
    try:
        hub.open = True
        start = hub.state.minutes
        hub._accept_declaration("zhouheng", "守候")
        hub.early = [("zhouheng", "守候")]
        hub.state.minutes += 10

        async def revise(*args, **kwargs):
            return {"text": "改去广场"}

        monkeypatch.setattr(hub, "_ask", revise)
        await hub._confirm_queued("zhouheng", "守候")
        assert hub.pool == [("zhouheng", "改去广场")] and not hub.early
        assert [(row.seat, row.text, row.at) for row in hub.state.declarations] == [("zhouheng", "改去广场", start + 10)]
        hub.state.minutes += 10
        hub._accept_declaration("zhouheng", "守候")
        assert hub.state.declarations[-1].at == start + 20
        assert len([row for row in hub.state.declarations if row.text == "守候"]) == 1
    finally:
        await hub.game.close()
