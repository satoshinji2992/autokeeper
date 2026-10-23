"""A LAN journey carries only each player's own currently authorised movement."""

from __future__ import annotations

import asyncio
import copy

import pytest

from autokeeper.agents.cast import Action, Plan, Ruling
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_multiplayer_transport import sit, sockets
from .test_net import until
from .test_request_execution import RequestCast, telling


def make_hub(fake, plans, tellings):
    m = Module.load(EXAMPLE)
    base = m.pregens[0]
    party = [base.model_copy(update={"id": ident, "name": name}, deep=True)
             for ident, name in (("elois", "埃洛娃"), ("betty", "贝蒂"), ("bert", "伯特"))]
    st = Game.new(m, party, human=[inv.id for inv in party])
    st.location = "square"
    cast = RequestCast(plans, {plan.action.summary: Ruling(needs_roll=False, minutes=5 if plan.action.kind == "wait" else 0)
                               for plan in plans.values()}, tellings)
    hub = Hub(EXAMPLE, state=st, cast=cast, rules=Rules(dice=fake))
    hub.m.events, hub.m.endings, hub.m.foreshadow, hub.m.agendas = [], [], [], []
    hub.started.set()
    return hub, cast


def declared_move(*, companions=None, remaining=""):
    return Plan(action=Action(kind="move", summary="前往钟楼", destination="tower", companions=companions or []),
                remaining=remaining)


def stay():
    return Plan(action=Action(kind="wait", summary="在广场留守"))


def journeys(hub):
    return [event for event in hub.state.record.events if event.op.startswith("travel:")]


async def test_named_companion_and_same_room_do_not_override_either_players_own_staying_declaration(fake):
    plans = {"我和贝蒂去钟楼": declared_move(companions=["betty"]), "我留在广场": stay(), "我也留守": stay()}
    hub, cast = make_hub(fake, plans, [telling(), telling(), telling()])
    try:
        async with sockets(hub) as (http, url):
            players = [await sit(http, url, seat) for seat in ("elois", "betty", "bert")]
            collected = asyncio.create_task(hub._collect())
            await until(players[0], lambda msg: msg["t"] == "pool" and msg["open"])
            for ws, text in zip(players, plans, strict=True):
                await ws.send_json({"t": "act", "text": text})
                await ws.send_json({"t": "done"})
            await asyncio.wait_for(collected, 1)
            await hub._settle()
            assert {seat: hub.game.world.place(seat) for seat in ("elois", "betty", "bert")} == {
                "elois": "tower", "betty": "square", "bert": "square"}
            assert len(journeys(hub)) == 1 and set(journeys(hub)[0].motions) == {"elois"}
            assert hub.game.world.entity("tower").observed_by == ["elois"]
            assert hub._view("betty")["location"] == hub._view("bert")["location"] == "square"
            assert hub._view("elois")["location"] == "tower"
            assert hub.game.pending_party() is None and cast.party_calls == 1
    finally:
        await hub.game.close()


@pytest.mark.parametrize("execute_tail,tail_minutes", [(False, 0), (True, 0), (True, 5)])
async def test_authorised_passenger_keeps_their_original_token_and_tail_without_spending_the_journey_twice(fake, execute_tail, tail_minutes):
    tail = "到达后向守钟人询问钟声。"
    talk = Plan(action=Action(kind="talk", summary="向守钟人问钟声", npc="warden", speech="请问钟声有什么规律？"))
    plans = {"我和贝蒂去钟楼": declared_move(companions=["betty"]),
             "我也去钟楼并问钟声": declared_move(remaining=tail), "我留守广场": stay()}
    tellings = [telling(), telling(next=talk) if execute_tail else telling(pending=tail)]
    if execute_tail:
        tellings.append(telling())
    tellings.append(telling())
    hub, cast = make_hub(fake, plans, tellings)
    cast.rulings[talk.action.summary] = Ruling(needs_roll=False, minutes=tail_minutes)
    start = hub.state.minutes
    try:
        hub.sits = {"elois": "a", "betty": "b", "bert": "c"}
        hub.clients = {}
        hub.pool = [("elois", "我和贝蒂去钟楼"), ("betty", "我也去钟楼并问钟声"), ("bert", "我留守广场")]
        await hub._settle()
        assert hub.game.world.place("elois") == hub.game.world.place("betty") == "tower"
        assert hub.game.world.place("bert") == "square"
        assert len(journeys(hub)) == 1 and set(journeys(hub)[0].motions) == {"elois", "betty"}
        route_minutes = hub.game._travel("square", "tower")
        assert hub.state.minutes - start == route_minutes + tail_minutes
        assert not hub.state.round_plan and hub.game.pending_party() is None and not hub.state.pending_turn
        assert sum(entry.kind == "player" and entry.text == "我也去钟楼并问钟声" for entry in hub.state.log) == 1
        if execute_tail:
            assert len(cast.npc_calls) == 1 and cast.npc_calls[0][0] == "warden"
            assert "betty" not in hub.state.unfinished
        else:
            assert not cast.npc_calls and hub.state.unfinished["betty"]["remaining"] == tail
            assert hub.state.unfinished["betty"]["original"] == "我也去钟楼并问钟声"
        assert not cast.planned and cast.party_calls == 1
    finally:
        await hub.game.close()


async def test_a_compound_requests_future_move_cannot_skip_its_owners_current_wait(fake):
    continuing = declared_move()
    plans = {"先去钟楼": declared_move(companions=["betty"]),
             "我先留守五分钟，然后去钟楼": Plan(action=stay().action, remaining="随后去钟楼"),
             "我一直留守": stay()}
    hub, _cast = make_hub(fake, plans, [telling(), telling(next=continuing), telling(), telling()])
    try:
        hub.sits = {"elois": "a", "betty": "b", "bert": "c"}
        hub.pool = [("elois", "先去钟楼"), ("betty", "我先留守五分钟，然后去钟楼"), ("bert", "我一直留守")]
        await hub._settle()
        moved = journeys(hub)
        assert len(moved) == 2
        assert set(moved[0].motions) == {"elois"} and set(moved[1].motions) == {"betty"}
        assert moved[1].motions["betty"]["start"] - moved[0].motions["elois"]["start"] == 5
        assert hub.game.world.place("bert") == "square"
    finally:
        await hub.game.close()


async def test_a_retained_unstarted_declaration_does_not_authorise_somebody_elses_current_journey(fake):
    plans = {"先去钟楼": declared_move(companions=["betty"])}
    hub, cast = make_hub(fake, plans, [telling()])
    try:
        hub.sits = {"elois": "a", "betty": "b", "bert": "c"}
        hub.pool, hub.early = [("elois", "先去钟楼")], [("betty", "我下一轮去钟楼")]
        original = copy.deepcopy(hub.early)
        await hub._settle()
        assert hub.game.world.place("elois") == "tower"
        assert hub.game.world.place("betty") == hub.game.world.place("bert") == "square"
        assert hub.early == original and hub.state.queued_actions == original
        assert set(journeys(hub)[0].motions) == {"elois"} and cast.party_calls == 1
    finally:
        await hub.game.close()
